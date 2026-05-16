"""initial schema

Revision ID: 20260516_0001
Revises: 
Create Date: 2026-05-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260516_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("images", sa.ARRAY(sa.String()), nullable=True),
        sa.Column("latitude_camera", sa.Float(), nullable=False),
        sa.Column("longitude_camera", sa.Float(), nullable=False),
        sa.Column("behavior", sa.ARRAY(sa.String()), nullable=True),
        sa.Column("date_time", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_records_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ibis_quantity", sa.Integer(), nullable=False),
        sa.Column("flock_size", sa.String(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("datetime", sa.DateTime(), nullable=False),
        sa.Column("recorder_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["recorder_id"], ["records.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recorder_id"),
    )

    op.create_table(
        "ibis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("color", sa.String(), nullable=False),
        sa.Column("age_group", sa.String(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ibis")
    op.drop_table("analyses")
    op.drop_table("records")
    op.drop_table("users")
