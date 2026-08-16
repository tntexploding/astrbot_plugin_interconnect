"""Tests for QQ event normalization."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect.qq.adapter import event_to_envelope  # noqa: E402


class Image:
    def __init__(self, file: str, url: str = "", path: str = "") -> None:
        self.file = file
        self.url = url
        self.path = path


class Video:
    def __init__(self, file: str, cover: str = "", path: str = "") -> None:
        self.file = file
        self.cover = cover
        self.path = path


class File:
    def __init__(self, name: str, file_: str = "", url: str = "") -> None:
        self.name = name
        self.file_ = file_
        self.url = url

    @property
    def file(self) -> str:
        raise AssertionError("The adapter must not access File.file")


class Share:
    def __init__(self, url: str, title: str, content: str = "") -> None:
        self.url = url
        self.title = title
        self.content = content
        self.image = ""


class Json:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


class Forward:
    def __init__(self, forward_id: str) -> None:
        self.id = forward_id


class FakeBot:
    async def call_action(self, action: str, **kwargs: Any) -> dict[str, Any]:
        if action == "get_group_file_url":
            return {"url": "https://example.com/raw-report.pdf"}
        if action == "get_forward_msg":
            self.forward_message_id = kwargs["message_id"]
            return {
                "messages": [
                    {
                        "data": {
                            "sender": {
                                "user_id": "forward-user",
                                "nickname": "Forward Sender",
                            },
                            "time": 123,
                            "content": [
                                {"type": "text", "data": {"text": "node text"}},
                                {"type": "image", "data": {"url": "temporary"}},
                                {
                                    "type": "file",
                                    "data": {"file_name": "report.pdf"},
                                },
                            ],
                        }
                    }
                ]
            }
        raise AssertionError(f"Unexpected action: {action}")


class FakeMessage:
    def __init__(self, raw_segments: list[dict[str, Any]] | None = None) -> None:
        self.message_id = "msg-1"
        self.group_id = "group-1"
        self.raw_message = {"message": raw_segments or []}


class FakeEvent:
    unified_msg_origin = "umo://qq/group/group-1"

    def __init__(
        self,
        messages: list[Any],
        raw_segments: list[dict[str, Any]] | None = None,
        text: str = "",
    ) -> None:
        self.message_obj = FakeMessage(raw_segments)
        self.message_str = text
        self.messages = messages
        self.bot = FakeBot()

    def get_sender_id(self) -> str:
        return "user-1"

    def get_sender_name(self) -> str:
        return "Alice"

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    def get_group_id(self) -> str:
        return "group-1"

    def get_messages(self) -> list[Any]:
        return self.messages


class QqAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_prefers_astrbot_group_id_accessor_for_conversation(self) -> None:
        event = FakeEvent([], text="hello")
        event.message_obj.group_id = ""

        envelope = await event_to_envelope(event)

        self.assertEqual(envelope.source.type, "qq_group")
        self.assertEqual(envelope.source.id, "group-1")
        self.assertEqual(envelope.source.extra["group_id"], "group-1")

    async def test_normalizes_image_video_and_file_without_downloading(self) -> None:
        event = FakeEvent(
            [
                Image("https://example.com/test.png"),
                Video(""),
                Video("file:///D:/media/clip.mp4", cover="cover.jpg"),
                File("report.pdf", url="https://example.com/report.pdf"),
            ]
        )
        envelope = await event_to_envelope(event)

        self.assertEqual(envelope.message_type, "mixed")
        image = envelope.content.images[0]
        self.assertEqual(image.media_id, "msg-1-image-0")
        self.assertEqual(image.kind, "image")
        self.assertEqual(image.url, "https://example.com/test.png")

        video = envelope.content.videos[0]
        self.assertEqual(video.file_path, "D:/media/clip.mp4")
        self.assertEqual(video.name, "clip.mp4")
        self.assertEqual(video.extra, {"cover": "cover.jpg"})

        file = envelope.content.files[0]
        self.assertEqual(file.url, "https://example.com/report.pdf")
        self.assertEqual(file.name, "report.pdf")

    async def test_resolves_file_dropped_from_astrbot_chain(self) -> None:
        event = FakeEvent(
            [],
            [
                {
                    "type": "file",
                    "data": {"file_id": "file-1", "file_name": "raw-report.pdf"},
                }
            ],
        )

        envelope = await event_to_envelope(event)

        self.assertEqual(envelope.message_type, "file")
        self.assertEqual(envelope.content.files[0].name, "raw-report.pdf")
        self.assertEqual(
            envelope.content.files[0].url,
            "https://example.com/raw-report.pdf",
        )

    async def test_expands_forward_records_and_structured_links(self) -> None:
        event = FakeEvent(
            [
                Forward("forward-1"),
                Share("https://example.com/share", "Shared page", "Summary"),
                Json(
                    {
                        "meta": {
                            "news": {
                                "jumpUrl": "https://example.com/card",
                                "title": "JSON card",
                            }
                        }
                    }
                ),
            ]
        )

        envelope = await event_to_envelope(event)

        self.assertEqual(envelope.message_type, "mixed")
        self.assertEqual(event.bot.forward_message_id, "forward-1")
        forward_node = envelope.content.forwards[0].nodes[0]
        self.assertEqual(forward_node["sender_id"], "forward-user")
        self.assertEqual(forward_node["sender_name"], "Forward Sender")
        self.assertEqual(forward_node["text"], "node text [image] [file: report.pdf]")
        self.assertEqual(envelope.content.links[0].title, "Shared page")
        self.assertEqual(envelope.content.links[1].url, "https://example.com/card")

    async def test_classifies_plain_text_separately_from_event_type(self) -> None:
        envelope = await event_to_envelope(FakeEvent([], text="hello"))

        self.assertEqual(envelope.message_type, "text")
        self.assertEqual(envelope.content.text, "hello")

    async def test_preserves_text_before_astrbot_removes_wake_prefix(self) -> None:
        event = FakeEvent([], text="t hello")
        event.message_obj.message_str = "/t hello"

        envelope = await event_to_envelope(event)

        self.assertEqual(envelope.content.text, "/t hello")


if __name__ == "__main__":
    unittest.main()
