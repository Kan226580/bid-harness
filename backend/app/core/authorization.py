from enum import StrEnum

from backend.app.core.errors import ApiError
from backend.app.core.principal import Principal


class Permission(StrEnum):
    READ_CONTENT = "read_content"
    EDIT_CONTENT = "edit_content"

    GENERATE = "generate"

    REQUEST_REVIEW = "request_review"
    REVIEW_DECISION = "review_decision"

    EXPORT_REVIEW = "export_review"
    EXPORT_SUBMISSION = "export_submission"

    MANAGE_MEMBERS = "manage_members"
    ARCHIVE_PROJECT = "archive_project"
    MANAGE_ORGANIZATION = "manage_organization"

    VIEW_AUDIT = "view_audit"


_ALL_ROLES = frozenset({"admin", "writer", "reviewer"})
_WRITING_ROLES = frozenset({"admin", "writer"})
_REVIEWING_ROLES = frozenset({"admin", "reviewer"})
_ADMIN_ONLY = frozenset({"admin"})


_ALLOWED_ROLES: dict[Permission, frozenset[str]] = {
    Permission.READ_CONTENT: _ALL_ROLES,
    Permission.EDIT_CONTENT: _WRITING_ROLES,
    Permission.GENERATE: _WRITING_ROLES,
    Permission.REQUEST_REVIEW: _ALL_ROLES,
    Permission.REVIEW_DECISION: _REVIEWING_ROLES,
    Permission.EXPORT_REVIEW: _ALL_ROLES,
    Permission.EXPORT_SUBMISSION: _REVIEWING_ROLES,
    Permission.MANAGE_MEMBERS: _ADMIN_ONLY,
    Permission.ARCHIVE_PROJECT: _ADMIN_ONLY,
    Permission.MANAGE_ORGANIZATION: _ADMIN_ONLY,
    Permission.VIEW_AUDIT: _REVIEWING_ROLES,
}


def authorize(actor: Principal, permission: Permission) -> None:
    allowed_roles = _ALLOWED_ROLES.get(permission, frozenset())

    if actor.role not in allowed_roles:
        raise ApiError(
            403,
            "FORBIDDEN",
            "没有执行此操作的权限",
        )
