from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String
from sqlmodel import Field, SQLModel


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    jti: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    revoked_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    replaced_by_token_id: Optional[int] = Field(default=None, foreign_key="refresh_tokens.id")
    user_agent: Optional[str] = Field(default=None, sa_column=Column(String(length=512)))
    ip_address: Optional[str] = Field(default=None, sa_column=Column(String(length=128)))

    class Config:
        from_attributes = True
