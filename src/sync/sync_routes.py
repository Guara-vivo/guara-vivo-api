import json
import os
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import OFFLINE_MODE, get_db
from database_local import AsyncSessionLocal as LocalSession
from models_local import SyncQueueItem as SyncQueueItemLocal
from models_local import IdMapping as IdMappingLocal
from schemas import (
    OfflineConfigureRequest,
    OfflineConfigureResponse,
    OfflineStatus,
    SyncHistoryItem,
    SyncHistoryResponse,
)
from sync.sync_engine import SyncEngine


router = APIRouter(prefix="/offline", tags=["offline"])

_syncing_in_progress = False
_remote_connected = False
_last_sync_at: datetime | None = None
_last_sync_error: str | None = None


def get_offline_status() -> OfflineStatus:
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        pending = asyncio.run_coroutine_threadsafe(_count_pending(), loop).result(timeout=5)
    except Exception:
        pending = 0
    return OfflineStatus(
        offline_mode=True,
        remote_connected=_remote_connected,
        pending_sync=pending,
        syncing=_syncing_in_progress,
        last_sync_at=_last_sync_at,
        last_sync_error=_last_sync_error,
    )


async def _count_pending() -> int:
    async with LocalSession() as db:
        result = await db.execute(
            select(func.count()).select_from(SyncQueueItemLocal).where(SyncQueueItemLocal.status == "pending")
        )
        return result.scalar() or 0


@router.get("/status", response_model=OfflineStatus)
async def offline_status():
    if not OFFLINE_MODE:
        return OfflineStatus(
            offline_mode=False,
            remote_connected=True,
            pending_sync=0,
            syncing=False,
        )
    return get_offline_status()


@router.post("/sync/trigger")
async def sync_trigger():
    global _syncing_in_progress, _last_sync_at, _last_sync_error

    if not OFFLINE_MODE:
        raise HTTPException(status_code=400, detail="Not in offline mode")

    if _syncing_in_progress:
        raise HTTPException(status_code=409, detail="Sync already in progress")

    remote_api = os.getenv("REMOTE_API_URL", "")
    if not remote_api:
        raise HTTPException(status_code=503, detail="REMOTE_API_URL not configured")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{remote_api}/health")
            if resp.status_code != 200:
                raise HTTPException(status_code=503, detail="No connection to remote server")
    except Exception:
        raise HTTPException(status_code=503, detail="No connection to remote server")

    _syncing_in_progress = True
    try:
        engine = SyncEngine()
        result = await engine.sync_all()
        _last_sync_at = datetime.now(timezone.utc)
        if result.failed_count > 0:
            _last_sync_error = f"{result.failed_count} item(s) failed"
        else:
            _last_sync_error = None
        return {
            "status": "completed",
            "synced": result.synced_count,
            "failed": result.failed_count,
            "errors": result.errors,
        }
    except Exception as exc:
        _last_sync_error = str(exc)
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}")
    finally:
        _syncing_in_progress = False


@router.get("/sync/history", response_model=SyncHistoryResponse)
async def sync_history(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
):
    if not OFFLINE_MODE:
        return SyncHistoryResponse(items=[], total=0)

    async with LocalSession() as db:
        count_result = await db.execute(
            select(func.count()).select_from(SyncQueueItemLocal)
        )
        total = count_result.scalar() or 0

        result = await db.execute(
            select(SyncQueueItemLocal)
            .order_by(SyncQueueItemLocal.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        items = result.scalars().all()

        return SyncHistoryResponse(
            items=[
                SyncHistoryItem(
                    id=item.id,
                    operation=item.operation,
                    local_record_id=item.local_record_id,
                    remote_record_id=item.remote_record_id,
                    status=item.status,
                    error=item.error,
                    created_at=item.created_at,
                    synced_at=item.synced_at,
                )
                for item in items
            ],
            total=total,
        )


@router.post("/configure", response_model=OfflineConfigureResponse)
async def offline_configure(payload: OfflineConfigureRequest):
    if not OFFLINE_MODE:
        raise HTTPException(status_code=400, detail="Not in offline mode")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{payload.api_base_url}/users/login",
                json={"email": payload.email, "password": payload.password},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=401, detail="Remote authentication failed")

            data = resp.json()
            refresh_token = data.get("refresh_token")
            if not refresh_token:
                raise HTTPException(status_code=502, detail="Remote did not return refresh token")

            auth_file = os.getenv("LOCAL_DB_PATH", "./data/guaravivo.db")
            auth_path = os.path.join(os.path.dirname(auth_file), ".auth_tokens")
            os.makedirs(os.path.dirname(auth_path), exist_ok=True)
            with open(auth_path, "w") as f:
                json.dump({
                    "refresh_token": refresh_token,
                    "api_base_url": payload.api_base_url.rstrip("/"),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }, f)

            return OfflineConfigureResponse(
                status="authenticated",
                expires_at=datetime.now(timezone.utc),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to configure: {exc}")