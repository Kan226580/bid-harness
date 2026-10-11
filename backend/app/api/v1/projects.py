import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session, sessionmaker

from backend.app.api.errors import ok, request_id_of
from backend.app.api.schemas import CursorPage, SuccessResponse
from backend.app.core.principal import Principal
from backend.app.db.session import get_app_session_factory
from backend.app.modules.identity.business import require_actor
from backend.app.modules.identity.dependencies import AuthContext, require_csrf
from backend.app.modules.operations.idempotency import WriteResult, validate_idempotency_key
from backend.app.modules.projects.schemas import (
    ArchiveProjectRequest,
    CreateProjectRequest,
    ProjectResponse,
    UpdateProjectRequest,
)
from backend.app.modules.projects.service import (
    archive_project,
    create_project,
    get_project,
    list_projects,
    update_project,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def apply_write_result(response: Response, result: WriteResult) -> dict[str, Any]:
    """设置创建或归档的状态码，重放时附加标记。"""
    response.status_code = result.status_code

    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"

    return result.body


@router.get("", response_model=SuccessResponse[CursorPage[ProjectResponse]])
def project_list(
    request: Request,
    q: str | None = Query(
        default=None,
        min_length=1,
        max_length=100,
    ),
    status: Literal["active", "archived"] | None = Query(default=None),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    cursor: str | None = Query(default=None, max_length=2048),
    actor: Principal = Depends(require_actor),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    """查询项目列表。"""
    data = list_projects(
        actor,
        session_factory,
        q=q,
        status=status,
        limit=limit,
        cursor=cursor,
    )

    return ok(request, data)


@router.post(
    "",
    status_code=201,
    response_model=SuccessResponse[ProjectResponse],
)
def project_create(
    payload: CreateProjectRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: Principal = Depends(require_actor),
    _csrf: AuthContext = Depends(require_csrf),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    """创建项目，幂等键可选。"""
    key = validate_idempotency_key(
        idempotency_key,
        required=False,
    )

    result = create_project(
        actor,
        session_factory,
        name=payload.name,
        construction_type=payload.construction_type,
        description=payload.description,
        data_classification=payload.data_classification,
        request_id=request_id_of(request),
        idempotency_key=key,
    )

    return apply_write_result(response, result)


@router.get("/{project_id}", response_model=SuccessResponse[ProjectResponse])
def project_detail(
    project_id: uuid.UUID,
    request: Request,
    actor: Principal = Depends(require_actor),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    """读取本组织项目详情。"""
    data = get_project(
        actor,
        session_factory,
        project_id=project_id,
    )

    return ok(request, data)


@router.patch("/{project_id}", response_model=SuccessResponse[ProjectResponse])
def patch_project(
    project_id: uuid.UUID,
    payload: UpdateProjectRequest,
    request: Request,
    actor: Principal = Depends(require_actor),
    _csrf: AuthContext = Depends(require_csrf),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    """修改项目，省略的字段保持原值。"""
    changes = payload.model_dump(
        exclude_unset=True,
        exclude={"expected_lock_version"},
    )

    data = update_project(
        actor,
        session_factory,
        project_id=project_id,
        expected_lock_version=payload.expected_lock_version,
        changes=changes,
    )

    return ok(request, data)


@router.post("/{project_id}/archive", response_model=SuccessResponse[ProjectResponse])
def project_archive(
    project_id: uuid.UUID,
    payload: ArchiveProjectRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: Principal = Depends(require_actor),
    _csrf: AuthContext = Depends(require_csrf),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    """管理员归档项目，必须携带幂等键。"""
    key = validate_idempotency_key(
        idempotency_key,
        required=True,
    )
    assert key is not None

    result = archive_project(
        actor,
        session_factory,
        project_id=project_id,
        expected_lock_version=payload.expected_lock_version,
        request_id=request_id_of(request),
        idempotency_key=key,
    )

    return apply_write_result(response, result)
