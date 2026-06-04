from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Literal, Optional
from sqlalchemy import Column, DateTime

ZoneType = Literal["feeding", "nest"]

class MapZone(SQLModel, table=True):
    __tablename__ = "map_zones"

    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(index=True)  # "feeding" or "nest"
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_meters: int = Field(default=50, ge=10, le=5000)
    user_id: int = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))

    class Config:
        from_attributes = True
