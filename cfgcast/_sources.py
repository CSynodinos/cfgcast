"""
Several sources for one configuration: files in layers (the later ones win), a `.env` file and
environment variables over them. `Sources` notes, per key path, which file or variable gave the
value (and on which line, where that is known), so that a problem names where its value came from.

Layers merge key by key, section by section. A later value **replaces** the earlier one, a list
included: an append could not be undone by a later layer.

Environment variables: the prefix taken off, `__` between levels, names matched to the schema's
fields without regard to case (`APP_DB__PORT` is `db.port`). A variable that names no field is
ignored, also under `strict`: the environment holds what other tools set too. Values are text, and
coercion does the rest; a list field takes `a, b, c` (ini's rule) or a JSON array, a dictionary field
a JSON object.

A `.env` file holds such variables as `KEY=VALUE` lines (`read_env_file`): the defaults of a machine,
under what the process environment says.
"""
from collections.abc import Mapping
from typing import Any, get_type_hints
from ._hints import is_dict, is_list, keys_of, section_of, split_list
from ._readers import RawConfigData
import json
import os
import re
import weakref

type Path = tuple[Any, ...]
#* {"type", "loc", "msg", "input", "ctx"}: `_errors.Problem` (this module is below it).
type Problem = dict[str, Any]

DEFAULT = "(default)"                                               # the source of a value no source gave
OVERRIDES = "overrides"                                             # the source of what `load(overrides = ...)` gave
NO_FILE = "(no file)"                                               # the source of a load that read nothing

#* Where each instance a build made came from, (its Sources, its key path), by the instance's id. Not on the
#* instance: `vars(app)` doesn't show it, and a copy or an unpickled one is not what was loaded. A weak reference
#* takes the entry out when the instance goes, before its id can be another's.
_LOADED: dict[int, tuple[weakref.ref[Any], "Sources", Path]] = {}


def note_loaded(instance: Any, sources: "Sources", path: Path) -> None:
    """Keep where `instance` was built from, for `sources_of`, as long as it lives."""
    key = id(instance)

    def gone(_: weakref.ref[Any]) -> None:
        _LOADED.pop(key, None)

    _LOADED[key] = (weakref.ref(instance, gone), sources, path)


def loaded_from(instance: Any) -> tuple["Sources", Path] | None:
    """`(Sources, key path)` of an instance a build made, None for any other."""
    entry = _LOADED.get(id(instance))
    if entry is None or entry[0]() is not instance:
        return None
    return entry[1], entry[2]


class OptionalFile:
    """A file that is read if it is there: what `optional("local.toml")` returns. Not a path (`os.fspath` refuses it)."""
    __slots__ = ("path",)

    def __init__(self, path: str | os.PathLike[str]) -> None:
        if not isinstance(path, (str, os.PathLike)):
            raise TypeError(f"optional takes a path, got {path!r}")
        self.path = os.fspath(path)

    def __repr__(self) -> str:
        return f"optional({self.path!r})"


def optional(path: str | os.PathLike[str]) -> OptionalFile:
    """
    Mark a file as one that may be absent: `App.load(["base.toml", optional("local.toml")])` reads
    `local.toml` if it is there, and goes on without it if not. Every file not marked must be there.

    Only "not there" is skipped. A file that is there is read like any other: one that can't be read,
    or holds a wrong value, is an error.
    """
    return OptionalFile(path)


def is_absent(path: str | os.PathLike[str]) -> bool:
    """Whether nothing is at `path`. A broken symbolic link is something: a file that was meant to be there."""
    return not os.path.lexists(path)


class Sources:
    """
    Where each value came from. `names` are the sources of the load in order (for the error's head),
    `of(path)` the one that gave the value at a key path: the nearest noted above it, else the last file.
    `lines` has, per source, the line of each key path that is known (a `.env` file's variables).

    `prefix` is the section of the files the schema was read from (`App.load(..., section = "tool.app")`),
    `files` the sources that are such files: a problem of theirs has the whole path in the file as its
    `loc`, the section first. A variable names the schema's own path.
    """

    def __init__(self, names: tuple[str, ...], prefix: Path = (), files: frozenset[str] = frozenset()) -> None:
        self.names = names or (NO_FILE,)
        self.last = self.names[-1]                                  # what a missing key names
        self.lines: dict[str, dict[Path, int]] = {}
        self.prefix = prefix
        self.files = files
        self.defaults: set[Path] = set()                            # the key paths no source gave: the class's default was taken
        self._noted: dict[Path, str] = {}

    def within(self, path: Path, name: str) -> Path:
        """`path` as it is in the source `name`: after the section, in a file that was read from one."""
        return (*self.prefix, *path) if name in self.files else path

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

    def line(self, path: Path, name: str) -> int | None:
        """The line of `path` in the source `name`: its own, or that of the nearest key above it. None where nothing is known."""
        lines = self.lines.get(name)
        if lines:
            path = self.within(path, name)
            for end in range(len(path), 0, -1):
                line = lines.get(path[:end])
                if line is not None:
                    return line
        return None

    def where(self, path: Path) -> str:
        """Where the value at `path` came from, for a reader: `prod.yaml:3`, `APP_DB__PORT`, `(default)`."""
        if any(path[:end] in self.defaults for end in range(1, len(path) + 1)):
            return DEFAULT
        name = self.of(path)
        line = self.line(path, name)
        return name if line is None else f"{name}:{line}"

    def problem(self, kind: str, path: Path, msg: str, ctx: dict[str, Any], name: str | None = None) -> Problem:
        """A problem at `path`: its `loc` the source (`name`, or the one that gave the value) then the path, its line in `ctx` if known."""
        name = self.of(path) if name is None else name
        line = self.line(path, name)
        return {"type": kind, "loc": [name, *self.within(path, name)], "msg": msg, "ctx": {**ctx, "line": line} if line is not None else ctx}

    def placed(self, entry: Problem, path: Path) -> Problem:
        """An entry of enforced's, its `loc` put after the source of `path` (and the line added, if known)."""
        name = self.of(path)
        line = self.line(path, name)
        entry["loc"] = [name, *self.within(path, name)]
        if line is not None:
            entry["ctx"] = {**entry.get("ctx", {}), "line": line}
        return entry


def layer(data: RawConfigData, over: RawConfigData, name: str, sources: Sources, path: Path = ()) -> None:
    """`over` laid on `data`, in place: a section merged key by key, anything else replaced (and noted as `name`'s)."""
    for key, value in over.items():
        here = (*path, key)
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            layer(data[key], value, name, sources, here)
        else:
            sources.note(here, name)
            data[key] = value


def read_env(schema: type, prefix: str, env: Mapping[str, str]) -> list[tuple[str, Path, RawConfigData]]:
    """
    The variables of `env` that name a field of `schema`, each as `(variable, key path, {key: {key: value}})`:
    one layer per variable, in the order of their names. A `ValueError` (that never shows the value) for a
    list or dictionary field whose text starts as JSON and isn't.

    A variable that names no field, and does without its `_FILE` ending, gives the path of a file whose text
    is the value (`APP_DB__PASSWORD_FILE=/run/secrets/db_password`: how Docker and Kubernetes hand a secret
    over). The text is UTF-8, and one line break at its end is taken off. The field given both ways is a
    `ValueError` that names the two variables; a file that can't be read is the `OSError` of it, the variable named.
    """
    found: list[tuple[str, Path, RawConfigData]] = []
    given: dict[Path, str] = {}                                     # the key path -> the variable that gave it
    for variable in sorted(env):
        if not variable.lower().startswith(prefix.lower()):
            continue
        name, text = variable[len(prefix):], env[variable]
        target = field_of(schema, name.split("__"))
        if target is None and name.lower().endswith(_FILE) and text:    # an empty one is as good as unset
            target = field_of(schema, name[:-len(_FILE)].split("__"))
            if target is not None and section_of(target[1]) is None:
                text = _file_text(variable, text)
            else:
                target = None
        if target is None:
            continue
        path, hint = target
        if tuple(path) in given:
            raise ValueError(f"{given[tuple(path)]} and {variable} both give {'.'.join(path)}: set one of them")
        given[tuple(path)] = variable
        layer_: Any = text_value(variable, text, hint)
        for key in reversed(path):
            layer_ = {key: layer_}
        found.append((variable, tuple(path), layer_))
    return found


_FILE = "_file"


def _file_text(variable: str, path: str) -> str:
    """The text of the file a `_FILE` variable names. An error names the variable and the path, never what was read."""
    try:
        with open(path, encoding = "utf-8") as stream:
            text = stream.read()
    except UnicodeDecodeError:
        raise ValueError(f"{variable}: the file it names is not UTF-8 text: {path}") from None
    except OSError as error:
        raise type(error)(error.errno, f"{variable}: {error.strerror or 'the file it names can not be read'}", path) from None
    return text[:-1] if text.endswith("\n") else text


# ── a .env file ─────────────────────────────────────────────────────────────

_ENV_LINE = re.compile(r"\s*(?:export\s+)?([^\s=#'\"]+)\s*=[ \t]*(.*)", re.DOTALL)
_ENV_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def read_env_file(file: str | os.PathLike[str]) -> dict[str, tuple[str, int]]:
    """
    The variables of a `.env` file, `{variable: (text, line)}`. One `KEY=VALUE` per line (an `export ` before
    it is allowed), blank lines and `#` lines ignored. A value is bare (stripped; ` #` starts a comment),
    `'single quoted'` (as written) or `"double quoted"` (`\\n`, `\\t`, `\\r`, `\\"` and `\\\\` are escapes); a
    quoted one may run over several lines. No interpolation: `${HOME}` is that text. A key given twice: the
    later line. A line that is none of this is a `ValueError` with the file and the line, never its text.
    """
    name = os.fspath(file)
    with open(file, encoding = "utf-8-sig") as stream:              # a BOM is allowed
        lines = stream.read().split("\n")
    found: dict[str, tuple[str, int]] = {}
    index = 0
    while index < len(lines):
        number, line = index + 1, lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _ENV_LINE.fullmatch(line.rstrip("\r"))
        if match is None:
            raise ValueError(f"{name}:{number}: not a KEY=VALUE line")
        key, rest = match.groups()
        if rest[:1] in ("'", '"'):
            quote, text, at = rest[0], rest[1:], 0
            value: list[str] = []
            while True:                                             # to the closing quote, over as many lines as it takes
                if at >= len(text):
                    if index >= len(lines):
                        raise ValueError(f"{name}:{number}: the quote of {key} is never closed")
                    value.append("\n")
                    text, at = lines[index].rstrip("\r"), 0
                    index += 1
                    continue
                char = text[at]
                at += 1
                if char == quote:
                    break
                if char == "\\" and quote == '"' and at < len(text) and text[at] in _ENV_ESCAPES:
                    char = _ENV_ESCAPES[text[at]]
                    at += 1
                value.append(char)
            after = text[at:].strip()
            if after and not after.startswith("#"):
                raise ValueError(f"{name}:{number}: text after the closing quote of {key}")
            found[key] = ("".join(value), number)
        else:
            comment = re.search(r"(?:^|\s)#", rest)
            found[key] = ((rest[:comment.start()] if comment else rest).strip(), number)
    return found


def field_of(schema: type, parts: list[str]) -> tuple[list[str], Any] | None:
    """
    The key path and the annotation of the field a variable names (`["DB", "PORT"]`), None if there is none.
    A key is matched as written, else without regard to case and with its `-` as `_` (`max-size` is `MAX_SIZE`).
    """
    path: list[str] = []
    for depth, part in enumerate(parts):
        keys = keys_of(schema)
        key = part if part in keys else next((key for key in keys if key.replace("-", "_").lower() == part.lower()), None)
        if key is None:
            return None
        path.append(key)
        hint = _hints(schema).get(keys[key].name)
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


def text_value(variable: str, text: str, hint: Any) -> Any:
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
