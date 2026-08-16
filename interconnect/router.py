"""Route matching for normalized interconnect messages."""

from __future__ import annotations

import re

from .config import PluginConfig, RouteConfig, RouteEndpoint
from .models import EndpointRef, MessageEnvelope


class InterconnectRouter:
    """Matches message envelopes against configured routes."""

    def __init__(self, config: PluginConfig) -> None:
        self._config = config

    @property
    def route_count(self) -> int:
        """Returns the number of configured routes."""

        return len(self._config.routes)

    @property
    def enabled_route_count(self) -> int:
        """Returns the number of enabled routes."""

        return sum(1 for route in self._config.routes if route.enabled)

    def match(self, envelope: MessageEnvelope) -> tuple[RouteConfig, ...]:
        """Returns enabled routes matching the envelope."""

        return tuple(
            route
            for route in self._config.routes
            if route.enabled and self._matches_route(route, envelope)
        )

    def _matches_route(self, route: RouteConfig, envelope: MessageEnvelope) -> bool:
        if route.direction != envelope.direction:
            return False
        if not _matches_endpoint(route.source, envelope.source):
            return False
        if route.match.require_image and not envelope.content.images:
            return False
        if route.match.text_prefix and not envelope.content.text.startswith(
            route.match.text_prefix
        ):
            return False
        if route.match.regex and not re.search(
            route.match.regex, envelope.content.text
        ):
            return False
        return True


def _matches_endpoint(route_endpoint: RouteEndpoint, endpoint: EndpointRef) -> bool:
    if route_endpoint.type not in ("*", endpoint.type):
        return False
    if route_endpoint.alias and route_endpoint.alias not in ("*", endpoint.alias):
        return False

    endpoint_group_id = str(endpoint.extra.get("group_id", ""))
    endpoint_user_id = str(endpoint.extra.get("user_id", ""))
    if endpoint.type in ("qq_group", "qq_private"):
        if not _matches_qq_conversation(
            route_endpoint,
            endpoint.id,
            endpoint_group_id,
        ):
            return False
    else:
        if route_endpoint.id and route_endpoint.id not in ("*", endpoint.id):
            return False
        if route_endpoint.group_id and route_endpoint.group_id not in (
            "*",
            endpoint_group_id,
        ):
            return False
    if route_endpoint.user_id and route_endpoint.user_id not in ("*", endpoint_user_id):
        return False

    endpoint_id = str(endpoint.extra.get("endpoint_id", ""))
    if route_endpoint.endpoint_id and route_endpoint.endpoint_id not in (
        "*",
        endpoint_id,
    ):
        return False
    return True


def _matches_qq_conversation(
    route_endpoint: RouteEndpoint,
    endpoint_id: str,
    endpoint_group_id: str,
) -> bool:
    """Matches one QQ conversation across canonical and legacy ID fields."""

    configured_ids = {
        value for value in (route_endpoint.id, route_endpoint.group_id) if value
    }
    if not configured_ids or "*" in configured_ids:
        return True
    event_ids = {value for value in (endpoint_id, endpoint_group_id) if value}
    return not configured_ids.isdisjoint(event_ids)
