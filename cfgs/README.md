# Example configurations

Two per format: `simple.*` (8 fields) and `complex.*` (nested, three levels). Every `simple.*` holds the
same data, and so does every `complex.*`, so loading any of them gives an equal object.

| format | files |
|---|---|
| JSON | `simple.json`, `complex.json` |
| TOML | `simple.toml`, `complex.toml` |
| YAML | `simple.yaml`, `complex.yaml` |
| ini | `simple.ini`, `complex.ini` |
| cfg | `simple.cfg`, `complex.cfg` (the ini format; `:` delimiters and `#` comments) |

Since 2026-11-15, one file per rule built then (the same data in every format of a set):

| files | what they hold |
|---|---|
| `fleet.json` / `.toml` / `.yaml` | a list of sections, each with a section of its own (`servers: list[Server]`, `[[servers]]` and `[servers.tls]` in TOML). No ini: it can't hold one |
| `schedule.*` (all five) | a `date`, a `time` and a `datetime` (TOML's own literals, YAML's bare date, text elsewhere), and a dictionary of sections (`[regions.eu]`) |
| `layers/base.toml`, `prod.yaml`, `local.ini` | three layers of one configuration, loaded in that order: a section merged, a value and a list replaced |

## Format traps in these files

- **YAML**
  - `fallback_locale: no` is `False` in YAML 1.1 (PyYAML's default). The reader must use YAML 1.2 booleans
    (only `true` / `false`) so it stays the string `"no"`.
  - `version: "1.0"` is quoted: bare, it is the float `1.0`.
  - `support_phone: "+4915112345678"` is quoted: bare, it is read as a number.
  - `format: "{asctime} ..."` is quoted: a bare `{` starts a mapping.
- **TOML**: top-level keys must come before the first `[table]`; strings are always quoted
  (`fallback_locale = "no"`).
- **ini / cfg**
  - Top-level keys live in `[DEFAULT]` (ini has no key outside a section), and are not copied into the
    other sections.
  - A dotted section is nesting: `[db.pool]` is `pool` inside `db`.
  - A list is `a, b, c`, split on commas only where the schema field is a `list[...]`.
  - Every value is text: `true`, `5432` and `2.5` are converted by coercion.
  - `format = {asctime} {levelname} {message}` has no `%`: `configparser` would read it as interpolation
    (the reader must switch interpolation off).
- **JSON**: no comments, no trailing commas, and no `NaN` / `Infinity`.

## Left out of these examples, for later

Left out so that all formats can hold the same data. Each needs its own example when its rule is built:

- **`null` / `None`**: JSON and YAML have it, TOML and ini don't (the writer leaves the key out; loading gives
  the field its code default).
- **A `%` in a value**: `configparser` interpolation.
- **Multi-line strings, anchors and aliases (`&` / `*`) in YAML**, `[DEFAULT]` keys that clash with a section key.
- **A key the schema doesn't name** (`strict` on and off), a **missing key**, a **wrong type**: the invalid
  files are made on the fly by `tests/simulation.py`, not kept here.
