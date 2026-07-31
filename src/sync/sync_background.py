import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import httpx

from sync.sync_engine import SyncEngine

logger = logging.getLogger(__name__)

SYNC_CHECK_INTERVAL = int(os.getenv("SYNC_CHECK_INTERVAL_SECONDS", "30"))

_connected = False
_syncing = False
_last_sync_at: datetime | None = None
_last_sync_error: str | None = None
_ws_clients: set = set()
_task: asyncio.Task | None = None


def get_connected() -> bool:
    return _connected


def get_syncing() -> bool:
    return _syncing


def get_last_sync() -> tuple[datetime | None, str | None]:
    return _last_sync_at, _last_sync_error


def add_ws_client(websocket) -> None:
    _ws_clients.add(websocket)


def remove_ws_client(websocket) -> None:
    _ws_clients.discard(websocket)


async def _notify_ws(event_type: str, data: dict) -> None:
    payload = json.dumps({"type": event_type, **data})
    disconnected = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        _ws_clients.discard(ws)


async def _connectivity_loop() -> None:
    global _connected, _syncing, _last_sync_at, _last_sync_error

    remote_api = os.getenv("REMOTE_API_URL", "")
    if not remote_api:
        logger.warning("REMOTE_API_URL not set, connectivity check disabled")
        return

    from database_local import AsyncSessionLocal
    from models_local import SyncQueueItem as SyncQueueItemLocal
    from sqlalchemy import select, func

    while True:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{remote_api}/health")
                was_connected = _connected
                _connected = resp.status_code == 200

                if _connected and not was_connected:
                    logger.info("Remote server connection restored")
                    await _notify_ws("connection_changed", {"connected": True})

                    async with AsyncSessionLocal() as db:
                        count_result = await db.execute(
                            select(func.count()).select_from(SyncQueueItemLocal).where(SyncQueueItemLocal.status == "pending")
                        )
                        pending = count_result.scalar() or 0

                    if pending > 0:
                        logger.info("Starting auto-sync for %d pending items", pending)
                        _syncing = True
                        try:
                            engine = SyncEngine()
                            result = await engine.sync_all()
                            _last_sync_at = datetime.now(timezone.utc)
                            if result.failed_count > 0:
                                _last_sync_error = f"{result.failed_count} item(s) failed"
                                logger.warning("Auto-sync completed with %d failures", result.failed_count)
                            else:
                                _last_sync_error = None
                                logger.info("Auto-sync completed successfully")

                            await _notify_ws("sync_completed", {
                                "synced": result.synced_count,
                                "failed": result.failed_count,
                            })
                        except Exception as exc:
                            _last_sync_error = str(exc)
                            _last_sync_at = datetime.now(timezone.utc)
                            logger.error("Auto-sync failed: %s", exc)
                            await _notify_ws("sync_error", {"error": str(exc)})
                        finally:
                            _syncing = False

                elif not _connected and was_connected:
                    logger.info("Remote server connection lost")
                    await _notify_ws("connection_changed", {"connected": False})

        except Exception:
            if _connected:
                _connected = False
                logger.warning("Remote server connection lost (exception)")
                await _notify_ws("connection_changed", {"connected": False})

        await asyncio.sleep(SYNC_CHECK_INTERVAL)


async def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_connectivity_loop())
        logger.info("Connectivity manager started (interval=%ds)", SYNC_CHECK_INTERVAL)


async def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
        logger.info("Connectivity manager stopped")