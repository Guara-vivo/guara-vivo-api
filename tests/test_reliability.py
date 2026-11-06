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

    def test_read_that_raced_with_an_invalidation_is_not_cached(self):
        import routes.record as records
        from fastapi import Response

        records._records_cache.clear()
        key = "records:5:summary:0:100"

        token = records.get_cache_token(5)  # the request starts reading the old state...
        records.invalidate_records_cache(5)  # ...the worker commits and the user's cache is invalidated...
        records.set_cached_payload(Response(), key, [{"ibis_quantity": None}], 5, token)  # ...then it stores

        self.assertNotIn(key, records._records_cache)

    def test_read_without_a_concurrent_invalidation_is_cached(self):
        import routes.record as records
        from fastapi import Response

        records._records_cache.clear()
        key = "records:5:summary:0:100"
        token = records.get_cache_token(5)
        records.invalidate_records_cache(6)  # another user: does not affect this one
        records.set_cached_payload(Response(), key, [], 5, token)

        self.assertIn(key, records._records_cache)
        records._records_cache.clear()

    def test_global_invalidation_also_discards_in_flight_reads(self):
        import routes.record as records
        from fastapi import Response

        records._records_cache.clear()
        key = "records:5:detail:1"
        token = records.get_cache_token(5)
        records.invalidate_all_records_cache()
        records.set_cached_payload(Response(), key, {}, 5, token)

        self.assertNotIn(key, records._records_cache)

    def test_cache_headers_force_revalidation(self):
        import routes.record as records
        from fastapi import Response

        response = Response()
        records.set_cached_payload(response, "records:1:summary:0:100", [])
        self.assertEqual(response.headers["Cache-Control"], "private, no-cache")
        records._records_cache.clear()


if __name__ == "__main__":
    unittest.main()


class ListenerHealthTests(unittest.TestCase):
    def manager(self, connection):
        from routes.progress import ProgressConnectionManager

        manager = ProgressConnectionManager()
        manager._connection = connection
        manager._keep_alive = True
        self.reconnects = 0

        def schedule():
            self.reconnects += 1

        manager._schedule_reconnect = schedule
        return manager

    def test_healthy_connection_is_left_alone(self):
        class Conn:
            def is_closed(self):
                return False

            async def execute(self, _sql):
                return "SELECT 1"

        conn = Conn()
        manager = self.manager(conn)
        self.assertTrue(asyncio.run(manager.check_listener()))
        self.assertIs(manager._connection, conn)
        self.assertEqual(self.reconnects, 0)

    def test_hung_connection_is_terminated_and_rebuilt(self):
        import routes.progress as progress

        class Conn:
            terminated = False

            def is_closed(self):
                return False

            async def execute(self, _sql):
                await asyncio.sleep(60)  # half-open socket: the server never answers

            def terminate(self):
                self.terminated = True

        original = progress.LISTENER_PING_TIMEOUT_SECONDS
        progress.LISTENER_PING_TIMEOUT_SECONDS = 0.05
        self.addCleanup(setattr, progress, "LISTENER_PING_TIMEOUT_SECONDS", original)

        conn = Conn()
        manager = self.manager(conn)
        self.assertFalse(asyncio.run(manager.check_listener()))
        self.assertTrue(conn.terminated)
        self.assertIsNone(manager._connection)
        self.assertEqual(self.reconnects, 1)

    def test_failing_ping_triggers_reconnect(self):
        class Conn:
            def is_closed(self):
                return False

            async def execute(self, _sql):
                raise ConnectionResetError("reset")

            def terminate(self):
                pass

        manager = self.manager(Conn())
        self.assertFalse(asyncio.run(manager.check_listener()))
        self.assertEqual(self.reconnects, 1)

    def test_missing_connection_is_rebuilt_even_if_no_termination_event_came(self):
        manager = self.manager(None)
        self.assertFalse(asyncio.run(manager.check_listener()))
        self.assertEqual(self.reconnects, 1)

    def test_start_launches_the_watchdog_and_close_stops_it(self):
        from routes.progress import ProgressConnectionManager

        async def scenario():
            manager = ProgressConnectionManager()

            async def no_connect():
                return None

            manager.ensure_listener = no_connect
            await manager.start()
            task = manager._health_task
            self.assertIsNotNone(task)
            await manager.close()
            await asyncio.sleep(0)
            return task

        task = asyncio.run(scenario())
        self.assertTrue(task.cancelled() or task.done())
