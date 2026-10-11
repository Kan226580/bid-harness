from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.app.api.errors import ok
from backend.app.core.principal import Principal
from backend.app.db.session import get_auth_db
from backend.app.models.identity.schemas import CreateMemberRequest, user_summary
from backend.app.modules.identity.business import require_actor
from backend.app.modules.identity.dependencies import AuthContext, require_csrf
from backend.app.modules.identity.members import create_member

router = APIRouter(prefix="/members", tags=["members"])


@router.post("", status_code=201)
def create_member_endpoint(
    payload: CreateMemberRequest,
    request: Request,
    actor: Principal = Depends(require_actor),
    _csrf: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_auth_db),
) -> dict[str, Any]:
    user = create_member(
        db,
        actor,
        email=payload.email,
        display_name=payload.display_name,
        role=payload.role,
        temporary_password=payload.temporary_password,
    )

    return ok(request, user_summary(user))
