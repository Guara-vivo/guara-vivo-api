"""add refresh_tokens.session_kind

Which client a refresh token belongs to: mobile (1 year, token in the body), web (7 days, cookie)
or web_session (24 h, session cookie). Existing tokens are mobile, which is how they behave today.

Revision ID: 20261112_0019
Revises: 20260610_0018
Create Date: 2026-11-12 00:19:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261112_0019"
down_revision: Union[str, None] = "20260610_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.add_column(
        "refresh_tokens",
        sa.Column("session_kind", sa.String(length=16), nullable=False, server_default="mobile"),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_column("refresh_tokens", "session_kind")
