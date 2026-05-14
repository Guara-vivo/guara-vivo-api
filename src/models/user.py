from sqlmodel import SQLModel, Field
from database import get_session

class User(SQLModel, table=True, get_session=get_session):
    __tablename__ = "users"

    id: int = Field(nullable=False, primary_key=True)
    name: str
    email: str

    class Config:
        orm_mode = True