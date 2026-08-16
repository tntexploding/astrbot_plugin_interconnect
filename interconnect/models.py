"""Shared message models for QQ and local network adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

MessageDirection = Literal["qq_to_local", "local_to_qq"]
MessageType = Literal[
    "text",
    "image",
    "video",
    "file",
    "audio",
    "forward",
    "link",
    "mixed",
    "unknown",
]


@dataclass(frozen=True)
class EndpointRef:
    """Protocol-neutral reference to a message source or target."""

    type: str
    id: str = ""
    alias: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "alias": self.alias,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EndpointRef:
        return cls(
            type=str(data.get("type", "")),
            id=str(data.get("id", "")),
            alias=str(data.get("alias", "")),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(frozen=True)
class SenderInfo:
    """Sender metadata normalized from AstrBot events or local payloads."""

    id: str = ""
    name: str = ""
    platform: str = ""
    group_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform,
            "group_id": self.group_id,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SenderInfo:
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            platform=str(data.get("platform", "")),
            group_id=str(data.get("group_id", "")),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(frozen=True)
class MediaRef:
    """Reference to an image or other media object."""

    media_id: str
    kind: str = "image"
    source_type: str = "unknown"
    url: str = ""
    file_path: str = ""
    name: str = ""
    mime_type: str = ""
    sha256: str = ""
    size_bytes: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_id": self.media_id,
            "kind": self.kind,
            "source_type": self.source_type,
            "url": self.url,
            "file_path": self.file_path,
            "name": self.name,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MediaRef:
        return cls(
            media_id=str(data.get("media_id") or uuid4()),
            kind=str(data.get("kind", "image")),
            source_type=str(data.get("source_type", "unknown")),
            url=str(data.get("url", "")),
            file_path=str(data.get("file_path", "")),
            name=str(data.get("name") or data.get("file_name") or ""),
            mime_type=str(data.get("mime_type", "")),
            sha256=str(data.get("sha256", "")),
            size_bytes=_parse_non_negative_int(
                data.get("size_bytes") or data.get("size")
            ),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(frozen=True)
class LinkRef:
    """Normalized share link or structured JSON card."""

    kind: str = "share"
    url: str = ""
    title: str = ""
    summary: str = ""
    image_url: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "url": self.url,
            "title": self.title,
            "summary": self.summary,
            "image_url": self.image_url,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LinkRef:
        return cls(
            kind=str(data.get("kind", "share")),
            url=str(data.get("url", "")),
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            image_url=str(data.get("image_url", "")),
            payload=dict(data.get("payload") or {}),
        )


@dataclass(frozen=True)
class ForwardRef:
    """Expanded QQ merged-forward record."""

    forward_id: str = ""
    nodes: tuple[dict[str, Any], ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "forward_id": self.forward_id,
            "nodes": [dict(node) for node in self.nodes],
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ForwardRef:
        return cls(
            forward_id=str(data.get("forward_id") or data.get("id") or ""),
            nodes=tuple(
                dict(node) for node in data.get("nodes", []) if isinstance(node, dict)
            ),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(frozen=True)
class MessageContent:
    """Protocol-neutral message content."""

    text: str = ""
    images: tuple[MediaRef, ...] = ()
    videos: tuple[MediaRef, ...] = ()
    files: tuple[MediaRef, ...] = ()
    attachments: tuple[MediaRef, ...] = ()
    links: tuple[LinkRef, ...] = ()
    forwards: tuple[ForwardRef, ...] = ()
    mentions: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "images": [image.to_dict() for image in self.images],
            "videos": [video.to_dict() for video in self.videos],
            "files": [file.to_dict() for file in self.files],
            "attachments": [item.to_dict() for item in self.attachments],
            "links": [link.to_dict() for link in self.links],
            "forwards": [forward.to_dict() for forward in self.forwards],
            "mentions": list(self.mentions),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageContent:
        return cls(
            text=str(data.get("text", "")),
            images=tuple(
                MediaRef.from_dict(item)
                for item in data.get("images", [])
                if isinstance(item, dict)
            ),
            videos=tuple(
                MediaRef.from_dict(item)
                for item in data.get("videos", [])
                if isinstance(item, dict)
            ),
            files=tuple(
                MediaRef.from_dict(item)
                for item in data.get("files", [])
                if isinstance(item, dict)
            ),
            attachments=tuple(
                MediaRef.from_dict(item)
                for item in data.get("attachments", [])
                if isinstance(item, dict)
            ),
            links=tuple(
                LinkRef.from_dict(item)
                for item in data.get("links", [])
                if isinstance(item, dict)
            ),
            forwards=tuple(
                ForwardRef.from_dict(item)
                for item in data.get("forwards", [])
                if isinstance(item, dict)
            ),
            mentions=tuple(str(item) for item in data.get("mentions", [])),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(frozen=True)
class RawRefs:
    """Stable references to original platform data."""

    astrbot_message_id: str = ""
    unified_msg_origin: str = ""
    raw_message_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "astrbot_message_id": self.astrbot_message_id,
            "unified_msg_origin": self.unified_msg_origin,
            "raw_message_id": self.raw_message_id,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RawRefs:
        return cls(
            astrbot_message_id=str(data.get("astrbot_message_id", "")),
            unified_msg_origin=str(data.get("unified_msg_origin", "")),
            raw_message_id=str(data.get("raw_message_id", "")),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(frozen=True)
class MessageEnvelope:
    """Internal message packet shared by all plugin modules."""

    message_id: str
    direction: MessageDirection
    source: EndpointRef
    target: EndpointRef
    sender: SenderInfo
    content: MessageContent
    message_type: MessageType = "unknown"
    raw_refs: RawRefs = field(default_factory=RawRefs)
    route_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "direction": self.direction,
            "route_id": self.route_id,
            "message_type": self.message_type,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "sender": self.sender.to_dict(),
            "content": self.content.to_dict(),
            "raw_refs": self.raw_refs.to_dict(),
            "timestamp": self.timestamp.isoformat(),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageEnvelope:
        timestamp_value = data.get("timestamp")
        if isinstance(timestamp_value, str) and timestamp_value:
            timestamp = datetime.fromisoformat(timestamp_value)
        else:
            timestamp = datetime.now(timezone.utc)

        content = MessageContent.from_dict(data.get("content") or {})
        return cls(
            message_id=str(data.get("message_id") or uuid4()),
            direction=_parse_direction(data.get("direction")),
            route_id=str(data.get("route_id", "")),
            source=EndpointRef.from_dict(data.get("source") or {}),
            target=EndpointRef.from_dict(data.get("target") or {}),
            sender=SenderInfo.from_dict(data.get("sender") or {}),
            content=content,
            message_type=_parse_message_type(data.get("message_type"), content),
            raw_refs=RawRefs.from_dict(data.get("raw_refs") or {}),
            timestamp=timestamp,
            extra=dict(data.get("extra") or {}),
        )


def new_message_id() -> str:
    """Returns a new stable ID for locally created envelopes."""

    return str(uuid4())


def _parse_direction(value: Any) -> MessageDirection:
    if value in ("qq_to_local", "local_to_qq"):
        return value
    raise ValueError(f"Unsupported message direction: {value!r}")


def _parse_message_type(value: Any, content: MessageContent) -> MessageType:
    allowed: tuple[MessageType, ...] = (
        "text",
        "image",
        "video",
        "file",
        "audio",
        "forward",
        "link",
        "mixed",
        "unknown",
    )
    if value in allowed:
        return value

    kinds: set[MessageType] = set()
    if content.images:
        kinds.add("image")
    if content.videos:
        kinds.add("video")
    if content.files:
        kinds.add("file")
    if content.links:
        kinds.add("link")
    if content.forwards:
        kinds.add("forward")
    for media in content.attachments:
        if media.kind == "audio":
            kinds.add("audio")
    if len(kinds) > 1:
        return "mixed"
    if kinds:
        return next(iter(kinds))
    if content.text:
        return "text"
    return "unknown"


def _parse_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
