"""add per-image analysis

Revision ID: 20260520_0008
Revises: 20260518_0007
Create Date: 2026-05-20 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260520_0008"
down_revision: Union[str, None] = "20260518_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create analysis_images table
    op.create_table(
        "analysis_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("image_index", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.String(), nullable=False),
        sa.Column("ibis_quantity", sa.Integer(), nullable=False),
        sa.Column("raw_result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"]),
        sa.ForeignKeyConstraint(["record_id"], ["records.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_images_analysis_id", "analysis_images", ["analysis_id"], unique=False)
    op.create_index("ix_analysis_images_record_id", "analysis_images", ["record_id"], unique=False)

    # Add columns to ibis table
    op.add_column("ibis", sa.Column("analysis_image_id", sa.Integer(), nullable=True))
    op.add_column("ibis", sa.Column("raw_detection", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_ibis_analysis_image_id",
        "ibis",
        "analysis_images",
        ["analysis_image_id"],
        ["id"],
    )
    op.create_index("ix_ibis_analysis_image_id", "ibis", ["analysis_image_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ibis_analysis_image_id", table_name="ibis")
    op.drop_constraint("fk_ibis_analysis_image_id", "ibis", type_="foreignkey")
    op.drop_column("ibis", "raw_detection")
    op.drop_column("ibis", "analysis_image_id")

    op.drop_index("ix_analysis_images_record_id", table_name="analysis_images")
    op.drop_index("ix_analysis_images_analysis_id", table_name="analysis_images")
    op.drop_table("analysis_images")
