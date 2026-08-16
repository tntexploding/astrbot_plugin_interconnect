"""Tests for delivery diagnostics."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect.interfaces import DispatchResult  # noqa: E402
from interconnect.models import (  # noqa: E402
    EndpointRef,
    MessageContent,
    MessageEnvelope,
    SenderInfo,
)
from interconnect.services import DeliveryDiagnostics  # noqa: E402


class DeliveryDiagnosticsTest(unittest.TestCase):
    """Delivery diagnostics behavior tests."""

    def test_history_limit_keeps_newest_records(self) -> None:
        diagnostics = DeliveryDiagnostics(history_limit=2)

        for index in range(3):
            envelope = MessageEnvelope(
                message_id=f"msg-{index}",
                direction="local_to_qq",
                source=EndpointRef(type="http_endpoint"),
                target=EndpointRef(type="qq_session", alias="main_group"),
                sender=SenderInfo(id="local"),
                content=MessageContent(text="hello"),
            )
            diagnostics.observe(
                envelope,
                DispatchResult(target=envelope.target, ok=True, message="sent"),
            )

        records = diagnostics.recent(limit=10)
        stats = diagnostics.stats()
        self.assertEqual([record.message_id for record in records], ["msg-2", "msg-1"])
        self.assertEqual(stats.total, 3)
        self.assertEqual(stats.retained, 2)

    def test_zero_history_still_counts_totals(self) -> None:
        diagnostics = DeliveryDiagnostics(history_limit=0)
        envelope = MessageEnvelope(
            message_id="msg-1",
            direction="local_to_qq",
            source=EndpointRef(type="http_endpoint"),
            target=EndpointRef(type="qq_session", alias="main_group"),
            sender=SenderInfo(id="local"),
            content=MessageContent(text="hello"),
        )

        diagnostics.observe(
            envelope,
            DispatchResult(target=envelope.target, ok=False, message="failed"),
        )

        self.assertEqual(diagnostics.recent(), ())
        self.assertEqual(diagnostics.stats().failed, 1)


if __name__ == "__main__":
    unittest.main()
