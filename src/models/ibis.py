from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.analysis import Analysis

class Ibis(SQLModel, table=True):
    __tablename__ = "ibis"

    id: Optional[int] = Field(default=None, primary_key=True)
    color: str
    age_group: str
    analysis_id: int = Field(foreign_key="analyses.id", index=True)

    analysis: "Analysis" = Relationship(back_populates="birds")

    class Config:
        from_attributes = True
