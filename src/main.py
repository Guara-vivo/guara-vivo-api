from contextlib import asynccontextmanager
import os

import bcrypt

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from routes import user, record, analysis, ibis
from database import AsyncSessionLocal
from models import User


MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", "10485760"))


def get_cors_origins() -> list[str]:
    origins = os.getenv("CORS_ORIGINS", "")
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSessionLocal() as db:
        # Admin seed only if ADMIN_EMAIL and ADMIN_PASSWORD are provided
        admin_email = os.getenv("ADMIN_EMAIL")
        admin_password = os.getenv("ADMIN_PASSWORD")
        
        if admin_email and admin_password:
            # Check if admin already exists
            result = await db.execute(select(User).where(User.email == admin_email.lower()))
            admin_user = result.scalar_one_or_none()
            
            if admin_user is None:
                # Create admin with provided credentials
                hashed_password = bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                admin_user = User(email=admin_email.lower(), password=hashed_password)
                db.add(admin_user)
                await db.commit()

    yield


app = FastAPI(lifespan=lifespan)

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
async def reject_oversized_or_invalid_json_requests(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH"}:
        content_length = request.headers.get("content-length")
        try:
            request_body_size = int(content_length) if content_length else 0
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid Content-Length header"},
            )

        if request_body_size > MAX_REQUEST_BODY_BYTES:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "Request body too large"},
            )

        content_type = request.headers.get("content-type", "")
        allowed_content_types = ("application/json", "multipart/form-data")
        if request_body_size > 0 and not content_type.startswith(allowed_content_types):
            return JSONResponse(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                content={"detail": "Content-Type must be application/json or multipart/form-data"},
            )

    return await call_next(request)


app.include_router(user.router)
app.include_router(record.router)
app.include_router(analysis.router)
app.include_router(ibis.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)
