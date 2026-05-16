import os
import logging
from dotenv import load_dotenv

load_dotenv()
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

DATABASE_URL = os.getenv("DATABASE_URL")
logger = logging.getLogger(__name__)

if DATABASE_URL is None:
    raise RuntimeError(
        "DATABASE_URL environment variable is required. Please set it in your .env file with a PostgreSQL connection string (e.g., postgres://user:password@host:port/database)"
    )

if not DATABASE_URL.startswith(("postgres://", "postgresql://", "postgresql+psycopg2://")):
    raise RuntimeError(
        "DATABASE_URL must use PostgreSQL. Invalid or unsupported URL prefix.\n"
        "Please set DATABASE_URL to a PostgreSQL connection string (e.g., postgres://user:password@host:port/database)"
    )

DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

try:
    import psycopg2
except Exception:
    raise RuntimeError(
        "psycopg2 is required for PostgreSQL connections. Install it in your venv: pip install psycopg2-binary"
    )

logger.info("Using external PostgreSQL database")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = SQLModel

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
