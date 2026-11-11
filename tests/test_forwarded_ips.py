import asyncio
import re
import unittest
from pathlib import Path

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def forwarded_allow_ips() -> str:
    match = re.search(r"--forwarded-allow-ips=(?P<value>\S+?)\"?\s*$", _api_command(), re.M)
    assert match, "--forwarded-allow-ips not found in the api command"
    return match.group("value").strip("'\"")


def _api_command() -> str:
    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        if "uvicorn" in line and "--forwarded-allow-ips" in line:
            return line
    raise AssertionError("uvicorn command with --forwarded-allow-ips not found in docker-compose.yml")


async def client_seen_by_app(trusted: str, peer_ip: str, forwarded_for: str) -> str:
    """Runs a request through uvicorn's proxy-headers middleware and returns the client host the app sees."""
    seen = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"][0]

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=trusted)
    scope = {
        "type": "http",
        "client": (peer_ip, 50000),
        "headers": [(b"x-forwarded-for", forwarded_for.encode())],
    }
    await middleware(scope, None, None)
    return seen["client"]


class ForwardedAllowIpsTests(unittest.TestCase):
    def test_compose_does_not_trust_every_origin(self):
        value = forwarded_allow_ips()
        self.assertNotIn("*", value)
        self.assertTrue(value, "the trusted proxy list must not be empty")

    def test_forwarded_header_is_honored_from_the_docker_gateway(self):
        # Caddy runs on the host and reaches the container through the Docker bridge gateway (172.18.0.1 in prod).
        client = asyncio.run(client_seen_by_app(forwarded_allow_ips(), "172.18.0.1", "203.0.113.7"))
        self.assertEqual(client, "203.0.113.7")

    def test_forwarded_header_is_ignored_from_a_public_address(self):
        # Someone who reaches the API directly must not be able to pick their own rate-limit key.
        client = asyncio.run(client_seen_by_app(forwarded_allow_ips(), "198.51.100.20", "203.0.113.7"))
        self.assertEqual(client, "198.51.100.20")

    def test_each_client_keeps_its_own_address_behind_the_proxy(self):
        first = asyncio.run(client_seen_by_app(forwarded_allow_ips(), "172.18.0.1", "203.0.113.7"))
        second = asyncio.run(client_seen_by_app(forwarded_allow_ips(), "172.18.0.1", "203.0.113.8"))
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
