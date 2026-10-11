import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.principal import Principal
from backend.app.core.security import hash_password
from backend.app.db.tenant import tenant_transaction
from backend.app.models import Organization, Project, User

TEST_PASSWORD = "tenant-test-password-2026"


@dataclass(frozen=True)
class TenantData:
    alpha_admin: Principal
    alpha_writer: Principal
    beta_admin: Principal

    alpha_project_id: uuid.UUID
    beta_project_id: uuid.UUID


def _clear_test_data(engine: Engine) -> None:
    database_name = engine.url.database

    if database_name is None or not database_name.endswith("_test"):
        raise RuntimeError("拒绝清理非测试数据库")

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    app.projects,
                    auth.sessions,
                    auth.users,
                    auth.organizations
                RESTART IDENTITY CASCADE
                """
            )
        )


@pytest.fixture
def tenant_data(
    engine: Engine,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> Iterator[TenantData]:
    _clear_test_data(engine)

    alpha_org_id = uuid.uuid4()
    beta_org_id = uuid.uuid4()

    data = TenantData(
        alpha_admin=Principal(alpha_org_id, uuid.uuid4(), "admin"),
        alpha_writer=Principal(alpha_org_id, uuid.uuid4(), "writer"),
        beta_admin=Principal(beta_org_id, uuid.uuid4(), "admin"),
        alpha_project_id=uuid.uuid4(),
        beta_project_id=uuid.uuid4(),
    )

    try:
        password_hash = hash_password(TEST_PASSWORD)

        with runtime_factories["auth"].begin() as db:
            db.add_all(
                [
                    Organization(
                        id=alpha_org_id,
                        slug="alpha",
                        name="甲组织",
                    ),
                    Organization(
                        id=beta_org_id,
                        slug="beta",
                        name="乙组织",
                    ),
                ]
            )
            db.flush()

            db.add_all(
                [
                    User(
                        id=data.alpha_admin.user_id,
                        org_id=alpha_org_id,
                        email="alpha-admin@example.invalid",
                        display_name="甲管理员",
                        role="admin",
                        password_hash=password_hash,
                        must_change_password=False,
                    ),
                    User(
                        id=data.alpha_writer.user_id,
                        org_id=alpha_org_id,
                        email="alpha-writer@example.invalid",
                        display_name="甲编写者",
                        role="writer",
                        password_hash=password_hash,
                        must_change_password=False,
                    ),
                    User(
                        id=data.beta_admin.user_id,
                        org_id=beta_org_id,
                        email="beta-admin@example.invalid",
                        display_name="乙管理员",
                        role="admin",
                        password_hash=password_hash,
                        must_change_password=False,
                    ),
                ]
            )

        with tenant_transaction(
            data.alpha_admin,
            runtime_factories["app"],
        ) as db:
            db.add(
                Project(
                    id=data.alpha_project_id,
                    org_id=alpha_org_id,
                    name="甲组织项目",
                    construction_type="市政道路",
                    description="甲组织原描述",
                    created_by=data.alpha_admin.user_id,
                )
            )

        with tenant_transaction(
            data.beta_admin,
            runtime_factories["app"],
        ) as db:
            db.add(
                Project(
                    id=data.beta_project_id,
                    org_id=beta_org_id,
                    name="乙组织项目",
                    construction_type="市政道路",
                    description="乙组织原描述",
                    created_by=data.beta_admin.user_id,
                )
            )

        yield data
    finally:
        _clear_test_data(engine)


def _login(
    client: TestClient,
    *,
    role: str = "writer",
) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "organization_slug": "alpha",
            "email": f"alpha-{role}@example.invalid",
            "password": TEST_PASSWORD,
        },
    )

    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def _sqlstate(exc: DBAPIError) -> str | None:
    value = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return str(value) if value is not None else None


def test_cross_org_read_returns_404(
    security_client: TestClient,
    tenant_data: TenantData,
) -> None:
    """跨组织读取返回 404"""
    _login(security_client)

    own = security_client.get(f"/api/v1/projects/{tenant_data.alpha_project_id}")
    assert own.status_code == 200

    foreign = security_client.get(f"/api/v1/projects/{tenant_data.beta_project_id}")
    missing = security_client.get(f"/api/v1/projects/{uuid.uuid4()}")

    assert foreign.status_code == 404
    assert missing.status_code == 404

    assert foreign.json()["error"] == missing.json()["error"]
    assert foreign.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_cross_org_update_returns_404(
    security_client: TestClient,
    tenant_data: TenantData,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """跨组织修改返回 404"""
    csrf = _login(security_client)

    own = security_client.patch(
        f"/api/v1/projects/{tenant_data.alpha_project_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_lock_version": 1,
            "description": "修改自己的项目",
        },
    )
    assert own.status_code == 200
    assert own.json()["data"]["lock_version"] == 2

    foreign = security_client.patch(
        f"/api/v1/projects/{tenant_data.beta_project_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_lock_version": 1,
            "description": "尝试修改乙组织项目",
        },
    )

    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    with tenant_transaction(
        tenant_data.beta_admin,
        runtime_factories["app"],
    ) as db:
        project = db.execute(
            select(Project).where(Project.id == tenant_data.beta_project_id)
        ).scalar_one()

        assert project.description == "乙组织原描述"
        assert project.lock_version == 1


def test_missing_tenant_context_returns_no_rows(
    tenant_data: TenantData,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """没有租户上下文时为空"""
    factory = runtime_factories["app"]

    with tenant_transaction(tenant_data.alpha_admin, factory) as db:
        visible_ids = db.execute(text("SELECT id FROM app.projects")).scalars().all()

        assert visible_ids == [tenant_data.alpha_project_id]

    with factory() as db, db.begin():
        rows = db.execute(text("SELECT id FROM app.projects")).all()

        assert rows == []


def test_app_role_cannot_read_passwords(
    tenant_data: TenantData,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """业务角色不能读密码"""
    factory = runtime_factories["app"]

    with factory() as db:
        assert db.execute(text("SELECT session_user")).scalar_one() == "app_runtime"

    with pytest.raises(DBAPIError) as exc_info, factory() as db, db.begin():
        db.execute(text("SELECT password_hash FROM auth.users")).all()

    assert _sqlstate(exc_info.value) == "42501"


def test_writer_cannot_create_members(
    security_client: TestClient,
    tenant_data: TenantData,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """writer 管理成员被拒绝"""
    csrf = _login(security_client)

    response = security_client.post(
        "/api/v1/members",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "new-member@example.invalid",
            "display_name": "新成员",
            "role": "writer",
            "temporary_password": "member-password-2026",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    with runtime_factories["auth"]() as db:
        user = db.execute(
            select(User).where(
                User.org_id == tenant_data.alpha_writer.org_id,
                User.email == "new-member@example.invalid",
            )
        ).scalar_one_or_none()

        assert user is None


def test_admin_can_create_member_in_own_org(
    security_client: TestClient,
    tenant_data: TenantData,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """管理员成功对照"""
    csrf = _login(security_client, role="admin")

    response = security_client.post(
        "/api/v1/members",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "new-member@example.invalid",
            "display_name": "新成员",
            "role": "writer",
            "temporary_password": "member-password-2026",
        },
    )

    assert response.status_code == 201

    body = response.json()["data"]
    assert body["must_change_password"] is True
    assert "password_hash" not in body
    assert "temporary_password" not in body

    with runtime_factories["auth"]() as db:
        user = db.execute(
            select(User).where(User.email == "new-member@example.invalid")
        ).scalar_one()

        assert user.org_id == tenant_data.alpha_admin.org_id


def test_rls_blocks_unscoped_read_and_update(
    tenant_data: TenantData,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """漏写组织过滤，仍然不能读取或修改其他组织"""
    factory = runtime_factories["app"]

    with tenant_transaction(tenant_data.alpha_admin, factory) as db:
        foreign = db.execute(
            text(
                """
                SELECT id
                FROM app.projects
                WHERE id = :project_id
                """
            ),
            {"project_id": tenant_data.beta_project_id},
        ).scalar_one_or_none()

        assert foreign is None

        changed = db.execute(
            text(
                """
                UPDATE app.projects
                SET description = '非法修改'
                WHERE id = :project_id
                RETURNING id
                """
            ),
            {"project_id": tenant_data.beta_project_id},
        ).all()

        assert changed == []

    with tenant_transaction(tenant_data.beta_admin, factory) as db:
        description = db.execute(
            text(
                """
                SELECT description
                FROM app.projects
                WHERE id = :project_id
                """
            ),
            {"project_id": tenant_data.beta_project_id},
        ).scalar_one()

        assert description == "乙组织原描述"


def test_rls_rejects_insert_for_another_org(
    tenant_data: TenantData,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """WITH CHECK 阻止伪造组织插入"""
    with (
        pytest.raises(DBAPIError) as exc_info,
        tenant_transaction(
            tenant_data.alpha_admin,
            runtime_factories["app"],
        ) as db,
    ):
        db.execute(
            text(
                """
                    INSERT INTO app.projects (
                        id,
                        org_id,
                        name,
                        construction_type,
                        created_by
                    )
                    VALUES (
                        :id,
                        :org_id,
                        '伪造项目',
                        '市政道路',
                        :created_by
                    )
                    """
            ),
            {
                "id": uuid.uuid4(),
                "org_id": tenant_data.beta_admin.org_id,
                "created_by": tenant_data.beta_admin.user_id,
            },
        )

    assert _sqlstate(exc_info.value) == "42501"


def test_client_cannot_supply_org_id(
    security_client: TestClient,
    tenant_data: TenantData,
) -> None:
    """客户端不能在请求体中指定组织"""
    csrf = _login(security_client)

    response = security_client.patch(
        f"/api/v1/projects/{tenant_data.alpha_project_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_lock_version": 1,
            "description": "正常描述",
            "org_id": str(tenant_data.beta_admin.org_id),
        },
    )

    assert response.status_code == 422


def test_app_role_and_business_tables_are_restricted(
    tenant_data: TenantData,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """检查业务角色及所有业务表安全设置"""
    with runtime_factories["app"]() as db:
        flags = db.execute(
            text(
                """
                SELECT
                    rolsuper,
                    rolbypassrls,
                    rolcreatedb,
                    rolcreaterole
                FROM pg_roles
                WHERE rolname = current_user
                """
            )
        ).one()

        assert tuple(flags) == (False, False, False, False)

        tables = db.execute(
            text(
                """
                SELECT
                    c.relname,
                    c.relrowsecurity,
                    c.relforcerowsecurity,
                    pg_get_userbyid(c.relowner) AS owner_name,
                    EXISTS (
                        SELECT 1
                        FROM pg_policy p
                        WHERE p.polrelid = c.oid
                    ) AS has_policy
                FROM pg_class c
                JOIN pg_namespace n
                    ON n.oid = c.relnamespace
                WHERE n.nspname = 'app'
                  AND c.relkind IN ('r', 'p')
                """
            )
        ).all()

        assert tables

        for table in tables:
            assert table.relrowsecurity is True
            assert table.relforcerowsecurity is True
            assert table.owner_name == "migration_owner"
            assert table.has_policy is True


class RollbackForTest(Exception):
    pass


@pytest.mark.parametrize("finish", ["commit", "rollback"])
def test_tenant_context_does_not_leak_through_pool(
    finish: str,
    tenant_data: TenantData,
    runtime_factories: dict[str, sessionmaker[Session]],
) -> None:
    """连接池 串租户 回归测试"""
    factory = runtime_factories["app"]
    first_pid: int | None = None

    try:
        with tenant_transaction(
            tenant_data.alpha_admin,
            factory,
        ) as db:
            first_pid = db.execute(text("SELECT pg_backend_pid()")).scalar_one()

            ids = db.execute(text("SELECT id FROM app.projects")).scalars().all()

            assert ids == [tenant_data.alpha_project_id]

            if finish == "rollback":
                raise RollbackForTest()

    except RollbackForTest:
        assert finish == "rollback"

    assert first_pid is not None

    # 上一笔事务结束，连接已归还。
    # 这里重新借出连接，但不设置任何租户上下文。
    with factory() as db, db.begin():
        second_pid = db.execute(text("SELECT pg_backend_pid()")).scalar_one()

        assert second_pid == first_pid

        context = db.execute(
            text(
                """
                    SELECT
                        NULLIF(
                            current_setting('app.current_org_id', true),
                            ''
                        ),
                        NULLIF(
                            current_setting('app.current_user_id', true),
                            ''
                        )
                    """
            )
        ).one()

        assert tuple(context) == (None, None)

        rows = db.execute(text("SELECT id FROM app.projects")).all()

        assert rows == []

    # 同一连接随后仍能安全服务乙组织。
    with tenant_transaction(
        tenant_data.beta_admin,
        factory,
    ) as db:
        ids = db.execute(text("SELECT id FROM app.projects")).scalars().all()

        assert ids == [tenant_data.beta_project_id]
