"""
From a dictionary to a schema instance. enforced checks and coerces each value; this module
does what it doesn't: sections made from sub-dictionaries, unknown and missing keys, and every
problem gathered with its key path before anything is raised.

One pass per level: the keys are sorted into known and unknown, each section is built first
(its problems collected, its place left empty if it failed), and the class is then constructed
once with what is left. enforced reports every wrong value of that construction at once
(`InvalidDataError.errors()`); a key that is missing, here or below, is reported by this module
and enforced's report of it is dropped, so nothing is said twice.

`strict = False` holds for everything below the class it is written on: a newer file can add a key at
any level. What that ignores is gathered too, and said in one `ConfigWarning`.
"""
from dataclasses import MISSING, fields, is_dataclass
from types import NoneType
from typing import Any, get_type_hints
from enforced import InvalidDataError
import os
import warnings
from ._base import OPTIONS_ATTR, is_config
from ._errors import MASK, ConfigError, ConfigWarning, Problem, members as _members, mentions_secret as _mentions_secret

_OUR_FILES = (os.path.dirname(os.path.abspath(__file__)) + os.sep,)


def build[T](schema: type[T], data: dict[str, Any], file: str, show_warnings: bool = True) -> T:
    """
    `schema` from `data`; a `ConfigError` with all of `problems_of` if there are any. Unknown keys that
    were ignored are warned about first, also when an error follows: `prot` ignored explains `port` missing.
    """
    if type(show_warnings) is not bool:
        raise TypeError(f"show_warnings must be True or False, got {show_warnings!r}")
    ignored: list[Problem] = []
    built, problems = _build(schema, data, (str(file),), False, ignored)
    if ignored and show_warnings:
        #* skip_file_prefixes: the warning points at the caller's line, whichever way in was taken.
        warnings.warn(ConfigWarning(str(file), schema, ignored), skip_file_prefixes = _OUR_FILES)
    if problems:
        raise ConfigError(str(file), schema, problems)
    return built


def problems_of(schema: type, data: dict[str, Any], loc: tuple[str | int, ...]) -> list[Problem]:
    """
    Everything wrong at this level and below: unknown keys (when strict here and above), missing keys (no
    default), a section that isn't a dictionary, and the enforced errors of the values
    (`InvalidDataError.errors()`, their `loc` put after this one).
    """
    return _build(schema, data, loc, False, [])[1]


# ── one level ───────────────────────────────────────────────────────────────

def _build(
    schema: type,
    data: Any,
    loc: tuple[str | int, ...],
    lenient: bool,
    ignored: list[Problem]) -> tuple[Any, list[Problem]]:
    """`(instance, [])`, or `(None, problems)`. `lenient`: a class above said `strict = False`; `ignored` is added to."""
    if not isinstance(data, dict):
        return None, [
                {
                "type": "section",
                "loc": list(loc),
                "msg": f"expected a section of keys, got {type(data).__name__}",
                "input": _shown(data),
                "ctx": {"expected": getattr(schema, "__name__", str(schema))}
                }
            ]
    strict = _options(schema)[0] and not lenient
    hints = _hints(schema)
    keys = {f.name: f for f in fields(schema) if f.init}
    problems: list[Problem] = []
    arguments: dict[str, Any] = {}
    secret = set()

    for key, value in data.items():
        if key not in keys:
            #* No "input": whether the value of a key nobody declared is a secret, nothing can say.
            (problems if strict else ignored).append(
                {
                    "type": "unknown",
                    "loc": [*loc, key],
                    "msg": "key not in the schema",
                    "ctx": {"schema": schema.__name__}
                }
            )
            continue
        hint = hints.get(key)
        if _mentions_secret(hint):
            secret.add(key)
        section = _section(hint)
        if section is not None and not (value is None and _optional(hint)):
            built, below = _build(section, value, (*loc, key), not strict, ignored)
            if below:
                problems += below
                continue                                            # left out: enforced would call it missing
            value = built
        arguments[key] = value

    for key, field in keys.items():
        if key not in data and field.default is MISSING and field.default_factory is MISSING:
            problems.append({"type": "missing", "loc": [*loc, key], "msg": "missing key",
                             "ctx": {"schema": schema.__name__}})

    try:
        instance = schema(**arguments)
    except InvalidDataError as error:
        for entry in error.errors():
            if entry["type"] == "missing":                          # said above, or a section that failed below
                continue
            entry["loc"] = [*loc, *entry["loc"]]
            if entry["loc"][len(loc)] in secret and "input" in entry:
                entry["input"] = MASK
            problems.append(entry)
        return None, problems
    return (None, problems) if problems else (instance, [])


# ── what a schema says ──────────────────────────────────────────────────────

def _options(schema: type) -> tuple[bool, bool]:
    return vars(schema).get(OPTIONS_ATTR, (True, True))


def _hints(schema: type) -> dict[str, Any]:
    return get_type_hints(schema, include_extras = True)


def _optional(hint: Any) -> bool:
    return NoneType in _members(hint)


def _section(hint: Any) -> type | None:
    """The configuration class a field is a section of (`Database`, `Database | None`), else None."""
    for member in _members(hint):
        if isinstance(member, type) and (is_config(member) or is_dataclass(member)):
            return member
    return None


_SHOWN_TEXT = 200


def _shown(value: Any) -> Any:
    """The input of a problem this module finds: JSON-safe and bounded (enforced's own are already)."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _SHOWN_TEXT else value[:_SHOWN_TEXT] + "..."
    if isinstance(value, dict):
        return {str(k): _shown(v) for k, v in list(value.items())[:50]}
    if isinstance(value, (list, tuple)):
        return [_shown(v) for v in value[:50]]
    return repr(value)[:_SHOWN_TEXT]
