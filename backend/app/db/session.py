from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.settings import settings

app_engine = create_engine(settings.db.url, pool_pre_ping=True)
auth_engine = create_engine(settings.auth_db.url, pool_pre_ping=True)

# expire_on_commit: commit之后对象属性还能读，接口返回响应时不会被提前置空
AppSessionLocal = sessionmaker(expire_on_commit=False, autoflush=False, bind=app_engine)

AuthSessionLocal = sessionmaker(expire_on_commit=False, autoflush=False, bind=auth_engine)


def get_auth_db() -> Iterator[Session]:
    """认证接口使用的数据库会话"""
    with AuthSessionLocal() as db:
        yield db


def get_app_session_factory() -> sessionmaker[Session]:
    """业务服务自行开启事务，依赖只提供会话工厂"""
    return AppSessionLocal
