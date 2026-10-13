import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.ops import OpsSessionLocal
from backend.app.models.outbox import Outbox
from backend.app.modules.runs.repository import db_now
from backend.app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Delivery:
    outbox_id: uuid.UUID
    org_id: uuid.UUID
    run_id: uuid.UUID
    generation: int
    owner: uuid.UUID
    attempt: int


def claim_batch(
    session_factory: sessionmaker[Session],
    *,
    size: int = 10,
) -> list[Delivery]:
    """用短事务领取一批，过期 sending 也可重新领取。"""
    with session_factory() as db, db.begin():
        now = db_now(db)
        rows = (
            db.execute(
                select(Outbox)
                .where(
                    or_(
                        and_(Outbox.status.in_(["pending", "failed"]), Outbox.available_at <= now),
                        and_(Outbox.status == "sending", Outbox.claim_expires_at <= now),
                    )
                )
                .order_by(Outbox.available_at, Outbox.id)
                .limit(size)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        deliveries = []
        for row in rows:
            owner = uuid.uuid4()
            row.status = "sending"
            row.claim_owner = owner
            row.claim_expires_at = now + timedelta(seconds=120)
            row.attempt_count += 1
            row.updated_at = now
            deliveries.append(
                Delivery(
                    row.id,
                    row.org_id,
                    row.run_id,
                    row.dispatch_generation,
                    owner,
                    row.attempt_count,
                )
            )
        return deliveries


def finish_delivery(
    session_factory: sessionmaker[Session],
    delivery: Delivery,
    *,
    succeeded: bool,
) -> bool:
    """条件更新：旧投递器不能确认别人已重新领取的 outbox。"""
    with session_factory() as db, db.begin():
        now = db_now(db)
        values: dict[str, object] = {
            "status": "sent" if succeeded else "failed",
            "claim_owner": None,
            "claim_expires_at": None,
            "updated_at": now,
            "last_error_code": None if succeeded else "BROKER_SEND_FAILED",
        }
        if succeeded:
            values["sent_at"] = now
        else:
            delay = min(60, 2 ** min(delivery.attempt, 6))
            values["available_at"] = now + timedelta(seconds=delay)
        updated_id = db.execute(
            update(Outbox)
            .where(
                Outbox.id == delivery.outbox_id,
                Outbox.status == "sending",
                Outbox.claim_owner == delivery.owner,
                Outbox.claim_expires_at > now,
            )
            .values(**values)
            .returning(Outbox.id)
        ).scalar_one_or_none()
        return updated_id is not None


def dispatch_once(session_factory: sessionmaker[Session]) -> int:
    """网络发送发生在 claim_batch 已经提交之后。"""
    deliveries = claim_batch(session_factory)
    for delivery in deliveries:
        try:
            celery_app.send_task(
                "harness.execute_run",
                kwargs={
                    "run_id": str(delivery.run_id),
                    "org_id": str(delivery.org_id),
                    "dispatch_generation": delivery.generation,
                },
                queue="harness_runs",
                retry=False,
            )
        except Exception:
            # 不记录异常原文，连接异常可能携带 Broker URL 和密码。
            logger.warning("broker_send_failed run_id=%s", delivery.run_id)
            finish_delivery(session_factory, delivery, succeeded=False)
        else:
            finish_delivery(session_factory, delivery, succeeded=True)
    return len(deliveries)


def main() -> None:
    """投递程序应由进程管理器独立运行。"""
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            count = dispatch_once(OpsSessionLocal)
        except Exception:
            logger.warning("dispatcher_iteration_failed")
            time.sleep(2)
        else:
            if count == 0:
                time.sleep(1)


if __name__ == "__main__":
    main()
