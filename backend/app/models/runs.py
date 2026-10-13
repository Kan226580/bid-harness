import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class Run(Base):
    """一次执行的状态、冻结输入、预算与租约。"""

    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_runs_org_id"),
        UniqueConstraint("org_id", "project_id", "id", name="uq_runs_org_project_id"),
        ForeignKeyConstraint(["org_id", "project_id"], ["app.projects.org_id", "app.projects.id"]),
        ForeignKeyConstraint(
            ["org_id", "project_id", "chapter_id"],
            ["app.chapters.org_id", "app.chapters.project_id", "app.chapters.id"],
        ),
        ForeignKeyConstraint(["org_id", "retry_of_run_id"], ["app.runs.org_id", "app.runs.id"]),
        ForeignKeyConstraint(["org_id", "created_by"], ["auth.users.org_id", "auth.users.id"]),
        {"schema": "app"},
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
    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(20))
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    baseline_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    retry_of_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(
        String(30), default="queued", server_default=text("'queued'")
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    snapshot_hash: Mapped[str] = mapped_column(CHAR(64))
    model_mode: Mapped[str] = mapped_column(String(20))
    current_step: Mapped[str | None] = mapped_column(String(100))
    max_calls: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    max_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    max_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    calls_started: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    tokens_observed: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    tokens_reserved: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    cost_observed_cny: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), default=Decimal("0"), server_default=text("0")
    )
    cost_reserved_cny: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), default=Decimal("0"), server_default=text("0")
    )
    has_unknown_calls: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    lease_owner: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_epoch: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_generation: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    recovery_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    artifact_type: Mapped[str | None] = mapped_column(String(50))
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_retryable: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    last_event_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    created_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunStep(Base):
    """逻辑节点用 step_key 唯一标识，重做时保留同一条记录。"""

    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_steps_org_id"),
        UniqueConstraint("org_id", "run_id", "step_key", name="uq_steps_key"),
        ForeignKeyConstraint(["org_id", "run_id"], ["app.runs.org_id", "app.runs.id"]),
        {"schema": "app"},
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
    step_key: Mapped[str] = mapped_column(String(150))
    node_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    lease_epoch: Mapped[int] = mapped_column(BigInteger)
    input_hash: Mapped[str] = mapped_column(CHAR(64))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunEvent(Base):
    """事件不可变；seq 在父运行行锁内分配。"""

    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_events_org_id"),
        UniqueConstraint("org_id", "run_id", "seq", name="uq_events_seq"),
        ForeignKeyConstraint(["org_id", "run_id"], ["app.runs.org_id", "app.runs.id"]),
        {"schema": "app"},
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
    seq: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(100))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
