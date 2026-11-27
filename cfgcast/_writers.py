"""
One writer per format: a dictionary in, a file out. The counterpart of `_readers`. JSON is the
standard library's, a TOML value or key tomli-w's, YAML PyYAML's (`safe_dump`, with our rule for
what is quoted); the layout of TOML (`[table]`, `[[array]]`, a list on one line) and all of ini are ours.

A writer is handed plain data, prepared by `write`: the configuration as a dictionary
(sections as nested dictionaries). So a writer meets
`dict`, `list`, `str`, `int`, `float`, `bool` and `None`, and knows nothing of the schema. Every
key is written, also one equal to its code default.

What is a subclass of those is written as its base value (`_plain`): a `TypeModel` such as `Port`
as its number, and a `SecretStr` as its real text, never the mask (`str()` of it is the mask).
An `Enum` is written as its value; a path, a `Decimal` and a `UUID` as text. Anything else is a
`TypeError` before the file is opened: a file is written whole or not at all.

A `timedelta` is written as text in the short form (`1h30m`, `500ms`), which enforced reads back under
`coerce`, in every format.

Dates and times (`date`, `datetime`, `time`) are written so that loading gives them back: TOML as its
own literals, YAML as a bare timestamp (a `time` quoted: YAML has none), JSON and ini as ISO 8601 text,
which coercion reads. A `time` with an offset is ISO text in TOML too (TOML's time has no offset).

`None`: JSON and YAML write `null`; TOML and ini have no null, so the key is left out, and
loading the file gives the field its code default (the same where that default is `None`).
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any, Callable
from uuid import UUID
from enforced import dispatch
from enforced.data_types import SecretStr
from ._errors import MASK
from ._readers import CfgType, RawConfigData
import functools
import json
import math
import os
import re
import secrets
import stat
import tomli_w


def json_text(data: RawConfigData, *, indent: int | None = 2, mask: bool = False) -> str:
    """`json.dumps` (`ensure_ascii = False`); NaN and the infinities are refused (they are no JSON), with the key named."""
    plain = _plain(data, mask)
    _no_nan(plain, ())
    return json.dumps(plain, indent = indent, ensure_ascii = False, allow_nan = False, default = _iso) + "\n"


def toml_text(data: RawConfigData, *, mask: bool = False) -> str:
    """
    TOML: top-level keys first, then each section as a `[table]` and nested ones as `[db.replica]`; a
    list of dictionaries as `[[array]]`, a list of values on one line. Each value and key as tomli-w
    writes it (strings escaped, a key that isn't bare quoted, `inf` / `nan` as TOML has them). A `None`
    in a list is a `ValueError` (TOML has no null).
    """
    lines: list[str] = []
    _toml_table(lines, _plain(data, mask), (), header = False)
    return "\n".join(lines).lstrip("\n") + "\n"


def ini_text(data: RawConfigData, *, mask: bool = False) -> str:
    """
    ini, as `read_ini` reads it back: top-level keys under `[DEFAULT]`, a section as
    `[db]` and a nested one as `[db.replica]`, a list comma-joined (`hosts = a, b, c`;
    an empty list as `hosts =`). A bool as `true` / `false`. `ValueError` for what ini
    can't hold: a list item with a comma, a list of dictionaries, a value with a line break.
    """
    plain = _plain(data, mask)
    lines: list[str] = []
    top = {key: value for key, value in plain.items() if not isinstance(value, dict)}
    if top:
        _ini_section(lines, "DEFAULT", top, ())
    for key, value in plain.items():
        if isinstance(value, dict):
            _ini_sections(lines, value, (key,))
    return "\n".join(lines).lstrip("\n") + "\n"


def yaml_text(data: RawConfigData, *, mask: bool = False) -> str:
    """
    YAML, by PyYAML's `safe_dump` (PyYAML is needed to write it, as to read it): block style, two spaces
    of indent, a list too. A string is quoted when a YAML 1.1 or 1.2 reader would take it for something
    else (`no`, `on`, `y`, `08`, `1e3`, `null`, `~`, `12:30`), and wherever PyYAML would quote it (a
    space around it, `: `, ` #`, a special character first): other readers in a workflow get the string too.
    """
    return _yaml_dump(_plain(data, mask))


#* The text of each format: `mask = True` writes a secret as the mask (text that is shown), False as its real text (a file).
TEXTS: dict[CfgType, Callable[..., str]] = {"json": json_text, "toml": toml_text, "ini": ini_text, "yaml": yaml_text}


def write_json(file: str | os.PathLike[str], data: RawConfigData, *, indent: int | None = 2) -> None:
    """`json_text` as the whole of `file` (UTF-8)."""
    _save(file, json_text(data, indent = indent))


def write_toml(file: str | os.PathLike[str], data: RawConfigData) -> None:
    """`toml_text` as the whole of `file`."""
    _save(file, toml_text(data))


def write_ini(file: str | os.PathLike[str], data: RawConfigData) -> None:
    """`ini_text` as the whole of `file`."""
    _save(file, ini_text(data))


def write_yaml(file: str | os.PathLike[str], data: RawConfigData) -> None:
    """`yaml_text` as the whole of `file`."""
    _save(file, yaml_text(data))


WRITERS: dict[CfgType, Callable[[str | os.PathLike[str], RawConfigData], None]] = {
    "json": write_json, "toml": write_toml, "ini": write_ini, "yaml": write_yaml,
    }


# ── what every writer shares ────────────────────────────────────────────────

def _plain(value: Any, mask: bool = False) -> Any:
    """The value as the built-in types only (see the module's docstring); with `mask`, a `SecretStr` as the mask."""
    if value is None or type(value) in (bool, int, float, str):
        return value
    return _plain_of(value, mask)


#* Type strategy: each node is dispatched on its type to its handler. A handler for `date` doesn't take a
#* `datetime` (enforced 0.2.0: a datetime is no date there), so every one that takes both names both.
#* The dispatchers are made once, here:
#* one made inside the function that calls it is built again at every call (some 300 µs, against 0.2 µs).
@dispatch
def _plain_of(node: Any, mask: bool) -> Any:
    raise TypeError(f"a {type(node).__name__} can't be written to a configuration file")

@_plain_of.register
def _(node: None | date | datetime | time, mask: bool) -> None | date | datetime | time:
    return node

@_plain_of.register
def _(node: Enum, mask: bool) -> Any:
    return _plain(node.value, mask)

@_plain_of.register
def _(node: str, mask: bool) -> str:
    if mask and isinstance(node, SecretStr):
        return MASK
    return str.__str__(node)                                        # a SecretStr's real text, not str()'s mask

@_plain_of.register
def _(node: int, mask: bool) -> int:
    return int(node)

@_plain_of.register
def _(node: float, mask: bool) -> float:
    return float(node)

@_plain_of.register
def _(node: bool, mask: bool) -> bool:
    return bool(node)

@_plain_of.register
def _(node: PurePath | Decimal | UUID, mask: bool) -> str:
    return str(node)

@_plain_of.register
def _(node: timedelta, mask: bool) -> str:
    return duration_text(node)


#* The units of the short form, largest first, in microseconds: what enforced reads back under coerce.
_UNITS = (("d", 86_400_000_000), ("h", 3_600_000_000), ("m", 60_000_000), ("s", 1_000_000), ("ms", 1_000), ("us", 1))


def duration_text(value: timedelta) -> str:
    """
    A `timedelta` in the short form, as enforced reads it under `coerce`: `1h30m`, `500ms`, `1d2h3m4s`, `-5m`,
    `0s`. Exact: whole units only, down to the microsecond, so the value loads back the same.
    """
    total = (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds
    if total == 0:
        return "0s"
    sign, left = ("-" if total < 0 else ""), abs(total)
    parts = []
    for unit, size in _UNITS:
        count, left = divmod(left, size)
        if count:
            parts.append(f"{count}{unit}")
    return sign + "".join(parts)

@_plain_of.register
def _(node: dict, mask: bool) -> dict:
    return {_key(key): _plain(item, mask) for key, item in node.items()}

@_plain_of.register
def _(node: list | tuple | set | frozenset, mask: bool) -> list[Any]:
    return [_plain(item, mask) for item in node]


def plain_json(value: Any, mask: bool = True) -> Any:
    """`_plain`, and what JSON has no type for as text: a date, a datetime and a time in ISO 8601 (`to_dict(plain = True)`)."""
    return _json_of(_plain(value, mask))


@dispatch
def _json_of(node: Any) -> Any:
    return node

@_json_of.register
def _(node: date | datetime | time) -> str:
    return node.isoformat()

@_json_of.register
def _(node: dict) -> dict:
    return {key: _json_of(value) for key, value in node.items()}

@_json_of.register
def _(node: list) -> list:
    return [_json_of(value) for value in node]


def _no_nan(value: Any, path: tuple[Any, ...]) -> None:
    """A `ValueError` that names the key of a NaN or an infinity: JSON has neither."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        where = "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in path).lstrip(".")
        raise ValueError(f"{where}: JSON has no NaN and no infinity (TOML and YAML do)")
    if isinstance(value, dict):
        for key, item in value.items():
            _no_nan(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _no_nan(item, (*path, index))


def _key(key: Any) -> str:
    """A dictionary's key as text, which is all a file has: a number, an `Enum`, a `UUID` or a date as loading reads it back."""
    return _key_of(_plain(key))


@dispatch
def _key_of(node: Any) -> str:
    #* The type, not the key: it is plain by now, and a tuple with a SecretStr in it would be its real text.
    raise TypeError(f"a {type(node).__name__} is not a valid key for a configuration file")

@_key_of.register
def _(node: str) -> str:
    return node

@_key_of.register
def _(node: bool) -> str:
    return "true" if node else "false"

@_key_of.register
def _(node: int | float) -> str:
    return repr(node)

@_key_of.register
def _(node: date | datetime | time) -> str:
    return node.isoformat()


def _save(file: str | os.PathLike[str], text: str, *, new: bool = False) -> None:
    """
    `text` as the whole of `file`, or `file` as it was: written to a temporary file next to it, which
    then takes its place (`os.replace`: the old file or the new one, never a mixture). With `new`, only
    where there is no file: the name is taken first with an exclusive create (`FileExistsError` if anything
    is there, also a file made a moment ago), so no file of someone else's is ever replaced.
    """
    #* The text is made in full, and encoded, before anything is opened: an error leaves no half-written file.
    encoded = text.encode("utf-8")
    reserved = False
    if new:
        os.close(os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666))    # raises if the name is taken, a link too
        reserved = True
    try:
        _replace(file, encoded)
    except BaseException:
        if reserved:                                                # the empty file this call made: nobody else's
            try:
                os.unlink(file)
            except OSError:
                pass
        raise


def _replace(file: str | os.PathLike[str], encoded: bytes) -> None:
    """`encoded` in a temporary file next to `file`, which then takes its place."""
    target = os.path.realpath(file)                                 # a symbolic link stays one: what it points to is replaced
    if os.path.isdir(target):
        raise IsADirectoryError(f"{os.fspath(file)} is a directory: a configuration is written to a file")
    try:
        mode: int | None = stat.S_IMODE(os.stat(target).st_mode)    # a file that was there keeps its permission bits
    except FileNotFoundError:
        mode = None
    directory, name = os.path.split(target)
    #* The same directory: os.replace is atomic only within one file system. Not tempfile.mkstemp: its
    #* file is 0o600, and a new configuration would be readable by its owner only (0o666 goes through the umask).
    temporary = os.path.join(directory, f".{name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o666)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _iso(value: Any) -> str:
    """A date, a datetime or a time as ISO 8601 text (`json.dumps` calls this for what it doesn't know: only those)."""
    if isinstance(value, (date, time)):
        return value.isoformat()
    raise TypeError(f"a {type(value).__name__} can't be written to a configuration file")


def _number(value: int | float, inf: str, nan: str) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return nan
        if math.isinf(value):
            return inf if value > 0 else "-" + inf
        return repr(value)                                          # 0.1, 1e+16: valid in all three formats
    return str(value)


# ── TOML ────────────────────────────────────────────────────────────────────

def _toml_scalar(node: Any) -> str:
    """A text, number, bool, date or time as tomli-w writes it."""
    return tomli_w.dumps({"v": node})[len("v = "):-1]


def _toml_key(key: str) -> str:
    """A key as tomli-w writes it: bare where it can be, quoted otherwise."""
    return tomli_w.dumps({key: 0})[:-len(" = 0\n")]


@dispatch
def _toml_value(node: Any, where: str) -> str:
    """A plain value as TOML writes it; `where` is its key, for the message of what TOML can't hold."""
    raise ValueError(f"{where}: TOML has no null, and a list can't hold one")


@_toml_value.register
def _(node: bool | int | float | str | date | datetime, where: str) -> str:
    return _toml_scalar(node)


@_toml_value.register
def _(node: time, where: str) -> str:
    if node.tzinfo is not None:
        return _toml_scalar(node.isoformat())                       # TOML's local time has no offset
    return _toml_scalar(node)


@_toml_value.register
def _(node: list, where: str) -> str:
    return "[" + ", ".join(_toml_value(item, where) for item in node) + "]"


@_toml_value.register
def _(node: dict, where: str) -> str:
    return "{" + ", ".join(f"{_toml_key(k)} = {_toml_value(v, where)}" for k, v in node.items() if v is not None) + "}"


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


@dispatch
def _ini_text(node: Any, where: str) -> str:
    raise ValueError(f"{where}: ini can't hold a {type(node).__name__} here")


@_ini_text.register
def _(node: bool, where: str) -> str:
    return "true" if node else "false"


@_ini_text.register
def _(node: int | float, where: str) -> str:
    return _number(node, "inf", "nan")


@_ini_text.register
def _(node: str, where: str) -> str:
    if "\n" in node or "\r" in node:
        raise ValueError(f"{where}: ini can't hold a value with a line break")
    if node != node.strip():
        raise ValueError(f"{where}: ini drops the spaces around a value, so this one would change")
    return node


@_ini_text.register
def _(node: date | datetime | time, where: str) -> str:
    return node.isoformat()


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
                if isinstance(item, dict):
                    raise ValueError(f"{where}: ini can't hold a list of sections: use JSON, TOML or YAML for this configuration")
                if isinstance(item, list) or item is None:
                    raise ValueError(f"{where}: ini can't hold a list of {type(item).__name__}")
                text = _ini_text(item, where)
                if "," in text:
                    raise ValueError(f"{where}: an item of an ini list can't hold a comma")
                items.append(text)
            if items == [""]:
                raise ValueError(f"{where}: ini can't tell a list of one empty text from an empty list")
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
#* and timestamps. A string that matches is written quoted, where PyYAML (1.1 only, and no y / n) might not.
_YAML_SPECIAL = re.compile(r"""(?x)
    (?: y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF
      | null|Null|NULL|~
      | [-+]?[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?
      | [-+]?\.[0-9_]+(?:[eE][-+]?[0-9]+)?
      | [-+]?0[0-7_]+ | [-+]?0o[0-7]+ | [-+]?0x[0-9a-fA-F_]+ | [-+]?0b[01_]+
      | [-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?
      | [-+]?\.(?:inf|Inf|INF) | \.(?:nan|NaN|NAN)
      | [0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt ].*)?
      | << | =
    )""")


@functools.cache
def _yaml_dumper() -> Any:
    """PyYAML's `SafeDumper` with our rules: a string a reader would misread is quoted, a time is text, a list is indented."""
    import yaml                                                     # PyYAML is an extra: imported where it is needed

    class Dumper(yaml.SafeDumper):
        def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
            return super().increase_indent(flow, False)             # a list under its key is indented, as a section is

    def text(dumper: yaml.SafeDumper, value: str) -> Any:
        if not value.isprintable():                                 # escaped: raw, a NEL or a line separator is a line break to YAML
            return dumper.represent_scalar("tag:yaml.org,2002:str", value, style = '"')
        if _YAML_SPECIAL.fullmatch(value):
            return dumper.represent_scalar("tag:yaml.org,2002:str", value, style = "'")
        return dumper.represent_str(value)

    def clock(dumper: yaml.SafeDumper, value: time) -> Any:
        return text(dumper, value.isoformat())                      # YAML has no time: text (12:30:00 is a number to YAML 1.1)

    Dumper.add_representer(str, text)
    Dumper.add_representer(time, clock)
    return Dumper


def _yaml_dump(value: Any) -> str:
    try:
        import yaml
    except ModuleNotFoundError as missing:
        raise ModuleNotFoundError(
            "writing YAML needs PyYAML: pip install cfgcast[yaml] (or pip install PyYAML)", name = "yaml"
            ) from missing
    return yaml.dump(
        value, Dumper = _yaml_dumper(), sort_keys = False, allow_unicode = True, default_flow_style = False, width = 1 << 30)


def _yaml_key(key: str) -> str:
    """A key as the YAML writer writes it."""
    return _yaml_dump({key: None})[:-len(": null\n")]


def _yaml_mapping(lines: list[str], mapping: dict[str, Any], indent: int) -> None:
    """The lines of `mapping` as the YAML writer writes it, each indented by `indent`."""
    lines += [" " * indent + line for line in _yaml_dump(mapping).splitlines()]
