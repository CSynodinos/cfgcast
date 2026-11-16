"""Typed configuration files: JSON, TOML, ini/cfg and YAML, checked by enforced."""
from ._errors import ConfigError, ConfigWarning
from ._base import Loadable, cfg, field, from_dict, sources_of, to_dict, to_text, write, write_template
from ._load import load_cfg, parse_overrides
from ._sources import optional
from clitint import turn_colours_off, turn_colours_on

__version__ = "0.1.0"
__all__ = [
    "cfg",
    "ConfigError",
    "ConfigWarning",
    "field",
    "from_dict",
    "Loadable",
    "load_cfg",
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
