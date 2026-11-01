from contextlib import asynccontextmanager
import os

import bcrypt

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from routes import user, record, analysis, ibis, map_zones, progress
from database import OFFLINE_MODE, AsyncSessionLocal
from models import User
from middleware import BodyLimitMiddleware

if OFFLINE_MODE:
    from fastapi import WebSocket, WebSocketDisconnect as _  # noqa
    from sync import sync_routes, sync_ws, sync_background


def get_cors_origins() -> list[str]:
    origins = os.getenv("CORS_ORIGINS", "")
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


def get_fastapi_docs_config() -> dict[str, str | None]:
    if os.getenv("APP_ENV", "").lower() == "production":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}

    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


async def seed_admin_user() -> None:
    """Create the admin user from ADMIN_EMAIL/ADMIN_PASSWORD when it does not exist.

    Runs in both modes. Offline it is what makes login possible on a fresh install,
    because the local database starts without any user.
    """
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if not (admin_email and admin_password):
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == admin_email.lower()))
        if result.scalar_one_or_none() is None:
            hashed_password = bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            db.add(User(name="admin", email=admin_email.lower(), password=hashed_password))
            await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if OFFLINE_MODE:
        from database_local import init_local_db

        await init_local_db()
        await seed_admin_user()
        await sync_background.start()
    else:
        await seed_admin_user()

    try:
        yield
    finally:
        if OFFLINE_MODE:
            await sync_background.stop()
        else:
            await progress.close_progress_manager()


app = FastAPI(lifespan=lifespan, **get_fastapi_docs_config())

# Add body limit middleware first (processes request stream)
app.add_middleware(BodyLimitMiddleware)

cors_origins = get_cors_origins()
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.middleware("http")
async def validate_request_content_type(request: Request, call_next):
    # Only validate content-type for methods with body
    if request.method in {"POST", "PUT", "PATCH"}:
        # Check if there's a body by looking at content-length or try to get content-type
        content_type = request.headers.get("content-type", "")
        content_length = request.headers.get("content-length", "0")
        
        # If content-length indicates a body, validate content-type
        try:
            if int(content_length) > 0:
                allowed_content_types = ("application/json", "multipart/form-data")
                if not content_type.startswith(allowed_content_types):
                    return JSONResponse(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        content={"detail": "Content-Type must be application/json or multipart/form-data"},
                    )
        except ValueError:
            pass
    
    return await call_next(request)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(user.router)
app.include_router(record.router)
app.include_router(analysis.router)
app.include_router(ibis.router)
app.include_router(map_zones.router)

if OFFLINE_MODE:
    # Create the directory before mounting: StaticFiles needs it to exist, and the
    # lifespan runs too late for the mount decision.
    local_storage_path = os.getenv("LOCAL_STORAGE_PATH", "./data/storage")
    os.makedirs(local_storage_path, exist_ok=True)
    app.mount("/local-storage", StaticFiles(directory=local_storage_path), name="local_storage")
    app.include_router(sync_routes.router)
    app.include_router(sync_ws.router)
else:
    app.include_router(progress.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)
