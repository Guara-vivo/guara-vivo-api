"""add users.is_active

Lets an account be switched off (login, API calls and refresh all fail) without deleting it,
for example a test account handed to someone. It is flipped directly in the database. Existing
users stay active.

Revision ID: 20261112_0020
Revises: 20261112_0019
Create Date: 2026-11-12 00:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261112_0020"
down_revision: Union[str, None] = "20261112_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_column("users", "is_active")
