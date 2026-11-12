"""Real HTTP through the ASGI stack (middlewares, routing, multipart), without a database.

Guards dependency upgrades (starlette, python-multipart, fastapi): unit tests call route
functions directly and would not notice a change in how requests are parsed.
"""
import os
import sys
import types
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("MAX_REQUEST_BODY_BYTES", "10485760")  # a local .env may set another value

magic_stub = types.ModuleType("magic")
magic_stub.from_buffer = lambda _file_bytes, mime=True: "image/jpeg"
sys.modules.setdefault("magic", magic_stub)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


class HttpSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # No `with`: the lifespan would try to reach the database.
        cls.client = TestClient(main.app)

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_accepts_head(self):
        # Monitores externos (UptimeRobot no plano grátis) checam com HEAD e não deixam trocar para GET.
        response = self.client.head("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    def test_protected_routes_require_a_token(self):
        for path in ("/users/me", "/records/", "/records/summary", "/analysis/", "/ibis/", "/map-zones/"):
            self.assertEqual(self.client.get(path).status_code, 401, path)

    def test_garbage_token_is_401(self):
        response = self.client.get("/users/me", headers={"Authorization": "Bearer nope"})
        self.assertEqual(response.status_code, 401)

    def test_unsupported_content_type_is_415(self):
        response = self.client.post("/users/login", content=b"email=a", headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status_code, 415)

    def test_invalid_login_body_is_422(self):
        response = self.client.post("/users/login", json={"email": "not-an-email", "password": "x"})
        self.assertEqual(response.status_code, 422)

    def test_oversized_body_is_413(self):
        from middleware import MAX_REQUEST_BODY_BYTES as limit

        response = self.client.post(
            "/users/login",
            content=b"x" * (limit + 1024),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_oversized_chunked_body_without_content_length_is_413(self):
        from middleware import MAX_REQUEST_BODY_BYTES as limit

        def chunks():
            for _ in range(limit // 65536 + 2):
                yield b"x" * 65536

        response = self.client.post(
            "/users/login", content=chunks(), headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 413)

    def test_body_under_the_limit_is_untouched(self):
        response = self.client.post("/users/login", json={"email": "not-an-email", "password": "x"})
        self.assertEqual(response.status_code, 422)  # validated, i.e. the body was read intact

    def test_multipart_upload_reaches_auth_before_parsing_files(self):
        response = self.client.post(
            "/records/upload",
            files={"images": ("a.jpg", b"\xff\xd8\xff", "image/jpeg")},
            data={"latitude_camera": "0", "longitude_camera": "0", "behavior": "voando", "date_time": "2026-01-01T00:00:00Z"},
        )
        self.assertEqual(response.status_code, 401)

    def test_docs_follow_app_env(self):
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)

    def test_public_signup_is_closed(self):
        # Guara-vivo/documents#56: accounts are created directly in the database.
        body = {"name": "x", "email": "a@b.com", "password": "12345678"}
        for path in ("/users/", "/users"):
            response = self.client.post(path, json=body)
            self.assertIn(response.status_code, (404, 405), path)
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertNotIn("post", paths.get("/users/", {}))

    def test_write_endpoints_removed_from_analysis_and_ibis(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        for prefix in ("/analysis/", "/ibis/"):
            self.assertEqual(set(paths[prefix]), {"get"}, prefix)


if __name__ == "__main__":
    unittest.main()
