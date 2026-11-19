"""`DATABASE_URL` can require TLS with libpq's `?sslmode=require` and every connection still works.

The same URL feeds three clients: psycopg2 (Alembic), `asyncpg.connect(dsn)` (progress LISTEN) and
SQLAlchemy's asyncpg dialect (the API engine). The first two read `sslmode` from the URL; the dialect
passes query parameters as keywords to `asyncpg.connect`, which only knows `ssl`, so `sslmode` used to
crash the API at startup with `TypeError`. `asyncpg_engine_url` renames it for the engine only.
"""
import os
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from database import asyncpg_engine_url  # noqa: E402


def engine_connect_kwargs(url: str) -> dict:
    """What SQLAlchemy would hand to `asyncpg.connect` for this URL."""
    _args, kwargs = PGDialect_asyncpg().create_connect_args(make_url(asyncpg_engine_url(url)))
    return kwargs


class AsyncpgEngineUrlTests(unittest.TestCase):
    def test_sslmode_becomes_ssl_for_the_engine(self):
        kwargs = engine_connect_kwargs("postgresql+asyncpg://user:pw@db.example:5432/postgres?sslmode=require")

        self.assertEqual(kwargs.get("ssl"), "require")
        self.assertNotIn("sslmode", kwargs)

    def test_url_without_sslmode_is_untouched(self):
        url = "postgresql+asyncpg://user:pw@db.example:5432/postgres"

        self.assertEqual(asyncpg_engine_url(url), url)

    def test_explicit_ssl_wins_over_sslmode(self):
        kwargs = engine_connect_kwargs(
            "postgresql+asyncpg://user:pw@db.example:5432/postgres?sslmode=require&ssl=verify-full"
        )

        self.assertEqual(kwargs.get("ssl"), "verify-full")
        self.assertNotIn("sslmode", kwargs)

    def test_other_parameters_and_credentials_survive(self):
        # Supabase passwords and pooler user names (postgres.<ref>) carry characters that must stay encoded.
        url = "postgresql+asyncpg://postgres.abc:p%40ss%2Fw%3Ard@db.example:5432/postgres?sslmode=require&application_name=api"

        parsed = make_url(asyncpg_engine_url(url))

        self.assertEqual(parsed.username, "postgres.abc")
        self.assertEqual(parsed.password, "p@ss/w:rd")
        self.assertEqual(parsed.host, "db.example")
        self.assertEqual(parsed.port, 5432)
        self.assertEqual(parsed.database, "postgres")
        self.assertEqual(dict(parsed.query), {"ssl": "require", "application_name": "api"})


class LibpqUrlsKeepSslmodeTests(unittest.TestCase):
    """Alembic (psycopg2) and the LISTEN connection (asyncpg DSN) must still see `sslmode`."""

    def test_alembic_url_keeps_sslmode(self):
        from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2

        _args, kwargs = PGDialect_psycopg2().create_connect_args(
            make_url("postgresql+psycopg2://user:pw@db.example:5432/postgres?sslmode=require")
        )

        self.assertEqual(kwargs.get("sslmode"), "require")

    def test_listen_dsn_keeps_sslmode(self):
        from routes.progress import asyncpg_dsn

        dsn = asyncpg_dsn("postgresql://user:pw@db.example:5432/postgres?sslmode=require")

        self.assertEqual(dsn, "postgresql://user:pw@db.example:5432/postgres?sslmode=require")


if __name__ == "__main__":
    unittest.main()
