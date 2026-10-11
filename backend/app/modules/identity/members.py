from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.authorization import Permission, authorize
from backend.app.core.errors import ApiError
from backend.app.core.principal import Principal
from backend.app.core.security import hash_password
from backend.app.models import User
from backend.app.modules.identity.service import normalize_email


def create_member(
    db: Session,
    actor: Principal,
    *,
    email: str,
    display_name: str,
    role: str,
    temporary_password: str,
) -> User:
    authorize(actor, Permission.MANAGE_MEMBERS)

    user = User(
        org_id=actor.org_id,
        email=normalize_email(email),
        display_name=display_name,
        role=role,
        password_hash=hash_password(temporary_password),
        must_change_password=True,
    )

    db.add(user)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()

        diagnostic = getattr(exc.orig, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)

        if constraint_name == "uq_users_org_email":
            raise ApiError(
                409,
                "MEMBER_EMAIL_CONFLICT",
                "本组织已存在该邮箱的成员",
            ) from exc

        raise

    return user
