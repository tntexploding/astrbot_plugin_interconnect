"""Tests for persistent inbound media caching."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from interconnect.config import MediaConfig  # noqa: E402
from interconnect.models import MediaRef  # noqa: E402
from interconnect.services.media_store import MediaStore  # noqa: E402


class StubMediaStore(MediaStore):
    async def _download_url(self, url: str, kind: str) -> tuple[bytes, str]:
        self.download = (url, kind)
        return b"stable-image", "image/png"


class MediaStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_persists_expiring_url_as_hashed_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "media"
            root.mkdir()
            store = StubMediaStore(MediaConfig(), root)

            persisted = await store.persist_inbound(
                MediaRef(
                    media_id="image-1",
                    kind="image",
                    source_type="url",
                    url="https://example.com/expiring.png",
                    name="image.png",
                )
            )

            digest = hashlib.sha256(b"stable-image").hexdigest()
            self.assertEqual(store.download[1], "image")
            self.assertEqual(persisted.url, "")
            self.assertEqual(persisted.source_type, "file")
            self.assertEqual(persisted.sha256, digest)
            self.assertEqual(persisted.size_bytes, len(b"stable-image"))
            self.assertTrue(Path(persisted.file_path).is_file())
            self.assertEqual(
                persisted.extra["original_url"],
                "https://example.com/expiring.png",
            )


if __name__ == "__main__":
    unittest.main()
