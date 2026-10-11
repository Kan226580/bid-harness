from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session as DbSession

from backend.app.api.errors import ok
from backend.app.core.cookies import clear_session_cookie, set_session_cookie
from backend.app.core.security import derive_csrf_token
from backend.app.db.session import get_auth_db
from backend.app.models.identity.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    organization_summary,
    user_summary,
)
from backend.app.modules.identity.dependencies import AuthContext, require_csrf, require_session
from backend.app.modules.identity.service import (
    authenticate,
    change_password,
    create_session,
    revoke_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession = Depends(get_auth_db),
) -> dict[str, Any]:
    """
    校验 → 建会话 → 存哈希 → 发 Cookie + csrf_token
    不需要 CSRF 头，但中间件会校验 Origin
    """
    user, organization = authenticate(
        db,
        organization_slug=payload.organization_slug,
        email=payload.email,
        password=payload.password,
    )
    issued = create_session(db, user)
    db.commit()
    set_session_cookie(response, issued.raw_token)
    return ok(
        request,
        {
            "user": user_summary(user),
            "organization": organization_summary(organization),
            "csrf_token": issued.csrf_token,
        },
    )


@router.get("/me")
def me(
    request: Request,
    ctx: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """返回当前用户、组织摘要、重算出来的 csrf_token"""
    return ok(
        request,
        {
            "user": user_summary(ctx.user),
            "organization": organization_summary(ctx.organization),
            # 重新派生，保证前端刷新后还能拿到和登陆时一致的 Token
            "csrf_token": derive_csrf_token(ctx.raw_token),
        },
    )


@router.post("/logout", status_code=204)
def logout(
    ctx: AuthContext = Depends(require_csrf), db: DbSession = Depends(get_auth_db)
) -> Response:
    """撤销当前会话 + 删 Cookie，返回 204，要带 CSRF 头"""
    revoke_session(db, ctx.session)
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.post("/password")
def change_password_endpoint(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(require_csrf),
    db: DbSession = Depends(get_auth_db),
) -> dict[str, Any]:
    """
    验旧密码 → 换新哈希 → 撤销所有旧会话 → 发新 Cookie 和新 csrf_token
    要带 CSRF 头
    """
    issued = change_password(
        db,
        ctx.user,
        ctx.session,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    # 轮换：旧 cookie 立即作废
    set_session_cookie(response, issued.raw_token)
    return ok(
        request,
        {
            "user": user_summary(ctx.user),
            "csrf_token": issued.csrf_token,
        },
    )
