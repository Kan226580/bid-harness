"""0005_runs

Revision ID: 7ae9316ba7f1
Revises: bcd589dc796d
Create Date: 2026-10-13 10:21:53.298424

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7ae9316ba7f1"
down_revision: str | Sequence[str] | None = "bcd589dc796d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建立运行、步骤、事件、待投递记录及最小权限。"""
    op.execute("""
        CREATE TABLE app.runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES auth.organizations(id),
            project_id uuid,
            kind varchar(20) NOT NULL,
            chapter_id uuid,
            document_version_id uuid,
            baseline_id uuid,
            retry_of_run_id uuid,
            status varchar(30) NOT NULL DEFAULT 'queued',
            cancel_requested boolean NOT NULL DEFAULT false,
            input_snapshot jsonb NOT NULL,
            snapshot_hash char(64) NOT NULL,
            model_mode varchar(20) NOT NULL,
            current_step varchar(100),
            max_calls integer NOT NULL DEFAULT 0,
            max_tokens bigint NOT NULL DEFAULT 0,
            max_cost_cny numeric(18,6),
            calls_started integer NOT NULL DEFAULT 0,
            tokens_observed bigint NOT NULL DEFAULT 0,
            tokens_reserved bigint NOT NULL DEFAULT 0,
            cost_observed_cny numeric(18,6) NOT NULL DEFAULT 0,
            cost_reserved_cny numeric(18,6) NOT NULL DEFAULT 0,
            has_unknown_calls boolean NOT NULL DEFAULT false,
            lease_owner uuid,
            lease_epoch bigint NOT NULL DEFAULT 0,
            lease_expires_at timestamptz,
            heartbeat_at timestamptz,
            dispatch_generation integer NOT NULL DEFAULT 1,
            recovery_count integer NOT NULL DEFAULT 0,
            next_attempt_at timestamptz,
            resume_payload jsonb,
            artifact_type varchar(50),
            artifact_id uuid,
            error_code varchar(100),
            error_message text,
            error_retryable boolean NOT NULL DEFAULT false,
            last_event_seq bigint NOT NULL DEFAULT 0,
            created_by uuid NOT NULL,
            started_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_runs_org_id UNIQUE (org_id, id),
            CONSTRAINT uq_runs_org_project_id UNIQUE (org_id, project_id, id),
            CONSTRAINT fk_runs_project FOREIGN KEY (org_id, project_id)
                REFERENCES app.projects(org_id, id),
            CONSTRAINT fk_runs_chapter FOREIGN KEY (org_id, project_id, chapter_id)
                REFERENCES app.chapters(org_id, project_id, id),
            CONSTRAINT fk_runs_retry FOREIGN KEY (org_id, retry_of_run_id)
                REFERENCES app.runs(org_id, id),
            CONSTRAINT fk_runs_creator FOREIGN KEY (org_id, created_by)
                REFERENCES auth.users(org_id, id),
            CONSTRAINT ck_runs_kind CHECK
                (kind IN ('parse','index','analyze','generate','review','export')),
            CONSTRAINT ck_runs_status CHECK
                (status IN ('queued','running','waiting_input','retry_wait',
                            'succeeded','failed','canceled')),
            CONSTRAINT ck_runs_model_mode CHECK
                (model_mode IN ('none','cloud','simulated')),
            CONSTRAINT ck_runs_targets CHECK (
                (project_id IS NOT NULL OR kind IN ('parse','index'))
                AND (kind NOT IN ('parse','index') OR document_version_id IS NOT NULL)
                AND (kind <> 'generate' OR chapter_id IS NOT NULL)
                AND (chapter_id IS NULL OR project_id IS NOT NULL)
                AND (kind NOT IN ('generate','review') OR baseline_id IS NOT NULL)
            ),
            CONSTRAINT ck_runs_budget CHECK (
                max_calls >= 0 AND max_tokens >= 0
                AND (max_cost_cny IS NULL OR max_cost_cny >= 0)
                AND calls_started >= 0 AND tokens_observed >= 0 AND tokens_reserved >= 0
                AND cost_observed_cny >= 0 AND cost_reserved_cny >= 0
            ),
            CONSTRAINT ck_runs_counters CHECK (
                lease_epoch >= 0 AND dispatch_generation >= 1
                AND recovery_count BETWEEN 0 AND 3 AND last_event_seq >= 0
            ),
            CONSTRAINT ck_runs_lease CHECK (
                (status = 'running' AND lease_owner IS NOT NULL
                 AND lease_expires_at IS NOT NULL)
                OR (status <> 'running' AND lease_owner IS NULL
                    AND lease_expires_at IS NULL)
            ),
            CONSTRAINT ck_runs_artifact CHECK
                ((artifact_type IS NULL) = (artifact_id IS NULL))
        );
        CREATE INDEX ix_runs_project_created ON app.runs
            (org_id, project_id, created_at DESC, id DESC);
        CREATE INDEX ix_runs_due ON app.runs (org_id, status, next_attempt_at)
            WHERE status IN ('queued','retry_wait');
        CREATE INDEX ix_runs_expired ON app.runs (org_id, lease_expires_at)
            WHERE status = 'running';
        CREATE INDEX ix_runs_generate_leases ON app.runs
            (org_id, project_id, lease_expires_at)
            WHERE kind = 'generate' AND status = 'running';

        CREATE TABLE app.run_steps (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES auth.organizations(id),
            run_id uuid NOT NULL,
            step_key varchar(150) NOT NULL,
            node_name varchar(100) NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'pending',
            attempt_count integer NOT NULL DEFAULT 0,
            lease_epoch bigint NOT NULL,
            input_hash char(64) NOT NULL,
            result_json jsonb,
            error_code varchar(100),
            started_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_steps_org_id UNIQUE (org_id, id),
            CONSTRAINT uq_steps_key UNIQUE (org_id, run_id, step_key),
            CONSTRAINT fk_steps_run FOREIGN KEY (org_id, run_id)
                REFERENCES app.runs(org_id, id),
            CONSTRAINT ck_steps_status CHECK
                (status IN ('pending','running','succeeded','failed','skipped')),
            CONSTRAINT ck_steps_counters CHECK (attempt_count >= 0 AND lease_epoch >= 0)
        );

        CREATE TABLE app.run_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES auth.organizations(id),
            run_id uuid NOT NULL,
            seq bigint NOT NULL,
            event_type varchar(100) NOT NULL,
            payload_json jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_events_org_id UNIQUE (org_id, id),
            CONSTRAINT uq_events_seq UNIQUE (org_id, run_id, seq),
            CONSTRAINT fk_events_run FOREIGN KEY (org_id, run_id)
                REFERENCES app.runs(org_id, id),
            CONSTRAINT ck_events_seq CHECK (seq > 0)
        );
        CREATE INDEX ix_events_created ON app.run_events (created_at);

        CREATE TABLE ops.outbox (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES auth.organizations(id),
            run_id uuid NOT NULL,
            dispatch_generation integer NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'pending',
            available_at timestamptz NOT NULL DEFAULT now(),
            claim_owner uuid,
            claim_expires_at timestamptz,
            attempt_count integer NOT NULL DEFAULT 0,
            sent_at timestamptz,
            last_error_code varchar(100),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_outbox_org_id UNIQUE (org_id, id),
            CONSTRAINT uq_outbox_generation UNIQUE (org_id, run_id, dispatch_generation),
            CONSTRAINT fk_outbox_run FOREIGN KEY (org_id, run_id)
                REFERENCES app.runs(org_id, id),
            CONSTRAINT ck_outbox_status CHECK (status IN ('pending','sending','sent','failed')),
            CONSTRAINT ck_outbox_counters CHECK
                (dispatch_generation >= 1 AND attempt_count >= 0),
            CONSTRAINT ck_outbox_claim CHECK (
                (status = 'sending' AND claim_owner IS NOT NULL AND claim_expires_at IS NOT NULL)
                OR (status <> 'sending' AND claim_owner IS NULL AND claim_expires_at IS NULL)
            )
        );
        CREATE INDEX ix_outbox_available ON ops.outbox (status, available_at)
            WHERE status IN ('pending','sending','failed');
        CREATE INDEX ix_outbox_claim_expired ON ops.outbox (claim_expires_at)
            WHERE status = 'sending';
    """)

    for table in ("runs", "run_steps", "run_events"):
        # table 来自代码中的固定白名单，不是用户输入。
        op.execute(f"""
            REVOKE ALL ON app.{table} FROM PUBLIC, auth_service, ops_runtime;
            GRANT SELECT, INSERT, UPDATE ON app.{table} TO app_runtime;
            ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY {table}_tenant_policy ON app.{table}
            FOR ALL TO app_runtime
            USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
            WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);
        """)

    op.execute("""
        REVOKE ALL ON ops.outbox FROM PUBLIC, auth_service, ops_runtime;
        GRANT USAGE ON SCHEMA ops TO app_runtime, ops_runtime;
        GRANT SELECT, INSERT, UPDATE ON ops.outbox TO app_runtime;
        GRANT SELECT, UPDATE ON ops.outbox TO ops_runtime;
        ALTER TABLE ops.outbox ENABLE ROW LEVEL SECURITY;
        ALTER TABLE ops.outbox FORCE ROW LEVEL SECURITY;
        CREATE POLICY outbox_tenant_policy ON ops.outbox FOR ALL TO app_runtime
        USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);
        CREATE POLICY outbox_dispatch_policy ON ops.outbox FOR ALL TO ops_runtime
        USING (true) WITH CHECK (true);
    """)


def downgrade() -> None:
    """有业务数据时先评估保留方案。"""
    op.execute("DROP TABLE ops.outbox")
    op.execute("DROP TABLE app.run_events")
    op.execute("DROP TABLE app.run_steps")
    op.execute("DROP TABLE app.runs")
