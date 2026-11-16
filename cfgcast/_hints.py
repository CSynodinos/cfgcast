"""
What an annotation says, asked the same way by every module: the members of a hint, whether it
holds a secret, whether its field is a section (or a list / dictionary of sections) or a list.

A section is any dataclass (a `@cfg` class is one): this module is below `_base`, and doesn't know
`@cfg`. `_base._Sections` keeps the `@cfg` ones only.
"""
from types import UnionType
from typing import Annotated, Any, Literal, NamedTuple, TypeAliasType, Union, get_args, get_origin, get_type_hints
from enforced.data_types import SecretStr
import collections.abc
import dataclasses

OPTIONS_ATTR = "__cfg_options__"


class Options(NamedTuple):
    """What `@cfg(...)` was given, kept on the class as `__cfg_options__` and read by name."""
    strict: bool = True
    frozen: bool = True
    env_prefix: str | None = None
    keys: str | None = None


#* Where `cfgcast.field` keeps what it is told, in a field's `metadata`: {"key": ..., "doc": ...}.
METADATA = "cfgcast"
KEY_STYLES = ("kebab",)


def said(field: Any, what: str) -> Any:
    """What `cfgcast.field` was told about a field (`"key"`, `"doc"`), None if nothing."""
    return (field.metadata.get(METADATA) or {}).get(what)


def styled(name: str, style: str | None) -> str:
    """A field's name as the key of a file, in the style of `@cfg(keys = ...)`: `max_size` is `max-size` in kebab, `class_` is `class`."""
    return name.rstrip("_").replace("_", "-") if style == "kebab" else name


def key_of(schema: type, field: Any) -> str:
    """The key a field has in a file: the `key` of `cfgcast.field`, else its name in the style of the class, else its name."""
    return said(field, "key") or styled(field.name, options_of(schema).keys)


def keys_of(schema: type) -> dict[str, Any]:
    """The keys of a schema, each with its field: what a file may hold at this level (a `field(init = False)` is no key)."""
    return {key_of(schema, field): field for field in dataclasses.fields(schema) if field.init}


def options_of(schema: type) -> Options:
    """The options of a `@cfg` class (its own: `vars`, not `getattr`), the defaults for any other dataclass used as a section."""
    return vars(schema).get(OPTIONS_ATTR, Options())


def hints_of(schema: type) -> dict[str, Any]:
    """The annotations of a schema by field name, `{}` where one can't be resolved (the build then reports it)."""
    try:
        return get_type_hints(schema, include_extras = True)
    except Exception:
        return {}


#* How a field holds sections: one (`db: Database`), a list (`list[Server]`), a tuple
#* (`tuple[Server, ...]`) or a dictionary of them by name (`dict[str, Server]`).
type SectionKind = Literal["one", "list", "tuple", "dict"]


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


def section_of(hint: Any) -> tuple[SectionKind, type] | None:
    """
    How the field of this annotation holds sections, and their class: `("one", Database)` for `Database` or
    `Database | None`, `("list", Server)` for `list[Server]`, `("tuple", Server)` for `tuple[Server, ...]`,
    `("dict", Server)` for `dict[str, Server]`. None for a field of values.
    """
    for member in members(hint):
        if _is_section(member):
            return "one", member
        origin, args = get_origin(member), get_args(member)
        item: Any = None
        kind: SectionKind
        if origin is list and len(args) == 1:
            kind, item = "list", args[0]
        elif origin is tuple and len(args) == 2 and args[1] is Ellipsis:
            kind, item = "tuple", args[0]
        elif origin is dict and len(args) == 2:
            kind, item = "dict", args[1]
        else:
            continue
        for inner in members(item):
            if _is_section(inner):
                return kind, inner
    return None


def _is_section(member: Any) -> bool:
    return isinstance(member, type) and dataclasses.is_dataclass(member)


_LISTS = (list, tuple, set, frozenset, collections.abc.Sequence, collections.abc.MutableSequence,
          collections.abc.Set, collections.abc.MutableSet, collections.abc.Collection, collections.abc.Iterable)
_DICTS = (dict, collections.abc.Mapping, collections.abc.MutableMapping)


def is_list(hint: Any) -> bool:
    """Whether the annotation is a list of some kind (`list[str]`, `tuple[int, ...] | None`, `Sequence[Port]`)."""
    return any(member in _LISTS or get_origin(member) in _LISTS for member in members(hint))


def is_dict(hint: Any) -> bool:
    """Whether the annotation is a dictionary (`dict[str, float]`, `Mapping[str, Server] | None`)."""
    return any(member in _DICTS or get_origin(member) in _DICTS for member in members(hint))


def split_list(text: str) -> list[str]:
    """Text as a list, ini's rule: split on commas, each item stripped; empty text is an empty list."""
    return [item.strip() for item in text.split(",")] if text.strip() else []
