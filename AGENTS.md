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
- PostgreSQL needs `psycopg2-binary`: `pip install psycopg2-binary`.
- SQLAlchemy uses `pool_pre_ping=True` to avoid stale pooled connections.
- Do not enable `echo=True` in normal execution because it logs SQL and can expose sensitive data.
- Tables are managed through Alembic migrations. Startup does not auto-create tables.

## Migrations

- Use Alembic for persistent schema changes.
- Apply migrations with `alembic upgrade head` before starting the API.
- Create a migration after model changes with `alembic revision --autogenerate -m "message"`, then review the generated file before applying it.
- Initial schema migration is in `migrations/versions/20260516_0001_initial_schema.py`.
- Alembic reads `DATABASE_URL` from `.env` through `src/database.py`.

## Seed Data

Startup seeds only the admin user when the users table is empty.

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
- `Record.images` and `Record.behavior` use PostgreSQL `ARRAY(String)`, so PostgreSQL support is required.
- Table models live under `src/models/` and should be used for persistence only.
- Request/response schemas live in `src/schemas.py`.

## Route Practices

- Keep list endpoints paginated; avoid unbounded `.all()` calls.
- Keep PUT handlers aligned with model fields; do not update nonexistent fields.
- Use `HTTPException(status_code=404, detail="... not found")` for missing entities.
- Use dedicated schemas from `src/schemas.py` for request bodies and response models.
- Prefer explicit field assignment in route updates.

## Database Practices

- Use one session per request through `get_db()`.
- Close sessions in `finally` or use a context manager for scripts.
- Avoid logging full database URLs, credentials, or raw SQL in production.
- Validate foreign-key assumptions before adding new seed data.
- Do not reintroduce `SQLModel.metadata.create_all()` into application startup.

## Performance Notes

- Avoid loading full tables in API responses.
- Add indexes before introducing frequent filters/searches beyond primary-key lookups.
- Keep response payloads bounded with pagination.
- Avoid expensive work during FastAPI startup beyond schema creation and minimal seed.

## Testing

No test files in repo yet. Add tests to `tests/` dir with `pytest`.

## Notes

- PostgreSQL is mandatory; local SQLite fallback was removed.
- Startup seeds admin user only; sample `Record`, `Analysis`, and `Ibis` rows are not created automatically.
- `database.db` was removed and is ignored.
- Routes use separate create/update/read schemas instead of SQLModel table models directly.

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
