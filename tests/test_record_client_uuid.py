import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
# Keep the default limit even if a developer .env overrides it (other tests assert 10MB).
os.environ.setdefault("MAX_REQUEST_BODY_BYTES", "10485760")

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)

UUID_A = "3f2b8c1e-9d4a-4c55-8f0e-1a2b3c4d5e6f"

RECORD = dict(
    images=["https://example.com/a.jpg"],
    latitude_camera=1,
    longitude_camera=2,
    behavior=["voando"],
    date_time="2026-06-04T12:00:00Z",
    user_id=1,
)


class FakeDb:
    def __init__(self):
        self.rollbacks = 0
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def refresh(self, _record):
        return None

    async def rollback(self):
        self.rollbacks += 1


class ClientUuidSchemaTests(unittest.TestCase):
    def test_client_uuid_is_optional_and_normalized(self):
        from schemas import RecordCreate

        self.assertIsNone(RecordCreate(**RECORD).client_uuid)
        record = RecordCreate(**RECORD, client_uuid=UUID_A.upper())
        self.assertEqual(record.client_uuid, UUID_A)

    def test_invalid_client_uuid_is_rejected(self):
        from schemas import RecordCreate

        with self.assertRaises(ValidationError):
            RecordCreate(**RECORD, client_uuid="not-a-uuid")

    def test_invalid_client_uuid_uses_the_documented_message(self):
        from schemas import CLIENT_UUID_INVALID_MESSAGE, RecordCreate

        self.assertEqual(
            CLIENT_UUID_INVALID_MESSAGE,
            "Identificador único da coleta (client_uuid) inválido ou ausente.",
        )
        with self.assertRaises(ValidationError) as ctx:
            RecordCreate(**RECORD, client_uuid="not-a-uuid")
        self.assertIn(CLIENT_UUID_INVALID_MESSAGE, str(ctx.exception))

    def test_update_schema_does_not_accept_client_uuid(self):
        from schemas import RecordUpdate

        self.assertNotIn("client_uuid", RecordUpdate(**RECORD, client_uuid=UUID_A).model_dump())

    def test_record_read_exposes_client_uuid(self):
        from schemas import RecordRead

        self.assertIn("client_uuid", RecordRead.model_fields)

    def test_unique_per_user_constraint_exists(self):
        from models import Record

        names = {c.name for c in Record.__table__.constraints}
        self.assertIn("uq_records_user_client_uuid", names)


class IdempotentCreateTests(unittest.TestCase):
    def setUp(self):
        import routes.record as record_routes

        self.routes = record_routes
        self.originals = (record_routes.find_record_by_client_uuid, record_routes.commit_record_and_publish)
        self.published = []

    def tearDown(self):
        self.routes.find_record_by_client_uuid, self.routes.commit_record_and_publish = self.originals

    def patch(self, found, commit):
        async def find(_db, _user_id, _client_uuid):
            return found.pop(0) if found else None

        self.routes.find_record_by_client_uuid = find
        self.routes.commit_record_and_publish = commit

    def test_existing_record_is_returned_without_creating_or_publishing(self):
        existing = SimpleNamespace(id=7, user_id=1, client_uuid=UUID_A, status="pending")

        async def commit(_db, _record):
            self.published.append("created")
            return _record

        self.patch([existing], commit)
        new = SimpleNamespace(id=None, user_id=1, client_uuid=UUID_A)
        result, created = asyncio.run(self.routes.create_record_idempotently(FakeDb(), new))

        self.assertIs(result, existing)
        self.assertFalse(created)
        self.assertEqual(self.published, [])

    def test_new_record_is_created_once(self):
        async def commit(_db, record):
            self.published.append(record)
            return record

        self.patch([], commit)
        new = SimpleNamespace(id=None, user_id=1, client_uuid=UUID_A)
        result, created = asyncio.run(self.routes.create_record_idempotently(FakeDb(), new))

        self.assertIs(result, new)
        self.assertTrue(created)
        self.assertEqual(len(self.published), 1)

    def test_failed_record_is_queued_again_on_retry(self):
        existing = SimpleNamespace(id=7, user_id=1, client_uuid=UUID_A, status="failed", analysis_progress=40)
        queued = []
        original = self.routes.publish_record_for_inference
        self.routes.publish_record_for_inference = queued.append
        self.addCleanup(setattr, self.routes, "publish_record_for_inference", original)

        async def commit(_db, record):
            return record

        self.patch([existing], commit)
        new = SimpleNamespace(id=None, user_id=1, client_uuid=UUID_A)
        result, created = asyncio.run(self.routes.create_record_idempotently(FakeDb(), new))

        self.assertIs(result, existing)
        self.assertEqual((existing.status, existing.analysis_progress), ("pending", 0))
        self.assertEqual(queued, [7])

    def test_failed_record_stays_failed_if_queueing_fails_again(self):
        from fastapi import HTTPException

        existing = SimpleNamespace(id=7, user_id=1, client_uuid=UUID_A, status="failed", analysis_progress=0)

        def broken(_record_id):
            raise RuntimeError("rabbit down")

        original = self.routes.publish_record_for_inference
        self.routes.publish_record_for_inference = broken
        self.addCleanup(setattr, self.routes, "publish_record_for_inference", original)

        async def commit(_db, record):
            return record

        self.patch([existing], commit)
        new = SimpleNamespace(id=None, user_id=1, client_uuid=UUID_A)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self.routes.create_record_idempotently(FakeDb(), new))

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(existing.status, "failed")

    def test_pending_or_completed_record_is_not_queued_again(self):
        queued = []
        original = self.routes.publish_record_for_inference
        self.routes.publish_record_for_inference = queued.append
        self.addCleanup(setattr, self.routes, "publish_record_for_inference", original)

        async def commit(_db, record):
            return record

        for status in ("pending", "processing", "completed"):
            existing = SimpleNamespace(id=7, user_id=1, client_uuid=UUID_A, status=status, analysis_progress=10)
            self.patch([existing], commit)
            new = SimpleNamespace(id=None, user_id=1, client_uuid=UUID_A)
            asyncio.run(self.routes.create_record_idempotently(FakeDb(), new))
            self.assertEqual(existing.status, status)

        self.assertEqual(queued, [])

    def test_race_on_unique_constraint_returns_the_winner(self):
        winner = SimpleNamespace(id=9, user_id=1, client_uuid=UUID_A, status="pending")

        async def commit(_db, _record):
            raise IntegrityError("insert", {}, Exception("duplicate"))

        # first lookup: nothing yet; lookup after the conflict: the other request's row
        self.patch([None, winner], commit)
        db = FakeDb()
        new = SimpleNamespace(id=None, user_id=1, client_uuid=UUID_A)
        result, created = asyncio.run(self.routes.create_record_idempotently(db, new))

        self.assertIs(result, winner)
        self.assertFalse(created)
        self.assertEqual(db.rollbacks, 1)

    def test_integrity_errors_without_a_match_are_not_swallowed(self):
        async def commit(_db, _record):
            raise IntegrityError("insert", {}, Exception("fk violation"))

        self.patch([], commit)
        new = SimpleNamespace(id=None, user_id=1, client_uuid=None)
        with self.assertRaises(IntegrityError):
            asyncio.run(self.routes.create_record_idempotently(FakeDb(), new))

    def test_records_without_client_uuid_skip_the_lookup(self):
        async def find(*_args):
            raise AssertionError("must not query without a client_uuid")

        async def commit(_db, record):
            return record

        self.routes.commit_record_and_publish = commit
        # the real finder returns early for a missing uuid, without touching the db
        asyncio.run(self.routes.find_record_by_client_uuid(FakeDb(), 1, None))


class CreateStatusCodeTests(unittest.TestCase):
    """POST /records answers 201 for a new record and 200 for an idempotent replay."""

    def setUp(self):
        import routes.record as record_routes

        self.routes = record_routes
        self.originals = (
            record_routes.create_record_idempotently,
            record_routes.load_record_map_zones,
            record_routes.serialize_record_read,
        )

        async def no_zones(_db, _ids):
            return {}

        record_routes.load_record_map_zones = no_zones
        record_routes.serialize_record_read = lambda record, _zones=None: {"id": record.id}

    def tearDown(self):
        (
            self.routes.create_record_idempotently,
            self.routes.load_record_map_zones,
            self.routes.serialize_record_read,
        ) = self.originals

    def call(self, created):
        from fastapi import Response
        from schemas import RecordCreate

        async def fake(_db, record):
            return SimpleNamespace(id=7), created

        self.routes.create_record_idempotently = fake
        response = Response()
        user = SimpleNamespace(id=1)
        asyncio.run(self.routes.create_record(RecordCreate(**RECORD), response, FakeDb(), user))
        return response

    def test_route_default_status_is_201(self):
        for route in self.routes.router.routes:
            if route.path in ("/records/", "/records/upload") and "POST" in route.methods:
                self.assertEqual(route.status_code, 201, route.path)

    def test_new_record_answers_201(self):
        self.assertEqual(self.call(created=True).status_code, 201)

    def test_replayed_record_answers_200(self):
        self.assertEqual(self.call(created=False).status_code, 200)


if __name__ == "__main__":
    unittest.main()
