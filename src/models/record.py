from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime
from typing import List, Literal, Optional, TYPE_CHECKING
from sqlalchemy import CheckConstraint, Column, ARRAY, String

if TYPE_CHECKING:
    from models.analysis import Analysis

birdbehavior = Literal["ninhando", "vocalizando", "alimentando-se", "voando"]


class Record(SQLModel, table=True):
    __tablename__ = "records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_records_status",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    images: List[str] = Field(sa_column=Column(ARRAY(String)))
    latitude_camera: float
    longitude_camera: float
    behavior: List[birdbehavior] = Field(sa_column=Column(ARRAY(String)))
    date_time: datetime
    user_id: int = Field(foreign_key="users.id")
    status: str = Field(default="pending", sa_column=Column(String, nullable=False))
    analysis: Optional["Analysis"] = Relationship(
        back_populates="record", 
        sa_relationship_kwargs={"uselist": False}
    )

    class Config:
        from_attributes = True
