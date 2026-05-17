# Guara Vivo API - Developer Notes

## Quick Start

```bash
# Create venv and install deps
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

# Load env vars
copy .env.example .env
# Edit .env with your external PostgreSQL DATABASE_URL

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
- Current migration chain is `20260516_0001` -> `20260517_0002` -> `d3a87201af95`.
- `20260517_0002_remove_analysis_unused_fields.py` removes `flock_size`, `latitude`, and `longitude` from `analyses`.
- `d3a87201af95_add_status_column_to_records_table.py` adds `records.status`, backfills existing rows to `pending`, and makes it `NOT NULL`.
- If an existing database already has the initial tables but no Alembic version, stamp the baseline first with `alembic stamp 20260516_0001`, then run `alembic upgrade head`.
- Alembic remains synchronous and reads `DATABASE_URL` from `.env` through `src/database.py`.
- Keep Alembic on the sync PostgreSQL driver (`postgresql+psycopg2://` or compatible); the API runtime converts to `postgresql+asyncpg://` for async sessions.

## Seed Data

Startup seeds only the admin user when the users table is empty. This runs inside FastAPI's async lifespan handler.

Optional sample data can be loaded manually:
```bash
python src/seed.py
```

## API Endpoints

- `GET /docs` - Swagger UI
- `/users/{id}` - GET, POST, PUT, DELETE
- `/records` - CRUD on records
- `/analysis` - CRUD on analyses  
- `/ibis` - CRUD on ibis
- List endpoints for `records`, `analysis`, and `ibis` support `skip` and `limit` query params. Default: `skip=0&limit=100`.

## Models

- `Record.status` is required and defaults to `pending`.
- Allowed record statuses: `pending`, `processing`, `completed`, `failed`.
- `Record.status` is persisted in the `records.status` column and must stay aligned between `src/models/record.py`, `src/schemas.py`, and Alembic migrations.
- `Record.images` and `Record.behavior` use PostgreSQL `ARRAY(String)`, so PostgreSQL support is required.
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
- Production schema has been migrated to Alembic head `d3a87201af95`: `analyses` no longer has unused location/flock fields and `records.status` exists with existing rows set to `pending`.

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
