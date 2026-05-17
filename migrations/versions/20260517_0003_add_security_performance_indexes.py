"""add security and performance indexes

Revision ID: 20260517_0003
Revises: 269cbb5d99ef
Create Date: 2026-05-17 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260517_0003"
down_revision: Union[str, None] = "269cbb5d99ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_records_user_id", "records", ["user_id"], unique=False)
    op.create_index("ix_ibis_analysis_id", "ibis", ["analysis_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ibis_analysis_id", table_name="ibis")
    op.drop_index("ix_records_user_id", table_name="records")
    op.drop_index("ix_users_email", table_name="users")
