"""Tests for media configuration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect.config import load_plugin_config  # noqa: E402
from interconnect.errors import ConfigError  # noqa: E402


class MediaConfigTest(unittest.TestCase):
    """Media configuration parsing tests."""

    def test_loads_media_safety_limits(self) -> None:
        config = load_plugin_config(
            {
                "media": {
                    "cache_enabled": True,
                    "max_cache_mb": 64,
                    "expire_seconds": 600,
                    "max_image_bytes": 2048,
                    "max_media_bytes": 4096,
                    "download_timeout_seconds": 5,
                    "allowed_mime_types": ["image/png"],
                }
            }
        )

        self.assertEqual(config.media.max_cache_mb, 64)
        self.assertEqual(config.media.max_image_bytes, 2048)
        self.assertEqual(config.media.max_media_bytes, 4096)
        self.assertEqual(config.media.download_timeout_seconds, 5)
        self.assertEqual(config.media.allowed_mime_types, ("image/png",))

    def test_rejects_image_limit_larger_than_cache(self) -> None:
        with self.assertRaises(ConfigError):
            load_plugin_config(
                {
                    "media": {
                        "cache_enabled": True,
                        "max_cache_mb": 1,
                        "max_image_bytes": 2 * 1024 * 1024,
                    }
                }
            )

    def test_rejects_media_limit_larger_than_cache(self) -> None:
        with self.assertRaises(ConfigError):
            load_plugin_config(
                {
                    "media": {
                        "cache_enabled": True,
                        "max_cache_mb": 1,
                        "max_media_bytes": 2 * 1024 * 1024,
                    }
                }
            )


if __name__ == "__main__":
    unittest.main()
