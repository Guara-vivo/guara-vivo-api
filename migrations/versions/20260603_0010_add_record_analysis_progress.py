"""add record analysis progress

Revision ID: 20260603_0010
Revises: 20260526_0009
Create Date: 2026-06-03 00:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260603_0010"
down_revision: Union[str, None] = "20260526_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "records",
        sa.Column("analysis_progress", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        UPDATE records
        SET analysis_progress = CASE
            WHEN status IN ('completed', 'failed') THEN 100
            WHEN status = 'processing' THEN 25
            ELSE 0
        END
        """
    )
    op.create_check_constraint(
        "ck_records_analysis_progress",
        "records",
        "analysis_progress >= 0 AND analysis_progress <= 100",
    )


def downgrade() -> None:
    op.drop_constraint("ck_records_analysis_progress", "records", type_="check")
    op.drop_column("records", "analysis_progress")
