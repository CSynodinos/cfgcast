"""`load_cfg`: read a file (or several, in layers, and the environment over them) and build the schema from it."""
from ._readers import CONFIG_READERS, CfgType, RawConfigData
from ._base import SUFFIXES, _format_of, is_config
from ._build import build
from ._sources import Sources, layer, read_env
from collections.abc import Mapping, Sequence
from typing import Any, Literal
import os

__all__ = ["SUFFIXES", "load_cfg"]


def load_cfg[T](
    file: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
    schema: type[T],
    cfg_type: CfgType | Literal["infer"] = "infer",
    *,
    env_prefix: str | None = None,
    env: Mapping[str, str] | None = None,
    show_warnings: bool = True) -> T:
    """
    Read `file` and return an instance of `schema`, or raise a `ConfigError` with every
    problem found (unknown keys, missing keys, wrong values), each with its file and key path.

    Args:
        file: one file, or a list of them read in layers: `load_cfg(["base.toml", "prod.toml"], App)`.
            Sections merge key by key and a later file's value replaces an earlier one's (a list is
            replaced, never appended to). Unknown and missing keys are checked once, on the result,
            and each problem names the file its value came from.
        cfg_type: `"infer"` takes the format from each file's suffix (`SUFFIXES`); a format named
            here holds for every file.
        env_prefix: read environment variables over the files: with `"APP_"`, `APP_DB__PORT=9090` is
            `db.port` (`__` between levels, names matched without regard to case). A variable that names
            no field is ignored, also under `strict`. A list field takes `a, b, c` or a JSON array, a
            dictionary field a JSON object. A problem names the variable. With it, `file` may be `[]`.
        env: the variables, `os.environ` if not given (a test or a workflow hands its own).
        show_warnings: unknown keys that `strict = False` ignores give one `ConfigWarning` that
            names them, unless this is `False`.
    """
    if not (isinstance(schema, type) and is_config(schema)):
        raise TypeError(f"load_cfg: {schema!r} is not a @cfg class")
    return load(schema, file, cfg_type, env_prefix, env, show_warnings)


def load(
    schema: type,
    file: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
    cfg_type: str,
    env_prefix: str | None,
    env: Mapping[str, str] | None,
    show_warnings: bool) -> Any:
    """What `load_cfg` and the `load` member of a configuration do."""
    if env_prefix is not None and not isinstance(env_prefix, str):
        raise TypeError(f"env_prefix must be text (or None for no environment variables), got {env_prefix!r}")
    if env is not None and env_prefix is None:
        raise TypeError("env is read only with an env_prefix (env_prefix = \"\" takes every variable that names a field)")
    one = isinstance(file, (str, os.PathLike))
    if not one and not isinstance(file, (list, tuple)):
        raise TypeError(f"file must be a path or a list of paths, got {file!r}")
    files: list[str | os.PathLike[str]] = [file] if one else list(file)         # type: ignore[list-item, arg-type]
    if not files and env_prefix is None:
        raise ValueError("no file to load: the list of files is empty")
    #* Every format is known before any file is read: a wrong suffix in the last layer is said first.
    formats = [_format_of(layer_file, cfg_type) for layer_file in files]
    layers: list[tuple[str, RawConfigData]] = []
    for layer_file, selected_cfg_type in zip(files, formats):
        reader = CONFIG_READERS[selected_cfg_type]
        layers.append((os.fspath(layer_file), reader(layer_file, schema) if selected_cfg_type == "ini" else reader(layer_file)))
    variables = read_env(schema, env_prefix, os.environ if env is None else env) if env_prefix is not None else []

    if len(layers) == 1 and not variables:
        return build(schema, layers[0][1], layers[0][0], show_warnings)
    names = [name for name, _ in layers]
    if variables or not names:
        names.append(f"{env_prefix}*")                              # the environment, in the head of an error
    sources = Sources(tuple(names))
    if layers:
        sources.last = layers[-1][0]                                # a missing key names the last file
    data: RawConfigData = {}
    for name, over in (*layers, *variables):
        layer(data, over, name, sources)
    return build(schema, data, sources, show_warnings)
