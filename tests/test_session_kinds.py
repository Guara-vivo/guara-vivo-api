"""Session kinds for refresh tokens: mobile (1 year, body) vs web (7 days / 24 h, cookie).

See docs/superpowers/specs/2026-11-12-refresh-cookie-web-design.md.
"""
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)

from unittest import mock  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import Response  # noqa: E402

import refresh_cookie  # noqa: E402
import security  # noqa: E402
from models import RefreshToken  # noqa: E402


class SessionKindTtlTests(unittest.TestCase):
    def test_mobile_ttl_is_the_existing_refresh_setting(self):
        self.assertEqual(security.refresh_ttl_seconds("mobile"), security.JWT_REFRESH_TOKEN_EXPIRE)

    def test_web_ttl_defaults_to_7_days(self):
        self.assertEqual(security.refresh_ttl_seconds("web"), 604800)

    def test_web_session_ttl_defaults_to_24_hours(self):
        self.assertEqual(security.refresh_ttl_seconds("web_session"), 86400)

    def test_unknown_kind_raises_value_error(self):
        with self.assertRaises(ValueError):
            security.refresh_ttl_seconds("desktop")

    def test_kind_constants(self):
        self.assertEqual(security.SESSION_KIND_MOBILE, "mobile")
        self.assertEqual(security.SESSION_KIND_WEB, "web")
        self.assertEqual(security.SESSION_KIND_WEB_SESSION, "web_session")
        self.assertEqual(security.WEB_SESSION_KINDS, ("web", "web_session"))

    def test_create_refresh_token_expiry_follows_kind(self):
        for kind in ("mobile", "web", "web_session"):
            with self.subTest(kind=kind):
                _token, _jti, expires_at = security.create_refresh_token(kind)
                expected = datetime.now(timezone.utc) + timedelta(seconds=security.refresh_ttl_seconds(kind))
                self.assertLess(abs((expires_at - expected).total_seconds()), 5)

    def test_create_refresh_token_defaults_to_mobile(self):
        _token, _jti, expires_at = security.create_refresh_token()
        expected = datetime.now(timezone.utc) + timedelta(seconds=security.JWT_REFRESH_TOKEN_EXPIRE)
        self.assertLess(abs((expires_at - expected).total_seconds()), 5)


class RefreshTokenModelTests(unittest.TestCase):
    def test_refresh_token_model_defaults_to_mobile(self):
        token = RefreshToken(
            user_id=1,
            token_hash="h",
            jti="j",
            expires_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        self.assertEqual(token.session_kind, "mobile")


def _set_cookie(response: Response) -> str:
    return response.headers.getlist("set-cookie")[0]


class RefreshCookieTests(unittest.TestCase):
    def test_web_cookie_attributes(self):
        response = Response()
        refresh_cookie.set_refresh_cookie(response, "tok123", "web")
        cookie = _set_cookie(response)

        self.assertIn("gv_refresh=tok123", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Path=/users", cookie)
        self.assertIn("Max-Age=604800", cookie)
        self.assertNotIn("Domain", cookie)

    def test_web_session_cookie_has_no_max_age(self):
        response = Response()
        refresh_cookie.set_refresh_cookie(response, "tok123", "web_session")
        cookie = _set_cookie(response)

        self.assertNotIn("Max-Age", cookie)
        self.assertNotIn("expires", cookie.lower())

    def test_secure_flag_follows_env(self):
        with mock.patch.dict(os.environ, {"REFRESH_COOKIE_SECURE": "false"}):
            response = Response()
            refresh_cookie.set_refresh_cookie(response, "t", "web")
            self.assertNotIn("Secure", _set_cookie(response))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REFRESH_COOKIE_SECURE", None)
            response = Response()
            refresh_cookie.set_refresh_cookie(response, "t", "web")
            self.assertIn("Secure", _set_cookie(response))

    def test_clear_cookie_expires_it(self):
        response = Response()
        refresh_cookie.clear_refresh_cookie(response)
        cookie = _set_cookie(response)

        self.assertIn("gv_refresh=", cookie)
        self.assertIn("Max-Age=0", cookie)
        self.assertIn("Path=/users", cookie)
        self.assertIn("HttpOnly", cookie)


def _request(origin: str | None) -> Request:
    headers = [] if origin is None else [(b"origin", origin.encode())]
    return Request({"type": "http", "method": "POST", "path": "/users/refresh", "headers": headers})


class RejectForeignOriginTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"CORS_ORIGINS": "https://guaravivo.com.br"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_origin_allowed_passes(self):
        refresh_cookie.reject_foreign_origin(_request("https://guaravivo.com.br"))

    def test_origin_missing_passes(self):
        refresh_cookie.reject_foreign_origin(_request(None))

    def test_origin_foreign_is_403(self):
        for origin in (
            "https://evil.example",
            "https://api.guaravivo.com.br",
            "http://guaravivo.com.br",
            "https://guaravivo.com.br:8443",
            "null",
        ):
            with self.subTest(origin=origin):
                with self.assertRaises(HTTPException) as caught:
                    refresh_cookie.reject_foreign_origin(_request(origin))
                self.assertEqual(caught.exception.status_code, 403)

    def test_get_cors_origins_parses_comma_list(self):
        with mock.patch.dict(os.environ, {"CORS_ORIGINS": " https://a.example , https://b.example ,"}):
            self.assertEqual(refresh_cookie.get_cors_origins(), ["https://a.example", "https://b.example"])


if __name__ == "__main__":
    unittest.main()
