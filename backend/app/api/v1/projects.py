import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from backend.app.api.errors import ok
from backend.app.core.principal import Principal
from backend.app.db.session import get_app_session_factory
from backend.app.modules.identity.business import require_actor
from backend.app.modules.identity.dependencies import AuthContext, require_csrf
from backend.app.modules.projects.schemas import UpdateProjectDescriptionRequest
from backend.app.modules.projects.service import (
    get_project,
    update_project_description,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("/{project_id}")
def project_detail(
    project_id: uuid.UUID,
    request: Request,
    actor: Principal = Depends(require_actor),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    data = get_project(
        actor,
        session_factory,
        project_id=project_id,
    )
    return ok(request, data)


@router.patch("/{project_id}")
def patch_project(
    project_id: uuid.UUID,
    payload: UpdateProjectDescriptionRequest,
    request: Request,
    actor: Principal = Depends(require_actor),
    _csrf: AuthContext = Depends(require_csrf),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    data = update_project_description(
        actor,
        session_factory,
        project_id=project_id,
        expected_lock_version=payload.expected_lock_version,
        description=payload.description,
    )
    return ok(request, data)
