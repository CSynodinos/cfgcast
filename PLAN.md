# cfgcast: plan

**Renamed on 2026-11-01** (the owner's choice): the project was `configured` until then.

**Python 3.13 on 2026-11-03**, with enforced: `_annotated_names` is the only line that differs by version.

## Decisions

- **Formats:** JSON, TOML, ini (`.ini` and `.cfg`, one format), YAML. No XML. No env for now (use cases to be thought through).
- **`load_cfg(file, schema, cfg_type = "infer")`**: `schema` is a `@cfg` class; it returns an instance of it
  (overloads `type[T] -> T`). `cfg_type` is a `Literal`; `"infer"` reads the suffix, and an unknown or missing
  suffix is an error that says to pass `cfg_type`.
- **`@cfg` is a decorator, not a base class** (the owner's decision, 2026-10-17: a configuration is a plain
  enforced dataclass, and is not meant to be inherited). `@dataclass_transform` on `cfg` for checkers; `cfg`
  calls `enforced.dataclass(cls, kw_only = True, coerce = True, deep = True, ...)` (in place: no slots).
  A field typed as another `@cfg` class is a section / table. A class with a `@cfg` class among its bases is
  refused (`TypeError`). `Config` stays as the internal class that holds `load`, `from_dict`, `to_dict` and
  `write`; `cfg` copies them onto the class, which does not inherit from it. Public: `cfg`, `load_cfg`,
  `ConfigError`.
- **Options of the decorator:** `strict` (default `True`: unknown keys refused; `False` ignores them, for an
  older program reading a newer file) and `frozen` (default `True`): `@cfg` and `@cfg(strict = False)`.
  **Changed on 2026-11-01** (the owner's decision): `strict = False` reaches every section below the class,
  whatever those say, and what it ignores is named in one `ConfigWarning` (`show_warnings = True` on `load_cfg`,
  `load`, `from_dict`; `write`'s overrides always warn). It was per class, and a plain `@cfg` section refused keys
  under a lenient schema. An unknown key's value is in no problem any more, an error's or a warning's.
- **`coerce` and `deep` are always on**, not options (to be said in the README).
- **Defaults live in the code**, in the schema, as in a dataclass. A value in the file always wins; the code
  default is used only where the file leaves the key out. The same rule in every format; no warning.
- **Errors:** one `ConfigError` with every problem (unknown, missing, wrong value), `errors()` / `json()` in
  enforced's shape, `loc` = the file, then the key path. The input of a `SecretStr` field is never shown.

## Format rules

JSON, TOML and YAML nest by themselves: each gives the builder the same nested dictionary. ini is flat,
so it has three rules:

- **`[DEFAULT]` holds the top-level keys** of the schema (ini allows no key outside a section). It is kept
  because it is part of the ini format, but `configparser`'s copying of its keys into every section is
  switched off: a key in `[DEFAULT]` is an ordinary value, like any other. (README: one line, since
  `configparser` users expect the copying.)
- **A dotted section is nesting**, as in TOML: `[db.replica]` is the `replica` section inside `db`.
  A field name can't hold a dot, so no real field is ever shadowed.
- **Lists:** `hosts = a, b, c` is split on commas where the schema's field is a `list[...]`; elsewhere
  a comma is part of the value. Keys are kept as written (`optionxform = str`).

YAML:

- **Read with YAML 1.2 booleans:** a `SafeLoader` subclass whose bool resolver matches only `true` / `false`
  (any case). `country: no` stays the string `"no"`; a `bool` field still takes `yes` / `on` through
  enforced's coercion. (README: one line.)
- `yaml.safe_load` only; PyYAML is imported inside `read_yaml`, an optional extra (`cfgcast[yaml]`).
- A top level that isn't a mapping is an error (in JSON too).

## Steps

1. `cfg`: refuse a configuration among the bases, options, `enforced.dataclass`, the list of sections, copy
   the members of `Config` onto the class.
2. The readers: `read_json`, `read_toml`, `read_ini` (the three rules), `read_yaml` (the 1.2 loader).
3. `_build`: a dictionary to the schema, recursing into sections; unknown and missing keys; enforced's
   errors with the key path in front; everything gathered before raising.
4. `ConfigError`, `load_cfg`, `infer_type`; the `load` / `from_dict` / `to_dict` / `write` members.
5. Tests: one config in all four formats loads to equal objects; strict on and off; a missing nested key;
   a bad `Port`; a `SecretStr` masked in the error; `country: no` in YAML; `[DEFAULT]` and dotted sections
   in ini; an unknown suffix.

## Methods

`load`, `from_dict`, `to_dict` and `write(file, overrides)` (the owner's addition) are all kept, as members the
decorator adds to the class.

**Open: static typing of those members.** A checker reads `@dataclass_transform` for the fields, but doesn't
see methods a decorator adds at run time: `app.write(...)` and `App.load(...)` would be errors in an editor.
Ways out: module functions that are typed (`load_cfg` already; `write_cfg(obj, file, ...)`, `cfg_dict(obj)`),
a `Protocol` the user may inherit for the editor's sake (against "not inheritable"), or a `.pyi` plugin
(too heavy). The functions are the cheapest and fit the decorator.

`write(file, overrides = None, *, cfg_type = "infer")`: what it is for is making variants of a
config in a workflow (one per run, per environment) without touching the original.

- **Our own writers, all four formats** (every format is a dictionary): JSON through `json.dumps`, and
  writers of our own for TOML (`tomllib` only reads), ini and YAML (no PyYAML needed to write).
- **The format**: `cfg_type`, `"infer"` by the suffix, as `load`.
- **Overrides apply to the file only**: checked against the schema (unknown keys as `strict` says, nested
  keys as nested dictionaries), never written into the object. One way: the object as a dictionary,
  the overrides merged into it, and that built with `from_dict`: the result is checked, then written,
  then dropped.
- **`SecretStr`** is written as its real text (`get_secret_value()`), not the mask: `str()` would write
  `**********`.
- **Comments and layout of a loaded file are lost**: `write` writes values, it doesn't edit a file.
- ini: top-level keys go to `[DEFAULT]`, nested sections as dotted ones, lists comma-joined.
- **Every key is written**, also one equal to its code default: simpler, and the file means the same if
  a default in the code changes later (a run can be repeated from its file).
- YAML: a string YAML 1.1 would read as something else (`no`, `on`, `08`, `1e3`, `null`, `~`) is
  written quoted, so other YAML readers in a workflow get the string too.

## Next features (planned 2026-10-18; built in this order)

What users will ask for first. 1 and 2 stand alone; 4 is built on 3.

### 1. Lists and dicts of sections

```python
@cfg
class App:
    servers: list[Server]              # TOML [[servers]]; a list of objects in JSON / YAML
    by_region: dict[str, Server]       # {"eu": {...}, "us": {...}}
```

- `build`: a field typed `list[X]`, `tuple[X, ...]` or `dict[str, X]`, `X` a configuration, is built item by
  item; a problem's `loc` has the index or key (`servers[2].port`).
- `__cfg_sections__` records these too, marked as list / dict.
- ini can't hold a list of sections: loading or writing such a field as ini is a clear error naming the field,
  and `read_ini` doesn't split it on commas.
- The writers already write lists of tables (`[[servers]]`); `to_dict` already turns configurations in lists
  into dictionaries.

### 2. Dates and times

- enforced first (the owner's decision): `coerce` already parses ISO 8601 text into `datetime` / `date` /
  `time`. Missing for configs: a `date` value (YAML's unquoted `2026-10-17`) for a `datetime` field, added to
  enforced's coercion on 2026-10-18 (midnight, no timezone: what the text `"2026-10-17"` gives).
- cfgcast, then: the writers write dates. TOML as native literals, YAML unquoted (read back as dates),
  JSON and ini as ISO text. `_plain` lets `date` / `datetime` / `time` through to the writers.
- Not now: durations (`timedelta`, "30s", "5m": not ISO; a `Duration` model later), Unix timestamps.
- Open (enforced): a `date` field accepts a `datetime` (it is a subclass), and keeps it as one.

### 3. Layering: several files, the later ones win

```python
app = load_cfg(["base.toml", "prod.toml"], App)
```

- Sections merge key by key; a later file **replaces** a value, a list included (never appended: an append
  can't be undone by a later layer). Each file in its own format.
- `strict` and missing keys are checked once, on the merged result.
- Errors name the file a value came from: the merge notes, per key path, the file that gave it, and `build`
  takes that (`build(schema, data, source_of)` in place of one file). A missing key names the last file, an
  unknown key the file that has it. This is the one change to existing code.
- Open: a layer that may be absent (a local override file). `optional("local.toml")` (a wrapper on that one
  path) or `missing_ok = True` (for every layer of the call).

### 4. Environment variables

```python
app = load_cfg("app.toml", App, env_prefix = "APP_")      # APP_DB__PORT=9090 -> db.port = 9090
```

- On `load_cfg` first (the owner's decision); a `@cfg(env_prefix = ...)` option may come later.
- The prefix taken off, `__` between levels, names matched to fields without regard to case (pydantic-settings'
  convention). Values are text: coercion does the rest, as for ini. A list field takes `a,b,c` (ini's rule) or
  a JSON array when the value starts with `[`.
- The last layer: over every file (built on 3, which gives it the merge and the sources). An error names the
  variable: `APP_DB__PORT: invalid 'Port': ...`.
- Unknown variables are ignored, also when `strict = True` (the owner's decision): the environment holds what
  other tools set too.
- `env: Mapping[str, str] = os.environ` as a parameter, so a test or a workflow hands its own.
- Not now: `.env` files (one more layer read into a dictionary, when wanted).

### Decided

- Dates: enforced first. Lists in layers: replaced. The env prefix: on `load_cfg` first.
  Unknown env variables with `strict`: ignored.

### 5. `ConfigError` raised by the class itself (2026-10-22; built the same day)

Planned with enforced's `error =` option, in `/mnt/d/dev/enforced/PLAN_error_type.md` (part 2): `@cfg` passes
`error = ConfigError` to `enforced.dataclass`, so a direct construction or assignment raises a `ConfigError`
too, and `ConfigError` works when enforced makes it without `__init__` (class-attribute defaults for `file`,
`schema` and `entries`). Do it after enforced's part is built. Includes masking a secret's input in
`ConfigError.errors()` for both doors.
