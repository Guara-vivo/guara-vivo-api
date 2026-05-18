"""make record and analysis datetimes timezone aware

Revision ID: 20260518_0006
Revises: 20260517_0005
Create Date: 2026-05-18 00:06:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260518_0006"
down_revision: Union[str, None] = "20260517_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE records
        ALTER COLUMN date_time TYPE TIMESTAMP WITH TIME ZONE
        USING date_time AT TIME ZONE 'UTC';
        """
    )
    op.execute(
        """
        ALTER TABLE analyses
        ALTER COLUMN datetime TYPE TIMESTAMP WITH TIME ZONE
        USING datetime AT TIME ZONE 'UTC';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE analyses
        ALTER COLUMN datetime TYPE TIMESTAMP WITHOUT TIME ZONE
        USING datetime AT TIME ZONE 'UTC';
        """
    )
    op.execute(
        """
        ALTER TABLE records
        ALTER COLUMN date_time TYPE TIMESTAMP WITHOUT TIME ZONE
        USING date_time AT TIME ZONE 'UTC';
        """
    )
