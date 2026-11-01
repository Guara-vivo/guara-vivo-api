import asyncio
import json
import logging
import os
from datetime import datetime

import httpx

from sync import sync_engine
from sync.sync_engine import SyncEngine, SyncInProgress

logger = logging.getLogger(__name__)

SYNC_CHECK_INTERVAL = int(os.getenv("SYNC_CHECK_INTERVAL_SECONDS", "30"))

_connected = False
_ws_clients: set = set()
_task: asyncio.Task | None = None


def get_connected() -> bool:
    return _connected


def get_syncing() -> bool:
    return sync_engine.sync_lock.locked()


def get_last_sync() -> tuple[datetime | None, str | None]:
    return sync_engine.state.last_sync_at, sync_engine.state.last_sync_error


def add_ws_client(websocket) -> None:
    _ws_clients.add(websocket)


def remove_ws_client(websocket) -> None:
    _ws_clients.discard(websocket)


async def _notify_ws(event_type: str, data: dict) -> None:
    payload = json.dumps({"type": event_type, **data})
    disconnected = set()
    # Iterate a copy: clients connect and disconnect while we await the sends.
    for ws in list(_ws_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        _ws_clients.discard(ws)


async def _safe_notify(event_type: str, data: dict) -> None:
    try:
        await _notify_ws(event_type, data)
    except Exception:
        logger.exception("Could not notify websocket clients")


async def _sync_pending() -> None:
    """Sync whatever is still pending (or failed with retries left)."""
    if sync_engine.sync_lock.locked() or await sync_engine.count_syncable() == 0:
        return

    logger.info("Starting auto-sync")
    try:
        result = await SyncEngine().sync_all()
    except SyncInProgress:
        return
    except Exception as exc:
        sync_engine.state.last_sync_error = "Sync failed"
        logger.error("Auto-sync failed: %s", exc)
        # Broadcast to every connected user, so no exception text.
        await _safe_notify("sync_error", {"error": "Sync failed"})
        return

    if result.failed_count:
        logger.warning("Auto-sync completed with %d failures", result.failed_count)
    await _safe_notify("sync_completed", {"synced": result.synced_count, "failed": result.failed_count})


async def _connectivity_loop() -> None:
    global _connected

    remote_api = os.getenv("REMOTE_API_URL", "").rstrip("/")
    if not remote_api:
        logger.warning("REMOTE_API_URL not set, connectivity check disabled")
        return

    while True:
        was_connected = _connected
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{remote_api}/health")
            _connected = resp.status_code == 200
        except Exception:
            _connected = False

        if _connected != was_connected:
            logger.info("Remote server connection %s", "restored" if _connected else "lost")
            await _safe_notify("connection_changed", {"connected": _connected})

        # Sync on every tick while connected, not only on reconnect, so items that
        # failed or were created while already online are still picked up.
        if _connected:
            try:
                await _sync_pending()
            except Exception:
                logger.exception("Unexpected error during auto-sync")

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
