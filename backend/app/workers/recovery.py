import logging
import time
import uuid
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.session import AppSessionLocal, AuthSessionLocal
from backend.app.models.identity.auth import Organization
from backend.app.models.runs import Run, RunStep
from backend.app.modules.runs.repository import (
    append_event,
    change_state,
    db_now,
    enqueue,
    lock_run,
    release_lease,
    worker_transaction,
)

logger = logging.getLogger(__name__)
MAX_RECOVERIES = 3
QUEUE_STALE_SECONDS = 120


def sweep_org(session_factory: sessionmaker[Session], *, org_id: uuid.UUID) -> int:
    """先读候选 ID，随后逐条按项目→运行加锁并重新判断。"""
    with worker_transaction(org_id, session_factory) as db:
        now = db_now(db)
        ids = list(
            db.execute(
                select(Run.id)
                .where(
                    Run.org_id == org_id,
                    or_(
                        and_(Run.status == "running", Run.lease_expires_at <= now),
                        and_(
                            Run.status == "queued",
                            Run.next_attempt_at <= now - timedelta(seconds=QUEUE_STALE_SECONDS),
                        ),
                        and_(Run.status == "retry_wait", Run.next_attempt_at <= now),
                    ),
                )
                .order_by(Run.created_at, Run.id)
                .limit(100)
            ).scalars()
        )

    recovered = 0
    for run_id in ids:
        with worker_transaction(org_id, session_factory) as db:
            run = lock_run(db, org_id=org_id, run_id=run_id)
            now = db_now(db)
            expired = (
                run.status == "running"
                and run.lease_expires_at is not None
                and run.lease_expires_at <= now
            )
            stale_queued = (
                run.status == "queued"
                and run.next_attempt_at is not None
                and run.next_attempt_at <= now - timedelta(seconds=QUEUE_STALE_SECONDS)
            )
            retry_due = (
                run.status == "retry_wait"
                and run.next_attempt_at is not None
                and run.next_attempt_at <= now
            )
            if not (expired or stale_queued or retry_due):
                continue

            if expired and run.current_step is not None:
                step = db.execute(
                    select(RunStep).where(
                        RunStep.org_id == org_id,
                        RunStep.run_id == run.id,
                        RunStep.step_key == run.current_step,
                    )
                ).scalar_one_or_none()
                if step is not None and step.status == "running":
                    step.status = "failed"
                    step.error_code = "LEASE_EXPIRED"
                    step.finished_at = now
                    step.updated_at = now

            release_lease(run, invalidate=True)
            if run.cancel_requested:
                run.finished_at = now
                run.error_code = None
                run.error_message = None
                run.error_retryable = False
                change_state(db, run, target="canceled", event_type="run.canceled")
                continue

            if run.recovery_count >= MAX_RECOVERIES:
                run.error_code = "LEASE_RECOVERY_EXHAUSTED"
                run.error_message = "自动恢复次数已用完，请检查依赖后人工重试"
                run.error_retryable = False
                run.finished_at = now
                change_state(db, run, target="failed", event_type="run.failed")
                continue

            reason = "lease_expired" if expired else "queue_stale" if stale_queued else "retry_due"
            run.recovery_count += 1
            run.dispatch_generation += 1
            run.next_attempt_at = now
            run.current_step = None
            if run.status == "queued":
                append_event(db, run, event_type="run.queued", payload={"reason": reason})
            else:
                change_state(
                    db, run, target="queued", event_type="run.queued", payload={"reason": reason}
                )
            enqueue(db, run, available_at=now)
            recovered += 1
    return recovered


def organization_ids(
    auth_factory: sessionmaker[Session],
    *,
    after: uuid.UUID | None,
) -> list[uuid.UUID]:
    """认证专用连接枚举组织元数据，业务内容仍逐组织访问。"""
    with auth_factory() as db:
        statement = select(Organization.id)
        if after is not None:
            statement = statement.where(Organization.id > after)
        return list(db.execute(statement.order_by(Organization.id).limit(500)).scalars())


def sweep_all(
    app_factory: sessionmaker[Session],
    auth_factory: sessionmaker[Session],
) -> None:
    """不授予后台 BYPASSRLS，不用 ops 连接读取业务运行。"""
    after = None
    while True:
        ids = organization_ids(auth_factory, after=after)
        if not ids:
            return
        for org_id in ids:
            try:
                sweep_org(app_factory, org_id=org_id)
            except Exception:
                logger.warning("org_recovery_failed org_id=%s", org_id)
        after = ids[-1]


def main() -> None:
    """独立常驻程序，不把恢复调度依赖在 Redis Beat 消息上。"""
    logging.basicConfig(level=logging.INFO)
    while True:
        started = time.monotonic()
        try:
            sweep_all(AppSessionLocal, AuthSessionLocal)
        except Exception:
            logger.warning("recovery_iteration_failed")
        time.sleep(max(0.1, 30 - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
