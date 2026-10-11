import base64
import binascii
import hashlib
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.core.errors import ApiError


@dataclass(frozen=True)
class CursorAnchor:
    """上一页最后一条记录的位置，由时间和 UUID 共同确定。"""

    created_at: datetime
    id: uuid.UUID


class CursorPayload(BaseModel):
    """游标内保存的字段，拒绝未定义字段和错误类型。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: int = Field(
        ...,
        ge=1,
        le=1,
        description="游标编码格式版本，当前只支持整数 1",
    )
    filters_hash: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
        description="组织、用户、资源类型与查询条件的 SHA-256 摘要，小写十六进制",
    )
    created_at: str = Field(
        ...,
        min_length=1,
        description="上一页最后一条记录的 UTC ISO 8601 时间；解析时还会检查时间格式和时区",
    )
    id: str = Field(
        ...,
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        description="上一页最后一条记录的 UUID，与 created_at 一起组成稳定排序锚点",
    )


def canonical_json(value: object) -> str:
    """统一 JSON 键顺序和分隔符，供摘要计算使用。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def filters_digest(filters: dict[str, object]) -> str:
    """计算过滤条件摘要，防止修改条件后继续使用旧游标。"""

    return hashlib.sha256(canonical_json(filters).encode("utf-8")).hexdigest()


def encode_cursor(anchor: CursorAnchor, filters: dict[str, object]) -> str:
    """将排序锚点和过滤摘要编码成客户端携带的游标。"""

    payload = {
        "version": 1,
        "filters_hash": filters_digest(filters),
        "created_at": anchor.created_at.astimezone(UTC).isoformat(),
        "id": str(anchor.id),
    }
    payload_json = canonical_json(payload)
    payload_bytes = payload_json.encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload_bytes)
    return encoded.decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None, filters: dict[str, object]) -> CursorAnchor | None:
    """解析并检查游标，无效或条件变化时返回 422。"""

    if cursor is None:
        return None
    try:
        if not cursor or len(cursor) > 2048:
            raise ValueError("cursor length")
        raw = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4),
            altchars=b"-_",
            validate=True,
        )
        payload = CursorPayload.model_validate_json(raw)
        if payload.version != 1 or payload.filters_hash != filters_digest(filters):
            raise ValueError("cursor scope")
        created_at = datetime.fromisoformat(payload.created_at)
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("timezone required")
        return CursorAnchor(created_at=created_at, id=uuid.UUID(payload.id))
    except (ValueError, ValidationError, binascii.Error) as exc:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "游标无效或过滤条件已改变",
            details={"fields": [{"field": "cursor", "reason": "请从第一页重新查询"}]},
        ) from exc


def paginate[T](
    *,
    limit: int = 20,
    cursor: str | None,
    filters: dict[str, object],
    fetch: Callable[[CursorAnchor | None, int], Sequence[T]],
    anchor_of: Callable[[T], CursorAnchor],
) -> tuple[list[T], str | None, bool]:
    """多查询一条判断下一页，只把用户请求的数量作为当前页返回。"""

    if not 1 <= limit <= 100:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "limit 必须在 1 到 100 之间",
        )
    anchor = decode_cursor(cursor, filters)
    rows = list(fetch(anchor, limit + 1))
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more:
        last_item = items[-1]
        next_anchor = anchor_of(last_item)
        next_cursor = encode_cursor(next_anchor, filters)
    return items, next_cursor, has_more
