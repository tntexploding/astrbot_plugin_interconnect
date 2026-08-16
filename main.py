"""AstrBot plugin entrypoint for local network interconnection."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .interconnect import load_plugin_config
from .interconnect.config import (
    RouteConfig,
    RouteEndpoint,
    SessionBindingConfig,
    migrate_plugin_config,
)
from .interconnect.errors import ConfigError
from .interconnect.lifecycle import PluginRuntime
from .interconnect.models import MessageEnvelope
from .interconnect.services import DeliveryRecord
from .interconnect.storage import SessionBinding, SessionStore, build_binding


@filter.command_group("interconnect")
def interconnect_group():
    """Interconnect command group."""


class InterconnectPlugin(Star):
    """Bridge QQ messages with local HTTP services."""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context, config)
        self._migrate_config(config)
        self._astrbot_config = config
        self._session_config_lock = asyncio.Lock()
        try:
            self._config = load_plugin_config(config)
        except ConfigError:
            logger.exception("astrbot_plugin_interconnect configuration is invalid.")
            raise
        self._session_store = SessionStore(self)
        self._runtime = PluginRuntime(self._config, context, self._session_store)

    @staticmethod
    def _migrate_config(config: AstrBotConfig | None) -> None:
        """Makes pre-template routes editable in the current AstrBot WebUI."""

        if config is None or not migrate_plugin_config(config):
            return
        try:
            config.save_config()
        except Exception:
            # The normalized in-memory data remains usable. A later reload can
            # retry persistence without preventing the plugin from starting.
            logger.exception(
                "Failed to persist the WebUI route configuration migration."
            )
            return
        logger.info("Migrated interconnect configuration to the current WebUI format.")

    async def _initialize_session_mappings(self) -> None:
        """Makes WebUI session mappings the editable source of truth."""

        configured = tuple(
            _session_binding_from_config(item)
            for item in self._config.sessions.bindings
        )
        if self._config.sessions.migrated_from_kv:
            await self._session_store.replace(configured)
            return

        legacy = await self._session_store.list()
        merged = _merge_session_bindings(configured, legacy)
        await self._session_store.replace(merged)
        await self._save_session_mappings()

    async def _save_session_mappings(self) -> None:
        """Persists the current QQ send-address book into AstrBot config."""

        if self._astrbot_config is None:
            return
        async with self._session_config_lock:
            bindings = await self._session_store.list()
            sessions = dict(self._astrbot_config.get("sessions") or {})
            sessions["auto_record"] = bool(sessions.get("auto_record", True))
            sessions["migrated_from_kv"] = True
            sessions["bindings"] = [
                _session_binding_to_config(binding) for binding in bindings
            ]
            self._astrbot_config["sessions"] = sessions
            try:
                self._astrbot_config.save_config()
            except Exception:
                logger.exception("Failed to save interconnect session mappings.")

    async def _find_session_mapping(
        self,
        envelope: MessageEnvelope,
    ) -> SessionBinding | None:
        """Finds an existing mapping by AstrBot address or conversation ID."""

        binding = await self._session_store.find_by_origin(
            envelope.raw_refs.unified_msg_origin
        )
        if binding is not None:
            return binding
        return await self._session_store.find_by_conversation(
            envelope.source.type,
            envelope.source.id,
            envelope.sender.platform,
        )

    async def _remember_session_mapping(
        self,
        envelope: MessageEnvelope,
        binding: SessionBinding | None,
    ) -> MessageEnvelope:
        """Best-effort automatic recording for a routed QQ conversation."""

        try:
            remembered, changed = await self._session_store.remember_observed(
                unified_msg_origin=envelope.raw_refs.unified_msg_origin,
                source_type=envelope.source.type,
                conversation_id=envelope.source.id,
                platform=envelope.sender.platform,
                preferred_alias=binding.alias if binding is not None else "",
                sender_name=envelope.sender.name,
            )
        except ConfigError as exc:
            logger.warning("Unable to record interconnect QQ session: %s", exc)
            return envelope
        if changed:
            await self._save_session_mappings()
        return _with_session_alias(envelope, remembered.alias)

    async def initialize(self) -> None:
        """Start plugin runtime services after AstrBot loads the plugin."""

        try:
            await self._initialize_session_mappings()
        except Exception:
            # Existing KV mappings remain available if config synchronization
            # fails, so message forwarding can still start.
            logger.exception("Failed to synchronize interconnect session mappings.")
        if not self._config.enabled:
            logger.info("astrbot_plugin_interconnect is loaded but disabled.")
            return
        try:
            await self._runtime.start()
        except Exception:
            logger.exception("Failed to start astrbot_plugin_interconnect runtime.")
            return
        logger.info("astrbot_plugin_interconnect runtime started.")

    @interconnect_group.command("status")
    async def interconnect_status(self, event: AstrMessageEvent):
        """Show current interconnect runtime status."""

        status = self._runtime.status()
        delivery_stats = self._runtime.diagnostics.stats()
        session_count = await self._session_store.count()
        yield event.plain_result(
            "\n".join(
                [
                    "AstrBot Interconnect status:",
                    f"- enabled: {status.enabled}",
                    f"- started: {status.started}",
                    f"- http_enabled: {status.http_enabled}",
                    f"- http_started: {status.http_started}",
                    f"- webhook_started: {status.webhook_started}",
                    f"- routes: {status.enabled_route_count}/{status.route_count}",
                    f"- sessions: {session_count}",
                    f"- sinks: {status.sink_count}",
                    f"- deliveries: {delivery_stats.succeeded}/"
                    f"{delivery_stats.total} ok",
                    f"- delivery_failures: {delivery_stats.failed}",
                    f"- startup_error: {status.startup_error or 'none'}",
                ]
            )
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @interconnect_group.command("bind")
    async def interconnect_bind(self, event: AstrMessageEvent, alias: str):
        """Assign a fallback alias to the current QQ conversation."""

        try:
            envelope = await self._runtime.qq_receiver.convert(event)
            binding = build_binding(
                alias=alias,
                unified_msg_origin=envelope.raw_refs.unified_msg_origin,
                source_type=envelope.source.type,
                platform=envelope.sender.platform,
                group_id=(
                    envelope.source.id if envelope.source.type == "qq_group" else ""
                ),
                user_id=(
                    envelope.source.id if envelope.source.type == "qq_private" else ""
                ),
                sender_name=envelope.sender.name,
            )
            await self._session_store.bind(binding)
            await self._save_session_mappings()
        except ConfigError as exc:
            yield event.plain_result(f"绑定失败：{exc!s}")
            return
        except Exception:
            logger.exception("Failed to bind interconnect session.")
            yield event.plain_result("绑定失败：内部错误，详情请查看日志。")
            return

        target = binding.group_id or binding.user_id or binding.source_type
        yield event.plain_result(f"已记录会话别名：{binding.alias} -> {target}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @interconnect_group.command("unbind")
    async def interconnect_unbind(self, event: AstrMessageEvent, alias: str):
        """Remove a fallback QQ conversation alias."""

        try:
            existed = await self._session_store.unbind(alias)
            if existed:
                await self._save_session_mappings()
        except ConfigError as exc:
            yield event.plain_result(f"解绑失败：{exc!s}")
            return
        except Exception:
            logger.exception("Failed to unbind interconnect session.")
            yield event.plain_result("解绑失败：内部错误，详情请查看日志。")
            return

        if existed:
            yield event.plain_result(f"已删除会话映射：{alias}")
        else:
            yield event.plain_result(f"未找到会话：{alias}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @interconnect_group.command("sessions")
    async def interconnect_sessions(self, event: AstrMessageEvent):
        """List known QQ conversation send addresses."""

        try:
            bindings = await self._session_store.list()
        except Exception:
            logger.exception("Failed to list interconnect sessions.")
            yield event.plain_result("读取会话列表失败：内部错误，详情请查看日志。")
            return

        if not bindings:
            yield event.plain_result("尚未记录任何 QQ 会话。")
            return

        lines = ["已记录 QQ 会话："]
        for binding in bindings:
            target = binding.group_id or binding.user_id or binding.source_type
            lines.append(
                f"- {binding.alias}: {binding.source_type} {target} "
                f"({binding.platform or 'unknown'})"
            )
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @interconnect_group.command("routes")
    async def interconnect_routes(self, event: AstrMessageEvent):
        """List configured message routes."""

        routes = self._config.routes
        if not routes:
            yield event.plain_result("当前没有配置路由。")
            return

        lines = ["已配置路由："]
        for route in routes:
            state = "enabled" if route.enabled else "disabled"
            targets = ", ".join(target.type for target in route.targets)
            lines.append(
                f"- {route.id}: {state}, {route.direction}, "
                f"{route.source.type} -> {targets}"
            )
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @interconnect_group.command("metrics")
    async def interconnect_metrics(self, event: AstrMessageEvent):
        """Show aggregate delivery metrics since runtime startup."""

        stats = self._runtime.diagnostics.stats()
        target_counts = ", ".join(
            f"{target_type}={count}"
            for target_type, count in sorted(stats.by_target_type.items())
        )
        yield event.plain_result(
            "\n".join(
                [
                    "Interconnect delivery metrics:",
                    f"- total: {stats.total}",
                    f"- succeeded: {stats.succeeded}",
                    f"- failed: {stats.failed}",
                    f"- history_retained: {stats.retained}/{stats.history_limit}",
                    f"- by_target_type: {target_counts or 'none'}",
                ]
            )
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @interconnect_group.command("deliveries")
    async def interconnect_deliveries(
        self,
        event: AstrMessageEvent,
        limit: int = 10,
    ):
        """Show recent delivery attempts."""

        diagnostics = self._runtime.diagnostics
        if diagnostics.history_limit == 0:
            yield event.plain_result("投递历史记录已关闭。")
            return

        records = diagnostics.recent(_normalize_record_limit(limit))
        yield event.plain_result(_format_delivery_records("最近投递记录：", records))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @interconnect_group.command("errors")
    async def interconnect_errors(
        self,
        event: AstrMessageEvent,
        limit: int = 10,
    ):
        """Show recent failed delivery attempts."""

        diagnostics = self._runtime.diagnostics
        if diagnostics.history_limit == 0:
            yield event.plain_result("投递历史记录已关闭。")
            return

        records = diagnostics.recent(
            _normalize_record_limit(limit),
            only_failed=True,
        )
        yield event.plain_result(_format_delivery_records("最近投递失败：", records))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @interconnect_group.command("route-test")
    async def interconnect_route_test(
        self,
        event: AstrMessageEvent,
        sample_text: str = "",
    ):
        """Preview routes that would match the current QQ conversation."""

        try:
            envelope = await self._runtime.qq_receiver.convert(event)
            if sample_text:
                envelope = replace(
                    envelope,
                    content=replace(envelope.content, text=sample_text),
                )
            should_capture = self._runtime.qq_receiver.should_capture(envelope)
            binding = await self._find_session_mapping(envelope)
        except Exception:
            logger.exception("Failed to test interconnect routes.")
            yield event.plain_result("路由测试失败：内部错误，详情请查看日志。")
            return

        lines = [
            "Interconnect route test:",
            f"- conversation_type: {envelope.source.type}",
            f"- conversation_id: {envelope.source.id or 'unknown'}",
            f"- sender_id: {envelope.sender.id or 'unknown'}",
            f"- captured_by_qq_policy: {should_capture}",
        ]
        if not should_capture:
            lines.append("- result: 当前消息不满足 QQ 接收策略，不会进入路由。")
            yield event.plain_result("\n".join(lines))
            return
        if binding is not None:
            envelope = _with_session_alias(envelope, binding.alias)
        matches = self._runtime.router.match(envelope)
        lines.append(
            f"- session_mapping: {binding.alias if binding is not None else 'none'}"
        )
        lines.append(f"- sample_text: {_shorten(envelope.content.text or '-', 80)}")
        lines.extend(_format_route_matches(matches))
        yield event.plain_result("\n".join(lines))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """Normalize QQ-side messages and pass them into the dispatcher."""

        if not self._config.enabled:
            return

        # Keep unexpected adapter or routing errors inside this plugin boundary.
        try:
            envelope = await self._runtime.qq_receiver.convert(event)
            if _is_interconnect_command_text(envelope.content.text):
                return
            if not self._runtime.qq_receiver.should_capture(envelope):
                return

            binding = await self._find_session_mapping(envelope)
            if binding is not None:
                envelope = _with_session_alias(envelope, binding.alias)

            # Routes are the only inbound allow-list. Session mappings are an
            # outbound address book and never gate QQ-to-local forwarding.
            matches = self._runtime.router.match(envelope)
            if self._config.sessions.auto_record and (binding is not None or matches):
                envelope = await self._remember_session_mapping(envelope, binding)
            if not matches:
                logger.debug("No interconnect route matched the QQ message.")
                return
            results = await self._runtime.dispatcher.dispatch(envelope)
            if self._config.observability.log_payload:
                logger.info("interconnect envelope: %s", envelope.to_dict())
            logger.debug("interconnect dispatch results: %s", results)
        except Exception:
            logger.exception("Failed to process interconnect message event.")
            return

        if self._config.qq.stop_event_after_forward:
            event.stop_event()

    async def terminate(self) -> None:
        """Stop plugin runtime services when AstrBot unloads the plugin."""

        try:
            await self._runtime.stop()
        except Exception:
            logger.exception("Failed to stop astrbot_plugin_interconnect runtime.")
            return
        logger.info("astrbot_plugin_interconnect runtime stopped.")


def _with_session_alias(
    envelope: MessageEnvelope,
    alias: str,
) -> MessageEnvelope:
    """Adds an optional local alias without changing QQ conversation identity."""

    return replace(
        envelope,
        source=replace(
            envelope.source,
            alias=alias,
            extra={
                **envelope.source.extra,
                "session_alias": alias,
            },
        ),
    )


def _session_binding_from_config(item: SessionBindingConfig) -> SessionBinding:
    return SessionBinding(
        alias=item.alias,
        unified_msg_origin=item.unified_msg_origin,
        source_type=item.source_type,
        platform=item.platform,
        group_id=item.conversation_id if item.source_type == "qq_group" else "",
        user_id=item.conversation_id if item.source_type == "qq_private" else "",
        updated_at=item.updated_at,
    )


def _session_binding_to_config(binding: SessionBinding) -> dict[str, object]:
    return {
        "__template_key": "qq_session",
        "alias": binding.alias,
        "source_type": binding.source_type,
        "conversation_id": binding.conversation_id,
        "platform": binding.platform,
        "unified_msg_origin": binding.unified_msg_origin,
        "updated_at": binding.updated_at,
    }


def _merge_session_bindings(
    configured: tuple[SessionBinding, ...],
    legacy: tuple[SessionBinding, ...],
) -> tuple[SessionBinding, ...]:
    """Merges one-time KV data without overriding explicit WebUI edits."""

    merged = list(configured)
    for old_binding in legacy:
        match_index = next(
            (
                index
                for index, current in enumerate(merged)
                if current.alias == old_binding.alias
                or (
                    current.source_type == old_binding.source_type
                    and current.conversation_id
                    and current.conversation_id == old_binding.conversation_id
                )
            ),
            None,
        )
        if match_index is None:
            merged.append(old_binding)
            continue
        current = merged[match_index]
        if not current.unified_msg_origin and old_binding.unified_msg_origin:
            merged[match_index] = replace(
                current,
                unified_msg_origin=old_binding.unified_msg_origin,
                platform=current.platform or old_binding.platform,
                updated_at=current.updated_at or old_binding.updated_at,
            )
    return tuple(merged)


def _normalize_record_limit(limit: int) -> int:
    """Keeps diagnostic command output small enough for QQ clients."""

    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        return 10
    return max(1, min(parsed, 20))


def _format_delivery_records(
    title: str,
    records: tuple[DeliveryRecord, ...],
) -> str:
    if not records:
        return f"{title}\n- none"

    lines = [title]
    for record in records:
        state = "ok" if record.ok else "fail"
        timestamp = record.timestamp.astimezone().strftime("%m-%d %H:%M:%S")
        source = _format_endpoint(record.source_type, record.source_alias)
        target = _format_endpoint(record.target_type, record.target_alias)
        route = record.route_id or "-"
        message = _shorten(record.message or "-", 96)
        lines.append(
            f"- {timestamp} [{state}] {record.direction} "
            f"{source} -> {target} route={route} msg={message}"
        )
    return "\n".join(lines)


def _is_interconnect_command_text(text: str) -> bool:
    """Prevents plugin management commands from being bridged as user messages."""

    normalized = text.strip().lower()
    return normalized.startswith("/interconnect")


def _format_endpoint(endpoint_type: str, alias: str) -> str:
    if alias:
        return f"{endpoint_type}:{alias}"
    return endpoint_type or "unknown"


def _format_route_matches(matches: tuple[RouteConfig, ...]) -> list[str]:
    if not matches:
        return ["- matched_routes: none"]

    lines = [f"- matched_routes: {len(matches)}"]
    for route in matches:
        targets = ", ".join(_format_route_target(target) for target in route.targets)
        lines.append(f"  - {route.id}: {route.direction} -> {targets}")
    return lines


def _format_route_target(target: RouteEndpoint) -> str:
    if target.type == "http_webhook":
        url = str(target.extra.get("url", "")).strip()
        return f"http_webhook({url or 'missing-url'})"
    if target.type == "qq_session":
        identity = target.id or target.alias or "missing-id"
        return f"qq_session({identity})"
    return target.type or "unknown"


def _shorten(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."
