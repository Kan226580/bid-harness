import copy
import uuid
from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.authorization import Permission, authorize
from backend.app.core.errors import ApiError
from backend.app.core.pagination import CursorAnchor, canonical_json, paginate
from backend.app.core.principal import Principal
from backend.app.db.tenant import tenant_transaction
from backend.app.models import Run, RunStep
from backend.app.modules.operations.idempotency import WriteResult, digest, execute_idempotent
from backend.app.modules.projects.schemas import utc_text
from backend.app.modules.projects.service import require_active
from backend.app.modules.runs.repository import (
    append_event,
    change_state,
    db_now,
    enqueue,
    lock_project,
    lock_run,
    release_lease,
    require_run,
)


def authorize_control(actor: Principal, run: Run) -> None:
    """控制权限随运行类型变化，不能统一要求管理员"""
    if run.kind in {"parse", "index", "analyze", "generate"}:
        authorize(actor, Permission.EDIT_CONTENT)
    elif run.kind == "review":
        authorize(actor, Permission.REQUEST_REVIEW)
    elif run.kind == "export":
        mode = run.input_snapshot.get("export_mode")
        if mode == "review":
            authorize(actor, Permission.EXPORT_REVIEW)
        elif mode == "submission":
            authorize(actor, Permission.EXPORT_SUBMISSION)
        else:
            raise ApiError(409, "INVALID_STATE", "导出模式无效")
    else:
        raise ApiError(409, "INVALID_STATE", "运行类型无效")


def require_demo(run: Run) -> None:
    """A8 只开放模拟执行器；后续业务接入各自的实时校验。"""
    if run.kind != "analyze" or run.input_snapshot.get("executor") != "a8_demo":
        raise ApiError(409, "INVALID_STATE", "该业务执行器尚未接入")


def run_summary(db: Session, run: Run) -> dict[str, Any]:
    """只公开 API 白名单字段，租约和冻结原始输入不出响应"""
    steps = (
        db.execute(select(RunStep).where(RunStep.run_id == run.id, RunStep.org_id == run.org_id))
        .scalars()
        .all()
    )
    artifact = None
    if run.artifact_type is not None and run.artifact_id is not None:
        artifact = {"type": run.artifact_type, "id": str(run.artifact_id)}
    error = None
    if run.error_code is not None:
        error = {
            "code": run.error_code,
            "message": run.error_message or "运行失败",
            "retryable": run.error_retryable,
        }
    input_required = None
    if run.status == "waiting_input":
        input_required = {"gap_ids": ["demo-confirm"], "simulated": True}

    return {
        "id": str(run.id),
        "project_id": str(run.project_id) if run.project_id is not None else None,
        "kind": run.kind,
        "chapter_id": str(run.chapter_id) if run.chapter_id is not None else None,
        "status": run.status,
        "cancel_requested": run.cancel_requested,
        "current_step": run.current_step,
        "completed_steps": sum(step.status == "succeeded" for step in steps),
        "total_steps": len(steps),
        "baseline_id": str(run.baseline_id) if run.baseline_id is not None else None,
        "snapshot_hash": run.snapshot_hash,
        "model_mode": run.model_mode,
        "retry_of_run_id": str(run.retry_of_run_id) if run.retry_of_run_id is not None else None,
        "budget": {
            "max_calls": run.max_calls,
            "max_tokens": run.max_tokens,
            "max_cost_cny": str(run.max_cost_cny) if run.max_cost_cny is not None else None,
        },
        "usage": {
            "call_count": run.calls_started,
            "tokens_observed": run.tokens_observed,
            "tokens_reserved": run.tokens_reserved,
            "cost_observed_cny": str(run.cost_observed_cny),
            "cost_reserved_cny": str(run.cost_reserved_cny),
            "has_unknown_calls": run.has_unknown_calls,
        },
        "input_required": input_required,
        "artifact": artifact,
        "error": error,
        "last_event_seq": run.last_event_seq,
        "created_at": utc_text(run.created_at),
        "started_at": utc_text(run.started_at) if run.started_at is not None else None,
        "finished_at": utc_text(run.finished_at) if run.finished_at is not None else None,
    }


def accepted(run: Run, *, request_id: str) -> WriteResult:
    """创建和 retry 返回新运行的查询地址"""
    return WriteResult(
        status_code=202,
        body={
            "data": {
                "run_id": str(run.id),
                "status": run.status,
                "retry_of_run_id": str(run.retry_of_run_id) if run.retry_of_run_id else None,
                "events_url": f"/api/v1/runs/{run.id}/events",
                "status_url": f"/api/v1/runs/{run.id}",
            },
            "request_id": request_id,
        },
        resource_type="run",
        resource_id=run.id,
    )


def insert_demo_run(
    db: Session,
    actor: Principal,
    *,
    project_id: uuid.UUID,
    snapshot: dict[str, Any],
    retry_of_run_id: uuid.UUID | None = None,
) -> Run:
    """调用者已经锁住项目；本函数不提交事务。"""
    run = Run(
        id=uuid.uuid4(),
        org_id=actor.org_id,
        project_id=project_id,
        kind="analyze",
        status="queued",
        input_snapshot=copy.deepcopy(snapshot),
        snapshot_hash=digest(canonical_json(snapshot)),
        model_mode="simulated",
        retry_of_run_id=retry_of_run_id,
        created_by=actor.user_id,
        next_attempt_at=db_now(db),
    )
    db.add(run)
    db.flush()  # 先写父运行，并让默认值和 created_at 可用。
    for step_key, node_name in (("prepare:r0", "prepare"), ("demo_work:r0", "demo_work")):
        db.add(
            RunStep(
                org_id=run.org_id,
                run_id=run.id,
                step_key=step_key,
                node_name=node_name,
                status="pending",
                lease_epoch=0,
                input_hash=run.snapshot_hash,
            )
        )
    append_event(db, run, event_type="run.queued", payload={"simulated": True})
    enqueue(db, run, available_at=db_now(db))
    db.flush()
    return run


def create_demo_run(
    actor: Principal,
    session_factory: sessionmaker[Session],
    *,
    project_id: uuid.UUID,
    seconds: int,
    pause: bool,
    fail_once: bool,
    request_id: str,
    idempotency_key: str,
) -> WriteResult:
    """开发专用创建入口：run、event、outbox、幂等响应原子提交。"""
    authorize(actor, Permission.EDIT_CONTENT)
    if not 1 <= seconds <= 300:
        raise ApiError(422, "VALIDATION_ERROR", "模拟耗时须为 1 到 300 秒")
    snapshot = {
        "executor": "a8_demo",
        "seconds": seconds,
        "pause": pause,
        "fail_once": fail_once,
        "simulated": True,
    }
    with tenant_transaction(actor, session_factory) as db:
        # 重放前也验证资源归属；拿项目锁的动作放在 action 中，
        # 保持 幂等锁→项目锁→运行锁 的统一顺序。
        from backend.app.modules.projects.service import require_project

        require_project(db, actor, project_id=project_id)

        def create_action() -> WriteResult:
            project = lock_project(db, org_id=actor.org_id, project_id=project_id)
            require_active(project)
            run = insert_demo_run(db, actor, project_id=project_id, snapshot=snapshot)
            return accepted(run, request_id=request_id)

        return execute_idempotent(
            db,
            actor,
            key=idempotency_key,
            method="POST",
            canonical_path=f"/internal/a8/projects/{project_id}/demo-runs",
            payload=snapshot,
            action=create_action,
        )


def get_run(
    actor: Principal,
    session_factory: sessionmaker[Session],
    *,
    run_id: uuid.UUID,
) -> dict[str, Any]:
    """读取本组织运行。"""
    authorize(actor, Permission.READ_CONTENT)
    with tenant_transaction(actor, session_factory) as db:
        run = require_run(db, org_id=actor.org_id, run_id=run_id)
        return run_summary(db, run)


def list_runs(
    actor: Principal,
    session_factory: sessionmaker[Session],
    *,
    project_id: uuid.UUID,
    kind: str | None,
    status: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    """稳定游标和过滤摘要。"""
    authorize(actor, Permission.READ_CONTENT)
    filters: dict[str, object] = {
        "resource": "runs",
        "org_id": str(actor.org_id),
        "user_id": str(actor.user_id),
        "project_id": str(project_id),
        "kind": kind,
        "status": status,
    }
    with tenant_transaction(actor, session_factory) as db:
        from backend.app.modules.projects.service import require_project

        require_project(db, actor, project_id=project_id)

        def fetch_page(anchor: CursorAnchor | None, size: int) -> Sequence[Run]:
            statement = select(Run).where(
                Run.org_id == actor.org_id,
                Run.project_id == project_id,
            )
            if kind is not None:
                statement = statement.where(Run.kind == kind)
            if status is not None:
                statement = statement.where(Run.status == status)
            if anchor is not None:
                statement = statement.where(
                    tuple_(Run.created_at, Run.id) < tuple_(anchor.created_at, anchor.id)
                )
            return (
                db.execute(statement.order_by(Run.created_at.desc(), Run.id.desc()).limit(size))
                .scalars()
                .all()
            )

        def anchor_of(run: Run) -> CursorAnchor:
            return CursorAnchor(created_at=run.created_at, id=run.id)

        items, next_cursor, has_more = paginate(
            limit=limit,
            cursor=cursor,
            filters=filters,
            fetch=fetch_page,
            anchor_of=anchor_of,
        )
        return {
            "items": [run_summary(db, run) for run in items],
            "next_cursor": next_cursor,
            "has_more": has_more,
        }


def cancel_locked_run(db: Session, run: Run, *, reason: str | None) -> None:
    """已持有项目与运行锁；执行中的取消只登记请求。"""
    if run.status == "canceled":
        return
    if run.status in {"succeeded", "failed"}:
        raise ApiError(409, "RUN_TERMINAL", "终态运行不能取消")
    if run.status == "running":
        if not run.cancel_requested:
            run.cancel_requested = True
            append_event(
                db,
                run,
                event_type="run.cancel_requested",
                payload={"reason": reason},
            )
        return

    run.cancel_requested = True
    run.finished_at = db_now(db)
    run.error_code = None
    run.error_message = None
    run.error_retryable = False
    release_lease(run, invalidate=True)
    change_state(db, run, target="canceled", event_type="run.canceled", payload={"reason": reason})


def control_run(
    actor: Principal,
    session_factory: sessionmaker[Session],
    *,
    run_id: uuid.UUID,
    operation: Literal["cancel", "resume", "retry"],
    payload: dict[str, Any],
    request_id: str,
    idempotency_key: str,
) -> WriteResult:
    """幂等锁 -> 项目锁 -> 运行锁，在同事务完成状态和协调记录。"""
    with tenant_transaction(actor, session_factory) as db:
        located = require_run(db, org_id=actor.org_id, run_id=run_id)
        authorize_control(actor, located)

        def control_action() -> WriteResult:
            run = lock_run(db, org_id=actor.org_id, run_id=run_id)
            if run.project_id is not None:
                project = lock_project(db, org_id=actor.org_id, project_id=run.project_id)
                require_active(project)

            if operation == "resume" and run.status != "waiting_input":
                raise ApiError(409, "INVALID_STATE", "只有等待输入的运行可以恢复")

            if operation == "cancel" or (operation == "resume" and payload["decision"] == "cancel"):
                cancel_locked_run(db, run, reason=payload.get("reason", payload.get("note")))

            elif operation == "resume":
                if run.status != "waiting_input":
                    raise ApiError(409, "INVALID_STATE", "只有等待输入的运行可以恢复")
                require_demo(run)
                if set(payload["acknowledged_gap_ids"]) != {"demo-confirm"}:
                    raise ApiError(422, "INVALID_REFERENCE", "必须确认全部当前缺口")
                run.resume_payload = copy.deepcopy(payload)
                run.dispatch_generation += 1
                run.cancel_requested = False
                run.next_attempt_at = db_now(db)
                release_lease(run, invalidate=True)
                change_state(db, run, target="queued", event_type="run.resumed")
                enqueue(db, run, available_at=run.next_attempt_at)

            elif operation == "retry":
                if run.status != "failed":
                    raise ApiError(409, "INVALID_STATE", "只有失败运行可以人工重试")
                require_demo(run)
                if run.project_id is None:
                    raise ApiError(409, "INVALID_STATE", "模拟运行需要项目")
                retried = insert_demo_run(
                    db,
                    actor,
                    project_id=run.project_id,
                    snapshot=run.input_snapshot,
                    retry_of_run_id=run.id,
                )
                return accepted(retried, request_id=request_id)

            db.flush()
            return WriteResult(
                status_code=202,
                body={"data": run_summary(db, run), "request_id": request_id},
                resource_type="run",
                resource_id=run.id,
            )

        return execute_idempotent(
            db,
            actor,
            key=idempotency_key,
            method="POST",
            canonical_path=f"/api/v1/runs/{run_id}/{operation}",
            payload=payload,
            action=control_action,
        )
