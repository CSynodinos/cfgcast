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
3. The options are stored on the class (`__cfg_options__`: `(strict, frozen)`), and the fields
   that are sections on `__cfg_sections__`. A class with `__cfg_options__` is a configuration.
4. `Config` holds the two members every configuration has (`load`, `from_dict`). `cfg` copies
   them onto the class; the class does not inherit from it, which is how it stays free of a
   base. `to_dict` and `write` are functions of the module, not members: they take the
   configuration as their first argument. (Open: checkers don't see members added by a
   decorator; see `PLAN.md`.)

A field whose type is another configuration is a nested section / table: the builder (`_build`)
makes it from the sub-dictionary. enforced checks the leaves; it does not turn a dict into a class.
"""
from ._errors import ConfigError
from ._readers import CONFIG_READERS, CfgType, RawConfigData
from ._writers import WRITERS
from typing import (
    Annotated,
    Any,
    Callable,
    ClassVar,
    Literal,
    Self,
    TypeAliasType,
    Union,
    overload,
    dataclass_transform,
    get_args,
    get_origin,
    get_type_hints,
    )
from types import NoneType, UnionType
from dataclasses import Field, field, fields, is_dataclass
import enforced
import inspect
import os
import sys

if sys.version_info >= (3, 14):
    import annotationlib


OPTIONS_ATTR = "__cfg_options__"
SECTIONS_ATTR = "__cfg_sections__"

#* .cfg is configparser's too: one format, two suffixes.
SUFFIXES: dict[str, CfgType] = {
    ".json": "json", ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".yaml": "yaml", ".yml": "yaml",
    }


class Config:
    """
    The members every `@cfg` class gets. Not a base class, and not exported: `cfg` copies these
    functions onto the class it decorates.
    """
    __cfg_options__: ClassVar[tuple[bool, bool]]                    # (strict, frozen)
    __cfg_sections__: ClassVar[dict[str, type]]                     # field name -> the nested configuration class

    @classmethod
    def load(cls, file: str | os.PathLike[str], cfg_type: CfgType | Literal["infer"] = "infer", *, show_warnings: bool = True) -> Self:
        """`load_cfg(file, cls, cfg_type = ..., show_warnings = ...)`."""
        from ._build import build          # _build imports _base: here, not at the top

        selected_cfg_type = _format_of(file, cfg_type)
        #* Load data and return an instance of the configuration class from the dictionary output
        reader = CONFIG_READERS[selected_cfg_type]
        data: RawConfigData = reader(file, cls) if selected_cfg_type == "ini" else reader(file)
        #* build, not from_dict: the file goes first in every problem's loc
        return build(cls, data, os.fspath(file), show_warnings)


    @classmethod
    def from_dict(cls, data: RawConfigData, *, show_warnings: bool = True) -> Self:
        """Build from a dictionary already read (what every reader returns): sections made, keys checked, as `load_cfg`."""
        from ._build import build          # _build imports _base: here, not at the top
        return build(cls, data, "<dict>", show_warnings)  #? remember, cls has become a dataclass via @cfg



def to_dict(obj: object) -> dict[str, Any]:
    """
    The values as a dictionary, sections as nested dictionaries. Values are as they are on the
    object (a `SecretStr` stays one, masked when printed); lists and dictionaries are copies, so
    changing the result never changes the configuration.
    """
    if isinstance(obj, type) or not is_config(obj):
        raise TypeError(f"to_dict takes an instance of a @cfg class, got {obj!r}")
    return _as_dict(obj)


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
    the format can't hold: either way before the file is opened, so nothing is written.
    """
    from ._build import build          # _build imports _base: here, not at the top

    if isinstance(obj, type) or not is_config(obj):
        raise TypeError(f"write takes an instance of a @cfg class, got {obj!r}")
    selected_cfg_type = _format_of(file, cfg_type)
    data = _as_dict(obj)
    if overrides:
        if not isinstance(overrides, dict):
            raise TypeError(f"write: overrides must be a dictionary of keys, got {type(overrides).__name__}")
        #* Checked by building a new instance (never assigned to obj), then written from that one:
        #* what is written is what the schema would load, converted values included.
        data = _as_dict(build(type(obj), _merged(data, overrides), os.fspath(file)))
    WRITERS[selected_cfg_type](file, data)


def _as_dict(obj: Any) -> dict[str, Any]:
    return {f.name: _copy(getattr(obj, f.name)) for f in fields(obj)}


def _copy(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type) and is_config(value):
        return _as_dict(value)
    if isinstance(value, list):
        return [_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy(item) for item in value)
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return type(value)(_copy(item) for item in value)
    return value


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
            raise ValueError(
                f"Unsupported configuration type: {cfg_type!r} "
                f"(one of {', '.join(CONFIG_READERS)}, or 'infer')"
                )


@overload
def cfg[T](cls: type[T], /) -> type[T]: ...
@overload
def cfg[T](*, strict: bool = True, frozen: bool = True) -> Callable[[type[T]], type[T]]: ...
@dataclass_transform(
    kw_only_default = True,
    frozen_default = True,
    field_specifiers = (field, Field),
    )
def cfg[T](cls: type[T] | None = None, /, *, strict: bool = True, frozen: bool = True) -> Any:
    """
    Make `cls` a configuration schema. Each annotated attribute is a key; its type hint says what the value
    must be, and a default makes the key optional. A field typed as another `@cfg` class is a section.

    Args:
        strict (bool): refuse keys the schema doesn't name (default `True`). `False` ignores them, in this
            class and every section below it, whatever those say: an older program reading a newer
            file. Loading then gives a `ConfigWarning` that names them (`show_warnings` of `load_cfg`).
        frozen (bool): instances can't be changed after loading (default `True`).

    `coerce` and `deep` are always on and are not options: `"8080"` is read as `8080` for an `int`, and `list[Port]` has every item checked.

    Example:
        >>> from enforced import SecretStr, Port
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
    if cls is None:
        return lambda target: _create_the_config_object(target, strict, frozen)
    return _create_the_config_object(cls, strict, frozen)


# ── how a class becomes a configuration ─────────────────────────────────────

#* What cfg adds to every class: the names the class itself may not use.
_CONFIG_MEMBERS = ("load", "from_dict")


def _create_the_config_object[T](cls: type[T], strict: bool, frozen: bool) -> type[T]:

    #* Validate the class before making it a configuration
    _validate_class_input(cls)
    setattr(cls, OPTIONS_ATTR, (strict, frozen))

    #* Enforced Dataclass
    #* ------------------
    #* The class is turned into an enforced dataclass with the given options.
    #* This provides all the mechanics of enforced dataclasses, including type checking, coercion, deep validation etc.
    #* The input for this dynamically created dataclass is the loaded file content
    enforced_dataclass_cfg = enforced.dataclass(
        cls,
        kw_only = True,
        frozen = frozen,
        coerce = True,
        deep = True,
        error = ConfigError
        )

    #* Dynamically add the configuration members to the new dataclass
    for name in _CONFIG_MEMBERS:
        setattr(enforced_dataclass_cfg, name, vars(Config)[name])       # the classmethod object: binds to the decorated class when read
    setattr(enforced_dataclass_cfg, SECTIONS_ATTR, _Sections())
    if "__init_subclass__" not in vars(enforced_dataclass_cfg):
        enforced_dataclass_cfg.__init_subclass__ = classmethod(_not_inherited)        # type: ignore[assignment]

    return enforced_dataclass_cfg


def _validate_class_input(cls: Any) -> None:
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


def _annotated_names(cls: type) -> set[str]:
    """The names the class annotates itself, without evaluating an annotation (they may name classes not made yet)."""
    try:
        if sys.version_info >= (3, 14):
            return set(annotationlib.get_annotations(cls, format = annotationlib.Format.FORWARDREF))
        return set(inspect.get_annotations(cls))            # 3.13 has evaluated them: a section named before it is written is quoted
    except Exception:
        return set()


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
    is another configuration (`Database`, `Database | None`), field name -> that class. Not at
    decoration: an annotation may name a section written further down, which doesn't exist yet
    then (quoted on 3.13, which evaluates an annotation where it stands). The first read replaces
    this object on the class by the dictionary.
    """

    def __get__(self, _: object, owner: type) -> dict[str, type]:
        sections: dict[str, type] = {}
        hints = get_type_hints(owner, include_extras = True)
        for name in getattr(owner, "__dataclass_fields__", {}):
            for member in _extract_members(hints.get(name)):
                if isinstance(member, type) and is_config(member):
                    sections[name] = member
                    break
        setattr(owner, SECTIONS_ATTR, sections)
        return sections


def _extract_members(hint: Any) -> list[Any]:
    """The annotations a hint stands for: `Annotated` and `type` aliases taken off, each union member."""
    if isinstance(hint, TypeAliasType):
        return _extract_members(hint.__value__)
    if get_origin(hint) is Annotated:
        return _extract_members(get_args(hint)[0])
    if get_origin(hint) in (Union, UnionType):
        return [m for member in get_args(hint) if member is not NoneType for m in _extract_members(member)]
    return [hint]
