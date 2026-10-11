"""0004_project_conventions

Revision ID: bcd589dc796d
Revises: 8e6f57350f58
Create Date: 2026-10-11 19:41:36.909814

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bcd589dc796d"
down_revision: str | Sequence[str] | None = "8e6f57350f58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增 A6 的章节目录、幂等记录、索引和租户策略。"""

    op.execute("""
        CREATE INDEX ix_projects_org_created
        ON app.projects (org_id, created_at DESC, id DESC);
        CREATE INDEX ix_projects_org_status_created
        ON app.projects (org_id, status, created_at DESC, id DESC);

        CREATE TABLE app.chapters (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES auth.organizations(id),
            project_id uuid NOT NULL,
            parent_id uuid NULL,
            title varchar(200) NOT NULL,
            chapter_type varchar(20) NOT NULL,
            position integer NOT NULL,
            needs_review boolean NOT NULL DEFAULT true,
            created_by uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            lock_version integer NOT NULL DEFAULT 1,
            CONSTRAINT uq_chapters_org_id_id UNIQUE (org_id, id),
            CONSTRAINT uq_chapters_org_project_id UNIQUE (org_id, project_id, id),
            CONSTRAINT fk_chapters_project_org
                FOREIGN KEY (org_id, project_id) REFERENCES app.projects(org_id, id),
            CONSTRAINT fk_chapters_parent_project
                FOREIGN KEY (org_id, project_id, parent_id)
                REFERENCES app.chapters(org_id, project_id, id),
            CONSTRAINT fk_chapters_created_by_org
                FOREIGN KEY (org_id, created_by) REFERENCES auth.users(org_id, id),
            CONSTRAINT ck_chapters_title CHECK (char_length(title) BETWEEN 1 AND 200),
            CONSTRAINT ck_chapters_type CHECK (chapter_type IN
                ('construction','schedule','quality','safety','resources','custom')),
            CONSTRAINT ck_chapters_position CHECK (position >= 0),
            CONSTRAINT ck_chapters_lock_version CHECK (lock_version > 0),
            CONSTRAINT ck_chapters_not_self CHECK (parent_id IS NULL OR parent_id <> id)
        );
        CREATE UNIQUE INDEX uq_chapters_sibling_position ON app.chapters
            (org_id, project_id,
             COALESCE(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), position);
        CREATE INDEX ix_chapters_directory ON app.chapters
            (org_id, project_id, parent_id, position);

        CREATE TABLE ops.idempotency_keys (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES auth.organizations(id),
            user_id uuid NOT NULL,
            http_method varchar(10) NOT NULL,
            canonical_path varchar(500) NOT NULL,
            key_hash char(64) NOT NULL,
            request_hash char(64) NOT NULL,
            status varchar(20) NOT NULL,
            processing_owner uuid NULL,
            processing_expires_at timestamptz NULL,
            response_status integer NULL,
            response_json jsonb NULL,
            resource_type varchar(50) NULL,
            resource_id uuid NULL,
            expires_at timestamptz NOT NULL DEFAULT now() + interval '24 hours',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_idempotency_scope UNIQUE
                (org_id, user_id, http_method, canonical_path, key_hash),
            CONSTRAINT fk_idempotency_user_org
                FOREIGN KEY (org_id, user_id) REFERENCES auth.users(org_id, id),
            CONSTRAINT ck_idempotency_status CHECK (status IN ('processing','completed')),
            CONSTRAINT ck_idempotency_completed_response CHECK
                (status <> 'completed' OR
                 (response_status IS NOT NULL AND response_json IS NOT NULL))
        );
        CREATE INDEX ix_idempotency_expires_at ON ops.idempotency_keys(expires_at);

        REVOKE ALL ON app.chapters FROM PUBLIC, auth_service, ops_runtime;
        GRANT SELECT, INSERT, UPDATE ON app.chapters TO app_runtime;
        ALTER TABLE app.chapters ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.chapters FORCE ROW LEVEL SECURITY;
        CREATE POLICY chapters_tenant_policy ON app.chapters FOR ALL TO app_runtime
        USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);

        REVOKE ALL ON ops.idempotency_keys FROM PUBLIC, auth_service, ops_runtime;
        GRANT USAGE ON SCHEMA ops TO app_runtime;
        GRANT SELECT, INSERT, UPDATE ON ops.idempotency_keys TO app_runtime;
        ALTER TABLE ops.idempotency_keys ENABLE ROW LEVEL SECURITY;
        ALTER TABLE ops.idempotency_keys FORCE ROW LEVEL SECURITY;
        CREATE POLICY idempotency_actor_policy ON ops.idempotency_keys
        FOR ALL TO app_runtime
        USING (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
        WITH CHECK (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        );
    """)


def downgrade() -> None:
    """撤销本次新增结构，保留 A5 已建立的项目表和 schema。"""

    op.execute("DROP TABLE ops.idempotency_keys")
    op.execute("DROP TABLE app.chapters")
    op.execute("DROP INDEX app.ix_projects_org_status_created")
    op.execute("DROP INDEX app.ix_projects_org_created")
