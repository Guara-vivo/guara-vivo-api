import asyncio
import json

import asyncpg
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from database import DEFAULT_DATABASE_URL, AsyncSessionLocal
from models import Record
from security import get_user_from_access_token


router = APIRouter(prefix="/records/progress", tags=["record-progress"])
NOTIFY_CHANNEL = "record_progress"
HEARTBEAT_SECONDS = 25


def serialize_progress_record(record: Record) -> dict[str, int | str]:
    return {
        "id": record.id,
        "status": record.status,
        "analysis_progress": record.analysis_progress,
    }


async def get_user_records_snapshot(user_id: int) -> list[dict[str, int | str]]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Record)
            .where(Record.user_id == user_id)
            .order_by(Record.id)
        )
        return [serialize_progress_record(record) for record in result.scalars().all()]


async def authenticate_websocket(token: str | None):
    if not token:
        return None

    async with AsyncSessionLocal() as db:
        try:
            return await get_user_from_access_token(token, db)
        except Exception:
            return None


@router.websocket("/ws")
async def record_progress_websocket(websocket: WebSocket):
    token = websocket.query_params.get("token")
    current_user = await authenticate_websocket(token)

    if current_user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    await websocket.send_json(
        {
            "type": "snapshot",
            "records": await get_user_records_snapshot(current_user.id),
        }
    )

    queue: asyncio.Queue[dict] = asyncio.Queue()
    connection = await asyncpg.connect(DEFAULT_DATABASE_URL)

    def listener(_connection, _pid, _channel, payload: str) -> None:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return

        if event.get("user_id") == current_user.id:
            queue.put_nowait(event)

    await connection.add_listener(NOTIFY_CHANNEL, listener)

    disconnect_task = asyncio.create_task(websocket.receive_text())

    try:
        while True:
            event_task = asyncio.create_task(queue.get())
            heartbeat_task = asyncio.create_task(asyncio.sleep(HEARTBEAT_SECONDS))
            done, pending = await asyncio.wait(
                {event_task, disconnect_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                if task is not disconnect_task:
                    task.cancel()

            if disconnect_task in done:
                raise WebSocketDisconnect

            if heartbeat_task in done:
                await websocket.send_json({"type": "heartbeat"})
                continue

            event = event_task.result()
            await websocket.send_json(
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
        disconnect_task.cancel()
        await connection.remove_listener(NOTIFY_CHANNEL, listener)
        await connection.close()
