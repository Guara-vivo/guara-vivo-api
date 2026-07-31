from sqlalchemy import Column, DateTime, Integer, MetaData, String, Text
from sqlmodel import Field, SQLModel
from datetime import datetime
from typing import Optional




class RecordLocal(SQLModel, table=True):
    __tablename__ = "records"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    images: str = Field(sa_column=Column(Text, nullable=False))
    latitude_camera: float
    longitude_camera: float
    behavior: str = Field(sa_column=Column(Text, nullable=False))
    date_time: datetime = Field(sa_column=Column(DateTime(), nullable=False))
    user_id: int = Field(index=True)
    status: str = Field(default="pending")
    analysis_progress: int = Field(default=0, sa_column=Column(Integer, nullable=False))

    class Config:
        from_attributes = True


class UserLocal(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    password: str

    class Config:
        from_attributes = True


class AnalysisLocal(SQLModel, table=True):
    __tablename__ = "analyses"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    ibis_quantity: int
    record_datetime: datetime = Field(sa_column=Column("datetime", DateTime(), nullable=False))
    recorder_id: int = Field(unique=True)

    class Config:
        from_attributes = True


class AnalysisImageLocal(SQLModel, table=True):
    __tablename__ = "analysis_images"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    analysis_id: int = Field(index=True)
    record_id: int = Field(index=True)
    image_index: int
    image_url: str
    ibis_quantity: int
    raw_result: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(sa_column=Column(DateTime(), nullable=False))

    class Config:
        from_attributes = True


class IbisLocal(SQLModel, table=True):
    __tablename__ = "ibis"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    color: str
    age_group: str
    analysis_id: int = Field(index=True)
    analysis_image_id: Optional[int] = Field(default=None, index=True)
    raw_detection: Optional[str] = Field(default=None, sa_column=Column(Text))

    class Config:
        from_attributes = True


class RefreshTokenLocal(SQLModel, table=True):
    __tablename__ = "refresh_tokens"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    token_hash: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    jti: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    expires_at: datetime = Field(sa_column=Column(DateTime(), nullable=False))
    revoked_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime()))
    created_at: datetime = Field(sa_column=Column(DateTime(), nullable=False))
    replaced_by_token_id: Optional[int] = Field(default=None)
    user_agent: Optional[str] = Field(default=None, sa_column=Column(String(length=512)))
    ip_address: Optional[str] = Field(default=None, sa_column=Column(String(length=128)))

    class Config:
        from_attributes = True


class MapZoneLocal(SQLModel, table=True):
    __tablename__ = "map_zones"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(sa_column=Column("type", String, index=True))
    name: str = Field(index=True)
    sequence_index: int = Field(index=True)
    latitude: float
    longitude: float
    radius_meters: int = Field(default=50)
    user_id: int = Field(index=True)
    created_at: datetime = Field(sa_column=Column(DateTime(), nullable=False))

    class Config:
        from_attributes = True


class RecordMapZoneLocal(SQLModel, table=True):
    __tablename__ = "record_map_zones"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    record_id: int = Field(index=True)
    map_zone_id: int = Field(index=True)

    class Config:
        from_attributes = True


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
    created_at: datetime = Field(sa_column=Column(DateTime(), nullable=False))
    synced_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime()))
    remote_record_id: Optional[int] = Field(default=None)

    class Config:
        from_attributes = True


class IdMapping(SQLModel, table=True):
    __tablename__ = "id_mappings"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    local_id: int
    remote_id: int
    entity_type: str
    synced_at: datetime = Field(sa_column=Column(DateTime(), nullable=False))

    class Config:
        from_attributes = True


def register_local_models():
    pass