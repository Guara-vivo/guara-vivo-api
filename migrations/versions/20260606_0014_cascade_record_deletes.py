"""cascade deletes from records down to analyses, images and detections

Deleting a record that had an analysis failed with a NOT NULL violation on
analyses.recorder_id (the ORM tried to null it). The foreign keys now cascade
in the database, and the ORM relationships use passive_deletes.

Revision ID: 20260606_0014
Revises: 20260606_0013
Create Date: 2026-06-06 00:14:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260606_0014"
down_revision: Union[str, None] = "20260606_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column, referred table)
FOREIGN_KEYS = (
    ("analyses", "recorder_id", "records"),
    ("analysis_images", "analysis_id", "analyses"),
    ("analysis_images", "record_id", "records"),
    ("ibis", "analysis_id", "analyses"),
    ("ibis", "analysis_image_id", "analysis_images"),
)


def _recreate(ondelete: Union[str, None]) -> None:
    inspector = sa.inspect(op.get_bind())
    for table, column, referred in FOREIGN_KEYS:
        for fk in inspector.get_foreign_keys(table):
            if fk["constrained_columns"] == [column] and fk["referred_table"] == referred:
                op.drop_constraint(fk["name"], table, type_="foreignkey")
                op.create_foreign_key(
                    fk["name"], table, referred, [column], ["id"], ondelete=ondelete
                )
                break
        else:
            raise RuntimeError(f"foreign key {table}.{column} -> {referred} not found")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _recreate("CASCADE")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _recreate(None)
