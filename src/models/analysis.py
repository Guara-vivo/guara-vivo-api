from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.ibis import Ibis
    from models.record import Record

class Analysis(SQLModel, table=True):
    __tablename__ = "analyses"

    id: Optional[int] = Field(default=None, primary_key=True)
    ibis_quantity: int
    flock_size: str
    latitude: float
    longitude: float
    datetime: datetime
    recorder_id: int = Field(foreign_key="records.id", unique=True)
    record: "Record" = Relationship(back_populates="analysis")
    birds: List["Ibis"] = Relationship(back_populates="analysis")

    class Config:
        from_attributes = True