"""Tests for AstrBot plugin metadata and release declarations."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect import PLUGIN_VERSION  # noqa: E402

_SEMANTIC_VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_SUPPORTED_QQ_PLATFORMS = {
    "aiocqhttp",
    "qq_official",
    "qq_official_webhook",
}


class MetadataTest(unittest.TestCase):
    """Checks metadata fields required by AstrBot and the plugin market."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.metadata = yaml.safe_load(
            (PLUGIN_ROOT / "metadata.yaml").read_text(encoding="utf-8")
        )

    def test_required_metadata_is_present(self) -> None:
        for field_name in ("name", "desc", "version", "author"):
            with self.subTest(field=field_name):
                self.assertIsInstance(self.metadata.get(field_name), str)
                self.assertTrue(self.metadata[field_name].strip())
        self.assertEqual(self.metadata["name"], PLUGIN_ROOT.name)
        self.assertIn("repo", self.metadata)

    def test_version_is_semantic_and_matches_runtime(self) -> None:
        version = self.metadata["version"]

        self.assertRegex(version, _SEMANTIC_VERSION_PATTERN)
        self.assertEqual(version, PLUGIN_VERSION)

    def test_declares_supported_qq_platform_keys(self) -> None:
        self.assertEqual(
            set(self.metadata["support_platforms"]),
            _SUPPORTED_QQ_PLATFORMS,
        )

    def test_declares_compatible_astrbot_range(self) -> None:
        supported_versions = SpecifierSet(self.metadata["astrbot_version"])

        self.assertIn(Version("4.13.0"), supported_versions)
        self.assertNotIn(Version("5.0.0"), supported_versions)


if __name__ == "__main__":
    unittest.main()
