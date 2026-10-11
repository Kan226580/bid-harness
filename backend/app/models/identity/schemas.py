from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models import Organization, User


class LoginRequest(BaseModel):
    """登录请求 schema"""

    model_config = ConfigDict(extra="forbid")

    organization_slug: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    """更换密码 schema"""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class CreateMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=50)
    role: Literal["admin", "writer", "reviewer"]
    temporary_password: str = Field(min_length=12, max_length=128)


def user_summary(user: User) -> dict[str, object]:
    """用户信息摘要"""
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "lock_version": user.lock_version,
    }


def organization_summary(organization: Organization) -> dict[str, object]:
    """组织信息摘要"""
    return {
        "id": str(organization.id),
        "name": organization.name,
        "slug": organization.slug,
    }
