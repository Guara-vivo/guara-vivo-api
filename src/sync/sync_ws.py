import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from database_local import AsyncSessionLocal
from models_local import SyncQueueItem as SyncQueueItemLocal
from security import get_user_from_access_token
from sync.sync_background import (
    add_ws_client,
    get_connected,
    get_last_sync,
    remove_ws_client,
)
from sqlalchemy import select, func


router = APIRouter(prefix="/offline", tags=["offline"])

AUTH_TIMEOUT = 5
HEARTBEAT_SECONDS = 25


async def _authenticate_ws(websocket: WebSocket):
    try:
        message = await asyncio.wait_for(websocket.receive_json(), timeout=AUTH_TIMEOUT)
    except (TimeoutError, ValueError, WebSocketDisconnect):
        return None

    if not isinstance(message, dict) or message.get("type") != "auth":
        return None

    token = message.get("token")
    if not isinstance(token, str):
        return None

    async with AsyncSessionLocal() as db:
        try:
            return await get_user_from_access_token(token, db)
        except Exception:
            return None


async def _count_pending() -> int:
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(func.count()).select_from(SyncQueueItemLocal).where(SyncQueueItemLocal.status == "pending")
            )
            return result.scalar() or 0
    except Exception:
        return 0


@router.websocket("/ws")
async def offline_ws(websocket: WebSocket):
    await websocket.accept()
    current_user = await _authenticate_ws(websocket)

    if current_user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    add_ws_client(websocket)

    try:
        last_sync_at, last_sync_error = get_last_sync()
        await websocket.send_json({
            "type": "status",
            "connected": get_connected(),
            "pending_sync": await _count_pending(),
            "syncing": False,
            "last_sync_at": last_sync_at.isoformat() if last_sync_at else None,
            "last_sync_error": last_sync_error,
        })

        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=HEARTBEAT_SECONDS)
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat"})
            except (json.JSONDecodeError, ValueError):
                pass

    except WebSocketDisconnect:
        pass
    finally:
        remove_ws_client(websocket)