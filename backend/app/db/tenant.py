from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.principal import Principal


@contextmanager
def tenant_transaction(
    actor: Principal,
    session_factory: sessionmaker[Session],
) -> Iterator[Session]:
    """每次调用都创建独立 Session 和事务。"""
    with session_factory() as db, db.begin():
        db.execute(
            text(
                """
                    SELECT
                        set_config('app.current_org_id', :org_id, true),
                        set_config('app.current_user_id', :user_id, true)
                    """
            ),
            {
                "org_id": str(actor.org_id),
                "user_id": str(actor.user_id),
            },
        )

        yield db
