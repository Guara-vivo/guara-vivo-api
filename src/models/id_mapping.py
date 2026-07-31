from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class IdMapping(SQLModel, table=True):
    __tablename__ = "id_mappings"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    local_id: int
    remote_id: int
    entity_type: str
    synced_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))

    class Config:
        from_attributes = True