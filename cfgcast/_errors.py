"""
`ConfigError`: every problem of a file at once, in enforced's `errors()` shape.

It is an `enforced.InvalidDataError` (so one `except InvalidDataError` catches a wrong argument
and a wrong configuration alike) and a `ValueError` (what a bad file has always been).

It is made two ways:

- by `_build`, for a file or a dictionary (`load`, `from_dict`): with the file,
  the schema and the entries, its `loc`s start with the file;
- by enforced itself, for a direct construction or assignment of a configuration (`App(name = 1)`): `Config`
  passes `error = ConfigError` to `enforced.dataclass`. enforced never runs this class's `__init__`, so
  `file`, `schema` and `entries` are `None` (class attributes), and `errors()` and the message come from
  enforced's problems, their `loc`s without a file.

Either way the input of a `SecretStr` field is never shown, in the message or in `errors()`, and an
unknown key's value not at all: nothing says it isn't a secret.

`ConfigWarning` says which unknown keys a lenient schema (`strict = False`) ignored, in the same shape.
"""
from dataclasses import FrozenInstanceError
from typing import Any, Callable, Sequence
from enforced import InvalidDataError, InvalidDataWarning
from clitint import formatter
from ._hints import mentions_secret
import inspect
import os

#* {"type", "loc", "msg", "input", "ctx"}; loc starts with the file, then the key path: ("app.toml", "db", "port").
type Problem = dict[str, Any]

MASK = "**********"                                                 # what SecretStr shows (str(), repr())


class ConfigError(InvalidDataError, ValueError):
    """
    Raised by `load` and `from_dict`, and by a configuration constructed or assigned to
    directly. The input of a `SecretStr` field is never shown, in the message or in
    `errors()`.
    """

    #* What a ConfigError made by enforced has (it never runs __init__): see the module's docstring.
    file: str | None = None
    files: tuple[str, ...] = ()
    schema: type | None = None
    entries: list[Problem] | None = None

    def __init__(self, file: str | os.PathLike[str] | Sequence[str], schema: type, problems: list[Problem]) -> None:
        #* Several sources (layered files, the environment): `files` has each, `file` all of them as one text,
        #* and every line of the message starts with the source its value came from.
        self.files = _sources(file)
        self.file = ", ".join(self.files)
        self.schema = schema
        self.entries = problems
        super().__init__(
            _message(problems, f"in {self.file} for {getattr(schema, '__name__', schema)}", True, self.files),
            context = "config"
            )
        return

    def errors(self, *, include_input: bool = True, include_context: bool = True) -> list[Problem]:
        """The problems, each a new dictionary (`loc` a list), without `input` / `ctx` when asked."""
        entries = self.entries if self.entries is not None else self._enforced_entries()
        return _copies(entries, include_input, include_context)

    # json() is InvalidDataError's: errors() as JSON, with the same options.

    @property
    def message(self) -> str:
        #* Made by enforced: its own message would show the values (a secret field's too). Said from
        #* the entries instead, whose "msg" never holds a value, as for a file.
        if self._message is None:
            if not self.problems:                               # a call that can't be bound: a sentence without a value
                return InvalidDataError.message.fget(self)      # type: ignore[attr-defined]  # the base's property
            entries = self._enforced_entries()
            self._message = _message(entries, f"in {_class_of(self)}", False)
        return self._message

    def _enforced_entries(self) -> list[Problem]:
        """enforced's entries of a direct construction or assignment, with the input of a secret field masked."""
        secret = {_root(problem.parameter) for problem in self.problems if mentions_secret(problem.annotation)}
        entries = InvalidDataError.errors(self)
        for entry in entries:
            if entry["loc"] and entry["loc"][0] in secret and "input" in entry:
                entry["input"] = MASK
        return entries

    def __reduce__(self) -> tuple[Any, ...]:
        if self.entries is None:
            return InvalidDataError.__reduce__(self)              # its message (masked) and problems, as this class
        return (type(self), (self.files, self.schema, self.entries))


class FrozenConfigError(FrozenInstanceError):
    """
    An assignment to, or a deletion of, an attribute of a frozen configuration (`frozen = True`, the default).
    A `dataclasses.FrozenInstanceError`, so an `AttributeError`: what catches those catches this. The message
    names the class and the key path (`db.port` for a section of a loaded configuration), never the value.
    """


class ConfigWarning(InvalidDataWarning):
    """
    The unknown keys a load ignored because of `strict = False`, one warning for all of them. It holds
    their paths and never their values. `warnings.filterwarnings("ignore", category = ConfigWarning)`
    leaves it out, and `warnings.filterwarnings("error", category = ConfigWarning)` or `pytest -W error::cfgcast.ConfigWarning` turns
    it into an error (not `python -W`, read before an installed package can be imported).
    """

    def __init__(self, file: str | os.PathLike[str] | Sequence[str], schema: type, ignored: list[Problem]) -> None:
        self.files = _sources(file)
        self.file = ", ".join(self.files)
        self.schema = schema
        self.entries = ignored
        super().__init__(self._text(lambda text, _: str(text)))

    def shown(self) -> str:
        """The message with colours, for a terminal. `str` stays plain: a filter's `message` regex reads that."""
        return self._text(formatter)

    def _text(self, style: Callable[[Any, str], str]) -> str:   # style is the internal formatter of enforced
        count = len(self.entries)
        located = _located(self.schema)
        head = (f"{style(count, 'bold_red')} unknown key{'s' if count != 1 else ''} ignored in {style(self.file, 'cyan')} "
                f"for {style(getattr(self.schema, '__name__', self.schema), 'bold_yellow')}"
                f"{f' ({style(located, 'cyan')})' if located else ''}:")
        return "\n".join([head, *(f"    -> {_from(p, self.files)}{style(_where(p['loc'], True), 'bold_red')}" for p in self.entries)])

    def errors(self, *, include_input: bool = True, include_context: bool = True) -> list[Problem]:
        """The ignored keys, in the shape of `ConfigError.errors()`. There is never an `input`."""
        return _copies(self.entries, include_input, include_context)

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.files, self.schema, self.entries))


def _located(schema: Any) -> str | None:
    """
    Where the class is written, as `file_path:line` (its first line, the decorator's: as `inspect` says), or None where that
    can't be found: a class made by `type()`, in a REPL, or from a file that is gone.
    """
    line = vars(schema).get("__firstlineno__") if isinstance(schema, type) else None    # 3.13+: set by the compiler
    if not isinstance(line, int):
        return None
    try:
        file = inspect.getsourcefile(schema)
    except (TypeError, OSError):
        return None
    return f"{file}:{line}" if file else None   # colon format allows editors like VSCode to jump to the line directly


def _copies(entries: list[Problem], include_input: bool, include_context: bool) -> list[Problem]:
    """Each entry a new dictionary (`loc` a list), without `input` / `ctx` when asked."""
    out = []
    for entry in entries:
        entry = {**entry, "loc": list(entry["loc"])}
        if not include_input:
            entry.pop("input", None)
        if not include_context:
            entry.pop("ctx", None)
        out.append(entry)
    return out


# ── the message ─────────────────────────────────────────────────────────────

def _root(parameter: Any) -> Any:
    """The field a problem is about: `keys` for `keys[1]`, `host` for `host`, each name of a missing tuple."""
    loc = getattr(parameter, "loc", None)
    if loc:
        return loc[0]
    return parameter if not isinstance(parameter, tuple) else (parameter[0] if parameter else None)


def _class_of(error: InvalidDataError) -> str:
    """The configuration class a problem of enforced's is about (`App` for `App.__init__`)."""
    for problem in error.problems:
        origin = problem.origin
        if isinstance(origin, type):
            return origin.__qualname__
        name = getattr(origin, "__qualname__", None)
        if name:
            return name.rsplit(".", 1)[0] if name.endswith("__init__") else name
    return "a configuration"


def _where(loc: list[Any] | tuple[Any, ...], has_file: bool) -> str:
    """The key path of a loc, after its file: `db.pool.max_size`, `server.open_ports[1]`."""
    text = ""
    for part in loc[1:] if has_file else loc:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else str(part))
    return text or ("(the whole file)" if has_file else "(the call)")


def _sources(file: str | os.PathLike[str] | Sequence[str]) -> tuple[str, ...]:
    """The sources of a load as texts: one file, or each layer."""
    if isinstance(file, (str, os.PathLike)):
        return (os.fspath(file),)
    return tuple(str(name) for name in file)


def _from(problem: Problem, files: tuple[str, ...]) -> str:
    """
    What stands in front of a line: `app.toml:12: ` where the line of the value is known (an editor opens
    it with a click), the source alone (`prod.toml: `, `APP_DB__PORT: `) where the head of the message
    doesn't say it already (several sources, or a variable), else nothing.
    """
    if not problem["loc"]:
        return ""
    line = (problem.get("ctx") or {}).get("line")
    if isinstance(line, int):
        return f"{problem['loc'][0]}:{line}: "
    return f"{problem['loc'][0]}: " if files != (problem["loc"][0],) else ""


def _message(problems: list[Problem], where: str, has_file: bool, files: tuple[str, ...] = ()) -> str:
    count = len(problems)
    head = f"{count} problem{'s' if count != 1 else ''} {where}:"
    return "\n".join([head, *(f"    {_from(p, files) if has_file else ''}{_where(p['loc'], has_file)}: {p['msg']}" for p in problems)])
