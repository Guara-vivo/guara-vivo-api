from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, TYPE_CHECKING
from sqlalchemy import Column, Text

if TYPE_CHECKING:
    from models.analysis import Analysis
    from models.analysis_image import AnalysisImage

class Ibis(SQLModel, table=True):
    __tablename__ = "ibis"

    id: Optional[int] = Field(default=None, primary_key=True)
    color: str
    age_group: str
    analysis_id: int = Field(foreign_key="analyses.id", index=True)
    analysis_image_id: Optional[int] = Field(default=None, foreign_key="analysis_images.id", index=True)
    raw_detection: Optional[str] = Field(default=None, sa_column=Column(Text))

    analysis: "Analysis" = Relationship(back_populates="birds")
    analysis_image: Optional["AnalysisImage"] = Relationship(back_populates="detections")

    class Config:
        from_attributes = True
