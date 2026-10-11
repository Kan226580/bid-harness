import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response as HttpResponse
from sqlalchemy import Engine, create_engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.errors import ApiError
from backend.app.core.pagination import canonical_json
from backend.app.core.principal import Principal
from backend.app.core.security import hash_password
from backend.app.core.settings import settings
from backend.app.db.tenant import tenant_transaction
from backend.app.models import Chapter, IdempotencyKey, Organization, Project, User
from backend.app.modules.operations.idempotency import WriteResult, digest, execute_idempotent
from backend.app.modules.projects.schemas import CreateProjectRequest
from backend.app.modules.projects.service import create_project, update_project

PASSWORD = "a6-learning-password-2026"


@dataclass(frozen=True)
class A6Data:
    """本文件使用的三个真实测试用户，属于同一个组织。"""

    admin: Principal
    writer: Principal
    reviewer: Principal


def clear_a6_test_data(engine: Engine) -> None:
    """只允许清理独立测试库，避免误操作开发数据库。"""

    if engine.url.database is None or not engine.url.database.endswith("_test"):
        raise RuntimeError("拒绝清理非测试库")
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE auth.organizations CASCADE"))


@pytest.fixture
def a6_data(
    engine: Engine,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> Iterator[A6Data]:
    # 与现有安全测试一样，需要独占测试库；不要用 pytest-xdist 并行跑整套测试。
    """准备组织和三个角色，测试结束后清理数据。"""

    clear_a6_test_data(engine)
    org_id = uuid.uuid4()
    data = A6Data(
        admin=Principal(
            org_id,
            uuid.uuid4(),
            "admin",
        ),
        writer=Principal(
            org_id,
            uuid.uuid4(),
            "writer",
        ),
        reviewer=Principal(
            org_id,
            uuid.uuid4(),
            "reviewer",
        ),
    )
    try:
        with runtime_factories["auth"].begin() as db:
            db.add(
                Organization(
                    id=org_id,
                    slug="a6-learning",
                    name="A6 测试组织",
                )
            )
            db.flush()
            password_hash = hash_password(PASSWORD)
            db.add_all(
                [
                    User(
                        id=actor.user_id,
                        org_id=org_id,
                        email=f"a6-{actor.role}@example.invalid",
                        display_name=actor.role,
                        role=actor.role,
                        password_hash=password_hash,
                        must_change_password=False,
                    )
                    for actor in (data.admin, data.writer, data.reviewer)
                ]
            )
        yield data
    finally:
        clear_a6_test_data(engine)


def login(client: TestClient, *, role: str = "admin") -> str:
    """使用已有认证接口登录，并取得本会话的 CSRF Token。"""

    response = client.post(
        "/api/v1/auth/login",
        json={
            "organization_slug": "a6-learning",
            "email": f"a6-{role}@example.invalid",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def create_via_http(
    client: TestClient,
    csrf: str,
    *,
    key: str | None = None,
) -> HttpResponse:
    """通过真实项目路由发起创建请求，按需附加幂等键。"""

    headers = {"X-CSRF-Token": csrf}
    if key is not None:
        headers["Idempotency-Key"] = key
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": "A6 测试工程",
            "construction_type": "市政道路",
        },
    )
    return cast(HttpResponse, response)


@pytest.fixture
def parallel_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    # 现有 runtime_factories 的 pool_size=1，会把线程排队。
    # 另建业务角色连接池，让并发测试真正有两个 PostgreSQL 连接。
    """准备两个真实业务连接，让并发请求不会被单连接池串行化。"""

    parallel_engine = create_engine(
        settings.db.url.set(database=engine.url.database),
        pool_size=2,
        max_overflow=0,
    )
    try:
        yield sessionmaker(
            bind=parallel_engine,
            expire_on_commit=False,
            autoflush=False,
        )
    finally:
        parallel_engine.dispose()


PROJECT_FIELDS = {
    "id",
    "name",
    "construction_type",
    "description",
    "data_classification",
    "status",
    "current_baseline_id",
    "baseline_dirty",
    "lock_version",
    "created_by",
    "created_at",
    "updated_at",
}


def test_create_defaults_and_five_chapters(
    security_client: TestClient,
    a6_data: A6Data,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """创建成功后，项目字段正确且默认五章已经入库。"""

    csrf = login(security_client)
    response = create_via_http(security_client, csrf)
    assert response.status_code == 201
    data = response.json()["data"]
    assert set(data) == PROJECT_FIELDS
    assert data["current_baseline_id"] is None
    assert data["baseline_dirty"] is True
    assert data["status"] == "active" and data["lock_version"] == 1
    assert data["created_by"] == str(a6_data.admin.user_id)
    assert data["created_at"].endswith("Z")
    project_id = uuid.UUID(data["id"])
    with tenant_transaction(a6_data.admin, runtime_factories["app"]) as db:
        chapters = (
            db.execute(
                select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.position)
            )
            .scalars()
            .all()
        )
        assert [chapter.title for chapter in chapters] == [
            "施工方案",
            "进度安排",
            "质量保证",
            "安全文明施工",
            "资源配置",
        ]
        assert [chapter.position for chapter in chapters] == list(range(5))
        assert all(chapter.parent_id is None and chapter.needs_review for chapter in chapters)
    detail = security_client.get(f"/api/v1/projects/{project_id}")
    assert detail.status_code == 200
    assert detail.json()["data"] == data


def test_http_pagination_no_duplicates_or_gaps(
    security_client: TestClient,
    a6_data: A6Data,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """验证 HTTP 参数、数据库排序和公共分页工具共同工作。"""

    login(security_client)
    ids = {uuid.uuid4() for _ in range(25)}
    moment = datetime(
        2026,
        10,
        11,
        tzinfo=UTC,
    )
    with tenant_transaction(a6_data.admin, runtime_factories["app"]) as db:
        db.add_all(
            [
                Project(
                    id=project_id,
                    org_id=a6_data.admin.org_id,
                    created_by=a6_data.admin.user_id,
                    name=f"分页工程-{number}",
                    construction_type="道路",
                    created_at=moment,
                )
                for number, project_id in enumerate(ids)
            ]
        )
    first = security_client.get("/api/v1/projects", params={"q": "分页工程", "status": "active"})
    assert first.status_code == 200
    page1 = first.json()["data"]
    assert len(page1["items"]) == 20 and page1["has_more"] is True
    second = security_client.get(
        "/api/v1/projects",
        params={
            "q": "分页工程",
            "status": "active",
            "cursor": page1["next_cursor"],
        },
    )
    assert second.status_code == 200
    page2 = second.json()["data"]
    assert len(page2["items"]) == 5
    assert page2["has_more"] is False and page2["next_cursor"] is None
    actual = [uuid.UUID(item["id"]) for item in page1["items"] + page2["items"]]
    assert actual == sorted(ids, reverse=True)
    assert len(set(actual)) == 25
    changed = security_client.get(
        "/api/v1/projects",
        params={
            "q": "别的工程",
            "status": "active",
            "cursor": page1["next_cursor"],
        },
    )
    assert changed.status_code == 422
    for params in [{"limit": 0}, {"limit": 101}, {"q": ""}, {"status": "bad"}, {"cursor": "%%%"}]:
        assert security_client.get("/api/v1/projects", params=params).status_code == 422


def test_only_one_concurrent_update_succeeds(
    a6_data: A6Data,
    parallel_factory: sessionmaker[Session],
) -> None:
    """两个请求使用同一版本号修改同一项目，只有一个成功。"""

    created = create_project(
        a6_data.admin,
        parallel_factory,
        name="并发工程",
        construction_type="道路",
        request_id=str(uuid.uuid4()),
        idempotency_key=None,
    )
    project_id = uuid.UUID(created.body["data"]["id"])
    barrier = threading.Barrier(2)

    def save(description: str) -> tuple[int, str]:
        barrier.wait(timeout=10)
        try:
            updated = update_project(
                a6_data.admin,
                parallel_factory,
                project_id=project_id,
                expected_lock_version=1,
                changes={"description": description},
            )
            return 200, str(updated["description"])
        except ApiError as exc:
            return exc.status_code, exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save, "甲提交")
        second = pool.submit(save, "乙提交")
        outcomes = [first.result(timeout=15), second.result(timeout=15)]
    assert sorted(status for status, _ in outcomes) == [200, 409]
    assert (409, "VERSION_CONFLICT") in outcomes
    with tenant_transaction(a6_data.admin, parallel_factory) as db:
        current = db.get(Project, project_id)
        assert current is not None and current.lock_version == 2
        assert current.description in {"甲提交", "乙提交"}


def test_name_change_marks_chapters_for_review(
    a6_data: A6Data,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """项目名称变化后，基准变脏并让章节重新复核。"""

    factory = runtime_factories["app"]
    result = create_project(
        a6_data.admin,
        factory,
        name="原名称",
        construction_type="道路",
        request_id=str(uuid.uuid4()),
        idempotency_key=None,
    )
    project_id = uuid.UUID(result.body["data"]["id"])
    # 模拟将来完成基准发布和复核的状态；这只是受控测试数据准备。
    with tenant_transaction(a6_data.admin, factory) as db:
        db.execute(update(Project).where(Project.id == project_id).values(baseline_dirty=False))
        db.execute(
            update(Chapter).where(Chapter.project_id == project_id).values(needs_review=False)
        )
    updated = update_project(
        a6_data.admin,
        factory,
        project_id=project_id,
        expected_lock_version=1,
        changes={"name": "新名称"},
    )
    assert updated["baseline_dirty"] is True
    assert updated["lock_version"] == 2
    with tenant_transaction(a6_data.admin, factory) as db:
        chapters = (
            db.execute(select(Chapter).where(Chapter.project_id == project_id)).scalars().all()
        )
        assert len(chapters) == 5
        assert all(chapter.needs_review and chapter.lock_version == 2 for chapter in chapters)


def test_create_idempotency_replay_and_conflict(
    security_client: TestClient,
    a6_data: A6Data,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """同键同体返回原结果，同键异体返回冲突。"""

    csrf = login(security_client)
    first = create_via_http(
        security_client,
        csrf,
        key="create-a6-project-001",
    )
    second = create_via_http(
        security_client,
        csrf,
        key="create-a6-project-001",
    )
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers["Idempotency-Replayed"] == "true"
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    assert first.json()["request_id"] == first.headers["X-Request-ID"]
    with tenant_transaction(a6_data.admin, runtime_factories["app"]) as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 1
        assert db.scalar(select(func.count()).select_from(Chapter)) == 5
        record = db.execute(select(IdempotencyKey)).scalar_one()
        assert record.status == "completed" and record.response_status == 201
    conflict = security_client.post(
        "/api/v1/projects",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "create-a6-project-001",
        },
        json={"name": "另一个请求体", "construction_type": "市政道路"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_persisted_processing_returns_retry_after(
    security_client: TestClient,
    a6_data: A6Data,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """已有 processing 记录时返回可重试错误和 Retry-After。"""

    csrf = login(security_client)
    key = "processing-a6-project-001"
    payload = CreateProjectRequest(name="A6 测试工程", construction_type="市政道路")
    with tenant_transaction(a6_data.admin, runtime_factories["app"]) as db:
        db.add(
            IdempotencyKey(
                id=uuid.uuid4(),
                org_id=a6_data.admin.org_id,
                user_id=a6_data.admin.user_id,
                http_method="POST",
                canonical_path="/api/v1/projects",
                key_hash=digest(key),
                request_hash=digest(canonical_json(payload.model_dump(mode="json"))),
                status="processing",
                processing_owner=uuid.uuid4(),
                processing_expires_at=datetime.now(UTC) + timedelta(minutes=1),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
    response = create_via_http(
        security_client,
        csrf,
        key=key,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
    assert response.json()["error"]["retryable"] is True
    assert response.headers["Retry-After"] == "1"
    with tenant_transaction(a6_data.admin, runtime_factories["app"]) as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 0


def test_inflight_transaction_returns_in_progress(
    a6_data: A6Data,
    parallel_factory: sessionmaker[Session],
) -> None:
    """第一笔事务尚未提交时，竞争请求也应立即识别处理中。"""

    entered = threading.Event()
    release = threading.Event()

    def first_request() -> WriteResult:
        with tenant_transaction(a6_data.admin, parallel_factory) as db:

            def action() -> WriteResult:
                entered.set()
                if not release.wait(timeout=10):
                    raise RuntimeError("test release timeout")
                return WriteResult(201, {"data": {"value": 1}, "request_id": "first"})

            return execute_idempotent(
                db,
                a6_data.admin,
                key="inflight-test-001",
                method="POST",
                canonical_path="/test/inflight",
                payload={"value": 1},
                action=action,
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first_request)
        try:
            assert entered.wait(timeout=10)
            with (
                pytest.raises(ApiError) as exc,
                tenant_transaction(a6_data.admin, parallel_factory) as db,
            ):

                def unexpected_action() -> WriteResult:
                    """竞争请求不应该再次执行业务。"""
                    return WriteResult(201, {"wrong": True})

                execute_idempotent(
                    db,
                    a6_data.admin,
                    key="inflight-test-001",
                    method="POST",
                    canonical_path="/test/inflight",
                    payload={"value": 1},
                    action=unexpected_action,
                )
            assert exc.value.code == "IDEMPOTENCY_IN_PROGRESS"
        finally:
            release.set()
        assert future.result(timeout=15).status_code == 201


def test_failure_rolls_back_business_and_idempotency(
    a6_data: A6Data,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """业务异常时，项目和幂等记录一起回滚。"""

    factory = runtime_factories["app"]
    with pytest.raises(RuntimeError), tenant_transaction(a6_data.admin, factory) as db:

        def failing_action() -> WriteResult:
            db.add(
                Project(
                    id=uuid.uuid4(),
                    org_id=a6_data.admin.org_id,
                    created_by=a6_data.admin.user_id,
                    name="不应留下的工程",
                    construction_type="道路",
                )
            )
            db.flush()
            raise RuntimeError("模拟建立章节前失败")

        execute_idempotent(
            db,
            a6_data.admin,
            key="rollback-test-001",
            method="POST",
            canonical_path="/test/rollback",
            payload={},
            action=failing_action,
        )
    with tenant_transaction(a6_data.admin, factory) as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 0
        assert db.scalar(select(func.count()).select_from(IdempotencyKey)) == 0


def test_archive_requires_key_checks_version_and_replays(
    security_client: TestClient,
    a6_data: A6Data,
) -> None:
    """归档检查幂等键和版本，重复请求重放且归档后只读。"""

    csrf = login(security_client)
    created = create_via_http(security_client, csrf)
    project_id = created.json()["data"]["id"]
    path = f"/api/v1/projects/{project_id}/archive"
    missing = security_client.post(
        path,
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_lock_version": 1,
        },
    )
    assert missing.status_code == 422
    stale = security_client.post(
        path,
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "archive-stale-001",
        },
        json={"expected_lock_version": 99},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["error"]["details"]["current_lock_version"] == 1
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "archive-good-001"}
    first = security_client.post(
        path,
        headers=headers,
        json={"expected_lock_version": 1},
    )
    replay = security_client.post(
        path,
        headers=headers,
        json={"expected_lock_version": 1},
    )
    assert first.status_code == replay.status_code == 200
    assert first.json()["data"]["status"] == "archived"
    assert first.json()["data"]["lock_version"] == 2
    assert first.json() == replay.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    edit = security_client.patch(
        f"/api/v1/projects/{project_id}",
        headers={
            "X-CSRF-Token": csrf,
        },
        json={"expected_lock_version": 2, "description": "不能修改"},
    )
    assert edit.status_code == 409
    assert edit.json()["error"]["code"] == "PROJECT_ARCHIVED"
    assert security_client.get(f"/api/v1/projects/{project_id}").status_code == 200
    archived = security_client.get("/api/v1/projects", params={"status": "archived"})
    assert [item["id"] for item in archived.json()["data"]["items"]] == [project_id]


def test_roles_and_csrf(security_client: TestClient, a6_data: A6Data) -> None:
    """writer 不能归档、reviewer 不能创建、写请求需要 CSRF。"""

    writer_csrf = login(security_client, role="writer")
    created = create_via_http(security_client, writer_csrf)
    assert created.status_code == 201
    project_id = created.json()["data"]["id"]
    archive = security_client.post(
        f"/api/v1/projects/{project_id}/archive",
        headers={
            "X-CSRF-Token": writer_csrf,
            "Idempotency-Key": "writer-archive-001",
        },
        json={"expected_lock_version": 1},
    )
    assert archive.status_code == 403
    csrf_missing = security_client.post(
        "/api/v1/projects",
        json={
            "name": "工程",
            "construction_type": "道路",
        },
    )
    assert csrf_missing.status_code == 403
    reviewer_csrf = login(security_client, role="reviewer")
    assert security_client.get("/api/v1/projects").status_code == 200
    assert create_via_http(security_client, reviewer_csrf).status_code == 403


@pytest.mark.skip(reason="TODO(A8): runs 表与项目锁协调完成后启用，验证 RUNS_ACTIVE")
def test_archive_rejects_nonterminal_runs() -> None:
    """A8 回填真实运行检查前，明确保留跳过标记。"""

    pytest.fail("A8：准备 queued/running/waiting_input 运行后归档应返回 409 RUNS_ACTIVE")


def test_expired_completed_key_can_be_reused(
    a6_data: A6Data,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """completed 记录超过有效期后可作为新请求处理。"""

    factory = runtime_factories["app"]
    payload = CreateProjectRequest(name="过期重试工程", construction_type="道路")
    first = create_project(
        a6_data.admin,
        factory,
        name=payload.name,
        construction_type=payload.construction_type,
        description=payload.description,
        data_classification=payload.data_classification,
        request_id="first",
        idempotency_key="expire-a6-001",
    )
    with tenant_transaction(a6_data.admin, factory) as db:
        db.execute(
            update(IdempotencyKey).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    second = create_project(
        a6_data.admin,
        factory,
        name=payload.name,
        construction_type=payload.construction_type,
        description=payload.description,
        data_classification=payload.data_classification,
        request_id="second",
        idempotency_key="expire-a6-001",
    )
    assert first.resource_id != second.resource_id
    assert second.replayed is False
    with tenant_transaction(a6_data.admin, factory) as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 2
        assert db.scalar(select(func.count()).select_from(IdempotencyKey)) == 1


def test_idempotency_scope_and_permissions(
    a6_data: A6Data,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """同键按用户隔离，调度角色不能读取缓存响应。"""

    factory = runtime_factories["app"]
    payload = CreateProjectRequest(name="用户作用域工程", construction_type="道路")
    admin_result = create_project(
        a6_data.admin,
        factory,
        name=payload.name,
        construction_type=payload.construction_type,
        description=payload.description,
        data_classification=payload.data_classification,
        request_id="admin",
        idempotency_key="same-key-a6-001",
    )
    with tenant_transaction(a6_data.writer, factory) as db:
        assert db.execute(select(IdempotencyKey)).scalars().all() == []
    writer_result = create_project(
        a6_data.writer,
        factory,
        name=payload.name,
        construction_type=payload.construction_type,
        description=payload.description,
        data_classification=payload.data_classification,
        request_id="writer",
        idempotency_key="same-key-a6-001",
    )
    assert admin_result.resource_id != writer_result.resource_id
    with runtime_factories["ops"]() as db, pytest.raises(DBAPIError) as exc:
        db.execute(text("SELECT response_json FROM ops.idempotency_keys")).all()
    sqlstate = getattr(
        exc.value.orig,
        "sqlstate",
        None,
    ) or getattr(
        exc.value.orig,
        "pgcode",
        None,
    )
    assert sqlstate == "42501"
    with factory() as db:
        assert db.execute(select(IdempotencyKey)).scalars().all() == []
