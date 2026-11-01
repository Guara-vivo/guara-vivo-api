import asyncio
import json
import logging
import mimetypes
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import delete, func, or_, select, update

from database_local import AsyncSessionLocal
from models_local import IdMapping as IdMappingLocal
from models_local import SyncQueueItem as SyncQueueItemLocal

logger = logging.getLogger(__name__)

MAX_RETRIES = int(os.getenv("SYNC_MAX_RETRIES", "5"))
# How long a remote access token is reused before asking for a new one. Every refresh
# rotates the single-use refresh token, so refreshing on every run is both wasteful and
# risky on a flaky link (a lost response leaves the stored token already revoked).
SESSION_TTL_SECONDS = int(os.getenv("SYNC_SESSION_TTL_SECONDS", "600"))

CREATE_OPERATIONS = ("create_record", "create_record_with_images")

# One sync at a time, shared by the background loop and the manual trigger.
sync_lock = asyncio.Lock()


@dataclass
class SyncState:
    last_sync_at: datetime | None = None
    last_sync_error: str | None = None


state = SyncState()


class SyncInProgress(Exception):
    """Another sync run already holds the lock."""


class SyncError(Exception):
    """An item could not be synced; the message is stored on the queue item."""


class SyncDeferred(Exception):
    """An item depends on another one that is not synced yet; retry later."""


class RemoteUnauthorized(Exception):
    """The remote rejected the access token. Not the item's fault, so it is not a failure."""


@dataclass
class SyncResult:
    synced_count: int = 0
    failed_count: int = 0
    deferred_count: int = 0
    errors: list[dict] = field(default_factory=list)


ProgressCallback = Callable[[dict[str, Any]], None]


# ---- per-user remote credentials ---------------------------------------------
# Each local user links their own remote account. Records are only ever synced with
# the credentials of the user who owns them.


def auth_token_path() -> str:
    db_path = os.getenv("LOCAL_DB_PATH", "./data/guaravivo.db")
    return os.path.join(os.path.dirname(db_path) or ".", ".auth_tokens")


def _read_store() -> dict[str, dict]:
    try:
        with open(auth_token_path()) as f:
            users = json.load(f).get("users", {})
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        return {}
    return users if isinstance(users, dict) else {}


def _write_store(users: dict[str, dict]) -> None:
    path = auth_token_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump({"users": users}, f)
    try:
        os.chmod(tmp, 0o600)  # no-op on Windows; the data directory is the real boundary there
    except OSError:
        pass
    os.replace(tmp, path)


def save_refresh_token(local_user_id: int, refresh_token: str) -> None:
    users = _read_store()
    users[str(local_user_id)] = {
        "refresh_token": refresh_token,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_store(users)
    _sessions.pop(local_user_id, None)


def load_refresh_token(local_user_id: int) -> str | None:
    return (_read_store().get(str(local_user_id)) or {}).get("refresh_token")


@dataclass
class RemoteSession:
    access_token: str
    refresh_token: str
    remote_user_id: int
    obtained_at: float

    @property
    def fresh(self) -> bool:
        return time.monotonic() - self.obtained_at < SESSION_TTL_SECONDS


# Shared across runs and engine instances on purpose: this is what avoids a refresh
# (and a refresh-token rotation) on every sync run.
_sessions: dict[int, RemoteSession] = {}


# ---- queue helpers -------------------------------------------------------------


def owner_expr():
    """SQL expression for the local user who owns a queue item (stored in its payload)."""
    return func.json_extract(SyncQueueItemLocal.payload, "$.user_id")


def item_owner(item: SyncQueueItemLocal) -> int | None:
    try:
        owner = json.loads(item.payload).get("user_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return owner if isinstance(owner, int) else None


def syncable_filter():
    """Queue items that still need work: pending, or failed with retries left."""
    return or_(
        SyncQueueItemLocal.status == "pending",
        (SyncQueueItemLocal.status == "failed") & (SyncQueueItemLocal.retry_count < MAX_RETRIES),
    )


async def count_syncable(user_id: int | None = None) -> int:
    query = select(func.count()).select_from(SyncQueueItemLocal).where(syncable_filter())
    if user_id is not None:
        query = query.where(owner_expr() == user_id)
    async with AsyncSessionLocal() as db:
        return (await db.execute(query)).scalar() or 0


async def reset_failed(user_id: int) -> int:
    """Give failed items that ran out of retries another chance (explicit user action)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(SyncQueueItemLocal)
            .where(SyncQueueItemLocal.status == "failed", owner_expr() == user_id)
            .values(retry_count=0)
        )
        await db.commit()
        return result.rowcount or 0


def _check(resp: httpx.Response, action: str, ok: tuple[int, ...] = (200, 201)) -> None:
    if resp.status_code == 401:
        raise RemoteUnauthorized()
    if resp.status_code not in ok:
        raise SyncError(f"{action} -> HTTP {resp.status_code}: {resp.text[:200]}")


class SyncEngine:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        # The remote URL is server configuration only; it is never taken from a request.
        self.api_base_url: str = os.getenv("REMOTE_API_URL", "").rstrip("/")
        self._transport = transport

    def _client(self, timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    @staticmethod
    def _headers(session: RemoteSession) -> dict[str, str]:
        return {"Authorization": f"Bearer {session.access_token}"}

    async def check_remote_health(self) -> bool:
        if not self.api_base_url:
            return False
        try:
            async with self._client(5.0) as client:
                resp = await client.get(f"{self.api_base_url}/health")
            return resp.status_code == 200
        except Exception:
            return False

    async def _session_for(self, local_user_id: int) -> RemoteSession | None:
        """A usable remote session for this local user, or None if their account is not
        linked (or the link expired). Network errors propagate as httpx.TransportError."""
        cached = _sessions.get(local_user_id)
        if cached and cached.fresh:
            return cached

        refresh_token = cached.refresh_token if cached else load_refresh_token(local_user_id)
        if not refresh_token or not self.api_base_url:
            return None

        async with self._client(10.0) as client:
            resp = await client.post(f"{self.api_base_url}/users/refresh", json={"refresh_token": refresh_token})
        if resp.status_code != 200:
            _sessions.pop(local_user_id, None)
            return None

        data = resp.json()
        session = RemoteSession(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            remote_user_id=data["user"]["id"],
            obtained_at=time.monotonic(),
        )
        # Persist the rotated token before anything else can fail.
        save_refresh_token(local_user_id, session.refresh_token)
        _sessions[local_user_id] = session
        return session

    # ---- main loop ------------------------------------------------------

    async def sync_all(
        self,
        progress_callback: ProgressCallback | None = None,
        *,
        user_id: int | None = None,
    ) -> SyncResult:
        """Sync queued items. ``user_id`` limits the run to one user's items."""
        if sync_lock.locked():
            raise SyncInProgress()
        # No await between the check and the acquire, so two callers cannot both pass.
        async with sync_lock:
            result = await self._sync_all(progress_callback, user_id)

        state.last_sync_at = datetime.now(timezone.utc)
        # Shared state is visible to every user, so keep it free of remote error bodies;
        # the details are on each user's own queue items.
        if result.failed_count:
            state.last_sync_error = f"{result.failed_count} item(s) failed"
        elif result.errors:
            state.last_sync_error = result.errors[0]["error"]
        else:
            state.last_sync_error = None
        return result

    async def _sync_all(self, progress_callback: ProgressCallback | None, only_user: int | None) -> SyncResult:
        result = SyncResult()
        sessions: dict[int | None, RemoteSession | None] = {}
        unreported: set[int | None] = set()
        touched_users: set[int] = set()

        query = (
            select(SyncQueueItemLocal)
            .where(syncable_filter())
            .order_by(SyncQueueItemLocal.created_at.asc(), SyncQueueItemLocal.id.asc())
        )
        if only_user is not None:
            query = query.where(owner_expr() == only_user)

        async with AsyncSessionLocal() as db:
            items = list((await db.execute(query)).scalars().all())

            for item in items:
                owner = item_owner(item)
                if owner not in sessions:
                    try:
                        sessions[owner] = await self._session_for(owner) if owner is not None else None
                    except httpx.TransportError as exc:
                        result.errors.append({"error": f"Remote server unreachable: {type(exc).__name__}"})
                        break
                session = sessions[owner]

                if session is None:
                    # Not this item's fault: the owner has not linked a remote account, or the
                    # link expired. Leave it pending until they call /offline/configure.
                    result.deferred_count += 1
                    if owner not in unreported:
                        unreported.add(owner)
                        result.errors.append(
                            {
                                "user_id": owner,
                                "error": "Remote account not linked or expired; call /offline/configure",
                            }
                        )
                    continue

                try:
                    await self._sync_item(db, item, session)
                    item.status = "completed"
                    item.error = None
                    item.synced_at = datetime.now(timezone.utc)
                    result.synced_count += 1
                    touched_users.add(owner)
                except SyncDeferred:
                    result.deferred_count += 1
                except RemoteUnauthorized:
                    # Token rejected: drop the session; the next run refreshes it. No retry is spent.
                    _sessions.pop(owner, None)
                    sessions[owner] = None
                    result.deferred_count += 1
                except httpx.TransportError as exc:
                    # The link dropped. This says nothing about the item, so do not spend a retry,
                    # and stop: every remaining item would fail the same way.
                    item.error = f"Remote server unreachable: {type(exc).__name__}"
                    await db.commit()
                    result.errors.append({"error": item.error})
                    break
                except Exception as exc:
                    # Nothing is added to the session until the remote call succeeded,
                    # so there is no partial state to roll back.
                    item.status = "failed"
                    item.error = str(exc)[:500]
                    item.retry_count = (item.retry_count or 0) + 1
                    result.failed_count += 1
                    result.errors.append({"user_id": owner, "local_record_id": item.local_record_id, "error": str(exc)})
                await db.commit()

                if progress_callback:
                    progress_callback(
                        {
                            "synced": result.synced_count,
                            "failed": result.failed_count,
                            "total": len(items),
                            "local_record_id": item.local_record_id,
                            "remote_record_id": item.remote_record_id,
                        }
                    )

        if touched_users:
            from routes.record import invalidate_records_cache

            for user_id in touched_users:
                invalidate_records_cache(user_id)
        return result

    async def _sync_item(self, db, item: SyncQueueItemLocal, session: RemoteSession) -> None:
        if item.operation in CREATE_OPERATIONS:
            await self._sync_create_record(db, item, session)
        elif item.operation == "update_record":
            await self._sync_update_record(db, item, session)
        elif item.operation == "delete_record":
            await self._sync_delete_record(db, item, session)
        else:
            raise SyncError(f"Unknown operation {item.operation!r}")

    # ---- helpers --------------------------------------------------------

    async def _remote_record_id(self, db, local_id: int) -> int | None:
        row = await db.execute(
            select(IdMappingLocal.remote_id).where(
                IdMappingLocal.local_id == local_id,
                IdMappingLocal.entity_type == "record",
            )
        )
        return row.scalars().first()

    async def _require_remote_record_id(self, db, item: SyncQueueItemLocal) -> int:
        remote_id = await self._remote_record_id(db, item.local_record_id)
        if remote_id is not None:
            return remote_id
        # The create may simply not have run yet, or failed and still have retries left.
        waiting = await db.execute(
            select(func.count())
            .select_from(SyncQueueItemLocal)
            .where(
                SyncQueueItemLocal.local_record_id == item.local_record_id,
                SyncQueueItemLocal.operation.in_(CREATE_OPERATIONS),
                syncable_filter(),
            )
        )
        if (waiting.scalar() or 0) > 0:
            raise SyncDeferred()
        raise SyncError(f"Record {item.local_record_id} was never created remotely")

    def _record_mapping(self, db, item: SyncQueueItemLocal, remote_id: int) -> None:
        item.remote_record_id = remote_id
        db.add(
            IdMappingLocal(
                local_id=item.local_record_id,
                remote_id=remote_id,
                entity_type="record",
                synced_at=datetime.now(timezone.utc),
            )
        )

    # ---- operations -----------------------------------------------------

    async def _sync_create_record(self, db, item: SyncQueueItemLocal, session: RemoteSession) -> None:
        existing = await self._remote_record_id(db, item.local_record_id)
        if existing is not None:
            # Already created remotely by a previous run that failed before committing.
            item.remote_record_id = existing
            return

        payload = json.loads(item.payload)
        behavior = payload.get("behavior", [])

        if item.image_local_paths:
            from storage_local import get_image_absolute_path

            files = []
            for local_path in json.loads(item.image_local_paths):
                abs_path = get_image_absolute_path(local_path)
                if abs_path is None:
                    raise SyncError(f"Local image not found: {local_path}")
                content_type = mimetypes.guess_type(abs_path.name)[0] or "image/jpeg"
                files.append(("images", (abs_path.name, abs_path.read_bytes(), content_type)))

            async with self._client() as client:
                resp = await client.post(
                    f"{self.api_base_url}/records/upload",
                    headers=self._headers(session),
                    data={
                        "latitude_camera": payload["latitude_camera"],
                        "longitude_camera": payload["longitude_camera"],
                        "behavior": json.dumps(behavior),
                        "date_time": payload["date_time"],
                    },
                    files=files,
                )
            _check(resp, "POST /records/upload")
            self._record_mapping(db, item, resp.json()["id"])
            return

        images = payload.get("images") or []
        if not images:
            raise SyncError("Record has no images to sync")
        body = {
            "images": images,
            "latitude_camera": payload["latitude_camera"],
            "longitude_camera": payload["longitude_camera"],
            "behavior": behavior,
            "date_time": payload["date_time"],
            "user_id": session.remote_user_id,  # local user ids mean nothing remotely
        }
        async with self._client() as client:
            resp = await client.post(f"{self.api_base_url}/records/", headers=self._headers(session), json=body)
        _check(resp, "POST /records/")
        self._record_mapping(db, item, resp.json()["id"])

    async def _sync_update_record(self, db, item: SyncQueueItemLocal, session: RemoteSession) -> None:
        remote_id = await self._require_remote_record_id(db, item)
        payload = json.loads(item.payload)
        async with self._client() as client:
            current = await client.get(f"{self.api_base_url}/records/{remote_id}", headers=self._headers(session))
            _check(current, f"GET /records/{remote_id}")
            remote = current.json()
            # Local image paths and the local pending status are meaningless remotely,
            # so keep what the remote record already has.
            body = {
                "images": remote["images"],
                "latitude_camera": payload["latitude_camera"],
                "longitude_camera": payload["longitude_camera"],
                "behavior": payload["behavior"],
                "date_time": payload["date_time"],
                "user_id": session.remote_user_id,
                "status": remote["status"],
            }
            resp = await client.put(
                f"{self.api_base_url}/records/{remote_id}", headers=self._headers(session), json=body
            )
        _check(resp, f"PUT /records/{remote_id}")
        item.remote_record_id = remote_id

    async def _sync_delete_record(self, db, item: SyncQueueItemLocal, session: RemoteSession) -> None:
        remote_id = await self._require_remote_record_id(db, item)
        async with self._client() as client:
            resp = await client.delete(
                f"{self.api_base_url}/records/{remote_id}", headers=self._headers(session)
            )
        _check(resp, f"DELETE /records/{remote_id}", ok=(200, 204, 404))
        item.remote_record_id = remote_id
        # The record is gone on both sides; drop its mappings so they cannot go stale.
        await db.execute(delete(IdMappingLocal).where(IdMappingLocal.local_id == item.local_record_id))
