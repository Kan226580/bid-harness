from dataclasses import dataclass

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session as DbSession

from backend.app.core.cookies import SESSION_COOKIE_NAME
from backend.app.core.errors import ApiError
from backend.app.core.security import verify_csrf_token
from backend.app.db.session import get_auth_db
from backend.app.models import (
    Organization,
    User,
)
from backend.app.models import (
    Session as AuthSession,
)
from backend.app.modules.identity.service import load_session

csrf_header_schema = APIKeyHeader(
    name="X-CSRF-Token",
    scheme_name="CsrfToken",
    description="/login 或 /me 响应体里的 csrf_token，原样粘贴",
    auto_error=False,
)


@dataclass(frozen=True)
class AuthContext:
    session: AuthSession
    user: User
    organization: Organization
    raw_token: str


def session_expired() -> ApiError:
    return ApiError(401, "SESSION_EXPIRED", "会话已过期，请重新登录")


def require_session(
    request: Request,
    db: DbSession = Depends(get_auth_db),
) -> AuthContext:
    """受保护接口的门卫：没登陆、过期、被撤销、账号停用，统统 [会话已过期]"""

    # 未登录
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        raise session_expired()

    # 过期、已撤销、查不到
    record = load_session(db, raw_token)
    if record is None:
        raise session_expired()

    # 用户停用
    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise session_expired()

    if user.org_id != record.org_id:
        raise session_expired()

    # 组织停用
    organization = db.get(Organization, user.org_id)

    if organization is None or not organization.is_active:
        raise session_expired()

    return AuthContext(session=record, user=user, organization=organization, raw_token=raw_token)


def require_csrf(
    request: Request,
    ctx: AuthContext = Depends(require_session),
    _csrf_header: str | None = Security(csrf_header_schema),
) -> AuthContext:
    """写操作的门卫：先要有会话，再核对 X-CSRF-Token"""
    provided = request.headers.get("X-CSRF-Token")
    if not verify_csrf_token(ctx.raw_token, provided, ctx.session.csrf_token_hash):
        raise ApiError(403, "CSRF_INVALID", "请求校验失败，请刷新页面后重试")
    return ctx


def require_password_changed(
    ctx: AuthContext = Depends(require_session),
) -> AuthContext:
    """
    临时密码没改的用户不能做业务操作

    用法：业务接口声明 `ctx: AuthContext = Depends(require_password_changed)`
    写接口再额外叠加 CSRF 校验
    """
    if ctx.user.must_change_password:
        raise ApiError(403, "PASSWORD_CHANGE_REQUIRED", "需要先修改密码")
    return ctx
