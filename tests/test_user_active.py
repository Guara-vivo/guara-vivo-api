"""Deactivating a user (users.is_active = false) blocks login, API calls and refresh.

The flag is flipped directly in the database (no endpoint), so it has to be read on every request.
Real HTTP through the ASGI stack against a throwaway SQLite file (users and refresh_tokens only).
"""
import asyncio
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("MAX_REQUEST_BODY_BYTES", "10485760")

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import main  # noqa: E402
from database import get_db  # noqa: E402
from models import RefreshToken, User  # noqa: E402
from routes import user as user_routes  # noqa: E402

USER_ID = 1
EMAIL, PASSWORD = "tester@example.com", "tester-correct-horse"


class UserActiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="user-active-")
        cls.db_file = Path(cls.tmpdir) / "test.db"

        cls.sync_engine = create_engine(f"sqlite:///{cls.db_file}")
        SQLModel.metadata.create_all(cls.sync_engine, tables=[User.__table__, RefreshToken.__table__])
        with Session(cls.sync_engine) as session:
            session.add(
                User(id=USER_ID, name="Tester", email=EMAIL, password=user_routes.hash_password(PASSWORD))
            )
            session.commit()

        cls.async_engine = create_async_engine(f"sqlite+aiosqlite:///{cls.db_file}", poolclass=NullPool)
        sessionmaker = async_sessionmaker(cls.async_engine, class_=AsyncSession, expire_on_commit=False)

        async def override_get_db():
            async with sessionmaker() as session:
                yield session

        cls._previous_override = main.app.dependency_overrides.get(get_db)
        main.app.dependency_overrides[get_db] = override_get_db

    @classmethod
    def tearDownClass(cls):
        if cls._previous_override is None:
            main.app.dependency_overrides.pop(get_db, None)
        else:
            main.app.dependency_overrides[get_db] = cls._previous_override
        asyncio.run(cls.async_engine.dispose())
        cls.sync_engine.dispose()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        for limiter in (
            user_routes.login_ip_limiter,
            user_routes.login_email_limiter,
            user_routes.refresh_ip_limiter,
        ):
            limiter._hits.clear()
        self.set_active(True)
        self.client = TestClient(main.app)

    # --- helpers --------------------------------------------------------------------------

    def set_active(self, active: bool) -> None:
        with Session(self.sync_engine) as session:
            session.execute(update(User).where(User.id == USER_ID).values(is_active=active))
            session.commit()

    def login(self, password: str = PASSWORD):
        return self.client.post("/users/login", json={"email": EMAIL, "password": password})

    def me(self, access_token: str):
        return self.client.get("/users/me", headers={"Authorization": f"Bearer {access_token}"})

    # --- tests ----------------------------------------------------------------------------

    def test_new_users_are_active_by_default(self):
        self.assertTrue(User(name="x", email="x@example.com", password="h").is_active)

    def test_active_user_logs_in_and_calls_the_api(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.me(response.json()["access_token"]).status_code, 200)

    def test_inactive_user_cannot_log_in(self):
        self.set_active(False)
        response = self.login()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Incorrect email or password")

    def test_inactive_login_looks_like_a_wrong_password(self):
        """A deactivated account must not be told apart from a wrong password (no enumeration)."""
        self.set_active(False)
        inactive = self.login()
        wrong = self.login(password="not-the-password")
        self.assertEqual(inactive.status_code, wrong.status_code)
        self.assertEqual(inactive.json(), wrong.json())

    def test_deactivating_drops_an_open_session_on_the_next_call(self):
        access_token = self.login().json()["access_token"]
        self.assertEqual(self.me(access_token).status_code, 200)

        self.set_active(False)

        response = self.me(access_token)
        self.assertEqual(response.status_code, 401)

    def test_deactivating_blocks_refresh(self):
        refresh_token = self.login().json()["refresh_token"]

        self.set_active(False)

        response = self.client.post("/users/refresh", json={"refresh_token": refresh_token})
        self.assertEqual(response.status_code, 401)

    def test_reactivating_restores_access(self):
        first = self.login().json()
        self.set_active(False)
        self.assertEqual(self.me(first["access_token"]).status_code, 401)

        self.set_active(True)

        self.assertEqual(self.me(first["access_token"]).status_code, 200)
        self.assertEqual(self.login().status_code, 200)
        refreshed = self.client.post("/users/refresh", json={"refresh_token": first["refresh_token"]})
        self.assertEqual(refreshed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
