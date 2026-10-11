import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.core.errors import ApiError
from backend.app.core.pagination import canonical_json
from backend.app.core.principal import Principal
from backend.app.models.idempotency import IdempotencyKey


@dataclass(frozen=True)
class WriteResult:
    """一次写操作的状态码、JSON 响应和资源位置。"""

    status_code: int
    body: dict[str, Any]
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    replayed: bool = False


def digest(value: str) -> str:
    """计算 SHA-256 摘要，不持久化原始幂等键。"""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_idempotency_key(key: str | None, *, required: bool) -> str | None:
    """区分可选和必需的幂等键，并检查长度和字符范围。"""

    if key is None and not required:
        return None
    # 使用可打印且不含空格的 ASCII；具体允许字符范围回写到 API 1.5。
    if (
        key is None
        or not 8 <= len(key) <= 128
        or not key.isascii()
        or not key.isprintable()
        or " " in key
    ):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Idempotency-Key 必须是 8 到 128 位可打印 ASCII 字符",
            details={"fields": [{"field": "Idempotency-Key", "reason": "长度或字符不合法"}]},
        )
    return key


def in_progress() -> ApiError:
    """相同请求尚未完成时，提示客户端稍后重试。"""

    return ApiError(
        409,
        "IDEMPOTENCY_IN_PROGRESS",
        "相同请求正在处理，请稍后重试",
        retryable=True,
        headers={"Retry-After": "1"},
    )


def cached_result(
    record: IdempotencyKey | None,
    *,
    request_hash: str,
    now: datetime,
) -> WriteResult | None:
    """检查已有记录，决定重放、冲突、处理中或允许执行新请求。"""

    if record is None:
        return None
    # processing 即使过期也不能盲目再次执行业务。
    if record.status == "processing":
        if record.request_hash != request_hash:
            raise ApiError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "同一幂等键对应了不同请求",
            )
        raise in_progress()
    if record.expires_at <= now:
        return None
    if record.request_hash != request_hash:
        raise ApiError(
            409,
            "IDEMPOTENCY_CONFLICT",
            "同一幂等键对应了不同请求",
        )
    if record.response_status is None or record.response_json is None:
        raise RuntimeError("completed idempotency record has no response")
    return WriteResult(
        status_code=record.response_status,
        body=record.response_json,
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        replayed=True,
    )


def execute_idempotent(
    db: Session,
    actor: Principal,
    *,
    key: str | None,
    method: str,
    canonical_path: str,
    payload: dict[str, Any],
    action: Callable[[], WriteResult],
) -> WriteResult:
    """使用当前租户事务协调业务副作用与持久化响应。"""

    if key is None:
        return action()

    key_hash = digest(key)
    request_hash = digest(canonical_json(payload))
    method = method.upper()
    statement = (
        select(IdempotencyKey)
        .where(
            IdempotencyKey.org_id == actor.org_id,
            IdempotencyKey.user_id == actor.user_id,
            IdempotencyKey.http_method == method,
            IdempotencyKey.canonical_path == canonical_path,
            IdempotencyKey.key_hash == key_hash,
        )
        .execution_options(populate_existing=True)
    )

    record = db.execute(statement).scalar_one_or_none()
    hit = cached_result(
        record,
        request_hash=request_hash,
        now=datetime.now(UTC),
    )
    if hit is not None:
        return hit

    scope = canonical_json(
        [
            str(actor.org_id),
            str(actor.user_id),
            method,
            canonical_path,
            key_hash,
        ]
    )
    # PostgreSQL 接收有符号 bigint；哈希碰撞最多导致保守的“处理中”，不会重放别人的数据。
    lock_id = int.from_bytes(
        hashlib.sha256(scope.encode()).digest()[:8],
        "big",
        signed=True,
    )
    acquired = db.execute(
        text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
    ).scalar_one()
    if not acquired:
        raise in_progress()

    # 在拿锁后再读，避免另一事务恰好在第一次 SELECT 后完成。
    record = db.execute(statement).scalar_one_or_none()
    now = datetime.now(UTC)
    hit = cached_result(
        record,
        request_hash=request_hash,
        now=now,
    )
    if hit is not None:
        return hit

    if record is None:
        record = IdempotencyKey(
            id=uuid.uuid4(),
            org_id=actor.org_id,
            user_id=actor.user_id,
            http_method=method,
            canonical_path=canonical_path,
            key_hash=key_hash,
            request_hash=request_hash,
            status="processing",
        )
        db.add(record)
    # 已完成且过期的同作用域记录通过 UPDATE 复用，不需要 DELETE 授权。
    record.request_hash = request_hash
    record.status = "processing"
    record.processing_owner = uuid.uuid4()
    record.processing_expires_at = now + timedelta(minutes=1)
    record.response_status = None
    record.response_json = None
    record.resource_type = None
    record.resource_id = None
    record.expires_at = now + timedelta(hours=24)
    record.updated_at = now
    db.flush()

    result = action()
    record.status = "completed"
    record.response_status = result.status_code
    record.response_json = result.body
    record.resource_type = result.resource_type
    record.resource_id = result.resource_id
    record.processing_owner = None
    record.processing_expires_at = None
    record.updated_at = datetime.now(UTC)
    db.flush()
    return result
