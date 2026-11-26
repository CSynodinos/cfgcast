"""
`Config`: the base class of a configuration schema. A class below it is an enforced dataclass with
`coerce` and `deep` always on (config files are text, and nested), and so is each class below that.

    class Database(Config):
        host: str
        port: Port = 5432

    class App(Config, strict = False):
        name: str
        db: Database                 # a section: another configuration

    class Prod(App):                 # a configuration too: App's fields and options, and its own
        replicas: int = 3

How it works:

1. `@dataclass_transform` on `Config`: mypy, pyright and editors read every class below it as a
    dataclass (`__init__` from the fields, `kw_only`, `frozen`, the class keywords). Nothing happens at run time.
2. `Config.__init_subclass__` takes the options as class keywords, checks the class, then calls
    `enforced.dataclass(cls, kw_only = True, frozen = ..., coerce = True, deep = True)`. Without
    slots the standard `dataclasses` changes a class in place and returns it: the class the
    statement binds is the class that was written, so `isinstance` and pickling just work.
3. The options are stored on the class (`__cfg_options__`: `strict`, `frozen`, `env_prefix`, `keys`), and the
    fields that are sections on `__cfg_sections__`. A class with `__cfg_options__` of its own is a configuration.
    `strict`, `env_prefix` and `keys` are inherited unless a class says its own; `frozen` is said by each class
    (checkers read it from the class statement, and dataclasses refuse a frozen class below one that isn't).
4. `load` and `from_dict` are the members every configuration has, inherited from `Config`: a checker
    sees them, and knows they give the class they are called on. `to_dict`, `to_text`, `write` and the
    others are functions of the module: they take the configuration as their first argument.

A field whose type is another configuration is a nested section / table: the builder (`_build`)
makes it from the sub-dictionary. enforced checks the leaves; it does not turn a dict into a class.
"""
from ._errors import ConfigError, FrozenConfigError, _where
from ._sources import OptionalFile, loaded_from
from ._readers import CONFIG_READERS, CfgType, RawConfigData
from ._writers import TEXTS, WRITERS, plain_json
from ._template import save_template
from ._hints import (
    KEY_STYLES,
    METADATA,
    OPTIONS_ATTR,
    Options,
    key_of,
    section_of,
    )
from typing import (
    Any,
    Callable,
    ClassVar,
    Literal,
    Self,
    TypedDict,
    Unpack,
    overload,
    dataclass_transform,
    get_type_hints,
    )
from dataclasses import (
    MISSING,
    Field,
    FrozenInstanceError,
    field as dataclass_field,
    fields,
    is_dataclass
    )
from collections.abc import Mapping, Sequence
from clitint import formatter
import enforced
import inspect
import os
import sys

if sys.version_info >= (3, 14):
    import annotationlib


SECTIONS_ATTR = "__cfg_sections__"

#* .cfg is configparser's too: one format, two suffixes.
SUFFIXES: dict[str, CfgType] = {
    ".json": "json",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".yaml": "yaml",
    ".yml": "yaml",
    }

type File = str | os.PathLike[str] | OptionalFile


@overload
def field[T](
    *,
    default: T,
    key: str | None = None,
    doc: str | None = None,
    init: bool = True,
    repr: bool = True,
    hash: bool | None = None,
    compare: bool = True,
    metadata: Mapping[Any, Any] | None = None) -> T: ...
@overload
def field[T](
    *,
    default_factory: Callable[[], T],
    key: str | None = None,
    doc: str | None = None,
    init: bool = True,
    repr: bool = True,
    hash: bool | None = None,
    compare: bool = True,
    metadata: Mapping[Any, Any] | None = None) -> T: ...
@overload
def field(
    *,
    key: str | None = None,
    doc: str | None = None,
    init: bool = True,
    repr: bool = True,
    hash: bool | None = None,
    compare: bool = True,
    metadata: Mapping[Any, Any] | None = None) -> Any: ...
def field(
    *,
    default: Any = MISSING,
    default_factory: Any = MISSING,
    key: str | None = None,
    doc: str | None = None,
    init: bool = True,
    repr: bool = True,
    hash: bool | None = None,
    compare: bool = True,
    metadata: Mapping[Any, Any] | None = None) -> Any:
    """
    `dataclasses.field`, with what a configuration says about a field beyond its type. `dataclasses.field`
    keeps working in a configuration: this one is needed only for `key` and `doc`.

    Args:
        key: the key of this field in a file, where it isn't the field's name: `max_size: int = field(key = "max-size")`,
            `class_: str = field(key = "class")`. Files, `to_dict`, `from_dict` and messages use the key;
            the attribute keeps its Python name (`pool.max_size`).
        doc: one line about the field, written as a comment above its key by `write_template`.
        default, default_factory, init, repr, hash, compare, metadata: as `dataclasses.field` has them.

    Example:
        >>> class Pool(Config):
        ...     max_size: int = field(default = 10, key = "max-size", doc = "connections at most")
    """
    for name, text in (("key", key), ("doc", doc)):
        if text is not None and not (isinstance(text, str) and text.strip()):
            raise TypeError(f"field: {name} must be text that isn't empty, got {text!r}")
    return dataclass_field(
        default = default,
        default_factory = default_factory,
        init = init,
        repr = repr,
        hash = hash,
        compare = compare,
        metadata = {
            **(metadata or {}),
            METADATA: {"key": key, "doc": doc}
            },
        )


class _Options(TypedDict, total = False):
    """The class keywords of a configuration: `class App(Config, strict = False)`."""
    strict: bool
    frozen: bool
    env_prefix: str | None
    keys: Literal["kebab-case"] | None


@dataclass_transform(
    kw_only_default = True,
    frozen_default = True,
    field_specifiers = (dataclass_field, Field, field),
    )
class Config:
    """
    The base of a configuration schema. Each annotated attribute of a class below it is a key; its type
    hint says what the value must be, and a default makes the key optional. A field typed as another
    configuration is a section.

    ```python
    class Database(Config):
        host: str
        port: Port = 5432

    class App(Config, strict = False):
        name: str
        db: Database

    class Lenient(Config, frozen = False):
        name: str
        db: Database

    app = App.load("app.toml")
    lenient = Lenient.load("lenient.toml")
    app.name = "new_name"  # FrozenConfigError
    lenient.name = "new_name"  # Works because frozen = False
    ```

    Options, as class keywords:
        * `strict` (bool): refuse keys the schema doesn't name (default `True`). `False` ignores them, in this
            class and every section below it, whatever those say: an older program reading a newer
            file. Loading then gives a `ConfigWarning` that names them.
        * `frozen` (bool): instances can't be changed after loading (default `True`). Said by each class:
            a class below a frozen one is frozen too, and one below a class that isn't says `frozen = False`.
        * `env_prefix` (str | None): read environment variables with this prefix over the files whenever this
            class is loaded, as `load(..., env_prefix = ...)` does; that argument wins over this one. Only
            the class that is loaded counts, not a section's. `from_dict` reads none. Defaults to `None`.
        * `keys` ("kebab-case" | None): how a field's name is written in a file. `"kebab-case"`: `max_size` is
            the key `max-size` (and `class_` is `class`), in this class only: a section says its own. A
            field's own `field(key = ...)` wins. Attributes keep their Python names. Defaults to `None`.

    ```python
    # Non strict, non frozen configuration class that reads environment variables with the prefix "APP_" and uses kebab-case keys.
    class App(Config, strict = False, frozen = False, env_prefix = "APP_", keys = "kebab-case"):
        ...
    ```

    `strict`, `env_prefix` and `keys` are inherited: a class below `App` has them unless it says its own.
    `coerce` and `deep` are always on and are not options: `"8080"` is read as `8080` for an `int`, and
    `list[Port]` has every item checked.

    A `TypeError` at the class statement for an option that isn't one or isn't of its type, a field
    named `load` or `from_dict` (the members every configuration has), and two fields with one key.
    `Config` itself is no configuration: it has no fields, and can't be loaded.

    """
    __cfg_options__: ClassVar[Options]      # strict, frozen, env_prefix, keys
    #* field name -> the nested configuration class, or list[it] / tuple[it, ...] / dict[str, it]
    __cfg_sections__: ClassVar[dict[str, Any]]

    def __init_subclass__(cls, **options: Unpack[_Options]) -> None:
        super().__init_subclass__()
        _make_config(cls, dict(options))
        return

    @classmethod
    def load(
        cls,
        file: File | Sequence[File],
        cfg_type: CfgType | Literal["infer"] = "infer",
        *,
        env_prefix: str | None = None,
        env: Mapping[str, str] | None = None,
        env_file: File | None = None,
        section: str | Sequence[str] | None = None,
        overrides: Mapping[str, Any] | None = None
        ) -> Self:
        """
        Read `file` and return an instance of this class, or raise a `ConfigError` with every problem
        found (unknown keys, missing keys, wrong values), each with its file and key path.

        Args:
            file: one file, or a list of them read in layers: `App.load(["base.toml", "prod.toml"])`.
                Sections merge key by key and a later file's value replaces an earlier one's (a list is
                replaced, never appended to). Unknown and missing keys are checked once, on the result,
                and each problem names the file its value came from. Every file must be there, unless it
                is marked: `optional("local.toml")` is read if it is there.
            cfg_type: `"infer"` takes the format from each file's suffix (`SUFFIXES`); a format named
                here holds for every file.
            env_prefix: read environment variables over the files: with `"APP_"`, `APP_DB__PORT=9090` is
                `db.port` (`__` between levels, names matched without regard to case). A variable that names
                no field is ignored, also under `strict`. A list field takes `a, b, c` or a JSON array, a
                dictionary field a JSON object. A problem names the variable. With it, `file` may be `[]`.
                Not given, the class's `env_prefix` is used, if it has one.
            env: the variables, `os.environ` if not given (a test or a workflow hands its own; `{}` reads none).
            env_file: a `.env` file of `KEY=VALUE` lines, read with the prefix like the environment, over
                the files and under the environment (a variable of the process wins). `optional(".env")`
                for one that may be absent. A problem names the file and the line.
            section: the part of each file that holds the configuration, where a file holds more:
                `App.load("pyproject.toml", section = "tool.myapp")` reads the table `[tool.myapp]`. Dots
                between levels, or a list of keys (`["tool", "my.app"]`) where a key has a dot. It holds for
                every file of a list (not for variables, which name the schema's own keys). A file without the
                section is a problem, as a missing key is. A problem's `loc` is the whole path in the file.
            overrides: values over every file and variable, for this load: what a command line says
                (`App.load("app.toml", overrides = {"db": {"port": 9090}})`). Nested keys as nested
                dictionaries; `parse_overrides` makes them from `db.port=9090` texts. Checked like
                everything else: a problem of theirs names `overrides`.

        Unknown keys that `strict = False` ignores give one `ConfigWarning` that names them: `warnings`
        filters it as any other (`warnings.filterwarnings("ignore", category = ConfigWarning)`).
        """
        from ._load import load          # _load imports _base: here, not at the top
        if not is_config(cls):
            raise TypeError(f"{cls.__qualname__}.load: {cls.__qualname__} is no configuration (load a class below Config)")
        return load(
            cls, file, cfg_type,
            env_prefix = env_prefix,
            env = env,
            env_file = env_file,
            section = section,
            overrides = overrides
            )

    @classmethod
    def from_dict(cls, data: RawConfigData) -> Self:
        """
        Build from a dictionary already read (what every reader returns): sections made, keys checked,
        every problem in one `ConfigError`, as `load` does for a file.
        """
        from ._build import build          # _build imports _base: here, not at the top
        if not is_config(cls):
            raise TypeError(f"{cls.__qualname__}.from_dict: {cls.__qualname__} is no configuration (build a class below Config)")
        return build(cls, data, "<dict>")


def to_dict(obj: object, *, plain: bool = False, reveal_secrets: bool = False) -> dict[str, Any]:
    """
    The values as a dictionary, sections as nested dictionaries. Values are as they are on the
    object (a `SecretStr` stays one, masked when printed); lists and dictionaries are copies, so
    changing the result never changes the configuration.

    Args:
        plain: only what JSON holds, for `json.dumps`, a log or a response: text, numbers, booleans,
            `None`, lists, and dictionaries with text keys. A path, a `Decimal`, a `UUID`, a date and a
            time become text, an enum its value, and **a `SecretStr` the mask** (`"**********"`).
        reveal_secrets: with `plain`, a `SecretStr` as its real text.
    """
    if isinstance(obj, type) or not is_config(obj):
        raise TypeError(f"to_dict takes a configuration (an instance of a class below Config), got {obj!r}")
    for name, option in (("plain", plain), ("reveal_secrets", reveal_secrets)):
        if type(option) is not bool:
            raise TypeError(f"to_dict: {name} must be True or False, got {option!r}")
    if reveal_secrets and not plain:
        raise TypeError("to_dict: reveal_secrets goes with plain = True (without it a SecretStr stays a SecretStr)")
    return plain_json(_as_dict(obj), mask = not reveal_secrets) if plain else _as_dict(obj)


def sources_of(obj: object) -> dict[str, str]:
    """
    Where each value of a loaded configuration came from, by its key path: the file and the line
    (`"prod.yaml:3"`), the environment variable (`"APP_DB__PORT"`), `"overrides"`, or `"(default)"` where no
    source gave it and the class's default was taken. For a start-up log, and for "why is this value what it is".

        >>> sources_of(App.load(["base.toml", "prod.yaml"], env_prefix = "APP_"))
        {'name': 'base.toml:1', 'db.host': 'prod.yaml:3', 'db.port': 'APP_DB__PORT', 'debug': '(default)'}

    Keys are written as in a message (`servers[0].host`), from the configuration asked: `sources_of(app.db)`
    has `host`, not `db.host`. Only names are in it, never a value. A line is there where it could be
    found, for a list item or a key without one the line of the key above it. It says how the
    configuration was loaded: a value assigned later (`frozen = False`) is still what it was.

    A `ValueError` for a configuration that wasn't loaded: one constructed directly (`App(name = "x")`),
    or a section that is its class's default.
    """

    #* Ensure the object is a loaded configuration instance.
    if isinstance(obj, type) or not is_config(obj):
        raise TypeError(f"sources_of takes a configuration (an instance of a class below Config), got {obj!r}")
    source_data = loaded_from(obj)
    if source_data is None:
        raise ValueError(
            f"sources_of: this {type(obj).__name__} was not loaded (it was constructed directly, or is a default): "
            f"ask the configuration that load or from_dict returned"
            )

    #* Walk the object recursively to collect the sources of each configuration value.
    sources, base = source_data
    found: dict[str, str] = {}
    def _walk_obj(obj: Any, path: tuple[Any, ...], shown: tuple[Any, ...]) -> None:
        nonlocal found
        pairs: list[tuple[Any, Any]] | None = None
        if _is_section(obj):
            pairs = [(key_of(type(obj), f), getattr(obj, f.name)) for f in fields(obj) if f.init]
        elif isinstance(obj, (list, tuple)) and obj and all(_is_section(item) for item in obj):
            pairs = list(enumerate(obj))
        elif isinstance(obj, dict) and obj and all(_is_section(item) for item in obj.values()):
            pairs = list(obj.items())
        if pairs is None:
            found[_where(shown, False)] = sources.where(path)
            return
        for key, item in pairs:
            _walk_obj(item, (*path, key), (*shown, key))
        return
    _walk_obj(obj, base, ())

    return found


def _is_section(value: Any) -> bool:
    return is_dataclass(value) and not isinstance(value, type)


def to_text(obj: object, cfg_type: CfgType = "json", *, reveal_secrets: bool = False) -> str:
    """
    The configuration as text in one of the formats: what `write` would write to a file, but for one
    difference: **a `SecretStr` is the mask** (`**********`) unless `reveal_secrets` is `True`. Text is
    printed, logged and sent; a file is where secrets go.
    """
    if isinstance(obj, type) or not is_config(obj):
        raise TypeError(f"to_text takes a configuration (an instance of a class below Config), got {obj!r}")
    if type(reveal_secrets) is not bool:
        raise TypeError(f"to_text: reveal_secrets must be True or False, got {reveal_secrets!r}")
    if cfg_type not in TEXTS:
        raise ValueError(f"Unsupported configuration type: {cfg_type!r} (one of {', '.join(TEXTS)})")
    return TEXTS[cfg_type](_as_dict(obj), mask = not reveal_secrets)


def write(obj: object, file: str | os.PathLike[str], cfg_type: CfgType | Literal["infer"] = "infer") -> None:
    """
    Write the configuration to `file` in any of the formats (each is a dictionary, so all four can be
    written). A `SecretStr` is written as its real text, not the mask. Comments and the layout of a file
    it was loaded from are not kept. To write other values, make the configuration that has them
    (`App.from_dict(...)`, `dataclasses.replace(app, ...)`) and write that.

    A `TypeError` / `ValueError` for a value the format can't hold, before the file is opened, so nothing
    is written. The file is written whole or not at all: to a temporary file next to it, which then takes
    its place.
    """
    if isinstance(obj, type) or not is_config(obj):
        raise TypeError(f"write takes a configuration (an instance of a class below Config), got {obj!r}")
    WRITERS[_format_of(file, cfg_type)](file, _as_dict(obj))
    return


def write_template(
    schema: type,
    file: str | os.PathLike[str],
    cfg_type: CfgType | Literal["infer"] = "infer",
    *,
    overwrite: bool = False
    ) -> None:
    """
    Write a template of `schema` to `file`: every key of the configuration, to be filled in. TOML, YAML
    or ini; JSON is refused (`ValueError`): it has no comments, and a template needs them.

    A key with a default is written with it. A key that is required, that is `None` by default or that
    is a secret is written commented out, with a value to replace: **the default of a `SecretStr` is
    never written**. Above each key, as comments: its `doc` (`cfgcast.field(doc = ...)`), `required`,
    `secret`, and its type where that says more than the value. A list or a dictionary of sections is
    one commented item to copy. With its required keys filled in, the template loads, and gives the
    defaults.

    A file that is there is not replaced (`FileExistsError`) unless `overwrite = True`: a template over
    a real configuration would lose it. Written whole or not at all, as `write` does.
    """
    if not isinstance(schema, type) or not is_config(schema):
        raise TypeError(f"write_template takes a configuration class (a class below Config), got {schema!r}")
    if type(overwrite) is not bool:
        raise TypeError(f"write_template: overwrite must be True or False, got {overwrite!r}")
    save_template(schema, file, _format_of(file, cfg_type), overwrite)
    return


def _as_dict(obj: Any) -> dict[str, Any]:
    #* The keys of a file: a field(init = False) is worked out by the class, and no key (build refuses it).
    #* By the key of the file (`cfgcast.field(key = ...)`, `keys = ...` of the class): what from_dict takes back.
    return {key_of(type(obj), f): _copy_node(getattr(obj, f.name)) for f in fields(obj) if f.init}


@enforced.dispatch
def _copy_node(node) -> Any:
    """Recursively copy a configuration node, converting sections to dictionaries."""
    if is_dataclass(node) and not isinstance(node, type):          # a section: a configuration, or any dataclass, as build takes them
        return _as_dict(node)
    else:
        return node

@_copy_node.register
def _(node: list[Any]) -> list[Any]:
    return [_copy_node(item) for item in node]

@_copy_node.register
def _(node: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(_copy_node(item) for item in node)

@_copy_node.register
def _(node: dict[Any, Any]) -> dict[Any, Any]:
    return {key: _copy_node(item) for key, item in node.items()}

@_copy_node.register
def _(node: set | frozenset) -> set | frozenset:
    return type(node)(_copy_node(item) for item in node)


def is_config(obj: object) -> bool:
    """
    Whether `obj` is a configuration schema (a class below `Config`), or an instance of one. The options must be
    the class's own (`vars`, not `getattr`): `Config` itself has none, and is not one.
    `isinstance(obj, type)`, not `type(obj) == type`: a class with a metaclass (an `ABC`, an `Enum`) is a class too.
    """
    cls = obj if isinstance(obj, type) else type(obj)
    return OPTIONS_ATTR in vars(cls)


def infer_type(file: str | os.PathLike[str]) -> CfgType:
    """The format of `file` by its suffix; a `ValueError` that says to pass `cfg_type` otherwise."""
    suffix = os.path.splitext(os.fspath(file))[1].lower()
    if suffix not in SUFFIXES:
        raise ValueError(
            f"Cannot infer configuration type from file extension: {os.path.basename(file)}. "
            f"Pass cfg_type (one of {', '.join(sorted(set(SUFFIXES.values())))}), "
            f"or use a suffix: {', '.join(SUFFIXES)}"
            )
    return SUFFIXES[suffix]


def _format_of(file: str | os.PathLike[str], cfg_type: str) -> CfgType:
    """The format to read or write `file` in: `cfg_type`, or its suffix's for `"infer"`."""
    match cfg_type:
        case "infer":
            return infer_type(file)
        case c if c in CONFIG_READERS:
            return c                                                # type: ignore[return-value]  # one of CfgType
        case _:
            raise ValueError(f"Unsupported configuration type: {cfg_type!r} (one of {', '.join(CONFIG_READERS)}, or 'infer')")


# ── how a class becomes a configuration ─────────────────────────────────────

#* What every configuration has: no field may take these names.
_CONFIG_MEMBERS = ("load", "from_dict")


def _make_config(cls: type, given: dict[str, Any]) -> None:
    """What `Config.__init_subclass__` does: the options, the checks, the enforced dataclass, in place."""

    #* Options: the class's own, else the ones above it (frozen excepted: each class says its own).
    unknown = sorted(set(given) - set(_Options.__annotations__))
    if unknown:
        raise TypeError(
            f"{cls.__qualname__}: {', '.join(unknown)} is no option of a configuration "
            f"(the options are {', '.join(_Options.__annotations__)})"
            )
    above = getattr(cls, OPTIONS_ATTR, Options())
    options = Options(
        strict = given.get("strict", above.strict),
        frozen = given.get("frozen", True),
        env_prefix = given.get("env_prefix", above.env_prefix),
        keys = given.get("keys", above.keys),
        )
    for name in ("strict", "frozen"):
        if type(getattr(options, name)) is not bool:
            raise TypeError(f"{cls.__qualname__}: {name} must be True or False, got {getattr(options, name)!r}")
    if options.env_prefix is not None and not isinstance(options.env_prefix, str):
        raise TypeError(f"{cls.__qualname__}: env_prefix must be text (or None for no environment variables), got {options.env_prefix!r}")
    if options.keys is not None and options.keys not in KEY_STYLES:
        raise TypeError(f"{cls.__qualname__}: keys must be None or one of {', '.join(map(repr, KEY_STYLES))}, got {options.keys!r}")
    for name in _CONFIG_MEMBERS:
        if name in _annotated_names(cls):
            raise TypeError(f"{cls.__qualname__}.{name}: the name is taken by what every configuration has ({', '.join(_CONFIG_MEMBERS)}); choose another")
    for base in cls.__mro__[1:]:
        if is_config(base) and vars(base)[OPTIONS_ATTR].frozen != options.frozen:
            raise TypeError(
                f"{cls.__qualname__}: frozen must be as {base.__qualname__}'s, {vars(base)[OPTIONS_ATTR].frozen} "
                f"(class {cls.__name__}({base.__name__}, frozen = {vars(base)[OPTIONS_ATTR].frozen}))"
                )
    setattr(cls, OPTIONS_ATTR, options)

    #* Enforced Dataclass
    #* ------------------
    #* The class is turned into an enforced dataclass with the given options, in place: type checking,
    #* coercion, deep validation. Fields of a configuration above it are fields of this one too.
    enforced.dataclass(
        cls,
        kw_only = True,
        frozen = options.frozen,
        coerce = True,
        deep = True,
        error = ConfigError
        )
    setattr(cls, SECTIONS_ATTR, _Sections())
    if options.frozen:
        _say_frozen(cls)

    #* Two fields with one key, inherited ones too: a file could give only one of them.
    taken: dict[str, str] = {}
    for field_ in fields(cls):                                      # type: ignore[arg-type]  # a dataclass now
        if not field_.init:
            continue
        key = key_of(cls, field_)
        if key in taken:
            raise TypeError(f"{cls.__qualname__}: the fields {taken[key]} and {field_.name} have the same key {key!r}")
        taken[key] = field_.name

    return


def _say_frozen(cls: type) -> None:
    """
    The `__setattr__` / `__delattr__` that dataclasses made for a frozen class, raising a `FrozenConfigError` in
    place of its `FrozenInstanceError`. `object.__setattr__` (`build`, a `__post_init__`), `dataclasses.replace`,
    `copy` and pickle go around them, as they did.
    """
    made_setattr, made_delattr = vars(cls)["__setattr__"], vars(cls)["__delattr__"]     # the functions dataclasses made

    def __setattr__(self: Any, name: str, value: Any) -> None:
        try:
            made_setattr(self, name, value)
        except FrozenInstanceError:
            #* from None: the FrozenInstanceError says less, and nothing of it is wanted in the traceback.
            raise FrozenConfigError(_frozen_message(self, name, "assigned")) from None

    def __delattr__(self: Any, name: str) -> None:
        try:
            made_delattr(self, name)
        except FrozenInstanceError:
            raise FrozenConfigError(_frozen_message(self, name, "deleted")) from None

    for method in (__setattr__, __delattr__):
        method.__qualname__ = f"{cls.__qualname__}.{method.__name__}"
    cls.__setattr__ = __setattr__                                   # type: ignore[assignment, method-assign]
    cls.__delattr__ = __delattr__                                   # type: ignore[assignment, method-assign]
    return


def _frozen_message(obj: Any, name: str, done: str) -> str:
    """What a `FrozenConfigError` says: the class, the key path (from what was loaded, where it was), never the value."""
    cls = type(obj)
    by_name = {field_.name: field_ for field_ in fields(cls)}
    key = key_of(cls, by_name[name]) if name in by_name else name
    noted = loaded_from(obj)
    where = _where((*noted[1], key) if noted else (key,), False)
    return (
        f"{formatter(cls.__name__, "bold_yellow")} is {formatter('frozen', 'bold')}: {where} can't be {done} "
        f"(dataclasses.{formatter('replace', 'bold_blue')} makes a changed copy; frozen = {formatter('False', 'bold_orange')} on the class allows it)"
        )


def _annotated_names(cls: type) -> dict[str, Any]:
    """The names the class annotates itself, in the order written, each with its annotation: not evaluated where that can't be (it may name a class not made yet)."""
    try:
        if sys.version_info >= (3, 14):
            return dict(annotationlib.get_annotations(cls, format = annotationlib.Format.FORWARDREF))
        return dict(inspect.get_annotations(cls))           # 3.13 has evaluated them: a section named before it is written is quoted
    except Exception:
        return {}


class _Sections:
    """
    `__cfg_sections__` of a configuration, worked out when it is first read: the fields whose type
    is another configuration (`Database`, `Database | None`), the field's key -> that class, and the
    fields that hold several, marked by how: `list[Server]`, `tuple[Server, ...]`, `dict[str, Server]`.
    Not at decoration: an annotation may name a section written further down, which doesn't exist yet
    then (quoted on 3.13, which evaluates an annotation where it stands). The first read replaces
    this object on the class by the dictionary.
    """

    def __get__(self, _: object, owner: type) -> dict[str, Any]:
        sections: dict[str, Any] = {}
        hints = get_type_hints(owner, include_extras = True)
        for field_ in fields(owner):
            found = section_of(hints.get(field_.name))
            if found is not None:                                  # any dataclass, as build and the template take it
                kind, section = found
                sections[key_of(owner, field_)] = {
                    "one": section,
                    "list": list[section],                         # type: ignore[valid-type]
                    "tuple": tuple[section, ...],                # type: ignore[valid-type]
                    "dict": dict[str, section]                   # type: ignore[valid-type]
                    }[kind]
        setattr(owner, SECTIONS_ATTR, sections)
        return sections
