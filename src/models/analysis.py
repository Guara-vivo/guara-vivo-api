from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime as DateTimeType
from typing import List, Optional, TYPE_CHECKING
from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import ARRAY

if TYPE_CHECKING:
    from models.ibis import Ibis
    from models.record import Record
    from models.analysis_image import AnalysisImage

class Analysis(SQLModel, table=True):
    __tablename__ = "analyses"

    id: Optional[int] = Field(default=None, primary_key=True)
    ibis_quantity: int
    datetime: DateTimeType = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    recorder_id: int = Field(foreign_key="records.id", unique=True, ondelete="CASCADE")
    status_analise: Optional[str] = Field(default=None, sa_column=Column(String(30), nullable=True))
    avisos: Optional[List[str]] = Field(default=None, sa_column=Column(ARRAY(String), nullable=True))
    record: "Record" = Relationship(back_populates="analysis")
    birds: List["Ibis"] = Relationship(
        back_populates="analysis",
        sa_relationship_kwargs={"cascade": "all", "passive_deletes": True},
    )
    image_analyses: List["AnalysisImage"] = Relationship(
        back_populates="analysis",
        sa_relationship_kwargs={"cascade": "all", "passive_deletes": True},
    )

    class Config:
        from_attributes = True
