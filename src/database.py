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

# A statement that runs longer than this is cancelled by Postgres (error 57014), so one slow query
# cannot hold a pooled connection indefinitely. 30 s is far above any query the API runs today.
# <= 0 turns it off. Only the API's engine uses it: Alembic and the progress LISTEN connection
# open their own connections and are not limited.
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 30


def read_statement_timeout_seconds() -> int:
    return get_int_env("DATABASE_STATEMENT_TIMEOUT_SECONDS", DEFAULT_STATEMENT_TIMEOUT_SECONDS)


def build_connect_args(statement_timeout_seconds: int) -> dict:
    """asyncpg connect arguments: the setting travels in the startup packet of every new connection."""
    if statement_timeout_seconds <= 0:
        return {}
    return {"server_settings": {"statement_timeout": str(statement_timeout_seconds * 1000)}}


if DATABASE_URL is None:
    raise RuntimeError(
        "DATABASE_URL environment variable is required. Please set it in your .env file with a PostgreSQL connection string (e.g., postgres://user:password@host:port/database)"
    )

if not DATABASE_URL.startswith(("postgres://", "postgresql://", "postgresql+psycopg2://", "postgresql+asyncpg://")):
    raise RuntimeError(
        "DATABASE_URL must use PostgreSQL. Invalid or unsupported URL prefix.\n"
        "Please set DATABASE_URL to a PostgreSQL connection string (e.g., postgres://user:password@host:port/database)"
    )

DEFAULT_DATABASE_URL = DATABASE_URL
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
STATEMENT_TIMEOUT_SECONDS = read_statement_timeout_seconds()
engine = create_async_engine(
    ASYNC_DATABASE_URL,
    connect_args=build_connect_args(STATEMENT_TIMEOUT_SECONDS),
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
