"""Tests for the AstrBot WebUI configuration schema."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class ConfSchemaTest(unittest.TestCase):
    """Protects the runtime configuration sections from UI regressions."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(
            (PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8")
        )

    def test_all_runtime_sections_are_visible_and_typed(self) -> None:
        expected_types = {
            "qq": "object",
            "local": "object",
            "sessions": "object",
            "routes": "template_list",
            "protocol": "object",
            "media": "object",
            "observability": "object",
        }

        for key, expected_type in expected_types.items():
            with self.subTest(key=key):
                item = self.schema[key]
                self.assertEqual(item["type"], expected_type)
                self.assertFalse(item.get("invisible", False))

    def test_routes_expose_supported_webui_templates(self) -> None:
        routes = self.schema["routes"]
        self.assertEqual(routes["default"], [])
        self.assertEqual(
            set(routes["templates"]),
            {
                "qq_to_http_webhook",
                "local_to_qq_session",
            },
        )
        for template in routes["templates"].values():
            self.assertIsInstance(template.get("items"), dict)
            self.assertIn("id", template["items"])
            self.assertIn("enabled", template["items"])

    def test_schema_has_no_online_json_editor_or_websocket_switch(self) -> None:
        serialized = json.dumps(self.schema, ensure_ascii=False)

        self.assertNotIn("editor_mode", serialized)
        self.assertNotIn("websocket", serialized.lower())
        self.assertEqual(set(self.schema["local"]["items"]), {"http"})

    def test_qq_route_templates_use_unified_source_fields(self) -> None:
        templates = self.schema["routes"]["templates"]
        expected = {
            "type",
            "conversation_id",
            "session_alias",
            "sender_id",
        }
        source_items = templates["qq_to_http_webhook"]["items"]["source"]["items"]
        self.assertEqual(set(source_items), expected)
        self.assertNotIn("group_id", source_items)
        self.assertNotIn("user_id", source_items)

    def test_session_mappings_are_editable_in_webui(self) -> None:
        bindings = self.schema["sessions"]["items"]["bindings"]
        fields = bindings["templates"]["qq_session"]["items"]

        self.assertEqual(bindings["type"], "template_list")
        self.assertEqual(
            set(fields),
            {
                "alias",
                "source_type",
                "conversation_id",
                "platform",
                "unified_msg_origin",
                "updated_at",
            },
        )

    def test_custom_payload_uses_json_file_selector(self) -> None:
        protocol = self.schema["protocol"]["items"]
        selector = protocol["webhook_payload_template_files"]

        self.assertEqual(selector["type"], "file")
        self.assertEqual(selector["file_types"], ["json"])
        self.assertTrue(protocol["webhook_payload_template"].get("invisible"))


if __name__ == "__main__":
    unittest.main()
