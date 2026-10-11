import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class Chapter(Base):
    """A6 的章节目录模型，暂不包含正文版本和选用指针。"""

    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "id",
            name="uq_chapters_org_id_id",
        ),
        UniqueConstraint(
            "org_id",
            "project_id",
            "id",
            name="uq_chapters_org_project_id",
        ),
        ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["app.projects.org_id", "app.projects.id"],
            name="fk_chapters_project_org",
        ),
        ForeignKeyConstraint(
            ["org_id", "project_id", "parent_id"],
            ["app.chapters.org_id", "app.chapters.project_id", "app.chapters.id"],
            name="fk_chapters_parent_project",
        ),
        ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["auth.users.org_id", "auth.users.id"],
            name="fk_chapters_created_by_org",
        ),
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="ck_chapters_title"),
        CheckConstraint(
            "chapter_type IN ('construction','schedule','quality','safety','resources','custom')",
            name="ck_chapters_type",
        ),
        CheckConstraint("position >= 0", name="ck_chapters_position"),
        CheckConstraint("lock_version > 0", name="ck_chapters_lock_version"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_chapters_not_self"),
        Index(
            "ix_chapters_directory",
            "org_id",
            "project_id",
            "parent_id",
            "position",
        ),
        {"schema": "app"},
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
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    chapter_type: Mapped[str] = mapped_column(String(20), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    needs_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
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
    lock_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )


Index(
    "uq_chapters_sibling_position",
    Chapter.org_id,
    Chapter.project_id,
    func.coalesce(Chapter.parent_id, text("'00000000-0000-0000-0000-000000000000'::uuid")),
    Chapter.position,
    unique=True,
)
