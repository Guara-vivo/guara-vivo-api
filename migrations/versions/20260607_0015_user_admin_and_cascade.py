"""add users.is_admin and cascade deletes from users

Deleting a user who owned records, zones or refresh tokens failed with a foreign key
violation. Those foreign keys now cascade in the database. `users.is_admin` gates the
creation of the (global) map zones.

Revision ID: 20260607_0015
Revises: 20260606_0014
Create Date: 2026-06-07 00:15:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260607_0015"
down_revision: Union[str, None] = "20260606_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column, referred table, ondelete)
FOREIGN_KEYS = (
    ("records", "user_id", "users", "CASCADE"),
    ("refresh_tokens", "user_id", "users", "CASCADE"),
    ("map_zones", "user_id", "users", "CASCADE"),
    ("refresh_tokens", "replaced_by_token_id", "refresh_tokens", "SET NULL"),
)


def _recreate(ondelete_for) -> None:
    inspector = sa.inspect(op.get_bind())
    for table, column, referred, ondelete in FOREIGN_KEYS:
        for fk in inspector.get_foreign_keys(table):
            if fk["constrained_columns"] == [column] and fk["referred_table"] == referred:
                op.drop_constraint(fk["name"], table, type_="foreignkey")
                op.create_foreign_key(
                    fk["name"], table, referred, [column], ["id"], ondelete=ondelete_for(ondelete)
                )
                break
        else:
            raise RuntimeError(f"foreign key {table}.{column} -> {referred} not found")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _recreate(lambda ondelete: ondelete)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _recreate(lambda _ondelete: None)
    op.drop_column("users", "is_admin")
