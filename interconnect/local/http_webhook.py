"""Outbound HTTP webhook sink for QQ-to-local delivery."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import aiohttp

from ..interfaces import DispatchResult
from ..models import EndpointRef, MessageEnvelope
from ..protocol import MessageProtocolCodec

_DEFAULT_TIMEOUT_SECONDS = 8.0
_DEFAULT_RETRY_ATTEMPTS = 0
_DEFAULT_RETRY_BACKOFF_SECONDS = 0.25
_MAX_RETRY_ATTEMPTS = 5
_MAX_RETRY_BACKOFF_SECONDS = 30.0


class HttpWebhookSender:
    """Posts normalized message envelopes to configured HTTP webhooks."""

    sink_type = "http_webhook"

    def __init__(self, protocol_codec: MessageProtocolCodec) -> None:
        self._protocol_codec = protocol_codec
        self._session: aiohttp.ClientSession | None = None

    @property
    def started(self) -> bool:
        """Returns whether the aiohttp client session is ready."""

        return self._session is not None and not self._session.closed

    async def start(self) -> None:
        """Creates the shared async HTTP client session."""

        if not self.started:
            self._session = aiohttp.ClientSession()

    async def stop(self) -> None:
        """Closes the shared async HTTP client session."""

        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def send(self, envelope: MessageEnvelope) -> DispatchResult:
        """Sends one envelope to an HTTP webhook target."""

        if not self.started:
            await self.start()

        url = str(envelope.target.extra.get("url", "")).strip()
        if not url:
            return DispatchResult(
                target=envelope.target,
                ok=False,
                message="http_webhook target requires extra.url.",
            )

        timeout_seconds = _timeout_seconds(envelope.target.extra)
        retry_attempts = _retry_attempts(envelope.target.extra)
        retry_backoff_seconds = _retry_backoff_seconds(envelope.target.extra)
        headers = _headers(envelope.target.extra)
        payload = self._protocol_codec.build_webhook_payload(envelope)

        assert self._session is not None
        for attempt in range(retry_attempts + 1):
            result = await self._post_payload(
                url=url,
                payload=payload,
                headers=headers,
                timeout_seconds=timeout_seconds,
                target=envelope.target,
            )
            if result.ok or attempt == retry_attempts or not _should_retry(result):
                if attempt > 0:
                    return replace(
                        result,
                        message=f"{result.message} (attempts={attempt + 1})",
                    )
                return result
            await asyncio.sleep(retry_backoff_seconds * (2**attempt))

        return DispatchResult(
            target=envelope.target,
            ok=False,
            message="HTTP webhook retry loop ended unexpectedly.",
        )

    async def _post_payload(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
        target: EndpointRef,
    ) -> DispatchResult:
        """Posts one payload attempt and converts errors to DispatchResult."""

        try:
            async with self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                body = await response.text()
                if 200 <= response.status < 300:
                    return DispatchResult(
                        target=target,
                        ok=True,
                        message=f"HTTP {response.status}",
                    )
                return DispatchResult(
                    target=target,
                    ok=False,
                    message=f"HTTP {response.status}: {body[:200]}",
                )
        except TimeoutError:
            return DispatchResult(
                target=target,
                ok=False,
                message=f"HTTP webhook timed out after {timeout_seconds:g}s.",
            )
        except aiohttp.ClientError as exc:
            return DispatchResult(
                target=target,
                ok=False,
                message=f"HTTP webhook request failed: {exc!s}",
            )


def _headers(extra: dict[str, Any]) -> dict[str, str]:
    headers = {
        str(key): str(value)
        for key, value in dict(extra.get("headers") or {}).items()
        if value is not None
    }
    auth_token = str(extra.get("auth_token", "")).strip()
    if auth_token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers


def _timeout_seconds(extra: dict[str, Any]) -> float:
    raw_timeout = extra.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
    return _bounded_float(raw_timeout, _DEFAULT_TIMEOUT_SECONDS, minimum=0.0)


def _retry_attempts(extra: dict[str, Any]) -> int:
    raw_attempts = extra.get("retry_attempts", _DEFAULT_RETRY_ATTEMPTS)
    try:
        retry_attempts = int(raw_attempts)
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_ATTEMPTS
    return max(0, min(retry_attempts, _MAX_RETRY_ATTEMPTS))


def _retry_backoff_seconds(extra: dict[str, Any]) -> float:
    raw_backoff = extra.get(
        "retry_backoff_seconds",
        _DEFAULT_RETRY_BACKOFF_SECONDS,
    )
    return _bounded_float(
        raw_backoff,
        _DEFAULT_RETRY_BACKOFF_SECONDS,
        minimum=0.0,
        maximum=_MAX_RETRY_BACKOFF_SECONDS,
    )


def _bounded_float(
    value: Any,
    default: float,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number <= minimum:
        return default
    if maximum is not None:
        return min(number, maximum)
    return number


def _should_retry(result: DispatchResult) -> bool:
    if result.ok:
        return False
    message = result.message
    return (
        message.startswith("HTTP 429")
        or message.startswith("HTTP 5")
        or "timed out" in message
        or "request failed" in message
    )
