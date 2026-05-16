from sqlmodel import SQLModel, Field

class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int = Field(nullable=False, primary_key=True)
    name: str
    email: str

    class Config:
        from_attributes = True