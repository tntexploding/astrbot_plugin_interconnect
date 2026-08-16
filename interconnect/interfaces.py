"""Interfaces used to keep adapters decoupled from services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import EndpointRef, MessageEnvelope


@dataclass(frozen=True)
class DispatchResult:
    """Result of sending an envelope to one target."""

    target: EndpointRef
    ok: bool
    message: str = ""


class MessageSink(Protocol):
    """Protocol implemented by outbound message adapters."""

    @property
    def sink_type(self) -> str:
        """Endpoint type handled by the sink."""

    async def send(self, envelope: MessageEnvelope) -> DispatchResult:
        """Sends an envelope to its target."""
