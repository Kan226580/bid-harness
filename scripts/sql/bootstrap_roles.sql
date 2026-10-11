-- 执行脚本
-- 以下命令中的管理员和数据库名称以 harness 为例，你需要换成实际值：
-- Get-Content -LiteralPath .\scripts\sql\bootstrap_roles.sql -Raw -Encoding utf8 | docker compose --env-file .env -f deploy/compose.yaml exec -T postgres psql -1 -v ON_ERROR_STOP=1 -U harness -d harness
-- -U 后填写你当前 .env 的 DB__USERNAME。
-- -d 后填写你当前 .env 的 DB__NAME。

-- \password migration_owner
-- \password app_runtime
-- \password auth_service
-- \password ops_runtime

begin ;

DO
$$
    declare
        target_role text;
    begin
        foreach target_role in array array [
            'migration_owner',
            'app_runtime',
            'auth_service',
            'ops_runtime'
            ]
            loop
                if not exists (select 1
                               from pg_roles
                               where rolname = target_role) then
                    execute format(
                            'create role %I login',
                            target_role
                            );
                end if;

                execute format(
                        'alter role %I with' ||
                        ' LOGIN' ||
                        ' NOSUPERUSER' ||
                        ' NOCREATEDB' ||
                        ' NOCREATEROLE' ||
                        ' NOREPLICATION' ||
                        ' NOBYPASSRLS' ||
                        ' NOINHERIT',
                        target_role
                        );

                execute format(
                        'alter role %I set timezone to %L',
                        target_role,
                        'utc'
                        );
            end loop;
    end
$$;

do
$$
    begin
        execute format(
                'grant connect on database %I to' ||
                ' migration_owner,' ||
                ' app_runtime,' ||
                ' auth_service,' ||
                ' ops_runtime',
                current_database()
                );

        execute format(
                'grant create on database %I to migration_owner',
                current_database()
                );
    end;
$$;

revoke create
on schema public
from public, app_runtime, auth_service, ops_runtime;

grant usage, create
on schema public
to migration_owner;

commit ;