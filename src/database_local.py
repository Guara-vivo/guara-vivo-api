import os
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

logger = logging.getLogger(__name__)

LOCAL_DB_PATH = os.getenv("LOCAL_DB_PATH", "./data/guaravivo.db")


def get_local_db_path() -> str:
    db_path = Path(LOCAL_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path.resolve())


engine = create_async_engine(
    f"sqlite+aiosqlite:///{get_local_db_path()}",
    connect_args={"check_same_thread": False},
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False,
)


async def init_local_db() -> None:
    from models_local import register_local_models
    register_local_models()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("Local SQLite database initialized at %s", get_local_db_path())