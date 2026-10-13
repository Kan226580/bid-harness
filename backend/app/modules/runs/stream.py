import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import StreamingResponse

from backend.app.core.authorization import Permission, authorize
from backend.app.core.cookies import SESSION_COOKIE_NAME
from backend.app.core.errors import ApiError
from backend.app.core.principal import Principal
from backend.app.db.session import AppSessionLocal, AuthSessionLocal
from backend.app.db.tenant import tenant_transaction
from backend.app.models.identity.auth import Organization, User
from backend.app.models.runs import RunEvent
from backend.app.modules.identity.dependencies import session_expired
from backend.app.modules.identity.service import load_session
from backend.app.modules.projects.schemas import utc_text
from backend.app.modules.runs.domain import TERMINAL_STATUSES
from backend.app.modules.runs.repository import require_run


@dataclass(frozen=True)
class EventBatch:
    status: str
    latest_seq: int
    events: list[dict[str, Any]]


def parse_cursor(run_id: uuid.UUID, *, last_event_id: str | None, after_seq: int) -> int:
    """Last-Event-ID 优先；拒绝其他运行、负数和超出 bigint 的序号。"""
    if last_event_id is None:
        return after_seq
    parts = last_event_id.split(":")
    if len(parts) != 2 or re.fullmatch(r"[0-9]{1,19}", parts[1]) is None:
        raise ApiError(422, "VALIDATION_ERROR", "事件游标无效")
    try:
        cursor_run_id = uuid.UUID(parts[0])
        seq = int(parts[1])
    except ValueError as exc:
        raise ApiError(422, "VALIDATION_ERROR", "事件游标无效") from exc
    if cursor_run_id != run_id or seq > 9223372036854775807:
        raise ApiError(422, "VALIDATION_ERROR", "事件游标不属于当前运行或序号无效")
    return seq


def stream_actor(raw_token: str | None, auth_factory: sessionmaker[Session]) -> Principal:
    """每次检查都关闭认证 Session，使撤销和停用在后续检查生效。"""
    if not raw_token:
        raise session_expired()
    with auth_factory() as db:
        record = load_session(db, raw_token)
        if record is None:
            raise session_expired()
        user = db.get(User, record.user_id)
        organization = db.get(Organization, record.org_id)
        if (
            user is None
            or not user.is_active
            or organization is None
            or not organization.is_active
            or user.org_id != record.org_id
        ):
            raise session_expired()
        if user.must_change_password:
            raise ApiError(403, "PASSWORD_CHANGE_REQUIRED", "需要先修改密码")
        actor = Principal(record.org_id, user.id, user.role)
        authorize(actor, Permission.READ_CONTENT)
        return actor


def read_batch(
    raw_token: str | None,
    *,
    run_id: uuid.UUID,
    after_seq: int,
    app_factory: sessionmaker[Session],
    auth_factory: sessionmaker[Session],
) -> EventBatch:
    """先检查当前会话，再用当前组织读取有限一页事件。"""
    actor = stream_actor(raw_token, auth_factory)
    with tenant_transaction(actor, app_factory) as db:
        run = require_run(db, org_id=actor.org_id, run_id=run_id)
        latest_seq = run.last_event_seq
        if after_seq > latest_seq:
            raise ApiError(422, "VALIDATION_ERROR", "事件游标超过当前运行位置")
        minimum = db.execute(
            select(func.min(RunEvent.seq)).where(
                RunEvent.org_id == actor.org_id,
                RunEvent.run_id == run_id,
            )
        ).scalar_one()
        if (minimum is not None and after_seq < minimum - 1) or (
            minimum is None and after_seq < latest_seq
        ):
            raise ApiError(410, "EVENT_HISTORY_EXPIRED", "事件历史已过期，请重新获取运行")

        rows = (
            db.execute(
                select(RunEvent)
                .where(
                    RunEvent.org_id == actor.org_id,
                    RunEvent.run_id == run_id,
                    RunEvent.seq > after_seq,
                    RunEvent.seq <= latest_seq,
                )
                .order_by(RunEvent.seq)
                .limit(100)
            )
            .scalars()
            .all()
        )
        events = [
            {
                "run_id": str(run_id),
                "seq": row.seq,
                "event_type": row.event_type,
                "status": row.payload_json["status"],
                "payload": {
                    key: value for key, value in row.payload_json.items() if key != "status"
                },
                "created_at": utc_text(row.created_at),
            }
            for row in rows
        ]
        return EventBatch(run.status, latest_seq, events)


def encode_event(event: dict[str, Any]) -> str:
    """SSE 一条事件必须用空行结尾；JSON 不输出实际换行。"""
    data = {key: value for key, value in event.items() if key != "event_type"}
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['run_id']}:{event['seq']}\nevent: {event['event_type']}\ndata: {body}\n\n"


def event_response(request: Request, *, run_id: uuid.UUID, after_seq: int) -> StreamingResponse:
    """先同步预检，之后在异步生成器中把阻塞数据库操作放到线程。"""
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    cursor = parse_cursor(
        run_id,
        last_event_id=request.headers.get("Last-Event-ID"),
        after_seq=after_seq,
    )
    first = read_batch(
        raw_token,
        run_id=run_id,
        after_seq=cursor,
        app_factory=AppSessionLocal,
        auth_factory=AuthSessionLocal,
    )

    async def generate() -> AsyncIterator[str]:
        current = cursor
        batch = first
        last_heartbeat = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            for event in batch.events:
                yield encode_event(event)
                current = int(event["seq"])
            # 必须先把剩余分页事件发完，不能见到终态就截断。
            if (
                batch.status in TERMINAL_STATUSES | {"waiting_input"}
                and current >= batch.latest_seq
            ):
                return
            if time.monotonic() - last_heartbeat >= 15:
                yield ": heartbeat\n\n"
                last_heartbeat = time.monotonic()
            if current >= batch.latest_seq:
                await asyncio.sleep(1)
            try:
                batch = await asyncio.to_thread(
                    read_batch,
                    raw_token,
                    run_id=run_id,
                    after_seq=current,
                    app_factory=AppSessionLocal,
                    auth_factory=AuthSessionLocal,
                )
            except ApiError:
                # 已开流只能关闭；客户端通过 GET Run / auth/me 判定原因。
                return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
