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

    def refresh(self, cookie: str | None = None, body: dict | None = None, origin: str | None = None):
        headers = {}
        if cookie is not None:
            headers["Cookie"] = f"{COOKIE}={cookie}"
        if origin is not None:
            headers["Origin"] = origin
        return self.client.post("/users/refresh", json=body, headers=headers)

    def web_login_cookie(self, remember: bool = True, **who) -> str:
        return _cookie_value(self.login(client="web", remember=remember, **who))

    def mobile_login_token(self, **who) -> str:
        return self.login(**who).json()["refresh_token"]

    def update_token_rows(self, token_value: str, **changes) -> None:
        from security import hash_refresh_token

        with Session(self.sync_engine) as session:
            row = session.execute(
                select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token_value))
            ).scalar_one()
            for name, value in changes.items():
                setattr(row, name, value)
            session.commit()

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


FOREIGN_ORIGINS = (
    "https://evil.example",
    "https://api.guaravivo.com.br",
    "http://guaravivo.com.br",
    "https://guaravivo.com.br:8443",
    "null",
)


class RefreshTests(CookieSessionTestCase):
    def test_refresh_by_cookie_rotates_and_resets_cookie(self):
        old = self.web_login_cookie(remember=True)

        response = self.refresh(cookie=old)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("refresh_token", response.json())
        self.assertTrue(response.json()["access_token"])
        header = _set_cookie_header(response)
        self.assertIsNotNone(header)
        self.assertIn("Max-Age=604800", header)
        self.assertNotEqual(_cookie_value(response), old)
        self.assertEqual(self.refresh(cookie=old).status_code, 401)  # rotated: the old one is revoked

    def test_refresh_inherits_web_session_kind(self):
        old = self.web_login_cookie(remember=False)

        response = self.refresh(cookie=old)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Max-Age", _set_cookie_header(response))
        self.assertEqual(self.last_token_row().session_kind, "web_session")

    def test_mobile_refresh_by_body_unchanged(self):
        old = self.mobile_login_token()

        response = self.refresh(body={"refresh_token": old})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(_set_cookie_header(response))
        body = response.json()
        self.assertTrue(body["refresh_token"])
        self.assertNotEqual(body["refresh_token"], old)
        self.assertEqual(self.last_token_row().session_kind, "mobile")
        self.assertLess(abs(self.expires_in_seconds(self.last_token_row()) - 31536000), 10)
        self.assertEqual(self.refresh(body={"refresh_token": old}).status_code, 401)

    def test_token_row_created_before_the_migration_refreshes_as_mobile(self):
        # Rows from before migration 0019 have no session_kind: the column default makes them mobile.
        from sqlalchemy import text

        old = self.mobile_login_token()
        row = self.last_token_row()
        with Session(self.sync_engine) as session:
            session.execute(text("DELETE FROM refresh_tokens WHERE id = :id"), {"id": row.id})
            session.execute(
                text(
                    "INSERT INTO refresh_tokens (id, user_id, token_hash, jti, expires_at, created_at) "
                    "VALUES (:id, :user_id, :token_hash, :jti, :expires_at, :created_at)"
                ),
                {
                    "id": row.id,
                    "user_id": row.user_id,
                    "token_hash": row.token_hash,
                    "jti": row.jti,
                    "expires_at": row.expires_at,
                    "created_at": row.created_at,
                },
            )
            session.commit()
        self.assertEqual(self.last_token_row().session_kind, "mobile")

        response = self.refresh(body={"refresh_token": old})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(_set_cookie_header(response))
        self.assertTrue(response.json()["refresh_token"])
        self.assertEqual(self.last_token_row().session_kind, "mobile")

    def test_null_body_refresh_token_falls_back_to_the_cookie(self):
        cookie = self.web_login_cookie(remember=True)

        response = self.refresh(cookie=cookie, body={"refresh_token": None})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("refresh_token", response.json())
        self.assertIsNotNone(_set_cookie_header(response))

    def test_body_wins_over_cookie(self):
        alice_cookie = self.web_login_cookie(remember=True)
        bob_token = self.mobile_login_token(email=BOB_EMAIL, password=BOB_PASSWORD)

        response = self.refresh(cookie=alice_cookie, body={"refresh_token": bob_token})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["email"], BOB_EMAIL)
        self.assertIsNone(_set_cookie_header(response))  # kind comes from the body token (mobile)
        self.assertEqual(self.refresh(cookie=alice_cookie).status_code, 200)  # the cookie was not consumed

    def test_body_token_with_client_web_migrates_to_cookie(self):
        for remember, kind in ((True, "web"), (False, "web_session")):
            with self.subTest(remember=remember):
                old = self.mobile_login_token()

                response = self.refresh(body={"refresh_token": old, "client": "web", "remember": remember})

                self.assertEqual(response.status_code, 200)
                self.assertNotIn("refresh_token", response.json())
                header = _set_cookie_header(response)
                self.assertIsNotNone(header)
                self.assertEqual("Max-Age" in header, remember)
                self.assertEqual(self.last_token_row().session_kind, kind)

    def test_remember_in_body_does_not_change_a_cookie_token(self):
        old = self.web_login_cookie(remember=False)

        response = self.refresh(cookie=old, body={"client": "web", "remember": True})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.last_token_row().session_kind, "web_session")

    def test_foreign_origin_with_cookie_is_403_and_does_not_rotate(self):
        cookie = self.web_login_cookie()
        for origin in FOREIGN_ORIGINS:
            with self.subTest(origin=origin):
                response = self.refresh(cookie=cookie, origin=origin)
                self.assertEqual(response.status_code, 403)
                self.assertIsNone(_set_cookie_header(response))
        self.assertEqual(self.refresh(cookie=cookie, origin=PANEL_ORIGIN).status_code, 200)  # still valid

    def test_missing_origin_with_cookie_is_accepted(self):
        self.assertEqual(self.refresh(cookie=self.web_login_cookie()).status_code, 200)

    def test_foreign_origin_with_body_token_is_accepted(self):
        old = self.mobile_login_token()

        response = self.refresh(body={"refresh_token": old}, origin="https://evil.example")

        self.assertEqual(response.status_code, 200)

    def test_invalid_cookie_is_401_and_expires_cookie(self):
        revoked = self.web_login_cookie()
        self.update_token_rows(revoked, revoked_at=datetime.now(timezone.utc))
        expired = self.web_login_cookie()
        self.update_token_rows(expired, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))

        for label, value in (("unknown", "not-a-real-token"), ("revoked", revoked), ("expired", expired)):
            with self.subTest(case=label):
                response = self.refresh(cookie=value)
                self.assertEqual(response.status_code, 401)
                header = _set_cookie_header(response)
                self.assertIsNotNone(header)
                self.assertIn("Max-Age=0", header)

    def test_invalid_body_token_is_401_and_does_not_touch_the_cookie(self):
        response = self.refresh(body={"refresh_token": "not-a-real-token"})

        self.assertEqual(response.status_code, 401)
        self.assertIsNone(_set_cookie_header(response))

    def test_no_token_at_all_is_401(self):
        self.assertEqual(self.refresh().status_code, 401)
        self.assertEqual(self.refresh(body={}).status_code, 401)

    def test_empty_body_refresh_token_is_422(self):
        self.assertEqual(self.refresh(body={"refresh_token": ""}).status_code, 422)

    def test_password_change_revokes_web_tokens(self):
        login = self.login(client="web", remember=True)
        cookie, access = _cookie_value(login), login.json()["access_token"]

        updated = self.client.put(
            f"/users/{ALICE}",
            headers={"Authorization": f"Bearer {access}"},
            json={
                "name": "Alice",
                "email": ALICE_EMAIL,
                "current_password": ALICE_PASSWORD,
                "password": "alice-new-horse-1",
            },
        )
        self.assertEqual(updated.status_code, 200)
        try:
            self.assertEqual(self.refresh(cookie=cookie).status_code, 401)
        finally:
            # Put the original password back so the other tests keep working.
            with Session(self.sync_engine) as session:
                user = session.get(User, ALICE)
                user.password = user_routes.hash_password(ALICE_PASSWORD)
                session.commit()


class LogoutTests(CookieSessionTestCase):
    def logout(self, cookie: str | None = None, body: dict | None = None, origin: str | None = None):
        headers = {}
        if cookie is not None:
            headers["Cookie"] = f"{COOKIE}={cookie}"
        if origin is not None:
            headers["Origin"] = origin
        return self.client.post("/users/logout", json=body, headers=headers)

    def test_logout_by_cookie_revokes_and_expires_cookie(self):
        cookie = self.web_login_cookie()

        response = self.logout(cookie=cookie)

        self.assertEqual(response.status_code, 200)
        header = _set_cookie_header(response)
        self.assertIsNotNone(header)
        self.assertIn("Max-Age=0", header)
        self.assertEqual(self.refresh(cookie=cookie).status_code, 401)

    def test_logout_by_body_still_works_for_mobile(self):
        token = self.mobile_login_token()

        response = self.logout(body={"refresh_token": token})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(_set_cookie_header(response))
        self.assertEqual(self.refresh(body={"refresh_token": token}).status_code, 401)

    def test_logout_with_cookie_and_body_revokes_both(self):
        cookie = self.web_login_cookie()
        token = self.mobile_login_token()

        response = self.logout(cookie=cookie, body={"refresh_token": token})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.refresh(cookie=cookie).status_code, 401)
        self.assertEqual(self.refresh(body={"refresh_token": token}).status_code, 401)

    def test_logout_without_any_token_is_200(self):
        self.assertEqual(self.logout().status_code, 200)
        self.assertEqual(self.logout(body={}).status_code, 200)

    def test_logout_with_unknown_token_is_200(self):
        self.assertEqual(self.logout(body={"refresh_token": "not-a-real-token"}).status_code, 200)
        response = self.logout(cookie="not-a-real-token")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Max-Age=0", _set_cookie_header(response))

    def test_logout_with_cookie_and_foreign_origin_is_403_and_keeps_the_session(self):
        cookie = self.web_login_cookie()

        response = self.logout(cookie=cookie, origin="https://evil.example")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.refresh(cookie=cookie).status_code, 200)

    def test_empty_body_refresh_token_is_422(self):
        self.assertEqual(self.logout(body={"refresh_token": ""}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
