from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime
from typing import List, Literal, Optional, TYPE_CHECKING
from sqlalchemy import CheckConstraint, Column, ARRAY, DateTime, Integer, String, UniqueConstraint, func

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
        CheckConstraint(
            "analysis_progress >= 0 AND analysis_progress <= 100",
            name="ck_records_analysis_progress",
        ),
        # Lets a client retry a create without duplicating it. NULLs never collide.
        UniqueConstraint("user_id", "client_uuid", name="uq_records_user_client_uuid"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    images: List[str] = Field(sa_column=Column(ARRAY(String)))
    latitude_camera: float
    longitude_camera: float
    behavior: List[birdbehavior] = Field(sa_column=Column(ARRAY(String)))
    date_time: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    status: str = Field(default="pending", sa_column=Column(String, nullable=False))
    analysis_progress: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    client_uuid: Optional[str] = Field(default=None, sa_column=Column(String(36), nullable=True))
    # When the record was last handed to the queue; lets a sweeper re-queue lost messages.
    queued_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    analysis: Optional["Analysis"] = Relationship(
        back_populates="record", 
        sa_relationship_kwargs={"uselist": False, "cascade": "all", "passive_deletes": True}
    )

    class Config:
        from_attributes = True
