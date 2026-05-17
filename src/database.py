import os
import logging
from dotenv import load_dotenv

load_dotenv()
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

DATABASE_URL = os.getenv("DATABASE_URL")
logger = logging.getLogger(__name__)


def get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer")

if DATABASE_URL is None:
    raise RuntimeError(
        "DATABASE_URL environment variable is required. Please set it in your .env file with a PostgreSQL connection string (e.g., postgres://user:password@host:port/database)"
    )

if not DATABASE_URL.startswith(("postgres://", "postgresql://", "postgresql+psycopg2://", "postgresql+asyncpg://")):
    raise RuntimeError(
        "DATABASE_URL must use PostgreSQL. Invalid or unsupported URL prefix.\n"
        "Please set DATABASE_URL to a PostgreSQL connection string (e.g., postgres://user:password@host:port/database)"
    )

DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)

try:
    import asyncpg
except Exception:
    raise RuntimeError(
        "asyncpg is required for async PostgreSQL connections. Install it in your venv: pip install asyncpg"
    )

logger.info("Using external PostgreSQL database")
engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=get_int_env("DATABASE_POOL_SIZE", 5),
    max_overflow=get_int_env("DATABASE_MAX_OVERFLOW", 10),
    pool_timeout=get_int_env("DATABASE_POOL_TIMEOUT", 30),
    pool_recycle=get_int_env("DATABASE_POOL_RECYCLE", 1800),
)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False,
)

Base = SQLModel

async def get_db():
    async with AsyncSessionLocal() as db:
        yield db
