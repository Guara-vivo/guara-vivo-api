"""remove unused analysis fields

Revision ID: 20260517_0002
Revises: 20260516_0001
Create Date: 2026-05-17 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260517_0002"
down_revision: Union[str, None] = "20260516_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("analyses", "longitude")
    op.drop_column("analyses", "latitude")
    op.drop_column("analyses", "flock_size")


def downgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("flock_size", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "analyses",
        sa.Column("latitude", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "analyses",
        sa.Column("longitude", sa.Float(), nullable=False, server_default="0"),
    )
    op.alter_column("analyses", "flock_size", server_default=None)
    op.alter_column("analyses", "latitude", server_default=None)
    op.alter_column("analyses", "longitude", server_default=None)
