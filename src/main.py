from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select

from routes import user, record, analysis, ibis
from database import AsyncSessionLocal
from models import User


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSessionLocal() as db:
        # simple seed: create one admin user if DB is empty
        result = await db.execute(select(User).limit(1))
        if result.scalar_one_or_none() is None:
            admin = User(name="admin", email="admin@example.com")
            db.add(admin)
            await db.commit()

    yield


app = FastAPI(lifespan=lifespan)


app.include_router(user.router)
app.include_router(record.router)
app.include_router(analysis.router)
app.include_router(ibis.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)
