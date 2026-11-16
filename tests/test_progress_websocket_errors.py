"""The progress websocket tells "bad credentials" apart from "the server failed".

A client that gets 1008 (policy violation) concludes its token is bad and may drop the session.
A database error (for example a statement cancelled by `statement_timeout`) is not the client's
fault, so it closes with 1011 (internal error) and the client can reconnect.
"""
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("MAX_REQUEST_BODY_BYTES", "10485760")

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import main  # noqa: E402
from routes import progress  # noqa: E402

PATH = "/records/progress/ws"
POLICY_VIOLATION, INTERNAL_ERROR = 1008, 1011


class FakeDriverError(Exception):
    def __init__(self, sqlstate: str):
        super().__init__("boom: SELECT secret FROM users")
        self.sqlstate = sqlstate


class FakeSession:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


def statement_timeout() -> DBAPIError:
    return DBAPIError("SELECT secret FROM users", {"p": "value"}, FakeDriverError("57014"))


class ProgressWebsocketErrorTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        patches = [
            mock.patch.object(progress, "AsyncSessionLocal", lambda: FakeSession()),
            mock.patch.object(progress.progress_manager, "add_client", mock.AsyncMock()),
            mock.patch.object(progress.progress_manager, "remove_client", mock.AsyncMock()),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def connect_and_expect_close(self) -> int:
        with self.client.websocket_connect(PATH) as ws:
            ws.send_json({"type": "auth", "token": "any-token"})
            with self.assertRaises(WebSocketDisconnect) as ctx:
                ws.receive_json()
        return ctx.exception.code

    def test_invalid_token_is_policy_violation(self):
        failing = mock.AsyncMock(side_effect=HTTPException(status_code=401, detail="Could not validate credentials"))
        with mock.patch.object(progress, "get_user_from_access_token", failing):
            self.assertEqual(self.connect_and_expect_close(), POLICY_VIOLATION)

    def test_statement_timeout_while_authenticating_is_an_internal_error_not_a_bad_token(self):
        failing = mock.AsyncMock(side_effect=statement_timeout())
        with mock.patch.object(progress, "get_user_from_access_token", failing):
            self.assertEqual(self.connect_and_expect_close(), INTERNAL_ERROR)

    def test_missing_server_configuration_is_an_internal_error(self):
        failing = mock.AsyncMock(side_effect=RuntimeError("JWT_SECRET_KEY environment variable is required"))
        with mock.patch.object(progress, "get_user_from_access_token", failing):
            self.assertEqual(self.connect_and_expect_close(), INTERNAL_ERROR)

    def test_missing_token_is_still_policy_violation(self):
        with self.client.websocket_connect(PATH) as ws:
            ws.send_json({"type": "auth"})
            with self.assertRaises(WebSocketDisconnect) as ctx:
                ws.receive_json()
        self.assertEqual(ctx.exception.code, POLICY_VIOLATION)

    def test_statement_timeout_on_the_snapshot_closes_cleanly_and_removes_the_client(self):
        user = SimpleNamespace(id=1)
        with (
            mock.patch.object(progress, "get_user_from_access_token", mock.AsyncMock(return_value=user)),
            mock.patch.object(progress, "get_user_records_snapshot", mock.AsyncMock(side_effect=statement_timeout())),
        ):
            self.assertEqual(self.connect_and_expect_close(), INTERNAL_ERROR)
        progress.progress_manager.remove_client.assert_awaited_once()

    def test_logs_never_carry_the_sql_or_its_parameters(self):
        failing = mock.AsyncMock(side_effect=statement_timeout())
        with mock.patch.object(progress, "get_user_from_access_token", failing):
            with self.assertLogs(progress.logger, level="WARNING") as captured:
                self.connect_and_expect_close()
        text = "\n".join(captured.output)
        self.assertIn("57014", text)
        for secret in ("SELECT", "secret", "users", "value"):
            self.assertNotIn(secret, text)

    def test_valid_user_still_gets_the_snapshot(self):
        user = SimpleNamespace(id=1)
        with (
            mock.patch.object(progress, "get_user_from_access_token", mock.AsyncMock(return_value=user)),
            mock.patch.object(progress, "get_user_records_snapshot", mock.AsyncMock(return_value=[])),
        ):
            with self.client.websocket_connect(PATH) as ws:
                ws.send_json({"type": "auth", "token": "any-token"})
                self.assertEqual(ws.receive_json(), {"type": "snapshot", "records": []})


if __name__ == "__main__":
    unittest.main()
