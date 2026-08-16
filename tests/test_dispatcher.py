"""Tests for message dispatch behavior."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect.config import load_plugin_config  # noqa: E402
from interconnect.interfaces import DispatchResult  # noqa: E402
from interconnect.models import (  # noqa: E402
    EndpointRef,
    MessageContent,
    MessageEnvelope,
    SenderInfo,
)
from interconnect.router import InterconnectRouter  # noqa: E402
from interconnect.services import DeliveryDiagnostics, MessageDispatcher  # noqa: E402


class FakeSink:
    """In-memory sink used to assert direct target delivery."""

    sink_type = "qq_session"

    def __init__(self) -> None:
        self.sent: list[MessageEnvelope] = []

    async def send(self, envelope: MessageEnvelope) -> DispatchResult:
        self.sent.append(envelope)
        return DispatchResult(target=envelope.target, ok=True, message="sent")


class DispatcherTest(unittest.IsolatedAsyncioTestCase):
    """Dispatcher behavior tests."""

    async def test_direct_target_uses_registered_sink_without_route(self) -> None:
        dispatcher = MessageDispatcher(InterconnectRouter(load_plugin_config({})))
        sink = FakeSink()
        dispatcher.register_sink(sink)
        envelope = MessageEnvelope(
            message_id="msg-1",
            direction="local_to_qq",
            source=EndpointRef(type="http_endpoint"),
            target=EndpointRef(type="qq_session", alias="main_group"),
            sender=SenderInfo(id="local"),
            content=MessageContent(text="hello"),
        )

        results = await dispatcher.dispatch(envelope)

        self.assertTrue(results[0].ok)
        self.assertEqual(sink.sent[0].target.alias, "main_group")

    async def test_records_direct_target_delivery(self) -> None:
        diagnostics = DeliveryDiagnostics(history_limit=10)
        dispatcher = MessageDispatcher(
            InterconnectRouter(load_plugin_config({})),
            diagnostics,
        )
        dispatcher.register_sink(FakeSink())
        envelope = MessageEnvelope(
            message_id="msg-1",
            direction="local_to_qq",
            source=EndpointRef(type="http_endpoint"),
            target=EndpointRef(type="qq_session", alias="main_group"),
            sender=SenderInfo(id="local"),
            content=MessageContent(text="hello"),
        )

        await dispatcher.dispatch(envelope)

        stats = diagnostics.stats()
        records = diagnostics.recent()
        self.assertEqual(stats.total, 1)
        self.assertEqual(stats.succeeded, 1)
        self.assertEqual(records[0].target_alias, "main_group")

    async def test_direct_target_takes_priority_over_fallback_route(self) -> None:
        dispatcher = MessageDispatcher(
            InterconnectRouter(
                load_plugin_config(
                    {
                        "routes": [
                            {
                                "id": "fallback",
                                "enabled": True,
                                "direction": "local_to_qq",
                                "source": {"type": "*"},
                                "targets": [
                                    {"type": "qq_session", "alias": "fallback"}
                                ],
                            }
                        ]
                    }
                )
            )
        )
        sink = FakeSink()
        dispatcher.register_sink(sink)
        envelope = MessageEnvelope(
            message_id="msg-1",
            direction="local_to_qq",
            source=EndpointRef(type="http_endpoint"),
            target=EndpointRef(type="qq_session", alias="direct"),
            sender=SenderInfo(id="local"),
            content=MessageContent(text="hello"),
        )

        await dispatcher.dispatch(envelope)

        self.assertEqual(sink.sent[0].target.alias, "direct")
        self.assertEqual(sink.sent[0].route_id, "")

    async def test_records_routed_delivery_failure(self) -> None:
        diagnostics = DeliveryDiagnostics(history_limit=10)
        dispatcher = MessageDispatcher(
            InterconnectRouter(
                load_plugin_config(
                    {
                        "routes": [
                            {
                                "id": "qq_to_http",
                                "enabled": True,
                                "direction": "qq_to_local",
                                "source": {"type": "*"},
                                "targets": [
                                    {
                                        "type": "http_webhook",
                                        "url": "http://127.0.0.1:9000/webhook",
                                    }
                                ],
                            }
                        ]
                    }
                )
            ),
            diagnostics,
        )
        envelope = MessageEnvelope(
            message_id="msg-1",
            direction="qq_to_local",
            source=EndpointRef(type="qq_group", alias="main_group"),
            target=EndpointRef(type="local"),
            sender=SenderInfo(id="local"),
            content=MessageContent(text="hello"),
        )

        await dispatcher.dispatch(envelope)

        stats = diagnostics.stats()
        failed_records = diagnostics.recent(only_failed=True)
        self.assertEqual(stats.failed, 1)
        self.assertEqual(failed_records[0].route_id, "qq_to_http")
        self.assertEqual(failed_records[0].target_type, "http_webhook")


if __name__ == "__main__":
    unittest.main()
