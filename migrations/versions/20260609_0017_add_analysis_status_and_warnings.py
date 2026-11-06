"""add analyses.status_analise and analyses.avisos

Root-level status and warning codes returned by the identifier
(status_analise: ok | atencao | sem_ave | sem_guara; avisos: list of codes).
Both nullable: analyses written before this change have no value.

Revision ID: 20260609_0017
Revises: 20260608_0016
Create Date: 2026-06-09 00:17:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260609_0017"
down_revision: Union[str, None] = "20260608_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.add_column("analyses", sa.Column("status_analise", sa.String(length=30), nullable=True))
    op.add_column("analyses", sa.Column("avisos", postgresql.ARRAY(sa.String()), nullable=True))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_column("analyses", "avisos")
    op.drop_column("analyses", "status_analise")
