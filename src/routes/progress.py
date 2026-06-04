import asyncio
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import asyncpg
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from database import DEFAULT_DATABASE_URL, AsyncSessionLocal
from models import Record
from security import get_user_from_access_token


router = APIRouter(prefix="/records/progress", tags=["record-progress"])
NOTIFY_CHANNEL = os.getenv("PROGRESS_NOTIFY_CHANNEL", "record_progress")
HEARTBEAT_SECONDS = int(os.getenv("WEBSOCKET_HEARTBEAT_SECONDS", "25"))
AUTH_TIMEOUT_SECONDS = int(os.getenv("WEBSOCKET_AUTH_TIMEOUT_SECONDS", "5"))
CLIENT_QUEUE_MAX_SIZE = int(os.getenv("WEBSOCKET_CLIENT_QUEUE_MAX_SIZE", "100"))
POSTGRES_RECONNECT_SECONDS = int(os.getenv("WEBSOCKET_POSTGRES_RECONNECT_SECONDS", "5"))
RECORD_STATUSES = {"pending", "processing", "completed", "failed"}


ProgressEvent = dict[str, int | str]


@dataclass(eq=False)
class ProgressClient:
    user_id: int
    websocket: WebSocket
    queue: asyncio.Queue[ProgressEvent] = field(
        default_factory=lambda: asyncio.Queue(maxsize=CLIENT_QUEUE_MAX_SIZE)
    )
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(payload)

    def enqueue(self, event: ProgressEvent) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


class ProgressConnectionManager:
    def __init__(self) -> None:
        self._connection: asyncpg.Connection | None = None
        self._clients_by_user: dict[int, set[ProgressClient]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task | None = None

    async def ensure_listener(self) -> None:
        async with self._lock:
            if self._connection is not None and not self._connection.is_closed():
                return

            connection = await asyncpg.connect(DEFAULT_DATABASE_URL)
            await connection.add_listener(NOTIFY_CHANNEL, self._listener)
            connection.add_termination_listener(self._handle_termination)
            self._connection = connection

    async def add_client(self, client: ProgressClient) -> None:
        await self.ensure_listener()

        async with self._lock:
            self._clients_by_user[client.user_id].add(client)

    async def remove_client(self, client: ProgressClient) -> None:
        async with self._lock:
            clients = self._clients_by_user.get(client.user_id)
            if not clients:
                return

            clients.discard(client)

            if not clients:
                self._clients_by_user.pop(client.user_id, None)

    async def close(self) -> None:
        async with self._lock:
            connection = self._connection
            reconnect_task = self._reconnect_task
            self._connection = None
            self._reconnect_task = None
            self._clients_by_user.clear()

        if reconnect_task is not None:
            reconnect_task.cancel()

        if connection is not None and not connection.is_closed():
            await connection.close()

    def _listener(self, _connection, _pid, _channel, payload: str) -> None:
        event = parse_progress_event(payload)

        if event is None:
            return

        asyncio.create_task(self._broadcast(event))

    def _handle_termination(self, _connection) -> None:
        self._connection = None

        if self._clients_by_user and self._reconnect_task is None:
            self._reconnect_task = asyncio.create_task(self._reconnect_until_connected())

    async def _reconnect_until_connected(self) -> None:
        try:
            while self._clients_by_user:
                try:
                    await self.ensure_listener()
                    return
                except Exception:
                    await asyncio.sleep(POSTGRES_RECONNECT_SECONDS)
        finally:
            self._reconnect_task = None

    async def _broadcast(self, event: ProgressEvent) -> None:
        user_id = event["user_id"]

        async with self._lock:
            clients = list(self._clients_by_user.get(user_id, set()))

        for client in clients:
            client.enqueue(event)


progress_manager = ProgressConnectionManager()


def serialize_progress_record(record: Record) -> ProgressEvent:
    return {
        "id": record.id,
        "status": record.status,
        "analysis_progress": record.analysis_progress,
    }


def parse_progress_event(payload: str) -> ProgressEvent | None:
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return None

    if not isinstance(event, dict):
        return None

    record_id = event.get("record_id")
    user_id = event.get("user_id")
    event_status = event.get("status")
    analysis_progress = event.get("analysis_progress")

    if not isinstance(record_id, int):
        return None
    if not isinstance(user_id, int):
        return None
    if event_status not in RECORD_STATUSES:
        return None
    if not isinstance(analysis_progress, int):
        return None
    if analysis_progress < 0 or analysis_progress > 100:
        return None

    return {
        "record_id": record_id,
        "user_id": user_id,
        "status": event_status,
        "analysis_progress": analysis_progress,
    }


async def get_user_records_snapshot(user_id: int) -> list[ProgressEvent]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Record)
            .where(Record.user_id == user_id)
            .order_by(Record.id)
        )
        return [serialize_progress_record(record) for record in result.scalars().all()]


async def authenticate_websocket_token(token: str | None):
    if not token:
        return None

    async with AsyncSessionLocal() as db:
        try:
            return await get_user_from_access_token(token, db)
        except Exception:
            return None


async def authenticate_websocket(websocket: WebSocket):
    try:
        message = await asyncio.wait_for(
            websocket.receive_json(),
            timeout=AUTH_TIMEOUT_SECONDS,
        )
    except (TimeoutError, ValueError, WebSocketDisconnect):
        return None

    if not isinstance(message, dict):
        return None

    if message.get("type") != "auth":
        return None

    token = message.get("token")

    if not isinstance(token, str):
        return None

    return await authenticate_websocket_token(token)


async def wait_for_disconnect(websocket: WebSocket) -> None:
    while True:
        await websocket.receive_text()


@router.websocket("/ws")
async def record_progress_websocket(websocket: WebSocket):
    await websocket.accept()
    current_user = await authenticate_websocket(websocket)

    if current_user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    client = ProgressClient(user_id=current_user.id, websocket=websocket)

    try:
        await progress_manager.add_client(client)
    except Exception:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    disconnect_task: asyncio.Task | None = None
    try:
        await client.send_json(
            {
                "type": "snapshot",
                "records": await get_user_records_snapshot(current_user.id),
            }
        )

        disconnect_task = asyncio.create_task(wait_for_disconnect(websocket))

        while True:
            event_task = asyncio.create_task(client.queue.get())
            heartbeat_task = asyncio.create_task(asyncio.sleep(HEARTBEAT_SECONDS))
            done, pending = await asyncio.wait(
                {event_task, disconnect_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                if task is not disconnect_task:
                    task.cancel()

            if disconnect_task in done:
                await disconnect_task
                raise WebSocketDisconnect

            if heartbeat_task in done:
                await client.send_json({"type": "heartbeat"})
                continue

            event = event_task.result()
            await client.send_json(
                {
                    "type": "progress",
                    "record": {
                        "id": event["record_id"],
                        "status": event["status"],
                        "analysis_progress": event["analysis_progress"],
                    },
                }
            )
    except WebSocketDisconnect:
        pass
    finally:
        if disconnect_task is not None:
            disconnect_task.cancel()
        await progress_manager.remove_client(client)


async def close_progress_manager() -> None:
    await progress_manager.close()
