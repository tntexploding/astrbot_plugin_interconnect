"""Tests for session binding storage."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect.errors import ConfigError  # noqa: E402
from interconnect.storage.sessions import SessionStore, build_binding  # noqa: E402


class FakeKvBackend:
    """In-memory stand-in for AstrBot plugin KV storage."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def put_kv_data(
        self, key: str, value: dict | list | str | int | bool
    ) -> None:
        self.values[key] = value

    async def get_kv_data(self, key: str, default: Any) -> Any:
        return self.values.get(key, default)

    async def delete_kv_data(self, key: str) -> None:
        self.values.pop(key, None)


class SessionStoreTest(unittest.IsolatedAsyncioTestCase):
    """SessionStore behavior tests."""

    async def test_bind_list_and_unbind_session(self) -> None:
        store = SessionStore(FakeKvBackend())
        binding = build_binding(
            alias="main_group",
            unified_msg_origin="umo://qq/group/1",
            source_type="qq_group",
            platform="aiocqhttp",
            group_id="123",
            user_id="456",
        )

        await store.bind(binding)

        self.assertEqual(await store.count(), 1)
        self.assertEqual((await store.get("main_group")).group_id, "123")
        self.assertTrue(await store.is_bound_origin("umo://qq/group/1"))
        self.assertFalse(await store.is_bound_origin("umo://qq/group/2"))
        self.assertEqual(
            (await store.find_by_origin("umo://qq/group/1")).alias,
            "main_group",
        )
        self.assertEqual([item.alias for item in await store.list()], ["main_group"])
        self.assertTrue(await store.unbind("main_group"))
        self.assertFalse(await store.is_bound_origin("umo://qq/group/1"))
        self.assertFalse(await store.unbind("main_group"))
        self.assertEqual(await store.count(), 0)

    async def test_rejects_invalid_alias(self) -> None:
        store = SessionStore(FakeKvBackend())
        binding = build_binding(
            alias="../bad",
            unified_msg_origin="umo://qq/group/1",
            source_type="qq_group",
        )

        with self.assertRaises(ConfigError):
            await store.bind(binding)

    async def test_observed_message_completes_webui_mapping(self) -> None:
        store = SessionStore(FakeKvBackend())
        configured = build_binding(
            alias="main_group",
            unified_msg_origin="",
            source_type="qq_group",
            group_id="123",
        )
        await store.replace((configured,))

        remembered, changed = await store.remember_observed(
            unified_msg_origin="umo://qq/group/123",
            source_type="qq_group",
            conversation_id="123",
            platform="aiocqhttp",
        )

        self.assertTrue(changed)
        self.assertEqual(remembered.alias, "main_group")
        self.assertEqual(remembered.unified_msg_origin, "umo://qq/group/123")
        resolved = await store.resolve(
            conversation_id="123",
            source_type="qq_group",
        )
        self.assertEqual(resolved.alias, "main_group")

    async def test_observed_message_creates_stable_automatic_alias(self) -> None:
        store = SessionStore(FakeKvBackend())

        remembered, changed = await store.remember_observed(
            unified_msg_origin="umo://qq/private/456",
            source_type="qq_private",
            conversation_id="456",
        )

        self.assertTrue(changed)
        self.assertEqual(remembered.alias, "qq_private_456")
        self.assertEqual(remembered.user_id, "456")

    async def test_conversation_id_takes_priority_over_fallback_alias(self) -> None:
        store = SessionStore(FakeKvBackend())
        await store.replace(
            (
                build_binding(
                    alias="id_target",
                    unified_msg_origin="umo://qq/group/123",
                    source_type="qq_group",
                    group_id="123",
                ),
                build_binding(
                    alias="fallback",
                    unified_msg_origin="umo://qq/group/999",
                    source_type="qq_group",
                    group_id="999",
                ),
            )
        )

        resolved = await store.resolve(
            alias="fallback",
            conversation_id="123",
            source_type="qq_group",
        )

        self.assertEqual(resolved.alias, "id_target")

    async def test_bindings_survive_session_store_recreation(self) -> None:
        backend = FakeKvBackend()
        first_store = SessionStore(backend)
        await first_store.bind(
            build_binding(
                alias="main_group",
                unified_msg_origin="umo://qq/group/123",
                source_type="qq_group",
                platform="aiocqhttp",
                group_id="123",
            )
        )

        restarted_store = SessionStore(backend)
        restored = await restarted_store.resolve(
            conversation_id="123",
            source_type="qq_group",
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored.alias, "main_group")
        self.assertEqual(restored.unified_msg_origin, "umo://qq/group/123")


if __name__ == "__main__":
    unittest.main()
