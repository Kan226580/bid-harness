import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class Outbox(Base):
    """只存调度元数据，不存原文。"""

    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_outbox_org_id"),
        UniqueConstraint("org_id", "run_id", "dispatch_generation", name="uq_outbox_generation"),
        ForeignKeyConstraint(["org_id", "run_id"], ["app.runs.org_id", "app.runs.id"]),
        {"schema": "ops"},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("auth.organizations.id")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True))
    dispatch_generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claim_owner: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
