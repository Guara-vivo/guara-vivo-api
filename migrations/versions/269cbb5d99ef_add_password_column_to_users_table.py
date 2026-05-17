"""add password column to users table

Revision ID: 269cbb5d99ef
Revises: d3a87201af95
Create Date: 2026-05-17 01:19:10.198705
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa



revision: str = '269cbb5d99ef'
down_revision: Union[str, None] = 'd3a87201af95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('password', sa.String(), nullable=True))
    op.execute(
        "UPDATE users SET password = '$2b$12$BMesHADfjjho0bbEK6lhqOX9DMPm3GI84sRwsAL.3oXC0u96piUHW' "
        "WHERE password IS NULL"
    )
    op.alter_column('users', 'password', existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    op.drop_column('users', 'password')
