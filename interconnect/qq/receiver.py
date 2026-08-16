"""QQ-side receiver service."""

from __future__ import annotations

from typing import Any

from ..config import PluginConfig
from ..models import MessageEnvelope
from ..services.media_store import MediaStore
from .adapter import event_to_envelope


class QqMessageReceiver:
    """Applies QQ capture policy and converts AstrBot events to envelopes."""

    def __init__(self, config: PluginConfig, media_store: MediaStore) -> None:
        self._config = config
        self._media_store = media_store

    def should_capture(self, envelope: MessageEnvelope) -> bool:
        """Returns whether the normalized QQ message is allowed by config."""

        qq_config = self._config.qq
        if not self._config.enabled:
            return False
        if envelope.source.type == "qq_private" and not qq_config.capture_private:
            return False
        if envelope.source.type == "qq_group" and not qq_config.capture_group:
            return False
        if envelope.content.images and not qq_config.capture_images:
            return False

        platform = envelope.sender.platform.upper()
        configured_platforms = {item.upper() for item in qq_config.platforms}
        if configured_platforms and platform not in configured_platforms:
            return False
        return True

    async def convert(self, event: Any) -> MessageEnvelope:
        """Converts an AstrBot event to a message envelope."""

        return await event_to_envelope(event, self._media_store)
