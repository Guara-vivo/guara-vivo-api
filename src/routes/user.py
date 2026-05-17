import bcrypt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from time import monotonic
from database import get_db
from models import User
from schemas import Token, UserCreate, UserLogin, UserRead, UserUpdate
from security import create_access_token, get_current_user

router = APIRouter(prefix="/users", tags=["users"])
LOGIN_RATE_LIMIT_MAX = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 60
_login_attempts: dict[str, list[float]] = {}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
    except ValueError:
        return False


def enforce_login_rate_limit(request: Request) -> None:
    client_host = request.client.host if request.client else "unknown"
    now = monotonic()
    attempts = [
        attempt
        for attempt in _login_attempts.get(client_host, [])
        if now - attempt < LOGIN_RATE_LIMIT_WINDOW_SECONDS
    ]
    if len(attempts) >= LOGIN_RATE_LIMIT_MAX:
        _login_attempts[client_host] = attempts
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
        )
    attempts.append(now)
    _login_attempts[client_host] = attempts


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


@router.post("/login", response_model=Token)
async def login(
    user_login: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    enforce_login_rate_limit(request)
    db_user = await get_user_by_email(db, str(user_login.email))

    if db_user is None or not verify_password(user_login.password, db_user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    return {
        "access_token": create_access_token(db_user),
        "token_type": "bearer",
        "user": db_user,
    }


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/{user_id}", response_model=UserRead)
async def read_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    
    return user

@router.post("/", response_model=UserRead)
async def create_user(user: UserCreate, db: AsyncSession = Depends(get_db)):
    existing_user = await get_user_by_email(db, str(user.email))
    if existing_user is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    db_user = User(name=user.name, email=str(user.email), password=hash_password(user.password))

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)

    return db_user

@router.put("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    user: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()

    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    existing_user = await get_user_by_email(db, str(user.email))
    if existing_user is not None and existing_user.id != user_id:
        raise HTTPException(status_code=409, detail="Email already registered")

    db_user.name = user.name
    db_user.email = str(user.email)
    db_user.password = hash_password(user.password)

    await db.commit()
    await db.refresh(db_user)

    return db_user

@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()

    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(db_user)
    await db.commit()

    return {"detail": "User deleted successfully"}
