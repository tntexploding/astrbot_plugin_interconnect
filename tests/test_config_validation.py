"""Tests for configuration safety validation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect.config import (  # noqa: E402
    load_plugin_config,
    migrate_plugin_config,
)
from interconnect.errors import ConfigError  # noqa: E402


class ConfigValidationTest(unittest.TestCase):
    """Route-specific configuration validation tests."""

    def test_rejects_http_webhook_without_url(self) -> None:
        with self.assertRaisesRegex(ConfigError, "target.url is required"):
            load_plugin_config(
                {
                    "routes": [
                        {
                            "id": "bad_webhook",
                            "enabled": True,
                            "direction": "qq_to_local",
                            "source": {"type": "*"},
                            "targets": [{"type": "http_webhook"}],
                        }
                    ]
                }
            )

    def test_rejects_http_webhook_invalid_retry_attempts(self) -> None:
        with self.assertRaisesRegex(ConfigError, "retry_attempts"):
            load_plugin_config(
                {
                    "routes": [
                        {
                            "id": "bad_retry",
                            "enabled": True,
                            "direction": "qq_to_local",
                            "source": {"type": "*"},
                            "targets": [
                                {
                                    "type": "http_webhook",
                                    "url": "http://127.0.0.1:9000/webhook",
                                    "retry_attempts": 9,
                                }
                            ],
                        }
                    ]
                }
            )

    def test_rejects_qq_session_without_alias_or_id(self) -> None:
        with self.assertRaisesRegex(ConfigError, "target.alias or target.id"):
            load_plugin_config(
                {
                    "routes": [
                        {
                            "id": "bad_qq_target",
                            "enabled": True,
                            "direction": "local_to_qq",
                            "source": {"type": "http_endpoint"},
                            "targets": [{"type": "qq_session"}],
                        }
                    ]
                }
            )

    def test_rejects_invalid_regex(self) -> None:
        with self.assertRaisesRegex(ConfigError, "match.regex is invalid"):
            load_plugin_config(
                {
                    "routes": [
                        {
                            "id": "bad_regex",
                            "enabled": True,
                            "direction": "qq_to_local",
                            "source": {"type": "*"},
                            "match": {"regex": "["},
                            "targets": [
                                {
                                    "type": "http_webhook",
                                    "url": "http://127.0.0.1:9000/webhook",
                                }
                            ],
                        }
                    ]
                }
            )

    def test_loads_structured_webui_http_route(self) -> None:
        config = load_plugin_config(
            {
                "routes": [
                    {
                        "__template_key": "qq_to_http_webhook",
                        "id": "main_group_to_las",
                        "enabled": True,
                        "source": {
                            "type": "*",
                            "conversation_id": "123456",
                            "session_alias": "main_group",
                            "sender_id": "654321",
                        },
                        "match": {
                            "text_prefix": "#las",
                            "regex": "",
                            "require_image": False,
                        },
                        "target": {
                            "id": "las",
                            "url": "http://127.0.0.1:9000/webhook",
                            "timeout_seconds": 6,
                            "retry_attempts": 2,
                            "retry_backoff_seconds": 0.5,
                            "auth_token": "secret",
                            "headers": {"X-Client": "astrbot"},
                        },
                    }
                ]
            }
        )

        route = config.routes[0]
        target = route.targets[0]
        self.assertEqual(route.direction, "qq_to_local")
        self.assertEqual(route.source.id, "123456")
        self.assertEqual(route.source.alias, "main_group")
        self.assertEqual(route.source.user_id, "654321")
        self.assertEqual(route.source.extra, {})
        self.assertEqual(route.match.text_prefix, "#las")
        self.assertEqual(target.type, "http_webhook")
        self.assertEqual(target.extra["url"], "http://127.0.0.1:9000/webhook")
        self.assertEqual(target.extra["headers"], {"X-Client": "astrbot"})
        self.assertNotIn("payload_mode", target.extra)
        self.assertNotIn("payload_template", target.extra)

    def test_loads_structured_webui_local_to_qq_route(self) -> None:
        config = load_plugin_config(
            {
                "routes": [
                    {
                        "__template_key": "local_to_qq_session",
                        "id": "alerts_to_group",
                        "enabled": True,
                        "source": {
                            "type": "http_endpoint",
                            "endpoint_id": "alerts",
                        },
                        "match": {},
                        "target": {
                            "source_type": "qq_group",
                            "alias": "",
                            "id": "123456",
                        },
                    }
                ]
            }
        )

        route = config.routes[0]
        self.assertEqual(route.direction, "local_to_qq")
        self.assertEqual(route.source.endpoint_id, "alerts")
        self.assertEqual(route.targets[0].type, "qq_session")
        self.assertEqual(route.targets[0].id, "123456")
        self.assertEqual(route.targets[0].extra["source_type"], "qq_group")

    def test_migrates_advanced_route_to_simple_routes(self) -> None:
        raw_config = {
            "routes": [
                {
                    "__template_key": "advanced_route",
                    "id": "broadcast",
                    "enabled": True,
                    "direction": "qq_to_local",
                    "source_json": '{"type":"*","id":"123456"}',
                    "match_json": "{}",
                    "targets_json": (
                        '[{"type":"http_webhook",'
                        '"url":"http://127.0.0.1:9000/one"},'
                        '{"type":"http_webhook",'
                        '"url":"http://127.0.0.1:9000/two"}]'
                    ),
                }
            ]
        }

        self.assertTrue(migrate_plugin_config(raw_config))
        self.assertEqual(len(raw_config["routes"]), 2)
        self.assertEqual(
            [route["id"] for route in raw_config["routes"]],
            ["broadcast_1", "broadcast_2"],
        )
        self.assertEqual(len(load_plugin_config(raw_config).routes), 2)

    def test_migrates_legacy_route_for_webui_without_behavior_change(self) -> None:
        raw_config = {
            "routes": [
                {
                    "id": "legacy",
                    "enabled": True,
                    "direction": "qq_to_local",
                    "source": {"type": "*", "alias": "main_group"},
                    "match": {"text_prefix": "!"},
                    "targets": [
                        {
                            "type": "http_webhook",
                            "id": "las",
                            "url": "http://127.0.0.1:9000/webhook",
                            "timeout_seconds": 4,
                            "custom_option": "kept",
                        }
                    ],
                }
            ]
        }

        self.assertTrue(migrate_plugin_config(raw_config))
        self.assertFalse(migrate_plugin_config(raw_config))
        migrated = raw_config["routes"][0]
        self.assertEqual(migrated["__template_key"], "qq_to_http_webhook")

        route = load_plugin_config(raw_config).routes[0]
        self.assertEqual(route.id, "legacy")
        self.assertEqual(route.source.alias, "main_group")
        self.assertEqual(route.match.text_prefix, "!")
        self.assertEqual(route.targets[0].id, "las")
        self.assertEqual(route.targets[0].extra["timeout_seconds"], 4)
        self.assertNotIn("custom_option", route.targets[0].extra)

    def test_migrates_duplicate_qq_source_ids_to_conversation_id(self) -> None:
        raw_config = {
            "routes": [
                {
                    "__template_key": "qq_to_http_webhook",
                    "id": "legacy_group",
                    "enabled": True,
                    "source": {
                        "type": "qq_group",
                        "id": "123456",
                        "alias": "main_group",
                        "group_id": "123456",
                        "user_id": "654321",
                        "extra": {},
                    },
                    "match": {},
                    "target": {
                        "url": "http://127.0.0.1:9000/webhook",
                        "extra": {},
                    },
                }
            ]
        }

        self.assertTrue(migrate_plugin_config(raw_config))
        source = raw_config["routes"][0]["source"]
        self.assertEqual(
            source,
            {
                "type": "qq_group",
                "conversation_id": "123456",
                "session_alias": "main_group",
                "sender_id": "654321",
            },
        )

        route = load_plugin_config(raw_config).routes[0]
        self.assertEqual(route.source.id, "123456")
        self.assertEqual(route.source.alias, "main_group")
        self.assertEqual(route.source.group_id, "")
        self.assertEqual(route.source.user_id, "654321")

    def test_migrates_multitarget_route_to_simple_templates(self) -> None:
        raw_config = {
            "routes": [
                {
                    "id": "broadcast",
                    "enabled": True,
                    "direction": "qq_to_local",
                    "source": {"type": "*"},
                    "targets": [
                        {
                            "type": "http_webhook",
                            "url": "http://127.0.0.1:9000/one",
                        },
                        {
                            "type": "http_webhook",
                            "url": "http://127.0.0.1:9000/two",
                        },
                    ],
                }
            ]
        }

        self.assertTrue(migrate_plugin_config(raw_config))
        self.assertEqual(
            [route["__template_key"] for route in raw_config["routes"]],
            ["qq_to_http_webhook", "qq_to_http_webhook"],
        )
        self.assertEqual(
            [route["id"] for route in raw_config["routes"]],
            ["broadcast_1", "broadcast_2"],
        )
        self.assertEqual(len(load_plugin_config(raw_config).routes), 2)

    def test_migration_removes_unimplemented_websocket_routes(self) -> None:
        raw_config = {
            "routes": [
                {
                    "__template_key": "qq_to_ws_topic",
                    "id": "unused_ws",
                }
            ]
        }

        self.assertTrue(migrate_plugin_config(raw_config))
        self.assertEqual(raw_config["routes"], [])

    def test_migration_removes_obsolete_global_filters_and_websocket(self) -> None:
        raw_config = {
            "qq": {
                "capture_group": True,
                "allow_groups": ["123"],
                "allow_users": [],
                "block_users": [],
            },
            "local": {
                "http": {"enabled": False},
                "websocket": {"enabled": True},
            },
            "routes": [],
        }

        self.assertTrue(migrate_plugin_config(raw_config))
        self.assertEqual(raw_config["qq"], {"capture_group": True})
        self.assertEqual(raw_config["local"], {"http": {"enabled": False}})
        self.assertFalse(migrate_plugin_config(raw_config))

    def test_loads_editable_session_mapping(self) -> None:
        config = load_plugin_config(
            {
                "sessions": {
                    "auto_record": True,
                    "bindings": [
                        {
                            "__template_key": "qq_session",
                            "alias": "main_group",
                            "source_type": "qq_group",
                            "conversation_id": "123456",
                        }
                    ],
                }
            }
        )

        self.assertTrue(config.sessions.auto_record)
        self.assertEqual(config.sessions.bindings[0].alias, "main_group")
        self.assertEqual(config.sessions.bindings[0].conversation_id, "123456")

    def test_template_mode_accepts_webui_json_file_path(self) -> None:
        config = load_plugin_config(
            {
                "protocol": {
                    "webhook_payload_mode": "template",
                    "webhook_payload_template_files": [
                        "files/protocol/webhook_payload_template_files/custom.json"
                    ],
                }
            }
        )

        self.assertEqual(
            config.protocol.webhook_payload_template_files,
            ("files/protocol/webhook_payload_template_files/custom.json",),
        )

    def test_template_mode_rejects_path_outside_webui_file_directory(self) -> None:
        with self.assertRaisesRegex(ConfigError, "invalid path"):
            load_plugin_config(
                {
                    "protocol": {
                        "webhook_payload_mode": "template",
                        "webhook_payload_template_files": ["../custom.json"],
                    }
                }
            )

    def test_rejects_unknown_webui_route_template(self) -> None:
        with self.assertRaisesRegex(ConfigError, "unsupported template"):
            load_plugin_config(
                {
                    "routes": [
                        {
                            "__template_key": "unknown",
                            "id": "bad",
                        }
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
