"""
The line of each key of a file: what lets a problem say `app.toml:12: db.port: ...`, and `sources_of`
say where a value was written.

One locator per format, run on the text its parser has already read. The parsers give data and no
lines (`json`, `tomllib` and `configparser` keep none), so each locator reads the text a second time,
for its keys only: `{key path: line}`, lines from 1, a list item as its index (`("servers", 0, "host")`).

**A line that is wrong is worse than none.** A locator that fails says nothing (`locate` returns `{}`),
and a path it found that the parsed data doesn't have is dropped. A key without a line of its own (an
item of an ini list, a key of a TOML inline table) takes the line of the nearest key above it
(`Sources.line`). Nothing here reads a value: no text of a file leaves this module.
"""
from bisect import bisect_right
from typing import Any
import json
import re
import tomllib

type Path = tuple[Any, ...]


def locate(cfg_type: str, text: str, data: Any) -> dict[Path, int]:
    """The line of every key path of `text` (a file of the format `cfg_type`, parsed as `data`) that could be found."""
    try:
        found = _LOCATORS[cfg_type](text)
    except Exception:                                               # also a RecursionError: no lines, never wrong ones
        return {}
    return {path: line for path, line in found.items() if _has(data, path)}


def _has(data: Any, path: Path) -> bool:
    for key in path:
        if isinstance(data, dict) and key in data:
            data = data[key]
        elif isinstance(data, list) and type(key) is int and 0 <= key < len(data):
            data = data[key]
        else:
            return False
    return True


def _lines(text: str) -> list[int]:
    """Where each line of the text starts: `bisect_right(starts, position)` is the line of a position."""
    return [0, *(match.end() for match in re.finditer("\n", text))]


# ── JSON ────────────────────────────────────────────────────────────────────

_JSON_SPACE = re.compile(r"[ \t\n\r]*")
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
_JSON_SCALAR = re.compile(r"[^\s,\]}]+")


def _json(text: str) -> dict[Path, int]:
    starts = _lines(text)
    found: dict[Path, int] = {}

    def skip(at: int) -> int:
        return _JSON_SPACE.match(text, at).end()                    # type: ignore[union-attr]  # it matches the empty text

    def value(at: int, path: Path) -> int:
        """Over the value that starts at `at` (after spaces), its keys and items noted: the position after it."""
        at = skip(at)
        opened = text[at]
        if opened in "{[":
            closing, index = "}]"["{[".index(opened)], 0
            at = skip(at + 1)
            if text[at] == closing:
                return at + 1
            while True:
                at = skip(at)
                if opened == "{":
                    key = _JSON_STRING.match(text, at)
                    here: Path = (*path, json.loads(key.group()))   # type: ignore[union-attr]  # None: the AttributeError ends the locator
                    found[here] = bisect_right(starts, at)          # a key written twice: the later one, as json keeps it
                    at = skip(key.end())                            # type: ignore[union-attr]
                    if text[at] != ":":
                        raise ValueError("no colon after a key")
                    at += 1
                else:
                    here = (*path, index)
                    found[here] = bisect_right(starts, at)
                    index += 1
                at = skip(value(at, here))
                if text[at] == closing:
                    return at + 1
                if text[at] != ",":
                    raise ValueError("no comma between items")
                at += 1
        scalar = (_JSON_STRING if opened == '"' else _JSON_SCALAR).match(text, at)
        return scalar.end()                                         # type: ignore[union-attr]

    value(0, ())
    return found


# ── TOML ────────────────────────────────────────────────────────────────────

def _toml(text: str) -> dict[Path, int]:
    text = text.replace("\r\n", "\n")                               # as tomllib reads it
    starts = _lines(text)
    found: dict[Path, int] = {}
    table: Path = ()                                                # where the keys of the lines go: the last header
    last: dict[Path, int] = {}                                      # an array of tables -> the index of its last item

    def real(keys: Path) -> Path:
        """The keys of a header as a path of the data: `[servers.tls]` is in the last item of `[[servers]]`."""
        path: Path = ()
        for key in keys:
            path = (*path, key)
            if path in last:
                path = (*path, last[path])
        return path

    def note(path: Path, line: int, base: int) -> None:
        for end in range(base + 1, len(path)):                      # a.b.c = 1: a and a.b are where they are first named
            found.setdefault(path[:end], line)
        found[path] = line

    at, end = 0, len(text)
    while at < end:
        char = text[at]
        if char in " \t\n":
            at += 1
        elif char == "#":
            at = _line_end(text, at)
        elif char == "[":
            line = bisect_right(starts, at)
            array = text.startswith("[[", at)
            start = at + (2 if array else 1)
            close = _toml_key_end(text, start, "]")
            keys = _toml_keys(text[start:close])
            if array:
                above = (*real(keys[:-1]), keys[-1])
                last[above] = last.get(above, -1) + 1
                found.setdefault(above, line)
                table = (*above, last[above])
                found[table] = line
            else:
                table = real(keys)
                note(table, line, 0)
            at = _line_end(text, close)
        else:
            line = bisect_right(starts, at)
            equals = _toml_key_end(text, at, "=")
            note((*table, *_toml_keys(text[at:equals])), line, len(table))
            at = _toml_value_end(text, equals + 1)
    return found


def _line_end(text: str, at: int) -> int:
    end = text.find("\n", at)
    return len(text) if end < 0 else end


def _toml_key_end(text: str, at: int, stop: str) -> int:
    """The position of `stop` after the key that starts at `at`: outside its quotes, on its line."""
    while True:
        char = text[at]                                             # past the end: the IndexError ends the locator
        if char == stop:
            return at
        if char == "\n":
            raise ValueError("a key that doesn't end on its line")
        if char == '"':
            at += 1
            while text[at] != '"':
                at += 2 if text[at] == "\\" else 1
        elif char == "'":
            at = text.index("'", at + 1)
        at += 1


def _toml_keys(source: str) -> Path:
    """The parts of a key as tomllib reads them: `a."b.c".d` is `("a", "b.c", "d")`."""
    data: Any = tomllib.loads(source + " = 0")
    keys: Path = ()
    while isinstance(data, dict):
        (key, data), = data.items()
        keys = (*keys, key)
    return keys


def _toml_value_end(text: str, at: int) -> int:
    """The position after the value that starts at `at`: the end of its line, or of its last line where a string or an array goes over several."""
    depth, end = 0, len(text)
    while at < end:
        char = text[at]
        if text.startswith(('"""', "'''"), at):
            quotes = text[at:at + 3]
            at += 3
            while not text.startswith(quotes, at):
                at += 2 if text[at] == "\\" and char == '"' else 1
            at += 3
            while text.startswith(char, at):                        # one or two quotes may end the text itself: """a""""
                at += 1
        elif char == '"':
            at += 1
            while text[at] != '"':
                at += 2 if text[at] == "\\" else 1
            at += 1
        elif char == "'":
            at = text.index("'", at + 1) + 1
        elif char == "#":
            at = _line_end(text, at)
        elif char == "\n":
            if depth == 0:
                return at
            at += 1
        else:
            depth += (char in "[{") - (char in "]}")
            at += 1
    return end


# ── ini ─────────────────────────────────────────────────────────────────────

_INI_SECTION = re.compile(r"\[(.+)\]")                              # configparser's: the header up to the last ]
_INI_KEY_END = re.compile(r"[=:]")


def _ini(text: str) -> dict[Path, int]:
    found: dict[Path, int] = {}
    section: Path | None = None
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if raw[0] in " \t":                                         # more of the value above (or an indented key: it takes its section's line)
            continue
        header = _INI_SECTION.match(line)
        if header is not None:
            section = () if header.group(1) == "DEFAULT" else tuple(header.group(1).split("."))
            for end in range(1, len(section) + 1):
                found.setdefault(section[:end], number)
        elif section is not None:
            found[(*section, _INI_KEY_END.split(line, maxsplit = 1)[0].strip())] = number
    return found


# ── YAML ────────────────────────────────────────────────────────────────────

def _yaml(text: str) -> dict[Path, int]:
    from ._readers import yaml_loader

    yaml, loader_class = yaml_loader()
    found: dict[Path, int] = {}
    loader = loader_class(text)

    def walk(node: Any, path: Path, above: frozenset[int]) -> None:
        if id(node) in above:                                       # an alias of a node it is inside of
            return
        if isinstance(node, yaml.MappingNode):
            loader.flatten_mapping(node)                            # a merge (<<): its keys are where the anchor wrote them
            for key_node, value_node in node.value:
                here = (*path, loader.construct_object(key_node, deep = True))
                found[here] = key_node.start_mark.line + 1
                walk(value_node, here, above | {id(node)})
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                found[(*path, index)] = item.start_mark.line + 1
                walk(item, (*path, index), above | {id(node)})

    try:
        root = loader.get_single_node()
        if root is not None:
            walk(root, (), frozenset())
    finally:
        loader.dispose()
    return found


_LOCATORS = {"json": _json, "toml": _toml, "ini": _ini, "yaml": _yaml}
