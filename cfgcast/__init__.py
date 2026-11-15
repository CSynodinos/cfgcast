"""Typed configuration files: JSON, TOML, ini/cfg and YAML, checked by enforced."""
from ._errors import ConfigError, ConfigWarning
from ._base import cfg, to_dict, write
from ._load import load_cfg
from clitint import turn_colours_off, turn_colours_on

__version__ = "0.1.0"
__all__ = [
    "cfg",
    "ConfigError",
    "ConfigWarning",
    "load_cfg",
    "to_dict",
    "write",
    "turn_colours_off",
    "turn_colours_on",
    ]
