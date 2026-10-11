from fastapi import Depends

from backend.app.core.principal import Principal
from backend.app.modules.identity.dependencies import (
    AuthContext,
    require_password_changed,
    session_expired,
)


def require_actor(
    ctx: AuthContext = Depends(require_password_changed),
) -> Principal:
    """先完成认证和首次改密检查，再生成业务身份。"""
    if ctx.session.org_id != ctx.user.org_id or ctx.session.org_id != ctx.organization.id:
        raise session_expired()

    return Principal(
        org_id=ctx.session.org_id,
        user_id=ctx.user.id,
        role=ctx.user.role,
    )
