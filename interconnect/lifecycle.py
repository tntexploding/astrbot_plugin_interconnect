"""Plugin runtime lifecycle management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .config import PluginConfig
from .local import HttpWebhookSender
from .local.http_server import LocalHttpServer
from .protocol import MessageProtocolCodec
from .qq.receiver import QqMessageReceiver
from .qq.sender import QqSessionSender, SendMessageContext
from .router import InterconnectRouter
from .services import DeliveryDiagnostics, MessageDispatcher
from .services.media_store import MediaStore
from .storage import SessionStore


@dataclass(frozen=True)
class RuntimeStatus:
    """Read-only status snapshot for commands and diagnostics."""

    enabled: bool
    started: bool
    http_enabled: bool
    http_started: bool
    webhook_started: bool
    route_count: int
    enabled_route_count: int
    sink_count: int
    startup_error: str = ""


class PluginRuntime:
    """Owns long-lived services created from a config snapshot."""

    def __init__(
        self,
        config: PluginConfig,
        context: SendMessageContext,
        session_store: SessionStore,
    ) -> None:
        self._config = config
        self._router = InterconnectRouter(config)
        self._diagnostics = DeliveryDiagnostics(
            config.observability.delivery_history_limit
        )
        self._dispatcher = MessageDispatcher(self._router, self._diagnostics)
        self._media_store = MediaStore(config.media)
        self._qq_receiver = QqMessageReceiver(config, self._media_store)
        self._http_server = LocalHttpServer(config.local.http, self._dispatcher)
        self._http_webhook_sender = HttpWebhookSender(
            MessageProtocolCodec(config.protocol)
        )
        self._started = False
        self._startup_error = ""
        self._dispatcher.register_sink(
            QqSessionSender(context, session_store, self._media_store)
        )
        self._dispatcher.register_sink(self._http_webhook_sender)

    @property
    def config(self) -> PluginConfig:
        return self._config

    @property
    def router(self) -> InterconnectRouter:
        return self._router

    @property
    def dispatcher(self) -> MessageDispatcher:
        return self._dispatcher

    @property
    def diagnostics(self) -> DeliveryDiagnostics:
        return self._diagnostics

    @property
    def qq_receiver(self) -> QqMessageReceiver:
        return self._qq_receiver

    @property
    def http_server(self) -> LocalHttpServer:
        return self._http_server

    @property
    def http_webhook_sender(self) -> HttpWebhookSender:
        return self._http_webhook_sender

    async def start(self) -> None:
        """Starts the implemented HTTP and media runtime services."""

        try:
            await self._media_store.start()
            await self._http_webhook_sender.start()
            await self._http_server.start()
        except Exception as exc:
            self._startup_error = str(exc)
            await self._stop_started_services()
            raise
        self._startup_error = ""
        self._started = True

    async def stop(self) -> None:
        """Stops all runtime services."""

        await self._stop_started_services()
        self._started = False

    async def _stop_started_services(self) -> None:
        """Best-effort cleanup for normal stop and partial startup failures."""

        await asyncio.gather(
            self._http_server.stop(),
            self._http_webhook_sender.stop(),
            self._media_store.stop(),
            return_exceptions=True,
        )

    def status(self) -> RuntimeStatus:
        """Returns a compact status snapshot."""

        return RuntimeStatus(
            enabled=self._config.enabled,
            started=self._started,
            http_enabled=self._config.local.http.enabled,
            http_started=self._http_server.started,
            webhook_started=self._http_webhook_sender.started,
            route_count=self._router.route_count,
            enabled_route_count=self._router.enabled_route_count,
            sink_count=self._dispatcher.sink_count,
            startup_error=self._startup_error,
        )
