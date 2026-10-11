"""0003_projects_rls

Revision ID: 8e6f57350f58
Revises: 1e621774e8c4
Create Date: 2026-10-11 17:02:03.674537

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e6f57350f58"
down_revision: str | Sequence[str] | None = "1e621774e8c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("CREATE SCHEMA IF NOT EXISTS ops")

    op.execute(
        """
        CREATE TABLE app.projects (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

            org_id uuid NOT NULL,
            name varchar(200) NOT NULL,
            construction_type varchar(100) NOT NULL,
            description text NOT NULL DEFAULT '',

            data_classification varchar(20)
                NOT NULL DEFAULT 'synthetic',

            status varchar(20) NOT NULL DEFAULT 'active',

            current_baseline_id uuid NULL,
            baseline_dirty boolean NOT NULL DEFAULT true,

            created_by uuid NOT NULL,

            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),

            lock_version integer NOT NULL DEFAULT 1,

            CONSTRAINT uq_projects_org_id_id
                UNIQUE (org_id, id),

            CONSTRAINT fk_projects_org
                FOREIGN KEY (org_id)
                REFERENCES auth.organizations (id),

            CONSTRAINT fk_projects_created_by_org
                FOREIGN KEY (org_id, created_by)
                REFERENCES auth.users (org_id, id),

            CONSTRAINT ck_projects_status
                CHECK (status IN ('active', 'archived')),

            CONSTRAINT ck_projects_data_classification
                CHECK (
                    data_classification IN (
                        'synthetic',
                        'deidentified',
                        'confidential'
                    )
                ),

            CONSTRAINT ck_projects_description_length
                CHECK (char_length(description) <= 2000),

            CONSTRAINT ck_projects_lock_version
                CHECK (lock_version > 0)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_app_projects_org_id
        ON app.projects (org_id)
        """
    )

    op.execute(
        """
        REVOKE ALL PRIVILEGES
        ON SCHEMA app
        FROM PUBLIC, auth_service, ops_runtime
        """
    )

    op.execute(
        """
        REVOKE ALL PRIVILEGES
        ON TABLE app.projects
        FROM PUBLIC, auth_service, ops_runtime
        """
    )

    op.execute("GRANT USAGE ON SCHEMA app TO app_runtime")

    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE
        ON TABLE app.projects
        TO app_runtime
        """
    )

    op.execute(
        """
        REVOKE ALL PRIVILEGES
        ON SCHEMA ops
        FROM PUBLIC, auth_service
        """
    )

    op.execute(
        """
        GRANT USAGE
        ON SCHEMA ops
        TO app_runtime, ops_runtime
        """
    )

    # RLS_BLOCK_START
    op.execute(
        """
        ALTER TABLE app.projects ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.projects FORCE ROW LEVEL SECURITY;

        CREATE POLICY projects_tenant_policy
        ON app.projects
        FOR ALL
        TO app_runtime
        USING (
            org_id = NULLIF(
                current_setting('app.current_org_id', true),
                ''
            )::uuid
        )
        WITH CHECK (
            org_id = NULLIF(
                current_setting('app.current_org_id', true),
                ''
            )::uuid
        );
        """
    )
    # RLS_BLOCK_END


def downgrade() -> None:
    op.execute("DROP TABLE app.projects")
    op.execute("DROP SCHEMA ops")
    op.execute("DROP SCHEMA app")
