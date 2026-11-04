import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)


class PasswordSchemaTests(unittest.TestCase):
    def test_password_over_72_bytes_is_rejected(self):
        from schemas import UserCreate

        with self.assertRaises(ValidationError):
            UserCreate(name="a", email="a@b.com", password="x" * 73)

    def test_multibyte_password_counts_bytes_not_characters(self):
        from schemas import UserCreate

        with self.assertRaises(ValidationError):
            UserCreate(name="a", email="a@b.com", password="é" * 40)  # 40 chars, 80 bytes

    def test_short_password_is_rejected_on_create(self):
        from schemas import UserCreate

        with self.assertRaises(ValidationError):
            UserCreate(name="a", email="a@b.com", password="1234567")

    def test_valid_password_is_accepted(self):
        from schemas import UserCreate

        self.assertEqual(UserCreate(name="a", email="a@b.com", password="x" * 72).password, "x" * 72)

    def test_login_still_accepts_legacy_short_password(self):
        from schemas import UserLogin

        self.assertEqual(UserLogin(email="a@b.com", password="123456").password, "123456")

    def test_update_requires_current_password_and_new_one_is_optional(self):
        from schemas import UserUpdate

        with self.assertRaises(ValidationError):
            UserUpdate(name="a", email="a@b.com")
        self.assertIsNone(UserUpdate(name="a", email="a@b.com", current_password="x").password)


class StorageTests(unittest.TestCase):
    def setUp(self):
        import supabase_storage

        self.storage = supabase_storage
        self.original = supabase_storage.SUPABASE_URL
        supabase_storage.SUPABASE_URL = "https://proj.supabase.co"
        self.base = self.storage._public_prefix()

    def tearDown(self):
        self.storage.SUPABASE_URL = self.original

    def test_accepts_only_own_uploads(self):
        own = f"{self.base}users/5/records/abc.jpg"
        self.assertTrue(self.storage.is_own_storage_url(own, 5))
        self.assertFalse(self.storage.is_own_storage_url(own, 6))

    def test_rejects_foreign_hosts_and_traversal(self):
        self.assertFalse(self.storage.is_own_storage_url("https://evil.example/a.jpg", 5))
        self.assertFalse(self.storage.is_own_storage_url(f"{self.base}users/5/records/../../users/6/x.jpg", 5))

    def test_extension_comes_from_detected_mime(self):
        self.assertEqual(self.storage.EXTENSION_BY_MIME["image/png"], ".png")


class UserRouteTests(unittest.TestCase):
    def test_other_users_get_404_not_403(self):
        from routes import user as user_routes

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(user_routes.read_user(2, db=None, current_user=SimpleNamespace(id=1)))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_update_with_wrong_current_password_is_401(self):
        from routes import user as user_routes
        from schemas import UserUpdate

        db_user = SimpleNamespace(id=1, password=user_routes.hash_password("correct-password"))

        class Result:
            def scalar_one_or_none(self):
                return db_user

        class Db:
            async def execute(self, _q):
                return Result()

        payload = UserUpdate(name="a", email="a@b.com", current_password="wrong-password")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(user_routes.update_user(1, payload, Db(), SimpleNamespace(id=1)))
        self.assertEqual(ctx.exception.status_code, 401)


class ReadOnlyRoutesTests(unittest.TestCase):
    def test_analysis_and_ibis_have_no_write_endpoints(self):
        from routes import analysis, ibis

        for router in (analysis.router, ibis.router):
            for route in router.routes:
                self.assertEqual(route.methods, {"GET"}, route.path)


if __name__ == "__main__":
    unittest.main()


class AdminAndCascadeTests(unittest.TestCase):
    def test_zone_writes_require_admin(self):
        from routes.map_zones import require_admin

        with self.assertRaises(HTTPException) as ctx:
            require_admin(SimpleNamespace(is_admin=False))
        self.assertEqual(ctx.exception.status_code, 404)
        admin = SimpleNamespace(is_admin=True)
        self.assertIs(require_admin(admin), admin)

    def test_user_foreign_keys_cascade_in_the_database(self):
        from models import MapZone, RefreshToken, Record

        for table, column in ((Record, "user_id"), (RefreshToken, "user_id"), (MapZone, "user_id")):
            fk = list(table.__table__.c[column].foreign_keys)[0]
            self.assertEqual(fk.ondelete, "CASCADE", f"{table.__tablename__}.{column}")


class UserReadTests(unittest.TestCase):
    def test_user_read_exposes_is_admin_but_not_password(self):
        from schemas import UserRead

        user = SimpleNamespace(id=1, name="a", email="a@b.com", password="hash", is_admin=True)
        data = UserRead.model_validate(user, from_attributes=True).model_dump()
        self.assertTrue(data["is_admin"])
        self.assertNotIn("password", data)


class JwtTests(unittest.TestCase):
    def setUp(self):
        import security

        self.security = security
        self.user = SimpleNamespace(id=7, email="a@b.com")

    def decode(self, token):
        import jwt

        return jwt.decode(token, self.security.JWT_SECRET_KEY, algorithms=[self.security.JWT_ALGORITHM])

    def run_user_lookup(self, token):
        class Result:
            def scalar_one_or_none(self_inner):
                return SimpleNamespace(id=7)

        class Db:
            async def execute(self_inner, _q):
                return Result()

        return asyncio.run(self.security.get_user_from_access_token(token, Db()))

    def test_access_token_round_trip(self):
        token = self.security.create_access_token(self.user)
        self.assertEqual(self.decode(token)["sub"], "7")
        self.assertEqual(self.run_user_lookup(token).id, 7)

    def test_rejects_expired_wrong_type_wrong_key_and_alg_none(self):
        import time
        import jwt

        key, alg = self.security.JWT_SECRET_KEY, self.security.JWT_ALGORITHM
        bad = {
            "expired": jwt.encode({"sub": "7", "type": "access", "exp": int(time.time()) - 10}, key, algorithm=alg),
            "refresh_type": jwt.encode({"sub": "7", "type": "refresh", "exp": int(time.time()) + 60}, key, algorithm=alg),
            "wrong_key": jwt.encode({"sub": "7", "type": "access", "exp": int(time.time()) + 60}, "another-key-another-key-another-key", algorithm=alg),
            "alg_none": jwt.encode({"sub": "7", "type": "access", "exp": int(time.time()) + 60}, None, algorithm="none"),
            "garbage": "not-a-token",
        }
        for name, token in bad.items():
            with self.assertRaises(HTTPException, msg=name) as ctx:
                self.run_user_lookup(token)
            self.assertEqual(ctx.exception.status_code, 401, name)
