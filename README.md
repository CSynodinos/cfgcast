# cfgcast

Typed configuration files for Python 3.13+. Write your configuration as a class, and cfgcast loads
JSON, TOML, ini or YAML into it. Every value is checked and converted, and every problem is reported
at once, with the file, line and key it came from.

Built on [enforced](https://github.com/CSynodinos/enforced) ([PyPI](https://pypi.org/project/enforced/)). MIT licensed.

```python
from enforced.data_types import Port, SecretStr
from cfgcast import Config, field

class Database(Config):
    host: str
    port: Port = 5432
    password: SecretStr

class App(Config):
    name: str
    db: Database                     # a section: another configuration
    debug: bool = False
    hosts: list[str] = field(default_factory = list)

app = App.load("app.toml")
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
App(name='shop', db=Database(host='db.internal', port=Port(5432), password=SecretStr('**********')), debug=False, hosts=[])
>>> app.db.password.get_secret_value()
's3cret'
```

**Contents:** [Why](#why) · [Install](#install) · [Writing a schema](#writing-a-schema) ·
[Loading](#loading) · [When something is wrong](#when-something-is-wrong) · [Writing a file](#writing-a-file) ·
[Format details](#format-details) · [Not yet](#not-yet)

## Why

- **A configuration is a plain dataclass.** It is frozen and keyword-only, and has `==`, `repr`, pickling,
  `dataclasses.replace` and everything else a dataclass has. Type checkers understand it fully. `Config`
  adds two methods, `load` and `from_dict`.
- **Safe by default.** Unknown keys are refused, so a typo like `debgu = true` never silently leaves you on
  the default. A loaded configuration can't be changed, and a secret never appears in a message.
- **Every problem at once**, each with its file, line and key path: `app.toml:12: db.port: ...`.
- **The formats are handled properly.** YAML's `no` stays the string `"no"`, ini gets nesting and lists,
  and written files are quoted so that any reader gets the same values back.
- **Layers and the environment.** Several files, environment variables, a `.env` file, secrets from files
  and overrides from the command line. Every problem still names the file or variable its value came from,
  and `sources_of(app)` tells you for every value.
- **Writing and templates.** Write any configuration to any format, and generate a commented template
  of a schema with every key, its default, type and description.
- **Few dependencies:** enforced, clitint (colours) and tomli-w. PyYAML only if you use YAML.

## Install

```bash
pip install cfgcast            # JSON, TOML, ini
pip install "cfgcast[yaml]"    # and YAML (PyYAML)
```

## Writing a schema

A schema is a class below `Config`. Each annotated attribute is a key, its type says what the value must
be, and a default makes the key optional. Options are given as class keywords:

```python
class Server(Config):
    host: str = "0.0.0.0"
    port: Port = 8080
    workers: int = 4
    tags: list[str] = field(default_factory = list)   # a mutable default needs default_factory
    tls: Tls | None = None                            # an optional section

class Lenient(Config, strict = False, frozen = False):
    ...
```

| option | default | |
|---|---|---|
| `strict` | `True` | Refuse keys the schema doesn't name. `False` ignores them (with a warning) in this class and every section below it: useful for an older program reading a newer file. See [Unknown keys](#unknown-keys-under-strict--false). |
| `frozen` | `True` | A configuration can't be changed after it is made. See [Frozen](#frozen). |
| `env_prefix` | `None` | Read environment variables with this prefix whenever the class is loaded. See [Environment variables](#environment-variables). |
| `keys` | `None` | `"kebab-case"` writes a field `max_size` as the key `max-size`. See [Keys that aren't Python names](#keys-that-arent-python-names). |

Two things are always on:

- **Values are converted**, as enforced's `coerce` does it: `"8080"` becomes `8080` for an `int`, `"true"`
  and `"yes"` become `True` for a `bool`, `"2026-10-17"` becomes a `date`, and a `date` given for a
  `datetime` is its midnight. An `int` is never turned into a `str`: `name = 1` is reported, because you
  probably meant `"1"`.
- **Every item is checked**: each item of a `list[Port]`, each value of a `dict[str, float]`.

Any type enforced can check works in a schema: `Literal["DEBUG", "INFO"]`, `Annotated[int, lambda v: v > 0]`,
enums, `Path`, `Decimal`, `datetime`, and enforced's validated types (`Port`, `Url`, `Email`, `SecretStr`,
`PhoneNumber`, `Probability` ...).

A field whose type is another configuration is a **section**: a table in TOML, a nested object in JSON and
YAML, a `[section]` in ini. Sections can be defined in any order. On Python 3.13, quote the name of a
section that is defined further down (`db: "Database"`), because 3.13 evaluates annotations immediately.
From 3.14 either form works.

### Lists and dictionaries of sections

```python
class App(Config):
    servers: list[Server]              # [[servers]] in TOML; a list of objects in JSON and YAML
    by_region: dict[str, Server]       # [by_region.eu] in TOML and ini; {"eu": {...}} in JSON and YAML
```

`list[X]`, `tuple[X, ...]` and `dict[str, X]` of a configuration `X` are built item by item, and each item
is checked like any section, unknown and missing keys included. A problem says which item it is in:
`servers[2].port`, `by_region.eu.host`. ini can hold a dictionary of sections but not a list of them
(see [ini](#ini)).

### `field`: a key and a doc

`cfgcast.field` is `dataclasses.field` (`default`, `default_factory`, `init` ...) with two more options.
`dataclasses.field` still works; you only need this one for these options.

```python
from cfgcast import Config, field

class Pool(Config):
    max_size: int = field(default = 10, key = "max-size", doc = "connections at most")
    class_: str = field(default = "small", key = "class")
```

| option | |
|---|---|
| `key` | the field's key in a file, where it differs from the field's name |
| `doc` | a line about the field, written above its key by [`write_template`](#a-template) |

### Keys that aren't Python names

Keys like `max-size`, `log-level` or `class` are common in TOML and YAML, but can't be Python names. Give
the key for one field with `field(key = "max-size")`, or for a whole class:

```python
class Pool(Config, keys = "kebab-case"):    # max_size is the key max-size, class_ is class
    max_size: int = 10
    class_: str = "small"
```

- **The key is what the file has**, and everything to do with files uses it: unknown and missing keys,
  `loc` and messages, `write`, `to_dict` and `from_dict` (so `App.from_dict(to_dict(app)) == app`). The
  attribute keeps its Python name: `pool.max_size`.
- The field's Python name is not a key: `max_size = 10` in a file is an unknown key.
- `keys = "kebab-case"` applies to its own class; a section sets its own. A field's own `key` wins over it.
- Environment variables can't hold a `-`, so `APP_POOL__MAX_SIZE` is `pool.max-size`.
- Two fields with the same key are refused with a `TypeError` when the class is defined.

### Inheritance

```python
class App(Config, strict = False, env_prefix = "APP_"):
    name: str
    db: Database

class Prod(App):                       # a configuration too: App's fields, options and sections, and its own
    replicas: int = 3
    name: str = "shop"                 # redefined, here with a default

prod = Prod.load("prod.toml")          # a Prod
```

- **Fields are inherited** as in any dataclass, in order. A redefined field keeps its place and takes its
  new type or default. Every field is checked, inherited ones too.
- **`strict`, `env_prefix` and `keys` are inherited.** A subclass can set its own
  (`class Plain(App, strict = True)`). `keys` also applies to the inherited fields.
- **`frozen` is set by each class.** A subclass of a frozen class is frozen. A subclass of a class with
  `frozen = False` must say `frozen = False` too; otherwise it is a `TypeError`, and type checkers flag it.
- Two fields with the same key are refused, inherited ones included.

### Frozen

A configuration is frozen unless its class says `frozen = False`. Assigning to it or deleting an attribute
raises a `FrozenConfigError`, which names the class and the key, never the value:

```text
FrozenConfigError: Database is frozen: db.password can't be assigned (dataclasses.replace makes a changed copy; frozen = False on the class allows it)
```

- It is a `dataclasses.FrozenInstanceError`, and so an `AttributeError`: code that catches either still
  catches it.
- A section of a loaded configuration gives its full key path (`db.password`, `servers[1].port`); one made
  in code gives just its key.
- `dataclasses.replace(app, debug = True)` makes a changed copy, checked like any other construction.

## Loading

```python
app = App.load("app.toml")
app = App.from_dict({"name": "shop", "db": {...}})   # from a dictionary you already have
```

Both are methods of `Config`, so type checkers know that `App.load` returns an `App` and `Prod.load` a
`Prod`. Because of this, a field can't be named `load` or `from_dict`.

| format | suffixes | notes |
|---|---|---|
| JSON | `.json` | the top level must be an object |
| TOML | `.toml` | |
| ini | `.ini`, `.cfg` | see [ini](#ini) |
| YAML | `.yaml`, `.yml` | read with YAML 1.2 booleans; see [YAML](#yaml) |

The format comes from the suffix, in any case (`APP.JSON` is JSON). For any other file name, name the
format:

```python
app = App.load("settings.conf", "toml")       # or cfg_type = "toml"
```

A file that isn't valid in its format raises what its parser raises (`json.JSONDecodeError`,
`tomllib.TOMLDecodeError`, `configparser.Error`, `yaml.YAMLError`), and a missing file `FileNotFoundError`.

### Several files, in layers

```python
from cfgcast import optional

app = App.load(["base.toml", "prod.yaml", optional("local.toml")])
```

- **A later file wins.** Sections are merged key by key; any other value is replaced, **lists included**.
  A list is never appended to, because a later layer couldn't undo an append. Each file can be in its own
  format.
- **The result is checked once.** A key is missing only if no layer has it, so a layer doesn't need to be
  a whole configuration.
- **A problem names the file its value came from.** An unknown key names the file that has it, a missing
  key the last file.
- **Every file must exist**, unless it is marked with `optional`. An optional file that is there is read
  like any other, and its problems are reported. A directory or a broken link in its place is an error.
  There is deliberately no `missing_ok` for the whole list: it would also skip a missing `base.toml`, and
  the program would run on defaults without anyone noticing.

### Environment variables

```python
app = App.load("app.toml", env_prefix = "APP_")      # APP_DB__PORT=9090 sets db.port
app = App.load([], env_prefix = "APP_")              # the environment alone
```

- The prefix is removed, `__` separates levels, and names match fields regardless of case: `APP_DB__PORT`,
  `app_db__port` and `APP_Db__Port` are all `db.port`.
- **Variables go over every file.**
- **A variable that names no field is ignored**, also under `strict` and without a warning: the
  environment holds variables set by other tools too (`APP_HOME`).
- Values are text, converted like any other value. A list field takes `a, b, c` (split on commas) or a
  JSON array (text starting with `[`); a dictionary field takes a JSON object (`{"eu": 1}`). Text that looks
  like JSON but isn't is a `ValueError` naming the variable, never its value.
- A variable can reach into a section (`APP_DB__PORT`), but not into an item of a list or dictionary of
  sections.
- A problem names the variable: `APP_DB__PORT: db.port: ...`.
- `env = {...}` replaces `os.environ`, for a test or a workflow; `env = {}` reads no variables.

**The prefix can be set on the class**, so every load reads the variables without repeating it:

```python
class App(Config, env_prefix = "APP_"):
    ...

app = App.load("app.toml")             # reads APP_* too
```

A prefix given to `load` wins over the class's. Only the class being loaded counts, not its sections', and
`from_dict` reads no variables.

### A `.env` file

```python
app = App.load("app.toml", env_prefix = "APP_", env_file = ".env")     # or env_file = optional(".env")
```

- It is read with the prefix, like the environment. It goes **over the files and under the environment**,
  so a variable set in the process wins. `os.environ` is never changed.
- One `KEY=VALUE` per line. Blank lines and `#` lines are skipped, and `export KEY=VALUE` is allowed. A value
  is bare (` #` starts a comment), `'single quoted'` (taken as written) or `"double quoted"` (with `\n`,
  `\t`, `\"` and `\\`, and it may span lines). There is no interpolation: `${HOME}` stays that text.
- A problem names the file and the line: `.env:12: db.port: ...`. A line that isn't `KEY=VALUE` is a
  `ValueError` with the file and line number, never the line's text.

### Secrets from files

Docker, Compose and Kubernetes hand secrets over as files (`/run/secrets/...`). Point a variable at one by
adding `_FILE` to its name:

```bash
APP_DB__PASSWORD_FILE=/run/secrets/db_password
```

- If a variable ending in `_FILE` names no field, but would name one without the `_FILE`, that field gets
  the text of the file (UTF-8, one trailing line break removed). This works for any field, not only a
  `SecretStr`.
- A variable that names a field as it is stays that field: `APP_TLS__CERT_FILE` is `tls.cert_file` if the
  schema has that field.
- Setting both `APP_DB__PASSWORD` and `APP_DB__PASSWORD_FILE` is a `ValueError` naming both. A file that
  isn't there is a `FileNotFoundError` naming the variable. The file's text is never in a message.

### A section of a bigger file

```python
app = App.load("pyproject.toml", section = "tool.myapp")     # the table [tool.myapp]
```

- Separate levels with dots, or pass a list of keys where a key has a dot in it (`["tool", "my.app"]`). The
  rest of the file is ignored: it belongs to other tools.
- It applies to every file of a list, but not to environment variables, which name the schema's own keys.
- A problem keeps the full path, so you can find it in the file: `pyproject.toml:14: tool.myapp.db.port: ...`.
- A file without the section is a problem (`tool.myapp: missing section`).

### Overrides for one run

```python
import sys
from cfgcast import parse_overrides

app = App.load("app.toml", overrides = {"db": {"port": 9090}})                # from code
app = App.load("app.toml", overrides = parse_overrides(sys.argv[1:], App))   # python run.py db.port=9090 debug=true
```

- **Overrides go over everything.** The order is: the files, then the `.env` file, then the environment,
  then the overrides.
- They are checked like every other value, and a problem in one names `overrides`.
- `parse_overrides` turns `KEY=VALUE` texts into that dictionary. A dot separates levels, and the value is
  everything after the first `=`. Values stay text and are converted by the load. A list field takes
  `hosts=a,b` or a JSON array, a dictionary field a JSON object. A key is matched as written, and otherwise
  regardless of case and with `_` for `-`. An argument that isn't `KEY=VALUE` is a `ValueError` naming its
  position, never its value.

### Where each value came from

```python
>>> from cfgcast import sources_of
>>> app = App.load(["base.toml", "prod.yaml"], env_prefix = "APP_", overrides = {"debug": True})
>>> sources_of(app)
{'name': 'base.toml:1', 'db.host': 'prod.yaml:3', 'db.port': 'APP_DB__PORT', 'debug': 'overrides', 'hosts': '(default)'}
```

- Keyed by path, as messages write it (`servers[0].host`). Each value is `file:line`, the variable,
  `overrides`, or `(default)` where nothing set it. It holds names only, never values, so it is safe for a
  start-up log.
- `sources_of(app.db)` gives that section's own keys (`host`, not `db.host`).
- It describes the load: a value assigned later (with `frozen = False`) still shows where the loaded one
  came from. A configuration that wasn't loaded, such as `App(name = "x")` or a copy from
  `dataclasses.replace`, is a `ValueError`.

## When something is wrong

`App.load` raises a `ConfigError` with every problem at once:

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

Each problem starts with `file:line`, which an editor or terminal opens with a click. The line is left out
where it couldn't be found, so it is never wrong. A missing key gets the line of the section it belongs in.

The same problems as data, in pydantic's shape, for a log or an API response:

```python
>>> error.errors(include_context = False)
[{'type': 'unknown', 'loc': ['bad.toml', 'debgu'], 'msg': 'key not in the schema'},
 {'type': 'invalid', 'loc': ['bad.toml', 'db', 'port'], 'msg': "invalid 'Port': must be between 1 and 65535", 'input': 0},
 {'type': 'type', 'loc': ['bad.toml', 'db', 'password'], 'msg': "expected type: 'enforced.data_types.SecretStr', got type 'list'", 'input': '**********'}]
>>> error.json(include_input = False)        # without the values
```

- `loc` is the file, then the key path: `["app.toml", "servers", 2, "port"]` for a list item, written
  `servers[2].port` in the message. The line is in `ctx["line"]`, not in `loc`.
- **A secret is never shown**: the input of a `SecretStr` field is `**********` in `errors()` and absent from
  the message. An unknown key has no `input` at all, since nothing says its value isn't a secret.
- `type` is `unknown`, `missing`, `section` (something other than a section where one belongs), `post_init`
  (below), or one of enforced's own (`type`, `invalid`, `length` ...). A `section` problem has no `input`
  either: what stands in a section's place may hold its secrets.
- **An exception raised by a `__post_init__` is a problem like any other** (type `post_init`), gathered
  with the rest: `app.toml:3: db: ValueError: replica is the host itself`. Its `loc` is the section whose
  `__post_init__` raised, it has no `input`, and the exception is the `ConfigError`'s `__cause__`. Keep
  secrets out of your exception messages: cfgcast masks that class's `SecretStr` values in the text, but
  can't know of any other secret.
- `ConfigError` is both an `enforced.InvalidDataError` and a `ValueError`: catch whichever you already do.
- **A configuration built or changed in code raises a `ConfigError` too**: `App(name = 1)`, or
  `app.name = 1` on a class with `frozen = False`. There is no file then: `error.file` is `None` and `loc`
  starts with the field (`["name"]`). Secrets are masked the same way.

With several files or environment variables, each problem starts with where its value came from, and so
does its `loc`:

```text
ConfigError: 3 problems in base.toml, prod.toml, APP_* for App:
    APP_DB__PORT: db.port: expected type: 'enforced.data_types.Port', got type 'str'
    base.toml:9: servers[1].port: invalid 'Port': must be between 1 and 65535
    prod.toml:1: debgu: key not in the schema
```

`error.files` is the tuple of sources (`("base.toml", "prod.toml", "APP_*")`), and `error.file` the same
as one text.

### Unknown keys under `strict = False`

`strict = False` applies to the class it is set on and every section below it, strict ones included. A
section can loosen itself under a strict class, but can't tighten what is above it. The ignored keys are
named in one `ConfigWarning`, so that a typo doesn't quietly leave you on a default:

```text
ConfigWarning: 2 unknown keys ignored in new.toml for App (/home/me/shop/config.py:12):
    -> monitoring.random_flag
    -> db.prot
```

- After the class name comes where the class is defined (`file:line`, clickable). It is left out when
  Python can't tell, as for a class made in a REPL or with `type()`.
- It names keys, never values. `warning.errors()` lists them in the shape above, without `input`.
- It points at the line that called `load` or `from_dict`, and comes before any `ConfigError` for the same
  load.
- To leave it out: `warnings.filterwarnings("ignore", category = ConfigWarning)`.
- To make it an error in CI: `pytest -W error::cfgcast.ConfigWarning`, or
  `warnings.filterwarnings("error", category = ConfigWarning)` in your code. `python -W` can't name it,
  because Python reads that option before it can import an installed package.

## Writing a file

```python
from cfgcast import write

write(app, "prod.yaml")
```

```yaml
# prod.yaml
name: shop
db:
  host: db.prod
  port: 5432
  password: s3cret
debug: false
hosts: []
```

- **`write` writes the configuration it is given.** For other values, make a configuration that has them,
  checked as every one is: `App.load("app.toml", overrides = {"db": {"host": "db.prod"}})`,
  `dataclasses.replace(app, debug = True)`, or `App.from_dict(...)`.
- **Any configuration, any format**: load YAML, write ini. The format comes from the suffix, or `cfg_type`.
- **Every key is written**, also one equal to its default. The file then means the same even if a default
  in the code changes later, so a run can be repeated from its file.
- **A `SecretStr` is written as its real text**: a configuration file is where secrets belong.
- **Comments and layout** of the file it was loaded from are not kept: `write` writes values, it doesn't
  edit a file.
- **Dates and times** load back as the same values: as TOML's own date and time literals, as a bare date or
  timestamp in YAML (a `time` quoted), and as ISO 8601 text in JSON and ini (`2026-10-17T12:00:00+00:00`).
  TOML has no time with an offset, so such a time is quoted text there.
- **A `dict` key that isn't text** (a number, a date, an enum) is written as text and converted back on
  loading (`dict[int, str]`).
- What a format can't hold is refused before the file is opened (see [ini](#ini)).
- **A file is written whole or not at all**, even if the disk fills up halfway. The text goes to a
  temporary file next to the target, which then replaces it. An existing file keeps its permission bits
  (not its owner, ACLs or extended attributes); a symbolic link stays a link and its target is replaced; a
  hard link is broken. The directory must be writable, not only the file.

### As text, and as a dictionary

```python
from cfgcast import to_dict, to_text

print(to_text(app))                     # JSON; to_text(app, "yaml") for another format
json.dumps(to_dict(app, plain = True))  # for a log, a response, a database column
```

- **`to_text(app, cfg_type = "json")`** is the text `write` would write, with one difference: **a
  `SecretStr` is `**********`** unless you pass `reveal_secrets = True`. Text gets printed, logged and sent;
  a file is where secrets belong.
- **`to_dict(app)`** gives the values as they are, with sections as nested dictionaries. Secrets stay
  `SecretStr` (masked when printed), and lists and dictionaries are copies.
- **`to_dict(app, plain = True)`** gives only what JSON can hold: a path, a `Decimal`, a `UUID`, a date and a
  time as text, an enum as its value, and a `SecretStr` as the mask (`reveal_secrets = True` for its text).
- **Don't `json.dumps(to_dict(app))`**: it fails on a `Path` or a `datetime`, and where it doesn't fail, it
  writes a `SecretStr`'s real text (`json` reads a `str` subclass by its characters).
- JSON has no NaN or infinity, so the JSON writer refuses them and names the key.

### A template

```python
from cfgcast import write_template

write_template(App, "app.toml")         # TOML, YAML or ini
```

For the `App` at the top of this page, with `doc = "name or address of the server"` on `Database.host`:

```toml
# A template for App, made by cfgcast. A key behind a '#' (#key) is required, a secret,
# or has no value by default: remove the '#' and give it a value. A '# ' with a space is a note.

# required: str
#name = ""

debug = false

# list[str]
hosts = []

[db]

# name or address of the server
# required: str
#host = ""

# Port
port = 5432

# required, secret: SecretStr
#password = ""
```

- **A key with a default is written with it.** A key that is required, `None` by default or a secret is
  commented out, with a placeholder of the right kind. **A `SecretStr` default is never written.**
- `#key = value` (no space) is a key to fill in, `# text` (with a space) is a note. People and programs can
  tell them apart.
- Above each key: its `doc` (`field(doc = ...)`), whether it is `required` or a `secret`, and its type where
  that says more than the value (`Port`, `Literal['dev', 'prod']`, an enum with its values).
- A section that is `None` by default is commented out with its keys. A list or dictionary of sections gets
  one commented-out item to copy (`#[[servers]]`, `#[by_region.name]`); in ini, a comment says it can't hold
  the list.
- **With its required keys filled in, the template loads and gives the defaults.**
- JSON is refused with a `ValueError`, since it has no comments. An existing file is kept
  (`FileExistsError`) unless you pass `overwrite = True`: a template written over a real configuration
  would lose it.

## Format details

### ini

ini has only one level of sections and only text values, so cfgcast adds three rules:

```ini
[DEFAULT]                       ; the top-level keys
name = shop
hosts = a.example.com, b.example.com    ; a list: split on commas where the field is a list[...]

[db]
host = db.internal

[db.pool]                       ; a dotted section is nesting: "pool" inside "db"
size = 10
```

- **`[DEFAULT]` holds the top-level keys**, because ini allows no key outside a section. Unlike Python's
  `configparser`, its keys are *not* copied into the other sections.
- **Lists** are comma-separated, but only where the field is a `list[...]`; elsewhere a comma is part of
  the text. `hosts =` is an empty list, and an item can't contain a comma.
- **Every value is text**: `true`, `5432` and `2.5` are converted like any other value.
- `%` is just a `%` (no interpolation), and keys keep their case.

ini can't hold a line break in a value, spaces around a value, a list whose only item is empty text, or a
list of sections. Writing one of these is an error, and so is loading an ini file that gives text to a
`list[Server]` field (it names the field, and nothing is split). Use another format for such data. A
dictionary of sections is fine: `[by_region.eu]`.

### YAML

YAML is read with **YAML 1.2 booleans**: only `true` and `false` are booleans. In YAML 1.1, which PyYAML
follows, `no`, `yes`, `on` and `off` are booleans too, so `country: no` (Norway) became `False`. Here it
stays `"no"`, and a `bool` field still accepts `yes` and `on` through conversion.

When writing (which needs PyYAML too), any string that a YAML 1.1 or 1.2 reader would take for something
else (`no`, `08`, `1e3`, `null`, `~`, `=` ...) is quoted. An unquoted `2026-10-17` is a date, and a
`datetime` field takes it as that day's midnight.

### TOML

TOML has no null: a `None` is left out when writing, and loading the file gives the field its default.

## Not yet

- Durations (`"30s"`, `"5m"`): a `timedelta` field doesn't take text yet.
- Renamed keys (reading the old key too, with a warning).
- A union of a section and something else (`db: Database | str`).
- Several `.env` files, a directory of secret files, and a JSON Schema of a configuration.
