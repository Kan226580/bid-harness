import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.app.api.errors import install_error_handlers, ok
from backend.app.api.middleware import RequestIdMiddleware
from backend.app.core.errors import ApiError
from backend.app.core.pagination import CursorAnchor, paginate


def convention_client() -> TestClient:
    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    install_error_handlers(application)

    @application.get("/ok")
    def success(request: Request) -> dict[str, object]:
        return ok(request, {"value": 1})

    @application.get("/business")
    def business() -> None:
        raise ApiError(
            409,
            "IDEMPOTENCY_IN_PROGRESS",
            "处理中",
            retryable=True,
            headers={"Retry-After": "1"},
        )

    @application.get("/validation")
    def validation(number: int) -> dict[str, int]:
        return {"number": number}

    @application.get("/crash")
    def crash() -> None:
        raise RuntimeError("internal-marker-do-not-return")

    return TestClient(application, raise_server_exceptions=False)


def test_response_ids_and_errors() -> None:
    """验证普通请求编号独立，业务和框架错误都使用统一外壳。"""

    with convention_client() as client:
        first = client.get("/ok")
        second = client.get("/ok")
        assert set(first.json()) == {"data", "request_id"}
        assert first.json()["data"] == {"value": 1}
        assert first.json()["request_id"] == first.headers["X-Request-ID"]
        assert first.json()["request_id"] != second.json()["request_id"]
        uuid.UUID(first.json()["request_id"])

        for path, status, code in [
            ("/business", 409, "IDEMPOTENCY_IN_PROGRESS"),
            ("/validation?number=wrong", 422, "VALIDATION_ERROR"),
            ("/missing", 404, "RESOURCE_NOT_FOUND"),
            ("/crash", 500, "INTERNAL_ERROR"),
        ]:
            response = client.get(path)
            body = response.json()
            assert response.status_code == status
            assert set(body) == {"error", "request_id"}
            assert set(body["error"]) == {"code", "message", "details", "retryable"}
            assert body["error"]["code"] == code
            assert body["request_id"] == response.headers["X-Request-ID"]
            assert "internal-marker-do-not-return" not in response.text

        processing = client.get("/business")
        assert processing.json()["error"]["retryable"] is True
        assert processing.headers["Retry-After"] == "1"
        method = client.post("/ok")
        assert method.status_code == 405
        assert method.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
        assert "GET" in method.headers["Allow"]


@dataclass(frozen=True)
class Item:
    created_at: datetime
    id: uuid.UUID


def test_pagination_twenty_five_items() -> None:
    """同时间的二十五条记录分两页取完，没有重复或遗漏。"""

    moment = datetime(
        2026,
        10,
        11,
        tzinfo=UTC,
    )
    rows = [
        Item(moment, uuid.UUID(int=value))
        for value in range(
            25,
            0,
            -1,
        )
    ]

    def fetch(anchor: CursorAnchor | None, size: int) -> list[Item]:
        eligible = [
            item
            for item in rows
            if anchor is None or (item.created_at, item.id) < (anchor.created_at, anchor.id)
        ]
        return eligible[:size]

    def anchor_of(item: Item) -> CursorAnchor:
        return CursorAnchor(item.created_at, item.id)

    first, cursor, more = paginate(
        cursor=None,
        filters={"status": "active"},
        fetch=fetch,
        anchor_of=anchor_of,
    )
    assert len(first) == 20
    assert more is True and cursor is not None
    second, last_cursor, last_more = paginate(
        cursor=cursor,
        filters={"status": "active"},
        fetch=fetch,
        anchor_of=anchor_of,
    )
    assert len(second) == 5
    assert last_cursor is None and last_more is False
    assert [item.id for item in first + second] == [item.id for item in rows]
    assert len({item.id for item in first + second}) == 25

    with pytest.raises(ApiError) as exc:
        paginate(
            cursor=cursor,
            filters={"status": "archived"},
            fetch=fetch,
            anchor_of=anchor_of,
        )
    assert exc.value.status_code == 422
    for bad_cursor in ["", "%%%", "eyJ2ZXJzaW9uIjoyfQ"]:
        with pytest.raises(ApiError):
            paginate(
                cursor=bad_cursor,
                filters={},
                fetch=fetch,
                anchor_of=anchor_of,
            )
    for bad_limit in [0, 101]:
        with pytest.raises(ApiError):
            paginate(
                limit=bad_limit,
                cursor=None,
                filters={},
                fetch=fetch,
                anchor_of=anchor_of,
            )

    def fetch_empty(anchor: CursorAnchor | None, size: int) -> list[Item]:
        return []

    empty, empty_cursor, empty_more = paginate(
        cursor=None,
        filters={},
        fetch=fetch_empty,
        anchor_of=anchor_of,
    )
    assert empty == [] and empty_cursor is None and empty_more is False
