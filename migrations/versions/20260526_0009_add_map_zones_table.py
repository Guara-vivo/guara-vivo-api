"""add map_zones table

Revision ID: 20260526_0009
Revises: 20260520_0008
Create Date: 2026-05-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260526_0009"
down_revision: Union[str, None] = "20260520_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "map_zones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("radius_meters", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_map_zones_type", "map_zones", ["type"], unique=False)
    op.create_index("ix_map_zones_user_id", "map_zones", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_map_zones_user_id", table_name="map_zones")
    op.drop_index("ix_map_zones_type", table_name="map_zones")
    op.drop_table("map_zones")
