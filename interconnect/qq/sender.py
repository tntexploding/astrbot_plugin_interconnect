"""QQ-side active message sender."""

from __future__ import annotations

from typing import Protocol

import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain

from ..interfaces import DispatchResult
from ..models import MessageEnvelope
from ..services.media_store import MediaError, MediaStore
from ..storage import SessionStore


class SendMessageContext(Protocol):
    """Minimal AstrBot context interface used by the QQ sender."""

    async def send_message(self, session: str, message_chain: MessageChain) -> bool:
        """Sends a message chain to an AstrBot unified message origin."""


class QqSessionSender:
    """Sends local messages to recorded QQ conversations."""

    sink_type = "qq_session"

    def __init__(
        self,
        context: SendMessageContext,
        session_store: SessionStore,
        media_store: MediaStore,
    ) -> None:
        self._context = context
        self._session_store = session_store
        self._media_store = media_store

    async def send(self, envelope: MessageEnvelope) -> DispatchResult:
        """Sends one envelope using an alias or recorded conversation ID."""

        alias = envelope.target.alias
        conversation_id = envelope.target.id
        if not (alias or conversation_id):
            return DispatchResult(
                target=envelope.target,
                ok=False,
                message="Missing QQ session alias or conversation ID.",
            )

        binding = await self._session_store.resolve(
            alias=alias,
            conversation_id=conversation_id,
            source_type=str(envelope.target.extra.get("source_type", "")),
            platform=str(envelope.target.extra.get("platform", "")),
        )
        if binding is None:
            target_label = alias or conversation_id
            return DispatchResult(
                target=envelope.target,
                ok=False,
                message=f"QQ session {target_label!r} has not been recorded.",
            )
        if not binding.unified_msg_origin:
            return DispatchResult(
                target=envelope.target,
                ok=False,
                message=(
                    f"QQ session {binding.alias!r} has no AstrBot send address yet; "
                    "let that conversation send one message to refresh it."
                ),
            )

        chain, image_errors = await self._build_message_chain(envelope)
        if not chain.chain:
            return DispatchResult(
                target=envelope.target,
                ok=False,
                message="Message content is empty or all images were rejected.",
            )

        sent = await self._context.send_message(binding.unified_msg_origin, chain)
        if not sent:
            return DispatchResult(
                target=envelope.target,
                ok=False,
                message="AstrBot did not find a matching platform for this session.",
            )
        message = "sent"
        if image_errors:
            message = f"sent with {len(image_errors)} rejected image(s)"
        return DispatchResult(target=envelope.target, ok=True, message=message)

    async def _build_message_chain(
        self,
        envelope: MessageEnvelope,
    ) -> tuple[MessageChain, tuple[str, ...]]:
        chain = MessageChain()
        image_errors: list[str] = []
        if envelope.content.text:
            chain.message(envelope.content.text)
        for image in envelope.content.images:
            try:
                source_type, value = await self._media_store.prepare_outbound_image(
                    image
                )
                if source_type == "url":
                    chain.chain.append(Comp.Image.fromURL(value))
                else:
                    chain.chain.append(Comp.Image.fromFileSystem(value))
            except (MediaError, OSError, ValueError) as exc:
                image_errors.append(str(exc))
        return chain, tuple(image_errors)
