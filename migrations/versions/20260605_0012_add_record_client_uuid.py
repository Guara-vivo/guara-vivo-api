"""add records.client_uuid for idempotent creates

Revision ID: 20260605_0012
Revises: 20260604_0011
Create Date: 2026-06-05 00:12:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260605_0012"
down_revision: Union[str, None] = "20260604_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("records", sa.Column("client_uuid", sa.String(length=36), nullable=True))
    op.create_unique_constraint(
        "uq_records_user_client_uuid", "records", ["user_id", "client_uuid"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_records_user_client_uuid", "records", type_="unique")
    op.drop_column("records", "client_uuid")
