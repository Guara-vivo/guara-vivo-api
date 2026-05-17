from datetime import datetime
from typing import List, Literal, Optional

from pydantic import EmailStr, Field, field_validator
from sqlmodel import SQLModel


BirdBehavior = Literal["ninhando", "vocalizando", "alimentando-se", "voando"]
RecordStatus = Literal["pending", "processing", "completed", "failed"]


class UserBase(SQLModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: EmailStr) -> str:
        return str(email).lower()


class UserCreate(UserBase):
    password: str = Field(min_length=6, max_length=128)


class UserUpdate(UserBase):
    password: str = Field(min_length=6, max_length=128)


class UserRead(UserBase):
    id: int


class UserLogin(SQLModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: EmailStr) -> str:
        return str(email).lower()


class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


class RecordBase(SQLModel):
    images: List[str] = Field(min_length=1, max_length=20)
    latitude_camera: float = Field(ge=-90, le=90)
    longitude_camera: float = Field(ge=-180, le=180)
    behavior: List[BirdBehavior] = Field(min_length=1, max_length=20)
    date_time: datetime
    user_id: int
    status: RecordStatus = "pending"

    @field_validator("images")
    @classmethod
    def validate_images(cls, images: List[str]) -> List[str]:
        for image in images:
            if not image or len(image) > 2048:
                raise ValueError("image references must be between 1 and 2048 characters")
        return images


class RecordCreate(RecordBase):
    pass


class RecordUpdate(RecordBase):
    pass


class RecordRead(RecordBase):
    id: int


class AnalysisBase(SQLModel):
    ibis_quantity: int = Field(ge=0, le=100000)
    datetime: datetime
    recorder_id: int


class AnalysisCreate(AnalysisBase):
    pass


class AnalysisUpdate(AnalysisBase):
    pass


class AnalysisRead(AnalysisBase):
    id: int


class IbisBase(SQLModel):
    color: str = Field(min_length=1, max_length=50)
    age_group: str = Field(min_length=1, max_length=50)
    analysis_id: int


class IbisCreate(IbisBase):
    pass


class IbisUpdate(IbisBase):
    pass


class IbisRead(IbisBase):
    id: int
