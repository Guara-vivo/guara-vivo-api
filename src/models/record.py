from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime
from typing import List, Literal, Optional, TYPE_CHECKING
from sqlalchemy import Column, ARRAY, String

if TYPE_CHECKING:
    from models.analysis import Analysis

birdbehavior = Literal["ninhando", "vocalizando", "alimentando-se", "voando"]


class Record(SQLModel, table=True):
    __tablename__ = "records"

    id: Optional[int] = Field(default=None, primary_key=True)
    images: str
    latitude_camera: float
    longitude_camera: float
    behavior: List[birdbehavior] = Field(sa_column=Column(ARRAY(String)))
    date_time: datetime
    user_id: int = Field(foreign_key="users.id")
    analysis: Optional["Analysis"] = Relationship(
        back_populates="record", 
        sa_relationship_kwargs={"uselist": False}
    )

    class Config:
        from_attributes = True