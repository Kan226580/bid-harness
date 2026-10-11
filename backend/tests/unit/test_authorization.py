import uuid

import pytest

from backend.app.core.authorization import Permission, authorize
from backend.app.core.errors import ApiError
from backend.app.core.principal import Principal


@pytest.mark.parametrize("role", ["writer", "reviewer", "unknown"])
def test_non_admin_cannot_manage_members(role: str) -> None:
    actor = Principal(
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        role=role,
    )

    with pytest.raises(ApiError) as exc_info:
        authorize(actor, Permission.MANAGE_MEMBERS)

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "FORBIDDEN"


def test_admin_can_manage_members() -> None:
    actor = Principal(
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        role="admin",
    )

    authorize(actor, Permission.MANAGE_MEMBERS)


def test_writer_cannot_archive_project() -> None:
    actor = Principal(
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        role="writer",
    )

    with pytest.raises(ApiError):
        authorize(actor, Permission.ARCHIVE_PROJECT)
