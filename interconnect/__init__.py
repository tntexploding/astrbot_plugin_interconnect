"""Core package for the AstrBot Interconnect plugin."""

from .config import PluginConfig, load_plugin_config

PLUGIN_VERSION = "0.1.0"

__all__ = [
    "PLUGIN_VERSION",
    "PluginConfig",
    "load_plugin_config",
]
