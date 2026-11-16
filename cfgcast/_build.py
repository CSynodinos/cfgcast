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
from dataclasses import MISSING, dataclass, field as _field, is_dataclass
from types import NoneType
from typing import Any, get_type_hints
from enforced import InvalidDataError
from enforced.data_types import SecretStr
import os
import warnings
from ._errors import MASK, ConfigError, ConfigWarning, Problem
from ._hints import SectionKind, members as _members, mentions_secret as _mentions_secret, keys_of as _keys_of, options_of as _options, section_of as _section_of
from ._sources import SOURCES_ATTR, Path, Sources

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
    run = _Run(sources)
    built, problems = _build(schema, data, (), False, run)
    if run.ignored and show_warnings:
        #* skip_file_prefixes: the warning points at the caller's line, whichever way in was taken.
        warnings.warn(ConfigWarning(sources.names, schema, run.ignored), skip_file_prefixes = _OUR_FILES)
    if problems:
        #* The first exception of a __post_init__ is the cause: its traceback is there for a debugger.
        raise ConfigError(sources.names, schema, problems) from (run.raised[0] if run.raised else None)
    return built


def problems_of(schema: type, data: dict[str, Any], loc: tuple[str | int, ...]) -> list[Problem]:
    """
    Everything wrong at this level and below: unknown keys (when strict here and above), missing keys (no
    default), a section that isn't a dictionary, and the enforced errors of the values
    (`InvalidDataError.errors()`, their `loc` put after this one). `loc` is the file, then the key path so far.
    """
    return _build(schema, data, tuple(loc[1:]), False, _Run(Sources((str(loc[0]),))))[1]


@dataclass
class _Run:
    """What one `build` gathers besides the problems: the keys a lenient schema ignored, the exceptions of `__post_init__`s."""
    sources: Sources
    ignored: list[Problem] = _field(default_factory = list)
    raised: list[Exception] = _field(default_factory = list)


# ── one level ───────────────────────────────────────────────────────────────

def _build(schema: type, data: Any, path: Path, lenient: bool, run: _Run) -> tuple[Any, list[Problem]]:
    """
    `(instance, [])`, or `(None, problems)`. `path` is the key path of this level (a problem's `loc` is its
    source, then its path); `lenient`: a class above said `strict = False`; `run.ignored` is added to.
    """
    sources, ignored = run.sources, run.ignored
    if not isinstance(data, dict):
        return None, [_not_sections("one", schema, data, path, sources)]
    strict = _options(schema).strict and not lenient
    hints = _hints(schema)
    keys = _keys_of(schema)                                         # the key of a file -> its field
    names = {field.name: key for key, field in keys.items()}
    problems: list[Problem] = []
    arguments: dict[str, Any] = {}
    secret = set()

    for key, value in data.items():
        here = (*path, key)
        if key not in keys:
            #* No "input": whether the value of a key nobody declared is a secret, nothing can say.
            said = "key not in the schema"
            if names.get(key, key) != key:                          # the field's name, where the file has another key for it
                said += f" (the key of this field is {names[key]!r})"
            (problems if strict else ignored).append(sources.problem("unknown", here, said, {"schema": schema.__name__}))
            continue
        name = keys[key].name
        hint = hints.get(name)
        if _mentions_secret(hint):
            secret.add(name)
        found = _section_of(hint)
        if found is not None and not (value is None and _optional(hint)):
            built, below = _sections(found[0], found[1], value, here, not strict, run)
            if below:
                problems += below
                continue                                            # left out: enforced would call it missing
            value = built
        arguments[name] = value

    for key, field in keys.items():
        if key in data:
            continue
        if field.default is MISSING and field.default_factory is MISSING:
            #* The last file: a key that no layer has is missing from the one that had the last word.
            problems.append(sources.problem("missing", (*path, key), "missing key", {"schema": schema.__name__}, sources.last))
        else:
            sources.defaults.add((*path, key))

    try:
        instance = schema(**arguments)
    except InvalidDataError as error:
        for entry in error.errors():
            if entry["type"] == "missing":                          # said above, or a section that failed below
                continue
            if entry["loc"] and entry["loc"][0] in secret and "input" in entry:
                entry["input"] = MASK
            #* enforced says the field's name: the file's key in its place.
            inner = [names.get(entry["loc"][0], entry["loc"][0]), *entry["loc"][1:]] if entry["loc"] else []
            problems.append(sources.placed(entry, (*path, *inner)))
        return None, problems
    except Exception as error:                                      # a __post_init__ that raised: not BaseException (Ctrl-C goes through)
        run.raised.append(error)
        #* No "input": the exception is about the whole section, which may hold its secrets. The text is
        #* the user's own: the secrets this level knows are taken out of it.
        text = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
        for known in sorted(_secret_texts(arguments, secret), key = len, reverse = True):
            text = text.replace(known, MASK)
        problems.append(sources.problem("post_init", path, text, {"schema": schema.__name__, "exception": type(error).__name__}))
        return None, problems
    if problems:
        return None, problems
    #* For sources_of: where this instance was built from. Not a field: equality, repr and to_dict don't see it.
    object.__setattr__(instance, SOURCES_ATTR, (sources, path))
    return instance, []


def _secret_texts(arguments: dict[str, Any], secret: set[str]) -> set[str]:
    """The real text of every secret among the arguments of a class: its own `SecretStr` fields, and those of the sections built below."""
    found: set[str] = set()

    def walk(value: Any, is_secret: bool) -> None:
        if isinstance(value, SecretStr):
            found.add(value.get_secret_value())
        elif isinstance(value, str):
            if is_secret:
                found.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item, is_secret)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                walk(item, is_secret)
        elif is_dataclass(value) and not isinstance(value, type):
            for name in getattr(value, "__dataclass_fields__", {}):
                walk(getattr(value, name, None), False)

    for key, value in arguments.items():
        walk(value, key in secret)
    return {text for text in found if text}                         # an empty text would be "replaced" everywhere


def _sections(
    kind: SectionKind,
    section: type,
    value: Any,
    path: Path,
    lenient: bool,
    run: _Run) -> tuple[Any, list[Problem]]:
    """The value of a field of sections, built: one, or each item of a list / dictionary of them (its index or key in the path)."""
    if kind == "one":
        return _build(section, value, path, lenient, run)
    pairs: list[tuple[Any, Any]]
    if kind == "dict" and isinstance(value, dict):
        pairs = list(value.items())
    elif kind != "dict" and isinstance(value, (list, tuple)):
        pairs = list(enumerate(value))
    else:
        return None, [_not_sections(kind, section, value, path, run.sources)]
    problems: list[Problem] = []
    items = []
    for key, item in pairs:
        built, below = _build(section, item, (*path, key), lenient, run)
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
    expected = {"one": name, "list": f"list[{name}]", "tuple": f"tuple[{name}, ...]", "dict": f"dict[str, {name}]"}[kind]
    return sources.problem("section", path, f"expected {_EXPECTED[kind]}, got {type(value).__name__}", {"expected": expected})


# ── what a schema says ──────────────────────────────────────────────────────

def _hints(schema: type) -> dict[str, Any]:
    return get_type_hints(schema, include_extras = True)


def _optional(hint: Any) -> bool:
    return NoneType in _members(hint)
