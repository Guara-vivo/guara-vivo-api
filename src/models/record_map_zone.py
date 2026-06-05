from typing import Optional

from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field, SQLModel


class RecordMapZone(SQLModel, table=True):
    __tablename__ = "record_map_zones"
    __table_args__ = (
        UniqueConstraint("record_id", "map_zone_id", name="uq_record_map_zones_record_zone"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    record_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("records.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    map_zone_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("map_zones.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    class Config:
        from_attributes = True
