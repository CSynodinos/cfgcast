"""
A template of a schema: a file with every key, to be filled in. The counterpart of `write`, for a
class instead of an instance.

A key with a default is written with it. A key is written *commented out*, with a value to replace,
where there is nothing to write: it is required, its default is `None`, or it is a secret (**the
default of a `SecretStr` is never written**). A key commented out is `#key = value`, with no space; a
note is `# note`: the two are told apart by eye and by a program. So a template loads as soon as its required keys are
filled in, and gives the defaults. Above a key: its `doc` (`cfgcast.field`), then what is to be
known of it (`required`, `secret`, the type where it says more than the value does).

A schema becomes a tree first (`_Value`, `_Section`), which knows no format; one function per format
turns the tree into lines, with the writers' own rules for a key and a value. Comments are whole
lines only (`#`): ini has no other kind.
"""
from dataclasses import MISSING, dataclass, field as dataclass_field, is_dataclass
from enum import Enum
from types import NoneType
from typing import Annotated, Any, Callable, Literal, TypeAliasType, Union, get_args, get_origin
from types import UnionType
from ._hints import hints_of, is_dict, is_list, keys_of, members, mentions_secret, said, section_of
from ._readers import CfgType
from ._writers import _INI_KEY, _ini_text, _plain, _save, _toml_key, _toml_value, _yaml_key, _yaml_mapping
import os


@dataclass
class _Value:
    """A key of values: `value` is plain data (what a writer takes), the default or, not `active`, what to replace."""
    key: str
    notes: list[str]
    value: Any
    active: bool
    required: bool = False


@dataclass
class _Section:
    """A key of keys. `kind`: one section, or the one example of a list (`"list"`) or dictionary (`"dict"`) of them."""
    key: str
    notes: list[str]
    kind: str
    active: bool
    required: bool
    items: list["_Value | _Section"] = dataclass_field(default_factory = list)


type _Item = _Value | _Section

#* The name of the one entry shown for a dictionary of sections (`[servers.name]`).
EXAMPLE = "name"


def template_text(schema: type, cfg_type: CfgType) -> str:
    """The template of `schema` as text, in TOML, YAML or ini (`ValueError` for JSON: it has no comments)."""
    if cfg_type not in _TEMPLATES:
        if cfg_type == "json":
            raise ValueError(
                "a template can't be JSON: JSON has no comments, and a template says in them which keys are "
                "required (use TOML, YAML or ini)"
                )
        raise ValueError(f"Unsupported configuration type: {cfg_type!r} (one of {', '.join(_TEMPLATES)})")
    lines = _Lines([
        f"# A template for {schema.__name__}, made by cfgcast. A key behind a '#' (#key) is required, a secret,",
        "# or has no value by default: remove the '#' and give it a value. A '# ' with a space is a note.",
        ])
    _TEMPLATES[cfg_type](lines, _items(schema, None, ()))
    return "\n".join(lines) + "\n"


def save_template(schema: type, file: str | os.PathLike[str], cfg_type: CfgType, overwrite: bool) -> None:
    """`template_text` as the whole of `file`; a file that is there is kept (`FileExistsError`) unless `overwrite`."""
    text = template_text(schema, cfg_type)                          # made in full first: a refusal writes nothing
    try:
        _save(file, text, new = not overwrite)                      # not a look, then a write: a file made between the two would be lost
    except FileExistsError:
        raise FileExistsError(
            f"{os.fspath(file)} exists: a template written over a configuration would lose it "
            f"(overwrite = True to replace it)"
            ) from None


# ── the tree ────────────────────────────────────────────────────────────────

def _items(schema: type, instance: Any, above: tuple[type, ...]) -> list[_Item]:
    """The keys of one level: `instance` is the default this section has (its values are the ones written), None for the class's own."""
    hints = hints_of(schema)
    items: list[_Item] = []
    for key, field in keys_of(schema).items():
        hint = hints.get(field.name)
        default = getattr(instance, field.name, MISSING) if instance is not None else _default(field)
        notes = [line for line in str(said(field, "doc") or "").splitlines() if line.strip()]
        section = section_of(hint)
        if section is not None:
            items.append(_section(key, notes, section, hint, default, (*above, schema)))
        else:
            items.append(_value(key, notes, hint, default))
    return items


def _default(field: Any) -> Any:
    """What a field is without a key: its default, its factory's, `MISSING` for a required one (or a factory that fails)."""
    if field.default is not MISSING:
        return field.default
    if field.default_factory is not MISSING:
        try:
            return field.default_factory()
        except Exception:
            return MISSING
    return MISSING


def _section(key: str, notes: list[str], section: tuple[str, type], hint: Any, default: Any, above: tuple[type, ...]) -> _Item:
    kind, schema = section
    required = default is MISSING
    if above.count(schema) > 1:                                     # a schema that holds itself: its keys are written once
        return _Value(key, [*notes, _note(required, False, hint), f"as {schema.__name__}, above"], {} if kind in ("one", "dict") else [], False)
    if kind == "one":
        instance = default if is_dataclass(default) and not isinstance(default, type) else None
        #* A section that is None by default stays one: written commented out, with its keys.
        active = required or instance is not None
        note = [] if active and _text(hint) == schema.__name__ else [_note(required, False, hint)]
        return _Section(key, [*notes, *note], "one", active, required, _items(schema, instance, above))
    #* A list or a dictionary of sections: one item to copy, never one that is read (the default, if any, stays).
    many = "a list: one of these for each item" if kind in ("list", "tuple") else f"one of these for each entry, '{EXAMPLE}' its name"
    return _Section(key, [*notes, _note(required, False, hint), many], "dict" if kind == "dict" else "list", False, required, _items(schema, None, above))


def _value(key: str, notes: list[str], hint: Any, default: Any) -> _Item:
    required, secret = default is MISSING, mentions_secret(hint)
    plain: Any = None
    if not required and not secret and default is not None:
        try:
            #* mask = True: a secret inside a default whose annotation doesn't say so (a `dict[str, Any]`) is no text either.
            plain = _plain(default, True)
        except (TypeError, ValueError):                             # a default no file can hold: the key is left to its default
            plain = None
    if plain is None:
        return _Value(key, [*notes, _note(required, secret, hint)], _placeholder(hint), False, required)
    if isinstance(plain, dict) and all(isinstance(k, str) for k in plain):
        return _Section(key, [*notes, _text(hint)], "one", True, False, _entries(plain))
    return _Value(key, [*notes, *([] if _is_obvious(hint) else [_text(hint)])], plain, True)


def _entries(data: dict[str, Any]) -> list[_Item]:
    """A dictionary of a default as keys: a dictionary inside as a section, a None left out (no format writes one here)."""
    return [
        _Section(key, [], "one", True, False, _entries(value)) if isinstance(value, dict) and all(isinstance(k, str) for k in value)
        else _Value(key, [], value, True)
        for key, value in data.items() if value is not None
        ]


def _note(required: bool, secret: bool, hint: Any) -> str:
    what = ", ".join(word for word, said_ in (("required", required), ("secret", secret)) if said_)
    kind = _text(hint)
    return f"{what}: {kind}" if what and kind else what or kind


def _is_obvious(hint: Any) -> bool:
    """Whether the value says all the annotation does: `port = 8080` needs no `int` above it."""
    return hint is None or hint in (str, int, float, bool)


def _text(hint: Any) -> str:
    """An annotation as it is written: `Port`, `int | None`, `list[str]`, `Literal['a', 'b']`. Empty where it isn't known."""
    if hint is None:
        return ""
    if hint is NoneType:
        return "None"
    if isinstance(hint, TypeAliasType):
        return hint.__name__
    origin, args = get_origin(hint), get_args(hint)
    if origin is Annotated:
        return _text(args[0])
    if origin in (Union, UnionType):
        return " | ".join(_text(arg) for arg in args)
    if origin is Literal:
        return f"Literal[{', '.join(repr(arg.value) if isinstance(arg, Enum) else repr(arg) for arg in args)}]"
    if origin is not None:
        inner = ", ".join("..." if arg is Ellipsis else _text(arg) for arg in args)
        return f"{getattr(origin, '__name__', str(origin))}[{inner}]"
    name = getattr(hint, "__name__", None) or str(hint)
    if isinstance(hint, type) and issubclass(hint, Enum) and 0 < len(hint) <= _FEW:
        try:
            return f"{name} ({', '.join(repr(_plain(member, True)) for member in hint)})"
        except (TypeError, ValueError):
            pass
    return name


#* An enumeration of this many members at most is written with them (`Level ('low', 'high')`).
_FEW = 8


def _placeholder(hint: Any) -> Any:
    """A value of the right kind for a key that has none: what is replaced when the key is filled in. Never a default."""
    if is_list(hint):
        return []
    if is_dict(hint):
        return {}
    for member in members(hint):
        if get_origin(member) is Literal:
            first = get_args(member)[0]
            return first.value if isinstance(first, Enum) else first
        if isinstance(member, type):
            if issubclass(member, Enum):
                try:
                    return _plain(next(iter(member)), True)
                except (StopIteration, TypeError, ValueError):
                    return ""
            for plain, value in ((bool, False), (int, 0), (float, 0.0)):    # bool first: it is an int
                if issubclass(member, plain):
                    return value
    return ""


# ── the formats ─────────────────────────────────────────────────────────────

class _Lines(list[str]):
    """The lines of a template, and whether the key written last had notes (the next one then stands apart)."""
    noted = True


def _block(lines: _Lines, notes: list[str], body: list[str], commented: bool, pad: str = "", *, apart: bool = False) -> None:
    """
    One key: its notes as comments (`# note`), then its lines (each behind a `#`, with no space, when commented out). An empty line
    before it when it has notes, when the key before it had, or when it is `apart` (a header): keys
    with nothing to say stay together.
    """
    notes = [note for note in notes if note]
    if notes or lines.noted or apart:
        lines.append("")
    lines.noted = bool(notes) and not apart                         # a key right under its header
    lines += [f"{pad}# {note}" for note in notes]
    lines += [f"{pad}#{line}" if commented else f"{pad}{line}" for line in body]


def _toml(lines: _Lines, items: list[_Item], path: tuple[str, ...] = (), commented: bool = False) -> None:
    #* The values of a table before its tables: a key after a header belongs to that header.
    for item in items:
        if isinstance(item, _Value):
            try:
                text = _toml_value(item.value, ".".join((*path, item.key)))
            except ValueError:                                      # a None in a list: TOML has no null, the key is left to its default
                _block(lines, [*item.notes, "TOML can't hold the default of this key (it has no null)"], [f"{_toml_key(item.key)} = []"], True)
                continue
            _block(lines, item.notes, [f"{_toml_key(item.key)} = {text}"], commented or not item.active)
    for item in items:
        if isinstance(item, _Section):
            inner = (*path, item.key, EXAMPLE) if item.kind == "dict" else (*path, item.key)
            name = ".".join(_toml_key(part) for part in inner)
            off = commented or not item.active
            _block(lines, item.notes, [f"[[{name}]]" if item.kind == "list" else f"[{name}]"], off, apart = True)
            _toml(lines, item.items, inner, off)


def _ini(lines: _Lines, items: list[_Item], path: tuple[str, ...] = (), commented: bool = False) -> None:
    values = [item for item in items if isinstance(item, _Value)]
    if not path and values:
        _block(lines, [], ["[DEFAULT]"], False, apart = True)
    for entry in values:
        where = ".".join((*path, entry.key))
        if not _INI_KEY.fullmatch(entry.key) or entry.key != entry.key.strip():
            raise ValueError(f"{where}: {entry.key!r} can't be an ini key")
        off = commented or not entry.active
        try:
            text = _ini_value(entry.value, where)
        except ValueError as error:                                 # a default ini can't hold: said, and left to the default
            _block(lines, [*entry.notes, f"ini can't hold the default of this key ({error})"], [f"{entry.key} ="], True)
            continue
        _block(lines, entry.notes, [f"{entry.key} = {text}".rstrip()], off)
    for item in items:
        if isinstance(item, _Section):
            if item.kind == "list":
                _block(lines, [*item.notes, f"{'.'.join((*path, item.key))}: ini can't hold a list of sections (use TOML or YAML)"], [], True)
                continue
            inner = (*path, item.key, EXAMPLE) if item.kind == "dict" else (*path, item.key)
            for part in inner:
                if "." in part or "]" in part or not part.strip() or part != part.strip():
                    raise ValueError(f"{'.'.join(inner)}: {part!r} can't be an ini section name")
            if inner == ("DEFAULT",):
                raise ValueError("DEFAULT: a section of that name would be read as the top-level keys")
            off = commented or not item.active
            _block(lines, item.notes, [f"[{'.'.join(inner)}]"], off, apart = True)
            _ini(lines, item.items, inner, off)


def _ini_value(value: Any, where: str) -> str:
    if isinstance(value, dict):
        if value:
            raise ValueError("a dictionary inside a list")
        return ""
    if not isinstance(value, list):
        return _ini_text(value, where)
    texts = []
    for item in value:
        if isinstance(item, (list, dict)) or item is None:
            raise ValueError(f"a list of {type(item).__name__}")
        text = _ini_text(item, where)
        if "," in text:
            raise ValueError("an item with a comma")
        texts.append(text)
    if texts == [""]:
        raise ValueError("a list of one empty text")
    return ", ".join(texts)


def _yaml(lines: _Lines, items: list[_Item], indent: int = 0, commented: bool = False) -> None:
    pad = " " * indent
    for item in items:
        off = commented or not item.active
        if isinstance(item, _Value):
            body: list[str] = []
            _yaml_mapping(body, {item.key: item.value}, 0)
            _block(lines, item.notes, body, off, pad)
            continue
        head, notes, deeper = f"{_yaml_key(item.key)}:", list(item.notes), indent + 2
        if item.kind != "one":
            _block(lines, notes, [head], True, pad, apart = True)
            lines.append(f"{pad}  #{'-' if item.kind == 'list' else _yaml_key(EXAMPLE) + ':'}")
            deeper = indent + 4
        else:
            #* A section with nothing active below it is null to YAML: loading takes that as an empty section.
            _block(lines, notes, [head], off, pad, apart = True)
        _yaml(lines, item.items, deeper, off)
        lines.noted = True                                          # what follows a section stands apart from it


_TEMPLATES: dict[str, Callable[[_Lines, list[_Item]], None]] = {"toml": _toml, "yaml": _yaml, "ini": _ini}
