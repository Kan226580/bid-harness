import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.projects import Project


def find_project(
    db: Session,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
) -> Project | None:
    return db.execute(
        select(Project).where(
            Project.org_id == org_id,
            Project.id == project_id,
        )
    ).scalar_one_or_none()
