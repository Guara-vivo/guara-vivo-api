"""Tables that exist only in the offline SQLite database.

The domain tables (users, records, analyses, ...) come from ``models`` and are
created in SQLite from the same definitions used for PostgreSQL. Only the sync
bookkeeping lives here, so these classes must never be imported in online mode
(Alembic must not see them).
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class SyncQueueItem(SQLModel, table=True):
    __tablename__ = "sync_queue"

    id: Optional[int] = Field(default=None, primary_key=True)
    operation: str
    local_record_id: int = Field(index=True)
    payload: str = Field(sa_column=Column(Text, nullable=False))
    image_local_paths: Optional[str] = Field(default=None, sa_column=Column(Text))
    # pending | completed | failed | cancelled
    status: str = Field(default="pending", index=True)
    error: Optional[str] = Field(default=None, sa_column=Column(Text))
    retry_count: int = Field(default=0)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    synced_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    remote_record_id: Optional[int] = Field(default=None)


class IdMapping(SQLModel, table=True):
    __tablename__ = "id_mappings"
    __table_args__ = (UniqueConstraint("entity_type", "local_id", name="uq_id_mappings_entity_local"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    local_id: int = Field(index=True)
    remote_id: int
    entity_type: str
    synced_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
