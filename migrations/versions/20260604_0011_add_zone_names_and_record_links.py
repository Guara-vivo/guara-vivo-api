"""add zone names and record links

Revision ID: 20260604_0011
Revises: 20260603_0010
Create Date: 2026-06-04 00:11:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260604_0011"
down_revision: Union[str, None] = "20260603_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def index_to_zone_suffix(index: int) -> str:
    value = index + 1
    suffix = ""

    while value > 0:
        value -= 1
        suffix = chr(ord("A") + (value % 26)) + suffix
        value //= 26

    return suffix


def format_zone_name(zone_type: str, sequence_index: int) -> str:
    label = "Alimentação" if zone_type == "feeding" else "Ninho"
    return f"{label} {index_to_zone_suffix(sequence_index)}"


def upgrade() -> None:
    op.add_column("map_zones", sa.Column("name", sa.String(length=80), nullable=True))
    op.add_column("map_zones", sa.Column("sequence_index", sa.Integer(), nullable=True))

    connection = op.get_bind()
    zones = connection.execute(
        sa.text(
            """
            SELECT id, type
            FROM map_zones
            ORDER BY type, created_at, id
            """
        )
    ).mappings().all()

    next_index_by_type: dict[str, int] = {}
    for zone in zones:
        zone_type = zone["type"]
        sequence_index = next_index_by_type.get(zone_type, 0)
        next_index_by_type[zone_type] = sequence_index + 1
        connection.execute(
            sa.text(
                """
                UPDATE map_zones
                SET name = :name, sequence_index = :sequence_index
                WHERE id = :id
                """
            ),
            {
                "id": zone["id"],
                "name": format_zone_name(zone_type, sequence_index),
                "sequence_index": sequence_index,
            },
        )

    op.alter_column("map_zones", "name", existing_type=sa.String(length=80), nullable=False)
    op.alter_column("map_zones", "sequence_index", existing_type=sa.Integer(), nullable=False)
    op.create_index("ix_map_zones_name", "map_zones", ["name"], unique=False)
    op.create_index("ix_map_zones_sequence_index", "map_zones", ["sequence_index"], unique=False)
    op.create_unique_constraint("uq_map_zones_type_name", "map_zones", ["type", "name"])
    op.create_unique_constraint("uq_map_zones_type_sequence_index", "map_zones", ["type", "sequence_index"])

    op.create_table(
        "record_map_zones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("map_zone_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["record_id"], ["records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["map_zone_id"], ["map_zones.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", "map_zone_id", name="uq_record_map_zones_record_zone"),
    )
    op.create_index("ix_record_map_zones_record_id", "record_map_zones", ["record_id"], unique=False)
    op.create_index("ix_record_map_zones_map_zone_id", "record_map_zones", ["map_zone_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_record_map_zones_map_zone_id", table_name="record_map_zones")
    op.drop_index("ix_record_map_zones_record_id", table_name="record_map_zones")
    op.drop_table("record_map_zones")
    op.drop_constraint("uq_map_zones_type_sequence_index", "map_zones", type_="unique")
    op.drop_constraint("uq_map_zones_type_name", "map_zones", type_="unique")
    op.drop_index("ix_map_zones_sequence_index", table_name="map_zones")
    op.drop_index("ix_map_zones_name", table_name="map_zones")
    op.drop_column("map_zones", "sequence_index")
    op.drop_column("map_zones", "name")
