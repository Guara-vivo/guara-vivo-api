import os
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from database import OFFLINE_MODE
from database_local import AsyncSessionLocal as LocalSession
from models import User
from models_local import SyncQueueItem as SyncQueueItemLocal
from schemas import (
    OfflineConfigureRequest,
    OfflineConfigureResponse,
    OfflineStatus,
    SyncHistoryItem,
    SyncHistoryResponse,
)
from security import get_current_user
from sync import sync_background, sync_engine
from sync.sync_engine import SyncEngine, SyncInProgress

# Every route here requires a logged-in user: they expose sync state and, for
# /configure, store credentials used to talk to the remote server.
router = APIRouter(prefix="/offline", tags=["offline"], dependencies=[Depends(get_current_user)])


def _require_offline_mode() -> None:
    if not OFFLINE_MODE:
        raise HTTPException(status_code=400, detail="Not in offline mode")


@router.get("/status", response_model=OfflineStatus)
async def offline_status(current_user: User = Depends(get_current_user)):
    if not OFFLINE_MODE:
        return OfflineStatus(offline_mode=False, remote_connected=True, pending_sync=0, syncing=False)

    last_sync_at, last_sync_error = sync_background.get_last_sync()
    return OfflineStatus(
        offline_mode=True,
        remote_connected=sync_background.get_connected(),
        pending_sync=await sync_engine.count_syncable(current_user.id),
        syncing=sync_background.get_syncing(),
        last_sync_at=last_sync_at,
        last_sync_error=last_sync_error,
    )


@router.post("/sync/trigger")
async def sync_trigger(current_user: User = Depends(get_current_user)):
    """Sync the caller's own queue. Asking explicitly also retries items that ran out of retries."""
    _require_offline_mode()

    remote_api = os.getenv("REMOTE_API_URL", "").rstrip("/")
    if not remote_api:
        raise HTTPException(status_code=503, detail="REMOTE_API_URL not configured")

    engine = SyncEngine()
    if not await engine.check_remote_health():
        raise HTTPException(status_code=503, detail="No connection to remote server")

    await sync_engine.reset_failed(current_user.id)
    try:
        result = await engine.sync_all(user_id=current_user.id)
    except SyncInProgress:
        raise HTTPException(status_code=409, detail="Sync already in progress")
    except Exception as exc:
        sync_engine.state.last_sync_error = str(exc)
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}")

    return {
        "status": "completed",
        "synced": result.synced_count,
        "failed": result.failed_count,
        "deferred": result.deferred_count,
        "errors": result.errors,
    }


@router.get("/sync/history", response_model=SyncHistoryResponse)
async def sync_history(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    if not OFFLINE_MODE:
        return SyncHistoryResponse(items=[], total=0)

    # Queue rows carry the owner only inside the JSON payload.
    owned = func.json_extract(SyncQueueItemLocal.payload, "$.user_id") == current_user.id
    async with LocalSession() as db:
        total = (await db.execute(select(func.count()).select_from(SyncQueueItemLocal).where(owned))).scalar() or 0
        result = await db.execute(
            select(SyncQueueItemLocal)
            .where(owned)
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
async def offline_configure(
    payload: OfflineConfigureRequest,
    current_user: User = Depends(get_current_user),
):
    """Link the caller's own remote account. Each local user syncs only with their own credentials."""
    _require_offline_mode()

    # The remote server is fixed by server configuration. Taking it from the request
    # would let a caller make this API forward credentials and records elsewhere.
    remote_api = os.getenv("REMOTE_API_URL", "").rstrip("/")
    if not remote_api:
        raise HTTPException(status_code=503, detail="REMOTE_API_URL not configured")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{remote_api}/users/login",
                json={"email": payload.email, "password": payload.password},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach remote server: {exc}")

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Remote authentication failed")

    refresh_token = resp.json().get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=502, detail="Remote did not return refresh token")

    sync_engine.save_refresh_token(current_user.id, refresh_token)

    return OfflineConfigureResponse(status="authenticated", expires_at=datetime.now(timezone.utc))
