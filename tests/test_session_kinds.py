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


if __name__ == "__main__":
    unittest.main()
