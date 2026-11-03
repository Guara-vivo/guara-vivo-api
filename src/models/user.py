from sqlalchemy import Boolean, Column, String, false
from sqlmodel import SQLModel, Field
from typing import Optional

class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    password: str
    is_admin: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=false()))

    class Config:
        from_attributes = True
