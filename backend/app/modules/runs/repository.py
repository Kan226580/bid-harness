import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.errors import ApiError
from backend.app.models import Outbox, Project, Run, RunEvent
from backend.app.modules.runs.domain import transition


def db_now(db: Session) -> datetime:
    """使用数据库实际时钟，避免不同 Worker 的主机时间漂移"""
    return cast(datetime, db.execute(select(func.clock_timestamp())).scalar_one())


@contextmanager
def worker_transaction(
    org_id: uuid.UUID,
    session_factory: sessionmaker[Session],
) -> Iterator[Session]:
    """后台受控入口仍使用 app_runtime 和组织 RLS"""
    with session_factory() as db, db.begin():
        db.execute(
            text(
                """
                SELECT
                    set_config('app.current_org_id', :org_id, true),
                    set_config('app.current_user_id', '', true)
                """
            ),
            {"org_id": str(org_id)},
        )
        yield db


def require_run(
    db: Session,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
) -> Run:
    """不存在和跨组织资源统一返回 404"""
    run = db.execute(
        select(Run)
        .where(Run.org_id == org_id, Run.id == run_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if run is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "资源不存在")
    return run


def lock_project(db: Session, *, org_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    """取得项目行锁；创建、归档、领取和控制使用同一把锁"""
    project = db.execute(
        select(Project)
        .where(Project.org_id == org_id, Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if project is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "资源不存在")
    return project


def lock_run(
    db: Session,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
) -> Run:
    """先普通读取定位项目，再按项目 -> 运行顺序加锁"""
    located = require_run(db, org_id=org_id, run_id=run_id)
    if located.project_id is not None:
        lock_project(db, org_id=org_id, project_id=located.project_id)

    # 定位读不加行锁，不能把他误写成先锁 run
    return db.execute(
        select(Run)
        .where(Run.org_id == org_id, Run.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def append_event(
    db: Session,
    run: Run,
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """调用者持有运行锁；新建未提交的运行也由当前事务独占"""
    run.last_event_seq += 1
    run.updated_at = db_now(db)
    db.add(
        RunEvent(
            org_id=run.org_id,
            run_id=run.id,
            seq=run.last_event_seq,
            event_type=event_type,
            payload_json={"status": run.status, **(payload or {})},
        )
    )


def change_state(
    db: Session,
    run: Run,
    *,
    target: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """服务只能经由这个入口改变状态，同时追加事件"""
    run.status = transition(run.status, target).value
    append_event(db, run=run, event_type=event_type, payload=payload)


def enqueue(
    db: Session,
    run: Run,
    *,
    available_at: datetime,
) -> None:
    """只登记 outbox ，绝不在这里联系 Redis"""
    db.add(
        Outbox(
            org_id=run.org_id,
            run_id=run.id,
            dispatch_generation=run.dispatch_generation,
            status="pending",
            available_at=available_at,
        )
    )


def release_lease(
    run: Run,
    *,
    invalidate: bool = False,
) -> None:
    """离开 running 时清空租约；失效操作另增加代际号"""
    if invalidate:
        run.lease_epoch += 1
    run.lease_owner = None
    run.lease_expires_at = None
