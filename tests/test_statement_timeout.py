"""The API's database engine asks Postgres to cancel any statement that runs too long.

Without it a slow query (missing index, lock, huge table) keeps a pooled connection busy
indefinitely and the pool (5 + 10) runs dry for everyone else. The limit is a Postgres session
setting sent when each connection opens (`server_settings`), configurable through
`DATABASE_STATEMENT_TIMEOUT_SECONDS`; `0` or less turns it off.

No Postgres runs in the tests, so these check what the engine would send to the server, not that
the server cancels the query.
"""
import asyncio
import os
import sys
import types
import unittest
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

import asyncpg  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

import database  # noqa: E402


class StopConnecting(Exception):
    pass


def capture_connect_kwargs(engine) -> dict:
    """What the engine passes to asyncpg.connect when it opens a connection (no server involved)."""
    seen: dict = {}

    async def fake_connect(*_args, **kwargs):
        seen.update(kwargs)
        raise StopConnecting

    async def attempt():
        try:
            async with engine.connect():
                pass
        except Exception:
            pass  # the fake refuses to connect; only the arguments matter

    with mock.patch.object(asyncpg, "connect", fake_connect):
        asyncio.run(attempt())
    return seen


class BuildConnectArgsTests(unittest.TestCase):
    def test_converts_seconds_to_the_milliseconds_postgres_expects(self):
        self.assertEqual(
            database.build_connect_args(30),
            {"server_settings": {"statement_timeout": "30000"}},
        )
        self.assertEqual(
            database.build_connect_args(5),
            {"server_settings": {"statement_timeout": "5000"}},
        )

    def test_zero_or_negative_disables_the_limit(self):
        self.assertEqual(database.build_connect_args(0), {})
        self.assertEqual(database.build_connect_args(-1), {})

    def test_default_is_30_seconds(self):
        self.assertEqual(database.DEFAULT_STATEMENT_TIMEOUT_SECONDS, 30)


class EngineSendsTheSettingTests(unittest.TestCase):
    def test_connection_startup_carries_statement_timeout(self):
        engine = create_async_engine(
            database.ASYNC_DATABASE_URL, connect_args=database.build_connect_args(7)
        )
        self.addCleanup(lambda: asyncio.run(engine.dispose()))
        kwargs = capture_connect_kwargs(engine)
        self.assertEqual(kwargs.get("server_settings"), {"statement_timeout": "7000"})

    def test_no_setting_sent_when_disabled(self):
        engine = create_async_engine(
            database.ASYNC_DATABASE_URL, connect_args=database.build_connect_args(0)
        )
        self.addCleanup(lambda: asyncio.run(engine.dispose()))
        self.assertNotIn("server_settings", capture_connect_kwargs(engine))

    def test_application_engine_uses_the_configured_value(self):
        """The module-level engine, built from the environment, is the one the API uses."""
        expected = database.build_connect_args(database.STATEMENT_TIMEOUT_SECONDS)
        kwargs = capture_connect_kwargs(database.engine)
        self.assertEqual(kwargs.get("server_settings"), expected.get("server_settings"))
        if "DATABASE_STATEMENT_TIMEOUT_SECONDS" not in os.environ:
            self.assertEqual(kwargs["server_settings"], {"statement_timeout": "30000"})


class EnvironmentValueTests(unittest.TestCase):
    def test_invalid_value_fails_fast_like_the_pool_settings(self):
        with mock.patch.dict(os.environ, {"DATABASE_STATEMENT_TIMEOUT_SECONDS": "abc"}):
            with self.assertRaises(RuntimeError):
                database.read_statement_timeout_seconds()

    def test_reads_the_variable(self):
        with mock.patch.dict(os.environ, {"DATABASE_STATEMENT_TIMEOUT_SECONDS": "12"}):
            self.assertEqual(database.read_statement_timeout_seconds(), 12)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_STATEMENT_TIMEOUT_SECONDS", None)
            self.assertEqual(database.read_statement_timeout_seconds(), 30)


class MigrationsAndListenerAreNotAffectedTests(unittest.TestCase):
    """Alembic and the progress LISTEN connection open their own connections, not the engine's."""

    def test_alembic_builds_its_own_sync_engine(self):
        env = (Path(__file__).resolve().parents[1] / "migrations" / "env.py").read_text(encoding="utf-8")
        self.assertIn("engine_from_config", env)
        self.assertNotIn("server_settings", env)

    def test_progress_listener_connects_directly(self):
        progress = (SRC / "routes" / "progress.py").read_text(encoding="utf-8")
        self.assertIn("asyncpg.connect(", progress)
        self.assertNotIn("statement_timeout", progress)


if __name__ == "__main__":
    unittest.main()
