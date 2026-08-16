"""Core package for the AstrBot Interconnect plugin."""

from .config import PluginConfig, load_plugin_config

__all__ = [
    "PluginConfig",
    "load_plugin_config",
]
