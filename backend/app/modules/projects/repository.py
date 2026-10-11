import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.orm import Session

from backend.app.core.pagination import CursorAnchor
from backend.app.models.chapters import Chapter
from backend.app.models.projects import Project


def find_project(
    db: Session,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
) -> Project | None:
    """按组织和项目编号查询，同时刷新 Session 中可能缓存的旧值。"""

    return db.execute(
        select(Project)
        .where(
            Project.org_id == org_id,
            Project.id == project_id,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def fetch_projects(
    db: Session,
    *,
    org_id: uuid.UUID,
    q: str | None,
    status: str | None,
    anchor: CursorAnchor | None,
    size: int,
) -> Sequence[Project]:
    """按过滤条件和排序锚点查询项目，不在仓储内提交事务。"""

    statement = select(Project).where(Project.org_id == org_id)
    if q is not None:
        # autoescape 将 % 和 _ 作为普通字符，避免查询意外变成通配符。
        statement = statement.where(Project.name.icontains(q, autoescape=True))
    if status is not None:
        statement = statement.where(Project.status == status)
    if anchor is not None:
        statement = statement.where(
            tuple_(Project.created_at, Project.id) < tuple_(anchor.created_at, anchor.id)
        )
    return (
        db.execute(statement.order_by(Project.created_at.desc(), Project.id.desc()).limit(size))
        .scalars()
        .all()
    )


def update_project_if_version_matches(
    db: Session,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    expected_lock_version: int,
    changes: dict[str, Any],
) -> Project | None:
    """版本和状态匹配才更新，返回 None 表示没有记录满足条件。"""

    return db.execute(
        update(Project)
        .where(
            Project.org_id == org_id,
            Project.id == project_id,
            Project.lock_version == expected_lock_version,
            Project.status == "active",
        )
        .values(
            **changes,
            lock_version=Project.lock_version + 1,
            updated_at=func.now(),
        )
        .returning(Project)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def mark_chapters_for_review(
    db: Session,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    # 这是项目修改带来的服务端失效操作，版本在数据库中逐行原子递增。
    # 只更新原先未标记的章节，避免无意义的重复增版。
    """项目上下文变化后，让已复核章节重新进入待复核状态。"""

    db.execute(
        update(Chapter)
        .where(
            Chapter.org_id == org_id,
            Chapter.project_id == project_id,
            Chapter.needs_review.is_(False),
        )
        .values(
            needs_review=True,
            lock_version=Chapter.lock_version + 1,
            updated_at=func.now(),
        )
    )
