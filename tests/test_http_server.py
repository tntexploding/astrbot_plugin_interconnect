"""Tests for local HTTP payload conversion."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import aiohttp

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect.config import HttpConfig  # noqa: E402
from interconnect.errors import ConfigError  # noqa: E402
from interconnect.interfaces import DispatchResult  # noqa: E402
from interconnect.local.http_server import (  # noqa: E402
    LocalHttpServer,
    _payload_to_envelope,
)
from interconnect.models import MessageEnvelope  # noqa: E402


class FakeDispatcher:
    """Records HTTP envelopes and returns a configurable delivery result."""

    def __init__(self, delivery_ok: bool = True) -> None:
        self.delivery_ok = delivery_ok
        self.envelopes: list[MessageEnvelope] = []

    async def dispatch(self, envelope: MessageEnvelope) -> tuple[DispatchResult, ...]:
        self.envelopes.append(envelope)
        message = "sent" if self.delivery_ok else "QQ delivery failed"
        return (DispatchResult(envelope.target, self.delivery_ok, message),)


def _bound_port(server: LocalHttpServer) -> int:
    """Returns the ephemeral port selected by aiohttp for a test server."""

    site = server._site  # pylint: disable=protected-access
    aiohttp_server = getattr(site, "_server", None)
    sockets = getattr(aiohttp_server, "sockets", None)
    if not sockets:
        raise AssertionError("HTTP test server has no listening socket.")
    return int(sockets[0].getsockname()[1])


class HttpServerPayloadTest(unittest.TestCase):
    """HTTP payload conversion tests."""

    def test_missing_target_uses_routes(self) -> None:
        envelope = _payload_to_envelope(
            {
                "source": {
                    "type": "http_endpoint",
                    "extra": {
                        "endpoint_id": "alerts",
                    },
                },
                "content": {
                    "text": "hello",
                },
            }
        )

        self.assertEqual(envelope.target.type, "local")
        self.assertEqual(envelope.source.extra["endpoint_id"], "alerts")

    def test_direct_target_uses_conversation_id(self) -> None:
        envelope = _payload_to_envelope(
            {
                "target": {
                    "id": "123456",
                    "conversation_type": "qq_group",
                },
                "content": {
                    "text": "hello",
                },
            }
        )

        self.assertEqual(envelope.target.type, "qq_session")
        self.assertEqual(envelope.target.id, "123456")
        self.assertEqual(envelope.target.extra["source_type"], "qq_group")

    def test_legacy_alias_target_type_remains_compatible(self) -> None:
        envelope = _payload_to_envelope(
            {
                "target": {
                    "type": "qq_session_alias",
                    "alias": "main_group",
                },
                "content": {"text": "hello"},
            }
        )

        self.assertEqual(envelope.target.type, "qq_session")
        self.assertEqual(envelope.target.alias, "main_group")


class HttpServerNetworkTest(unittest.IsolatedAsyncioTestCase):
    """Release checks against a real local aiohttp listener."""

    async def _start_server(
        self,
        *,
        auth_token: str = "test-token",
        max_body_bytes: int = 1024 * 1024,
        delivery_ok: bool = True,
    ) -> tuple[LocalHttpServer, FakeDispatcher, aiohttp.ClientSession, str]:
        dispatcher = FakeDispatcher(delivery_ok=delivery_ok)
        server = LocalHttpServer(
            HttpConfig(
                enabled=True,
                host="127.0.0.1",
                port=0,
                auth_token=auth_token,
                max_body_bytes=max_body_bytes,
            ),
            dispatcher,
            plugin_version="v0.1.0-test",
        )
        await server.start()
        self.addAsyncCleanup(server.stop)
        client = aiohttp.ClientSession()
        self.addAsyncCleanup(client.close)
        base_url = f"http://127.0.0.1:{_bound_port(server)}"
        return server, dispatcher, client, base_url

    async def test_health_requires_configured_bearer_token(self) -> None:
        _, _, client, base_url = await self._start_server()

        response = await client.get(f"{base_url}/health")
        unauthorized = await response.json()
        self.assertEqual(response.status, 401)
        self.assertEqual(unauthorized["error"]["code"], "unauthorized")

        response = await client.get(
            f"{base_url}/health",
            headers={"Authorization": "Bearer test-token"},
        )
        health = await response.json()
        self.assertEqual(response.status, 200)
        self.assertTrue(health["ok"])
        self.assertEqual(health["version"], "v0.1.0-test")

    async def test_send_message_dispatches_direct_target(self) -> None:
        _, dispatcher, client, base_url = await self._start_server()

        response = await client.post(
            f"{base_url}/v1/messages",
            headers={"Authorization": "Bearer test-token"},
            json={
                "target": {
                    "id": "123456",
                    "conversation_type": "qq_group",
                },
                "content": {"text": "hello"},
            },
        )
        payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(dispatcher.envelopes), 1)
        self.assertEqual(dispatcher.envelopes[0].target.id, "123456")

    async def test_invalid_requests_return_structured_errors(self) -> None:
        _, dispatcher, client, base_url = await self._start_server()
        headers = {
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
        }

        response = await client.post(
            f"{base_url}/v1/messages",
            headers=headers,
            data="{",
        )
        malformed = await response.json()
        self.assertEqual(response.status, 400)
        self.assertEqual(malformed["error"]["code"], "invalid_json")

        response = await client.post(
            f"{base_url}/v1/messages",
            headers=headers,
            json={"target": [], "content": {"text": "hello"}},
        )
        invalid = await response.json()
        self.assertEqual(response.status, 400)
        self.assertEqual(invalid["error"]["code"], "invalid_request")
        self.assertEqual(dispatcher.envelopes, [])

    async def test_delivery_failure_returns_bad_gateway(self) -> None:
        _, _, client, base_url = await self._start_server(delivery_ok=False)

        response = await client.post(
            f"{base_url}/v1/messages",
            headers={"Authorization": "Bearer test-token"},
            json={
                "target": {
                    "id": "123456",
                    "conversation_type": "qq_group",
                },
                "content": {"text": "hello"},
            },
        )
        payload = await response.json()

        self.assertEqual(response.status, 502)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["results"][0]["ok"])

    async def test_body_limit_returns_payload_too_large(self) -> None:
        _, _, client, base_url = await self._start_server(max_body_bytes=128)

        response = await client.post(
            f"{base_url}/v1/messages",
            headers={"Authorization": "Bearer test-token"},
            json={"content": {"text": "x" * 1024}},
        )
        payload = await response.json()

        self.assertEqual(response.status, 413)
        self.assertEqual(payload["error"]["code"], "request_too_large")

    async def test_server_can_stop_and_restart(self) -> None:
        dispatcher = FakeDispatcher()
        server = LocalHttpServer(
            HttpConfig(enabled=True, host="127.0.0.1", port=0),
            dispatcher,
        )
        try:
            await server.start()
            self.assertTrue(server.started)
            await server.stop()
            self.assertFalse(server.started)

            await server.start()
            async with aiohttp.ClientSession() as client:
                response = await client.get(
                    f"http://127.0.0.1:{_bound_port(server)}/health"
                )
                self.assertEqual(response.status, 200)
        finally:
            await server.stop()

    async def test_non_loopback_listener_requires_authentication(self) -> None:
        server = LocalHttpServer(
            HttpConfig(enabled=True, host="0.0.0.0", port=0),
            FakeDispatcher(),
        )

        with self.assertRaises(ConfigError):
            await server.start()
        self.assertFalse(server.started)


if __name__ == "__main__":
    unittest.main()
