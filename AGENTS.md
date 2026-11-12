# Guara Vivo API - Developer Notes

## Current Architecture

- API repo: `guara-vivo-api`, FastAPI on port `8001`.
- Identifier repo: sibling `../guara-vivo-identifier`, inference API on port `8000`.
- Worker repo: sibling `../guara-vivo-worker`, consumes RabbitMQ jobs and calls the identifier service.
- PostgreSQL is external in Supabase. There is no local Postgres service in `docker-compose.yml`.
- Uploaded frontend images are stored in Supabase Storage bucket `frontend-uploads`; `records.images` stores public URLs.
- RabbitMQ runs locally through Docker Compose with the management UI on port `15672`.
- Inference flow: frontend creates a record -> API inserts `records` row -> API publishes `record_id` to RabbitMQ -> worker calls identifier -> worker writes aggregate `analyses`, per-image `analysis_images`, and per-detection `ibis` rows.

## Quick Start

```bash
# Create venv and install deps
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

# Load env vars
copy .env.example .env
# Edit .env with your Supabase PostgreSQL DATABASE_URL and JWT_SECRET_KEY

# Apply database migrations
alembic upgrade head

# Run dev server
python src/main.py
# Default: http://localhost:8001
# Or: uvicorn src.main:app --host 0.0.0.0 --port 8001 --reload
```

## Docker Compose

- `docker-compose.yml` orchestrates `api`, `identifier`, `worker`, and `rabbitmq`.
- Docker Compose does not start PostgreSQL; use Supabase through `DATABASE_URL`.
- Use `.env.docker-compose` for local Docker secrets and runtime config. This file is ignored by Git.
- Keep `.env.docker-compose.example` versioned as a safe template with placeholder values only.
- Start the stack with `docker compose --env-file .env.docker-compose up --build`.
- Validate the stack with `docker compose --env-file .env.docker-compose config`, but do not share that output because it expands secrets.
- The API service runs `alembic upgrade head` before starting `uvicorn`.
- The `api` and `worker` services depend on RabbitMQ health, not on a local database container.

## Database

- PostgreSQL is required. The app must fail fast if `DATABASE_URL` is missing or is not PostgreSQL.
- Supabase is the current PostgreSQL provider; set the Supabase connection string in `.env` or `.env.docker-compose`.
- Valid URL prefixes: `postgres://`, `postgresql://`, `postgresql+psycopg2://`.
- SQLite is not supported by default and `database.db` must not be committed.
- Do not reintroduce a local Postgres container unless explicitly requested.
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
- Current migration chain is `20260516_0001` -> `20260517_0002` -> `d3a87201af95` -> `269cbb5d99ef` -> `20260517_0003` -> `20260517_0004` -> `20260517_0005` -> `20260518_0006` -> `20260518_0007` -> `20260520_0008` -> `20260526_0009` -> `20260603_0010` -> `20260604_0011` -> `20260605_0012` -> `20260606_0013` -> `20260606_0014` -> `20260607_0015` -> `20260608_0016` -> `20260609_0017` -> `20260610_0018` -> `20261112_0019` -> `20261112_0020` (head).
- `20260517_0002_remove_analysis_unused_fields.py` removes `flock_size`, `latitude`, and `longitude` from `analyses`.
- `d3a87201af95_add_status_column_to_records_table.py` adds `records.status` with idempotent logic to handle both initial schema creation and migration scenarios. Backfills existing rows to `pending` and makes the column `NOT NULL`.
- `269cbb5d99ef_add_password_column_to_users_table.py` adds required `users.password`, backfills existing users with the bcrypt hash for `admin123`, and makes it `NOT NULL`.
- `20260517_0003_add_security_performance_indexes.py` adds a unique index on `users.email` plus indexes on `records.user_id` and `ibis.analysis_id`.
- `20260517_0004_fix_records_images_array_type.py` fixes existing databases where `records.images` was created as `varchar` instead of `varchar[]`.
- `20260517_0005_add_unique_constraint_to_analyses_recorder_id.py` adds the unique constraint required by worker upserts on `analyses.recorder_id`.
- `20260518_0006_make_datetimes_timezone_aware.py` converts `analyses.datetime`, `ibis.datetime`, and other timestamp columns to `DateTime(timezone=True)` for UTC consistency.
- `20260518_0007_add_refresh_tokens.py` creates the `refresh_tokens` table with `user_id`, `token_hash`, `jti`, `expires_at`, `revoked_at`, `created_at`, `replaced_by_token_id`, `user_agent`, and `ip_address` columns.
- `20260520_0008_add_per_image_analysis.py` creates `analysis_images` and adds nullable `ibis.analysis_image_id` for image-level detections.
- `20260526_0009_add_map_zones_table.py` creates `map_zones` (`type`, `latitude`, `longitude`, `radius_meters` default 50, `user_id`, `created_at`) with indexes on `type` and `user_id`.
- `20260603_0010_add_record_analysis_progress.py` adds `records.analysis_progress` (0-100, check constraint), backfilled from `status`.
- `20260604_0011_add_zone_names_and_record_links.py` adds `map_zones.name` and `sequence_index` (backfilled per type) and the record-to-zone link.
- `20260605_0012_add_record_client_uuid.py` adds nullable `records.client_uuid` with a unique constraint on `(user_id, client_uuid)` so clients can retry a create without duplicating it.
- `20260606_0013_enable_row_level_security.py` enables row level security on all public tables (PostgreSQL only).
- `20260606_0014_cascade_record_deletes.py` recreates the foreign keys so deleting a record cascades to its analysis, images and ibis.
- `20260607_0015_user_admin_and_cascade.py` adds `users.is_admin` (default false) and recreates user-related foreign keys with cascade.
- `20260608_0016_add_record_queued_at.py` adds `records.queued_at` (default now) plus partial index `ix_records_pending_queued_at` on pending rows, used by `src/requeue.py`.
- `20260609_0017_add_analysis_status_and_warnings.py` adds nullable `analyses.status_analise` (`ok|atencao|sem_ave|sem_guara`) and `analyses.avisos` (`varchar[]` of warning codes) from the identifier's root response. The worker must write them; `AnalysisRead` exposes them. The API returns codes only; user-facing text belongs to the frontend. `ibis.color`/`age_group` are free strings, so the identifier's `indefinido` is accepted as is; per-guará `status_analise`/`avisos` live in `ibis.raw_detection`.
- `20260610_0018_add_analysis_image_dimensions.py` adds nullable `analysis_images.image_width`/`image_height` (pixel space of `bbox_xyxy`, from the identifier's per-image `image_width`/`image_height`). `AnalysisImageRead` exposes them; null for older rows. Run the migration before deploying the worker (it inserts the columns). `scripts/backfill_image_dimensions.py <record_ids> [--apply]` fills old records.
- `20261112_0019_add_refresh_token_session_kind.py` adds `refresh_tokens.session_kind` (`varchar(16)`, `NOT NULL`, server default `mobile`), so existing tokens count as mobile sessions (PostgreSQL only, like the other recent revisions).
- `20261112_0020_add_user_is_active.py` adds `users.is_active` (`boolean`, `NOT NULL`, server default `true`, PostgreSQL only), so existing users stay active. It is flipped directly in the database, there is no endpoint: `update users set is_active = false where email = '...'`. A deactivated account cannot log in (same 401 `Incorrect email or password` as a wrong password, so it cannot be told apart), its access token stops working on the next request (`get_user_from_access_token` reads the user every time, which also covers the progress websocket on connect) and `POST /users/refresh` answers 401. Its refresh tokens are not revoked, so setting it back to `true` restores the session. Records and other data are untouched.
- If an existing database already has the initial tables but no Alembic version, stamp the baseline first with `alembic stamp 20260516_0001`, then run `alembic upgrade head`.
- Alembic remains synchronous and reads `DATABASE_URL` from process env or `.env` through `src/database.py`.
- Keep Alembic on the sync PostgreSQL driver (`postgresql+psycopg2://` or compatible); the API runtime converts to `postgresql+asyncpg://` for async sessions.

## RabbitMQ And Inference

- API-side RabbitMQ integration lives in `src/rabbitmq.py` and uses `pika`.
- `POST /records/` publishes `{"record_id": id}` after the record is committed.
- Queue name is configured by `QUEUE_NAME` and currently defaults to `guara-vermelho-inference`.
- RabbitMQ connection variables: `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`.
- Worker error queue is configured by `ERROR_QUEUE_NAME`, currently `guara-vermelho-inference-error`.
- Worker calls `IA_API_URL`, currently `http://identifier:8000/guara-vermelho/inference` inside Docker.
- Worker debug downloads are controlled by `DEBUG_SAVE_IMAGES_DIR`; in Docker Compose they are saved under `../guara-vivo-worker/debug-images`.
- If publishing to RabbitMQ fails during record creation, the API sets the record status to `failed` and returns an HTTP error.
- Keep worker writes aligned with the current inference schema: aggregate rows in `analyses`, one row per source image in `analysis_images`, and individual detections in `ibis` linked through `analysis_image_id`.
- `ibis.raw_detection` stores the full identifier JSON for each detected bird, including `cor`, `fase_vida`, `acuracia.deteccao_yolo`, `acuracia.classificacao_guara`, `acuracia.classificacao_cor`, `acuracia.classificacao_fase_vida`, and optional technical fields such as distance and bounding box.

## Seed Data

Startup seeds the admin user only if `ADMIN_EMAIL` and `ADMIN_PASSWORD` environment variables are configured and the user does not already exist. This runs inside FastAPI's async lifespan handler. The password is always stored as a bcrypt hash, never in plain text.

If `ADMIN_EMAIL` and `ADMIN_PASSWORD` are not configured, no admin is created at startup. The first user must be created manually via SQL or after manual setup.

Optional sample data can be loaded manually:
```bash
python src/seed.py
```

## API Endpoints

- `GET /docs` - Swagger UI (also `/redoc` and `/openapi.json`); all three are disabled when `APP_ENV=production`
- There is no `POST /users/`: public sign-up is closed (Guara-vivo/documents#56). Accounts are created directly in the database (hash the password with bcrypt as described in Authentication). The `UserCreate` schema stays for when sign-up comes back, which must not be public.
- `/users/login` - POST, validates email/password and returns a JWT access token, refresh token, and user data. Body also takes `client` (`"web"` | `"mobile"`, default `"mobile"`) and `remember` (bool, web only); with `client="web"` the refresh token goes only in the `gv_refresh` HttpOnly cookie and the body has no `refresh_token` (see Authentication).
- `/users/refresh` - POST, validates refresh token (body, else the `gv_refresh` cookie) and issues new access/refresh token pair; the body is optional
- `/users/logout` - POST, revokes the refresh token(s) named by the body and/or the cookie and expires the cookie
- `/users/me` - GET, protected endpoint that returns the current authenticated user from `Authorization: Bearer <token>`
- `/users/{id}` - GET, PUT, DELETE protected by JWT and limited to the authenticated user (others get 404). PUT requires `current_password`; `password` (new) is optional and, when set, revokes all refresh tokens. DELETE cascades to records, zones and refresh tokens.
- `/records` - CRUD on records; create/update/delete require JWT and ownership through `user_id`
- `POST /records/` and `/records/upload` answer 201 for a new record. They accept an optional `client_uuid` (UUID generated by the client). A repeated create with the same `client_uuid` for the same user returns the existing record with 200 (not 201), without uploading images again or queueing inference again. If that record is `failed` (it could not be queued, or inference gave up), the retry queues it again and sets it back to `pending`. Records return `client_uuid`. An invalid `client_uuid` returns 422 with `Identificador único da coleta (client_uuid) inválido ou ausente.`; the field stays optional for now.
- `/records/upload` - POST multipart upload, stores images in Supabase Storage bucket `frontend-uploads`, creates a record, and queues inference
- `/records/summary` - GET protected, lightweight list of the authenticated user's records (`RecordSummaryRead`); cached per user (see Hardening Notes)
- `/records/progress/ws` - WebSocket (`src/routes/progress.py`) that pushes record progress to the authenticated owner; fed by Postgres `LISTEN`/`NOTIFY` on `record_progress`
- `/records/{id}/detail` - GET protected detail endpoint returning the record, aggregate analysis, `ibis`, and ordered `image_analyses` for the authenticated owner
- `/analysis` - read-only (GET); analyses are written only by the worker
- `/ibis` - read-only (GET); detections are written only by the worker
- `/map-zones` - GET list; `GET /map-zones/{zone_id}/records` lists the records linked to a zone; POST/DELETE require `users.is_admin`; non-admins get 404 (zone logic in `src/services/map_zone_service.py`). Nest zones (`type="nest"`) are admin-only: the list hides them from non-admins, and on a nest `GET /{zone_id}/records` returns non-admins only their own records (404, same as a missing zone, when they have none). Feeding zones are visible to every logged-in user.
- List endpoints for `records`, `analysis`, and `ibis` support bounded `skip` and `limit` query params. Default: `skip=0&limit=100`, max `limit=100`.

## Authentication

- Passwords are required for users and must be stored only as bcrypt hashes.
- Use `bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")` for hashing and `bcrypt.checkpw()` for verification.
- `src/routes/user.py` owns password hashing, password verification, user creation/update, and login.
- `src/security.py` owns JWT access-token creation, refresh-token management, and current-user validation.
- Login returns a JWT access token with `token_type="bearer"` and the authenticated `UserRead` payload. For the mobile app (default) it also returns the refresh token in the body; for the panel (`client="web"`) the refresh token goes only in the cookie (see the session kinds below).
- Auth endpoints are rate limited per process by `src/ratelimit.py` (limits listed in Hardening Notes).
- JWT access token payload includes `sub` as the user id, `email`, `type="access"`, `iat`, and `exp`.
- JWT refresh token payload includes `sub` as the user id, `type="refresh"`, `jti` (unique token identifier), `iat`, and `exp`.
- Access tokens currently expire after 1 hour by default (`JWT_ACCESS_TOKEN_EXPIRE=3600`).
- Refresh tokens are long-lived, single-use, and persisted in the `refresh_tokens` table for revocation support.
- Refresh token rotation is atomic: validation, issuance, and revocation occur in the same transaction with pessimistic locking (`SELECT ... FOR UPDATE`) to prevent concurrent reuse.
- `POST /users/refresh` endpoint validates refresh tokens and issues new access/refresh token pairs.
- Two session kinds, stored in `refresh_tokens.session_kind` (migration `20261112_0019`, default `mobile`): `mobile` (the app: 1 year, token in the response body, no change for clients that send no `client`), `web` (the panel with "remember me": 7 days, sliding) and `web_session` (the panel without it: 24 h, session cookie). Rotation inherits the kind. Panel kinds are delivered only in the cookie `gv_refresh` (`HttpOnly`, `Secure` by `REFRESH_COOKIE_SECURE`, `SameSite=Lax`, `Path=/users`, no `Domain`; `Max-Age` only for `web`); their responses never carry `refresh_token` in the body (`WebToken`), so a script in the page cannot read it. Code in `src/refresh_cookie.py`.
- `POST /users/refresh` and `/users/logout` read the token from the body if present (it wins), else from the cookie. A cookie-borne request is checked against `CORS_ORIGINS`: an `Origin` that is present and not allowed gets 403 (no `Origin` is accepted); body-borne requests skip that check. An invalid cookie token answers 401 and expires the cookie.
- One-time migration of a panel session kept in `localStorage`: send that token in the refresh body with `client="web"` and `remember`; the new token is a panel kind delivered by cookie. `client`/`remember` are ignored for cookie tokens.
- Refresh token revocation is tracked via `revoked_at` column and `replaced_by_token_id` linkage.
- Protected endpoints should depend on `get_current_user` from `src/security.py`.
- User read/update/delete routes require the authenticated user's own id.
- Record, analysis, and ibis read endpoints require JWT and must return only resources owned by the authenticated user.
- Record writes require JWT and `record.user_id` must match the authenticated user.
- Analysis and ibis writes require JWT and ownership through the associated record.
- The mobile app persists both `access_token` and `refresh_token` (it gets the latter in the body). The panel keeps `access_token` only in memory; its refresh token is the `gv_refresh` HttpOnly cookie and must never be stored where JavaScript can read it. Both send `Authorization: Bearer <access_token>` on protected requests.
- Clients can call `GET /users/me` to validate an existing session or `POST /users/refresh` to obtain a new access token before expiration. The panel calls it with `credentials: 'include'` and an empty body.
- Optional user metadata (user_agent, ip_address) is captured on token issuance for security auditing.

## Environment

- `DATABASE_URL` is required and must point to PostgreSQL.
- In Docker, `DATABASE_URL` must point to Supabase or another external PostgreSQL database.
- `JWT_SECRET_KEY` is required before issuing or validating JWTs. Generate it with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `ADMIN_EMAIL` and `ADMIN_PASSWORD` configure the startup admin seed. If set, the API creates an admin user on first startup if no user exists. Both must be provided together or both omitted.
- `JWT_ACCESS_TOKEN_EXPIRE` is optional and defaults to `3600` seconds.
- `JWT_REFRESH_TOKEN_EXPIRE` is optional and defaults to `31536000` seconds (1 year), sliding: every refresh issues a new token with a fresh 1 year. It is long on purpose because the app works offline and a device can stay without network for months; clients must only drop the session when `POST /users/refresh` answers 401, never on network errors.
- `JWT_WEB_REFRESH_TOKEN_EXPIRE` (604800, 7 days) and `JWT_WEB_SESSION_REFRESH_TOKEN_EXPIRE` (86400, 24 h) set the panel session lengths; both are sliding and have defaults in `docker-compose.yml`, so no new secret is needed.
- `REFRESH_COOKIE_SECURE` defaults to `true`; set `false` only for local development over plain HTTP (a `Secure` cookie is not sent back by every browser over `http://`).
- `CORS_ORIGINS` is optional and should be a comma-separated allowlist of frontend origins. If empty, CORS middleware is not enabled. The cookie CSRF check reads the same list, so with it empty a cookie request with any `Origin` is refused.
- `MAX_REQUEST_BODY_BYTES` is optional and defaults to `10485760` (10MB). Enforced by ASGI middleware by reading the request stream.
- `MAX_UPLOAD_FILE_BYTES` is optional and defaults to `5242880` (5MB) per uploaded image. Aggregate upload limit is 10MB per request.
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and `SUPABASE_STORAGE_BUCKET` configure API uploads to Supabase Storage. The default bucket is `frontend-uploads`.
- `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_TIMEOUT`, and `DATABASE_POOL_RECYCLE` tune SQLAlchemy async pool behavior.
- `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, and `QUEUE_NAME` configure API queue publishing.
- `RABBITMQ_DEFAULT_USER` and `RABBITMQ_DEFAULT_PASS` configure the local RabbitMQ Docker container.
- `APP_ENV=production` disables the docs endpoints.
- `REQUEUE_AFTER_SECONDS` (600), `REQUEUE_INTERVAL_SECONDS` (60, `<= 0` disables) and `REQUEUE_BATCH_SIZE` (20) tune `src/requeue.py`.
- `RECORDS_CACHE_TTL_SECONDS` (30) and `RECORDS_CACHE_MAX_ENTRIES` (1000) tune the per-user records cache.
- Progress websocket: `PROGRESS_NOTIFY_CHANNEL` (`record_progress`), `WEBSOCKET_HEARTBEAT_SECONDS` (25), `WEBSOCKET_AUTH_TIMEOUT_SECONDS` (5), `WEBSOCKET_CLIENT_QUEUE_MAX_SIZE` (100), `WEBSOCKET_POSTGRES_RECONNECT_SECONDS` (5), `PROGRESS_LISTENER_PING_SECONDS` (30), `PROGRESS_LISTENER_PING_TIMEOUT_SECONDS` (10).
- `ERROR_QUEUE_NAME`, `IA_API_URL`, and `DEBUG_SAVE_IMAGES_DIR` are used by the worker container.
- Do not commit `.env`, `.env.docker-compose`, real Supabase URLs, JWT secrets, or RabbitMQ passwords.

## Request Hardening

- `src/middleware.py` enforces request body size limits by reading the ASGI stream, making it immune to Content-Length manipulation. Limit is set by `MAX_REQUEST_BODY_BYTES`.
- `src/main.py` validates `Content-Type` header for `POST`, `PUT`, and `PATCH` requests: only `application/json` and `multipart/form-data` are allowed when body is present.
- `src/main.py` configures CORS only when `CORS_ORIGINS` is non-empty; keep it as a comma-separated allowlist, never `*` with credentials.
- Upload images are validated by file extension and magic bytes (binary signature), not just the `Content-Type` header. Only JPEG, PNG, and WebP are allowed.
- Upload processing is sequential per file to reduce peak memory usage. Aggregate limit per request is 10MB.
- Unauthorized access to resources returns `404` (not `403`) to prevent enumeration attacks.
- Keep rate limiting in `src/ratelimit.py`; replace the in-memory limiter with Redis or another shared store before horizontal scaling.

## Models

- `Record.status` is required and defaults to `pending`.
- Allowed record statuses: `pending`, `processing`, `completed`, `failed`.
- `Record.status` is persisted in the `records.status` column and must stay aligned between `src/models/record.py`, `src/schemas.py`, and Alembic migrations.
- `Record.images` and `Record.behavior` use PostgreSQL `ARRAY(String)`, so PostgreSQL support is required.
- `User.password` is required and persists only the bcrypt hash, never the plain text password.
- `User.email` is validated with `EmailStr`, normalized to lowercase in request schemas, and must be unique in the database.
- `Analysis` currently stores only `id`, `ibis_quantity`, `datetime`, and `recorder_id`; do not reintroduce `flock_size`, `latitude`, or `longitude` unless there is a new schema requirement and migration.
- `Analysis.recorder_id` is a unique foreign key to `records.id`, representing a one-to-one relationship between a record and its analysis.
- `AnalysisImage` stores per-image inference metadata: `analysis_id`, `record_id`, `image_index`, `image_url`, `ibis_quantity`, `raw_result`, and `created_at`.
- `Ibis.analysis_image_id` links a detection to the image that produced it. `Ibis.raw_detection` stores the raw identifier result for frontend technical details.
- `RefreshToken` persists refresh tokens with `token_hash`, `jti`, `expires_at`, `revoked_at`, `created_at`, `replaced_by_token_id`, `user_agent`, and `ip_address` for rotation and audit tracking, plus `session_kind` (`mobile` default, `web`, `web_session`), which rotation inherits.
- All datetime columns (`analyses.datetime`, `ibis.datetime`, `records.created_at`, `refresh_tokens.created_at/expires_at/revoked_at`) use `DateTime(timezone=True)` for UTC consistency.
- Table models live under `src/models/` and should be used for persistence only.
- Request/response schemas live in `src/schemas.py`.
- `email-validator` is required by Pydantic's `EmailStr`; keep it in `requirements.txt`.

## Route Practices

- Keep list endpoints paginated; avoid unbounded result loading.
- Keep pagination parameters bounded with `Query`: `skip >= 0`, `1 <= limit <= 100`.
- Route handlers use `async def` with `AsyncSession` from `get_db()`.
- Use SQLAlchemy `select()` with `await db.execute(...)`; do not use sync `.query()` calls in routes.
- Use `await db.commit()`, `await db.refresh(...)`, and `await db.delete(...)` for writes.
- Keep PUT handlers aligned with model fields; do not update nonexistent fields.
- Use `HTTPException(status_code=404, detail="... not found")` for missing entities.
- Use dedicated schemas from `src/schemas.py` for request bodies and response models.
- Prefer explicit field assignment in route updates.
- Do not expose `User.password` in response models; use `UserRead` for user responses.
- Keep request schemas bounded with `min_length`, `max_length`, and numeric ranges where applicable.
- Validate ownership before reads and writes for user-scoped resources.
- Validate ownership before writes that reference foreign keys. Records belong to `record.user_id`; analyses and ibis inherit ownership through the associated record.
- Keep record creation publishing to RabbitMQ after the database commit so queued jobs reference persisted rows.
- Keep the Supabase service role key backend-only; never send it to the frontend.

## Database Practices

- Use one async session per request through `get_db()`.
- Configure SQLAlchemy async pooling through `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_TIMEOUT`, and `DATABASE_POOL_RECYCLE`.
- Close sessions with async context managers; scripts should use `async with AsyncSessionLocal()`.
- Avoid logging full database URLs, credentials, or raw SQL in production.
- Validate foreign-key assumptions before adding new seed data.
- Do not reintroduce `SQLModel.metadata.create_all()` into application startup.

## Performance Notes

- Avoid loading full tables in API responses.
- Add indexes before introducing frequent filters/searches beyond primary-key lookups.
- Current performance indexes cover `users.email` (unique), `records.user_id`, and `ibis.analysis_id`.
- Keep response payloads bounded with pagination.
- Avoid expensive work during FastAPI lifespan startup beyond minimal seed.
- Upload processing is sequential per file and releases memory explicitly after each file to prevent memory exhaustion on large batch uploads.

## Testing

Tests live in `tests/` and run with `pytest` (also in CI before deploy). Files: `test_http_smoke.py` (real ASGI stack), `test_nest_zone_visibility.py` (HTTP + real JWT against a throwaway SQLite file; needs `aiosqlite`, installed by CI: `pip install aiosqlite`), `test_record_client_uuid.py`, `test_record_delete_cascade.py`, `test_session_kinds.py` (session kinds and lifetimes), `test_user_active.py` (deactivated accounts, over real HTTP against SQLite), `test_refresh_cookie.py` (panel cookie sessions over real HTTP against SQLite; the concurrent-rotation lock needs PostgreSQL and is not covered), `test_reliability.py` (publish, requeue, cache races, listener), `test_review_fixes.py`, `test_scale_and_ops.py`, `test_security_hardening.py`. Add new tests there.

## Notes

- PostgreSQL is mandatory; local SQLite fallback was removed.
- PostgreSQL is hosted externally on Supabase for the Docker flow.
- FastAPI startup uses `lifespan`; do not reintroduce deprecated `@app.on_event("startup")`.
- Startup seeds admin user only if `ADMIN_EMAIL` and `ADMIN_PASSWORD` are configured; sample `Record`, `Analysis`, and `Ibis` rows are not created automatically.
- `database.db` was removed and is ignored.
- Local Docker Compose includes RabbitMQ but no database container.
- Routes use separate create/update/read schemas instead of SQLModel table models directly.
- Schema reference as of Alembic head `20261112_0020` (details per revision in Migrations; `refresh_tokens.session_kind` is `mobile|web|web_session`): `analyses` no longer has unused location/flock fields, `analyses.recorder_id` is unique, `analysis_images` stores image-level inference results, `ibis.analysis_image_id` links detections to images, `records.status` exists with existing rows set to `pending`, `records.images` is `varchar[]`, `users.password` is required, `users.email` is unique, all datetime columns are timezone-aware (UTC), `refresh_tokens` table exists for JWT refresh token persistence and atomic rotation, and key lookup indexes exist.
- **Plan Mode**: During planning phases, only generate plans and architecture. Do not output code blocks or file modifications. Wait for explicit implementation command before making any changes.

## Hardening Notes (2026-06)

- `UserRead` (/users/me, login, refresh) exposes `is_admin` so clients can hide admin-only actions.
- Passwords: 8-72 **bytes** on create/update (bcrypt 5 raises above 72). Login accepts shorter legacy passwords and always runs one bcrypt check, even for unknown emails.
- `users.is_admin` (migration `20260607_0015`) gates map-zone writes. Startup promotes the configured `ADMIN_EMAIL` account.
- Upload: the MIME type detected from magic bytes (not the client header) is stored and decides the extension. Files stored for a request that fails are deleted from Storage. `records.images` must be URLs under `users/{user_id}/records/` of the configured bucket (checked when `SUPABASE_URL` is set); `status` cannot be set by clients.
- CI runs pytest before the EC2 deploy job.
- Reliability: publishing runs in a thread (`asyncio.to_thread`) with publisher confirms, so a slow broker no longer freezes the event loop. `records.queued_at` (migration `20260608_0016`) records the last hand-off to the queue; `src/requeue.py` re-publishes records still `pending` after `REQUEUE_AFTER_SECONDS` (default 600), sweeping every `REQUEUE_INTERVAL_SECONDS` (default 60, `<= 0` disables). Duplicate messages are harmless: the worker holds an advisory lock and only accepts non-completed records.
- The progress LISTEN connection now starts with the API (not on first websocket), reconnects on its own, and invalidates the user's HTTP caches on every worker NOTIFY. It needs a direct (session-mode) Postgres connection; Supabase's transaction pooler (port 6543) does not support LISTEN. Cached endpoints send `Cache-Control: private, no-cache` and rely on ETag/304.
- Records cache (`src/routes/record.py`, `/records/summary` and `/{id}/detail`) avoids stale writes: each request reads a per-user version token (plus a global one) before querying and stores its result only if no invalidation (`invalidate_records_cache`) happened meanwhile. Otherwise a read that started before the worker committed could cache the old payload for up to `RECORDS_CACHE_TTL_SECONDS` and show `ibis_quantity=0` after `completed`. Keep this check when touching the cache.
- Creating a map zone links existing records with one `INSERT ... SELECT` (haversine in SQL, `link_records_to_zone_statement`), not by loading every record into the API.
- Rate limits live in `src/ratelimit.py` (sliding window, per process): login per IP (5/min) and per email (10/10 min), refresh per IP (30/min); 429 carries `Retry-After`. Counters are per process, so move to Redis before running several API instances.
- RabbitMQ is pinned to `rabbitmq:3.13-management`. Deploy uses the `EC2_KNOWN_HOSTS` secret (a `known_hosts` line for the server) to pin the SSH host key; without it the workflow warns and falls back to `ssh-keyscan`. The env file copied to `/tmp` on the server is deleted after install.
- JWT uses PyJWT (python-jose was dropped; tokens it issued stay valid, same HS256 format). `pip-audit` runs in CI as a non-blocking `audit` job; keep `requirements.txt` at "No known vulnerabilities".
- The Dockerfile is multi-stage (compilers only in the build stage) with a `/health` HEALTHCHECK. Compose sets memory caps (api 512m, worker 512m, rabbitmq 1g, identifier 4g) and a healthcheck for the identifier; measured idle use on the server is ~0.8 GB for the identifier and well under 0.3 GB for the rest, so raise a cap if a container is OOM-killed (`docker inspect -f '{{.State.OOMKilled}}'`).
- `BodyLimitMiddleware` answers 413 for oversized bodies (declared or streamed). FastAPI turns exceptions raised while reading the body into 400, so the middleware discards the app's answer and sends the 413 itself. `tests/test_http_smoke.py` exercises the real ASGI stack; run it after bumping starlette/fastapi/python-multipart.
- The progress listener is watched: `check_listener` pings it every `PROGRESS_LISTENER_PING_SECONDS` (30) with a `PROGRESS_LISTENER_PING_TIMEOUT_SECONDS` (10) timeout and rebuilds it when dead. An idle LISTEN connection can be dropped by the pooler/network without closing the socket, and asyncpg never notices. To check by hand: `select * from pg_stat_activity where query ilike '%LISTEN "record_progress"%'` must return a row. Events missed while it was down are not replayed; clients get a fresh snapshot on reconnecting the websocket.
