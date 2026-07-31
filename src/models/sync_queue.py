from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel


class SyncQueueItem(SQLModel, table=True):
    __tablename__ = "sync_queue"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    operation: str
    local_record_id: int
    payload: str = Field(sa_column=Column(Text, nullable=False))
    image_local_paths: Optional[str] = Field(default=None, sa_column=Column(Text))
    status: str = Field(default="pending")
    error: Optional[str] = Field(default=None, sa_column=Column(Text))
    retry_count: int = Field(default=0)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    synced_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    remote_record_id: Optional[int] = Field(default=None)

    class Config:
        from_attributes = True