"""bcrypt must not run on the event loop.

`bcrypt.hashpw`/`checkpw` take a few hundred milliseconds by design. Called straight from an
`async def` route they freeze every other request of the process for that long, so the routes run
them in a worker thread. The hash cost and the answers stay the same.
"""
import asyncio
import os
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("MAX_REQUEST_BODY_BYTES", "10485760")

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)

from fastapi import HTTPException  # noqa: E402

from routes import user as user_routes  # noqa: E402
from schemas import UserLogin, UserUpdate  # noqa: E402

TICK_SECONDS = 0.005


async def max_loop_stall(work) -> float:
    """Longest gap between two ticks of a 5 ms ticker while `work()` runs; 0 means the loop never stalled."""
    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(TICK_SECONDS)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    await asyncio.sleep(TICK_SECONDS * 3)  # let the ticker settle
    await work()
    stop.set()
    await task
    return max(gaps)


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDb:
    """Answers every query with `value`; enough for the login and update handlers."""

    def __init__(self, value):
        self._value = value

    async def execute(self, _query):
        return FakeResult(self._value)

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def fake_request():
    return SimpleNamespace(client=SimpleNamespace(host="203.0.113.7"), headers={})


def clear_limiters():
    for limiter in (user_routes.login_ip_limiter, user_routes.login_email_limiter):
        limiter._hits.clear()


class EventLoopStaysResponsive(unittest.TestCase):
    def test_async_helpers_do_not_stall_the_event_loop(self):
        hashed = user_routes.hash_password("correct-password")

        async def run():
            blocking = await max_loop_stall(
                lambda: asyncio.sleep(0, result=user_routes.verify_password("correct-password", hashed))
            )

            async def off_loop():
                self.assertTrue(await user_routes.verify_password_async("correct-password", hashed))
                self.assertFalse(await user_routes.verify_password_async("wrong-password", hashed))
                await user_routes.hash_password_async("another-password")

            responsive = await max_loop_stall(off_loop)
            return blocking, responsive

        blocking, responsive = asyncio.run(run())
        # Calling bcrypt inline freezes the loop for the whole check; off the loop it keeps ticking.
        # Relative, so a slow CI machine cannot make it flaky: one check is the lower bound of `blocking`.
        self.assertGreater(blocking, 0.05, "bcrypt check too fast to tell the difference on this machine")
        self.assertLess(responsive, blocking / 2)

    def test_hash_and_verify_keep_working_together(self):
        async def run():
            hashed = await user_routes.hash_password_async("s3cret-pass")
            self.assertTrue(hashed.startswith("$2"))
            self.assertTrue(await user_routes.verify_password_async("s3cret-pass", hashed))
            self.assertFalse(await user_routes.verify_password_async("other", hashed))
            # A malformed hash is "wrong password", not a 500 (same as the sync helper).
            self.assertFalse(await user_routes.verify_password_async("x", "not-a-bcrypt-hash"))

        asyncio.run(run())


class RoutesHashInAWorkerThread(unittest.TestCase):
    def setUp(self):
        clear_limiters()
        self.main_thread = threading.get_ident()
        self.verify_threads: list[int] = []
        self.hash_threads: list[int] = []
        self._verify = user_routes.verify_password
        self._hash = user_routes.hash_password

        def spy_verify(password, hashed):
            self.verify_threads.append(threading.get_ident())
            return self._verify(password, hashed)

        def spy_hash(password):
            self.hash_threads.append(threading.get_ident())
            return self._hash(password)

        user_routes.verify_password = spy_verify
        user_routes.hash_password = spy_hash

    def tearDown(self):
        user_routes.verify_password = self._verify
        user_routes.hash_password = self._hash
        clear_limiters()

    def login(self, db_user, email="someone@example.com", password="wrong-password"):
        payload = UserLogin(email=email, password=password)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(user_routes.login(payload, fake_request(), FakeDb(db_user)))
        return ctx.exception

    def test_login_unknown_email_still_costs_one_check_off_the_loop(self):
        error = self.login(None)
        self.assertEqual((error.status_code, error.detail), (401, "Incorrect email or password"))
        self.assertEqual(len(self.verify_threads), 1)
        self.assertNotEqual(self.verify_threads[0], self.main_thread)

    def test_login_wrong_password_costs_one_check_off_the_loop_and_same_answer(self):
        db_user = SimpleNamespace(password=self._hash("right-password"), is_active=True)
        error = self.login(db_user)
        self.assertEqual((error.status_code, error.detail), (401, "Incorrect email or password"))
        self.assertEqual(len(self.verify_threads), 1)
        self.assertNotEqual(self.verify_threads[0], self.main_thread)

    def test_update_user_verifies_and_hashes_off_the_loop(self):
        db_user = SimpleNamespace(id=1, name="a", email="a@b.com", password=self._hash("old-password"))
        payload = UserUpdate(
            name="b", email="b@b.com", current_password="old-password", password="new-password-1"
        )
        asyncio.run(user_routes.update_user(1, payload, FakeDb(db_user), SimpleNamespace(id=1)))
        self.assertEqual(len(self.verify_threads), 1)
        self.assertEqual(len(self.hash_threads), 1)
        self.assertNotEqual(self.verify_threads[0], self.main_thread)
        self.assertNotEqual(self.hash_threads[0], self.main_thread)
        self.assertTrue(self._verify("new-password-1", db_user.password))


if __name__ == "__main__":
    unittest.main()
