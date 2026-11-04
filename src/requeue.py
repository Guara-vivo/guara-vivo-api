"""Re-queues records that stayed `pending` after their queue message was lost."""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from database import AsyncSessionLocal
from models import Record
from rabbitmq import publish_record_for_inference

logger = logging.getLogger(__name__)

REQUEUE_INTERVAL_SECONDS = int(os.getenv("REQUEUE_INTERVAL_SECONDS", "60"))  # <= 0 disables
REQUEUE_AFTER_SECONDS = int(os.getenv("REQUEUE_AFTER_SECONDS", "600"))
REQUEUE_BATCH_SIZE = int(os.getenv("REQUEUE_BATCH_SIZE", "20"))


async def requeue_next_stale_record(db, publish=publish_record_for_inference) -> bool:
    """Re-queue one stale pending record. Returns False when there is nothing to do.

    The row stays locked (SKIP LOCKED, so several API instances never pick the same one)
    until `queued_at` is refreshed, so a record is not published twice in the same window.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=REQUEUE_AFTER_SECONDS)
    result = await db.execute(
        select(Record)
        .where(Record.status == "pending", Record.queued_at < cutoff)
        .order_by(Record.queued_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    record = result.scalar_one_or_none()
    if record is None:
        return False

    try:
        await asyncio.to_thread(publish, record.id)
    except Exception:
        logger.exception("could not re-queue record %s", record.id)
        # Retry in the next window instead of every tick.
        record.queued_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    record.queued_at = datetime.now(timezone.utc)
    await db.commit()
    logger.warning("re-queued stale pending record %s", record.id)
    return True


async def requeue_stale_records() -> int:
    count = 0
    for _ in range(REQUEUE_BATCH_SIZE):
        async with AsyncSessionLocal() as db:
            if not await requeue_next_stale_record(db):
                break
        count += 1
    return count


async def run_requeue_loop() -> None:
    if REQUEUE_INTERVAL_SECONDS <= 0:
        return
    while True:
        try:
            await requeue_stale_records()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("requeue sweep failed")
        await asyncio.sleep(REQUEUE_INTERVAL_SECONDS)
