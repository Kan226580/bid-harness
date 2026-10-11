import os
import re
import subprocess
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from backend.app.core.db_setting import (
    BASE_DIR,
    AdminSettings,
    MigrationSettings,
)
from backend.app.core.settings import settings
from backend.app.db.session import get_app_session_factory, get_auth_db
from backend.app.main import app

SAFE_DB_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _test_db_name() -> str:
    name = os.environ.get("HARNESS_TEST_DB_NAME", "harness_test")

    if not SAFE_DB_NAME_PATTERN.fullmatch(name):
        raise RuntimeError("测试数据库名不合法")

    if not name.endswith("_test"):
        raise RuntimeError("测试数据库名必须以 _test 结尾")

    protected_names = {
        settings.db.name,
        settings.auth_db.name,
        settings.ops_db.name,
        AdminSettings().admin_db.name,
        MigrationSettings().migration_db.name,
    }

    if name in protected_names:
        raise RuntimeError("测试数据库不能与开发或管理配置中的数据库同名")

    return name


def _assert_test_engine(engine: Engine) -> None:
    if engine.url.database != _test_db_name():
        raise RuntimeError("拒绝在非测试数据库执行管理操作")


def _reset_test_schema(engine: Engine) -> None:
    _assert_test_engine(engine)

    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS app CASCADE"))
        connection.execute(text("DROP SCHEMA IF EXISTS ops CASCADE"))
        connection.execute(text("DROP SCHEMA IF EXISTS auth CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS public.alembic_version"))


def _upgrade_test_database() -> None:
    env = {
        **os.environ,
        "MIGRATION_DB__NAME": _test_db_name(),
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(BASE_DIR / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        pytest.fail(f"测试库迁移失败：\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    admin_config = AdminSettings().admin_db
    migration_config = MigrationSettings().migration_db

    target_server = (settings.db.host, settings.db.port)

    for config in (admin_config, migration_config):
        if (config.host, config.port) != target_server:
            raise RuntimeError("测试管理、迁移和运行时连接必须指向同一服务器")

    test_name = _test_db_name()

    maintenance_engine = create_engine(
        admin_config.url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )

    try:
        with maintenance_engine.connect() as connection:
            is_superuser = connection.execute(
                text(
                    """
                    SELECT rolsuper
                    FROM pg_roles
                    WHERE rolname = current_user
                    """
                )
            ).scalar_one()

            if not is_superuser:
                raise RuntimeError("测试库管理连接需要数据库管理员权限")

            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": test_name},
            ).scalar()

            if not exists:
                connection.execute(text(f'CREATE DATABASE "{test_name}"'))
    finally:
        maintenance_engine.dispose()

    admin_engine = create_engine(
        admin_config.url.set(database=test_name),
        poolclass=NullPool,
    )

    try:
        bootstrap_sql = (BASE_DIR / "scripts" / "sql" / "bootstrap_roles.sql").read_text(
            encoding="utf-8"
        )

        with admin_engine.begin() as connection:
            connection.execute(text(bootstrap_sql))

        _reset_test_schema(admin_engine)
        _upgrade_test_database()

        yield admin_engine
    finally:
        try:
            _reset_test_schema(admin_engine)
        finally:
            admin_engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    """保留现有认证测试使用的事务回滚方式。"""
    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )

    try:
        yield session
    finally:
        session.close()

        if transaction.is_active:
            transaction.rollback()

        connection.close()


@pytest.fixture
def api_client(db_session: Session) -> Iterator[TestClient]:
    """仅供已有认证流程测试使用，不用于证明 RLS。"""

    def override_auth_db() -> Iterator[Session]:
        yield db_session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_auth_db] = override_auth_db

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture(scope="session")
def runtime_factories(
    engine: Engine,
) -> Iterator[dict[str, sessionmaker[Session]]]:
    _assert_test_engine(engine)

    configs = {
        "app": settings.db,
        "auth": settings.auth_db,
        "ops": settings.ops_db,
    }

    runtime_engines: dict[str, Engine] = {}
    factories: dict[str, sessionmaker[Session]] = {}

    try:
        for name, config in configs.items():
            runtime_engine = create_engine(
                config.url.set(database=_test_db_name()),
                pool_size=1,
                max_overflow=0,
                pool_pre_ping=True,
            )

            runtime_engines[name] = runtime_engine
            factories[name] = sessionmaker(
                bind=runtime_engine,
                expire_on_commit=False,
                autoflush=False,
            )

        yield factories
    finally:
        for runtime_engine in runtime_engines.values():
            runtime_engine.dispose()


@pytest.fixture
def security_client(
    runtime_factories: dict[str, sessionmaker[Session]],
) -> Iterator[TestClient]:
    """安全测试使用真实认证角色和业务角色。"""

    def override_auth_db() -> Iterator[Session]:
        with runtime_factories["auth"]() as db:
            yield db

    def override_app_factory() -> sessionmaker[Session]:
        return runtime_factories["app"]

    previous = app.dependency_overrides.copy()

    app.dependency_overrides[get_auth_db] = override_auth_db
    app.dependency_overrides[get_app_session_factory] = override_app_factory

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
