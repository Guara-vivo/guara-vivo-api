"""Nest zones are hidden from non-admins (issue #53, item 1).

Real HTTP through the ASGI stack with real JWTs, against a throwaway SQLite file so the actual
SQL filters run. The Postgres ARRAY columns are swapped for JSON for the duration of this module
only (SQLite has no arrays); everything else is the production code path.
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
from sqlalchemy import JSON, create_engine  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import main  # noqa: E402
from database import get_db  # noqa: E402
from models import Analysis, MapZone, Record, RecordMapZone, User  # noqa: E402
from security import create_access_token  # noqa: E402

ADMIN, ALICE, BOB, CAROL = 1, 2, 3, 4

FEEDING = 1
NEST_A = 2  # records from alice (completed + pending) and bob
NEST_B = 3  # a record from bob only

# Distinct from every record's camera position, so a leak of the zone position is detectable.
NEST_A_LAT, NEST_A_LON = -15.111111, -47.222222

R_ALICE_NEST = 1
R_BOB_NEST = 2
R_ALICE_FEEDING = 3
R_BOB_FEEDING = 4
R_ALICE_NEST_PENDING = 5
R_BOB_NEST_B = 6

ARRAY_COLUMNS = (Record.__table__.c.images, Record.__table__.c.behavior, Analysis.__table__.c.avisos)
TABLES = [
    User.__table__,
    Record.__table__,
    Analysis.__table__,
    MapZone.__table__,
    RecordMapZone.__table__,
]


def _record(record_id: int, user_id: int, status: str) -> Record:
    return Record(
        id=record_id,
        images=[f"https://example.com/{record_id}.jpg"],
        latitude_camera=-10.0 - record_id,
        longitude_camera=-40.0 - record_id,
        behavior=["voando"],
        date_time=datetime(2026, 6, 1, 12, record_id, tzinfo=timezone.utc),
        user_id=user_id,
        status=status,
    )


def _zone(zone_id: int, zone_type: str, name: str, index: int, lat: float, lon: float) -> MapZone:
    return MapZone(
        id=zone_id,
        type=zone_type,
        name=name,
        sequence_index=index,
        latitude=lat,
        longitude=lon,
        radius_meters=75,
        user_id=ADMIN,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


def _seed(sync_engine) -> None:
    with Session(sync_engine) as session:
        session.add_all(
            [
                User(id=ADMIN, name="Admin", email="admin@example.com", password="x", is_admin=True),
                User(id=ALICE, name="Alice", email="alice@example.com", password="x", is_admin=False),
                User(id=BOB, name="Bob", email="bob@example.com", password="x", is_admin=False),
                User(id=CAROL, name="Carol", email="carol@example.com", password="x", is_admin=False),
            ]
        )
        session.add_all(
            [
                _zone(FEEDING, "feeding", "Alimentação A", 0, -12.5, -45.5),
                _zone(NEST_A, "nest", "Ninho A", 0, NEST_A_LAT, NEST_A_LON),
                _zone(NEST_B, "nest", "Ninho B", 1, -16.333333, -48.444444),
            ]
        )
        session.flush()
        session.add_all(
            [
                _record(R_ALICE_NEST, ALICE, "completed"),
                _record(R_BOB_NEST, BOB, "completed"),
                _record(R_ALICE_FEEDING, ALICE, "completed"),
                _record(R_BOB_FEEDING, BOB, "completed"),
                _record(R_ALICE_NEST_PENDING, ALICE, "pending"),
                _record(R_BOB_NEST_B, BOB, "completed"),
            ]
        )
        session.flush()
        for record_id in (R_ALICE_NEST, R_BOB_NEST, R_ALICE_FEEDING, R_BOB_FEEDING, R_BOB_NEST_B):
            session.add(
                Analysis(
                    ibis_quantity=2,
                    datetime=datetime(2026, 6, 1, tzinfo=timezone.utc),
                    recorder_id=record_id,
                )
            )
        for record_id, zone_id in (
            (R_ALICE_NEST, NEST_A),
            (R_BOB_NEST, NEST_A),
            (R_ALICE_NEST_PENDING, NEST_A),
            (R_ALICE_FEEDING, FEEDING),
            (R_BOB_FEEDING, FEEDING),
            (R_BOB_NEST_B, NEST_B),
        ):
            session.add(RecordMapZone(record_id=record_id, map_zone_id=zone_id))
        session.commit()


class NestZoneVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._original_types = [(column, column.type) for column in ARRAY_COLUMNS]
        for column in ARRAY_COLUMNS:
            column.type = JSON()

        cls.tmpdir = tempfile.mkdtemp(prefix="nest-zones-")
        db_file = Path(cls.tmpdir) / "test.db"

        sync_engine = create_engine(f"sqlite:///{db_file}")
        SQLModel.metadata.create_all(sync_engine, tables=TABLES)
        _seed(sync_engine)
        sync_engine.dispose()

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
        shutil.rmtree(cls.tmpdir, ignore_errors=True)
        for column, original in cls._original_types:
            column.type = original

    def get(self, path: str, user_id: int):
        token = create_access_token(SimpleNamespace(id=user_id, email=f"user{user_id}@example.com"))
        return self.client.get(path, headers={"Authorization": f"Bearer {token}"})

    # --- GET /map-zones/ -------------------------------------------------------------------

    def test_non_admin_list_has_no_nest_zones(self):
        response = self.get("/map-zones/", ALICE)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([zone["id"] for zone in response.json()], [FEEDING])
        self.assertNotIn("nest", {zone["type"] for zone in response.json()})
        self.assertNotIn(str(NEST_A_LAT), response.text)

    def test_admin_list_has_every_zone(self):
        response = self.get("/map-zones/", ADMIN)

        self.assertEqual(response.status_code, 200)
        self.assertEqual({zone["id"] for zone in response.json()}, {FEEDING, NEST_A, NEST_B})

    # --- GET /map-zones/{id}/records on a nest ---------------------------------------------

    def test_non_admin_owner_sees_only_their_own_records_in_a_nest(self):
        response = self.get(f"/map-zones/{NEST_A}/records", ALICE)

        self.assertEqual(response.status_code, 200)
        items = response.json()
        self.assertEqual([item["id"] for item in items], [R_ALICE_NEST])
        self.assertEqual({item["author_name"] for item in items}, {"Alice"})

    def test_non_admin_owner_does_not_get_the_nest_position(self):
        response = self.get(f"/map-zones/{NEST_A}/records", ALICE)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(str(NEST_A_LAT), response.text)
        self.assertNotIn(str(NEST_A_LON), response.text)
        self.assertNotIn("radius_meters", response.text)
        for item in response.json():
            for linked_zone in item["map_zones"]:
                self.assertEqual(set(linked_zone), {"id", "type", "name"})

    def test_non_admin_without_records_in_the_nest_gets_404(self):
        for user_id in (CAROL, ALICE):
            zone_id = NEST_A if user_id == CAROL else NEST_B
            response = self.get(f"/map-zones/{zone_id}/records", user_id)
            self.assertEqual(response.status_code, 404, (user_id, zone_id))

    def test_nest_with_only_foreign_records_looks_like_a_missing_zone(self):
        existing = self.get(f"/map-zones/{NEST_B}/records", ALICE)
        missing = self.get("/map-zones/9999/records", ALICE)

        self.assertEqual(existing.status_code, 404)
        self.assertEqual(existing.json(), missing.json())

    def test_non_admin_paging_past_their_records_is_not_a_404(self):
        response = self.get(f"/map-zones/{NEST_A}/records?skip=5", ALICE)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_admin_sees_every_visible_record_in_a_nest(self):
        response = self.get(f"/map-zones/{NEST_A}/records", ADMIN)

        self.assertEqual(response.status_code, 200)
        self.assertEqual({item["id"] for item in response.json()}, {R_ALICE_NEST, R_BOB_NEST})

    # --- feeding zones keep today's behavior -----------------------------------------------

    def test_feeding_zone_records_stay_visible_to_every_user(self):
        for user_id in (ADMIN, ALICE, CAROL):
            response = self.get(f"/map-zones/{FEEDING}/records", user_id)
            self.assertEqual(response.status_code, 200, user_id)
            self.assertEqual(
                {item["id"] for item in response.json()}, {R_ALICE_FEEDING, R_BOB_FEEDING}, user_id
            )


if __name__ == "__main__":
    unittest.main()
