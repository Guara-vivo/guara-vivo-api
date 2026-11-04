import math
import os
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)


class ZoneLinkingSqlTests(unittest.TestCase):
    def zone(self, **overrides):
        values = dict(id=4, type="nest", latitude=-23.5, longitude=-46.6, radius_meters=100)
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_statement_is_a_single_insert_select_for_postgres(self):
        from sqlalchemy.dialects import postgresql
        from services.map_zone_service import link_records_to_zone_statement

        sql = " ".join(str(link_records_to_zone_statement(self.zone()).compile(dialect=postgresql.dialect())).split())
        self.assertTrue(sql.startswith("INSERT INTO record_map_zones (record_id, map_zone_id) SELECT"))
        self.assertIn("FROM records", sql)
        self.assertIn("NOT (EXISTS", sql)  # skips records already linked to a zone of this type
        self.assertIn("ON CONFLICT ON CONSTRAINT uq_record_map_zones_record_zone DO NOTHING", sql)

    def test_sql_distance_matches_python_haversine(self):
        from sqlalchemy import select
        from sqlalchemy.dialects import sqlite
        from services.map_zone_service import distance_meters, distance_meters_sql

        conn = sqlite3.connect(":memory:")
        for name, fn, args in (
            ("radians", math.radians, 1),
            ("sin", math.sin, 1),
            ("cos", math.cos, 1),
            ("asin", math.asin, 1),
            ("sqrt", math.sqrt, 1),
            ("power", math.pow, 2),
            ("least", min, 2),
        ):
            conn.create_function(name, args, fn)

        cases = [
            (-23.5, -46.6, -23.5, -46.6),
            (-23.5, -46.6, -23.5005, -46.6005),
            (0, 0, 0, 0.001),
            (10, 20, -10, -160),  # near-antipodal: haversine rounds close to 1
        ]
        for a_lat, a_lng, b_lat, b_lng in cases:
            expr = select(distance_meters_sql(a_lat, a_lng, b_lat, b_lng))
            sql = str(expr.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
            got = conn.execute(sql).fetchone()[0]
            self.assertAlmostEqual(got, distance_meters(a_lat, a_lng, b_lat, b_lng), places=3)

    def test_route_no_longer_loads_every_record(self):
        source = (SRC / "routes" / "map_zones.py").read_text(encoding="utf-8")
        self.assertNotIn("select(Record).order_by(Record.id)", source)
        self.assertIn("link_records_to_zone_statement", source)


class RateLimiterTests(unittest.TestCase):
    def make(self, **kw):
        from ratelimit import RateLimiter

        self.now = 0.0
        return RateLimiter(clock=lambda: self.now, **kw)

    def test_blocks_after_max_and_reports_retry_after(self):
        limiter = self.make(max_attempts=2, window_seconds=60)
        self.assertEqual(limiter.hit("ip")[0], True)
        self.now = 10
        self.assertEqual(limiter.hit("ip")[0], True)
        allowed, retry_after = limiter.hit("ip")
        self.assertFalse(allowed)
        self.assertAlmostEqual(retry_after, 50)

    def test_window_slides_and_keys_are_independent(self):
        limiter = self.make(max_attempts=1, window_seconds=60)
        self.assertTrue(limiter.hit("a")[0])
        self.assertTrue(limiter.hit("b")[0])
        self.assertFalse(limiter.hit("a")[0])
        self.now = 61
        self.assertTrue(limiter.hit("a")[0])

    def test_hitting_the_key_cap_does_not_reset_everyone(self):
        limiter = self.make(max_attempts=1, window_seconds=600, max_keys=3)
        for index, key in enumerate(("a", "b", "c")):
            self.now = index
            limiter.hit(key)
        self.now = 3
        limiter.hit("d")  # over the cap: evicts the least recently active ("a"), keeps b and c
        self.assertFalse(limiter.hit("c")[0])
        self.assertFalse(limiter.hit("b")[0])

    def test_expired_keys_are_dropped_before_evicting_live_ones(self):
        limiter = self.make(max_attempts=1, window_seconds=10, max_keys=2)
        limiter.hit("old")
        self.now = 5
        limiter.hit("live")
        self.now = 12  # "old" expired, "live" has not
        limiter.hit("new")
        self.assertFalse(limiter.hit("live")[0])


class RateLimitRouteTests(unittest.TestCase):
    def test_429_carries_retry_after(self):
        from fastapi import HTTPException
        from ratelimit import RateLimiter
        from routes.user import enforce_rate_limit

        limiter = RateLimiter(max_attempts=1, window_seconds=60)
        enforce_rate_limit(limiter, "x")
        with self.assertRaises(HTTPException) as ctx:
            enforce_rate_limit(limiter, "x")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Retry-After", ctx.exception.headers)

    def test_register_and_refresh_are_limited_too(self):
        source = (SRC / "routes" / "user.py").read_text(encoding="utf-8")
        for limiter in ("login_ip_limiter", "login_email_limiter", "register_ip_limiter", "refresh_ip_limiter"):
            self.assertIn(f"enforce_rate_limit({limiter}", source)


if __name__ == "__main__":
    unittest.main()
