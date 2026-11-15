"""
`ConfigError`: every problem of a file at once, in enforced's `errors()` shape.

It is an `enforced.InvalidDataError` (so one `except InvalidDataError` catches a wrong argument
and a wrong configuration alike) and a `ValueError` (what a bad file has always been).

It is made two ways:

- by `_build`, for a file or a dictionary (`load_cfg`, `from_dict`, `write`'s overrides): with the file,
  the schema and the entries, its `loc`s start with the file;
- by enforced itself, for a direct construction or assignment of a `@cfg` class (`App(name = 1)`): `@cfg`
  passes `error = ConfigError` to `enforced.dataclass`. enforced never runs this class's `__init__`, so
  `file`, `schema` and `entries` are `None` (class attributes), and `errors()` and the message come from
  enforced's problems, their `loc`s without a file.

Either way the input of a `SecretStr` field is never shown, in the message or in `errors()`, and an
unknown key's value not at all: nothing says it isn't a secret.

`ConfigWarning` says which unknown keys a lenient schema (`strict = False`) ignored, in the same shape.
"""
from types import UnionType
from typing import Annotated, Any, Callable, TypeAliasType, Union, get_args, get_origin
from enforced import InvalidDataError, InvalidDataWarning
from enforced.data_types import SecretStr
from enforced._formatting_tool import formatter

#* {"type", "loc", "msg", "input", "ctx"}; loc starts with the file, then the key path: ("app.toml", "db", "port").
type Problem = dict[str, Any]

MASK = "**********"                                                 # what SecretStr shows (str(), repr())


class ConfigError(InvalidDataError, ValueError):
    """
    Raised by `load_cfg`, `from_dict`, `write` (overrides), and by a `@cfg` class constructed or
    assigned to directly. The input of a `SecretStr` field is never shown, in the message or in
    `errors()`.
    """

    #* What a ConfigError made by enforced has (it never runs __init__): see the module's docstring.
    file: str | None = None
    schema: type | None = None
    entries: list[Problem] | None = None

    def __init__(self, file: str, schema: type, problems: list[Problem]) -> None:
        self.file = str(file)
        self.schema = schema
        self.entries = problems
        super().__init__(
            _message(problems, f"in {self.file} for {getattr(schema, '__name__', schema)}", True),
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
        return (type(self), (self.file, self.schema, self.entries))


class ConfigWarning(InvalidDataWarning):
    """
    The unknown keys a load ignored because of `strict = False`, one warning for all of them. It holds
    their paths and never their values. `show_warnings = False` leaves it out, and
    `warnings.filterwarnings("error", category = ConfigWarning)` or `pytest -W error::cfgcast.ConfigWarning` turns
    it into an error (not `python -W`, read before an installed package can be imported).
    """

    def __init__(self, file: str, schema: type, ignored: list[Problem]) -> None:
        self.file = str(file)
        self.schema = schema
        self.entries = ignored
        super().__init__(self._text(lambda text, _: str(text)))

    def shown(self) -> str:
        """The message with colours, for a terminal. `str` stays plain: a filter's `message` regex reads that."""
        return self._text(formatter)

    def _text(self, style: Callable[[Any, str], str]) -> str:   # style is the internal formatter of enforced
        count = len(self.entries)
        head = (f"{style(count, 'bold_red')} unknown key{'s' if count != 1 else ''} ignored in {style(self.file, 'cyan')} "
                f"for {style(getattr(self.schema, '__name__', self.schema), 'bold_yellow')}:")
        return "\n".join([head, *(f"    -> {style(_where(p['loc'], True), 'bold_red')}" for p in self.entries)])

    def errors(self, *, include_input: bool = True, include_context: bool = True) -> list[Problem]:
        """The ignored keys, in the shape of `ConfigError.errors()`. There is never an `input`."""
        return _copies(self.entries, include_input, include_context)

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.file, self.schema, self.entries))


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


# ── what is a secret ────────────────────────────────────────────────────────

def members(hint: Any) -> list[Any]:
    """The annotations a hint stands for, `Annotated` and aliases taken off, each union member."""
    if isinstance(hint, TypeAliasType):
        return members(hint.__value__)
    if get_origin(hint) is Annotated:
        return members(get_args(hint)[0])
    if get_origin(hint) in (Union, UnionType):
        return [m for member in get_args(hint) for m in members(member)]
    return [hint]


def mentions_secret(hint: Any) -> bool:
    """Whether a `SecretStr` is anywhere in the annotation (`SecretStr`, `list[SecretStr] | None` ...)."""
    if hint is None:
        return False
    for member in members(hint):
        if isinstance(member, type) and issubclass(member, SecretStr):
            return True
        if any(mentions_secret(arg) for arg in get_args(member) if not isinstance(arg, (str, int, float, bool))):
            return True
    return False


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


def _message(problems: list[Problem], where: str, has_file: bool) -> str:
    count = len(problems)
    head = f"{count} problem{'s' if count != 1 else ''} {where}:"
    return "\n".join([head, *(f"    {_where(p['loc'], has_file)}: {p['msg']}" for p in problems)])
