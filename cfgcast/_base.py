"""
`@cfg`: makes a class a configuration schema: an enforced dataclass with `coerce` and `deep`
always on (config files are text, and not deep). Not inheritable: a configuration is a plain
class, and so is each of its sections.

    @cfg
    class Database:
        host: str
        port: Port = 5432

    @cfg(strict = False)
    class App:
        name: str
        db: Database                 # a section: another @cfg class

How it works, with no base class for the user to inherit:

1. `@dataclass_transform` on `cfg`: mypy, pyright and editors read the decorated class as a
    dataclass (`__init__` from the fields, `kw_only`, `frozen`). Nothing happens at run time.
2. `cfg` refuses a class that has a configuration among its bases, then calls
    `enforced.dataclass(cls, kw_only = True, frozen = ..., coerce = True, deep = True)`. Without
    slots the standard `dataclasses` changes a class in place and returns it: the class the
    statement binds is the class that was written, so `isinstance` and pickling just work.
3. The options are stored on the class (`__cfg_options__`: `strict`, `frozen`, `env_prefix`), and the fields
    that are sections on `__cfg_sections__`. A class with `__cfg_options__` is a configuration.
4. `Loadable` holds the two members every configuration has (`load`, `from_dict`). `cfg` copies
    them onto the class; the class need not inherit from it, which is how it stays free of a
    base. `to_dict` and `write` are functions of the module, not members: they take the
    configuration as their first argument. Checkers don't see members added by a decorator:
    a class that wants them seen names `Loadable` as its base, or uses `load_cfg` / `from_dict`.

A field whose type is another configuration is a nested section / table: the builder (`_build`)
makes it from the sub-dictionary. enforced checks the leaves; it does not turn a dict into a class.
"""
from ._errors import ConfigError, _where
from ._sources import SOURCES_ATTR, OptionalFile
from ._readers import CONFIG_READERS, CfgType, RawConfigData
from ._writers import TEXTS, WRITERS, plain_json
from ._template import save_template
from ._hints import (
    KEY_STYLES,
    METADATA,
    OPTIONS_ATTR,
    Options,
    key_of,
    said,
    section_of,
    styled
    )
from typing import (
    Any,
    Callable,
    ClassVar,
    Literal,
    Self,
    overload,
    dataclass_transform,
    get_origin,
    get_type_hints,
    )
from dataclasses import (
    MISSING,
    Field,
    field as dataclass_field,
    fields,
    is_dataclass
    )
from collections.abc import Mapping, Sequence
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


class Loadable:
    """
    The members every `@cfg` class gets, `load` and `from_dict`. `cfg` copies them onto the class it
    decorates, so no base class is needed. As a base it is for type checkers only: they don't see
    members a decorator adds, and do see inherited ones.

        @cfg
        class App(Loadable):
            name: str

        app = App.load("app.toml")       # a checker knows `load`, and that it gives an `App`

    It is not a configuration itself (the rule that configurations are not inheritable is about `@cfg`
    classes), and a class below it is one only with `@cfg`.
    """
    __cfg_options__: ClassVar[Options]                              # strict, frozen, env_prefix, keys
    #* field name -> the nested configuration class, or list[it] / tuple[it, ...] / dict[str, it]
    __cfg_sections__: ClassVar[dict[str, Any]]

    @classmethod
    def load(
        cls,
        file: str | os.PathLike[str] | OptionalFile | Sequence[str | os.PathLike[str] | OptionalFile],
        cfg_type: CfgType | Literal["infer"] = "infer",
        *,
        env_prefix: str | None = None,
        env: Mapping[str, str] | None = None,
        env_file: str | os.PathLike[str] | OptionalFile | None = None,
        section: str | Sequence[str] | None = None,
        overrides: Mapping[str, Any] | None = None,
        show_warnings: bool = True
        ) -> Self:
        from ._load import load_cfg          # _load imports _base: here, not at the top
        return load_cfg(
            file, cls, cfg_type,
            env_prefix = env_prefix,
            env = env,
            env_file = env_file,
            section = section,
            overrides = overrides,
            show_warnings = show_warnings
            )

    @classmethod
    def from_dict(cls, data: RawConfigData, *, show_warnings: bool = True) -> Self:
        """Build from a dictionary already read (what every reader returns): sections made, keys checked, as `load_cfg`."""
        return from_dict(cls, data, show_warnings = show_warnings)


Config = Loadable                                                   # the name it had before it was public


def from_dict[T](schema: type[T], data: RawConfigData, *, show_warnings: bool = True) -> T:
    """
    Build `schema` from a dictionary already read (what every reader returns): sections made, keys
    checked, every problem in one `ConfigError`, as `load_cfg` does for a file. The same as
    `schema.from_dict(data)`, written so that a type checker knows what it returns.
    """
    from ._build import build          # _build imports _base: here, not at the top
    if not (isinstance(schema, type) and is_config(schema)):
        raise TypeError(f"from_dict: {schema!r} is not a @cfg class")
    return build(schema, data, "<dict>", show_warnings)


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
        raise TypeError(f"to_dict takes an instance of a @cfg class, got {obj!r}")
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

        >>> sources_of(load_cfg(["base.toml", "prod.yaml"], App, env_prefix = "APP_"))
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
        raise TypeError(f"sources_of takes an instance of a @cfg class, got {obj!r}")
    source_data = vars(obj).get(SOURCES_ATTR)
    if source_data is None:
        raise ValueError(
            f"sources_of: this {type(obj).__name__} was not loaded (it was constructed directly, or is a default): "
            f"ask the configuration that load_cfg or from_dict returned"
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


def to_text(
    obj: object,
    cfg_type: CfgType = "json",
    *,
    overrides: dict[str, Any] | None = None,
    reveal_secrets: bool = False) -> str:
    """
    The configuration as text in one of the formats: what `write` would write to a file, but for one
    difference: **a `SecretStr` is the mask** (`**********`) unless `reveal_secrets` is `True`. Text is
    printed, logged and sent; a file is where secrets go.

    `overrides` as for `write`: checked against the schema, and never changing `obj`.
    """
    if isinstance(obj, type) or not is_config(obj):
        raise TypeError(f"to_text takes an instance of a @cfg class, got {obj!r}")
    if type(reveal_secrets) is not bool:
        raise TypeError(f"to_text: reveal_secrets must be True or False, got {reveal_secrets!r}")
    if cfg_type not in TEXTS:
        raise ValueError(f"Unsupported configuration type: {cfg_type!r} (one of {', '.join(TEXTS)})")
    return TEXTS[cfg_type](_with(obj, overrides, "<overrides>", "to_text"), mask = not reveal_secrets)


def write(
    obj: object,
    file: str | os.PathLike[str],
    overrides: dict[str, Any] | None = None,
    cfg_type: CfgType | Literal["infer"] = "infer") -> None:
    """
    Write the configuration to `file` in any of the formats (each is a dictionary, so all
    four can be written), with our own writers. `overrides` apply to the file only: they are
    checked against the schema (nested keys as nested dictionaries; strict as for loading) and
    never change this object. An override that a lenient schema ignores isn't written, and always
    gives a `ConfigWarning`. A `SecretStr` is written as its real text, not the mask.
    Comments and the layout of a file it was loaded from are not kept.

    A `ConfigError` for overrides the schema refuses, a `TypeError` / `ValueError` for a value
    the format can't hold: either way before the file is opened, so nothing is written. The file
    is written whole or not at all: to a temporary file next to it, which then takes its place.
    """
    if isinstance(obj, type) or not is_config(obj):
        raise TypeError(f"write takes an instance of a @cfg class, got {obj!r}")
    selected_cfg_type = _format_of(file, cfg_type)
    WRITERS[selected_cfg_type](file, _with(obj, overrides, os.fspath(file), "write"))
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
        raise TypeError(f"write_template takes a @cfg class, got {schema!r}")
    if type(overwrite) is not bool:
        raise TypeError(f"write_template: overwrite must be True or False, got {overwrite!r}")
    save_template(schema, file, _format_of(file, cfg_type), overwrite)
    return


def _with(obj: Any, overrides: dict[str, Any] | None, name: str, caller: str) -> dict[str, Any]:
    """The configuration as a dictionary, `overrides` laid over it and checked (`name`: what a problem of theirs names)."""
    from ._build import build          # _build imports _base: here, not at the top

    data = _as_dict(obj)
    if overrides:
        if not isinstance(overrides, dict):
            raise TypeError(f"{caller}: overrides must be a dictionary of keys, got {type(overrides).__name__}")
        #* Checked by building a new instance (never assigned to obj), then written from that one:
        #* what is written is what the schema would load, converted values included.
        data = _as_dict(build(type(obj), _merged(data, overrides), name))

    return data


def _as_dict(obj: Any) -> dict[str, Any]:
    #* The keys of a file: a field(init = False) is worked out by the class, and no key (build refuses it).
    #* By the key of the file (`cfgcast.field(key = ...)`, `@cfg(keys = ...)`): what from_dict takes back.
    return {key_of(type(obj), f): _copy_node(getattr(obj, f.name)) for f in fields(obj) if f.init}


@enforced.dispatch
def _copy_node(node) -> Any:
    """Recursively copy a configuration node, converting configuration instances to dictionaries."""
    if is_dataclass(node) and not isinstance(node, type) and is_config(node):   # Special case, a configuration instance: convert to dictionary
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


def _merged(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """`base` with `overrides` laid over it, section by section (a new dictionary; neither is changed)."""
    out = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merged(out[key], value)
        else:
            out[key] = value
    return out


def is_config(obj: object) -> bool:
    """
    Whether `obj` is a configuration schema made by `@cfg`, or an instance of one. The options must be
    the class's own (`vars`, not `getattr`): a class below a configuration that wasn't decorated
    itself is not one. `isinstance(obj, type)`, not `type(obj) == type`: a class with a metaclass
    (an `ABC`, an `Enum`) is a class too.
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
    keeps working in a `@cfg` class: this one is needed only for `key` and `doc`.

    Args:
        key: the key of this field in a file, where it isn't the field's name: `max_size: int = field(key = "max-size")`,
            `class_: str = field(key = "class")`. Files, `to_dict`, `from_dict` and messages use the key;
            the attribute keeps its Python name (`pool.max_size`).
        doc: one line about the field, written as a comment above its key by `write_template`.
        default, default_factory, init, repr, hash, compare, metadata: as `dataclasses.field` has them.

    Example:
        >>> @cfg
        ... class Pool:
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


@overload
def cfg[T](cls: type[T], /) -> type[T]: ...
@overload
def cfg[T](
    *,
    strict: bool = True,
    frozen: bool = True,
    env_prefix: str | None = None,
    keys: Literal["kebab-case"] | None = None) -> Callable[[type[T]], type[T]]: ...
@dataclass_transform(
    kw_only_default = True,
    frozen_default = True,
    field_specifiers = (dataclass_field, Field, field),
    )
def cfg[T](
    cls: type[T] | None = None,
    /,
    *,
    strict: bool = True,
    frozen: bool = True,
    env_prefix: str | None = None,
    keys: Literal["kebab-case"] | None = None) -> Any:
    """
    Make `cls` a configuration schema. Each annotated attribute is a key; its type hint says what the value
    must be, and a default makes the key optional. A field typed as another `@cfg` class is a section.

    Args:
        strict (bool): refuse keys the schema doesn't name (default `True`). `False` ignores them, in this
            class and every section below it, whatever those say: an older program reading a newer
            file. Loading then gives a `ConfigWarning` that names them (`show_warnings` of `load_cfg`).
        frozen (bool): instances can't be changed after loading (default `True`).
        env_prefix (str | None): read environment variables with this prefix over the files whenever this
            class is loaded (`load_cfg`, `load`), as `load_cfg(..., env_prefix = ...)` does; that argument wins
            over this one. Only the class that is loaded counts, not a section's. `from_dict` reads none.
        keys ("kebab-case" | None): how a field's name is written in a file. `"kebab-case"`: `max_size` is the key
            `max-size` (and `class_` is `class`), in this class only: a section says its own. A field's
            own `field(key = ...)` wins. Attributes keep their Python names.

    `coerce` and `deep` are always on and are not options: `"8080"` is read as `8080` for an `int`, and `list[Port]` has every item checked.

    Example:
        >>> from enforced.data_types import SecretStr, Port
        ... from cfgcast import cfg, load_cfg
        ...
        ... @cfg
        ... class Database:
        ...     host: str
        ...     port: Port
        ...     name: str
        ...     user: str
        ...     password: SecretStr
        ... 
        ... @cfg
        ... class Server:
        ...     host: str
        ...     port: Port = 8080
        ...     database: Database
        ...     backend: str = "http"
        ...
        ... server = load_cfg("server.yaml", Server)
        ... server.host
        ... server.database.name
        ... server.database.user
        ... server.database.password = "bad"    # dataclasses.FrozenInstanceError: frozen is True by default

    A `TypeError` for what isn't a plain class (a dataclass already, a configuration already, a class
    with a configuration among its bases), for options that aren't `bool`, and for a field or a
    method of the class's own named `load` or `from_dict` (the members `cfg` adds). A class can't be
    derived from a configuration afterwards either: configurations are not inheritable.
    """
    for name, option in (("strict", strict), ("frozen", frozen)):
        if type(option) is not bool:
            raise TypeError(f"cfg: {name} must be True or False, got {option!r}")
    if env_prefix is not None and not isinstance(env_prefix, str):
        raise TypeError(f"cfg: env_prefix must be text (or None for no environment variables), got {env_prefix!r}")
    if keys is not None and keys not in KEY_STYLES:
        raise TypeError(f"cfg: keys must be None or one of {', '.join(map(repr, KEY_STYLES))}, got {keys!r}")
    options = Options(strict, frozen, env_prefix, keys)
    if cls is None:
        return lambda target: _create_the_config_object(target, options)
    return _create_the_config_object(cls, options)


# ── how a class becomes a configuration ─────────────────────────────────────

#* What cfg adds to every class: the names the class itself may not use.
_CONFIG_MEMBERS = ("load", "from_dict")


def _create_the_config_object[T](cls: type[T], options: Options) -> type[T]:

    #* Validate the class before making it a configuration
    _validate_class_input(cls, options)
    setattr(cls, OPTIONS_ATTR, options)

    #* Enforced Dataclass
    #* ------------------
    #* The class is turned into an enforced dataclass with the given options.
    #* This provides all the mechanics of enforced dataclasses, including type checking, coercion, deep validation etc.
    #* The input for this dynamically created dataclass is the loaded file content
    enforced_dataclass_cfg = enforced.dataclass(
        cls,
        kw_only = True,
        frozen = options.frozen,
        coerce = True,
        deep = True,
        error = ConfigError
        )

    #* Dynamically add the configuration members to the new dataclass
    for name in _CONFIG_MEMBERS:
        setattr(enforced_dataclass_cfg, name, vars(Loadable)[name])       # the classmethod object: binds to the decorated class when read
    setattr(enforced_dataclass_cfg, SECTIONS_ATTR, _Sections())
    if "__init_subclass__" not in vars(enforced_dataclass_cfg):
        enforced_dataclass_cfg.__init_subclass__ = classmethod(_not_inherited)        # type: ignore[assignment]

    return enforced_dataclass_cfg


def _validate_class_input(cls: Any, options: Options) -> None:
    """Everything `cfg` can't make a configuration of, said before anything is changed."""

    if not isinstance(cls, type):
        raise TypeError(f"cfg decorates a class, got {cls!r} (options are keywords: @cfg(strict = False))")
    if is_config(cls):
        raise TypeError(f"cfg: {cls.__qualname__} is a configuration already")
    if "__dataclass_fields__" in vars(cls):
        raise TypeError(f"cfg: {cls.__qualname__} is a dataclass already: write @cfg in place of @dataclass")
    if any(is_config(base) for base in cls.__mro__[1:]):            # also refused at the class statement (_not_inherited)
        raise TypeError(f"cfg: {cls.__qualname__} derives from a configuration; configurations are not inheritable")

    own, names = vars(cls), _annotated_names(cls)
    for name in _CONFIG_MEMBERS:
        if name in names or name in own:
            raise TypeError(f"cfg: {cls.__qualname__}.{name}: the name is taken by what cfg adds ({', '.join(_CONFIG_MEMBERS)}); choose another")

    #* Two fields with one key: a file could give only one of them.
    taken: dict[str, str] = {}
    for name, hint in names.items():
        if _is_class_variable(hint):
            continue
        written = own.get(name)
        key = (said(written, "key") if isinstance(written, Field) else None) or styled(name, options.keys)
        if key in taken:
            raise TypeError(f"cfg: {cls.__qualname__}: the fields {taken[key]} and {name} have the same key {key!r}")
        taken[key] = name

    return


def _annotated_names(cls: type) -> dict[str, Any]:
    """The names the class annotates itself, in the order written, each with its annotation: not evaluated where that can't be (it may name a class not made yet)."""
    try:
        if sys.version_info >= (3, 14):
            return dict(annotationlib.get_annotations(cls, format = annotationlib.Format.FORWARDREF))
        return dict(inspect.get_annotations(cls))           # 3.13 has evaluated them: a section named before it is written is quoted
    except Exception:
        return {}


def _is_class_variable(hint: Any) -> bool:
    """Whether an annotation is a `ClassVar` (no field, so no key), also as the text of a quoted one."""
    if isinstance(hint, str):
        return hint.replace("typing.", "").lstrip().startswith("ClassVar")
    return hint is ClassVar or get_origin(hint) is ClassVar


def _not_inherited(cls: type, **_) -> None:
    """The `__init_subclass__` of a configuration: refuses the class statement that derives from it."""
    base = next(base for base in cls.__mro__[1:] if is_config(base))
    raise TypeError(
        f"{cls.__qualname__} derives from the configuration {base.__qualname__}: configurations are "
        f"not inheritable (write a configuration of its own, or use {base.__qualname__} as a section)"
        )


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
            if found is not None and is_config(found[1]):
                kind, section = found
                sections[key_of(owner, field_)] = {
                    "one": section,
                    "list": list[section],
                    "tuple": tuple[section, ...],
                    "dict": dict[str, section]
                    }[kind]
        setattr(owner, SECTIONS_ATTR, sections)
        return sections
