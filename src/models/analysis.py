from sqlmodel import SQLModel, Field
from database import get_session
from datetime import datetime
from sqlmodel import Relationship

class Analysis(SQLModel, table=True, get_session=get_session):
    __tablename__ = "analyses"

    id: int = Field(default=None, primary_key=True)
    ibis_quantity: int
    flock_size: str
    latitude: float
    longitude: float
    datetime: datetime
    recorder_id: int = Field(foreign_key="recorder.id")


    class Config:
        orm_mode = True

    recorder = Relationship(back_populates="analyses")