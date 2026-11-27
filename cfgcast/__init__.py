"""Typed configuration files: JSON, TOML, ini/cfg and YAML, checked by enforced."""
from ._errors import ConfigError, ConfigWarning, FrozenConfigError
from ._base import Config, field, sources_of, to_dict, to_text, write, write_template
from ._load import parse_overrides
from ._sources import optional
from ._colours import turn_colours_off, turn_colours_on

__version__ = "0.1.1"
__all__ = [
    "Config",
    "ConfigError",
    "ConfigWarning",
    "field",
    "FrozenConfigError",
    "optional",
    "parse_overrides",
    "sources_of",
    "to_dict",
    "to_text",
    "write",
    "write_template",
    "turn_colours_off",
    "turn_colours_on",
    ]
