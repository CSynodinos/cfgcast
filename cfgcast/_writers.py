"""
One writer per format, our own: a dictionary in, a file out. The counterpart of `_readers`.

A writer is handed plain data, prepared by `write`: the configuration as a dictionary
(sections as nested dictionaries), the overrides merged in and checked. So a writer meets
`dict`, `list`, `str`, `int`, `float`, `bool` and `None`, and knows nothing of the schema. Every
key is written, also one equal to its code default.

What is a subclass of those is written as its base value (`_plain`): a `TypeModel` such as `Port`
as its number, and a `SecretStr` as its real text, never the mask (`str()` of it is the mask).
An `Enum` is written as its value; a path, a `Decimal` and a `UUID` as text. Anything else is a
`TypeError` before the file is opened: a file is written whole or not at all.

`None`: JSON and YAML write `null`; TOML and ini have no null, so the key is left out, and
loading the file gives the field its code default (the same where that default is `None`).
"""
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any, Callable
from uuid import UUID
from ._readers import CfgType, RawConfigData
import json
import math
import os
import re


def write_json(file: str | os.PathLike[str], data: RawConfigData, *, indent: int | None = 2) -> None:
    """`json.dumps` (`ensure_ascii = False`, `allow_nan = False`: NaN is no JSON), UTF-8."""
    _save(file, json.dumps(_plain(data), indent = indent, ensure_ascii = False, allow_nan = False) + "\n")


def write_toml(file: str | os.PathLike[str], data: RawConfigData) -> None:
    """
    TOML, our own (`tomllib` only reads). Top-level keys first, then each section as a
    `[table]` and nested ones as `[db.replica]`; a list of dictionaries as `[[array]]`.
    Strings are basic strings with `\\`, `"` and control characters escaped; a key that isn't
    bare (`A-Za-z0-9_-`) is quoted. `inf` / `nan` are written as TOML has them. A `None` in a
    list is a `ValueError` (TOML has no null).
    """
    lines: list[str] = []
    _toml_table(lines, _plain(data), (), header = False)
    _save(file, "\n".join(lines).lstrip("\n") + "\n")


def write_ini(file: str | os.PathLike[str], data: RawConfigData) -> None:
    """
    ini, as `read_ini` reads it back: top-level keys under `[DEFAULT]`, a section as
    `[db]` and a nested one as `[db.replica]`, a list comma-joined (`hosts = a, b, c`;
    an empty list as `hosts =`). A bool as `true` / `false`. `ValueError` for what ini
    can't hold: a list item with a comma, a list of dictionaries, a value with a line break.
    """
    plain = _plain(data)
    lines: list[str] = []
    top = {key: value for key, value in plain.items() if not isinstance(value, dict)}
    if top:
        _ini_section(lines, "DEFAULT", top, ())
    for key, value in plain.items():
        if isinstance(value, dict):
            _ini_sections(lines, value, (key,))
    _save(file, "\n".join(lines).lstrip("\n") + "\n")


def write_yaml(file: str | os.PathLike[str], data: RawConfigData) -> None:
    """
    YAML, our own (PyYAML not needed to write): block style, two spaces of indent. A string is
    quoted when a YAML 1.1 reader would take it for something else (`no`, `on`, `08`, `1e3`,
    `null`, `~`), when it starts or ends with a space, or holds `:`, `#` or a special
    character first: other readers in a workflow get the string too.
    """
    lines: list[str] = []
    plain = _plain(data)
    if plain:
        _yaml_mapping(lines, plain, 0)
    else:
        lines.append("{}")
    _save(file, "\n".join(lines) + "\n")


WRITERS: dict[CfgType, Callable[[str | os.PathLike[str], RawConfigData], None]] = {
    "json": write_json, "toml": write_toml, "ini": write_ini, "yaml": write_yaml,
    }


# ── what every writer shares ────────────────────────────────────────────────

def _plain(value: Any) -> Any:
    """The value as the built-in types only (see the module's docstring)."""
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, str):
        return str.__str__(value)                                   # a SecretStr's real text, not str()'s mask
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, (PurePath, Decimal, UUID)):
        return str(value)
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise TypeError(f"a key of a configuration must be text, got {key!r}")
        return {str.__str__(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    raise TypeError(f"{type(value).__name__} can't be written to a configuration file: {value!r}")


def _save(file: str | os.PathLike[str], text: str) -> None:
    #* The text is made in full before the file is opened: an error leaves no half-written file.
    with open(file, "w", encoding = "utf-8", newline = "\n") as stream:
        stream.write(text)


def _number(value: int | float, inf: str, nan: str) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return nan
        if math.isinf(value):
            return inf if value > 0 else "-" + inf
        return repr(value)                                          # 0.1, 1e+16: valid in all three formats
    return str(value)


# ── TOML ────────────────────────────────────────────────────────────────────

_TOML_BARE = re.compile(r"[A-Za-z0-9_-]+")
_TOML_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _toml_string(text: str) -> str:
    out = []
    for char in text:
        if char in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _toml_key(key: str) -> str:
    return key if _TOML_BARE.fullmatch(key) else _toml_string(key)


def _toml_value(value: Any, where: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number(value, "inf", "nan")
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item, where) for item in value) + "]"
    if isinstance(value, dict):                                     # a table inside a list that isn't all tables
        return "{" + ", ".join(f"{_toml_key(k)} = {_toml_value(v, where)}" for k, v in value.items() if v is not None) + "}"
    raise ValueError(f"{where}: TOML has no null, and a list can't hold one")


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def _toml_table(lines: list[str], table: dict[str, Any], path: tuple[str, ...], *, header: bool, array: bool = False) -> None:
    scalars = [(k, v) for k, v in table.items() if v is not None and not isinstance(v, dict) and not _is_table_array(v)]
    if header and (scalars or array or not table):                  # a table of tables only needs no header of its own
        name = ".".join(_toml_key(part) for part in path)
        lines += ["", f"[[{name}]]" if array else f"[{name}]"]
    for key, value in scalars:
        lines.append(f"{_toml_key(key)} = {_toml_value(value, '.'.join((*path, key)))}")
    for key, value in table.items():
        if isinstance(value, dict):
            _toml_table(lines, value, (*path, key), header = True)
        elif _is_table_array(value):
            for item in value:
                _toml_table(lines, item, (*path, key), header = True, array = True)


# ── ini ─────────────────────────────────────────────────────────────────────

_INI_KEY = re.compile(r"[^\s=:\[\];#][^=:\n\r]*")


def _ini_text(value: Any, where: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number(value, "inf", "nan")
    if isinstance(value, str):
        if "\n" in value or "\r" in value:
            raise ValueError(f"{where}: ini can't hold a value with a line break")
        if value != value.strip():
            raise ValueError(f"{where}: ini drops the spaces around a value, so {value!r} would change")
        return value
    raise ValueError(f"{where}: ini can't hold a {type(value).__name__} here")


def _ini_section(lines: list[str], name: str, values: dict[str, Any], path: tuple[str, ...]) -> None:
    lines += ["", f"[{name}]"]
    for key, value in values.items():
        where = ".".join((*path, key)) or key
        if not _INI_KEY.fullmatch(key) or key != key.strip():
            raise ValueError(f"{where}: {key!r} can't be an ini key")
        if value is None:
            continue
        if isinstance(value, list):
            items = []
            for item in value:
                if isinstance(item, (dict, list)) or item is None:
                    raise ValueError(f"{where}: ini can't hold a list of {type(item).__name__}")
                text = _ini_text(item, where)
                if "," in text:
                    raise ValueError(f"{where}: an item of an ini list can't hold a comma ({text!r})")
                items.append(text)
            lines.append(f"{key} = {', '.join(items)}".rstrip())
        else:
            lines.append(f"{key} = {_ini_text(value, where)}".rstrip())


def _ini_sections(lines: list[str], table: dict[str, Any], path: tuple[str, ...]) -> None:
    for part in path:
        if "." in part or "]" in part or not part.strip() or part != part.strip():
            raise ValueError(f"{'.'.join(path)}: {part!r} can't be an ini section name")
    if path == ("DEFAULT",):
        raise ValueError("DEFAULT: a section of that name would be read as the top-level keys")
    values = {key: value for key, value in table.items() if not isinstance(value, dict)}
    if values or not table:                                         # a section of sections only needs no header
        _ini_section(lines, ".".join(path), values, path)
    for key, value in table.items():
        if isinstance(value, dict):
            _ini_sections(lines, value, (*path, key))


# ── YAML ────────────────────────────────────────────────────────────────────

#* Plain scalars a YAML reader (1.1 or 1.2) would take for something other than a string: booleans
#* and their 1.1 words, nulls, numbers in every notation 1.1 knows (octal 010, sexagesimal 1:20, 1_000),
#* and timestamps. A string that matches is written quoted.
_YAML_SPECIAL = re.compile(r"""(?x)
    (?: y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF
      | null|Null|NULL|~
      | [-+]?[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?
      | [-+]?\.[0-9_]+(?:[eE][-+]?[0-9]+)?
      | [-+]?0[0-7_]+ | [-+]?0o[0-7]+ | [-+]?0x[0-9a-fA-F_]+ | [-+]?0b[01_]+
      | [-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?
      | [-+]?\.(?:inf|Inf|INF) | \.(?:nan|NaN|NAN)
      | [0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt ].*)?
      | <<
    )""")
_YAML_FIRST = set("-?:,[]{}#&*!|>'\"%@`")


def _yaml_string(text: str) -> str:
    plain = (
        text and not _YAML_SPECIAL.fullmatch(text) and text[0] not in _YAML_FIRST
        and text == text.strip() and ": " not in text and " #" not in text and not text.endswith(":")
        and all(char.isprintable() for char in text)
        )
    if plain:
        return text
    return json.dumps(text, ensure_ascii = False)                   # a JSON string is a YAML double-quoted one


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        text = _number(value, ".inf", ".nan")
        if "e" in text and "." not in text:                         # YAML 1.1 (PyYAML) reads 1e+16 as text: 1.0e+16
            text = text.replace("e", ".0e", 1)
        return text
    return _yaml_string(value)


def _yaml_key(key: str) -> str:
    return _yaml_string(key)


def _yaml_mapping(lines: list[str], mapping: dict[str, Any], indent: int) -> None:
    pad = " " * indent
    for key, value in mapping.items():
        if isinstance(value, dict) and value:
            lines.append(f"{pad}{_yaml_key(key)}:")
            _yaml_mapping(lines, value, indent + 2)
        elif isinstance(value, list) and value:
            lines.append(f"{pad}{_yaml_key(key)}:")
            _yaml_sequence(lines, value, indent + 2)
        else:
            empty = "{}" if isinstance(value, dict) else "[]" if isinstance(value, list) else _yaml_scalar(value)
            lines.append(f"{pad}{_yaml_key(key)}: {empty}")


def _yaml_sequence(lines: list[str], sequence: list[Any], indent: int) -> None:
    pad = " " * indent
    for item in sequence:
        if isinstance(item, dict) and item:
            nested: list[str] = []
            _yaml_mapping(nested, item, indent + 2)
            lines.append(f"{pad}- {nested[0].lstrip()}")            # the first key on the dash's line
            lines += nested[1:]
        elif isinstance(item, list) and item:
            nested = []
            _yaml_sequence(nested, item, indent + 2)
            lines.append(f"{pad}- {nested[0].lstrip()}")
            lines += nested[1:]
        else:
            empty = "{}" if isinstance(item, dict) else "[]" if isinstance(item, list) else _yaml_scalar(item)
            lines.append(f"{pad}- {empty}")
