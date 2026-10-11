import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class IdempotencyKey(Base):
    """持久化请求摘要、处理状态和可重放的响应。"""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "user_id",
            "http_method",
            "canonical_path",
            "key_hash",
            name="uq_idempotency_scope",
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["auth.users.org_id", "auth.users.id"],
            name="fk_idempotency_user_org",
        ),
        CheckConstraint("status IN ('processing','completed')", name="ck_idempotency_status"),
        CheckConstraint(
            "status <> 'completed' OR (response_status IS NOT NULL AND response_json IS NOT NULL)",
            name="ck_idempotency_completed_response",
        ),
        Index("ix_idempotency_expires_at", "expires_at"),
        {"schema": "ops"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("auth.organizations.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    http_method: Mapped[str] = mapped_column(String(10), nullable=False)
    canonical_path: Mapped[str] = mapped_column(String(500), nullable=False)
    key_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    processing_owner: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    processing_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now() + interval '24 hours'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
