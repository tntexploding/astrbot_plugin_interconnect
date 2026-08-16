"""Typed configuration parsing for AstrBot Interconnect."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .errors import ConfigError

_SESSION_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class QqConfig:
    """QQ-side message capture settings."""

    capture_private: bool = True
    capture_group: bool = True
    platforms: tuple[str, ...] = ()
    capture_images: bool = True
    stop_event_after_forward: bool = False


@dataclass(frozen=True)
class HttpConfig:
    """Local HTTP server settings."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    auth_token: str = ""
    max_body_bytes: int = 1024 * 1024


@dataclass(frozen=True)
class LocalConfig:
    """Local network endpoint settings."""

    http: HttpConfig = field(default_factory=HttpConfig)


@dataclass(frozen=True)
class RouteEndpoint:
    """Configured route source or target."""

    type: str
    id: str = ""
    alias: str = ""
    group_id: str = ""
    user_id: str = ""
    endpoint_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteMatch:
    """Optional matching predicates for a route."""

    text_prefix: str = ""
    regex: str = ""
    require_image: bool = False


@dataclass(frozen=True)
class RouteConfig:
    """Message route configuration."""

    id: str
    enabled: bool
    direction: str
    source: RouteEndpoint
    targets: tuple[RouteEndpoint, ...]
    match: RouteMatch = field(default_factory=RouteMatch)


@dataclass(frozen=True)
class SessionBindingConfig:
    """Editable QQ conversation address stored in AstrBot configuration."""

    alias: str
    source_type: str
    conversation_id: str = ""
    platform: str = ""
    unified_msg_origin: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class SessionConfig:
    """Automatic and user-managed QQ session mapping settings."""

    auto_record: bool = True
    migrated_from_kv: bool = False
    bindings: tuple[SessionBindingConfig, ...] = ()


@dataclass(frozen=True)
class ProtocolConfig:
    """Wire format settings for local HTTP integrations."""

    schema_version: str = "1.0"
    webhook_payload_mode: str = "standard"
    webhook_payload_template_files: tuple[str, ...] = ()
    # Kept only so existing inline-template configurations continue to load.
    webhook_payload_template: str = ""
    include_raw_refs: bool = True
    include_extra: bool = True


@dataclass(frozen=True)
class MediaConfig:
    """Media cache settings."""

    cache_enabled: bool = True
    max_cache_mb: int = 256
    expire_seconds: int = 86400
    max_image_bytes: int = 10 * 1024 * 1024
    max_media_bytes: int = 64 * 1024 * 1024
    download_timeout_seconds: int = 15
    allowed_mime_types: tuple[str, ...] = (
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    )


@dataclass(frozen=True)
class ObservabilityConfig:
    """Logging and diagnostic settings."""

    log_payload: bool = False
    log_level: str = "INFO"
    delivery_history_limit: int = 100


@dataclass(frozen=True)
class PluginConfig:
    """Validated plugin configuration snapshot."""

    enabled: bool = True
    qq: QqConfig = field(default_factory=QqConfig)
    local: LocalConfig = field(default_factory=LocalConfig)
    routes: tuple[RouteConfig, ...] = ()
    sessions: SessionConfig = field(default_factory=SessionConfig)
    protocol: ProtocolConfig = field(default_factory=ProtocolConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)


_QQ_TO_HTTP_TEMPLATE = "qq_to_http_webhook"
_LOCAL_TO_QQ_TEMPLATE = "local_to_qq_session"
_ADVANCED_ROUTE_TEMPLATE = "advanced_route"
_LEGACY_QQ_TO_WS_TEMPLATE = "qq_to_ws_topic"
_QQ_SESSION_TARGET = "qq_session"
_LEGACY_QQ_SESSION_TARGET = "qq_session_alias"
_ROUTE_TEMPLATE_KEYS = {_QQ_TO_HTTP_TEMPLATE, _LOCAL_TO_QQ_TEMPLATE}


def migrate_plugin_config(raw_config: MutableMapping[str, Any] | None) -> bool:
    """Removes obsolete fields and converts routes to supported templates.

    AstrBot's ``template_list`` editor identifies each item through
    ``__template_key``. Multi-target routes are split into independent routes;
    obsolete WebSocket targets are removed because no WebSocket runtime exists.

    Args:
        raw_config: Mutable AstrBot plugin configuration.

    Returns:
        Whether the configuration was changed.
    """

    if raw_config is None:
        return False
    changed = _remove_obsolete_config_fields(raw_config)
    routes = raw_config.get("routes")
    if not isinstance(routes, list):
        return changed

    migrated: list[Any] = []
    for index, item in enumerate(routes):
        if not isinstance(item, Mapping):
            migrated.append(item)
            continue

        route = dict(item)
        template_key = str(route.get("__template_key", "")).strip()
        if template_key in _ROUTE_TEMPLATE_KEYS:
            normalized = _normalize_webui_route(route)
            migrated.append(normalized)
            changed = changed or normalized != route
            continue
        if template_key == _LEGACY_QQ_TO_WS_TEMPLATE:
            changed = True
            continue
        if template_key == _ADVANCED_ROUTE_TEMPLATE:
            route = _advanced_route_to_legacy(route, index)
        elif template_key:
            # Keep unknown template keys intact so normal validation reports a
            # useful configuration error instead of silently deleting data.
            migrated.append(route)
            continue

        migrated.extend(_legacy_route_to_webui(route))
        changed = True

    if changed:
        raw_config["routes"] = migrated
    return changed


def _remove_obsolete_config_fields(raw_config: MutableMapping[str, Any]) -> bool:
    """Drops settings that no longer have runtime behavior or WebUI controls."""

    changed = False
    qq = raw_config.get("qq")
    if isinstance(qq, MutableMapping):
        for key in ("allow_groups", "allow_users", "block_users"):
            if key in qq:
                qq.pop(key)
                changed = True

    local = raw_config.get("local")
    if isinstance(local, MutableMapping) and "websocket" in local:
        local.pop("websocket")
        changed = True
    return changed


def _normalize_webui_route(route: dict[str, Any]) -> dict[str, Any]:
    """Upgrades and trims one supported WebUI route entry."""

    template_key = str(route.get("__template_key", "")).strip()
    common = {
        "__template_key": template_key,
        "id": route.get("id", ""),
        "enabled": route.get("enabled", True),
        "match": _legacy_match_to_webui(route.get("match")),
    }
    if template_key == _QQ_TO_HTTP_TEMPLATE:
        return {
            **common,
            "source": _qq_source_to_webui(route.get("source")),
            "target": _legacy_http_target_to_webui(_as_mapping(route.get("target"))),
        }
    if template_key == _LOCAL_TO_QQ_TEMPLATE:
        return {
            **common,
            "source": _legacy_local_source_to_webui(route.get("source")),
            "target": _legacy_qq_target_to_webui(route.get("target")),
        }
    return route


def _advanced_route_to_legacy(
    route: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    """Reads a legacy advanced route only for one-time migration."""

    return {
        "id": route.get("id", ""),
        "enabled": route.get("enabled", False),
        "direction": route.get("direction", ""),
        "source": _json_object_field(
            route.get("source_json"),
            f"routes[{index}].source_json",
        ),
        "match": _json_object_field(
            route.get("match_json"),
            f"routes[{index}].match_json",
        ),
        "targets": _json_array_field(
            route.get("targets_json"),
            f"routes[{index}].targets_json",
        ),
    }


def load_plugin_config(raw_config: Mapping[str, Any] | None) -> PluginConfig:
    """Builds a typed configuration snapshot from AstrBotConfig."""

    raw = dict(raw_config or {})
    qq = _as_mapping(raw.get("qq"))
    local = _as_mapping(raw.get("local"))
    http = _as_mapping(local.get("http"))
    sessions = _as_mapping(raw.get("sessions"))
    protocol = _as_mapping(raw.get("protocol"))
    media = _as_mapping(raw.get("media"))
    observability = _as_mapping(raw.get("observability"))

    config = PluginConfig(
        enabled=bool(raw.get("enabled", True)),
        qq=QqConfig(
            capture_private=bool(qq.get("capture_private", True)),
            capture_group=bool(qq.get("capture_group", True)),
            platforms=_str_tuple(qq.get("platforms")),
            capture_images=bool(qq.get("capture_images", True)),
            stop_event_after_forward=bool(qq.get("stop_event_after_forward", False)),
        ),
        local=LocalConfig(
            http=HttpConfig(
                enabled=bool(http.get("enabled", False)),
                host=str(http.get("host", "127.0.0.1")),
                port=_port(http.get("port", 8765), "local.http.port"),
                auth_token=str(http.get("auth_token", "")),
                max_body_bytes=_positive_int(
                    http.get("max_body_bytes", 1024 * 1024),
                    "local.http.max_body_bytes",
                ),
            ),
        ),
        routes=_parse_routes(raw.get("routes")),
        sessions=SessionConfig(
            auto_record=bool(sessions.get("auto_record", True)),
            migrated_from_kv=bool(sessions.get("migrated_from_kv", False)),
            bindings=_parse_session_bindings(sessions.get("bindings")),
        ),
        protocol=ProtocolConfig(
            schema_version=str(protocol.get("schema_version", "1.0")).strip(),
            webhook_payload_mode=str(
                protocol.get("webhook_payload_mode", "standard")
            ).strip(),
            webhook_payload_template_files=_str_tuple(
                protocol.get("webhook_payload_template_files")
            ),
            webhook_payload_template=str(protocol.get("webhook_payload_template", "")),
            include_raw_refs=bool(protocol.get("include_raw_refs", True)),
            include_extra=bool(protocol.get("include_extra", True)),
        ),
        media=MediaConfig(
            cache_enabled=bool(media.get("cache_enabled", True)),
            max_cache_mb=_positive_int(
                media.get("max_cache_mb", 256),
                "media.max_cache_mb",
            ),
            expire_seconds=_positive_int(
                media.get("expire_seconds", 86400),
                "media.expire_seconds",
            ),
            max_image_bytes=_positive_int(
                media.get("max_image_bytes", 10 * 1024 * 1024),
                "media.max_image_bytes",
            ),
            max_media_bytes=_positive_int(
                media.get("max_media_bytes", 64 * 1024 * 1024),
                "media.max_media_bytes",
            ),
            download_timeout_seconds=_positive_int(
                media.get("download_timeout_seconds", 15),
                "media.download_timeout_seconds",
            ),
            allowed_mime_types=_str_tuple(
                media.get(
                    "allowed_mime_types",
                    ["image/jpeg", "image/png", "image/gif", "image/webp"],
                )
            ),
        ),
        observability=ObservabilityConfig(
            log_payload=bool(observability.get("log_payload", False)),
            log_level=str(observability.get("log_level", "INFO")).upper(),
            delivery_history_limit=_non_negative_int(
                observability.get("delivery_history_limit", 100),
                "observability.delivery_history_limit",
            ),
        ),
    )
    _validate_config(config)
    return config


def _parse_session_bindings(value: Any) -> tuple[SessionBindingConfig, ...]:
    """Parses editable WebUI session entries into a stable configuration."""

    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise ConfigError("sessions.bindings must be a list.")

    bindings: list[SessionBindingConfig] = []
    seen_aliases: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ConfigError(f"sessions.bindings[{index}] must be an object.")
        template_key = str(item.get("__template_key", "qq_session")).strip()
        if template_key != "qq_session":
            raise ConfigError(
                f"sessions.bindings[{index}] uses unsupported template "
                f"{template_key!r}."
            )
        alias = str(item.get("alias", "")).strip()
        if not alias:
            raise ConfigError(f"sessions.bindings[{index}].alias is required.")
        if alias in seen_aliases:
            raise ConfigError(f"Duplicate session alias: {alias}.")
        seen_aliases.add(alias)
        bindings.append(
            SessionBindingConfig(
                alias=alias,
                source_type=str(item.get("source_type", "")).strip(),
                conversation_id=str(item.get("conversation_id", "")).strip(),
                platform=str(item.get("platform", "")).strip(),
                unified_msg_origin=str(item.get("unified_msg_origin", "")).strip(),
                updated_at=str(item.get("updated_at", "")).strip(),
            )
        )
    return tuple(bindings)


def _parse_routes(value: Any) -> tuple[RouteConfig, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise ConfigError("routes must be a list.")

    routes: list[RouteConfig] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ConfigError(f"routes[{index}] must be an object.")
        item = _webui_route_to_legacy(item, index)

        route_id = str(item.get("id", "")).strip()
        if not route_id:
            raise ConfigError(f"routes[{index}].id is required.")
        if route_id in seen_ids:
            raise ConfigError(f"Duplicate route id: {route_id}.")
        seen_ids.add(route_id)

        direction = str(item.get("direction", "")).strip()
        if direction not in ("qq_to_local", "local_to_qq"):
            raise ConfigError(f"routes[{index}].direction is invalid.")

        targets = item.get("targets", [])
        if not isinstance(targets, list) or not targets:
            raise ConfigError(f"routes[{index}].targets must be a non-empty list.")

        routes.append(
            RouteConfig(
                id=route_id,
                enabled=bool(item.get("enabled", True)),
                direction=direction,
                source=_parse_endpoint(item.get("source")),
                targets=tuple(_parse_endpoint(target) for target in targets),
                match=_parse_match(item.get("match")),
            )
        )
    return tuple(routes)


def _webui_route_to_legacy(
    value: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    """Converts one WebUI template entry to the canonical route shape."""

    data = dict(value)
    template_key = str(data.get("__template_key", "")).strip()
    if not template_key:
        return data
    if template_key not in _ROUTE_TEMPLATE_KEYS:
        raise ConfigError(
            f"routes[{index}] uses unsupported template {template_key!r}."
        )
    data = _normalize_webui_route(data)

    if template_key == _QQ_TO_HTTP_TEMPLATE:
        source = _webui_qq_source(data.get("source"), f"routes[{index}].source")
    else:
        source = _webui_endpoint(data.get("source"), f"routes[{index}].source")
    match = dict(_as_mapping(data.get("match")))
    target = _webui_endpoint(data.get("target"), f"routes[{index}].target")
    direction = ""
    if template_key == _QQ_TO_HTTP_TEMPLATE:
        direction = "qq_to_local"
        target["type"] = "http_webhook"
    elif template_key == _LOCAL_TO_QQ_TEMPLATE:
        direction = "local_to_qq"
        target["type"] = _QQ_SESSION_TARGET

    return {
        "id": data.get("id", ""),
        "enabled": data.get("enabled", True),
        "direction": direction,
        "source": source,
        "match": match,
        "targets": [target],
    }


def _webui_endpoint(value: Any, path: str) -> dict[str, Any]:
    """Flattens a WebUI endpoint's free-form ``extra`` object."""

    data = dict(_as_mapping(value))
    extra_value = data.pop("extra", {})
    if extra_value in (None, ""):
        extra: dict[str, Any] = {}
    elif isinstance(extra_value, Mapping):
        extra = dict(extra_value)
    else:
        raise ConfigError(f"{path}.extra must be an object.")
    extra.update(data)
    return extra


def _webui_qq_source(value: Any, path: str) -> dict[str, Any]:
    """Maps the unified QQ source form to the canonical endpoint fields."""

    source = _webui_endpoint(value, path)
    conversation_id = _select_qq_conversation_id(source)
    session_alias = _first_text(
        source.pop("session_alias", ""),
        source.pop("alias", ""),
    )
    sender_id = _first_text(
        source.pop("sender_id", ""),
        source.pop("user_id", ""),
    )

    source.pop("conversation_id", None)
    source.pop("group_id", None)
    source["id"] = conversation_id
    source["alias"] = session_alias
    source["user_id"] = sender_id
    return source


def _json_object_field(value: Any, path: str) -> dict[str, Any]:
    parsed = _json_field(value, path)
    if not isinstance(parsed, Mapping):
        raise ConfigError(f"{path} must contain a JSON object.")
    return dict(parsed)


def _json_array_field(value: Any, path: str) -> list[Any]:
    parsed = _json_field(value, path)
    if not isinstance(parsed, list):
        raise ConfigError(f"{path} must contain a JSON array.")
    return parsed


def _json_field(value: Any, path: str) -> Any:
    if isinstance(value, (Mapping, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must not be empty.")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc.msg}.") from exc


def _legacy_route_to_webui(route: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Splits one legacy route into supported single-target routes."""

    targets = route.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ConfigError("Legacy route targets must be a non-empty list.")

    direction = str(route.get("direction", "")).strip()
    converted: list[tuple[int, str, dict[str, Any]]] = []
    for index, target_value in enumerate(targets):
        if not isinstance(target_value, Mapping):
            continue
        target_type = _normalize_target_type(target_value.get("type"))
        if direction == "qq_to_local" and target_type == "http_webhook":
            converted.append(
                (
                    index,
                    _QQ_TO_HTTP_TEMPLATE,
                    _legacy_http_target_to_webui(target_value),
                )
            )
        elif direction == "local_to_qq" and target_type == _QQ_SESSION_TARGET:
            converted.append(
                (
                    index,
                    _LOCAL_TO_QQ_TEMPLATE,
                    _legacy_qq_target_to_webui(target_value),
                )
            )

    result: list[dict[str, Any]] = []
    base_id = str(route.get("id", "")).strip()
    for original_index, template_key, target in converted:
        route_id = base_id
        if len(converted) > 1:
            route_id = f"{base_id}_{original_index + 1}"
        result.append(
            {
                "__template_key": template_key,
                "id": route_id,
                "enabled": route.get("enabled", True),
                "source": (
                    _qq_source_to_webui(route.get("source"))
                    if template_key == _QQ_TO_HTTP_TEMPLATE
                    else _legacy_local_source_to_webui(route.get("source"))
                ),
                "match": _legacy_match_to_webui(route.get("match")),
                "target": target,
            }
        )
    return result


def _qq_source_to_webui(value: Any) -> dict[str, Any]:
    """Collapses synonymous legacy QQ source fields into one form value."""

    data = _flatten_legacy_endpoint(value)
    conversation_id = _select_qq_conversation_id(data)
    source_type = str(data.pop("type", "*")).strip() or "*"
    session_alias = _first_text(
        data.pop("session_alias", ""),
        data.pop("alias", ""),
    )
    sender_id = _first_text(
        data.pop("sender_id", ""),
        data.pop("user_id", ""),
    )

    return {
        "type": source_type,
        "conversation_id": conversation_id,
        "session_alias": session_alias,
        "sender_id": sender_id,
    }


def _legacy_local_source_to_webui(value: Any) -> dict[str, Any]:
    """Keeps only supported HTTP source fields during migration."""

    data = _flatten_legacy_endpoint(value)
    return {
        "type": str(data.get("type", "http_endpoint")).strip() or "http_endpoint",
        "endpoint_id": str(data.get("endpoint_id", "")).strip(),
    }


def _legacy_qq_target_to_webui(value: Any) -> dict[str, Any]:
    """Keeps the conversation identity and optional fallback alias."""

    data = _flatten_legacy_endpoint(value)
    return {
        "source_type": str(
            data.get("source_type") or data.get("conversation_type") or "qq_group"
        ).strip(),
        "id": str(data.get("id", "")).strip(),
        "alias": str(data.get("alias", "")).strip(),
    }


def _flatten_legacy_endpoint(value: Any) -> dict[str, Any]:
    """Flattens an old endpoint's optional nested ``extra`` object."""

    data = dict(_as_mapping(value))
    extra_value = data.pop("extra", {})
    extra = dict(extra_value) if isinstance(extra_value, Mapping) else {}
    extra.update(data)
    return extra


def _select_qq_conversation_id(data: Mapping[str, Any]) -> str:
    """Returns one stable conversation ID from new or legacy source fields."""

    configured = str(data.get("conversation_id", "")).strip()
    if configured:
        return configured
    source_type = str(data.get("type", "")).strip()
    source_id = str(data.get("id", "")).strip()
    group_id = str(data.get("group_id", "")).strip()
    if source_type == "qq_private":
        return source_id or group_id
    return group_id or source_id


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _legacy_match_to_webui(value: Any) -> dict[str, Any]:
    data = dict(_as_mapping(value))
    return {
        "text_prefix": str(data.get("text_prefix", "")),
        "regex": str(data.get("regex", "")),
        "require_image": bool(data.get("require_image", False)),
    }


def _legacy_http_target_to_webui(value: Mapping[str, Any]) -> dict[str, Any]:
    data = _flatten_legacy_endpoint(value)
    return {
        "id": data.get("id", ""),
        "url": data.get("url", ""),
        "timeout_seconds": data.get("timeout_seconds", 8.0),
        "retry_attempts": data.get("retry_attempts", 0),
        "retry_backoff_seconds": data.get("retry_backoff_seconds", 0.5),
        "auth_token": data.get("auth_token", ""),
        "headers": data.get("headers", {}),
    }


def _normalize_target_type(value: Any) -> str:
    target_type = str(value or "").strip()
    if target_type == _LEGACY_QQ_SESSION_TARGET:
        return _QQ_SESSION_TARGET
    return target_type


def _parse_endpoint(value: Any) -> RouteEndpoint:
    data = _as_mapping(value)
    extra_keys = set(data) - {
        "type",
        "id",
        "alias",
        "group_id",
        "user_id",
        "endpoint_id",
    }
    return RouteEndpoint(
        type=_normalize_target_type(data.get("type")),
        id=str(data.get("id", "")).strip(),
        alias=str(data.get("alias", "")).strip(),
        group_id=str(data.get("group_id", "")).strip(),
        user_id=str(data.get("user_id", "")).strip(),
        endpoint_id=str(data.get("endpoint_id", "")).strip(),
        extra={key: data[key] for key in extra_keys},
    )


def _parse_match(value: Any) -> RouteMatch:
    data = _as_mapping(value)
    return RouteMatch(
        text_prefix=str(data.get("text_prefix", "")),
        regex=str(data.get("regex", "")),
        require_image=bool(data.get("require_image", False)),
    )


def _validate_config(config: PluginConfig) -> None:
    if not config.protocol.schema_version:
        raise ConfigError("protocol.schema_version must not be empty.")
    if config.protocol.webhook_payload_mode not in ("standard", "template"):
        raise ConfigError(
            "protocol.webhook_payload_mode must be 'standard' or 'template'."
        )
    if (
        config.protocol.webhook_payload_mode == "template"
        and not config.protocol.webhook_payload_template_files
        and not config.protocol.webhook_payload_template.strip()
    ):
        raise ConfigError(
            "protocol.webhook_payload_template_files requires a JSON file in "
            "template mode."
        )
    for template_file in config.protocol.webhook_payload_template_files:
        normalized = template_file.replace("\\", "/")
        if (
            not normalized.startswith("files/protocol/webhook_payload_template_files/")
            or "/../" in f"/{normalized}/"
            or not normalized.lower().endswith(".json")
        ):
            raise ConfigError(
                "protocol.webhook_payload_template_files contains an invalid path."
            )
    if not config.media.allowed_mime_types:
        raise ConfigError("media.allowed_mime_types must not be empty.")
    if (
        config.media.cache_enabled
        and config.media.max_image_bytes > config.media.max_cache_mb * 1024 * 1024
    ):
        raise ConfigError(
            "media.max_image_bytes must not exceed the total media cache size."
        )
    if (
        config.media.cache_enabled
        and config.media.max_media_bytes > config.media.max_cache_mb * 1024 * 1024
    ):
        raise ConfigError(
            "media.max_media_bytes must not exceed the total media cache size."
        )
    if config.observability.delivery_history_limit > 1000:
        raise ConfigError("observability.delivery_history_limit must not exceed 1000.")

    for binding in config.sessions.bindings:
        if not _SESSION_ALIAS_PATTERN.fullmatch(binding.alias):
            raise ConfigError(
                f"Session alias {binding.alias!r} must be 1-64 characters and use "
                "only letters, digits, '.', '_' or '-'."
            )
        if binding.source_type not in ("qq_group", "qq_private"):
            raise ConfigError(
                f"Session {binding.alias!r} source_type must be qq_group or qq_private."
            )
        if not (binding.conversation_id or binding.unified_msg_origin):
            raise ConfigError(
                f"Session {binding.alias!r} requires conversation_id or "
                "unified_msg_origin."
            )

    for route in config.routes:
        if not route.source.type:
            raise ConfigError(f"Route {route.id} source.type is required.")
        if route.match.regex:
            try:
                re.compile(route.match.regex)
            except re.error as exc:
                raise ConfigError(
                    f"Route {route.id} match.regex is invalid: {exc!s}"
                ) from exc
        for target in route.targets:
            expected_type = (
                "http_webhook"
                if route.direction == "qq_to_local"
                else _QQ_SESSION_TARGET
            )
            if target.type != expected_type:
                raise ConfigError(
                    f"Route {route.id} direction {route.direction!r} requires "
                    f"target type {expected_type!r}."
                )
            _validate_route_target(route.id, target)


def _validate_route_target(route_id: str, target: RouteEndpoint) -> None:
    """Validates target-type specific fields before runtime dispatch."""

    if not target.type:
        raise ConfigError(f"Route {route_id} target.type is required.")

    if target.type == "http_webhook":
        url = str(target.extra.get("url", "")).strip()
        if not url:
            raise ConfigError(f"Route {route_id} http_webhook target.url is required.")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ConfigError(
                f"Route {route_id} http_webhook target.url must be http(s)."
            )
        _optional_positive_float(
            target.extra.get("timeout_seconds"),
            f"Route {route_id} http_webhook timeout_seconds",
        )
        _optional_bounded_int(
            target.extra.get("retry_attempts"),
            f"Route {route_id} http_webhook retry_attempts",
            minimum=0,
            maximum=5,
        )
        _optional_positive_float(
            target.extra.get("retry_backoff_seconds"),
            f"Route {route_id} http_webhook retry_backoff_seconds",
        )
        return

    if target.type == _QQ_SESSION_TARGET:
        if not (target.alias or target.id):
            raise ConfigError(
                f"Route {route_id} qq_session target.alias or target.id is required."
            )
        source_type = str(target.extra.get("source_type", "")).strip()
        if source_type and source_type not in ("qq_group", "qq_private"):
            raise ConfigError(
                f"Route {route_id} qq_session source_type must be qq_group or "
                "qq_private."
            )
        return

    raise ConfigError(f"Route {route_id} target type {target.type!r} is unsupported.")


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"Expected object config, got {type(value).__name__}.")
    return value


def _str_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise ConfigError("Expected a list of strings.")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _port(value: Any, path: str) -> int:
    port = _positive_int(value, path)
    if port > 65535:
        raise ConfigError(f"{path} must be between 1 and 65535.")
    return port


def _positive_int(value: Any, path: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} must be an integer.") from exc
    if number <= 0:
        raise ConfigError(f"{path} must be greater than 0.")
    return number


def _optional_positive_float(value: Any, path: str) -> None:
    if value in (None, ""):
        return
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} must be a number.") from exc
    if number <= 0:
        raise ConfigError(f"{path} must be greater than 0.")


def _optional_bounded_int(
    value: Any,
    path: str,
    *,
    minimum: int,
    maximum: int,
) -> None:
    if value in (None, ""):
        return
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} must be an integer.") from exc
    if number < minimum or number > maximum:
        raise ConfigError(f"{path} must be between {minimum} and {maximum}.")


def _non_negative_int(value: Any, path: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} must be an integer.") from exc
    if number < 0:
        raise ConfigError(f"{path} must be greater than or equal to 0.")
    return number
