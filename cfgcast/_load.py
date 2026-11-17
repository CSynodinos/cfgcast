"""`Config.load`: read a file (or several, in layers, a `.env` file and the environment over them) and build the schema from it."""
from ._errors import ConfigError, Problem
from ._readers import PARSERS, CfgType, RawConfigData, split_lists, text_of
from ._base import SUFFIXES, File, _format_of, is_config
from ._build import build
from ._hints import options_of
from ._locate import locate
from ._sources import OVERRIDES, OptionalFile, Path, Sources, field_of, is_absent, layer, read_env, read_env_file, text_value
from collections.abc import Mapping, Sequence
from typing import Any, Literal
import os

__all__ = ["SUFFIXES", "load", "parse_overrides"]

def load(
    schema: type,
    file: File | Sequence[File],
    cfg_type: str,
    *,
    env_prefix: str | None = None,
    env: Mapping[str, str] | None = None,
    env_file: File | None = None,
    section: str | Sequence[str] | None = None,
    overrides: Mapping[str, Any] | None = None) -> Any:
    """What `Config.load` does (its docstring says how)."""
    if env_prefix is not None and not isinstance(env_prefix, str):
        raise TypeError(f"env_prefix must be text (or None for no environment variables), got {env_prefix!r}")
    prefix = env_prefix if env_prefix is not None else options_of(schema).env_prefix
    for name, given in (("env", env), ("env_file", env_file)):
        if given is not None and prefix is None:
            raise TypeError(f"{name} is read only with an env_prefix (env_prefix = \"\" takes every variable that names a field)")
    if env_file is not None and not isinstance(env_file, (str, os.PathLike, OptionalFile)):
        raise TypeError(f"env_file must be a path, got {env_file!r}")
    within = _section_path(section)
    if overrides is not None and not isinstance(overrides, Mapping):
        raise TypeError(f"overrides must be a dictionary of keys, got {type(overrides).__name__}")
    one = isinstance(file, (str, os.PathLike, OptionalFile))
    if not one and not isinstance(file, (list, tuple)):
        raise TypeError(f"file must be a path or a list of paths, got {file!r}")
    files: list[File] = [file] if one else list(file)               # type: ignore[list-item, arg-type]
    for layer_file in files:
        if not isinstance(layer_file, (str, os.PathLike, OptionalFile)):
            raise TypeError(f"file must be a path or a list of paths, got {layer_file!r} in the list")
    if not files and prefix is None and overrides is None:
        raise ValueError("no file to load: the list of files is empty")
    #* Every format is known before any file is read: a wrong suffix in the last layer is said first,
    #* also that of an optional file that isn't there (a mistake in the program, not a missing file).
    formats = [_format_of(_path(layer_file), cfg_type) for layer_file in files]

    #* Each layer: the source's name, the key path it gives (a variable; () for a file), its data, its line.
    layers: list[tuple[str, Path, RawConfigData, int | None]] = []
    names: list[str] = []
    last: str | None = None
    absent: list[Problem] = []                                      # a file that has no such section
    lines: dict[str, dict[Path, int]] = {}
    for layer_file, selected_cfg_type in zip(files, formats):
        path = _path(layer_file)
        try:
            text = text_of(path, selected_cfg_type)
        except FileNotFoundError:
            if isinstance(layer_file, OptionalFile) and is_absent(path):
                continue
            raise
        last = os.fspath(path)
        names.append(last)
        data = PARSERS[selected_cfg_type](text, path)
        lines[last] = locate(selected_cfg_type, text, data)         # of the whole file: a section is found by its whole path
        for depth, key in enumerate(within):
            #* No "input": what stands in a section's place may hold its secrets.
            if key not in data:
                absent.append({"type": "missing", "loc": [last, *within[:depth + 1]], "msg": "missing section",
                               "ctx": {"schema": schema.__name__}})
                break
            if not isinstance(data[key], dict):
                absent.append({"type": "section", "loc": [last, *within[:depth + 1]],
                               "msg": f"expected a section of keys, got {type(data[key]).__name__}",
                               "ctx": {"expected": schema.__name__}})
                break
            data = data[key]
        else:
            if selected_cfg_type == "ini":                          # lists are split where the schema starts
                split_lists(data, schema, path, tuple(within))
            layers.append((last, (), data, None))
    file_names = frozenset(names)
    if prefix is not None:
        if env_file is not None:
            try:
                in_file = read_env_file(_path(env_file))
            except FileNotFoundError:
                if not (isinstance(env_file, OptionalFile) and is_absent(_path(env_file))):
                    raise
            else:
                name = os.fspath(_path(env_file))
                names.append(name)
                for variable, path_, data in read_env(schema, prefix, {key: text for key, (text, _) in in_file.items()}):
                    layers.append((name, path_, data, in_file[variable][1]))
        variables = read_env(schema, prefix, os.environ if env is None else env)
        if variables or not names:
            names.append(f"{prefix}*")                              # the environment, in the head of an error
        layers += [(variable, path_, data, None) for variable, path_, data in variables]
    if overrides:
        names.append(OVERRIDES)
        layers.append((OVERRIDES, (), _copied(overrides), None))    # a copy: a layer is merged into, the caller's is not

    if absent:
        raise ConfigError(tuple(names), schema, absent)
    sources = Sources(tuple(names), within, file_names)
    sources.lines.update(lines)
    if last is not None:
        sources.last = last                                         # a missing key names the last file
    merged: RawConfigData = {}
    for name, path_, data, line in layers:
        layer(merged, data, name, sources)
        if line is not None:
            sources.lines.setdefault(name, {})[path_] = line
    return build(schema, merged, sources)


def parse_overrides(arguments: Sequence[str], schema: type) -> dict[str, Any]:
    """
    The `overrides` of `Config.load` from texts as a command line has them: `["db.port=9090", "debug=true"]` is
    `{"db": {"port": "9090"}, "debug": "true"}`. A dot between levels, the value everything after the first
    `=`. Values stay text (the load converts them), except where the schema's field is a list (`hosts=a,b`
    or a JSON array) or a dictionary (a JSON object), as for an environment variable. A key is matched to
    the schema as written, else without regard to case and with `_` for `-`; one the schema doesn't have
    is kept as written, for the load to report with everything else.

        >>> overrides = parse_overrides(sys.argv[1:], App)
        >>> app = App.load("app.toml", overrides = overrides)

    A `ValueError` for an argument that is no `KEY=VALUE`, and for two that can't both hold (`db=x` and
    `db.port=1`): it names the argument's position or its key, never its value. The same key twice: the later one.
    """
    if not (isinstance(schema, type) and is_config(schema)):
        raise TypeError(f"parse_overrides: {schema!r} is no configuration (a class below Config)")
    if isinstance(arguments, str) or not isinstance(arguments, Sequence):
        raise TypeError(f"parse_overrides takes a list of KEY=VALUE texts, got {type(arguments).__name__}")
    found: dict[str, Any] = {}
    leaves: set[tuple[str, ...]] = set()
    for position, argument in enumerate(arguments, 1):
        if not isinstance(argument, str):
            raise TypeError(f"parse_overrides: argument {position} is no text")
        key, equals, text = argument.partition("=")
        parts = [part.strip() for part in key.split(".")]
        if not equals or not all(parts):
            raise ValueError(f"override {position} is not KEY=VALUE (db.port=9090)")
        target = field_of(schema, parts)
        path, value = (target[0], text_value(".".join(target[0]), text, target[1])) if target else (parts, text)
        at = found
        for depth, part in enumerate(path[:-1]):
            if tuple(path[:depth + 1]) in leaves:
                raise ValueError(f"overrides give both {'.'.join(path[:depth + 1])} and {'.'.join(path)}: one is inside the other")
            at = at.setdefault(part, {})
        if tuple(path) not in leaves and isinstance(at.get(path[-1]), dict):
            raise ValueError(f"overrides give both {'.'.join(path)} and a key inside it")
        at[path[-1]] = value
        leaves.add(tuple(path))
    return found


def _copied(data: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _copied(value) if isinstance(value, Mapping) else value for key, value in data.items()}


def _section_path(section: str | Sequence[str] | None) -> tuple[str, ...]:
    """The keys of `section`: `"tool.myapp"` or `["tool", "myapp"]`; `()` for none."""
    if section is None:
        return ()
    parts = tuple(section.split(".")) if isinstance(section, str) else tuple(section) if isinstance(section, (list, tuple)) else None
    if not parts or not all(isinstance(part, str) and part for part in parts):
        raise TypeError(f"section must be keys with dots between them (\"tool.myapp\") or a list of keys, got {section!r}")
    return parts


def _path(file: File) -> str | os.PathLike[str]:
    return file.path if isinstance(file, OptionalFile) else file
