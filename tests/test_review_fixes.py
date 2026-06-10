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

    def test_zone_suffix_continues_after_alphabet(self):
        from services.map_zone_service import index_to_zone_suffix

        self.assertEqual(index_to_zone_suffix(0), "A")
        self.assertEqual(index_to_zone_suffix(25), "Z")
        self.assertEqual(index_to_zone_suffix(26), "AA")
        self.assertEqual(index_to_zone_suffix(27), "AB")

    def test_zone_name_uses_type_label(self):
        from services.map_zone_service import format_zone_name

        self.assertEqual(format_zone_name("feeding", 0), "Alimentação A")
        self.assertEqual(format_zone_name("nest", 26), "Ninho AA")

    def test_smallest_free_sequence_index_reuses_deleted_name(self):
        from services.map_zone_service import find_smallest_free_sequence_index

        self.assertEqual(find_smallest_free_sequence_index([0, 2, 3]), 1)
        self.assertEqual(find_smallest_free_sequence_index([0, 1, 2]), 3)

    def test_zones_overlap_only_when_distance_is_smaller_than_radius_sum(self):
        from services.map_zone_service import zones_overlap

        self.assertTrue(zones_overlap(0, 0, 100, 0, 0.001, 100))
        self.assertFalse(zones_overlap(0, 0, 50, 0, 0.001, 50))

    def test_point_inside_zone_uses_radius(self):
        from services.map_zone_service import point_inside_zone

        self.assertTrue(point_inside_zone(0, 0.0005, 0, 0, 100))
        self.assertFalse(point_inside_zone(0, 0.002, 0, 0, 100))

    def test_record_summary_serializes_linked_map_zones(self):
        from models import MapZone, Record
        from routes.record import serialize_record_summary

        record = Record(
            id=123,
            user_id=1,
            images=["https://example.com/a.jpg"],
            latitude_camera=0,
            longitude_camera=0,
            behavior=["voando"],
            date_time="2026-06-04T12:00:00Z",
            status="pending",
            analysis_progress=0,
        )
        zone = MapZone(
            id=7,
            type="feeding",
            name="Alimentação A",
            sequence_index=0,
            latitude=0,
            longitude=0,
            radius_meters=100,
            user_id=1,
            created_at="2026-06-04T12:00:00Z",
        )

        payload = serialize_record_summary(record, None, [zone])

        self.assertEqual(payload["map_zones"], [{"id": 7, "type": "feeding", "name": "Alimentação A"}])

    def test_map_zone_record_serializes_author_name(self):
        from models import MapZone, Record
        from routes.map_zones import serialize_map_zone_record

        record = Record(
            id=123,
            user_id=1,
            images=["https://example.com/a.jpg"],
            latitude_camera=0,
            longitude_camera=0,
            behavior=["voando"],
            date_time="2026-06-04T12:00:00Z",
            status="completed",
            analysis_progress=100,
        )
        zone = MapZone(
            id=7,
            type="feeding",
            name="Alimentação A",
            sequence_index=0,
            latitude=0,
            longitude=0,
            radius_meters=100,
            user_id=1,
            created_at="2026-06-04T12:00:00Z",
        )

        payload = serialize_map_zone_record(
            record,
            SimpleNamespace(id=9, ibis_quantity=4),
            [zone],
            SimpleNamespace(name="Ana Silva", email="ana@example.com"),
        )

        self.assertEqual(payload["id"], 123)
        self.assertEqual(payload["ibis_quantity"], 4)
        self.assertEqual(payload["author_name"], "Ana Silva")
        self.assertNotIn("email", payload)

    def test_map_zone_record_visibility_requires_completed_status_and_guaras(self):
        from models import Record
        from routes.map_zones import is_visible_map_zone_record

        base_record = Record(
            id=123,
            user_id=1,
            images=["https://example.com/a.jpg"],
            latitude_camera=0,
            longitude_camera=0,
            behavior=["voando"],
            date_time="2026-06-04T12:00:00Z",
            status="completed",
            analysis_progress=100,
        )

        self.assertTrue(is_visible_map_zone_record(base_record, SimpleNamespace(ibis_quantity=1)))

        failed_record = Record(**{**base_record.model_dump(), "status": "failed"})
        self.assertFalse(is_visible_map_zone_record(failed_record, SimpleNamespace(ibis_quantity=1)))
        self.assertFalse(is_visible_map_zone_record(base_record, SimpleNamespace(ibis_quantity=0)))
        self.assertFalse(is_visible_map_zone_record(base_record, None))
