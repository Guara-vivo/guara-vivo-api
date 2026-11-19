"""least-privilege database roles for the API and the worker

Until now the API and the worker connected as `postgres`, which owns every table, can run DDL and
has BYPASSRLS, so the RLS enabled in 20260606_0013 protected nothing against them. This creates:

- `guara_api`: SELECT/INSERT/UPDATE/DELETE on the app tables in `public`, no DDL, no
  `alembic_version`, nothing outside `public`.
- `guara_worker`: only what the worker writes: reads `records` and updates `status` and
  `analysis_progress`; upserts `analyses`; inserts and deletes `analysis_images` and `ibis`.

Both are created NOLOGIN and without a password: login and password are set outside git (see
AGENTS.md, "Database roles"). Migrations keep running as the owner (`postgres` today) through
`MIGRATION_DATABASE_URL`.

RLS stays on. These roles do not bypass it, so each one gets a permissive policy (`USING (true)`)
on the tables it may touch: RLS becomes an extra layer that lets only these roles through, while
the per-route owner checks still decide which rows a user sees. `anon` and `authenticated`
(Supabase's public Data API, which the app does not use) lose their grants on `public` too, so
they are blocked by privileges and not only by RLS without policies.

Default privileges make tables and sequences that the migration role creates later readable and
writable by `guara_api` (not by the worker, which must be granted explicitly) and no longer granted
to `anon`/`authenticated`. Supabase's own default privileges for `supabase_admin` cannot be changed
by `postgres` and are left as they are; the app never creates objects as `supabase_admin`.

Revision ID: 20261119_0021
Revises: 20261112_0020
Create Date: 2026-11-19 00:21:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20261119_0021"
down_revision: Union[str, None] = "20261112_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


API_ROLE = "guara_api"
WORKER_ROLE = "guara_worker"
PUBLIC_API_ROLES = ("anon", "authenticated")

API_TABLES = (
    "users",
    "records",
    "analyses",
    "analysis_images",
    "ibis",
    "refresh_tokens",
    "map_zones",
    "record_map_zones",
)

# Matches the SQL in guara-vivo-worker/main.py. `records` is updated only in these columns.
WORKER_TABLE_GRANTS = {
    "records": "SELECT, UPDATE (status, analysis_progress)",
    "analyses": "SELECT, INSERT, UPDATE",
    "analysis_images": "SELECT, INSERT, DELETE",
    "ibis": "SELECT, INSERT, DELETE",
}
WORKER_SEQUENCES = ("analyses_id_seq", "analysis_images_id_seq", "ibis_id_seq")


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _policy_name(role: str) -> str:
    return f"{role}_all"


def _create_role(role: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} NOLOGIN;
            END IF;
        END
        $$
        """
    )


def _create_policy(table: str, role: str) -> None:
    name = _policy_name(role)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'public' AND tablename = '{table}' AND policyname = '{name}'
            ) THEN
                CREATE POLICY {name} ON public."{table}" AS PERMISSIVE FOR ALL TO {role}
                    USING (true) WITH CHECK (true);
            END IF;
        END
        $$
        """
    )


def _for_existing_public_roles(statement: str) -> None:
    """Run `statement` (with `{role}`) for anon/authenticated, which only exist on Supabase."""
    for role in PUBLIC_API_ROLES:
        sql = statement.format(role=role).replace("'", "''")
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    EXECUTE '{sql}';
                END IF;
            END
            $$
            """
        )


def upgrade() -> None:
    if not _is_postgres():
        return

    _create_role(API_ROLE)
    _create_role(WORKER_ROLE)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {API_ROLE}, {WORKER_ROLE}")

    tables = ", ".join(f'public."{table}"' for table in API_TABLES)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO {API_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {API_ROLE}")
    for table in API_TABLES:
        _create_policy(table, API_ROLE)

    for table, privileges in WORKER_TABLE_GRANTS.items():
        op.execute(f'GRANT {privileges} ON public."{table}" TO {WORKER_ROLE}')
        _create_policy(table, WORKER_ROLE)
    sequences = ", ".join(f"public.{sequence}" for sequence in WORKER_SEQUENCES)
    op.execute(f"GRANT USAGE ON SEQUENCE {sequences} TO {WORKER_ROLE}")

    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {API_ROLE}"
    )
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {API_ROLE}")

    _for_existing_public_roles("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
    _for_existing_public_roles("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
    _for_existing_public_roles("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {role}")
    _for_existing_public_roles("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {role}")
    _for_existing_public_roles("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM {role}")


def downgrade() -> None:
    if not _is_postgres():
        return

    # Give anon/authenticated back what Supabase grants by default (RLS without policies still
    # blocks them, as before this migration).
    _for_existing_public_roles("GRANT ALL ON ALL TABLES IN SCHEMA public TO {role}")
    _for_existing_public_roles("GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {role}")
    _for_existing_public_roles("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {role}")
    _for_existing_public_roles("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {role}")
    _for_existing_public_roles("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {role}")

    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {API_ROLE}"
    )
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM {API_ROLE}")

    for role, tables in ((API_ROLE, API_TABLES), (WORKER_ROLE, tuple(WORKER_TABLE_GRANTS))):
        for table in tables:
            op.execute(f'DROP POLICY IF EXISTS {_policy_name(role)} ON public."{table}"')

    # The roles own nothing, so revoking their grants is enough for DROP ROLE. (DROP OWNED BY would
    # need membership in the role, which the migration role does not have on Supabase.) A role that
    # still has LOGIN (in use by a running API or worker) is dropped all the same: switch the
    # services back to the owner's URL before downgrading.
    for role in (WORKER_ROLE, API_ROLE):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role};
                    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role};
                    REVOKE ALL ON SCHEMA public FROM {role};
                    DROP ROLE {role};
                END IF;
            END
            $$
            """
        )
