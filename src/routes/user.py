import bcrypt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from database import get_db
from models import RefreshToken, User
from schemas import LogoutRequest, RefreshTokenRequest, Token, UserCreate, UserLogin, UserRead, UserUpdate, WebToken
from ratelimit import RateLimiter
from refresh_cookie import REFRESH_COOKIE_NAME, clear_refresh_cookie, reject_foreign_origin, set_refresh_cookie
from security import (
    SESSION_KIND_MOBILE,
    SESSION_KIND_WEB,
    SESSION_KIND_WEB_SESSION,
    WEB_SESSION_KINDS,
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_refresh_token,
)

router = APIRouter(prefix="/users", tags=["users"])
# Per-process limits (see ratelimit.py). Login is limited per IP and, more loosely, per
# email: the per-email limit slows password guessing spread over many IPs, and is loose
# enough that spamming a victim's email only locks them out briefly.
login_ip_limiter = RateLimiter(max_attempts=5, window_seconds=60)
login_email_limiter = RateLimiter(max_attempts=10, window_seconds=600)
register_ip_limiter = RateLimiter(max_attempts=20, window_seconds=3600)
refresh_ip_limiter = RateLimiter(max_attempts=30, window_seconds=60)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
    except ValueError:
        return False


# Verified against when the email does not exist, so both paths cost one bcrypt check.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt()).decode("utf-8")


def enforce_rate_limit(limiter: RateLimiter, key: str) -> None:
    allowed, retry_after = limiter.hit(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again later.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


def get_client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


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
    session_kind: str = SESSION_KIND_MOBILE,
) -> tuple[str, RefreshToken]:
    if user.id is None:
        raise RuntimeError("Cannot create refresh token for unsaved user")

    token, jti, expires_at = create_refresh_token(session_kind)
    db_token = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(token),
        jti=jti,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
        user_agent=get_request_user_agent(request),
        ip_address=get_request_ip(request),
        session_kind=session_kind,
    )
    db.add(db_token)
    await db.flush()
    return token, db_token


def web_session_response(db_user: User, access_token: str, refresh_token: str, session_kind: str) -> JSONResponse:
    """Panel response: the refresh token goes only in the HttpOnly cookie, never in the body."""
    body = WebToken(access_token=access_token, user=UserRead.model_validate(db_user, from_attributes=True))
    response = JSONResponse(content=body.model_dump(mode="json"))
    set_refresh_cookie(response, refresh_token, session_kind)
    return response


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
    new_kind: str | None = None,
) -> tuple[User, str, str, str]:
    """
    Atomically validate, rotate, and revoke refresh token.
    Uses pessimistic locking to prevent race conditions.

    The new token inherits the session kind of the one it replaces, unless `new_kind` says otherwise
    (used once to move a panel session from localStorage to the cookie).

    Returns: (user, new_access_token, new_refresh_token_string, new_session_kind)
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
        session_kind = new_kind or db_token.session_kind
        new_refresh_token, new_db_token = await create_db_refresh_token(db, db_user, request, session_kind)

        # Revoke the old token in the same transaction
        db_token.revoked_at = datetime.now(timezone.utc)
        db_token.replaced_by_token_id = new_db_token.id

        return db_user, create_access_token(db_user), new_refresh_token, session_kind


@router.post("/login", response_model=Token)
async def login(
    user_login: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    enforce_rate_limit(login_ip_limiter, get_client_key(request))
    enforce_rate_limit(login_email_limiter, str(user_login.email).lower())
    db_user = await get_user_by_email(db, str(user_login.email))

    password_ok = verify_password(
        user_login.password,
        db_user.password if db_user is not None else _DUMMY_PASSWORD_HASH,
    )
    if db_user is None or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    session_kind = SESSION_KIND_MOBILE
    if user_login.client == "web":
        session_kind = SESSION_KIND_WEB if user_login.remember else SESSION_KIND_WEB_SESSION
    refresh_token, _ = await create_db_refresh_token(db, db_user, request, session_kind)
    await db.commit()

    if session_kind in WEB_SESSION_KINDS:
        return web_session_response(db_user, create_access_token(db_user), refresh_token, session_kind)

    return {
        "access_token": create_access_token(db_user),
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": db_user,
    }


def read_refresh_source(request: Request, body_token: str | None) -> tuple[str | None, bool]:
    """The refresh token and whether it came from the cookie. The body wins when both are present."""
    if body_token is not None:
        return body_token, False
    return request.cookies.get(REFRESH_COOKIE_NAME), True


@router.post("/refresh", response_model=Token)
async def refresh_token(
    request: Request,
    payload: RefreshTokenRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    enforce_rate_limit(refresh_ip_limiter, get_client_key(request))
    payload = payload or RefreshTokenRequest()
    token, from_cookie = read_refresh_source(request, payload.refresh_token)
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate refresh token")
    if from_cookie:
        reject_foreign_origin(request)

    new_kind = None
    if not from_cookie and payload.client == "web":
        new_kind = SESSION_KIND_WEB if payload.remember else SESSION_KIND_WEB_SESSION

    try:
        db_user, access_token, refresh_token_str, session_kind = await validate_and_rotate_refresh_token_atomically(
            db, token, request, new_kind
        )
    except HTTPException as exc:
        if from_cookie and exc.status_code == status.HTTP_401_UNAUTHORIZED:
            # Do not leave a dead cookie behind for the browser to keep sending.
            response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            clear_refresh_cookie(response)
            return response
        raise
    await db.commit()

    if session_kind in WEB_SESSION_KINDS:
        return web_session_response(db_user, access_token, refresh_token_str, session_kind)

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
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    
    return user

@router.post("/", response_model=UserRead)
async def create_user(user: UserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    enforce_rate_limit(register_ip_limiter, get_client_key(request))
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
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()

    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(user.current_password, db_user.password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    existing_user = await get_user_by_email(db, str(user.email))
    if existing_user is not None and existing_user.id != user_id:
        raise HTTPException(status_code=409, detail="Email already registered")

    db_user.name = user.name
    db_user.email = str(user.email)
    if user.password is not None:
        db_user.password = hash_password(user.password)
        # A password change must end every other session.
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )

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
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()

    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(db_user)
    await db.commit()

    return {"detail": "User deleted successfully"}
