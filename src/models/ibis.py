from sqlmodel import SQLModel, Field
from database import get_session
from sqlmodel import Relationship

class Ibis(SQLModel, table=True, get_session=get_session):
    __tablename__ = "ibis"

    id: int = Field(nullable=False, primary_key=True)
    color: str
    age_group: str
    analysis_id: int = Field(foreign_key="analysis.id")

    class Config:
        orm_mode = True

    analysis = Relationship(back_populates="ibis")