from sqlalchemy import Column, String
from sqlmodel import SQLModel, Field
from typing import Optional

class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    password: str

    class Config:
        from_attributes = True
