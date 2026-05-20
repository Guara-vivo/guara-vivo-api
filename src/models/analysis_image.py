from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime as DateTimeType
from typing import List, Optional, TYPE_CHECKING
from sqlalchemy import Column, DateTime, Text
import json

if TYPE_CHECKING:
    from models.analysis import Analysis
    from models.record import Record
    from models.ibis import Ibis

class AnalysisImage(SQLModel, table=True):
    __tablename__ = "analysis_images"

    id: Optional[int] = Field(default=None, primary_key=True)
    analysis_id: int = Field(foreign_key="analyses.id", index=True)
    record_id: int = Field(foreign_key="records.id", index=True)
    image_index: int
    image_url: str
    ibis_quantity: int
    raw_result: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: DateTimeType = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    
    analysis: "Analysis" = Relationship(back_populates="image_analyses")
    detections: List["Ibis"] = Relationship(back_populates="analysis_image")

    class Config:
        from_attributes = True
