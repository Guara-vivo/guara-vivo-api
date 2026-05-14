from sqlmodel import SQLModel, Field
from database import get_session
from datetime import datetime
from sqlmodel import Relationship

class Record(SQLModel, table=True, get_session=get_session):
    __tablename__ = "records"

    id: int = Field(default=None, primary_key=True)
    images: str
    latitude_camera: float
    longitude_camera: float
    behavior: str
    date_time: datetime
    user_id: int = Field(foreign_key="users.id")

    class Config:
        orm_mode = True

    users = Relationship("User", back_populates="records")