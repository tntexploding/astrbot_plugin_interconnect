"""Tests for HTTP webhook sender helpers."""

from __future__ import annotations

import asyncio
import sys
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiohttp import web

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect.config import ProtocolConfig  # noqa: E402
from interconnect.interfaces import DispatchResult  # noqa: E402
from interconnect.local.http_webhook import (  # noqa: E402
    HttpWebhookSender,
    _retry_attempts,
    _retry_backoff_seconds,
    _should_retry,
)
from interconnect.models import (  # noqa: E402
    EndpointRef,
    MessageContent,
    MessageEnvelope,
    SenderInfo,
)
from interconnect.protocol import MessageProtocolCodec  # noqa: E402


def _webhook_envelope(url: str, **target_extra: object) -> MessageEnvelope:
    return MessageEnvelope(
        message_id="msg-1",
        direction="qq_to_local",
        source=EndpointRef(type="qq_group", id="123"),
        target=EndpointRef(
            type="http_webhook",
            id="local",
            extra={"url": url, **target_extra},
        ),
        sender=SenderInfo(id="456", group_id="123"),
        content=MessageContent(text="hello"),
        message_type="text",
        route_id="group_to_local",
    )


class HttpWebhookHelperTest(unittest.TestCase):
    """HTTP webhook helper behavior tests."""

    def test_retry_attempts_are_bounded(self) -> None:
        self.assertEqual(_retry_attempts({"retry_attempts": "3"}), 3)
        self.assertEqual(_retry_attempts({"retry_attempts": -1}), 0)
        self.assertEqual(_retry_attempts({"retry_attempts": 99}), 5)
        self.assertEqual(_retry_attempts({"retry_attempts": "bad"}), 0)

    def test_retry_backoff_is_bounded(self) -> None:
        self.assertEqual(_retry_backoff_seconds({"retry_backoff_seconds": "1.5"}), 1.5)
        self.assertEqual(_retry_backoff_seconds({"retry_backoff_seconds": 99}), 30.0)

    def test_should_retry_only_transient_errors(self) -> None:
        target = EndpointRef(type="http_webhook")
        self.assertTrue(_should_retry(DispatchResult(target, False, "HTTP 500: bad")))
        self.assertTrue(_should_retry(DispatchResult(target, False, "HTTP 429: slow")))
        self.assertFalse(_should_retry(DispatchResult(target, False, "HTTP 400: bad")))
        self.assertFalse(_should_retry(DispatchResult(target, True, "HTTP 200")))


class HttpWebhookNetworkTest(unittest.IsolatedAsyncioTestCase):
    """Release checks against a real webhook listener."""

    async def _start_webhook(
        self,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> str:
        app = web.Application()
        app.router.add_post("/hook", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="127.0.0.1", port=0)
        await site.start()
        self.addAsyncCleanup(runner.cleanup)

        aiohttp_server = getattr(site, "_server", None)
        sockets = getattr(aiohttp_server, "sockets", None)
        if not sockets:
            raise AssertionError("Webhook test server has no listening socket.")
        port = int(sockets[0].getsockname()[1])
        return f"http://127.0.0.1:{port}/hook"

    def _sender(self) -> HttpWebhookSender:
        sender = HttpWebhookSender(MessageProtocolCodec(ProtocolConfig()))
        self.addAsyncCleanup(sender.stop)
        return sender

    async def test_posts_standard_payload_with_configured_headers(self) -> None:
        received: dict[str, object] = {}

        async def handler(request: web.Request) -> web.Response:
            received["authorization"] = request.headers.get("Authorization")
            received["client"] = request.headers.get("X-Client")
            received["payload"] = await request.json()
            return web.json_response({"ok": True})

        url = await self._start_webhook(handler)
        envelope = _webhook_envelope(
            url,
            auth_token="secret",
            headers={"X-Client": "astrbot"},
        )

        result = await self._sender().send(envelope)

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "HTTP 200")
        self.assertEqual(received["authorization"], "Bearer secret")
        self.assertEqual(received["client"], "astrbot")
        payload = received["payload"]
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["content"]["text"], "hello")

    async def test_retries_transient_server_failure(self) -> None:
        attempts = 0

        async def handler(_: web.Request) -> web.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return web.Response(status=500, text="temporary")
            return web.Response(status=204)

        url = await self._start_webhook(handler)
        envelope = _webhook_envelope(
            url,
            retry_attempts=1,
            retry_backoff_seconds=0.001,
        )

        result = await self._sender().send(envelope)

        self.assertTrue(result.ok)
        self.assertEqual(attempts, 2)
        self.assertIn("attempts=2", result.message)

    async def test_timeout_is_returned_as_delivery_failure(self) -> None:
        async def handler(_: web.Request) -> web.Response:
            await asyncio.sleep(0.05)
            return web.Response(status=200)

        url = await self._start_webhook(handler)
        envelope = _webhook_envelope(url, timeout_seconds=0.01)

        result = await self._sender().send(envelope)

        self.assertFalse(result.ok)
        self.assertIn("timed out", result.message)


if __name__ == "__main__":
    unittest.main()
