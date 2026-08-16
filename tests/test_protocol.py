"""Tests for local HTTP wire protocol rendering."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect.config import ProtocolConfig  # noqa: E402
from interconnect.errors import ConfigError  # noqa: E402
from interconnect.models import (  # noqa: E402
    EndpointRef,
    ForwardRef,
    LinkRef,
    MediaRef,
    MessageContent,
    MessageEnvelope,
    SenderInfo,
)
from interconnect.protocol import MessageProtocolCodec  # noqa: E402


def _envelope() -> MessageEnvelope:
    return MessageEnvelope(
        message_id="msg-1",
        direction="qq_to_local",
        source=EndpointRef(type="qq_group", id="123"),
        target=EndpointRef(type="http_webhook", id="local"),
        sender=SenderInfo(id="456", name="Alice", group_id="123"),
        content=MessageContent(text="hello", mentions=("789",)),
        message_type="text",
        route_id="qq_to_local_default",
    )


class MessageProtocolCodecTest(unittest.TestCase):
    """Wire protocol codec behavior tests."""

    def test_standard_payload_has_explicit_version(self) -> None:
        payload = MessageProtocolCodec(ProtocolConfig()).build_webhook_payload(
            _envelope()
        )

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["event_type"], "message")
        self.assertEqual(payload["message_type"], "text")
        self.assertEqual(payload["content"]["text"], "hello")

    def test_standard_payload_preserves_video_and_file_metadata(self) -> None:
        envelope = replace(
            _envelope(),
            content=MessageContent(
                videos=(
                    MediaRef(
                        media_id="video-1",
                        kind="video",
                        source_type="url",
                        url="https://example.com/clip.mp4",
                    ),
                ),
                files=(
                    MediaRef(
                        media_id="file-1",
                        kind="file",
                        source_type="url",
                        url="https://example.com/report.pdf",
                        name="report.pdf",
                    ),
                ),
            ),
        )

        payload = MessageProtocolCodec(ProtocolConfig()).build_webhook_payload(envelope)

        self.assertEqual(payload["content"]["videos"][0]["kind"], "video")
        self.assertEqual(payload["content"]["files"][0]["name"], "report.pdf")

    def test_standard_payload_preserves_links_and_expanded_forwards(self) -> None:
        envelope = replace(
            _envelope(),
            message_type="mixed",
            content=MessageContent(
                links=(
                    LinkRef(
                        kind="share",
                        url="https://example.com/page",
                        title="Shared page",
                        summary="Summary",
                    ),
                ),
                forwards=(
                    ForwardRef(
                        forward_id="forward-1",
                        nodes=(
                            {
                                "sender_id": "456",
                                "sender_name": "Alice",
                                "time": 1,
                                "text": "Forwarded text",
                                "segments": [],
                            },
                        ),
                        extra={"expanded": True, "node_count": 1},
                    ),
                ),
            ),
        )

        payload = MessageProtocolCodec(ProtocolConfig()).build_webhook_payload(envelope)

        self.assertEqual(payload["message_type"], "mixed")
        self.assertEqual(payload["content"]["links"][0]["title"], "Shared page")
        self.assertEqual(
            payload["content"]["forwards"][0]["nodes"][0]["text"],
            "Forwarded text",
        )

    def test_template_payload_preserves_json_types(self) -> None:
        codec = MessageProtocolCodec(
            ProtocolConfig(
                webhook_payload_mode="template",
                webhook_payload_template=(
                    '{"text":"${content.text}","mentions":"${content.mentions}",'
                    '"label":"QQ: ${content.text}"}'
                ),
            )
        )

        payload = codec.build_webhook_payload(_envelope())

        self.assertEqual(payload["text"], "hello")
        self.assertEqual(payload["mentions"], ["789"])
        self.assertEqual(payload["label"], "QQ: hello")

    def test_template_payload_loads_selected_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            relative_path = "files/protocol/webhook_payload_template_files/custom.json"
            template_path = root / relative_path
            template_path.parent.mkdir(parents=True)
            template_path.write_text(
                '{"sender_id":"${sender.id}","text":"${content.text}"}',
                encoding="utf-8",
            )
            codec = MessageProtocolCodec(
                ProtocolConfig(
                    webhook_payload_mode="template",
                    webhook_payload_template_files=(relative_path,),
                ),
                template_root=root,
            )

            payload = codec.build_webhook_payload(_envelope())

        self.assertEqual(payload, {"sender_id": "456", "text": "hello"})

    def test_template_payload_rejects_file_outside_plugin_data(self) -> None:
        codec = MessageProtocolCodec(
            ProtocolConfig(
                webhook_payload_mode="template",
                webhook_payload_template_files=("../outside.json",),
            ),
            template_root=Path.cwd(),
        )

        with self.assertRaisesRegex(ConfigError, "outside the plugin data"):
            codec.build_webhook_payload(_envelope())

    def test_include_extra_false_removes_nested_extension_objects(self) -> None:
        envelope = replace(
            _envelope(),
            source=EndpointRef(
                type="qq_group",
                id="123",
                extra={"adapter_field": "value"},
            ),
            content=MessageContent(text="hello", extra={"custom": True}),
            extra={"trace": "local"},
        )

        payload = MessageProtocolCodec(
            ProtocolConfig(include_extra=False)
        ).build_webhook_payload(envelope)

        self.assertNotIn("extra", payload)
        self.assertNotIn("extra", payload["source"])
        self.assertNotIn("extra", payload["content"])

    def test_standard_payload_does_not_echo_webhook_credentials(self) -> None:
        target_extra = {
            "url": "http://127.0.0.1:9000/webhook",
            "auth_token": "secret",
            "headers": {"X-Api-Key": "secret"},
        }
        envelope = replace(
            _envelope(),
            target=EndpointRef(type="http_webhook", extra=target_extra),
        )
        payload = MessageProtocolCodec(ProtocolConfig()).build_webhook_payload(envelope)

        self.assertNotIn("auth_token", payload["target"]["extra"])
        self.assertNotIn("headers", payload["target"]["extra"])
        self.assertNotIn("url", payload["target"]["extra"])


if __name__ == "__main__":
    unittest.main()
