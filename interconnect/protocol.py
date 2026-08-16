"""Versioned JSON wire protocol and safe webhook payload templates."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import ProtocolConfig
from .errors import ConfigError
from .models import MessageEnvelope

_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Za-z0-9_.]+)\}")
_EXACT_PLACEHOLDER_PATTERN = re.compile(r"^\$\{([A-Za-z0-9_.]+)\}$")
_TARGET_CONFIG_KEYS = {
    "auth_token",
    "headers",
    "retry_attempts",
    "retry_backoff_seconds",
    "timeout_seconds",
    "url",
}
_PLUGIN_NAME = "astrbot_plugin_interconnect"
_MAX_TEMPLATE_BYTES = 1024 * 1024


class MessageProtocolCodec:
    """Builds stable or template-based JSON payloads for local integrations."""

    def __init__(
        self,
        config: ProtocolConfig,
        template_root: Path | None = None,
    ) -> None:
        self._config = config
        self._template_root = template_root

    def build_webhook_payload(
        self,
        envelope: MessageEnvelope,
    ) -> dict[str, Any]:
        """Builds a webhook JSON object using the global protocol settings."""

        standard_payload = self._build_standard_payload(envelope)
        mode = self._config.webhook_payload_mode
        if mode == "standard":
            return standard_payload
        if mode != "template":
            raise ConfigError(f"Unsupported webhook payload mode: {mode!r}.")

        template = self._config.webhook_payload_template
        if template:
            # Compatibility for configurations created before file-based
            # templates were introduced. This field is hidden in the WebUI.
            template_value = _load_inline_template(template)
        else:
            template_file = ""
            if self._config.webhook_payload_template_files:
                template_file = self._config.webhook_payload_template_files[0]
            template_value = _load_template_file(
                template_file,
                self._template_root or _default_template_root(),
            )
        context = deepcopy(standard_payload)
        context["envelope"] = deepcopy(standard_payload)
        rendered = _render_template(template_value, context)
        if not isinstance(rendered, dict):
            raise ConfigError("Webhook payload template must render to a JSON object.")
        return rendered

    def _build_standard_payload(
        self,
        envelope: MessageEnvelope,
    ) -> dict[str, Any]:
        payload = envelope.to_dict()
        payload["schema_version"] = self._config.schema_version
        payload["event_type"] = "message"
        target_extra_payload = payload.get("target", {}).get("extra")
        if isinstance(target_extra_payload, dict):
            for key in _TARGET_CONFIG_KEYS:
                target_extra_payload.pop(key, None)
        if not self._config.include_raw_refs:
            payload.pop("raw_refs", None)
        if not self._config.include_extra:
            _remove_extra_fields(payload)
        return payload


def _load_inline_template(template: Any) -> Any:
    if isinstance(template, dict):
        return deepcopy(template)
    if not isinstance(template, str) or not template.strip():
        raise ConfigError("Webhook payload template is empty.")
    try:
        return json.loads(template)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Webhook payload template is not valid JSON: {exc.msg}."
        ) from exc


def _load_template_file(relative_path: str, template_root: Path) -> Any:
    """Loads one WebUI-selected JSON file inside the plugin data directory."""

    if not relative_path:
        raise ConfigError("Webhook payload template file is not selected.")
    normalized = relative_path.replace("\\", "/")
    if not normalized.lower().endswith(".json"):
        raise ConfigError("Webhook payload template file must use a .json extension.")

    root = template_root.resolve()
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigError(
            "Webhook payload template file is outside the plugin data directory."
        ) from exc
    try:
        if not candidate.is_file():
            raise ConfigError(
                f"Webhook payload template file does not exist: {normalized}."
            )
        if candidate.stat().st_size > _MAX_TEMPLATE_BYTES:
            raise ConfigError("Webhook payload template file exceeds 1 MiB.")
        raw_template = candidate.read_text(encoding="utf-8-sig")
    except ConfigError:
        raise
    except OSError as exc:
        raise ConfigError(
            f"Unable to read webhook payload template file: {exc!s}."
        ) from exc

    try:
        template = json.loads(raw_template)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Webhook payload template file is not valid JSON: {exc.msg}."
        ) from exc
    if not isinstance(template, dict):
        raise ConfigError("Webhook payload template file must contain a JSON object.")
    return template


def _default_template_root() -> Path:
    from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

    return Path(get_astrbot_plugin_data_path()) / _PLUGIN_NAME


def _remove_extra_fields(value: Any) -> None:
    """Removes protocol extension objects recursively in place."""

    if isinstance(value, dict):
        value.pop("extra", None)
        for child in value.values():
            _remove_extra_fields(child)
    elif isinstance(value, list):
        for child in value:
            _remove_extra_fields(child)


def _render_template(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _render_template(child, context) for key, child in value.items()
        }
    if isinstance(value, list):
        return [_render_template(child, context) for child in value]
    if not isinstance(value, str):
        return value

    exact_match = _EXACT_PLACEHOLDER_PATTERN.fullmatch(value)
    if exact_match:
        return deepcopy(_lookup(context, exact_match.group(1)))

    def replace(match: re.Match[str]) -> str:
        resolved = _lookup(context, match.group(1))
        if isinstance(resolved, (dict, list)):
            return json.dumps(resolved, ensure_ascii=False, separators=(",", ":"))
        return str(resolved)

    return _PLACEHOLDER_PATTERN.sub(replace, value)


def _lookup(context: dict[str, Any], dotted_path: str) -> Any:
    value: Any = context
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ConfigError(f"Unknown webhook template placeholder: {dotted_path}.")
        value = value[part]
    return value
