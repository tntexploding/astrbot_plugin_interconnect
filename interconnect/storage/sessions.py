"""Session binding storage backed by AstrBot plugin KV data."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ..errors import ConfigError

_SESSIONS_KEY = "sessions.v1"
_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class KvBackend(Protocol):
    """Minimal AstrBot plugin KV interface required by this store."""

    async def put_kv_data(
        self, key: str, value: dict | list | str | int | bool
    ) -> None:
        """Stores a plugin-scoped KV value."""

    async def get_kv_data(self, key: str, default: Any) -> Any:
        """Loads a plugin-scoped KV value."""

    async def delete_kv_data(self, key: str) -> None:
        """Deletes a plugin-scoped KV value."""


@dataclass(frozen=True)
class SessionBinding:
    """QQ conversation identity and its AstrBot send address."""

    alias: str
    unified_msg_origin: str
    source_type: str
    platform: str = ""
    group_id: str = ""
    user_id: str = ""
    sender_name: str = ""
    updated_at: str = ""

    @property
    def conversation_id(self) -> str:
        """Returns the group or private-peer ID represented by this entry."""

        if self.source_type == "qq_group":
            return self.group_id
        if self.source_type == "qq_private":
            return self.user_id
        return self.group_id or self.user_id

    def to_dict(self) -> dict[str, str]:
        """Serializes the binding into AstrBot KV compatible data."""

        return {
            "alias": self.alias,
            "unified_msg_origin": self.unified_msg_origin,
            "source_type": self.source_type,
            "platform": self.platform,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "sender_name": self.sender_name,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionBinding:
        """Builds a binding from persisted KV data."""

        return cls(
            alias=str(data.get("alias", "")),
            unified_msg_origin=str(data.get("unified_msg_origin", "")),
            source_type=str(data.get("source_type", "")),
            platform=str(data.get("platform", "")),
            group_id=str(data.get("group_id", "")),
            user_id=str(data.get("user_id", "")),
            sender_name=str(data.get("sender_name", "")),
            updated_at=str(data.get("updated_at", "")),
        )


class SessionStore:
    """Stores QQ send addresses in AstrBot's plugin-scoped KV storage."""

    def __init__(self, backend: KvBackend) -> None:
        self._backend = backend
        self._lock = asyncio.Lock()

    async def bind(self, binding: SessionBinding) -> None:
        """Creates or replaces a manually named session mapping."""

        validate_alias(binding.alias)
        if not binding.unified_msg_origin:
            raise ConfigError("Cannot bind an empty unified_msg_origin.")

        async with self._lock:
            sessions = await self._load_raw_unlocked()
            _remove_duplicate_conversations(sessions, binding, binding.alias)
            sessions[binding.alias] = binding.to_dict()
            await self._store_raw_unlocked(sessions)

    async def replace(self, bindings: tuple[SessionBinding, ...]) -> None:
        """Replaces storage with the mappings currently present in config."""

        sessions: dict[str, dict[str, Any]] = {}
        for binding in bindings:
            validate_alias(binding.alias)
            if binding.alias in sessions:
                raise ConfigError(f"Duplicate session alias: {binding.alias}.")
            sessions[binding.alias] = binding.to_dict()

        async with self._lock:
            await self._store_raw_unlocked(sessions)

    async def remember_observed(
        self,
        *,
        unified_msg_origin: str,
        source_type: str,
        conversation_id: str,
        platform: str = "",
        preferred_alias: str = "",
        sender_name: str = "",
    ) -> tuple[SessionBinding, bool]:
        """Records a routed QQ conversation and returns `(binding, changed)`."""

        if not unified_msg_origin:
            raise ConfigError("Cannot record an empty unified_msg_origin.")
        if source_type not in ("qq_group", "qq_private"):
            raise ConfigError(f"Unsupported QQ source type: {source_type!r}.")
        if not conversation_id:
            raise ConfigError("Cannot record an empty QQ conversation ID.")
        if preferred_alias:
            validate_alias(preferred_alias)

        async with self._lock:
            sessions = await self._load_raw_unlocked()
            bindings = _bindings_from_raw(sessions)
            existing = _find_origin(bindings, unified_msg_origin)
            if existing is None:
                existing = _find_conversation(
                    bindings,
                    source_type=source_type,
                    conversation_id=conversation_id,
                    platform=platform,
                )

            alias = existing.alias if existing is not None else preferred_alias
            if not alias:
                alias = _auto_alias(source_type, conversation_id)
            if existing is None or alias != existing.alias:
                alias = _available_alias(
                    alias,
                    sessions,
                    unified_msg_origin=unified_msg_origin,
                )
            binding = build_binding(
                alias=alias,
                unified_msg_origin=unified_msg_origin,
                source_type=source_type,
                platform=platform or (existing.platform if existing else ""),
                group_id=conversation_id if source_type == "qq_group" else "",
                user_id=conversation_id if source_type == "qq_private" else "",
                sender_name=(existing.sender_name if existing else sender_name),
            )
            if existing is not None and _same_runtime_address(existing, binding):
                return existing, False

            if existing is not None and existing.alias != alias:
                sessions.pop(existing.alias, None)
            _remove_duplicate_conversations(sessions, binding, alias)
            sessions[alias] = binding.to_dict()
            await self._store_raw_unlocked(sessions)
            return binding, True

    async def unbind(self, alias: str) -> bool:
        """Deletes a binding by alias and returns whether it existed."""

        validate_alias(alias)
        async with self._lock:
            sessions = await self._load_raw_unlocked()
            existed = alias in sessions
            if existed:
                del sessions[alias]
                await self._store_raw_unlocked(sessions)
            return existed

    async def get(self, alias: str) -> SessionBinding | None:
        """Returns one binding by alias."""

        validate_alias(alias)
        async with self._lock:
            data = (await self._load_raw_unlocked()).get(alias)
        if not isinstance(data, dict):
            return None
        return SessionBinding.from_dict(data)

    async def find_by_origin(self, unified_msg_origin: str) -> SessionBinding | None:
        """Returns the first binding matching an AstrBot message origin."""

        if not unified_msg_origin:
            return None
        async with self._lock:
            bindings = _bindings_from_raw(await self._load_raw_unlocked())
        return _find_origin(bindings, unified_msg_origin)

    async def find_by_conversation(
        self,
        source_type: str,
        conversation_id: str,
        platform: str = "",
    ) -> SessionBinding | None:
        """Finds a QQ send address from its normalized conversation ID."""

        if not conversation_id:
            return None
        async with self._lock:
            bindings = _bindings_from_raw(await self._load_raw_unlocked())
        return _find_conversation(
            bindings,
            source_type=source_type,
            conversation_id=conversation_id,
            platform=platform,
        )

    async def resolve(
        self,
        *,
        alias: str = "",
        conversation_id: str = "",
        source_type: str = "",
        platform: str = "",
    ) -> SessionBinding | None:
        """Resolves a local target by conversation ID, then fallback alias."""

        if conversation_id:
            binding = await self.find_by_conversation(
                source_type,
                conversation_id,
                platform,
            )
            if binding is not None:
                return binding
            # Preserve compatibility with callers that historically placed an
            # alias in target.id before conversation-ID lookup was supported.
            try:
                binding = await self.get(conversation_id)
            except ConfigError:
                binding = None
            if binding is not None:
                return binding
        if alias:
            return await self.get(alias)
        return None

    async def is_bound_origin(self, unified_msg_origin: str) -> bool:
        """Returns whether an AstrBot message origin is explicitly bound."""

        return await self.find_by_origin(unified_msg_origin) is not None

    async def list(self) -> tuple[SessionBinding, ...]:
        """Returns all bindings sorted by alias."""

        async with self._lock:
            bindings = _bindings_from_raw(await self._load_raw_unlocked())
        return tuple(sorted(bindings, key=lambda item: item.alias))

    async def count(self) -> int:
        """Returns the number of stored session bindings."""

        async with self._lock:
            return len(await self._load_raw_unlocked())

    async def clear(self) -> None:
        """Clears all session bindings."""

        async with self._lock:
            await self._backend.delete_kv_data(_SESSIONS_KEY)

    async def _load_raw_unlocked(self) -> dict[str, dict[str, Any]]:
        raw = await self._backend.get_kv_data(_SESSIONS_KEY, {})
        if not isinstance(raw, dict):
            return {}
        return {
            str(alias): value
            for alias, value in raw.items()
            if isinstance(alias, str) and isinstance(value, dict)
        }

    async def _store_raw_unlocked(
        self,
        sessions: dict[str, dict[str, Any]],
    ) -> None:
        if sessions:
            await self._backend.put_kv_data(_SESSIONS_KEY, sessions)
        else:
            await self._backend.delete_kv_data(_SESSIONS_KEY)


def build_binding(
    *,
    alias: str,
    unified_msg_origin: str,
    source_type: str,
    platform: str = "",
    group_id: str = "",
    user_id: str = "",
    sender_name: str = "",
) -> SessionBinding:
    """Creates a binding with a UTC update timestamp."""

    return SessionBinding(
        alias=alias,
        unified_msg_origin=unified_msg_origin,
        source_type=source_type,
        platform=platform,
        group_id=group_id,
        user_id=user_id,
        sender_name=sender_name,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def validate_alias(alias: str) -> None:
    """Validates a user-facing session alias."""

    if not _ALIAS_PATTERN.fullmatch(alias):
        raise ConfigError(
            "Alias must be 1-64 chars and only contain letters, digits, '.', '_' or '-'."
        )


def _bindings_from_raw(
    sessions: dict[str, dict[str, Any]],
) -> tuple[SessionBinding, ...]:
    return tuple(
        SessionBinding.from_dict(data)
        for data in sessions.values()
        if isinstance(data, dict)
    )


def _find_origin(
    bindings: tuple[SessionBinding, ...],
    unified_msg_origin: str,
) -> SessionBinding | None:
    return next(
        (
            binding
            for binding in bindings
            if binding.unified_msg_origin == unified_msg_origin
        ),
        None,
    )


def _find_conversation(
    bindings: tuple[SessionBinding, ...],
    *,
    source_type: str,
    conversation_id: str,
    platform: str,
) -> SessionBinding | None:
    candidates = tuple(
        binding
        for binding in bindings
        if binding.conversation_id == conversation_id
        and (not source_type or binding.source_type == source_type)
    )
    if platform:
        exact = tuple(binding for binding in candidates if binding.platform == platform)
        if len(exact) == 1:
            return exact[0]
        generic = tuple(binding for binding in candidates if not binding.platform)
        if len(generic) == 1:
            return generic[0]
    return candidates[0] if len(candidates) == 1 else None


def _remove_duplicate_conversations(
    sessions: dict[str, dict[str, Any]],
    binding: SessionBinding,
    keep_alias: str,
) -> None:
    for alias, value in tuple(sessions.items()):
        if alias == keep_alias or not isinstance(value, dict):
            continue
        candidate = SessionBinding.from_dict(value)
        same_origin = (
            binding.unified_msg_origin
            and candidate.unified_msg_origin == binding.unified_msg_origin
        )
        same_conversation = (
            binding.conversation_id
            and candidate.source_type == binding.source_type
            and candidate.conversation_id == binding.conversation_id
            and (
                not binding.platform
                or not candidate.platform
                or candidate.platform == binding.platform
            )
        )
        if same_origin or same_conversation:
            sessions.pop(alias, None)


def _same_runtime_address(left: SessionBinding, right: SessionBinding) -> bool:
    return (
        left.alias == right.alias
        and left.unified_msg_origin == right.unified_msg_origin
        and left.source_type == right.source_type
        and left.platform == right.platform
        and left.conversation_id == right.conversation_id
    )


def _auto_alias(source_type: str, conversation_id: str) -> str:
    normalized_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", conversation_id).strip("_.-")
    if not normalized_id:
        normalized_id = hashlib.sha256(conversation_id.encode()).hexdigest()[:12]
    prefix = "qq_group" if source_type == "qq_group" else "qq_private"
    return f"{prefix}_{normalized_id}"[:64]


def _available_alias(
    alias: str,
    sessions: dict[str, dict[str, Any]],
    *,
    unified_msg_origin: str,
) -> str:
    existing = sessions.get(alias)
    if not isinstance(existing, dict):
        return alias
    if SessionBinding.from_dict(existing).unified_msg_origin == unified_msg_origin:
        return alias
    digest = hashlib.sha256(unified_msg_origin.encode()).hexdigest()[:8]
    return f"{alias[:55]}_{digest}"
