"""The API's database engine asks Postgres to cancel any statement that runs too long.

Without it a slow query (missing index, lock, huge table) keeps a pooled connection busy
indefinitely and the pool (5 + 10) runs dry for everyone else. The limit is applied with
`SET statement_timeout` right after each new connection opens and then committed (SQLAlchemy rolls
back every connection it returns to the pool, which would undo an uncommitted SET). A startup
parameter (`server_settings`) is NOT used: Supabase's pooler accepts it and silently ignores it.
Configurable through `DATABASE_STATEMENT_TIMEOUT_SECONDS`; `0` or less turns it off.

No Postgres runs in the tests, so these check what the engine would send to the server, not that
the server cancels the query. That was checked by hand against the Supabase pooler (see the PR).
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

from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

import database  # noqa: E402


class FakeCursor:
    def __init__(self, log):
        self._log = log

    def execute(self, statement, *args):
        self._log.append(("execute", statement))

    def close(self):
        self._log.append(("cursor.close", None))


class FakeDbapiConnection:
    """Stands in for the connection SQLAlchemy hands to `connect` listeners."""

    def __init__(self):
        self.log: list[tuple[str, str | None]] = []

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.log.append(("commit", None))

    def await_(self, awaitable):
        # SQLAlchemy runs listeners of async pools inside a greenlet driven through this.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(awaitable)
        finally:
            loop.close()


def open_connection(listener) -> FakeDbapiConnection:
    """Run the listener on a fake connection, as the pool does for each new one."""
    connection = FakeDbapiConnection()
    if listener is not None:
        listener(connection, None)
    return connection


def new_engine(seconds: int):
    engine = create_async_engine(database.ASYNC_DATABASE_URL)
    listener = database.apply_statement_timeout(engine.sync_engine, seconds)
    return engine, listener


class StatementTimeoutSqlTests(unittest.TestCase):
    def test_converts_seconds_to_the_milliseconds_postgres_expects(self):
        self.assertEqual(database.statement_timeout_sql(30), "SET statement_timeout = 30000")
        self.assertEqual(database.statement_timeout_sql(5), "SET statement_timeout = 5000")

    def test_zero_or_negative_disables_the_limit(self):
        self.assertIsNone(database.statement_timeout_sql(0))
        self.assertIsNone(database.statement_timeout_sql(-1))

    def test_default_is_30_seconds(self):
        self.assertEqual(database.DEFAULT_STATEMENT_TIMEOUT_SECONDS, 30)

    def test_does_not_use_a_startup_parameter(self):
        """The pooler ignores startup parameters, so the limit must not depend on one."""
        self.assertFalse(hasattr(database, "build_connect_args"))


class EngineAppliesTheSettingTests(unittest.TestCase):
    def test_every_new_connection_gets_the_limit_and_commits_it(self):
        engine, listener = new_engine(7)
        self.addCleanup(lambda: asyncio.run(engine.dispose()))
        # Registered on the engine's pool, so it runs for every connection the pool opens.
        self.assertTrue(event.contains(engine.sync_engine, "connect", listener))
        # SET, then commit: the pool rolls back on return, which would undo an uncommitted SET.
        self.assertEqual(
            open_connection(listener).log,
            [("execute", "SET statement_timeout = 7000"), ("cursor.close", None), ("commit", None)],
        )

    def test_nothing_is_registered_when_disabled(self):
        engine, listener = new_engine(0)
        self.addCleanup(lambda: asyncio.run(engine.dispose()))
        self.assertIsNone(listener)
        self.assertEqual(open_connection(listener).log, [])

    def test_application_engine_uses_the_configured_value(self):
        """The module-level engine, built from the environment, is the one the API uses."""
        expected = database.statement_timeout_sql(database.STATEMENT_TIMEOUT_SECONDS)
        listener = database.statement_timeout_listener
        if expected is None:
            self.assertIsNone(listener)
            return
        self.assertTrue(event.contains(database.engine.sync_engine, "connect", listener))
        self.assertEqual(
            [entry for kind, entry in open_connection(listener).log if kind == "execute"],
            [expected],
        )
        if "DATABASE_STATEMENT_TIMEOUT_SECONDS" not in os.environ:
            self.assertEqual(expected, "SET statement_timeout = 30000")

    def test_engine_has_no_startup_parameter(self):
        """No `server_settings`: see the module docstring."""
        kwargs = database.engine.sync_engine.dialect.create_connect_args(database.engine.url)[1]
        self.assertNotIn("server_settings", kwargs)


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
