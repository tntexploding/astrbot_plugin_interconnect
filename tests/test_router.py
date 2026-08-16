"""Tests for route matching."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect.config import load_plugin_config  # noqa: E402
from interconnect.models import (  # noqa: E402
    EndpointRef,
    MessageContent,
    MessageEnvelope,
    RawRefs,
    SenderInfo,
)
from interconnect.router import InterconnectRouter  # noqa: E402


class RouterTest(unittest.TestCase):
    """Router behavior tests."""

    def test_matches_enabled_route(self) -> None:
        config = load_plugin_config(
            {
                "routes": [
                    {
                        "id": "group_to_http",
                        "enabled": True,
                        "direction": "qq_to_local",
                        "source": {
                            "type": "qq_group",
                            "group_id": "123",
                        },
                        "match": {
                            "text_prefix": "!",
                        },
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
        envelope = MessageEnvelope(
            message_id="msg-1",
            direction="qq_to_local",
            source=EndpointRef(
                type="qq_group",
                id="123",
                extra={
                    "group_id": "123",
                    "user_id": "456",
                },
            ),
            target=EndpointRef(type="local"),
            sender=SenderInfo(id="456", group_id="123"),
            content=MessageContent(text="!hello"),
            raw_refs=RawRefs(unified_msg_origin="umo://qq/group/123"),
        )

        matches = InterconnectRouter(config).match(envelope)

        self.assertEqual([route.id for route in matches], ["group_to_http"])

    def test_ignores_disabled_route(self) -> None:
        config = load_plugin_config(
            {
                "routes": [
                    {
                        "id": "disabled",
                        "enabled": False,
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
        envelope = MessageEnvelope(
            message_id="msg-1",
            direction="qq_to_local",
            source=EndpointRef(type="qq_private", id="456"),
            target=EndpointRef(type="local"),
            sender=SenderInfo(id="456"),
            content=MessageContent(text="hello"),
        )

        self.assertEqual(InterconnectRouter(config).match(envelope), ())

    def test_preserves_http_webhook_target_extra(self) -> None:
        config = load_plugin_config(
            {
                "routes": [
                    {
                        "id": "qq_to_webhook",
                        "enabled": True,
                        "direction": "qq_to_local",
                        "source": {"type": "*"},
                        "targets": [
                            {
                                "type": "http_webhook",
                                "url": "http://127.0.0.1:9000/webhook",
                                "timeout_seconds": 3,
                            }
                        ],
                    }
                ]
            }
        )

        target = config.routes[0].targets[0]

        self.assertEqual(target.type, "http_webhook")
        self.assertEqual(target.extra["url"], "http://127.0.0.1:9000/webhook")
        self.assertEqual(target.extra["timeout_seconds"], 3)

    def test_matches_bound_session_alias_exactly(self) -> None:
        config = load_plugin_config(
            {
                "routes": [
                    {
                        "id": "main_group_only",
                        "enabled": True,
                        "direction": "qq_to_local",
                        "source": {"type": "*", "alias": "main_group"},
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
        envelope = MessageEnvelope(
            message_id="msg-1",
            direction="qq_to_local",
            source=EndpointRef(type="qq_group", alias="other_group"),
            target=EndpointRef(type="local"),
            sender=SenderInfo(id="456", group_id="123"),
            content=MessageContent(text="hello"),
        )

        self.assertEqual(InterconnectRouter(config).match(envelope), ())

    def test_webui_conversation_id_matches_qq_group(self) -> None:
        config = load_plugin_config(
            {
                "routes": [
                    {
                        "__template_key": "qq_to_http_webhook",
                        "id": "group_only",
                        "enabled": True,
                        "source": {
                            "type": "qq_group",
                            "conversation_id": "123",
                            "session_alias": "main_group",
                            "sender_id": "",
                        },
                        "match": {},
                        "target": {
                            "url": "http://127.0.0.1:9000/webhook",
                        },
                    }
                ]
            }
        )
        envelope = MessageEnvelope(
            message_id="msg-1",
            direction="qq_to_local",
            source=EndpointRef(
                type="qq_group",
                id="123",
                alias="main_group",
                extra={"user_id": "456"},
            ),
            target=EndpointRef(type="local"),
            sender=SenderInfo(id="456", group_id="123"),
            content=MessageContent(text="hello"),
        )

        matches = InterconnectRouter(config).match(envelope)

        self.assertEqual([route.id for route in matches], ["group_only"])

    def test_qq_conversation_matches_when_adapter_only_sets_group_extra(self) -> None:
        config = load_plugin_config(
            {
                "routes": [
                    {
                        "id": "group_only",
                        "enabled": True,
                        "direction": "qq_to_local",
                        "source": {"type": "qq_group", "id": "123"},
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
        envelope = MessageEnvelope(
            message_id="msg-1",
            direction="qq_to_local",
            source=EndpointRef(
                type="qq_group",
                id="",
                extra={"group_id": "123", "user_id": "456"},
            ),
            target=EndpointRef(type="local"),
            sender=SenderInfo(id="456", group_id="123"),
            content=MessageContent(text="hello"),
        )

        self.assertEqual(
            [route.id for route in InterconnectRouter(config).match(envelope)],
            ["group_only"],
        )

    def test_legacy_group_id_matches_when_adapter_only_sets_source_id(self) -> None:
        config = load_plugin_config(
            {
                "routes": [
                    {
                        "id": "legacy_group",
                        "enabled": True,
                        "direction": "qq_to_local",
                        "source": {"type": "qq_group", "group_id": "123"},
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
        envelope = MessageEnvelope(
            message_id="msg-1",
            direction="qq_to_local",
            source=EndpointRef(type="qq_group", id="123"),
            target=EndpointRef(type="local"),
            sender=SenderInfo(id="456", group_id="123"),
            content=MessageContent(text="hello"),
        )

        self.assertEqual(
            [route.id for route in InterconnectRouter(config).match(envelope)],
            ["legacy_group"],
        )

    def test_conversation_and_sender_restrictions_are_independent(self) -> None:
        config = load_plugin_config(
            {
                "routes": [
                    {
                        "__template_key": "qq_to_http_webhook",
                        "id": "member_only",
                        "enabled": True,
                        "source": {
                            "type": "qq_group",
                            "conversation_id": "123",
                            "session_alias": "",
                            "sender_id": "456",
                        },
                        "match": {},
                        "target": {
                            "url": "http://127.0.0.1:9000/webhook",
                        },
                    }
                ]
            }
        )
        wrong_sender = MessageEnvelope(
            message_id="msg-1",
            direction="qq_to_local",
            source=EndpointRef(
                type="qq_group",
                id="123",
                extra={"user_id": "999"},
            ),
            target=EndpointRef(type="local"),
            sender=SenderInfo(id="999", group_id="123"),
            content=MessageContent(text="hello"),
        )

        self.assertEqual(InterconnectRouter(config).match(wrong_sender), ())


if __name__ == "__main__":
    unittest.main()
