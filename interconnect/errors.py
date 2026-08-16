"""Exception types used by the interconnect core."""


class InterconnectError(Exception):
    """Base exception for plugin-specific failures."""


class ConfigError(InterconnectError):
    """Raised when plugin configuration is invalid."""


class RoutingError(InterconnectError):
    """Raised when a message cannot be routed."""
