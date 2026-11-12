"""Panel sessions: the refresh token lives in an HttpOnly cookie, the mobile app keeps using the body.

Real HTTP through the ASGI stack against a throwaway SQLite file (only the users and refresh_tokens
tables, which have no Postgres-only column types). See
docs/superpowers/specs/2026-11-12-refresh-cookie-web-design.md.
"""
import asyncio
import os
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("MAX_REQUEST_BODY_BYTES", "10485760")

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import main  # noqa: E402
from database import get_db  # noqa: E402
from models import RefreshToken, User  # noqa: E402
from routes import user as user_routes  # noqa: E402

PANEL_ORIGIN = "https://guaravivo.com.br"
ALICE, BOB = 1, 2
ALICE_EMAIL, ALICE_PASSWORD = "alice@example.com", "alice-correct-horse"
BOB_EMAIL, BOB_PASSWORD = "bob@example.com", "bob-correct-horse"
COOKIE = "gv_refresh"


def _set_cookie_header(response) -> str | None:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{COOKIE}="):
            return header
    return None


def _cookie_value(response) -> str:
    header = _set_cookie_header(response)
    assert header is not None, "response has no gv_refresh Set-Cookie"
    return header.split(";", 1)[0].split("=", 1)[1]


class CookieSessionTestCase(unittest.TestCase):
    """Shared harness: app + SQLite with two real users, rate limiters reset for every test."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="refresh-cookie-")
        cls.db_file = Path(cls.tmpdir) / "test.db"

        cls.sync_engine = create_engine(f"sqlite:///{cls.db_file}")
        SQLModel.metadata.create_all(cls.sync_engine, tables=[User.__table__, RefreshToken.__table__])
        with Session(cls.sync_engine) as session:
            session.add_all(
                [
                    User(id=ALICE, name="Alice", email=ALICE_EMAIL, password=user_routes.hash_password(ALICE_PASSWORD)),
                    User(id=BOB, name="Bob", email=BOB_EMAIL, password=user_routes.hash_password(BOB_PASSWORD)),
                ]
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
        patcher = mock.patch.dict(os.environ, {"CORS_ORIGINS": PANEL_ORIGIN})
        patcher.start()
        self.addCleanup(patcher.stop)
        # No `with`: the lifespan would try to reach PostgreSQL. Cookies are sent by hand, never from the jar.
        self.client = TestClient(main.app)

    # --- helpers --------------------------------------------------------------------------

    def login(self, email=ALICE_EMAIL, password=ALICE_PASSWORD, **extra):
        return self.client.post("/users/login", json={"email": email, "password": password, **extra})

    def last_token_row(self, user_id: int = ALICE) -> RefreshToken:
        with Session(self.sync_engine) as session:
            return session.execute(
                select(RefreshToken).where(RefreshToken.user_id == user_id).order_by(RefreshToken.id.desc())
            ).scalars().first()

    @staticmethod
    def expires_in_seconds(row: RefreshToken) -> float:
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return (expires_at - datetime.now(timezone.utc)).total_seconds()


class WebLoginTests(CookieSessionTestCase):
    def test_web_login_with_remember_sets_persistent_cookie_and_no_refresh_token_in_body(self):
        response = self.login(client="web", remember=True)

        self.assertEqual(response.status_code, 200)
        header = _set_cookie_header(response)
        self.assertIsNotNone(header)
        self.assertIn("HttpOnly", header)
        self.assertIn("Max-Age=604800", header)
        self.assertIn("Path=/users", header)
        body = response.json()
        self.assertNotIn("refresh_token", body)
        self.assertTrue(body["access_token"])
        self.assertEqual(body["user"]["email"], ALICE_EMAIL)
        row = self.last_token_row()
        self.assertEqual(row.session_kind, "web")
        self.assertLess(abs(self.expires_in_seconds(row) - 604800), 10)

    def test_web_login_without_remember_sets_session_cookie(self):
        response = self.login(client="web", remember=False)

        self.assertEqual(response.status_code, 200)
        header = _set_cookie_header(response)
        self.assertIsNotNone(header)
        self.assertNotIn("Max-Age", header)
        self.assertNotIn("refresh_token", response.json())
        row = self.last_token_row()
        self.assertEqual(row.session_kind, "web_session")
        self.assertLess(abs(self.expires_in_seconds(row) - 86400), 10)

    def test_web_login_defaults_to_no_remember(self):
        response = self.login(client="web")

        self.assertEqual(self.last_token_row().session_kind, "web_session")
        self.assertNotIn("Max-Age", _set_cookie_header(response))

    def test_mobile_login_unchanged(self):
        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(_set_cookie_header(response))
        body = response.json()
        self.assertTrue(body["refresh_token"])
        self.assertTrue(body["access_token"])
        row = self.last_token_row()
        self.assertEqual(row.session_kind, "mobile")
        self.assertLess(abs(self.expires_in_seconds(row) - 31536000), 10)

    def test_remember_is_ignored_for_mobile(self):
        response = self.login(client="mobile", remember=True)

        self.assertIsNone(_set_cookie_header(response))
        self.assertTrue(response.json()["refresh_token"])
        self.assertEqual(self.last_token_row().session_kind, "mobile")

    def test_failed_login_sets_no_cookie(self):
        response = self.login(password="wrong-password", client="web", remember=True)

        self.assertEqual(response.status_code, 401)
        self.assertIsNone(_set_cookie_header(response))

    def test_invalid_client_value_is_422(self):
        response = self.login(client="desktop")

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
