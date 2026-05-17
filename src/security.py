import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import User


JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE", "3600"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/users/login")


def create_access_token(user: User) -> str:
    if JWT_SECRET_KEY is None:
        raise RuntimeError("JWT_SECRET_KEY environment variable is required")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=JWT_ACCESS_TOKEN_EXPIRE)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": expires_at,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if JWT_SECRET_KEY is None:
        raise RuntimeError("JWT_SECRET_KEY environment variable is required")

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise credentials_exception
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    try:
        parsed_user_id = int(user_id)
    except ValueError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == parsed_user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception

    return user
