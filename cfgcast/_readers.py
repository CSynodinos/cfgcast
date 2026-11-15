"""
One reader per format: a file in, a plain dictionary out. Readers don't know the schema, except
`read_ini`, which needs it to know which values are lists.

A file that isn't valid in its format raises what its parser raises (`json.JSONDecodeError`,
`tomllib.TOMLDecodeError`, `configparser.Error`, `yaml.YAMLError`); a file whose top level
isn't a mapping, or an ini file whose sections clash, is a `ValueError`.
"""
from typing import Any, Literal, get_type_hints
from ._hints import is_list, section_of, split_list
import collections.abc
import configparser
import json
import os
import re
import tomllib


type RawConfigData = dict[str, Any]
type CfgType = Literal["json", "toml", "ini", "yaml"]


def read_json(file: str | os.PathLike[str]) -> RawConfigData:
    """`json.load`; a top level that isn't an object is an error."""
    with open(file, encoding = "utf-8") as stream:
        return _mapping(json.load(stream), file)


def read_toml(file: str | os.PathLike[str]) -> RawConfigData:
    """`tomllib.load` (the file opened in binary mode, as tomllib asks)."""
    with open(file, "rb") as stream:
        return tomllib.load(stream)                                 # a TOML document is always a table


def read_ini(file: str | os.PathLike[str], schema: type | None = None) -> RawConfigData:
    """
    `configparser`: each section a nested dictionary, keys as written (`optionxform = str`).
    `[DEFAULT]` holds the top-level keys, and is not copied into the other sections. A dotted
    section is nesting (`[db.replica]`: `replica` inside `db`). A value is split on commas
    into a list where the schema's field is a `list[...]` (`hosts = a, b, c`; `hosts =` is an
    empty list). Interpolation is off: a `%` is a `%`. A field that is a list of sections
    (`servers: list[Server]`) can't be given in ini: a `ValueError` that names it.
    """
    #* default_section names a section nobody writes, so [DEFAULT] is an ordinary one: its keys
    #* are not copied into the others (they are the top-level keys).
    parser = configparser.ConfigParser(interpolation = None, default_section = "\0no default\0", strict = True)
    parser.optionxform = str                                        # type: ignore[assignment,method-assign]  # keys as written
    with open(file, encoding = "utf-8") as stream:
        parser.read_file(stream)
    data: RawConfigData = {}
    for section in parser.sections():
        values = dict(parser.items(section, raw = True))
        if section == "DEFAULT":
            _merge(data, values, (), file)
            continue
        if any(not part for part in section.split(".")):
            raise ValueError(f"{os.fspath(file)}: section [{section}] has an empty part in its name")
        target = data
        for depth, part in enumerate(parts := section.split(".")):
            existing = target.setdefault(part, {})
            if not isinstance(existing, dict):
                raise ValueError(
                    f"{os.fspath(file)}: [{section}] clashes with the key "      # never its value: it may be a secret
                    f"{'.'.join(parts[:depth + 1])}"
                    )
            target = existing
        _merge(target, values, tuple(parts), file)
    if schema is not None:
        _split_lists(data, schema, file)
    return data


def read_yaml(file: str | os.PathLike[str]) -> RawConfigData:
    """
    `yaml.safe_load` with YAML 1.2 booleans: a `SafeLoader` whose bool resolver matches only
    `true` / `false`, so `country: no` stays `"no"` (a `bool` field still takes `yes`
    through enforced's coercion). PyYAML is imported here and only here: a `ModuleNotFoundError`
    that says `pip install cfgcast[yaml]` when it is missing. An empty file is an empty mapping.
    """
    with open(file, encoding = "utf-8") as stream:
        yaml, loader = _yaml()
        return _mapping(yaml.load(stream, Loader = loader), file, empty_is_none = True)


CONFIG_READERS: dict[str, collections.abc.Callable[..., RawConfigData]] = {"json": read_json, "toml": read_toml, "ini": read_ini, "yaml": read_yaml}


# ── helpers ─────────────────────────────────────────────────────────────────

def _mapping(data: Any, file: str | os.PathLike[str], empty_is_none: bool = False) -> RawConfigData:
    if data is None and empty_is_none:                              # an empty YAML file (a JSON null is no mapping)
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{os.fspath(file)}: the top level must be a mapping of keys, got {type(data).__name__}")
    return data


def _merge(target: RawConfigData, values: dict[str, str], where: tuple[str, ...], file: str | os.PathLike[str]) -> None:
    """The keys of one ini section into its dictionary; a key that is also a section is a clash."""
    for key, value in values.items():
        if isinstance(target.get(key), dict):
            raise ValueError(f"{os.fspath(file)}: the key {'.'.join((*where, key))} clashes with a section of that name")
        target[key] = value


def _hints(schema: type) -> dict[str, Any]:
    try:
        return get_type_hints(schema, include_extras = True)
    except Exception:                                               # unresolvable: nothing is split; build reports it
        return {}


def _split_lists(data: RawConfigData, schema: type, file: str | os.PathLike[str], path: tuple[str, ...] = ()) -> None:
    """
    In place: the text of each field the schema types as a list, split on commas, in every section (also
    the sections of a `dict[str, Section]`, which ini writes as `[field.name]`). A `ValueError` for a
    list of sections given as text: ini can't hold one.
    """
    for name, hint in _hints(schema).items():
        if name not in data:
            continue
        value, found = data[name], section_of(hint)
        if found is None:
            if isinstance(value, str) and is_list(hint):
                data[name] = split_list(value)
            continue
        kind, section = found
        if kind == "one" and isinstance(value, dict):
            _split_lists(value, section, file, (*path, name))
        elif kind == "dict" and isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, dict):
                    _split_lists(item, section, file, (*path, name, key))
        elif kind in ("list", "tuple") and isinstance(value, str):
            raise ValueError(
                f"{os.fspath(file)}: {'.'.join((*path, name))} is a list of sections ({section.__name__}), "
                f"which ini can't hold: use JSON, TOML or YAML for this configuration"
                )


_YAML: tuple[Any, Any] | None = None


def _yaml() -> tuple[Any, Any]:
    """PyYAML and a `SafeLoader` class with the YAML 1.2 booleans, made once (imported on first use)."""
    global _YAML
    if _YAML is None:
        try:
            import yaml
        except ModuleNotFoundError as missing:
            raise ModuleNotFoundError(
                "reading YAML needs PyYAML: pip install cfgcast[yaml] (or pip install PyYAML)", name = "yaml"
                ) from missing

        class Loader(yaml.SafeLoader):
            pass

        bool_tag = "tag:yaml.org,2002:bool"
        #* Each loader class has its own table (PyYAML copies it on the first add): the bool resolver
        #* is taken out of the copy and put back with the two YAML 1.2 spellings only.
        Loader.yaml_implicit_resolvers = {
            first: [(tag, regexp) for tag, regexp in resolvers if tag != bool_tag]
            for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
            }
        Loader.add_implicit_resolver(bool_tag, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))
        _YAML = (yaml, Loader)
    return _YAML
