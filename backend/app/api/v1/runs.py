import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import StreamingResponse

from backend.app.api.errors import ok, request_id_of
from backend.app.api.schemas import CursorPage, SuccessResponse
from backend.app.core.principal import Principal
from backend.app.db.session import get_app_session_factory
from backend.app.modules.identity.business import require_actor
from backend.app.modules.identity.dependencies import AuthContext, require_csrf
from backend.app.modules.operations.idempotency import WriteResult, validate_idempotency_key
from backend.app.modules.runs.schemas import (
    CancelRunRequest,
    ResumeRunRequest,
    RetryRunRequest,
    RunAcceptedResponse,
    RunKind,
    RunResponse,
    RunState,
)
from backend.app.modules.runs.service import control_run, get_run, list_runs
from backend.app.modules.runs.stream import event_response

router = APIRouter(tags=["runs"])


def apply_result(response: Response, result: WriteResult) -> dict[str, Any]:
    """延续项目接口的状态码和幂等重放响应头。"""
    response.status_code = result.status_code
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return result.body


@router.get("/projects/{project_id}/runs", response_model=SuccessResponse[CursorPage[RunResponse]])
def run_list(
    project_id: uuid.UUID,
    request: Request,
    kind: RunKind | None = Query(default=None),
    status: RunState | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=2048),
    actor: Principal = Depends(require_actor),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    data = list_runs(
        actor,
        session_factory,
        project_id=project_id,
        kind=kind,
        status=status,
        limit=limit,
        cursor=cursor,
    )
    return ok(request, data)


@router.get("/runs/{run_id}", response_model=SuccessResponse[RunResponse])
def run_detail(
    run_id: uuid.UUID,
    request: Request,
    actor: Principal = Depends(require_actor),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    data = get_run(actor, session_factory, run_id=run_id)
    return ok(request, data)


@router.post("/runs/{run_id}/cancel", status_code=202, response_model=SuccessResponse[RunResponse])
def run_cancel(
    run_id: uuid.UUID,
    payload: CancelRunRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: Principal = Depends(require_actor),
    _csrf: AuthContext = Depends(require_csrf),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    key = validate_idempotency_key(idempotency_key, required=True)
    assert key is not None
    result = control_run(
        actor,
        session_factory,
        run_id=run_id,
        operation="cancel",
        payload=payload.model_dump(),
        request_id=request_id_of(request),
        idempotency_key=key,
    )
    return apply_result(response, result)


@router.post("/runs/{run_id}/resume", status_code=202, response_model=SuccessResponse[RunResponse])
def run_resume(
    run_id: uuid.UUID,
    payload: ResumeRunRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: Principal = Depends(require_actor),
    _csrf: AuthContext = Depends(require_csrf),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    key = validate_idempotency_key(idempotency_key, required=True)
    assert key is not None
    result = control_run(
        actor,
        session_factory,
        run_id=run_id,
        operation="resume",
        payload=payload.model_dump(),
        request_id=request_id_of(request),
        idempotency_key=key,
    )
    return apply_result(response, result)


@router.post(
    "/runs/{run_id}/retry", status_code=202, response_model=SuccessResponse[RunAcceptedResponse]
)
def run_retry(
    run_id: uuid.UUID,
    payload: RetryRunRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: Principal = Depends(require_actor),
    _csrf: AuthContext = Depends(require_csrf),
    session_factory: sessionmaker[Session] = Depends(get_app_session_factory),
) -> dict[str, Any]:
    key = validate_idempotency_key(idempotency_key, required=True)
    assert key is not None
    result = control_run(
        actor,
        session_factory,
        run_id=run_id,
        operation="retry",
        payload=payload.model_dump(),
        request_id=request_id_of(request),
        idempotency_key=key,
    )
    return apply_result(response, result)


@router.get("/runs/{run_id}/events", response_class=StreamingResponse)
def run_events(
    run_id: uuid.UUID,
    request: Request,
    after_seq: int = Query(default=0, ge=0, le=9223372036854775807),
) -> StreamingResponse:
    """认证在 event_response 的短事务预检中完成。"""
    return event_response(request, run_id=run_id, after_seq=after_seq)
