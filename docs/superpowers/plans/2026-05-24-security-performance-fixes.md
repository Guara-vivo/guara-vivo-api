# Security & Performance Fixes Implementation Plan

> **For agentic workers:** Execute tasks sequentially using inline execution with checkpoints.

**Goal:** Fix 9 security and performance issues in guara-vivo-api discovered by code review.

**Architecture:** Fix migrations idempotency, remove default credentials, harden body limits, atomize token rotation, validate uploads, optimize memory, improve rate limiting, blind resource enumeration, and update documentation.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Supabase, bcrypt, Python magic/Pillow

---

## Task 1: Fix Migration Duplicated Status Column

**Files:**
- Modify: `migrations/versions/d3a87201af95_add_status_column_to_records_table.py`
- Test: Manual `alembic upgrade head` on clean database

**Issue:** `records.status` created in initial migration, but d3a migration adds it again. Fails on fresh database.

- [ ] **Step 1: Read current d3a migration**

```bash
Read: migrations/versions/d3a87201af95_add_status_column_to_records_table.py
```

- [ ] **Step 2: Modify to add `IF NOT EXISTS` check**

```python
# Replace the op.add_column call with conditional:
from alembic import op
import sqlalchemy as sa

def upgrade():
    # Check if column exists before adding
    inspector = op.get_context().get_impl(op.get_context().dialect).get_columns('records', schema=None)
    column_names = [col['name'] for col in inspector]
    
    if 'status' not in column_names:
        op.add_column('records', sa.Column('status', sa.String(), nullable=False, server_default='pending'))
    else:
        # Already exists, ensure it's NOT NULL with correct default
        pass

def downgrade():
    # Only drop if exists
    inspector = op.get_context().get_impl(op.get_context().dialect).get_columns('records', schema=None)
    column_names = [col['name'] for col in inspector]
    
    if 'status' in column_names:
        op.drop_column('records', 'status')
```

- [ ] **Step 3: Commit migration fix**

```bash
git add migrations/versions/d3a87201af95_add_status_column_to_records_table.py
git commit -m "fix: make records.status column addition idempotent"
```

---

## Task 2: Remove Default Admin Credentials

**Files:**
- Modify: `src/main.py` (lifespan handler)
- Modify: `src/seed.py`
- Modify: `.env.example`
- Modify: `README.md`

**Issue:** Hard-coded `admin@example.com` / `admin123` seeded automatically. Security risk.

- [ ] **Step 1: Read current seed code**

```bash
Read: src/main.py (search for seed/startup)
Read: src/seed.py
```

- [ ] **Step 2: Modify `src/main.py` to require environment variables**

Update lifespan to:

```python
async def lifespan(app: FastAPI):
    async with get_db() as session:
        # Check if admin exists
        result = await session.execute(select(User).where(User.email == "admin@example.com"))
        admin = result.scalars().first()
        
        if not admin:
            # Admin seeding only if env vars provided
            admin_email = os.getenv("ADMIN_EMAIL")
            admin_password = os.getenv("ADMIN_PASSWORD")
            
            if not admin_email or not admin_password:
                logger.error("Admin seed required but ADMIN_EMAIL/ADMIN_PASSWORD not set")
                # Do NOT seed by default; require explicit env config
            else:
                # Hash password and create admin
                hashed = bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                admin_user = User(email=admin_email.lower(), password=hashed)
                session.add(admin_user)
                await session.commit()
    
    yield
```

- [ ] **Step 3: Update `src/seed.py` to require env vars**

```python
import os
import bcrypt
from src.database import AsyncSessionLocal
from src.models.user import User

async def seed_admin():
    email = os.getenv("ADMIN_EMAIL")
    password = os.getenv("ADMIN_PASSWORD")
    
    if not email or not password:
        print("ADMIN_EMAIL and ADMIN_PASSWORD required. Skipping seed.")
        return
    
    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(User).where(User.email == email.lower()))
        if existing.scalars().first():
            print(f"Admin {email} already exists")
            return
        
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        user = User(email=email.lower(), password=hashed)
        session.add(user)
        await session.commit()
        print(f"Admin {email} created")

if __name__ == "__main__":
    import asyncio
    asyncio.run(seed_admin())
```

- [ ] **Step 4: Update `.env.example`**

Add:
```
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=change-me-in-production
ENABLE_ADMIN_SEED=true
```

- [ ] **Step 5: Update `README.md`**

Add section:
```markdown
## Admin User

Admin seed requires environment variables:
- `ADMIN_EMAIL`: Admin email address
- `ADMIN_PASSWORD`: Admin password (plain text, hashed on seed)
- `ENABLE_ADMIN_SEED`: Set to `false` in production if no admin seed is needed

If not provided, admin seed is skipped. First manual user must be created via API or direct SQL.
```

- [ ] **Step 6: Commit changes**

```bash
git add src/main.py src/seed.py .env.example README.md
git commit -m "security: remove hard-coded admin credentials, require env vars"
```

---

## Task 3: Implement Real Request Body Limit

**Files:**
- Modify: `src/main.py` (create ASGI middleware)
- Add: `src/middleware.py` (new file for body limit middleware)
- Test: Manual POST with oversized body

**Issue:** Body limit only checks `Content-Length` header, which can be missing or manipulated.

- [ ] **Step 1: Create `src/middleware.py`**

```python
import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", "10485760"))

class BodyLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Only check POST, PUT, PATCH
        if request.method in ["POST", "PUT", "PATCH"]:
            # Read body stream with size limit
            body = b""
            async for chunk in request.stream():
                body += chunk
                if len(body) > MAX_REQUEST_BODY_BYTES:
                    return Response(
                        status_code=413,
                        content={"detail": "Payload too large"},
                        media_type="application/json"
                    )
            
            # Replace request stream with our body
            request._receive = self._receive_factory(body)
        
        return await call_next(request)
    
    def _receive_factory(self, body):
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        return receive
```

- [ ] **Step 2: Modify `src/main.py` to use middleware**

Add import:
```python
from src.middleware import BodyLimitMiddleware
```

Add to app creation (before other middleware):
```python
app.add_middleware(BodyLimitMiddleware)
```

Remove old `request_body_size` check from existing middleware if present.

- [ ] **Step 3: Commit**

```bash
git add src/middleware.py src/main.py
git commit -m "security: implement real ASGI body limit middleware"
```

---

## Task 4: Atomize Refresh Token Rotation

**Files:**
- Modify: `src/security.py` (validate + revoke + issue in single transaction)
- Modify: `src/routes/user.py` (POST /users/refresh)
- Test: Two concurrent `POST /users/refresh` calls

**Issue:** Token rotation not atomic; concurrent requests can reuse same token before revocation.

- [ ] **Step 1: Read current refresh token logic**

```bash
Read: src/security.py
Read: src/routes/user.py (search for /users/refresh)
```

- [ ] **Step 2: Modify `src/security.py` to add atomic validation**

```python
async def validate_and_rotate_refresh_token(token: str, session: AsyncSession) -> tuple[User, str, str]:
    """
    Validate refresh token, revoke old, issue new tokens atomically.
    Raises if token already used/revoked.
    """
    from sqlalchemy import func
    
    # Hash the token
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    
    # Lock and validate in single transaction
    async with session.begin_nested():
        # SELECT FOR UPDATE to lock the row
        result = await session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash
            ).with_for_update()
        )
        refresh_token_row = result.scalars().first()
        
        if not refresh_token_row:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        
        # Check if already revoked
        if refresh_token_row.revoked_at or refresh_token_row.replaced_by_token_id:
            raise HTTPException(status_code=401, detail="Token already used or revoked")
        
        # Check expiration
        if datetime.now(timezone.utc) > refresh_token_row.expires_at:
            raise HTTPException(status_code=401, detail="Token expired")
        
        # Get user
        user = await session.get(User, refresh_token_row.user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        
        # Create new tokens
        new_access_token = create_access_token(user)
        new_refresh_token = create_refresh_token(user)
        
        # Mark old token as replaced
        refresh_token_row.replaced_by_token_id = new_refresh_token.id
        refresh_token_row.revoked_at = datetime.now(timezone.utc)
        
        # Save new token
        session.add(new_refresh_token)
        await session.flush()
    
    return user, new_access_token, new_refresh_token.token
```

- [ ] **Step 3: Modify POST /users/refresh to use atomic function**

```python
@router.post("/refresh")
async def refresh_token(
    refresh_token: str = Header(...),
    db: AsyncSession = Depends(get_db)
):
    user, new_access, new_refresh = await validate_and_rotate_refresh_token(refresh_token, db)
    await db.commit()
    
    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "user": UserRead.from_orm(user)
    }
```

- [ ] **Step 4: Commit**

```bash
git add src/security.py src/routes/user.py
git commit -m "security: atomize refresh token rotation with pessimistic locking"
```

---

## Task 5: Validate Upload File Types by Magic Bytes

**Files:**
- Modify: `src/routes/record.py`
- Modify: `src/supabase_storage.py`
- Add: `src/utils/file_validation.py` (new)
- Test: Upload fake JPEG (actually PNG)

**Issue:** Upload validates only by `content_type` and extension, not by file content.

- [ ] **Step 1: Create `src/utils/file_validation.py`**

```python
import magic
from fastapi import HTTPException

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

def validate_image_file(filename: str, file_bytes: bytes) -> str:
    """
    Validate image by extension and magic bytes.
    Returns detected MIME type or raises HTTPException.
    """
    import os
    
    # Check extension
    _, ext = os.path.splitext(filename)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type {ext} not allowed")
    
    # Check magic bytes
    mime = magic.from_buffer(file_bytes, mime=True)
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File MIME type {mime} not allowed. Only JPEG, PNG, WebP accepted."
        )
    
    return mime
```

- [ ] **Step 2: Update `requirements.txt`**

Add:
```
python-magic-bin==0.4.14  # Windows binary for magic
```

Or for Linux:
```
python-magic==0.4.27
```

- [ ] **Step 3: Modify `src/routes/record.py` to validate uploads**

```python
from src.utils.file_validation import validate_image_file

# In upload handler:
for image in images:
    file_content = await image.read()
    
    # Validate by magic bytes
    mime = validate_image_file(image.filename, file_content)
    
    # Then upload to storage...
```

- [ ] **Step 4: Commit**

```bash
git add src/utils/file_validation.py src/routes/record.py requirements.txt
git commit -m "security: validate upload files by magic bytes, not just MIME header"
```

---

## Task 6: Stream Upload Processing to Reduce Memory

**Files:**
- Modify: `src/routes/record.py` (upload handler)
- Modify: `src/supabase_storage.py` (streaming upload)

**Issue:** Upload reads all files into memory. With 20 files × 5MB each = 100MB peak.

- [ ] **Step 1: Modify upload to process files sequentially**

```python
# In POST /records/upload handler:
image_urls = []
total_size = 0
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100MB aggregate

for image in images:
    # Read and validate
    file_content = await image.read()
    file_size = len(file_content)
    
    # Check aggregate limit
    if total_size + file_size > MAX_TOTAL_SIZE:
        raise HTTPException(status_code=413, detail="Total upload size exceeds limit")
    
    # Validate MIME
    mime = validate_image_file(image.filename, file_content)
    
    # Upload to storage
    url = await supabase_upload(image.filename, file_content, mime)
    image_urls.append(url)
    
    total_size += file_size
    # Explicitly free memory after each file
    del file_content
```

- [ ] **Step 2: Commit**

```bash
git add src/routes/record.py src/supabase_storage.py
git commit -m "perf: stream upload processing to reduce memory footprint"
```

---

## Task 7: Improve In-Memory Rate Limiter

**Files:**
- Modify: `src/routes/user.py` (rate limiter)

**Issue:** In-memory rate limiter grows unbounded, ineffective across replicas.

- [ ] **Step 1: Add cleanup and limits to rate limiter**

```python
from collections import defaultdict
from datetime import datetime, timedelta
import asyncio

class RateLimiter:
    def __init__(self, max_attempts: int = 5, window_seconds: int = 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.attempts = defaultdict(list)  # ip -> [timestamp, timestamp, ...]
        self.max_ips = 10000  # Prevent unbounded growth
    
    def is_allowed(self, client_ip: str) -> bool:
        now = datetime.now()
        window_start = now - timedelta(seconds=self.window_seconds)
        
        # Prune old attempts
        if client_ip in self.attempts:
            self.attempts[client_ip] = [
                ts for ts in self.attempts[client_ip]
                if ts > window_start
            ]
        
        # Check limit
        if len(self.attempts[client_ip]) >= self.max_attempts:
            return False
        
        # Record attempt
        self.attempts[client_ip].append(now)
        
        # Cleanup old IPs if map grows too large
        if len(self.attempts) > self.max_ips:
            self.attempts = {
                ip: ts for ip, ts in self.attempts.items()
                if ts and ts[-1] > window_start
            }
        
        return True
```

- [ ] **Step 2: Document Redis upgrade path**

Add to `src/routes/user.py`:
```python
# TODO: Replace in-memory limiter with Redis for production/horizontal scaling
# See environment variable: RATE_LIMIT_BACKEND (future implementation)
```

- [ ] **Step 3: Commit**

```bash
git add src/routes/user.py
git commit -m "perf: add cleanup and cardinalitylimits to in-memory rate limiter"
```

---

## Task 8: Blind Resource Ownership Checks (404 vs 403)

**Files:**
- Modify: `src/routes/record.py`
- Modify: `src/routes/analysis.py`
- Modify: `src/routes/ibis.py`

**Issue:** Return 403 for unauthorized access reveals resource exists. Use 404 instead.

- [ ] **Step 1: Update `src/routes/record.py`**

For all GET/{id}, PUT/{id}, DELETE/{id} endpoints:

```python
@router.get("/records/{id}")
async def get_record(id: int, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Record).where(Record.id == id, Record.user_id == current_user.id))
    record = result.scalars().first()
    
    # Return 404 whether record doesn't exist OR doesn't belong to user
    if not record:
        raise HTTPException(status_code=404, detail="Not found")
    
    return record
```

- [ ] **Step 2: Update `src/routes/analysis.py`**

Apply same pattern: no 403, always 404 for missing or unauthorized.

- [ ] **Step 3: Update `src/routes/ibis.py`**

Apply same pattern.

- [ ] **Step 4: Commit**

```bash
git add src/routes/record.py src/routes/analysis.py src/routes/ibis.py
git commit -m "security: blind resource ownership checks, return 404 for unauthorized"
```

---

## Task 9: Update Documentation

**Files:**
- Modify: `README.md`

**Issue:** Docs cite wrong Alembic head, missing env vars, missing security notes.

- [ ] **Step 1: Update migration head reference**

Replace:
```markdown
Current migration chain is ... -> 20260517_0003 -> ...
```

With:
```markdown
Current migration chain is 20260516_0001 -> 20260517_0002 -> d3a87201af95 -> 269cbb5d99ef -> 20260517_0003 -> 20260517_0004 -> 20260517_0005 -> 20260518_0006 -> 20260518_0007 -> 20260520_0008 (HEAD).
```

- [ ] **Step 2: Add security notes section**

```markdown
## Security Notes

- Admin credentials must be provided via `ADMIN_EMAIL` and `ADMIN_PASSWORD` env vars.
- Request body size limited to `MAX_REQUEST_BODY_BYTES` (default 10MB).
- Refresh tokens are single-use and rotated atomically on validation.
- Uploads validated by file MIME type (magic bytes), not just extension.
- Total upload size per request limited to 100MB.
- Resource access checks return 404 for non-existent or unauthorized resources (no 403).
```

- [ ] **Step 3: Update env vars section**

Add:
```markdown
- `ADMIN_EMAIL`, `ADMIN_PASSWORD`: Admin user seed (optional; if not set, no admin seed on startup)
- `ENABLE_ADMIN_SEED`: Set to `false` to skip admin seed entirely
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: update migration chain, add security and env var notes"
```

---

## Verification

After all tasks, verify:

- [ ] Run `alembic upgrade head` on clean database
- [ ] Run `pytest tests/` (if tests exist)
- [ ] Manual test: POST login with rate limit
- [ ] Manual test: POST refresh twice concurrently (one should fail)
- [ ] Manual test: POST upload with fake JPEG (should reject)
- [ ] Manual test: POST oversized body (should get 413)
- [ ] Manual test: Access record from other user (should get 404)

---
