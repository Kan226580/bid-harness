from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.settings import settings

ops_engine = create_engine(
    settings.ops_db.url,
    pool_pre_ping=True,
)

OpsSessionLocal = sessionmaker(
    bind=ops_engine,
    expire_on_commit=False,
    autoflush=False,
)
