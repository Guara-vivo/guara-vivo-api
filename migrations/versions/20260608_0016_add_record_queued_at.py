"""add records.queued_at

Last time the record was handed to RabbitMQ. A background sweeper re-queues records
still `pending` long after that (message lost, API crashed between commit and publish).

Revision ID: 20260608_0016
Revises: 20260607_0015
Create Date: 2026-06-08 00:16:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260608_0016"
down_revision: Union[str, None] = "20260607_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.add_column(
        "records",
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_records_pending_queued_at",
        "records",
        ["queued_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_index("ix_records_pending_queued_at", table_name="records")
    op.drop_column("records", "queued_at")
