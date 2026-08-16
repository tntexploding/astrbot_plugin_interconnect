"""Authentication helpers for local HTTP endpoints."""

from __future__ import annotations

from aiohttp import web


def is_loopback_host(host: str) -> bool:
    """Returns whether a configured host is limited to the local machine."""

    return host in {"127.0.0.1", "localhost", "::1"}


def check_bearer_token(request: web.Request, expected_token: str) -> bool:
    """Validates an optional Authorization bearer token."""

    if not expected_token:
        return True
    auth_header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not auth_header.startswith(prefix):
        return False
    return auth_header.removeprefix(prefix).strip() == expected_token
