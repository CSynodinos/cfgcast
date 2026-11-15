# cfgcast

Typed configuration files for Python 3.13+. Write your configuration as a class, and `cfgcast`
loads JSON, TOML, ini/cfg or YAML into it: every value checked and converted, every problem
reported at once with the file and key it came from.

Built on [enforced](../../README.md). MIT licensed.

```python
from dataclasses import field
from enforced.data_types import Port, SecretStr
from cfgcast import cfg, load_cfg

@cfg
class Database:
    host: str
    port: Port = 5432
    password: SecretStr

@cfg
class App:
    name: str
    db: Database                     # a section: another @cfg class
    debug: bool = False
    hosts: list[str] = field(default_factory = list)

app = load_cfg("app.toml", App)
```

```toml
# app.toml
name = "shop"

[db]
host = "db.internal"
port = "5432"                        # text is fine: it is converted
password = "s3cret"
```

```python
>>> app
App(name='shop', debug=True, hosts=[], db=Database(host='db.internal', port=Port(5432), password=SecretStr('**********')))
>>> app.db.password.get_secret_value()
's3cret'
```

## Why

- **A configuration is a plain dataclass.** Frozen and keyword-only, with `==`, `repr`, pickling,
  `dataclasses.replace` and everything else a dataclass has. No base class, no model to learn.
- **Safe by default.** Unknown keys are refused (a typo like `debgu = true` never silently falls back
  to the default), loaded configurations can't be changed, and a secret never shows in a message.
- **Every problem at once**, each with its file and key path.
- **The formats are done properly**: YAML's `no` stays the string `"no"`, ini gets nesting and lists,
  and what is written is quoted so that any reader gets the same values back.
- **Writing, with checked overrides**: make variants of a configuration for a workflow without
  touching the original.
- **No dependency** besides enforced (PyYAML only if you read YAML).

## Install

```bash
pip install cfgcast            # JSON, TOML, ini/cfg
pip install "cfgcast[yaml]"    # and YAML (PyYAML)
```

## Formats

| format | suffixes | notes |
|---|---|---|
| JSON | `.json` | the top level must be an object |
| TOML | `.toml` | |
| ini | `.ini`, `.cfg` | see [ini](#ini) |
| YAML | `.yaml`, `.yml` | read with YAML 1.2 booleans, see [YAML](#yaml) |

The format comes from the suffix (in any case: `APP.JSON` is JSON). For any other name, say it:

```python
app = load_cfg("settings.conf", App, "toml")       # or cfg_type = "toml"
```

## Writing a schema

`@cfg` makes a class a schema. Each annotated attribute is a key, its type says what the value must
be, and a default makes the key optional.

```python
@cfg                                   # strict = True, frozen = True
class Server:
    host: str = "0.0.0.0"
    port: Port = 8080
    workers: int = 4
    tags: list[str] = field(default_factory = list)   # a mutable default needs default_factory
    tls: Tls | None = None                            # an optional section

@cfg(strict = False, frozen = False)
class Lenient:
    ...
```

| option | default | |
|---|---|---|
| `strict` | `True` | refuse keys the schema doesn't name. `False` ignores them, here and in every section below, with a warning: an older program reading a newer file |
| `frozen` | `True` | a loaded configuration can't be changed (`FrozenInstanceError`) |

A section may be written after the class that names it. On Python 3.13 quote its name there
(`db: "Database"`): 3.13 evaluates an annotation where it stands, so without quotes it is Python's own
`NameError`. From 3.14 either form works.

Always on, not options:

- **Values are converted** as enforced's `coerce` does: `"8080"` is `8080` for an `int`, `"true"` and
  `"yes"` are `True` for a `bool`, `"2026-10-17"` is a `date`, and a `date` is midnight for a `datetime`.
  An `int` is never turned into a `str` (`name = 1` is reported: you probably meant `"1"`).
- **Items are checked**: every item of a `list[Port]`, every value of a `dict[str, float]`.

Any type enforced checks works in a schema: `Literal["DEBUG", "INFO"]`, `Annotated[int, lambda v: v > 0]`,
enums, `Path`, `Decimal`, `datetime`, and enforced's validated types (`Port`, `Url`, `Email`,
`SecretStr`, `PhoneNumber`, `Probability` ...).

A field whose type is another `@cfg` class is a **section** (a table in TOML, a nested object in JSON
and YAML, a `[section]` in ini). Sections may be written in any order in the module.

**Not inheritable.** A configuration is a class of its own: `class Prod(App)` is refused. Use a
section, or a second schema.

## Loading

```python
app = load_cfg("app.toml", App)           # the way to load
app = App.load("app.toml")                # the same, as a member
app = App.from_dict({"name": "shop", "db": {...}})   # from a dictionary you already have
```

> **`App.load` and `App.from_dict` are added by `@cfg` when the program runs, so editors and type
> checkers (mypy, pyright) don't know them:** they flag `App.load(...)` as an unknown attribute and
> can't tell you what it returns. This is expected: a checker can't see members a decorator adds.
> The members work as written. Use `load_cfg(file, App)` where you want your editor's help: it is
> fully typed and returns an `App`.

## When something is wrong

`load_cfg` raises a `ConfigError` with every problem in the file:

```toml
# bad.toml
name = "shop"
debgu = true

[db]
host = "db.internal"
port = 0
password = ["oops"]
```

```text
ConfigError: 3 problems in bad.toml for App:
    debgu: key not in the schema
    db.port: invalid 'Port': must be between 1 and 65535
    db.password: expected type: 'enforced.data_types.SecretStr', got type 'list'
```

The same as data, in pydantic's shape, for a log or an API response:

```python
>>> error.errors(include_context = False)
[{'type': 'unknown', 'loc': ['bad.toml', 'debgu'], 'msg': 'key not in the schema'},
 {'type': 'invalid', 'loc': ['bad.toml', 'db', 'port'], 'msg': "invalid 'Port': must be between 1 and 65535", 'input': 0},
 {'type': 'type', 'loc': ['bad.toml', 'db', 'password'], 'msg': "expected type: 'enforced.data_types.SecretStr', got type 'list'", 'input': '**********'}]
>>> error.json(include_input = False)        # leave the values out
```

- `loc` starts with the file, then the key path (`["app.toml", "servers", 2, "port"]` for an item).
- The input of a `SecretStr` field is never shown: `**********` in `errors()`, nothing in the message.
  An unknown key has no `input` at all: nothing says its value isn't a secret.
- `type` is one of `unknown`, `missing`, `section` (a section that isn't a table), and enforced's own
  (`type`, `invalid`, `length` ...).
- `ConfigError` is an `enforced.InvalidDataError` and a `ValueError`: catch whichever you already catch.
- **A configuration built or changed in code raises a `ConfigError` too**: `App(name = 1)`, or
  `app.name = 1` on a `@cfg(frozen = False)` class. There is no file then: `error.file` is `None`, and `loc`
  starts with the field (`["name"]`). Secrets are masked the same way.

### Unknown keys under `strict = False`

`strict = False` holds for the class it is written on and every section below it, also one with a plain
`@cfg`. A section can loosen itself under a strict class, but can't tighten what is above it. The ignored
keys are named in one `ConfigWarning`, so that a typo doesn't quietly leave you on a default:

```text
ConfigWarning: 2 unknown keys ignored in new.toml for App:
    -> monitoring.random_flag
    -> db.prot
```

```python
app = load_cfg("new.toml", App, show_warnings = False)      # also App.load and App.from_dict
```

- It names keys and never values. `warning.errors()` has them in the shape above, without `input`.
- It points at the line that called `load_cfg`, and comes before a `ConfigError` for the same file.
- To make it an error in CI: `pytest -W error::cfgcast.ConfigWarning`, or `warnings.filterwarnings("error", category = ConfigWarning)`
  in your code. `python -W` can't name it: Python reads that option before it can import an installed package.

A file that isn't valid in its format raises what its parser raises (`json.JSONDecodeError`,
`tomllib.TOMLDecodeError`, `configparser.Error`, `yaml.YAMLError`), and a missing file `FileNotFoundError`.

## Writing

```python
from cfgcast import write, to_dict

write(app, "prod.yaml", {"db": {"host": "db.prod"}, "debug": False})
```

```yaml
# prod.yaml
name: shop
debug: false
hosts: []
db:
  host: db.prod
  port: 5432
  password: s3cret
```

- **Overrides go to the file only.** They are checked against the schema like a loaded file (wrong
  values and unknown keys raise a `ConfigError`, and nothing is written), and `app` is never changed.
  Under `strict = False` an unknown key is left out of the file and always gives a `ConfigWarning`.
  Nested keys are nested dictionaries, merged section by section.
- **Any object, any format**: load YAML, write ini. The format comes from the suffix, or `cfg_type`.
- **Every key is written**, also one equal to its default: the file means the same if a default in
  the code changes later, so a run can be repeated from its file.
- **A `SecretStr` is written as its real text** (a configuration file is where secrets go).
- **Comments and layout** of a file it was loaded from are not kept: `write` writes values, it doesn't
  edit a file.
- What a format can't hold is refused before the file is opened (see [ini](#ini)); a file is written
  whole or not at all.

`to_dict(app)` gives the values as a dictionary, sections as nested dictionaries. Secrets stay
`SecretStr` (masked when printed); lists and dictionaries are copies.

## Format details

### ini

ini has one level of sections and only text, so cfgcast adds three rules:

```ini
[DEFAULT]                       ; the top-level keys
name = shop
hosts = a.example.com, b.example.com    ; a list: split on commas where the field is a list[...]

[db]
host = db.internal

[db.pool]                       ; a dotted section is nesting: "pool" inside "db"
size = 10
```

- **`[DEFAULT]` holds the top-level keys.** ini allows no key outside a section. Unlike Python's
  `configparser`, its keys are *not* copied into the other sections.
- **Lists** are comma-separated, only where the field is a `list[...]` (elsewhere a comma is part of the
  text). `hosts =` is an empty list. An item can't hold a comma.
- **Every value is text**: `true`, `5432`, `2.5` are converted like any value.
- `%` is a `%` (no interpolation). Keys keep their case.

ini can't hold a line break in a value, spaces around a value, or a list of sections: writing one is
an error, so pick another format for such data.

### YAML

Read with **YAML 1.2 booleans**: only `true` / `false` are booleans. In YAML 1.1, which PyYAML follows,
`no`, `yes`, `on` and `off` are booleans too, so `country: no` (Norway) became `False`. Here it stays
`"no"`, and a `bool` field still takes `yes` and `on` through conversion. When writing, any string a
YAML 1.1 or 1.2 reader would take for something else (`no`, `08`, `1e3`, `null`, `~` ...) is quoted.

### TOML

TOML has no null: a `None` is left out when writing, and loading the file gives the field its default.

## Not yet

Planned, in this order (see `PLAN.md`): lists of sections (`servers: list[Server]`), dates in the
writers, several files in layers (`load_cfg(["base.toml", "prod.toml"], App)`), and environment
variables (`env_prefix = "APP_"`).
