"""
Several sources for one configuration: files in layers (the later ones win), and environment
variables over them. `Sources` notes, per key path, which file or variable gave the value, so that
a problem names where its value came from.

Layers merge key by key, section by section. A later value **replaces** the earlier one, a list
included: an append could not be undone by a later layer.

Environment variables: the prefix taken off, `__` between levels, names matched to the schema's
fields without regard to case (`APP_DB__PORT` is `db.port`). A variable that names no field is
ignored, also under `strict`: the environment holds what other tools set too. Values are text, and
coercion does the rest; a list field takes `a, b, c` (ini's rule) or a JSON array, a dictionary field
a JSON object.
"""
from collections.abc import Mapping
from dataclasses import fields
from typing import Any, get_type_hints
from ._hints import is_dict, is_list, section_of, split_list
from ._readers import RawConfigData
import json

type Path = tuple[Any, ...]


class Sources:
    """
    Where each value came from. `names` are the sources of the load in order (for the error's head),
    `of(path)` the one that gave the value at a key path: the nearest noted above it, else the last file.
    """

    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names
        self.last = names[-1]                                       # what a missing key names
        self._noted: dict[Path, str] = {}

    def note(self, path: Path, name: str) -> None:
        """`name` gave the whole value at `path`: what was noted below it is replaced with it."""
        for noted in [noted for noted in self._noted if noted[:len(path)] == path]:
            del self._noted[noted]
        self._noted[path] = name

    def of(self, path: Path) -> str:
        for end in range(len(path), 0, -1):
            name = self._noted.get(path[:end])
            if name is not None:
                return name
        return self.last


def layer(data: RawConfigData, over: RawConfigData, name: str, sources: Sources, path: Path = ()) -> None:
    """`over` laid on `data`, in place: a section merged key by key, anything else replaced (and noted as `name`'s)."""
    for key, value in over.items():
        here = (*path, key)
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            layer(data[key], value, name, sources, here)
        else:
            sources.note(here, name)
            data[key] = value


def read_env(schema: type, prefix: str, env: Mapping[str, str]) -> list[tuple[str, RawConfigData]]:
    """
    The variables of `env` that name a field of `schema`, each as `(variable, {key: {key: value}})`: one
    layer per variable, in the order of their names. A `ValueError` (that never shows the value) for a
    list or dictionary field whose text starts as JSON and isn't.
    """
    found: list[tuple[str, RawConfigData]] = []
    for variable in sorted(env):
        if not variable.lower().startswith(prefix.lower()):
            continue
        target = _field_of(schema, variable[len(prefix):].split("__"))
        if target is None:
            continue
        path, hint = target
        layer_: Any = _value(variable, env[variable], hint)
        for key in reversed(path):
            layer_ = {key: layer_}
        found.append((variable, layer_))
    return found


def _field_of(schema: type, parts: list[str]) -> tuple[list[str], Any] | None:
    """The key path and the annotation of the field a variable names (`["DB", "PORT"]`), None if there is none."""
    path: list[str] = []
    for depth, part in enumerate(parts):
        names = [f.name for f in fields(schema) if f.init]
        name = part if part in names else next((name for name in names if name.lower() == part.lower()), None)
        if name is None:
            return None
        path.append(name)
        hint = _hints(schema).get(name)
        if depth == len(parts) - 1:
            return path, hint
        below = section_of(hint)
        if below is None or below[0] != "one":                      # only a section has fields to go on with
            return None
        schema = below[1]
    return None


def _hints(schema: type) -> dict[str, Any]:
    try:
        return get_type_hints(schema, include_extras = True)
    except Exception:                                               # unresolvable: build reports it
        return {}


def _value(variable: str, text: str, hint: Any) -> Any:
    """The text of a variable as its field wants it: a list split or read as a JSON array, a JSON object for a dictionary."""
    start = text.lstrip()[:1]
    for opens, wanted, what in (("[", list, "array"), ("{", dict, "object")):
        if start == opens and (is_list(hint) if wanted is list else is_dict(hint)):
            try:
                value = json.loads(text)
            except json.JSONDecodeError as error:
                #* error.msg and the position only: the document itself (error.doc) may be a secret.
                raise ValueError(f"{variable}: starts with {opens!r} but is no JSON {what} ({error.msg}, at character {error.pos})") from None
            if not isinstance(value, wanted):
                raise ValueError(f"{variable}: starts with {opens!r} but is no JSON {what}")
            return value
    section = section_of(hint)
    if is_list(hint) and (section is None or section[0] == "one"):  # a list of sections can't be split from text
        return split_list(text)
    return text
