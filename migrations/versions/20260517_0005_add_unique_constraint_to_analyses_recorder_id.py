"""add unique constraint to analyses recorder id

Revision ID: 20260517_0005
Revises: 20260517_0004
Create Date: 2026-05-17 00:05:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260517_0005"
down_revision: Union[str, None] = "20260517_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM analyses
                GROUP BY recorder_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'cannot add unique constraint: duplicate analyses.recorder_id values exist';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint c
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid
                 AND a.attnum = ANY(c.conkey)
                WHERE c.conrelid = 'analyses'::regclass
                  AND c.contype = 'u'
                  AND a.attname = 'recorder_id'
            ) THEN
                ALTER TABLE analyses
                ADD CONSTRAINT uq_analyses_recorder_id UNIQUE (recorder_id);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE analyses
        DROP CONSTRAINT IF EXISTS uq_analyses_recorder_id;
        """
    )
