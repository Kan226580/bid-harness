import logging
import threading
import time
import uuid

from backend.app.core.errors import ApiError
from backend.app.db.session import AppSessionLocal
from backend.app.modules.runs.domain import LeaseLost, RetryableTaskError
from backend.app.modules.runs.execution import (
    HEARTBEAT_SECONDS,
    Lease,
    begin_step,
    boundary,
    claim_run,
    complete_prepare,
    finish_demo,
    heartbeat,
    record_failure,
    wait_for_input,
)
from backend.app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def renew_loop(lease: Lease, stop: threading.Event, lost: threading.Event) -> None:
    """心跳线程只共享不可变凭证，独立打开数据库 Session。"""
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            heartbeat(AppSessionLocal, lease)
        except Exception:
            # 无法确认有效租约时先停止本执行者，不冒险提交。
            lost.set()
            logger.warning("lease_renewal_failed run_id=%s", lease.run_id)
            return


def execute_claimed(lease: Lease, lost: threading.Event) -> None:
    """模拟任务不占长事务；这里未来可替换为解析/模型适配器。"""
    if lease.snapshot.get("executor") != "a8_demo":
        raise RuntimeError("unsupported executor")
    preparing = begin_step(AppSessionLocal, lease, step_key="prepare:r0")
    if preparing == "stopped":
        return
    if preparing == "execute" and not complete_prepare(AppSessionLocal, lease):
        return
    if lease.snapshot.get("pause") and lease.resume_payload is None:
        wait_for_input(AppSessionLocal, lease)
        return

    working = begin_step(AppSessionLocal, lease, step_key="demo_work:r0")
    if working == "stopped":
        return
    if working == "execute":
        if lease.snapshot.get("fail_once") and lease.recovery_count == 0:
            raise RetryableTaskError("simulated temporary failure")
        seconds = int(lease.snapshot["seconds"])
        for elapsed in range(seconds):
            if lost.is_set():
                raise LeaseLost("心跳线程报告租约不可用")
            if not boundary(AppSessionLocal, lease):
                return
            logger.info("demo_progress run_id=%s elapsed=%s", lease.run_id, elapsed)
            time.sleep(1)  # 没有打开的业务事务。

    if lost.is_set():
        raise LeaseLost("提交前发现心跳失败")
    finish_demo(AppSessionLocal, lease)


@celery_app.task(name="harness.execute_run")  # type: ignore[untyped-decorator]
def execute_run(run_id: str, org_id: str, dispatch_generation: int) -> None:
    """队列入口只接受标识，从数据库读取冻结输入。"""
    try:
        lease = claim_run(
            AppSessionLocal,
            org_id=uuid.UUID(org_id),
            run_id=uuid.UUID(run_id),
            dispatch_generation=dispatch_generation,
        )
    except (ValueError, ApiError):
        logger.warning("invalid_run_delivery")
        return
    if lease is None:
        return

    stop = threading.Event()
    lost = threading.Event()
    renewer = threading.Thread(target=renew_loop, args=(lease, stop, lost), daemon=True)
    renewer.start()
    try:
        execute_claimed(lease, lost)
    except LeaseLost:
        logger.info("stale_worker_stopped run_id=%s", lease.run_id)
    except RetryableTaskError:
        try:
            record_failure(AppSessionLocal, lease, retryable=True)
        except LeaseLost:
            logger.info("stale_worker_stopped run_id=%s", lease.run_id)
    except Exception:
        logger.warning("task_execution_failed run_id=%s", lease.run_id)
        try:
            record_failure(AppSessionLocal, lease, retryable=False)
        except LeaseLost:
            logger.info("stale_worker_stopped run_id=%s", lease.run_id)
    finally:
        stop.set()
        renewer.join(timeout=5)
