"""Local aiohttp server for sending messages into AstrBot."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from ..config import HttpConfig
from ..errors import ConfigError
from ..models import (
    EndpointRef,
    MessageContent,
    MessageEnvelope,
    SenderInfo,
    new_message_id,
)
from ..services import MessageDispatcher
from .auth import check_bearer_token, is_loopback_host


class LocalHttpServer:
    """Small local HTTP API backed by aiohttp."""

    def __init__(
        self,
        config: HttpConfig,
        dispatcher: MessageDispatcher,
        plugin_version: str = "v0.1.0",
    ) -> None:
        self._config = config
        self._dispatcher = dispatcher
        self._plugin_version = plugin_version
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._started = False

    @property
    def started(self) -> bool:
        """Returns whether the HTTP server has been started."""

        return self._started

    async def start(self) -> None:
        """Starts the HTTP server if enabled."""

        if not self._config.enabled:
            return
        if not is_loopback_host(self._config.host) and not self._config.auth_token:
            raise ConfigError(
                "HTTP auth_token is required when listening outside loopback hosts."
            )

        app = web.Application(client_max_size=self._config.max_body_bytes)
        app.router.add_get("/health", self._handle_health)
        app.router.add_post("/v1/messages", self._handle_send_message)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            host=self._config.host,
            port=self._config.port,
        )
        await self._site.start()
        self._started = True

    async def stop(self) -> None:
        """Stops the HTTP server and releases sockets."""

        if self._runner is not None:
            await self._runner.cleanup()
        self._site = None
        self._runner = None
        self._started = False

    async def _handle_health(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return _json_error("unauthorized", "Invalid bearer token.", status=401)
        return web.json_response(
            {
                "ok": True,
                "plugin": "astrbot_plugin_interconnect",
                "version": self._plugin_version,
                "http": {
                    "host": self._config.host,
                    "port": self._config.port,
                },
            }
        )

    async def _handle_send_message(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return _json_error("unauthorized", "Invalid bearer token.", status=401)

        try:
            payload = await request.json()
            envelope = _payload_to_envelope(payload)
            results = await self._dispatcher.dispatch(envelope)
        except web.HTTPRequestEntityTooLarge:
            return _json_error("request_too_large", "Request body is too large.", 413)
        except ValueError as exc:
            return _json_error("invalid_json", str(exc), 400)
        except ConfigError as exc:
            return _json_error("invalid_request", str(exc), 400)
        except Exception:
            return _json_error(
                "internal_error",
                "Failed to process local message request.",
                500,
            )

        return web.json_response(
            {
                "ok": all(result.ok for result in results),
                "message_id": envelope.message_id,
                "results": [
                    {
                        "target": result.target.to_dict(),
                        "ok": result.ok,
                        "message": result.message,
                    }
                    for result in results
                ],
            },
            status=200 if all(result.ok for result in results) else 502,
        )

    def _authorized(self, request: web.Request) -> bool:
        return check_bearer_token(request, self._config.auth_token)


def _payload_to_envelope(payload: Any) -> MessageEnvelope:
    if not isinstance(payload, dict):
        raise ConfigError("Request body must be a JSON object.")

    target = _parse_target(payload.get("target"))
    content = MessageContent.from_dict(_as_dict(payload.get("content"), "content"))
    if not content.text and not content.images:
        raise ConfigError("content.text or content.images is required.")

    source_data = _as_dict(payload.get("source", {}), "source")
    return MessageEnvelope(
        message_id=str(payload.get("message_id") or new_message_id()),
        direction="local_to_qq",
        source=EndpointRef(
            type=str(source_data.get("type", "http_endpoint")),
            id=str(source_data.get("id", "")),
            alias=str(source_data.get("alias", "")),
            extra=dict(source_data.get("extra") or {}),
        ),
        target=target,
        sender=SenderInfo.from_dict(_as_dict(payload.get("sender", {}), "sender")),
        content=content,
        extra=dict(payload.get("extra") or {}),
    )


def _parse_target(value: Any) -> EndpointRef:
    if value is None:
        return EndpointRef(type="local")
    data = _as_dict(value, "target")
    if not data:
        return EndpointRef(type="local")
    alias = str(data.get("alias", "")).strip()
    target_type = str(data.get("type", "qq_session")).strip()
    if target_type == "qq_session_alias":
        target_type = "qq_session"
    target_id = str(data.get("id", "")).strip()
    if target_type != "qq_session":
        raise ConfigError(f"Unsupported target.type: {target_type!r}.")
    if not (alias or target_id):
        raise ConfigError(
            "target.id or target.alias is required for QQ session targets."
        )
    extra = dict(data.get("extra") or {})
    source_type = str(
        data.get("source_type") or data.get("conversation_type") or ""
    ).strip()
    if source_type and source_type not in ("qq_group", "qq_private"):
        raise ConfigError("target.conversation_type must be qq_group or qq_private.")
    if source_type:
        extra["source_type"] = source_type
    platform = str(data.get("platform") or "").strip()
    if platform:
        extra["platform"] = platform
    return EndpointRef(
        type=target_type,
        id=target_id,
        alias=alias,
        extra=extra,
    )


def _as_dict(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be a JSON object.")
    return value


def _json_error(code: str, message: str, status: int) -> web.Response:
    return web.json_response(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
            },
        },
        status=status,
    )
