# Guara Vivo API - Developer Notes

## Quick Start

```bash
# Create venv and install deps
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

# Load env vars
copy .env.example .env
# Edit .env with your external PostgreSQL DATABASE_URL and JWT_SECRET_KEY

# Apply database migrations
alembic upgrade head

# Run dev server
python src/main.py
# Default: http://localhost:8001
# Or: uvicorn src.main:app --host 0.0.0.0 --port 8001 --reload
```

## Database

- PostgreSQL is required. The app must fail fast if `DATABASE_URL` is missing or is not PostgreSQL.
- Valid URL prefixes: `postgres://`, `postgresql://`, `postgresql+psycopg2://`.
- SQLite is not supported by default and `database.db` must not be committed.
- PostgreSQL needs `psycopg2-binary` for Alembic and `asyncpg` for the async API runtime.
- Runtime database access uses SQLAlchemy async engine/session (`create_async_engine`, `AsyncSession`, `async_sessionmaker`).
- SQLAlchemy uses `pool_pre_ping=True` to avoid stale pooled connections.
- Do not enable `echo=True` in normal execution because it logs SQL and can expose sensitive data.
- Tables are managed through Alembic migrations. Startup does not auto-create tables.

## Migrations

- Use Alembic for persistent schema changes.
- Apply migrations with `alembic upgrade head` before starting the API.
- Create a migration after model changes with `alembic revision --autogenerate -m "message"`, then review the generated file before applying it.
- Initial schema migration is in `migrations/versions/20260516_0001_initial_schema.py`.
- Current migration chain is `20260516_0001` -> `20260517_0002` -> `d3a87201af95` -> `269cbb5d99ef`.
- `20260517_0002_remove_analysis_unused_fields.py` removes `flock_size`, `latitude`, and `longitude` from `analyses`.
- `d3a87201af95_add_status_column_to_records_table.py` adds `records.status`, backfills existing rows to `pending`, and makes it `NOT NULL`.
- `269cbb5d99ef_add_password_column_to_users_table.py` adds required `users.password`, backfills existing users with the bcrypt hash for `admin123`, and makes it `NOT NULL`.
- If an existing database already has the initial tables but no Alembic version, stamp the baseline first with `alembic stamp 20260516_0001`, then run `alembic upgrade head`.
- Alembic remains synchronous and reads `DATABASE_URL` from `.env` through `src/database.py`.
- Keep Alembic on the sync PostgreSQL driver (`postgresql+psycopg2://` or compatible); the API runtime converts to `postgresql+asyncpg://` for async sessions.

## Seed Data

Startup seeds only the admin user when the users table is empty. This runs inside FastAPI's async lifespan handler.
The admin seed uses `admin@example.com` / `admin123`, and the password must be stored as a bcrypt hash.

Optional sample data can be loaded manually:
```bash
python src/seed.py
```

## API Endpoints

- `GET /docs` - Swagger UI
- `/users/login` - POST, validates email/password and returns a JWT access token plus user data
- `/users/me` - GET, protected endpoint that returns the current authenticated user from `Authorization: Bearer <token>`
- `/users/{id}` - GET, POST, PUT, DELETE
- `/records` - CRUD on records
- `/analysis` - CRUD on analyses  
- `/ibis` - CRUD on ibis
- List endpoints for `records`, `analysis`, and `ibis` support `skip` and `limit` query params. Default: `skip=0&limit=100`.

## Authentication

- Passwords are required for users and must be stored only as bcrypt hashes.
- Use `bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")` for hashing and `bcrypt.checkpw()` for verification.
- `src/routes/user.py` owns password hashing, password verification, user creation/update, and login.
- `src/security.py` owns JWT access-token creation and current-user validation.
- Login returns a stateless JWT access token with `token_type="bearer"` and the authenticated `UserRead` payload.
- JWT payload includes `sub` as the user id, `email`, `type="access"`, `iat`, and `exp`.
- Access tokens currently expire after 1 hour by default (`JWT_ACCESS_TOKEN_EXPIRE=3600`).
- No refresh token or remote logout is implemented yet.
- Protected endpoints should depend on `get_current_user` from `src/security.py`.
- Frontends should persist the returned `access_token` and send `Authorization: Bearer <access_token>` on protected requests.
- Frontends can call `GET /users/me` to validate an existing session.

## Environment

- `DATABASE_URL` is required and must point to PostgreSQL.
- `JWT_SECRET_KEY` is required before issuing or validating JWTs. Generate it with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `JWT_ACCESS_TOKEN_EXPIRE` is optional and defaults to `3600` seconds.

## Models

- `Record.status` is required and defaults to `pending`.
- Allowed record statuses: `pending`, `processing`, `completed`, `failed`.
- `Record.status` is persisted in the `records.status` column and must stay aligned between `src/models/record.py`, `src/schemas.py`, and Alembic migrations.
- `Record.images` and `Record.behavior` use PostgreSQL `ARRAY(String)`, so PostgreSQL support is required.
- `User.password` is required and persists only the bcrypt hash, never the plain text password.
- `Analysis` currently stores only `id`, `ibis_quantity`, `datetime`, and `recorder_id`; do not reintroduce `flock_size`, `latitude`, or `longitude` unless there is a new schema requirement and migration.
- `Analysis.recorder_id` is a unique foreign key to `records.id`, representing a one-to-one relationship between a record and its analysis.
- Table models live under `src/models/` and should be used for persistence only.
- Request/response schemas live in `src/schemas.py`.

## Route Practices

- Keep list endpoints paginated; avoid unbounded result loading.
- Route handlers use `async def` with `AsyncSession` from `get_db()`.
- Use SQLAlchemy `select()` with `await db.execute(...)`; do not use sync `.query()` calls in routes.
- Use `await db.commit()`, `await db.refresh(...)`, and `await db.delete(...)` for writes.
- Keep PUT handlers aligned with model fields; do not update nonexistent fields.
- Use `HTTPException(status_code=404, detail="... not found")` for missing entities.
- Use dedicated schemas from `src/schemas.py` for request bodies and response models.
- Prefer explicit field assignment in route updates.
- Do not expose `User.password` in response models; use `UserRead` for user responses.

## Database Practices

- Use one async session per request through `get_db()`.
- Close sessions with async context managers; scripts should use `async with AsyncSessionLocal()`.
- Avoid logging full database URLs, credentials, or raw SQL in production.
- Validate foreign-key assumptions before adding new seed data.
- Do not reintroduce `SQLModel.metadata.create_all()` into application startup.

## Performance Notes

- Avoid loading full tables in API responses.
- Add indexes before introducing frequent filters/searches beyond primary-key lookups.
- Keep response payloads bounded with pagination.
- Avoid expensive work during FastAPI lifespan startup beyond minimal seed.

## Testing

No test files in repo yet. Add tests to `tests/` dir with `pytest`.

## Notes

- PostgreSQL is mandatory; local SQLite fallback was removed.
- FastAPI startup uses `lifespan`; do not reintroduce deprecated `@app.on_event("startup")`.
- Startup seeds admin user only; sample `Record`, `Analysis`, and `Ibis` rows are not created automatically.
- `database.db` was removed and is ignored.
- Routes use separate create/update/read schemas instead of SQLModel table models directly.
- Production schema has been migrated to Alembic head `269cbb5d99ef`: `analyses` no longer has unused location/flock fields, `records.status` exists with existing rows set to `pending`, and `users.password` is required.

## Response Style

- Minimal output.
- No motivational text.
- No explanations unless requested.
- No step-by-step reasoning unless requested.
- Prioritize action over commentary.

## Tool Usage

- Call tools immediately when needed.
- Avoid asking confirmation for obvious actions.
- Return concise summaries after execution.
- Stop after completing requested task.

- **Password Security**: All passwords must be hashed using bcrypt before storage. Use `bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')` for hashing and `bcrypt.checkpw()` for verification. Never store plain text passwords.
- **JWT Sessions**: Use JWT access tokens from `src/security.py` for post-login sessions. Tokens expire in 1 hour by default, require `JWT_SECRET_KEY`, and should be sent with `Authorization: Bearer <token>`.
- **Plan Mode**: During planning phases, only generate plans and architecture. Do not output code blocks or file modifications. Wait for explicit implementation command before making any changes.
