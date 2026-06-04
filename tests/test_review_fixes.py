import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)


class ReviewFixTests(unittest.TestCase):
    def test_record_create_does_not_accept_client_progress(self):
        from schemas import RecordCreate

        record = RecordCreate(
            images=["https://example.com/a.jpg"],
            latitude_camera=1,
            longitude_camera=2,
            behavior=["voando"],
            date_time="2026-06-04T12:00:00Z",
            user_id=1,
            analysis_progress=77,
        )

        self.assertNotIn("analysis_progress", record.model_dump())

    def test_total_upload_limit_matches_request_body_limit(self):
        import routes.record as record_routes
        from middleware import MAX_REQUEST_BODY_BYTES

        self.assertEqual(record_routes.MAX_TOTAL_UPLOAD_SIZE, MAX_REQUEST_BODY_BYTES)
        self.assertEqual(record_routes.MAX_TOTAL_UPLOAD_SIZE, 10 * 1024 * 1024)

    def test_records_cache_evicts_oldest_entry_when_full(self):
        import routes.record as record_routes

        class Response:
            def __init__(self):
                self.headers = {}

        record_routes._records_cache.clear()
        original_limit = record_routes.RECORDS_CACHE_MAX_ENTRIES
        record_routes.RECORDS_CACHE_MAX_ENTRIES = 2
        try:
            record_routes.set_cached_payload(Response(), "records:1:summary:0", [{"id": 1}])
            record_routes.set_cached_payload(Response(), "records:1:summary:1", [{"id": 2}])
            record_routes.set_cached_payload(Response(), "records:1:summary:2", [{"id": 3}])

            self.assertNotIn("records:1:summary:0", record_routes._records_cache)
            self.assertEqual(len(record_routes._records_cache), 2)
        finally:
            record_routes.RECORDS_CACHE_MAX_ENTRIES = original_limit
            record_routes._records_cache.clear()

    def test_body_limit_middleware_replays_body_chunks_without_coalescing(self):
        from middleware import BodyLimitMiddleware

        received_chunks = []

        async def app(scope, receive, send):
            while True:
                message = await receive()
                received_chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break

        messages = [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]

        async def receive():
            return messages.pop(0)

        async def send(_message):
            return None

        asyncio.run(
            BodyLimitMiddleware(app)(
                {"type": "http", "method": "POST"},
                receive,
                send,
            )
        )

        self.assertEqual(received_chunks, [b"abc", b"def"])

    def test_read_record_returns_404_for_other_user(self):
        from models import Record
        from routes.record import read_record

        class Result:
            def scalar_one_or_none(self):
                return Record(id=123, user_id=2, images=["x"], latitude_camera=0, longitude_camera=0, behavior=["voando"], date_time="2026-06-04T12:00:00Z")

        class DB:
            async def execute(self, _statement):
                return Result()

        with self.assertRaises(HTTPException) as exc_info:
            asyncio.run(read_record(123, db=DB(), current_user=SimpleNamespace(id=1)))

        self.assertEqual(exc_info.exception.status_code, 404)
