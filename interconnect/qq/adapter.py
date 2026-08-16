"""AstrBot QQ event conversion helpers."""

from __future__ import annotations

import inspect
import json
import logging
import mimetypes
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ..models import (
    EndpointRef,
    ForwardRef,
    LinkRef,
    MediaRef,
    MessageContent,
    MessageEnvelope,
    MessageType,
    RawRefs,
    SenderInfo,
    new_message_id,
)
from ..services.media_store import MediaError, MediaStore

_LOGGER = logging.getLogger(__name__)
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_MAX_FORWARD_NODES = 100
_MAX_SEGMENTS_PER_NODE = 100
_MAX_JSON_DEPTH = 8
_MAX_JSON_ITEMS = 100
_MAX_STRING_LENGTH = 4096


async def event_to_envelope(
    event: Any, media_store: MediaStore | None = None
) -> MessageEnvelope:
    """Converts an AstrBot message event into the internal envelope format."""

    message_obj = getattr(event, "message_obj", None)
    message_id = str(getattr(message_obj, "message_id", "") or new_message_id())
    group_id = str(
        _safe_call(event, "get_group_id") or getattr(message_obj, "group_id", "") or ""
    )
    sender_id = _safe_call(event, "get_sender_id")
    sender_name = _safe_call(event, "get_sender_name")
    platform_name = _safe_call(event, "get_platform_name")
    message_chain = _safe_call(event, "get_messages", default=[])
    raw_segments = _raw_segments(message_obj)

    source_type = "qq_group" if group_id else "qq_private"
    source_id = group_id or sender_id
    images = await _extract_media(
        event, message_chain, raw_segments, message_id, "image", media_store
    )
    videos = await _extract_media(
        event, message_chain, raw_segments, message_id, "video", media_store
    )
    files = await _extract_media(
        event, message_chain, raw_segments, message_id, "file", media_store
    )
    audio = await _extract_media(
        event, message_chain, raw_segments, message_id, "audio", media_store
    )
    links = _extract_links(message_chain, raw_segments)
    forwards = await _extract_forwards(event, message_chain, raw_segments)
    content = MessageContent(
        text=_original_message_text(event, message_obj),
        images=tuple(images),
        videos=tuple(videos),
        files=tuple(files),
        attachments=tuple(audio),
        links=tuple(links),
        forwards=tuple(forwards),
        mentions=tuple(_extract_mentions(message_chain)),
        extra={"segment_types": _segment_types(message_chain, raw_segments)},
    )

    return MessageEnvelope(
        message_id=message_id,
        direction="qq_to_local",
        message_type=_classify_message(content, message_chain, raw_segments),
        source=EndpointRef(
            type=source_type,
            id=source_id,
            extra={
                "group_id": group_id,
                "user_id": sender_id,
                "platform": platform_name,
            },
        ),
        target=EndpointRef(type="local"),
        sender=SenderInfo(
            id=sender_id,
            name=sender_name,
            platform=platform_name,
            group_id=group_id,
        ),
        content=content,
        raw_refs=RawRefs(
            astrbot_message_id=message_id,
            unified_msg_origin=str(getattr(event, "unified_msg_origin", "") or ""),
            raw_message_id=message_id,
            extra={"segment_types": _segment_types(message_chain, raw_segments)},
        ),
    )


def _original_message_text(event: Any, message_obj: Any) -> str:
    """Returns QQ text before AstrBot applies wake-prefix processing.

    AstrBot's waking stage removes a configured wake prefix from
    ``event.message_str``. The message object's copy remains unchanged and is
    therefore the correct value for route matching and outbound payloads.
    """

    original_text = str(getattr(message_obj, "message_str", "") or "")
    if original_text:
        return original_text
    return str(getattr(event, "message_str", "") or "")


async def _extract_media(
    event: Any,
    message_chain: Any,
    raw_segments: list[dict[str, Any]],
    message_id: str,
    kind: str,
    media_store: MediaStore | None,
) -> list[MediaRef]:
    media: list[MediaRef] = []
    component_names = {"audio": {"record", "audio"}}.get(kind, {kind})
    if isinstance(message_chain, list):
        for component in message_chain:
            if component.__class__.__name__.lower() not in component_names:
                continue
            candidate = _media_from_component(component, message_id, kind, len(media))
            if candidate is not None:
                media.append(candidate)

    for segment in raw_segments:
        segment_type = str(segment.get("type", "")).lower()
        if segment_type not in component_names:
            continue
        data = segment.get("data")
        if not isinstance(data, Mapping):
            continue
        candidate = await _media_from_raw_segment(
            event, dict(data), message_id, kind, len(media)
        )
        if candidate is not None and not _contains_media(media, candidate):
            media.append(candidate)

    persisted: list[MediaRef] = []
    for item in media:
        if media_store is None or item.source_type == "base64":
            persisted.append(item)
            continue
        try:
            persisted.append(await media_store.persist_inbound(item))
        except MediaError as exc:
            _LOGGER.warning("Unable to persist inbound %s: %s", kind, exc)
            persisted.append(
                replace(
                    item,
                    extra={
                        **item.extra,
                        "cached": False,
                        "cache_error": str(exc),
                    },
                )
            )
    return persisted


def _media_from_component(
    component: Any, message_id: str, kind: str, index: int
) -> MediaRef | None:
    component_url = _safe_attr(component, "url")
    component_file = _safe_attr(component, "file_" if kind == "file" else "file")
    component_path = _safe_attr(component, "path")
    name = _safe_attr(component, "name")
    return _build_media(
        message_id=message_id,
        kind=kind,
        index=index,
        url=component_url,
        file_value=component_file,
        path_value=component_path,
        name=name,
        cover=_safe_attr(component, "cover"),
    )


async def _media_from_raw_segment(
    event: Any,
    data: dict[str, Any],
    message_id: str,
    kind: str,
    index: int,
) -> MediaRef | None:
    url = str(data.get("url") or "")
    if kind == "file" and not url:
        url = await _resolve_file_url(event, data)
    return _build_media(
        message_id=message_id,
        kind=kind,
        index=index,
        url=url,
        file_value=str(data.get("file") or data.get("file_path") or ""),
        path_value=str(data.get("path") or ""),
        name=str(
            data.get("file_name") or data.get("name") or data.get("filename") or ""
        ),
        cover=str(data.get("cover") or ""),
        extra={
            "file_id": str(data.get("file_id") or ""),
        },
    )


def _build_media(
    *,
    message_id: str,
    kind: str,
    index: int,
    url: str,
    file_value: str,
    path_value: str,
    name: str,
    cover: str = "",
    extra: dict[str, Any] | None = None,
) -> MediaRef | None:
    remote_url = url if _is_remote_url(url) else ""
    if not remote_url and _is_remote_url(file_value):
        remote_url = file_value
    file_path = _normalise_file_path(path_value)
    if not file_path:
        file_path = _normalise_file_path(file_value)
    resolved_name = name or _source_name(remote_url or file_path)
    source_type = "url" if remote_url else ("file" if file_path else "unknown")
    if file_value.startswith("base64://"):
        source_type = "base64"
    if not (remote_url or file_path or resolved_name):
        return None
    mime_type = mimetypes.guess_type(resolved_name or remote_url or file_path)[0] or ""
    metadata = {key: value for key, value in (extra or {}).items() if value}
    if cover:
        metadata["cover"] = cover
    if source_type == "base64":
        metadata["embedded"] = True
    return MediaRef(
        media_id=f"{message_id}-{kind}-{index}",
        kind=kind,
        source_type=source_type,
        url=remote_url,
        file_path=file_path,
        name=resolved_name,
        mime_type=mime_type,
        extra=metadata,
    )


async def _resolve_file_url(event: Any, data: dict[str, Any]) -> str:
    file_id = data.get("file_id") or data.get("id")
    bot = getattr(event, "bot", None)
    call_action = getattr(bot, "call_action", None)
    if not file_id or not callable(call_action):
        return ""
    group_id = _safe_call(event, "get_group_id")
    try:
        if group_id:
            result = await call_action(
                action="get_group_file_url", file_id=file_id, group_id=group_id
            )
        else:
            result = await call_action(action="get_private_file_url", file_id=file_id)
    except Exception as exc:
        _LOGGER.warning("Unable to resolve QQ file URL: %s", exc)
        return ""
    if isinstance(result, Mapping):
        return str(result.get("url") or "")
    return ""


def _extract_links(
    message_chain: Any, raw_segments: list[dict[str, Any]]
) -> list[LinkRef]:
    links: list[LinkRef] = []
    if isinstance(message_chain, list):
        for component in message_chain:
            component_name = component.__class__.__name__.lower()
            if component_name == "share":
                links.append(
                    LinkRef(
                        kind="share",
                        url=_safe_attr(component, "url"),
                        title=_safe_attr(component, "title"),
                        summary=_safe_attr(component, "content"),
                        image_url=_safe_attr(component, "image"),
                    )
                )
            elif component_name == "json":
                links.append(_link_from_json(_safe_value(component, "data")))

    for segment in raw_segments:
        segment_type = str(segment.get("type", "")).lower()
        data = segment.get("data")
        if not isinstance(data, Mapping):
            continue
        if segment_type == "share":
            candidate = LinkRef(
                kind="share",
                url=str(data.get("url") or ""),
                title=str(data.get("title") or ""),
                summary=str(data.get("content") or data.get("summary") or ""),
                image_url=str(data.get("image") or data.get("image_url") or ""),
            )
        elif segment_type == "json":
            candidate = _link_from_json(data.get("data") or data)
        else:
            continue
        if not _contains_link(links, candidate):
            links.append(candidate)
    return [link for link in links if link.url or link.title or link.payload]


def _link_from_json(value: Any) -> LinkRef:
    payload = _parse_json_payload(value)
    return LinkRef(
        kind="json",
        url=_find_payload_value(payload, ("url", "jumpurl", "jump_url"), True),
        title=_find_payload_value(payload, ("title", "prompt")),
        summary=_find_payload_value(
            payload, ("desc", "description", "summary", "content")
        ),
        image_url=_find_payload_value(
            payload, ("image", "image_url", "preview", "preview_url"), True
        ),
        payload=payload,
    )


async def _extract_forwards(
    event: Any, message_chain: Any, raw_segments: list[dict[str, Any]]
) -> list[ForwardRef]:
    refs: list[tuple[str, Any | None]] = []
    if isinstance(message_chain, list):
        for component in message_chain:
            component_name = component.__class__.__name__.lower()
            if component_name == "forward":
                refs.append((_safe_attr(component, "id"), None))
            elif component_name in {"node", "nodes"}:
                refs.append(("", component))
    for segment in raw_segments:
        if str(segment.get("type", "")).lower() != "forward":
            continue
        data = segment.get("data")
        if isinstance(data, Mapping):
            forward_id = str(data.get("id") or data.get("message_id") or "")
            if forward_id and all(item[0] != forward_id for item in refs):
                refs.append((forward_id, None))

    forwards: list[ForwardRef] = []
    for forward_id, component in refs:
        payload = await _forward_payload(event, forward_id, component)
        nodes = _normalise_forward_nodes(payload)
        forwards.append(
            ForwardRef(
                forward_id=forward_id,
                nodes=tuple(nodes),
                extra={
                    "expanded": bool(nodes),
                    "node_count": len(nodes),
                },
            )
        )
    return forwards


async def _forward_payload(event: Any, forward_id: str, component: Any) -> Any:
    if component is not None:
        method = getattr(component, "to_dict", None)
        if callable(method):
            try:
                result = method()
                return await result if inspect.isawaitable(result) else result
            except Exception as exc:
                _LOGGER.warning("Unable to serialize forward nodes: %s", exc)
                return {}
    bot = getattr(event, "bot", None)
    call_action = getattr(bot, "call_action", None)
    if not forward_id or not callable(call_action):
        return {}
    try:
        return await call_action(action="get_forward_msg", message_id=forward_id)
    except Exception as exc:
        _LOGGER.warning("Unable to expand QQ forward message: %s", exc)
        return {}


def _normalise_forward_nodes(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        values = payload.get("messages")
        if values is None and isinstance(payload.get("data"), Mapping):
            values = payload["data"].get("messages")
    elif isinstance(payload, list):
        values = payload
    else:
        values = None
    if not isinstance(values, list):
        return []

    nodes: list[dict[str, Any]] = []
    for item in values[:_MAX_FORWARD_NODES]:
        if not isinstance(item, Mapping):
            continue
        data = item.get("data") if isinstance(item.get("data"), Mapping) else item
        content = data.get("content") or data.get("message") or []
        segments = _forward_segments(content)
        sender = data.get("sender") if isinstance(data.get("sender"), Mapping) else {}
        nodes.append(
            {
                "sender_id": str(
                    data.get("user_id")
                    or data.get("uin")
                    or data.get("sender_id")
                    or sender.get("user_id")
                    or sender.get("uin")
                    or ""
                ),
                "sender_name": str(
                    data.get("nickname")
                    or data.get("name")
                    or data.get("sender_name")
                    or sender.get("nickname")
                    or sender.get("name")
                    or ""
                ),
                "time": _safe_int(data.get("time")),
                "text": _segment_text(segments),
                "segments": segments,
            }
        )
    return nodes


def _forward_segments(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [{"type": "text", "data": {"text": value}}]
    safe = _json_safe(value)
    return safe[:_MAX_SEGMENTS_PER_NODE] if isinstance(safe, list) else []


def _segment_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for segment in value:
        if not isinstance(segment, Mapping):
            continue
        segment_type = str(segment.get("type", "")).lower()
        data = segment.get("data")
        if not isinstance(data, Mapping):
            continue
        if segment_type in {"text", "plain"}:
            text = str(data.get("text") or "")
            if text:
                parts.append(text)
        elif segment_type == "image":
            parts.append("[image]")
        elif segment_type == "video":
            parts.append("[video]")
        elif segment_type in {"record", "audio"}:
            parts.append("[audio]")
        elif segment_type == "file":
            name = str(data.get("name") or data.get("file_name") or "")
            parts.append(f"[file: {name}]" if name else "[file]")
        elif segment_type == "at":
            qq = str(data.get("qq") or "")
            parts.append(f"@{qq}" if qq else "[mention]")
        elif segment_type == "share":
            title = str(data.get("title") or data.get("content") or "")
            url = str(data.get("url") or "")
            parts.append(title or url or "[link]")
        elif segment_type == "json":
            link = _link_from_json(data.get("data") or data)
            parts.append(link.title or link.summary or link.url or "[card]")
    return " ".join(part for part in parts if part)[:_MAX_STRING_LENGTH]


def _classify_message(
    content: MessageContent,
    message_chain: Any,
    raw_segments: list[dict[str, Any]],
) -> MessageType:
    kinds: set[MessageType] = set()
    if content.images:
        kinds.add("image")
    if content.videos:
        kinds.add("video")
    if content.files:
        kinds.add("file")
    if content.attachments:
        kinds.add("audio")
    if content.forwards:
        kinds.add("forward")
    if content.links:
        kinds.add("link")

    segment_types = _segment_types(message_chain, raw_segments)
    mapping: dict[str, MessageType] = {
        "image": "image",
        "video": "video",
        "file": "file",
        "record": "audio",
        "audio": "audio",
        "forward": "forward",
        "node": "forward",
        "nodes": "forward",
        "share": "link",
        "json": "link",
    }
    kinds.update(mapping[item] for item in segment_types if item in mapping)
    if len(kinds) > 1:
        return "mixed"
    if kinds:
        return next(iter(kinds))
    if content.text:
        return "text"
    return "unknown"


def _raw_segments(message_obj: Any) -> list[dict[str, Any]]:
    raw = getattr(message_obj, "raw_message", None)
    if not isinstance(raw, Mapping):
        return []
    message = raw.get("message")
    if not isinstance(message, list):
        return []
    return [dict(item) for item in message if isinstance(item, Mapping)]


def _segment_types(message_chain: Any, raw_segments: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    if isinstance(message_chain, list):
        values.extend(
            component.__class__.__name__.lower() for component in message_chain
        )
    values.extend(str(segment.get("type", "")).lower() for segment in raw_segments)
    return list(dict.fromkeys(item for item in values if item))


def _extract_mentions(message_chain: Any) -> list[str]:
    mentions: list[str] = []
    if not isinstance(message_chain, list):
        return mentions
    for component in message_chain:
        if component.__class__.__name__.lower() != "at":
            continue
        qq = getattr(component, "qq", "")
        if qq:
            mentions.append(str(qq))
    return mentions


def _parse_json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            match = _URL_PATTERN.search(value)
            return {
                "text": value[:_MAX_STRING_LENGTH],
                "url": match.group(0) if match else "",
            }
    safe = _json_safe(value)
    return safe if isinstance(safe, dict) else {"value": safe}


def _json_safe(value: Any, depth: int = 0) -> Any:
    if depth >= _MAX_JSON_DEPTH:
        return "[depth-limit]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_STRING_LENGTH]
    if isinstance(value, Mapping):
        return {
            str(key)[:128]: _json_safe(child, depth + 1)
            for key, child in list(value.items())[:_MAX_JSON_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(child, depth + 1) for child in value[:_MAX_JSON_ITEMS]]
    return str(value)[:_MAX_STRING_LENGTH]


def _find_payload_value(
    payload: Any, keys: tuple[str, ...], require_url: bool = False
) -> str:
    wanted = {key.lower() for key in keys}
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            for key, value in current.items():
                if str(key).lower() in wanted and isinstance(value, str):
                    if not require_url or _is_remote_url(value):
                        return value[:_MAX_STRING_LENGTH]
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
        elif require_url and isinstance(current, str):
            match = _URL_PATTERN.search(current)
            if match:
                return match.group(0)[:_MAX_STRING_LENGTH]
    return ""


def _normalise_file_path(value: str) -> str:
    if value.startswith("file:///"):
        return value.removeprefix("file:///")
    if value.startswith("file://"):
        return value.removeprefix("file://").lstrip("/")
    if value and not _is_remote_url(value) and not value.startswith("base64://"):
        if "/" in value or "\\" in value or re.match(r"^[A-Za-z]:", value):
            return value
    return ""


def _contains_media(media: list[MediaRef], candidate: MediaRef) -> bool:
    key = (candidate.kind, candidate.url, candidate.file_path, candidate.name)
    return any(
        (item.kind, item.url, item.file_path, item.name) == key for item in media
    )


def _contains_link(links: list[LinkRef], candidate: LinkRef) -> bool:
    return any(
        (item.kind, item.url, item.title)
        == (candidate.kind, candidate.url, candidate.title)
        for item in links
    )


def _is_remote_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _source_name(value: str) -> str:
    if not value:
        return ""
    normalized = value.split("?", maxsplit=1)[0].replace("\\", "/")
    return normalized.rsplit("/", maxsplit=1)[-1]


def _safe_value(component: Any, name: str) -> Any:
    try:
        return getattr(component, name, None)
    except Exception:
        return None


def _safe_attr(component: Any, name: str) -> str:
    value = _safe_value(component, name)
    return str(value or "")


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_call(event: Any, method_name: str, default: Any = "") -> Any:
    method = getattr(event, method_name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:
        return default
