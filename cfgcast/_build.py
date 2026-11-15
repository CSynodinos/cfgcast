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
from dataclasses import MISSING, fields
from types import NoneType
from typing import Any, get_type_hints
from enforced import InvalidDataError
import os
import warnings
from ._base import OPTIONS_ATTR
from ._errors import MASK, ConfigError, ConfigWarning, Problem
from ._hints import SectionKind, members as _members, mentions_secret as _mentions_secret, section_of as _section_of
from ._sources import Path, Sources

_OUR_FILES = (os.path.dirname(os.path.abspath(__file__)) + os.sep,)


def build[T](schema: type[T], data: dict[str, Any], file: str | Sources, show_warnings: bool = True) -> T:
    """
    `schema` from `data`; a `ConfigError` with all of `problems_of` if there are any. Unknown keys that
    were ignored are warned about first, also when an error follows: `prot` ignored explains `port` missing.
    `file` is the one file the data came from, or the `Sources` of a load from several.
    """
    if type(show_warnings) is not bool:
        raise TypeError(f"show_warnings must be True or False, got {show_warnings!r}")
    sources = file if isinstance(file, Sources) else Sources((str(file),))
    ignored: list[Problem] = []
    built, problems = _build(schema, data, (), False, ignored, sources)
    if ignored and show_warnings:
        #* skip_file_prefixes: the warning points at the caller's line, whichever way in was taken.
        warnings.warn(ConfigWarning(sources.names, schema, ignored), skip_file_prefixes = _OUR_FILES)
    if problems:
        raise ConfigError(sources.names, schema, problems)
    return built


def problems_of(schema: type, data: dict[str, Any], loc: tuple[str | int, ...]) -> list[Problem]:
    """
    Everything wrong at this level and below: unknown keys (when strict here and above), missing keys (no
    default), a section that isn't a dictionary, and the enforced errors of the values
    (`InvalidDataError.errors()`, their `loc` put after this one). `loc` is the file, then the key path so far.
    """
    return _build(schema, data, tuple(loc[1:]), False, [], Sources((str(loc[0]),)))[1]


# ── one level ───────────────────────────────────────────────────────────────

def _build(
    schema: type,
    data: Any,
    path: Path,
    lenient: bool,
    ignored: list[Problem],
    sources: Sources) -> tuple[Any, list[Problem]]:
    """
    `(instance, [])`, or `(None, problems)`. `path` is the key path of this level (a problem's `loc` is its
    source, then its path); `lenient`: a class above said `strict = False`; `ignored` is added to.
    """
    if not isinstance(data, dict):
        return None, [_not_sections("one", schema, data, path, sources)]
    strict = _options(schema)[0] and not lenient
    hints = _hints(schema)
    keys = {f.name: f for f in fields(schema) if f.init}
    problems: list[Problem] = []
    arguments: dict[str, Any] = {}
    secret = set()

    for key, value in data.items():
        here = (*path, key)
        if key not in keys:
            #* No "input": whether the value of a key nobody declared is a secret, nothing can say.
            (problems if strict else ignored).append(
                {
                    "type": "unknown",
                    "loc": [sources.of(here), *here],
                    "msg": "key not in the schema",
                    "ctx": {"schema": schema.__name__}
                }
            )
            continue
        hint = hints.get(key)
        if _mentions_secret(hint):
            secret.add(key)
        found = _section_of(hint)
        if found is not None and not (value is None and _optional(hint)):
            built, below = _sections(found[0], found[1], value, here, not strict, ignored, sources)
            if below:
                problems += below
                continue                                            # left out: enforced would call it missing
            value = built
        arguments[key] = value

    for key, field in keys.items():
        if key not in data and field.default is MISSING and field.default_factory is MISSING:
            #* The last file: a key that no layer has is missing from the one that had the last word.
            problems.append({"type": "missing", "loc": [sources.last, *path, key], "msg": "missing key",
                             "ctx": {"schema": schema.__name__}})

    try:
        instance = schema(**arguments)
    except InvalidDataError as error:
        for entry in error.errors():
            if entry["type"] == "missing":                          # said above, or a section that failed below
                continue
            if entry["loc"] and entry["loc"][0] in secret and "input" in entry:
                entry["input"] = MASK
            here = (*path, *entry["loc"])
            entry["loc"] = [sources.of(here), *here]
            problems.append(entry)
        return None, problems
    return (None, problems) if problems else (instance, [])


def _sections(
    kind: SectionKind,
    section: type,
    value: Any,
    path: Path,
    lenient: bool,
    ignored: list[Problem],
    sources: Sources) -> tuple[Any, list[Problem]]:
    """The value of a field of sections, built: one, or each item of a list / dictionary of them (its index or key in the path)."""
    if kind == "one":
        return _build(section, value, path, lenient, ignored, sources)
    pairs: list[tuple[Any, Any]]
    if kind == "dict" and isinstance(value, dict):
        pairs = list(value.items())
    elif kind != "dict" and isinstance(value, (list, tuple)):
        pairs = list(enumerate(value))
    else:
        return None, [_not_sections(kind, section, value, path, sources)]
    problems: list[Problem] = []
    items = []
    for key, item in pairs:
        built, below = _build(section, item, (*path, key), lenient, ignored, sources)
        problems += below
        items.append((key, built))
    if problems:
        return None, problems
    if kind == "dict":
        return dict(items), []
    return (tuple if kind == "tuple" else list)(built for _, built in items), []


_EXPECTED = {"one": "a section of keys", "list": "a list of sections", "tuple": "a list of sections", "dict": "sections by name"}


def _not_sections(kind: SectionKind, section: type, value: Any, path: Path, sources: Sources) -> Problem:
    """
    The problem of a value that isn't what a field of sections takes. No "input": what stands in a
    section's place may hold the secrets of that section.
    """
    name = getattr(section, "__name__", str(section))
    return {
        "type": "section",
        "loc": [sources.of(path), *path],
        "msg": f"expected {_EXPECTED[kind]}, got {type(value).__name__}",
        "ctx": {"expected": {"one": name, "list": f"list[{name}]", "tuple": f"tuple[{name}, ...]", "dict": f"dict[str, {name}]"}[kind]}
        }


# ── what a schema says ──────────────────────────────────────────────────────

def _options(schema: type) -> tuple[bool, bool]:
    return vars(schema).get(OPTIONS_ATTR, (True, True))


def _hints(schema: type) -> dict[str, Any]:
    return get_type_hints(schema, include_extras = True)


def _optional(hint: Any) -> bool:
    return NoneType in _members(hint)
