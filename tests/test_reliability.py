import asyncio
import os
import sys
import threading
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)


class FakeDb:
    def __init__(self, record=None):
        self.record = record
        self.commits = 0

    async def execute(self, _query):
        record = self.record
        return SimpleNamespace(scalar_one_or_none=lambda: record)

    async def commit(self):
        self.commits += 1


class PublishTests(unittest.TestCase):
    def test_publish_runs_off_the_event_loop_and_stamps_queued_at(self):
        import routes.record as routes

        seen = {}

        def fake_publish(record_id):
            seen["thread"] = threading.get_ident()

        original = routes.publish_record_for_inference
        routes.publish_record_for_inference = fake_publish
        self.addCleanup(setattr, routes, "publish_record_for_inference", original)

        record = SimpleNamespace(id=1, user_id=1, queued_at=None, status="pending")
        db = FakeDb()

        async def run():
            main_thread = threading.get_ident()
            await routes.publish_or_mark_failed(db, record)
            return main_thread

        main_thread = asyncio.run(run())
        self.assertNotEqual(seen["thread"], main_thread)
        self.assertIsNotNone(record.queued_at)
        self.assertEqual(db.commits, 1)

    def test_broker_uses_publisher_confirms(self):
        import rabbitmq

        calls = []

        class Channel:
            def confirm_delivery(self):
                calls.append("confirm")

            def queue_declare(self, **_kw):
                calls.append("declare")

            def basic_publish(self, **kw):
                calls.append(("publish", kw["mandatory"]))

        class Connection:
            def channel(self):
                return Channel()

            def close(self):
                calls.append("close")

        original = rabbitmq.get_connection
        rabbitmq.get_connection = lambda: Connection()
        self.addCleanup(setattr, rabbitmq, "get_connection", original)

        rabbitmq.publish_record_for_inference(5)
        self.assertLess(calls.index("confirm"), calls.index("declare"))
        self.assertIn(("publish", True), calls)


class RequeueTests(unittest.TestCase):
    def test_nothing_to_requeue(self):
        import requeue

        published = []
        self.assertFalse(asyncio.run(requeue.requeue_next_stale_record(FakeDb(None), published.append)))
        self.assertEqual(published, [])

    def test_stale_record_is_published_and_stamped(self):
        import requeue

        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        record = SimpleNamespace(id=9, queued_at=old)
        db = FakeDb(record)
        published = []

        self.assertTrue(asyncio.run(requeue.requeue_next_stale_record(db, published.append)))
        self.assertEqual(published, [9])
        self.assertGreater(record.queued_at, old)
        self.assertEqual(db.commits, 1)

    def test_publish_failure_is_swallowed_and_retried_next_window(self):
        import requeue

        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        record = SimpleNamespace(id=9, queued_at=old)
        db = FakeDb(record)

        def broken(_record_id):
            raise ConnectionError("broker down")

        self.assertTrue(asyncio.run(requeue.requeue_next_stale_record(db, broken)))
        self.assertGreater(record.queued_at, old)
        self.assertEqual(db.commits, 1)


class ProgressTests(unittest.TestCase):
    def test_asyncpg_dsn_strips_sqlalchemy_driver_suffix(self):
        from routes.progress import asyncpg_dsn

        self.assertEqual(asyncpg_dsn("postgresql+psycopg2://u:p@h/db"), "postgresql://u:p@h/db")
        self.assertEqual(asyncpg_dsn("postgres://u:p@h/db"), "postgres://u:p@h/db")

    def test_worker_notification_invalidates_the_users_cache(self):
        import routes.record as records
        from routes.progress import ProgressConnectionManager

        records._records_cache.clear()
        records._records_cache["records:7:summary:0:100"] = (0, '"e"', [])
        records._records_cache["records:7:detail:3"] = (0, '"e"', {})
        records._records_cache["records:8:summary:0:100"] = (0, '"e"', [])

        event = {"record_id": 3, "user_id": 7, "status": "completed", "analysis_progress": 100}
        asyncio.run(ProgressConnectionManager()._broadcast(event))

        self.assertEqual(list(records._records_cache), ["records:8:summary:0:100"])
        records._records_cache.clear()

    def test_cache_headers_force_revalidation(self):
        import routes.record as records
        from fastapi import Response

        response = Response()
        records.set_cached_payload(response, "records:1:summary:0:100", [])
        self.assertEqual(response.headers["Cache-Control"], "private, no-cache")
        records._records_cache.clear()


if __name__ == "__main__":
    unittest.main()
