from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled"})

# 排队投递长期失败、retry_wait 恢复耗尽，也必须能明确失败
ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.CANCELED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_INPUT,
            RunStatus.RETRY_WAIT,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELED,
            RunStatus.QUEUED,
        }
    ),
    RunStatus.WAITING_INPUT: frozenset(
        {
            RunStatus.QUEUED,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.RETRY_WAIT: frozenset(
        {
            RunStatus.QUEUED,
            RunStatus.CANCELED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELED: frozenset(),
}


class InvalidTransition(ValueError):
    """当前状态不允许进入目标状态"""


class LeaseLost(RuntimeError):
    """当前执行者已经失去提交资格"""


class RetryableTaskError(RuntimeError):
    """已知可恢复错误，由数据库安排下一次执行"""


def transition(current: str, target: str) -> RunStatus:
    """校验一次状态转换，返回合法的目标状态"""
    try:
        current_status = RunStatus(current)
        target_status = RunStatus(target)
    except ValueError as e:
        raise InvalidTransition("未知运行状态") from e

    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise InvalidTransition(f"不允许从 {current} 转为 {target}")

    return target_status
