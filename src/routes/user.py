import bcrypt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from time import monotonic
from database import get_db
from models import RefreshToken, User
from schemas import LogoutRequest, RefreshTokenRequest, Token, UserCreate, UserLogin, UserRead, UserUpdate
from security import create_access_token, create_refresh_token, get_current_user, hash_refresh_token

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


def get_request_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_request_user_agent(request: Request) -> str | None:
    user_agent = request.headers.get("user-agent")
    return user_agent[:512] if user_agent else None


async def create_db_refresh_token(
    db: AsyncSession,
    user: User,
    request: Request,
) -> tuple[str, RefreshToken]:
    if user.id is None:
        raise RuntimeError("Cannot create refresh token for unsaved user")

    token, jti, expires_at = create_refresh_token()
    db_token = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(token),
        jti=jti,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
        user_agent=get_request_user_agent(request),
        ip_address=get_request_ip(request),
    )
    db.add(db_token)
    await db.flush()
    return token, db_token


async def get_valid_refresh_token(
    db: AsyncSession,
    refresh_token: str,
) -> RefreshToken:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate refresh token",
    )
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(refresh_token)
        )
    )
    db_token = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    expires_at = db_token.expires_at if db_token is not None else None

    if expires_at is not None and (expires_at.tzinfo is None or expires_at.tzinfo.utcoffset(expires_at) is None):
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if db_token is None or db_token.revoked_at is not None or expires_at is None or expires_at <= now:
        raise credentials_exception

    return db_token


async def validate_and_rotate_refresh_token_atomically(
    db: AsyncSession,
    refresh_token: str,
    request: Request,
) -> tuple[User, str, RefreshToken]:
    """
    Atomically validate, rotate, and revoke refresh token.
    Uses pessimistic locking to prevent race conditions.
    
    Returns: (user, new_access_token, new_refresh_token_string)
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate refresh token",
    )
    
    async with db.begin_nested():
        # Lock the row with FOR UPDATE to prevent concurrent rotation
        result = await db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(refresh_token)
            ).with_for_update()
        )
        db_token = result.scalar_one_or_none()
        
        # Validate the token
        now = datetime.now(timezone.utc)
        if db_token is None or db_token.revoked_at is not None:
            raise credentials_exception
        
        expires_at = db_token.expires_at
        if expires_at is not None and (expires_at.tzinfo is None or expires_at.tzinfo.utcoffset(expires_at) is None):
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        
        if expires_at is None or expires_at <= now:
            raise credentials_exception
        
        # Get the user
        user_result = await db.execute(select(User).where(User.id == db_token.user_id))
        db_user = user_result.scalar_one_or_none()
        
        if db_user is None:
            raise credentials_exception
        
        # Create new token
        new_refresh_token, new_db_token = await create_db_refresh_token(db, db_user, request)
        
        # Revoke the old token in the same transaction
        db_token.revoked_at = datetime.now(timezone.utc)
        db_token.replaced_by_token_id = new_db_token.id
        
        return db_user, create_access_token(db_user), new_refresh_token


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

    refresh_token, _ = await create_db_refresh_token(db, db_user, request)
    await db.commit()

    return {
        "access_token": create_access_token(db_user),
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": db_user,
    }


@router.post("/refresh", response_model=Token)
async def refresh_token(
    payload: RefreshTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    db_user, access_token, refresh_token_str = await validate_and_rotate_refresh_token_atomically(
        db, payload.refresh_token, request
    )
    await db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_str,
        "token_type": "bearer",
        "user": db_user,
    }


@router.post("/logout")
async def logout(
    payload: LogoutRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(payload.refresh_token)
        )
    )
    db_token = result.scalar_one_or_none()

    if db_token is not None and db_token.revoked_at is None:
        db_token.revoked_at = datetime.now(timezone.utc)
        await db.commit()

    return {"detail": "Logged out successfully"}


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
