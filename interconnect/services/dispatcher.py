"""Message dispatch service."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..config import RouteEndpoint
from ..interfaces import DispatchResult, MessageSink
from ..models import EndpointRef, MessageEnvelope
from ..router import InterconnectRouter
from .diagnostics import DeliveryDiagnostics


class MessageDispatcher:
    """Routes message envelopes and delegates delivery to registered sinks."""

    def __init__(
        self,
        router: InterconnectRouter,
        diagnostics: DeliveryDiagnostics | None = None,
    ) -> None:
        self._router = router
        self._sinks: dict[str, MessageSink] = {}
        self._diagnostics = diagnostics

    @property
    def sink_count(self) -> int:
        """Returns the number of registered sinks."""

        return len(self._sinks)

    def register_sink(self, sink: MessageSink) -> None:
        """Registers or replaces a sink for its endpoint type."""

        self._sinks[sink.sink_type] = sink

    async def dispatch(self, envelope: MessageEnvelope) -> tuple[DispatchResult, ...]:
        """Dispatches an envelope to all matched route targets."""

        results: list[DispatchResult] = []
        if envelope.target.type and envelope.target.type != "local":
            result = await self._send_to_target(envelope)
            self._observe(envelope, result)
            return (result,)

        routes = self._router.match(envelope)
        if not routes:
            result = DispatchResult(
                target=envelope.target,
                ok=False,
                message="No enabled route matched the message.",
            )
            self._observe(envelope, result)
            return (result,)

        for route in routes:
            for route_target in route.targets:
                target = EndpointRef(
                    type=route_target.type,
                    id=route_target.id,
                    alias=route_target.alias,
                    extra=_route_target_extra(route_target),
                )
                routed = replace(envelope, route_id=route.id, target=target)
                result = await self._send_to_target(routed)
                self._observe(routed, result)
                results.append(result)
        return tuple(results)

    async def _send_to_target(self, envelope: MessageEnvelope) -> DispatchResult:
        sink = self._sinks.get(envelope.target.type)
        if sink is None:
            return DispatchResult(
                target=envelope.target,
                ok=False,
                message=f"No sink registered for target type {envelope.target.type!r}.",
            )
        try:
            return await sink.send(envelope)
        except Exception as exc:
            return DispatchResult(
                target=envelope.target,
                ok=False,
                message=f"Sink {envelope.target.type!r} failed: {exc!s}",
            )

    def _observe(self, envelope: MessageEnvelope, result: DispatchResult) -> None:
        """Best-effort diagnostics recording that never breaks delivery."""

        if self._diagnostics is None:
            return
        try:
            self._diagnostics.observe(envelope, result)
        except Exception:
            return


def _route_target_extra(target: RouteEndpoint) -> dict[str, Any]:
    """Builds endpoint extras without emitting empty legacy fields."""

    extra = dict(target.extra)
    for key, value in (
        ("group_id", target.group_id),
        ("user_id", target.user_id),
        ("endpoint_id", target.endpoint_id),
    ):
        if value:
            extra[key] = value
    return extra
