from contextlib import asynccontextmanager
import os

import bcrypt

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from routes import user, record, analysis, ibis, map_zones
from database import AsyncSessionLocal
from models import User
from middleware import BodyLimitMiddleware


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)
