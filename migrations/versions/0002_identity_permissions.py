"""0002_identity_permissions

Revision ID: 1e621774e8c4
Revises: 7e4a6d55dbbc
Create Date: 2026-10-11 16:40:57.016597

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1e621774e8c4"
down_revision: str | Sequence[str] | None = "7e4a6d55dbbc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        REVOKE ALL PRIVILEGES
        ON SCHEMA auth
        FROM PUBLIC, app_runtime, auth_service, ops_runtime
        """
    )

    op.execute(
        """
        REVOKE ALL PRIVILEGES
        ON ALL TABLES IN SCHEMA auth
        FROM PUBLIC, app_runtime, auth_service, ops_runtime
        """
    )

    op.execute("GRANT USAGE ON SCHEMA auth TO auth_service")

    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE
        ON TABLE auth.organizations, auth.users, auth.sessions
        TO auth_service
        """
    )


def downgrade() -> None:
    op.execute(
        """
        REVOKE SELECT, INSERT, UPDATE
        ON TABLE auth.organizations, auth.users, auth.sessions
        FROM auth_service
        """
    )

    op.execute("REVOKE USAGE ON SCHEMA auth FROM auth_service")
