from datetime import datetime
from typing import List, Literal, Optional

from sqlmodel import SQLModel


BirdBehavior = Literal["ninhando", "vocalizando", "alimentando-se", "voando"]
RecordStatus = Literal["pending", "processing", "completed", "failed"]


class UserBase(SQLModel):
    name: str
    email: str


class UserCreate(UserBase):
    pass


class UserUpdate(UserBase):
    pass


class UserRead(UserBase):
    id: int


class RecordBase(SQLModel):
    images: List[str]
    latitude_camera: float
    longitude_camera: float
    behavior: List[BirdBehavior]
    date_time: datetime
    user_id: int
    status: RecordStatus = "pending"


class RecordCreate(RecordBase):
    pass


class RecordUpdate(RecordBase):
    pass


class RecordRead(RecordBase):
    id: int


class AnalysisBase(SQLModel):
    ibis_quantity: int
    datetime: datetime
    recorder_id: int


class AnalysisCreate(AnalysisBase):
    pass


class AnalysisUpdate(AnalysisBase):
    pass


class AnalysisRead(AnalysisBase):
    id: int


class IbisBase(SQLModel):
    color: str
    age_group: str
    analysis_id: int


class IbisCreate(IbisBase):
    pass


class IbisUpdate(IbisBase):
    pass


class IbisRead(IbisBase):
    id: int
