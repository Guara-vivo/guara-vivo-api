"""Admins can open the detail of other users' records (Guara-vivo/documents#50) and list every
user's records for the map (Guara-vivo/documents#70).

Real HTTP through the ASGI stack with real JWTs, against a throwaway SQLite file so the actual
SQL runs. The Postgres ARRAY columns are swapped for JSON for the duration of this module only
(SQLite has no arrays); everything else is the production code path.
"""
import asyncio
import os
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("MAX_REQUEST_BODY_BYTES", "10485760")

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import JSON, create_engine, update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import main  # noqa: E402
from database import get_db  # noqa: E402
from models import Analysis, AnalysisImage, Ibis, MapZone, Record, RecordMapZone, User  # noqa: E402
from routes.record import invalidate_records_cache  # noqa: E402
from security import create_access_token  # noqa: E402

ADMIN, ALICE, BOB = 1, 2, 3

R_ALICE = 1
R_BOB = 2
R_BOB_CACHE = 3  # only the cache test touches it, so no other test depends on its analysis
R_ADMIN = 4
R_BOB_STALE = 5  # only the summary cache test changes its analysis
R_PENDING = 6  # still waiting for inference: no analysis row yet
R_MISSING = 9999

ARRAY_COLUMNS = (Record.__table__.c.images, Record.__table__.c.behavior, Analysis.__table__.c.avisos)
TABLES = [
    User.__table__,
    Record.__table__,
    Analysis.__table__,
    AnalysisImage.__table__,
    Ibis.__table__,
    MapZone.__table__,
    RecordMapZone.__table__,
]


def _record(record_id: int, user_id: int) -> Record:
    return Record(
        id=record_id,
        images=[f"https://example.com/{record_id}.jpg"],
        latitude_camera=-10.0 - record_id,
        longitude_camera=-40.0 - record_id,
        behavior=["voando"],
        date_time=datetime(2026, 6, 1, 12, record_id, tzinfo=timezone.utc),
        user_id=user_id,
        status="completed",
    )


def _seed(sync_engine) -> None:
    with Session(sync_engine) as session:
        session.add_all(
            [
                User(id=ADMIN, name="Admin", email="admin@example.com", password="x", is_admin=True),
                User(id=ALICE, name="Alice", email="alice@example.com", password="x", is_admin=False),
                User(id=BOB, name="Bob", email="bob@example.com", password="x", is_admin=False),
            ]
        )
        session.flush()
        session.add_all(
            [
                _record(R_ALICE, ALICE),
                _record(R_BOB, BOB),
                _record(R_BOB_CACHE, BOB),
                _record(R_ADMIN, ADMIN),
                _record(R_BOB_STALE, BOB),
                _record(R_PENDING, ALICE),
            ]
        )
        session.flush()
        for record_id in (R_ALICE, R_BOB, R_BOB_CACHE, R_ADMIN, R_BOB_STALE):
            session.add(
                Analysis(
                    ibis_quantity=2,
                    datetime=datetime(2026, 6, 1, tzinfo=timezone.utc),
                    recorder_id=record_id,
                )
            )
        session.execute(update(Record).where(Record.id == R_PENDING).values(status="pending"))
        session.commit()


class AdminRecordDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._original_types = [(column, column.type) for column in ARRAY_COLUMNS]
        for column in ARRAY_COLUMNS:
            column.type = JSON()

        cls.tmpdir = tempfile.mkdtemp(prefix="admin-record-detail-")
        db_file = Path(cls.tmpdir) / "test.db"

        cls.sync_engine = create_engine(f"sqlite:///{db_file}")
        SQLModel.metadata.create_all(cls.sync_engine, tables=TABLES)
        _seed(cls.sync_engine)

        cls.async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", poolclass=NullPool)
        sessionmaker = async_sessionmaker(cls.async_engine, class_=AsyncSession, expire_on_commit=False)

        async def override_get_db():
            async with sessionmaker() as session:
                yield session

        cls._previous_override = main.app.dependency_overrides.get(get_db)
        main.app.dependency_overrides[get_db] = override_get_db
        # No `with`: the lifespan would try to reach PostgreSQL.
        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls):
        if cls._previous_override is None:
            main.app.dependency_overrides.pop(get_db, None)
        else:
            main.app.dependency_overrides[get_db] = cls._previous_override
        asyncio.run(cls.async_engine.dispose())
        cls.sync_engine.dispose()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)
        for column, original in cls._original_types:
            column.type = original

    def detail(self, record_id: int, user_id: int):
        token = create_access_token(SimpleNamespace(id=user_id, email=f"user{user_id}@example.com"))
        return self.client.get(
            f"/records/{record_id}/detail", headers={"Authorization": f"Bearer {token}"}
        )

    def admin_summary(self, user_id: int, **params):
        token = create_access_token(SimpleNamespace(id=user_id, email=f"user{user_id}@example.com"))
        return self.client.get(
            "/records/admin/summary", params=params, headers={"Authorization": f"Bearer {token}"}
        )

    def set_ibis_quantity(self, record_id: int, quantity: int) -> None:
        with Session(self.sync_engine) as session:
            session.execute(
                update(Analysis).where(Analysis.recorder_id == record_id).values(ibis_quantity=quantity)
            )
            session.commit()

    # --- who can open a record -------------------------------------------------------------

    def test_admin_opens_the_detail_of_another_users_record(self):
        response = self.detail(R_BOB, ADMIN)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], R_BOB)
        self.assertEqual(body["user_id"], BOB)
        self.assertEqual(body["analysis"]["ibis_quantity"], 2)

    def test_admin_sees_who_sent_the_record(self):
        response = self.detail(R_BOB, ADMIN)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["author_name"], "Bob")

    def test_non_admin_still_gets_404_on_someone_elses_record(self):
        response = self.detail(R_BOB, ALICE)

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Bob", response.text)

    def test_owner_still_opens_their_own_record(self):
        response = self.detail(R_BOB, BOB)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], R_BOB)
        self.assertEqual(response.json()["author_name"], "Bob")

    def test_admin_still_opens_their_own_record(self):
        response = self.detail(R_ADMIN, ADMIN)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["author_name"], "Admin")

    def test_admin_gets_404_on_a_record_that_does_not_exist(self):
        self.assertEqual(self.detail(R_MISSING, ADMIN).status_code, 404)

    # --- the admin must not read stale data of someone else's record -----------------------

    def test_admin_does_not_get_stale_data_after_the_owner_cache_is_invalidated(self):
        """The worker's NOTIFY invalidates the OWNER's cache entries. An admin reading the same
        record must not keep a copy under their own key, or they would see the old analysis."""
        first = self.detail(R_BOB_CACHE, ADMIN)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["analysis"]["ibis_quantity"], 2)

        self.set_ibis_quantity(R_BOB_CACHE, 3)
        invalidate_records_cache(BOB)

        second = self.detail(R_BOB_CACHE, ADMIN)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["analysis"]["ibis_quantity"], 3)

    # --- admin list of every user's records, for the map (#70) -----------------------------

    def test_admin_summary_lists_every_users_records_with_author(self):
        response = self.admin_summary(ADMIN)

        self.assertEqual(response.status_code, 200)
        rows = {row["id"]: row for row in response.json()}
        self.assertEqual(set(rows), {R_ALICE, R_BOB, R_BOB_CACHE, R_ADMIN, R_BOB_STALE, R_PENDING})
        self.assertEqual(rows[R_BOB]["author_name"], "Bob")
        self.assertEqual(rows[R_ALICE]["author_name"], "Alice")
        self.assertEqual(rows[R_BOB]["ibis_quantity"], 2)
        self.assertEqual(rows[R_BOB]["latitude_camera"], -12.0)

    def test_admin_summary_includes_records_without_analysis(self):
        rows = {row["id"]: row for row in self.admin_summary(ADMIN).json()}

        self.assertEqual(rows[R_PENDING]["status"], "pending")
        self.assertIsNone(rows[R_PENDING]["ibis_quantity"])

    def test_admin_summary_is_404_for_non_admin(self):
        response = self.admin_summary(ALICE)

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Bob", response.text)

    def test_admin_summary_requires_authentication(self):
        self.assertEqual(self.client.get("/records/admin/summary").status_code, 401)

    def test_admin_summary_is_paginated_in_a_stable_order(self):
        first = self.admin_summary(ADMIN, skip=0, limit=2).json()
        second = self.admin_summary(ADMIN, skip=2, limit=2).json()

        self.assertEqual([row["id"] for row in first], [1, 2])
        self.assertEqual([row["id"] for row in second], [3, 4])

    def test_admin_summary_rejects_a_limit_above_100(self):
        self.assertEqual(self.admin_summary(ADMIN, limit=101).status_code, 422)

    def test_admin_summary_does_not_get_stale_after_the_owner_cache_is_invalidated(self):
        """The worker's NOTIFY invalidates only the OWNER's cache entries, so the admin list
        must not be served from a copy that the owner's invalidation cannot reach."""
        before = {row["id"]: row for row in self.admin_summary(ADMIN).json()}
        self.assertEqual(before[R_BOB_STALE]["ibis_quantity"], 2)

        self.set_ibis_quantity(R_BOB_STALE, 5)
        invalidate_records_cache(BOB)

        after = {row["id"]: row for row in self.admin_summary(ADMIN).json()}
        self.assertEqual(after[R_BOB_STALE]["ibis_quantity"], 5)

    def test_summary_of_a_regular_user_still_lists_only_their_own_records(self):
        token = create_access_token(SimpleNamespace(id=ALICE, email="alice@example.com"))
        response = self.client.get("/records/summary", headers={"Authorization": f"Bearer {token}"})

        self.assertEqual({row["id"] for row in response.json()}, {R_ALICE, R_PENDING})


if __name__ == "__main__":
    unittest.main()
