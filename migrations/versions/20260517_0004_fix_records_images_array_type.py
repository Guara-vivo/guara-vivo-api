"""fix records images array type

Revision ID: 20260517_0004
Revises: 20260517_0003
Create Date: 2026-05-17 00:04:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260517_0004"
down_revision: Union[str, None] = "20260517_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'records'
                  AND column_name = 'images'
                  AND data_type <> 'ARRAY'
            ) THEN
                ALTER TABLE records
                ALTER COLUMN images TYPE VARCHAR[]
                USING CASE
                    WHEN images IS NULL THEN ARRAY[]::VARCHAR[]
                    WHEN btrim(images) = '' THEN ARRAY[]::VARCHAR[]
                    WHEN btrim(images) LIKE '{%}' THEN btrim(images)::VARCHAR[]
                    ELSE ARRAY[images]::VARCHAR[]
                END;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'records'
                  AND column_name = 'images'
                  AND data_type = 'ARRAY'
            ) THEN
                ALTER TABLE records
                ALTER COLUMN images TYPE VARCHAR
                USING CASE
                    WHEN images IS NULL THEN NULL
                    WHEN cardinality(images) = 0 THEN ''
                    WHEN cardinality(images) = 1 THEN images[1]
                    ELSE array_to_string(images, ',')
                END;
            END IF;
        END $$;
        """
    )
