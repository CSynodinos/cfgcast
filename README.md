# cfgcast

Typed configuration files for Python 3.13+. Write your configuration as a class, and `cfgcast`
loads JSON, TOML, ini/cfg or YAML into it: every value checked and converted, every problem
reported at once with the file and key it came from.

Built on [enforced](https://github.com/CSynodinos/enforced) ([PyPI](https://pypi.org/project/enforced/)). MIT licensed.

```python
from enforced.data_types import Port, SecretStr
from cfgcast import cfg, field, load_cfg

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
- **Every problem at once**, each with its file, line and key path (`app.toml:12: db.port: ...`).
- **The formats are done properly**: YAML's `no` stays the string `"no"`, ini gets nesting and lists,
  and what is written is quoted so that any reader gets the same values back.
- **Writing, with checked overrides**: make variants of a configuration for a workflow without
  touching the original.
- **Layers and the environment**: `load_cfg(["base.toml", "prod.toml"], App, env_prefix = "APP_")`, a `.env`
  file, secrets from files, overrides from the command line: every problem still names the file or
  variable its value came from, and `sources_of(app)` says it for every value.
- **A template of the schema**: `write_template(App, "app.toml")`, every key with its default, type and doc.
- **No dependency** besides enforced and clitint (colours); PyYAML only if you read YAML.

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
| `env_prefix` | `None` | read environment variables with this prefix whenever the class is loaded: see [Environment variables](#environment-variables) |
| `keys` | `None` | `"kebab-case"`: a field `max_size` is the key `max-size` in a file: see [Keys that aren't Python names](#keys-that-arent-python-names) |

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

### Lists and dictionaries of sections

```python
@cfg
class App:
    servers: list[Server]              # TOML [[servers]]; a list of objects in JSON and YAML
    by_region: dict[str, Server]       # [by_region.eu] in TOML and ini; {"eu": {...}} in JSON and YAML
```

`list[X]`, `tuple[X, ...]` and `dict[str, X]`, `X` a `@cfg` class, are built item by item, each one
checked like any section (unknown and missing keys too). A problem says which item: `servers[2].port`,
`by_region.eu.host`. ini holds a dictionary of sections (`[by_region.eu]`) but not a list of them: see [ini](#ini).

**Not inheritable.** A configuration is a class of its own: `class Prod(App)` is refused. Use a
section, or a second schema.

### `field`: a key and a doc

`cfgcast.field` is `dataclasses.field` (`default`, `default_factory`, `init` ...) with two more options.
`dataclasses.field` keeps working: this one is needed only for them.

```python
from cfgcast import cfg, field

@cfg
class Pool:
    max_size: int = field(default = 10, key = "max-size", doc = "connections at most")
    class_: str = field(default = "small", key = "class")
```

| option | |
|---|---|
| `key` | the key of the field in a file, where it isn't the field's name |
| `doc` | a line about the field, written above its key by [`write_template`](#a-template) |

### Keys that aren't Python names

`max-size`, `log-level`, `class`: common in TOML and YAML, and no field can have that name. Say the key
per field (`field(key = "max-size")`), or for a whole class:

```python
@cfg(keys = "kebab-case")              # max_size is the key max-size, class_ is class
class Pool:
    max_size: int = 10
    class_: str = "small"
```

- **The key is what a file has**, and everything about a file uses it: unknown and missing keys, `loc` and
  the message, `write`, `to_dict` and `from_dict` (so `from_dict(App, to_dict(app)) == app`). The attribute
  keeps its Python name: `pool.max_size`.
- The field's name is no key any more: `max_size = 10` in a file is an unknown key.
- `keys = "kebab-case"` holds for its class only: a section says its own. A field's `key` wins over it.
- An environment variable can't hold a `-`: `APP_POOL__MAX_SIZE` is `pool.max-size`.
- Two fields with one key are refused at the decorator (`TypeError`).

## Loading

```python
app = load_cfg("app.toml", App)           # the way to load
app = App.load("app.toml")                # the same, as a member
app = App.from_dict({"name": "shop", "db": {...}})   # from a dictionary you already have
```

`App.load` and `App.from_dict` are added by `@cfg` when the program runs, and a type checker (mypy,
pyright, an editor) can't see members a decorator adds: it flags `App.load(...)` as an unknown attribute.
They work as written. Two ways to have the checker's help:

```python
from cfgcast import Loadable, cfg, from_dict, load_cfg

app = load_cfg("app.toml", App)           # the functions are fully typed: an App
app = from_dict(App, {"name": "shop"})

@cfg
class App(Loadable):                      # or name Loadable as the base: App.load and App.from_dict are seen
    name: str
```

`Loadable` is for checkers only: it is no configuration (a `@cfg` class still can't derive from another),
and a class is one only with `@cfg`.

### Several files, in layers

```python
app = load_cfg(["base.toml", "prod.yaml", "local.ini"], App)      # also App.load([...])
```

- **The later file wins.** Sections merge key by key; any other value is replaced, **a list included**
  (never appended to: an append could not be undone by a later layer). Each file in its own format.
- **Checked once, on the result**: a key is missing only if no layer has it, and a layer need not be a
  whole configuration.
- **A problem names the file its value came from**: an unknown key the file that has it, a missing key
  the last file (see [below](#when-something-is-wrong)).
- Every file must exist: a missing one is a `FileNotFoundError`. **Unless it is marked**:

```python
from cfgcast import load_cfg, optional

app = load_cfg(["base.toml", "prod.toml", optional("local.toml")], App)
```

`optional` is about being there and nothing else: a `local.toml` that is there is read like any file, and
a problem in it is a problem. A directory or a broken link in its place is an error. There is no
`missing_ok` for the whole list: it would skip a missing `base.toml` too, and the program would run on
defaults without anyone knowing.

### Environment variables

```python
app = load_cfg("app.toml", App, env_prefix = "APP_")      # APP_DB__PORT=9090 is db.port
app = load_cfg([], App, env_prefix = "APP_")              # the environment alone
```

- The prefix is taken off, `__` goes between levels, and names match fields without regard to case:
  `APP_DB__PORT`, `app_db__port` and `APP_Db__Port` are all `db.port`.
- **Over every file**: a variable is the last layer.
- **A variable that names no field is ignored, also under `strict`** and without a warning: the
  environment holds what other tools set too (`APP_HOME`).
- Values are text, converted like any value. A list field takes `a, b, c` (split on commas, as in ini) or
  a JSON array when the text starts with `[`; a dictionary field takes a JSON object (`{"eu": 1}`). Text
  that starts as JSON and isn't is a `ValueError` naming the variable (never its value).
- A variable reaches into a section (`APP_DB__PORT`), not into an item of a list or dictionary of sections.
- A problem names the variable: `APP_DB__PORT: db.port: ...`.
- `env = {...}` hands the variables in place of `os.environ` (a test, a workflow); `env = {}` reads none.

**The prefix on the class**: `@cfg(env_prefix = "APP_")` reads the variables whenever that class is loaded
(`load_cfg`, `App.load`), without saying so at each call. A prefix given to the call wins. Only the class
that is loaded counts, not a section's, and `from_dict` reads no variables.

**A `.env` file**:

```python
app = load_cfg("app.toml", App, env_prefix = "APP_", env_file = ".env")     # or optional(".env")
```

- Read with the prefix like the environment, **over the files and under the environment**: a variable of
  the process wins over the file's. `os.environ` is never changed.
- `KEY=VALUE` per line; blank lines and `#` lines are ignored, `export KEY=VALUE` is allowed. A value is
  bare (` #` starts a comment), `'single quoted'` (as written) or `"double quoted"` (`\n`, `\t`, `\"`, `\\`;
  it may run over several lines). No interpolation: `${HOME}` is that text.
- A problem names the file and the line: `.env:12: db.port: ...`. A line that is no `KEY=VALUE` is a
  `ValueError` with the file and the line number, never the line's text.

**A secret from a file** (docker, compose and kubernetes hand one over as `/run/secrets/...`):

```bash
APP_DB__PASSWORD_FILE=/run/secrets/db_password
```

- A variable that names no field, but names one with `_FILE` taken off, gives that field the text of the
  file (UTF-8, one line break at its end taken off). Any field, not only a `SecretStr`.
- A variable that names a field as it is stays that field (`APP_TLS__CERT_FILE` is `tls.cert_file` where
  the schema has one).
- `APP_DB__PASSWORD` and `APP_DB__PASSWORD_FILE` both set is a `ValueError` that names both; a file that
  isn't there a `FileNotFoundError` that names the variable. The text of the file is never in a message.

### A section of a bigger file

```python
app = load_cfg("pyproject.toml", App, section = "tool.myapp")     # the table [tool.myapp]
```

- Dots between levels, or a list of keys where a key has a dot (`["tool", "my.app"]`). The rest of the file
  is not looked at: it belongs to others.
- It holds for every file of a list, not for environment variables (they name the schema's own keys).
- A problem keeps the whole path, so it is found in the file: `pyproject.toml:14: tool.myapp.db.port: ...`.
- A file without the section is a problem (`tool.myapp: missing section`), as a missing file is an error.

### Overrides for one run

```python
import sys
from cfgcast import load_cfg, parse_overrides

app = load_cfg("app.toml", App, overrides = {"db": {"port": 9090}})                # from code
app = load_cfg("app.toml", App, overrides = parse_overrides(sys.argv[1:], App))   # python run.py db.port=9090 debug=true
```

- **Over everything**: the files, then the `.env` file, then the environment, then the overrides.
- Checked like every value; a problem of theirs names `overrides`.
- `parse_overrides` turns `KEY=VALUE` texts into the dictionary: a dot between levels, the value everything
  after the first `=`. Values stay text and are converted by the load; a list field takes `hosts=a,b` or a
  JSON array, a dictionary field a JSON object. A key is matched as written, else without regard to case
  and with `_` for `-`. An argument that is no `KEY=VALUE` is a `ValueError` naming its position, never its value.

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
    bad.toml:3: debgu: key not in the schema
    bad.toml:7: db.port: invalid 'Port': must be between 1 and 65535
    bad.toml:8: db.password: expected type: 'enforced.data_types.SecretStr', got type 'list'
```

Each problem starts with `file:line`, which an editor or a terminal opens with a click. The line is left
out where it couldn't be found: never a wrong one. A missing key has the line of the section it belongs in.

The same as data, in pydantic's shape, for a log or an API response:

```python
>>> error.errors(include_context = False)
[{'type': 'unknown', 'loc': ['bad.toml', 'debgu'], 'msg': 'key not in the schema'},
 {'type': 'invalid', 'loc': ['bad.toml', 'db', 'port'], 'msg': "invalid 'Port': must be between 1 and 65535", 'input': 0},
 {'type': 'type', 'loc': ['bad.toml', 'db', 'password'], 'msg': "expected type: 'enforced.data_types.SecretStr', got type 'list'", 'input': '**********'}]
>>> error.json(include_input = False)        # leave the values out
```

- `loc` starts with the file, then the key path (`["app.toml", "servers", 2, "port"]` for an item,
  `servers[2].port` in the message). The line is in `ctx` (`ctx["line"]`), not in `loc`.
- The input of a `SecretStr` field is never shown: `**********` in `errors()`, nothing in the message.
  An unknown key has no `input` at all: nothing says its value isn't a secret.
- `type` is one of `unknown`, `missing`, `section` (a section that isn't a table, a list of sections
  that isn't a list ...), `post_init` (below), and enforced's own (`type`, `invalid`, `length` ...). A
  `section` problem has no `input` either: what stands in a section's place may hold its secrets.
- **An exception of a `__post_init__` is a problem like any other** (`type` `post_init`), gathered with the
  rest: `app.toml:3: db: ValueError: replica is the host itself`. Its `loc` is the section whose
  `__post_init__` raised, it has no `input`, and the exception itself is the `ConfigError`'s `__cause__`.
  Don't put a secret in an exception's text: cfgcast replaces the text of that class's `SecretStr` values
  with `**********` in the message, and can't know of any other.
- `ConfigError` is an `enforced.InvalidDataError` and a `ValueError`: catch whichever you already catch.
- **A configuration built or changed in code raises a `ConfigError` too**: `App(name = 1)`, or
  `app.name = 1` on a `@cfg(frozen = False)` class. There is no file then: `error.file` is `None`, and `loc`
  starts with the field (`["name"]`). Secrets are masked the same way.

With several files or environment variables, each problem starts with where its value came from, and
`loc` starts with that file or variable:

```text
ConfigError: 3 problems in base.toml, prod.toml, APP_* for App:
    APP_DB__PORT: db.port: expected type: 'enforced.data_types.Port', got type 'str'
    base.toml:9: servers[1].port: invalid 'Port': must be between 1 and 65535
    prod.toml:1: debgu: key not in the schema
```

`error.files` is the tuple of sources (`("base.toml", "prod.toml", "APP_*")`), `error.file` the same as
one text. A `ConfigWarning` names the file of each ignored key the same way.

### Unknown keys under `strict = False`

`strict = False` holds for the class it is written on and every section below it, also one with a plain
`@cfg`. A section can loosen itself under a strict class, but can't tighten what is above it. The ignored
keys are named in one `ConfigWarning`, so that a typo doesn't quietly leave you on a default:

```text
ConfigWarning: 2 unknown keys ignored in new.toml for App (/home/me/shop/config.py:12):
    -> monitoring.random_flag
    -> db.prot
```

```python
app = load_cfg("new.toml", App, show_warnings = False)      # also App.load and App.from_dict
```

- After the class comes where it is written (`file:line`, the decorator's line, which an editor opens with a click), left out when
  Python can't tell (a class made in a REPL or with `type()`).
- It names keys and never values. `warning.errors()` has them in the shape above, without `input`.
- It points at the line that called `load_cfg`, and comes before a `ConfigError` for the same file.
- To make it an error in CI: `pytest -W error::cfgcast.ConfigWarning`, or `warnings.filterwarnings("error", category = ConfigWarning)`
  in your code. `python -W` can't name it: Python reads that option before it can import an installed package.

### Where each value came from

```python
>>> from cfgcast import sources_of
>>> app = load_cfg(["base.toml", "prod.yaml"], App, env_prefix = "APP_", overrides = {"debug": True})
>>> sources_of(app)
{'name': 'base.toml:1', 'db.host': 'prod.yaml:3', 'db.port': 'APP_DB__PORT', 'debug': 'overrides', 'hosts': '(default)'}
```

- By key path, as a message writes it (`servers[0].host`): `file:line`, the variable, `overrides`, or
  `(default)` where nothing gave the value. Names only, never a value: fit for a start-up log.
- `sources_of(app.db)` has that section's own keys (`host`, not `db.host`).
- It says how the configuration was loaded: a value assigned later (`frozen = False`) is still what it was.
  A configuration that wasn't loaded (`App(name = "x")`) is a `ValueError`, and a copy made with
  `dataclasses.replace` is such a one.

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
- **Dates and times** are written so that they load as the same value: TOML as its own date and time
  literals, YAML as a bare date or timestamp (a `time` quoted), JSON and ini as ISO 8601 text
  (`2026-10-17T12:00:00+00:00`). TOML has no time with an offset, so that one is quoted text there.
- **A key of a `dict` field that isn't text** (a number, a date, an enum) is written as text, and
  converted back on loading (`dict[int, str]`).
- What a format can't hold is refused before the file is opened (see [ini](#ini)).
- **A file is written whole or not at all**, also when the disk fills up in the middle: the text goes to a
  temporary file next to the target, which then takes its place. A file that was there keeps its
  permission bits (not its owner, ACLs or extended attributes); a symbolic link stays a link, and what it
  points to is replaced; a hard link is broken. The directory must be writable, not only the file.

### As text, and as a dictionary

```python
from cfgcast import to_dict, to_text

print(to_text(app))                     # JSON; to_text(app, "yaml") for any other format
json.dumps(to_dict(app, plain = True))  # a log, a response, a database column
```

- **`to_text(app, cfg_type = "json")`** is the text `write` would write, with one difference: **a
  `SecretStr` is `**********`**, unless `reveal_secrets = True`. Text is printed, logged and sent; a file is
  where secrets go. It takes `overrides` as `write` does.
- **`to_dict(app)`** gives the values as they are, sections as nested dictionaries. Secrets stay `SecretStr`
  (masked when printed); lists and dictionaries are copies.
- **`to_dict(app, plain = True)`** gives only what JSON holds: a path, a `Decimal`, a `UUID`, a date and a
  time as text, an enum as its value, a `SecretStr` as the mask (`reveal_secrets = True` for its text).
- **Don't `json.dumps(to_dict(app))`**: it fails on a `Path` or a `datetime`, and where it doesn't fail it
  writes a `SecretStr`'s real text (`json` reads a `str` subclass by its characters).
- JSON has no NaN and no infinity: the JSON writer refuses them, with the key named.

### A template

```python
from cfgcast import write_template

write_template(App, "app.toml")         # TOML, YAML or ini
```

```toml
# A template for App, made by cfgcast. A key behind a '#' (#key) is required, a secret,
# or has no value by default: remove the '#' and give it a value. A '# ' with a space is a note.

# required: str
#name = ""

debug = false

[db]

# name or address of the server
# required: str
#host = ""

# Port
port = 5432

# required, secret: SecretStr
#password = ""

pool-size = 10

# list[Server]
# a list: one of these for each item
#[[servers]]

# required: str
#host = ""
```

- **A key with a default is written with it.** A key that is required, `None` by default or a secret is
  commented out, with a value of the right kind to replace. **The default of a `SecretStr` is never written.**
- `#key = value` (no space) is a key to fill in, `# text` a note: told apart by eye and by a program.
- Above a key: its `doc` (`field(doc = ...)`), `required`, `secret`, and the type where it says more than
  the value does (`Port`, `Literal['dev', 'prod']`, an enum with its values).
- A section that is `None` by default is commented out with its keys. A list or a dictionary of sections is
  one commented item to copy (`#[[servers]]`, `#[by_region.name]`); ini says that it can't hold the list.
- **With its required keys filled in, the template loads, and gives the defaults.**
- JSON is refused (`ValueError`): it has no comments. A file that is there is kept (`FileExistsError`)
  unless `overwrite = True`: a template over a real configuration would lose it.

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

ini can't hold a line break in a value, spaces around a value, a list whose only item is empty text, or
a list of sections: writing one is an error, and so is loading an ini file that gives text to a
`list[Server]` field (it names the field; nothing is split on commas). Pick another format for such
data. A dictionary of sections is fine: `[by_region.eu]`.

### YAML

Read with **YAML 1.2 booleans**: only `true` / `false` are booleans. In YAML 1.1, which PyYAML follows,
`no`, `yes`, `on` and `off` are booleans too, so `country: no` (Norway) became `False`. Here it stays
`"no"`, and a `bool` field still takes `yes` and `on` through conversion. When writing, any string a
YAML 1.1 or 1.2 reader would take for something else (`no`, `08`, `1e3`, `null`, `~`, `=` ...) is quoted.
An unquoted `2026-10-17` is a date, and a `datetime` field takes it as that day's midnight.

### TOML

TOML has no null: a `None` is left out when writing, and loading the file gives the field its default.

## Not yet

- Durations (`"30s"`, `"5m"`): a `timedelta` field takes no text yet.
- A key that was renamed (the old one still read, with a warning).
- A union of a section and something else (`db: Database | str`).
- Several `.env` files, a directory of secret files, a JSON Schema of a configuration.

See `PLAN.md`.
