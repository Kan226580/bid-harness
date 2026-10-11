import uuid

from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.authorization import Permission, authorize
from backend.app.core.errors import ApiError
from backend.app.core.principal import Principal
from backend.app.db.tenant import tenant_transaction
from backend.app.models.projects import Project
from backend.app.modules.projects.repository import find_project
from backend.app.modules.projects.schemas import project_summary


def resource_not_found() -> ApiError:
    return ApiError(
        404,
        "RESOURCE_NOT_FOUND",
        "资源不存在",
    )


def get_project(
    actor: Principal,
    session_factory: sessionmaker[Session],
    *,
    project_id: uuid.UUID,
) -> dict[str, object]:
    with tenant_transaction(actor, session_factory) as db:
        project = find_project(
            db,
            org_id=actor.org_id,
            project_id=project_id,
        )

        if project is None:
            raise resource_not_found()

        authorize(actor, Permission.READ_CONTENT)

        return project_summary(project)


def update_project_description(
    actor: Principal,
    session_factory: sessionmaker[Session],
    *,
    project_id: uuid.UUID,
    expected_lock_version: int,
    description: str,
) -> dict[str, object]:
    with tenant_transaction(actor, session_factory) as db:
        project = find_project(
            db,
            org_id=actor.org_id,
            project_id=project_id,
        )

        if project is None:
            raise resource_not_found()

        authorize(actor, Permission.EDIT_CONTENT)

        if project.status == "archived":
            raise ApiError(
                409,
                "PROJECT_ARCHIVED",
                "归档项目只读",
            )

        updated = db.execute(
            update(Project)
            .where(
                Project.org_id == actor.org_id,
                Project.id == project_id,
                Project.lock_version == expected_lock_version,
                Project.status == "active",
            )
            .values(
                description=description,
                lock_version=Project.lock_version + 1,
                updated_at=func.now(),
            )
            .returning(Project)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

        if updated is None:
            raise ApiError(
                409,
                "VERSION_CONFLICT",
                "内容已更新，请刷新后重试",
            )

        return project_summary(updated)
