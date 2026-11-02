"""enable row level security on all public tables

The API and worker connect directly as the table owner (`postgres`, which has
BYPASSRLS), so they are unaffected. Enabling RLS without policies denies all
access through Supabase's Data API (anon/authenticated roles).

Do NOT use FORCE ROW LEVEL SECURITY: it would make the owner obey RLS too.

Revision ID: 20260606_0013
Revises: 20260605_0012
Create Date: 2026-06-06 00:13:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260606_0013"
down_revision: Union[str, None] = "20260605_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = (
    "users",
    "records",
    "analyses",
    "analysis_images",
    "ibis",
    "refresh_tokens",
    "map_zones",
    "record_map_zones",
    "alembic_version",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    if not _is_postgres():
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
