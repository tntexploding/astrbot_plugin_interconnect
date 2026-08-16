"""Bounded media persistence under AstrBot's plugin data directory."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import mimetypes
import re
import socket
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

from astrbot.core.utils.astrbot_path import (
    get_astrbot_plugin_data_path,
    get_astrbot_temp_path,
)

from ..config import MediaConfig
from ..errors import InterconnectError
from ..models import MediaRef

_PLUGIN_NAME = "astrbot_plugin_interconnect"
_CHUNK_SIZE = 64 * 1024
_SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,12}$")


class MediaError(InterconnectError):
    """Raised when media fails validation or caching."""


class MediaStore:
    """Manages validated inbound and outbound media files."""

    def __init__(self, config: MediaConfig, root: Path | None = None) -> None:
        self._config = config
        self._root = root or (
            Path(get_astrbot_plugin_data_path()) / _PLUGIN_NAME / "media"
        )
        self._session: aiohttp.ClientSession | None = None

    @property
    def root(self) -> Path:
        """Returns the media cache root inside AstrBot data."""

        return self._root

    async def start(self) -> None:
        """Creates the cache directory and shared HTTP client."""

        self._root.mkdir(parents=True, exist_ok=True)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        await self.cleanup()

    async def stop(self) -> None:
        """Closes the shared async HTTP client."""

        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def prepare_outbound_image(self, media: MediaRef) -> tuple[str, str]:
        """Returns `(source_type, value)` for a validated outbound image."""

        if media.url:
            _validate_http_url(media.url)
            if not self._config.cache_enabled:
                return ("url", media.url)
            return ("file", str(await self.cache_url(media.url)))

        if media.file_path:
            path = Path(media.file_path).expanduser().resolve()
            if not _is_within(path, self._root.resolve()):
                raise MediaError(
                    "Local image path is outside the plugin media directory."
                )
            if not path.is_file():
                raise MediaError("Local image file does not exist.")
            if path.stat().st_size > self._config.max_image_bytes:
                raise MediaError("Local image exceeds media.max_image_bytes.")
            return ("file", str(path))

        raise MediaError("Image requires a URL or an allowed cached file path.")

    async def persist_inbound(self, media: MediaRef) -> MediaRef:
        """Copies one inbound QQ media reference into the bounded cache."""

        if not self._config.cache_enabled:
            return media

        if media.url:
            data, mime_type = await self._download_url(media.url, media.kind)
        elif media.file_path:
            data = await self._read_allowed_file(media.file_path, media.kind)
            mime_type = (
                media.mime_type
                or mimetypes.guess_type(media.name or media.file_path)[0]
                or ""
            )
        else:
            return media

        digest = hashlib.sha256(data).hexdigest()
        extension = _media_extension(media, mime_type)
        target = self._root / f"{digest}{extension}"
        if not target.exists():
            await asyncio.to_thread(target.write_bytes, data)
        await self.cleanup()

        extra = dict(media.extra)
        extra["cached"] = True
        if media.url:
            extra["original_url"] = media.url
        return replace(
            media,
            source_type="file",
            url="",
            file_path=str(target.resolve()),
            mime_type=mime_type or media.mime_type,
            sha256=digest,
            size_bytes=len(data),
            extra=extra,
        )

    async def cache_url(self, url: str) -> Path:
        """Downloads, validates, hashes, and caches one outbound image URL."""

        data, mime_type = await self._download_url(url, "image")
        digest = hashlib.sha256(data).hexdigest()
        extension = _safe_extension(mimetypes.guess_extension(mime_type)) or ".img"
        target = self._root / f"{digest}{extension}"
        if not target.exists():
            await asyncio.to_thread(target.write_bytes, data)
        await self.cleanup()
        return target

    async def _download_url(self, url: str, kind: str) -> tuple[bytes, str]:
        _validate_http_url(url)
        await _assert_public_http_url(url)
        if self._session is None or self._session.closed:
            await self.start()

        assert self._session is not None
        timeout = aiohttp.ClientTimeout(total=self._config.download_timeout_seconds)
        try:
            async with self._session.get(url, timeout=timeout) as response:
                response.raise_for_status()
                mime_type = (
                    response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
                ).lower()
                if kind == "image" and mime_type not in self._config.allowed_mime_types:
                    raise MediaError(
                        f"Unsupported image MIME type: {mime_type or 'unknown'}."
                    )
                data = await _read_limited(response, self._limit_for(kind))
        except TimeoutError as exc:
            raise MediaError("Media download timed out.") from exc
        except aiohttp.ClientError as exc:
            raise MediaError(f"Media download failed: {exc!s}") from exc
        return data, mime_type

    async def _read_allowed_file(self, value: str, kind: str) -> bytes:
        path = Path(value).expanduser().resolve()
        roots = (self._root.resolve(), Path(get_astrbot_temp_path()).resolve())
        if not any(_is_within(path, root) for root in roots):
            raise MediaError("Inbound media path is outside AstrBot managed storage.")
        if not path.is_file():
            raise MediaError("Inbound media file does not exist.")
        limit = self._limit_for(kind)
        if path.stat().st_size > limit:
            raise MediaError("Inbound media exceeds its configured size limit.")
        return await asyncio.to_thread(path.read_bytes)

    def _limit_for(self, kind: str) -> int:
        if kind == "image":
            return self._config.max_image_bytes
        return self._config.max_media_bytes

    async def cleanup(self) -> None:
        """Removes expired and oldest files until cache limits are satisfied."""

        if not self._root.exists():
            return
        now = time.time()
        files = [path for path in self._root.iterdir() if path.is_file()]
        for path in files:
            if now - path.stat().st_mtime > self._config.expire_seconds:
                path.unlink(missing_ok=True)

        files = sorted(
            (path for path in self._root.iterdir() if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
        max_bytes = self._config.max_cache_mb * 1024 * 1024
        total = sum(path.stat().st_size for path in files)
        for path in files:
            if total <= max_bytes:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size


async def _read_limited(response: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    content_length = response.content_length
    if content_length is not None and content_length > max_bytes:
        raise MediaError("Remote media exceeds its configured size limit.")

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
        total += len(chunk)
        if total > max_bytes:
            raise MediaError("Remote media exceeds its configured size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MediaError("Media URL must use http or https.")


async def _assert_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    assert parsed.hostname is not None
    try:
        addresses = [ipaddress.ip_address(parsed.hostname)]
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            results = await loop.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise MediaError("Media URL host could not be resolved.") from exc
        addresses = list({ipaddress.ip_address(item[4][0]) for item in results})

    if not addresses or any(not address.is_global for address in addresses):
        raise MediaError("Media URL resolves to a restricted network address.")


def _media_extension(media: MediaRef, mime_type: str) -> str:
    candidates = (
        Path(media.name).suffix,
        Path(urlparse(media.url).path).suffix,
        Path(media.file_path).suffix,
        mimetypes.guess_extension(mime_type) or "",
    )
    for candidate in candidates:
        extension = _safe_extension(candidate)
        if extension:
            return extension
    return ".bin"


def _safe_extension(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.lower()
    return normalized if _SAFE_EXTENSION.fullmatch(normalized) else ""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
