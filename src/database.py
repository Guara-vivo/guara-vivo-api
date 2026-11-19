import os
import logging
from dotenv import load_dotenv

load_dotenv()
from sqlalchemy import event
from sqlalchemy.engine import make_url
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
#
# Applied with `SET` after each connection opens, not as a startup parameter (`server_settings`):
# Supabase's pooler accepts a startup parameter and silently ignores it (checked by hand: SHOW still
# returned the role default). The SET is session-wide, so it needs the session-mode connection the
# API already requires (direct, or the pooler on port 5432).
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 30


def read_statement_timeout_seconds() -> int:
    return get_int_env("DATABASE_STATEMENT_TIMEOUT_SECONDS", DEFAULT_STATEMENT_TIMEOUT_SECONDS)


def statement_timeout_sql(statement_timeout_seconds: int) -> str | None:
    if statement_timeout_seconds <= 0:
        return None
    return f"SET statement_timeout = {int(statement_timeout_seconds) * 1000}"


def apply_statement_timeout(sync_engine, statement_timeout_seconds: int):
    """Run the SET on every new connection of the pool, and commit it.

    The commit matters: SQLAlchemy rolls back each connection it takes back into the pool, and a
    SET that is still inside the open transaction would be undone by that rollback.

    Returns the registered listener (None when disabled).
    """
    statement = statement_timeout_sql(statement_timeout_seconds)
    if statement is None:
        return None

    @event.listens_for(sync_engine, "connect")
    def set_statement_timeout(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(statement)
        finally:
            cursor.close()
        dbapi_connection.commit()

    return set_statement_timeout


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


def asyncpg_engine_url(url: str) -> str:
    """Rewrite libpq's `sslmode` as asyncpg's `ssl` for the SQLAlchemy async engine.

    `DATABASE_URL` is written for libpq (`?sslmode=require`), which is what psycopg2 (Alembic) and
    `asyncpg.connect(dsn)` (progress LISTEN) understand. SQLAlchemy's asyncpg dialect instead passes
    every query parameter as a keyword to `asyncpg.connect`, which has no `sslmode` keyword and fails
    with `TypeError`; it takes the same values as `ssl`. A URL without `sslmode` is left untouched.
    """
    parsed = make_url(url)
    if "sslmode" not in parsed.query:
        return url
    query = dict(parsed.query)
    sslmode = query.pop("sslmode")
    query.setdefault("ssl", sslmode)
    return parsed.set(query=query).render_as_string(hide_password=False)


ASYNC_DATABASE_URL = asyncpg_engine_url(
    DATABASE_URL.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
)

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
    pool_pre_ping=True,
    pool_size=get_int_env("DATABASE_POOL_SIZE", 5),
    max_overflow=get_int_env("DATABASE_MAX_OVERFLOW", 10),
    pool_timeout=get_int_env("DATABASE_POOL_TIMEOUT", 30),
    pool_recycle=get_int_env("DATABASE_POOL_RECYCLE", 1800),
)
statement_timeout_listener = apply_statement_timeout(engine.sync_engine, STATEMENT_TIMEOUT_SECONDS)
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
