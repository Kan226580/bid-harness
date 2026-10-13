import copy
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models.runs import Run, RunStep
from backend.app.modules.runs.domain import LeaseLost
from backend.app.modules.runs.repository import (
    append_event,
    change_state,
    db_now,
    enqueue,
    lock_project,
    lock_run,
    release_lease,
    worker_transaction,
)

LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 20


@dataclass(frozen=True)
class Lease:
    org_id: uuid.UUID
    run_id: uuid.UUID
    owner: uuid.UUID
    epoch: int
    snapshot: dict[str, Any]
    resume_payload: dict[str, Any] | None
    recovery_count: int


def claim_run(
    session_factory: sessionmaker[Session],
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    dispatch_generation: int,
) -> Lease | None:
    """重复消息、旧世代、未到执行时间的消息都不领取。"""
    with worker_transaction(org_id, session_factory) as db:
        run = lock_run(db, org_id=org_id, run_id=run_id)
        now = db_now(db)
        if run.status != "queued" or run.dispatch_generation != dispatch_generation:
            return None
        if run.next_attempt_at is not None and run.next_attempt_at > now:
            return None
        if run.project_id is not None:
            project = lock_project(db, org_id=org_id, project_id=run.project_id)
            if project.status != "active":
                run.error_code = "PROJECT_ARCHIVED"
                run.error_message = "项目已归档，停止执行"
                run.error_retryable = False
                run.finished_at = now
                change_state(db, run, target="failed", event_type="run.failed")
                return None

        if run.kind == "generate":
            active = db.execute(
                select(func.count())
                .select_from(Run)
                .where(
                    Run.org_id == org_id,
                    Run.project_id == run.project_id,
                    Run.kind == "generate",
                    Run.status == "running",
                    Run.lease_expires_at > now,
                )
            ).scalar_one()
            if active >= 2:
                # 容量等待不是执行失败，不增加 recovery_count。
                run.dispatch_generation += 1
                run.next_attempt_at = now + timedelta(seconds=5)
                append_event(
                    db, run, event_type="run.queued", payload={"reason": "project_capacity"}
                )
                enqueue(db, run, available_at=run.next_attempt_at)
                return None

        owner = uuid.uuid4()  # 每次领取独立，不复用 Celery task_id。
        run.lease_owner = owner
        run.lease_epoch += 1
        run.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        run.heartbeat_at = now
        run.started_at = run.started_at or now
        run.finished_at = None
        run.error_code = None
        run.error_message = None
        run.error_retryable = False
        change_state(db, run, target="running", event_type="run.started")
        return Lease(
            org_id,
            run.id,
            owner,
            run.lease_epoch,
            copy.deepcopy(run.input_snapshot),
            copy.deepcopy(run.resume_payload),
            run.recovery_count,
        )


@contextmanager
def owned_transaction(
    session_factory: sessionmaker[Session],
    lease: Lease,
) -> Iterator[tuple[Session, Run]]:
    """所有节点提交共享这道围栏，事务内只允许短 SQL 和本地赋值。"""
    with worker_transaction(lease.org_id, session_factory) as db:
        run = lock_run(db, org_id=lease.org_id, run_id=lease.run_id)
        expires_at = run.lease_expires_at
        if (
            run.status != "running"
            or run.lease_owner != lease.owner
            or run.lease_epoch != lease.epoch
            or expires_at is None
            or expires_at <= db_now(db)
        ):
            raise LeaseLost("执行租约已失效")

        yield db, run
        db.flush()
        # 防止 SQL 等待把一个原本有效的节点提交拖过原租约期限。
        # 行锁阻止巡检或其他 Worker 在这笔短事务中更换 owner/epoch。
        # 即使本次节点清空了租约，仍使用进入事务时捕获的期限校验。
        if expires_at <= db_now(db):
            raise LeaseLost("节点提交已超过租约期限")


def heartbeat(session_factory: sessionmaker[Session], lease: Lease) -> None:
    """每次使用独立 Session，线程之间不能共享 SQLAlchemy Session。"""
    with owned_transaction(session_factory, lease) as (db, run):
        now = db_now(db)
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        run.updated_at = now


def cancel_at_boundary(db: Session, run: Run) -> bool:
    """返回 True 表示已经取消；不要抛异常回滚这次取消。"""
    if not run.cancel_requested:
        return False
    if run.current_step is not None:
        step = db.execute(
            select(RunStep).where(
                RunStep.org_id == run.org_id,
                RunStep.run_id == run.id,
                RunStep.step_key == run.current_step,
            )
        ).scalar_one_or_none()
        if step is not None and step.status == "running":
            step.status = "skipped"
            step.error_code = "CANCELED"
            step.finished_at = db_now(db)
            step.updated_at = db_now(db)
    run.finished_at = db_now(db)
    run.error_code = None
    run.error_message = None
    run.error_retryable = False
    release_lease(run)
    change_state(db, run, target="canceled", event_type="run.canceled")
    return True


def boundary(session_factory: sessionmaker[Session], lease: Lease) -> bool:
    """模拟慢工作中的安全点；True 表示可以继续。"""
    with owned_transaction(session_factory, lease) as (db, run):
        return not cancel_at_boundary(db, run)


def require_step(db: Session, run: Run, *, step_key: str) -> RunStep:
    """运行锁已覆盖本运行步骤的并发写入。"""
    return db.execute(
        select(RunStep).where(
            RunStep.org_id == run.org_id,
            RunStep.run_id == run.id,
            RunStep.step_key == step_key,
        )
    ).scalar_one()


def begin_step(
    session_factory: sessionmaker[Session],
    lease: Lease,
    *,
    step_key: str,
) -> Literal["execute", "cached", "stopped"]:
    """已成功步骤直接复用，失败或被中断的步骤增加尝试次数。"""
    with owned_transaction(session_factory, lease) as (db, run):
        if cancel_at_boundary(db, run):
            return "stopped"
        step = require_step(db, run, step_key=step_key)
        if step.input_hash != run.snapshot_hash:
            raise RuntimeError("步骤输入摘要与冻结输入不一致")
        if step.status == "succeeded":
            return "cached"
        now = db_now(db)
        step.status = "running"
        step.attempt_count += 1
        step.lease_epoch = lease.epoch
        step.started_at = now
        step.finished_at = None
        step.error_code = None
        step.updated_at = now
        run.current_step = step_key
        append_event(db, run, event_type="step.started", payload={"step_key": step_key})
        return "execute"


def complete_prepare(session_factory: sessionmaker[Session], lease: Lease) -> bool:
    """准备节点只有可解释摘要，没有原文或隐藏推理。"""
    with owned_transaction(session_factory, lease) as (db, run):
        if cancel_at_boundary(db, run):
            return False
        step = require_step(db, run, step_key="prepare:r0")
        if step.status != "succeeded":
            step.status = "succeeded"
            step.result_json = {"simulated": True, "prepared": True}
            step.finished_at = db_now(db)
            step.updated_at = db_now(db)
            append_event(db, run, event_type="step.completed", payload={"step_key": step.step_key})
        return True


def wait_for_input(session_factory: sessionmaker[Session], lease: Lease) -> None:
    """暂停释放执行资格，Worker 返回，不在进程里等待人工操作。"""
    with owned_transaction(session_factory, lease) as (db, run):
        if cancel_at_boundary(db, run):
            return
        release_lease(run)
        change_state(
            db,
            run,
            target="waiting_input",
            event_type="run.waiting_input",
            payload={"gap_ids": ["demo-confirm"], "simulated": True},
        )


def finish_demo(session_factory: sessionmaker[Session], lease: Lease) -> bool:
    """结果、步骤完成、成果绑定、终态与事件必须原子提交。"""
    with owned_transaction(session_factory, lease) as (db, run):
        if cancel_at_boundary(db, run):
            return False
        step = require_step(db, run, step_key="demo_work:r0")
        if step.status != "succeeded":
            step.status = "succeeded"
            step.lease_epoch = lease.epoch
            step.result_json = {
                "simulated": True,
                "message": "A8 后台执行骨架验证完成",
                "business_result_key": str(run.id),
            }
            step.finished_at = db_now(db)
            step.updated_at = db_now(db)
            append_event(db, run, event_type="step.completed", payload={"step_key": step.step_key})
        run.artifact_type = "analysis_batch"
        run.artifact_id = run.id
        run.finished_at = db_now(db)
        release_lease(run)
        change_state(
            db, run, target="succeeded", event_type="run.succeeded", payload={"simulated": True}
        )
        return True


def record_failure(
    session_factory: sessionmaker[Session],
    lease: Lease,
    *,
    retryable: bool,
) -> None:
    """只保存固定脱敏摘要，异常原文不能进入运行响应。"""
    with owned_transaction(session_factory, lease) as (db, run):
        if cancel_at_boundary(db, run):
            return
        now = db_now(db)
        code = "DEPENDENCY_UNAVAILABLE" if retryable else "TASK_EXECUTION_FAILED"
        if run.current_step is not None:
            step = require_step(db, run, step_key=run.current_step)
            if step.status == "running":
                step.status = "failed"
                step.error_code = code
                step.finished_at = now
                step.updated_at = now
        run.error_code = code
        run.error_message = "模拟依赖暂时不可用" if retryable else "任务执行失败，请查看服务端诊断"
        run.error_retryable = retryable
        release_lease(run)
        if retryable:
            run.next_attempt_at = now + timedelta(seconds=min(60, 5 * 2**run.recovery_count))
            change_state(db, run, target="retry_wait", event_type="run.retry_scheduled")
        else:
            run.finished_at = now
            change_state(db, run, target="failed", event_type="run.failed")
