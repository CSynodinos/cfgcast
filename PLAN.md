# cfgcast: plan

**Renamed on 2026-11-01** (the owner's choice): the project was `configured` until then.

**Python 3.13 on 2026-11-03**, with enforced: `_annotated_names` is the only line that differs by version.

## Decisions

- **Formats:** JSON, TOML, ini (`.ini` and `.cfg`, one format), YAML. No XML. Environment variables since 2026-11-15 (feature 4 below).
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

**Open: static typing of those members** (planned 2026-11-15: "Next features", 7; decided: yes). A checker reads `@dataclass_transform` for the fields, but doesn't
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

## Next features (planned 2026-10-18; 1 to 4 built on 2026-11-15, 5 on 2026-10-22)

What users will ask for first. 1 and 2 stand alone; 4 is built on 3. Each keeps its plan below, with
"Built" saying what was decided while building it. What came after is in "Decided on 2026-11-15" and the second "Next features".

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

**Built.** `_hints.section_of` says how a field holds sections (`one` / `list` / `tuple` / `dict`), and `_build`,
`_Sections`, `read_ini` and `read_env` all ask it. Decided on the way:

- A value that isn't what the field takes (text for a `list[Server]`) is one `section` problem, **without an
  `input`** (it was shown before, for one section too): what stands in a section's place may hold its secrets.
- ini *holds* a dictionary of sections (`[by_region.eu]`), so only the list is refused. Loading: a `ValueError`
  from `read_ini` naming the field, not a `ConfigError` (the file can't say it at all; it is not a wrong value).
- A union with a section in it (`Db | str`, `list[Db | None]`) is taken as the section only: see "Next features (planned 2026-11-15)", 15 (maybe).

### 2. Dates and times

- enforced first (the owner's decision): `coerce` already parses ISO 8601 text into `datetime` / `date` /
  `time`. Missing for configs: a `date` value (YAML's unquoted `2026-10-17`) for a `datetime` field, added to
  enforced's coercion on 2026-10-18 (midnight, no timezone: what the text `"2026-10-17"` gives).
- cfgcast, then: the writers write dates. TOML as native literals, YAML unquoted (read back as dates),
  JSON and ini as ISO text. `_plain` lets `date` / `datetime` / `time` through to the writers.
- Not now: durations (`timedelta`, "30s", "5m": not ISO; a `Duration` model later), Unix timestamps.
- Open (enforced): a `date` field accepts a `datetime` (it is a subclass), and keeps it as one. Planned in enforced on
  2026-11-15: `/mnt/d/dev/enforced/date_plan_cfgcast.md`.

**Built.** TOML: its own literals; a `time` with an offset has none, and is quoted ISO text. YAML: a bare date
or timestamp, a `time` quoted (YAML has no time). JSON and ini: ISO text.

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
- Was open, decided 2026-11-15 (`optional(...)`, feature 3; not `missing_ok`): a layer that may be absent (a local override file). `optional("local.toml")` (a wrapper on that one
  path) or `missing_ok = True` (for every layer of the call).

**Built** (`_sources.py`: `Sources`, `layer`). Decided on the way:

- With more than one source the message says the source per line (`    prod.toml: db.port: ...`) and the head
  names them all; one file alone reads as before. `ConfigError.files` is the tuple, `file` their text.
- Every format is worked out before any file is read (a wrong suffix in the last layer is said first), and
  every file must exist: the absent layer is still open.
- `App.load` takes the list and `env_prefix` too.

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

**Built** (`_sources.read_env`). Decided on the way:

- One layer per variable, in the order of their names, so each problem names its own variable (`loc[0]`); the
  head of an error names the environment as `APP_*`.
- **A dictionary field takes a JSON object** (text starting with `{`), next to the list's JSON array: not in
  the plan, added because a `dict[str, float]` had no way in otherwise.
- Text that starts as JSON and isn't: a `ValueError` naming the variable, with the parser's message and
  position, never the text (it may be a secret).
- `env = None` stands for `os.environ` (a default of `os.environ` itself would show in the signature and be
  bound at import); `env` without `env_prefix` is a `TypeError`. `load_cfg([], App, env_prefix = ...)` is
  allowed: the environment alone.
- A variable reaches a field of a section, not an item of a list or dictionary of sections (`APP_SERVERS__0__PORT`
  is ignored like any unknown variable).

### Decided

- Dates: enforced first. Lists in layers: replaced. The env prefix: on `load_cfg` first.
  Unknown env variables with `strict`: ignored.

### 5. `ConfigError` raised by the class itself (2026-10-22; built the same day)

Planned with enforced's `error =` option, in `/mnt/d/dev/enforced/PLAN_error_type.md` (part 2): `@cfg` passes
`error = ConfigError` to `enforced.dataclass`, so a direct construction or assignment raises a `ConfigError`
too, and `ConfigError` works when enforced makes it without `__init__` (class-attribute defaults for `file`,
`schema` and `entries`). Do it after enforced's part is built. Includes masking a secret's input in
`ConfigError.errors()` for both doors.

## Code review of 2026-11-15

After 1 to 4 were built. Fixed, each with a test ("found by review" in the tests):

- `_save` opened the file before the text was encoded: text that isn't UTF-8 (a lone surrogate) left an empty
  file where a good one was. It encodes first now.
- `to_dict` / `write` took `init = False` fields, which `from_dict` then refused as unknown.
- Messages that showed a value: ini's `[DEFAULT]` clash on reading, and the ini writer's refusals (a line
  break, surrounding spaces, a comma in a list item). `write` gives a `SecretStr` as real text, so these were secrets.
- YAML: a bare `=` (the 1.1 value tag) and non-printable characters (DEL, NEL, a lone surrogate) are quoted / escaped.
- A key of a `dict` field that isn't text (`dict[int, str]`) broke the TOML, ini and YAML writers: written as text now.
- ini wrote `[""]` as an empty value, which loads as `[]`: refused.

## Since the code review (2026-11-15)

- **The class's place in the `ConfigWarning`**: `for App (path:line)`, the line of the decorator
  (`__firstlineno__`), left out when Python can't tell (`type()`, a REPL). The owner chose `path:line` over
  `(path,line)`: an editor opens it with a click.
- **clitint** is a dependency of its own (the colours, `turn_colours_off` / `turn_colours_on`), as it is of enforced.
- enforced 0.1.3 and clitint 0.1.3 are on PyPI.

## Decided on 2026-11-15 (the owner)

- **A missing file is an error, unless it is marked optional.** `optional("local.toml")` marks one file of a
  list as "read it if it is there"; every file not marked must be there (a `FileNotFoundError`, as now).
  `missing_ok = True` is refused: it would skip a missing `base.toml` too, and the program would run on
  defaults without anyone knowing. Next feature 3.
- **An exception of a `__post_init__` is a problem like any other**: gathered with the rest, raised in the one
  `ConfigError` (an `InvalidDataError`). Next feature 2.
- **enforced: a `date` field and a `datetime` value** is planned in enforced, in
  `/mnt/d/dev/enforced/date_plan_cfgcast.md`, with what cfgcast does after it.
- **Durations wait for enforced** (feature 6): planned here, not started until enforced parses them.
- **Checkers see `load` and `from_dict`: yes** (feature 7), both ways a and b.
- **Of the proposals** (answered the same day): keys that aren't Python names, a section of a bigger file,
  secrets from files, where each value came from (with the file *and line*), and a template file: **yes**,
  planned as 10 to 14 (with 9, `cfgcast.field`, which they need). Renamed keys and unions with a section: **maybe** (15). Overrides at load time:
  **explanation asked** (15), not decided.
- **Order**: atomic writes, the `__post_init__` problems, optional files, `.env` files, `@cfg(env_prefix = ...)`,
  (durations when enforced is ready), checkers, JSON, then the proposals taken.

## Next features (planned 2026-11-15; in this order, decided by the owner)

### 1. A file is written whole or not at all, also when the disk fails

Rule 4 holds today for everything known before the file is opened. A full disk, a quota or a crash while
writing still leaves half a file in place of a good one.

- `_save(file, text)` writes to a **temporary file in the same directory** and `os.replace`s the target with
  it: the target is the old file or the new one, never a mixture. The same directory, because `os.replace`
  is atomic only within one file system.
- The temporary file: `os.open(name, O_WRONLY | O_CREAT | O_EXCL, 0o666)`, with a name of its own
  (`.app.toml.<random>.tmp`). Not `tempfile.mkstemp`: it makes the file `0o600`, and a new configuration
  would then be readable by its owner only. `os.open`'s mode goes through the umask, as `open()`'s does.
- Written, flushed, `os.fsync`ed, closed, then replaced. On any error the temporary file is removed and the
  error raised; the target is untouched.
- **A file that was there keeps its permission bits** (`os.stat` -> `os.chmod` on the temporary file before
  the replace). Its owner, group, ACLs and extended attributes are not kept: README note.
- **A symbolic link stays a link**: the target is `os.path.realpath(file)`, so the file it points to is
  replaced, not the link. A hard link is broken (the other name keeps the old text): README note.
- **The directory must be writable**, not only the file: a writable file in a read-only directory is now a
  `PermissionError`. No fallback to writing in place: it would break the rule without saying so.
- Windows: `os.replace` fails while another program holds the target open (`PermissionError`). No retry.
- Not done: an `fsync` of the directory after the replace (POSIX only; durability after a power cut, not
  atomicity). Later, if asked.
- Tests: an error in the middle of the write (a monkeypatched `os.write` / `os.fsync`) leaves the old file
  and no temporary one; the mode of an old file is kept; a new file's mode follows the umask; a link stays
  a link and its target changes; a directory as the target is refused before anything is written.
- CLAUDE.md: the "full disk" line of the known issues goes; rule 4 holds without the "before".

### 2. Problems from `__post_init__`

Today a `__post_init__` that raises anything but an `InvalidDataError` leaves `_build`, and every problem
gathered so far is lost (tried on 2026-11-15: `name: 1` and `replica.port: 0` were not reported, the
`ValueError` of `db` was).

- `_build`, around `schema(**arguments)`: after `except InvalidDataError` (as now), `except Exception as
  error` makes a problem of it. `BaseException` is not caught (`KeyboardInterrupt`, `SystemExit` go through).
- The problem: `type` `"post_init"`, `loc` the path of the class whose `__post_init__` raised (`["app.toml",
  "db"]`; at the top level the file alone, which the message says as `(the whole file)`), `msg` the
  exception as `ValueError: host may not be 'bad'`, `ctx` `{"schema": "Database", "exception": "ValueError"}`.
  **No `input`**: the exception is about the whole section, which may hold its secrets.
- **Secrets** (rule 3): the text is the user's own, and cfgcast can't read it. What it can do: the
  arguments are at hand, so the real text of every `SecretStr` argument of that class is replaced in the
  message with `MASK` (`raise ValueError(f"wrong password {self.password.get_secret_value()}")` shows
  `**********`). README: don't put a secret in an exception's text; cfgcast hides the ones it knows.
- The exception itself: the first one is the `ConfigError`'s `__cause__` (`raise ... from it`), so its
  traceback is still there for a debugger; the others are in the problems only.
- The section's key is left out of its parent's arguments, as for any section that failed, so the parent
  doesn't call it missing; the parent's own problems are still gathered.
- A section whose `__post_init__` raises in a list (`servers[2]`) has the index in its `loc`, as now.
- An `InvalidDataError` a `__post_init__` raises (from a validated call of its own): its entries are merged
  as enforced's are now. Their `loc`s are of that call, not of a field; the test says what they look like.
- Not changed: a direct construction (`Database(host = "bad")`) raises the `ValueError` as it is. Nothing to
  gather in one call.
- `write`'s overrides go through `_build`, so they get it too.
- Tests: two failing `__post_init__`s and a wrong value in one file, all three reported; the masked secret;
  the `__cause__`; a `KeyboardInterrupt` going through; the top level; an item of a list.
- README ("When something is wrong"): the new type and an example. CLAUDE.md: the problem types.

### 3. Optional files in a list: `optional("local.toml")`
Decided 2026-11-15: a file of the list may be marked optional; nothing else may be missing.

```python
from cfgcast import load_cfg, optional

app = load_cfg(["base.toml", "prod.toml", optional("local.toml")], App)
```

- `optional(path)` returns a small marker (an `Optional` object holding the path; `os.fspath` is refused on
  it, so it can't be passed where a path is wanted by mistake). Public: `cfgcast.optional`.
- An optional file that isn't there is left out: the load goes on as if it weren't in the list. One that is
  there is read like any other, and a problem in it is a problem (an unreadable file, a wrong format, a wrong
  value): optional is about being there, nothing else.
- **Only "not there" is skipped**: `FileNotFoundError` for that path. A directory in its place, no permission
  to read it, a broken symbolic link: errors as for any file (a broken link is a file that was meant to be there).
- A single optional file (`load_cfg(optional("local.toml"), App)`) that isn't there: a load from nothing, so
  only the defaults (and the environment): every key without a default is `missing`. Allowed, said in the README.
- The head of an error names only the files that were read. A `ConfigWarning` and `sources_of` (13) the same.
- Not for `write` (a file to write is always meant), nor for `.env` files at first (`env_file = optional(".env")`
  is the obvious next step, same marker; say so in the README when 4 is built).
- The suffix of an optional file that isn't there is still checked (`_format_of`): a wrong suffix is a mistake
  in the program, not a missing file.
- Tests: there and not there; a problem in an optional file that is there; a directory in its place; all files
  optional and none there; the marker refused by `os.fspath`; `App.load([...])` too.
- README: "Several files, in layers" gains it; "Not yet" loses "a layer that may be absent".

### 4. `.env` files

```python
app = load_cfg("app.toml", App, env_prefix = "APP_", env_file = ".env")
```

- `env_file` takes one path, read only with an `env_prefix` (a `TypeError` otherwise, as `env`). A missing
  file is a `FileNotFoundError` (decided above).
- **Order: the files, then the `.env` file, then the environment**: a variable set in the process wins over
  the `.env` file (pydantic-settings and docker compose do the same: the file holds defaults for a machine,
  the environment what a run says).
- Its variables go through `read_env` like the environment's: the prefix, `__`, any case, unknown ones ignored.
  The process environment is never changed.
- **The format** (what docker compose and python-dotenv agree on): `KEY=VALUE` per line, blank lines and `#`
  lines ignored, an optional `export ` before the key. A value: bare (stripped; ` #` starts a comment),
  `'single quoted'` (as written), or `"double quoted"` (`\n`, `\t`, `\"`, `\\`; may run over several lines).
  A key given twice: the later line. UTF-8, a BOM allowed.
- **No interpolation** (`${HOME}`): the value is what is written, as in ini.
- A line that is no `KEY=VALUE`: a `ValueError` with the file and the line number, never the line's text.
- **A problem names the line**: `loc[0]` is `.env:12` (clickable, as the warning's place), so the message
  reads `.env:12: db.port: ...`. The head lists the file among the sources.
- Where: `_sources.read_env_file(path) -> {variable: (text, line)}`; `load` puts its variables in as
  layers before the environment's.
- Not now: several `.env` files (`.env`, `.env.local`), `env_file` on `@cfg`.

### 5. `@cfg(env_prefix = "APP_")`

- The prefix as an option of the class: `App.load("app.toml")` and `load_cfg("app.toml", App)` read
  `APP_*` without saying so. `env_prefix` given on the call wins over the class's; `env = {}` reads no
  variables (a test).
- The options become a small `NamedTuple` (`strict`, `frozen`, `env_prefix`) in `__cfg_options__`, read by name.
- Only the class that is loaded counts: a section's `env_prefix` is not read when it is built as a section
  (README note), and is used when that class is loaded by itself.
- `from_dict` reads no variables: it builds what it is given.
- A non-text prefix is a `TypeError` at the decorator. `dataclass_transform` is not affected; `cfg`'s
  overloads gain the parameter.

### 6. Durations (waits on enforced)
**Waits on enforced** (decided 2026-11-15): kept here, started when enforced parses durations.


`timeout: timedelta` takes no text today (`ttl: expected type: 'datetime.timedelta', got type 'str'`), and
the writers refuse a `timedelta`.

- **enforced first** (a check of a value: CLAUDE.md), as for dates: under `coerce`, text for a `timedelta`:
  ISO 8601 durations (`PT30S`, `P1DT2H`) and the short forms (`500ms`, `30s`, `5m`, `2h`, `1d`, combined
  `1h30m`, a leading `-`). Its own plan in enforced when this is next.
- Open, the owner's call: a number for a `timedelta` (TOML `timeout = 30`) as seconds, as pydantic does, or
  refused (seconds or milliseconds is a guess).
- cfgcast: `_plain` lets a `timedelta` through, and every writer writes it as text in the short form, the
  largest units first (`1h30m`, `500ms`, `-30s`): text in every format (none has a duration), read back by
  the rule above. A round trip test in every format, and a `cfgs/` case in every format.
- Not now: months and years (not a fixed length), a `PositiveDuration` (an `Annotated` check does it).

### 7. Checkers see `load` and `from_dict`
**Decided 2026-11-15: yes, a and b.** The base's name is still to choose (`Loadable` proposed; `CfgMethods`, `Loads`
are others).


A checker reads `@dataclass_transform` for the fields, but doesn't see members a decorator adds at run time:
`App.load(...)` is an error in mypy and pyright. The ways ("Methods" above):

- **a. A typed base that is not a configuration** (proposed): `@cfg class App(Loadable)`, whose classmethods
  are written for checkers (`def load(cls, file, ...) -> Self`). "Not inheritable" holds: it refuses a `@cfg`
  class among the bases, and `Loadable` isn't one. Opt-in: without it everything works as now, unseen by
  checkers. `@cfg` sees the base's members and doesn't copy its own over them.
- **b. Module functions only**: `load_cfg` is typed already; `from_dict(App, data)` would be its pair. No new
  base, but `App.load` stays unseen.
- Not: a metaclass (`class App(metaclass = ...)`: the decorator was the owner's choice), a base class that
  replaces the decorator (the same), a mypy plugin (pyright, VS Code's checker, has none), an intersection
  type (Python has none).
- Proposed: a and b both (b is a few lines). The name of the base: the owner's call.
- A test as enforced has (`tests/test_static_typing.py`): mypy on a sample file, and pyright if installed.

### 8. JSON serialisation

What there is: `write(app, "x.json")` (complete: dates, paths, `Decimal`, enums, a `SecretStr` as its real
text), and `ConfigError.json()` / `errors()` (secrets masked). What is missing, tried on 2026-11-15:

- **`json.dumps(to_dict(app))` fails** on a `Path`, a `datetime`, a `Decimal` or a `UUID` (`to_dict` keeps the
  values as they are), and **writes a `SecretStr`'s real text** where it doesn't fail: `json` encodes a `str`
  subclass by its characters, not its `__str__`. That is the first thing a user tries to log a configuration.
- No text without a file: printing the effective configuration, or sending it to a service, needs a file.
- enforced has `to_json` (masks secrets, dates, paths), but cfgcast neither exports nor mentions it, and its
  rules may part from `write`'s (`timedelta` after 6).

Plan:

- **`to_text(obj, cfg_type = "json", *, overrides = None, reveal_secrets = False) -> str`**: the text `write`
  would write, in any format, made by the same writers (one place for the rules), with **secrets masked
  unless `reveal_secrets`**. Masked by default because text is shown and logged; a file is where secrets go,
  so `write` keeps writing them. README: the difference, in one line.
- **`to_dict(obj, *, plain = False, reveal_secrets = False)`**: with `plain`, only what JSON holds (text,
  numbers, booleans, `None`, lists, dictionaries with text keys), the writers' `_plain` rules, secrets masked
  unless asked: for `json.dumps`, a web framework's response, a database column.
- README: the `json.dumps(to_dict(app))` trap, and what to use instead.
- `NaN` and the infinities: the JSON writer refuses them (`allow_nan = False`), but with `json`'s own message;
  one of cfgcast's that names the key.

### 9. `cfgcast.field`: what a field says beyond its type
Not asked for by itself: the place where 10, 14 and 15 put what they need to know about a field.

```python
from cfgcast import cfg, field

@cfg
class Pool:
    max_size: int = field(default = 10, key = "max-size", doc = "connections at most")
```

- `cfgcast.field(*, default, default_factory, key = None, doc = None, ...)`: `dataclasses.field` with cfgcast's
  options in its `metadata` (under one key, `"cfgcast"`), so enforced and `dataclasses` see a plain field.
- Listed in `dataclass_transform(field_specifiers = ...)` next to `dataclasses.field`, so checkers know its
  `default` / `default_factory` (a field without one is required, as now).
- `dataclasses.field` keeps working: `cfgcast.field` is needed only for its options.
- Public: `cfgcast.field`. Its options are checked at the decorator (a `key` that isn't text, two fields with
  one key: `TypeError`).

### 10. Keys that aren't Python names
`max-size`, `log-level`, `class`: common in TOML and YAML, and no field can have that name.

- Per field: `field(key = "max-size")`. Per class: `@cfg(keys = "kebab")` reads every field `max_size` as
  `max-size` (a field's own `key` wins). Only `"kebab"` at first; a function later if asked.
- **The key is what the file has**: unknown and missing keys, `loc` and the message say `max-size`; `write`
  writes it; `to_dict` gives it (`to_dict` is the file's shape: `write` and the overrides merge into it, and
  `from_dict(to_dict(app)) == app` must hold). Attributes stay Python names: `app.pool.max_size`.
- `from_dict` takes the keys, as a file does. The Python name in a dictionary is an unknown key (one name for
  one thing; the message could add "did you mean `max-size`?").
- Environment variables: `APP_POOL__MAX_SIZE` (a variable can't hold `-`): the key with `-` read as `_`,
  compared without case, as now.
- ini: keys are kept as written already. A `.` in a section's key is a `TypeError` at the decorator (in ini
  `[db.pool]` is nesting, and TOML reads a bare dotted key as nesting too); in a value's key it is fine.
- `__cfg_sections__` is by key. Two fields with one key (`max_size` and `max-size` under `kebab`): `TypeError`.
- Tests in every format, a `cfgs/` case in every format (rule 1).

### 11. A section of a bigger file
```python
app = load_cfg("pyproject.toml", App, section = "tool.myapp")
```

- `section` is a dotted path into the file; the configuration is built from what is there. Every other key of
  the file is not looked at (not unknown: the file belongs to others too).
- With layers, it holds for every file of the call. One section per file (`("pyproject.toml", "tool.myapp")`)
  later, if asked.
- **`loc` and the message keep the whole path** (`tool.myapp.db.port`), so the user finds it in the file.
- A section that isn't there: a `missing` problem at its path (with every key's default it would load:
  no, a missing section is always the problem, as a missing file is). A value there that isn't a table: a
  `section` problem.
- Every format: in ini a dotted section already is nesting (`[tool.myapp]`, `[tool.myapp.db]`).
- Environment variables are not affected (they name the configuration's keys, not the file's).
- Not for `write`: it would write the whole `pyproject.toml` again, without its comments. Refused with a
  message that says so.

### 12. Secrets from files
Docker, compose and kubernetes give a secret as a file (`/run/secrets/db_password`), and the
convention of the official images is a `_FILE` variable: `POSTGRES_PASSWORD_FILE`.

- **`APP_DB__PASSWORD_FILE=/run/secrets/db`**: with an `env_prefix`, a variable that names no field, but
  names one with `_FILE` taken off, gives that field the text of the file. A variable that names a field as
  it is stays that field (`APP_TLS__CERT_FILE` is `tls.cert_file` where there is one).
- The text: UTF-8, one trailing line break taken off (what `echo` and most tools write), nothing else.
- Any field, not only a `SecretStr`: a file is how a secret comes, but the field says what it is.
- Both `APP_DB__PASSWORD` and `APP_DB__PASSWORD_FILE`: a `ValueError` naming both variables (which is meant
  can't be guessed).
- A file that isn't there: `FileNotFoundError` naming the variable and the path (a path is no secret).
- The source of the value (a problem's `loc[0]`, `sources_of`): `APP_DB__PASSWORD_FILE`. Its input masked as
  any secret's; the text of the file never in a message.
- Then, if asked: `secrets_dir = "/run/secrets"` (pydantic's: each file named after a key, `db__password`).
- Tests: the variable, the trailing line break, both given, the file missing, a `_FILE` field of its own.

### 13. Where each value came from, with its line
Decided 2026-11-15: yes, **with the file and the line**.

```python
>>> sources_of(app)
{"name": "base.toml:1", "db.host": "prod.yaml:3", "db.port": "APP_DB__PORT", "debug": "(default)"}
```

- Each value's source: `path:line` for a file (clickable, as the warning's place), the variable for the
  environment, `.env:12` for a `.env` file (4), `APP_DB__PASSWORD_FILE` for a secret file (12), `(default)`.
- **The lines are the work**: none of the parsers says where a key is (`json`, `tomllib`, `configparser`;
  PyYAML does, in its nodes). One **locator per format**, run on the text after it parsed (so it only reads
  valid files): YAML from `yaml.compose` (each node's `start_mark.line`); ini by a scan of section headers and
  `key =` lines; JSON by a small scanner of its grammar (keys, strings, nesting); TOML by a scan of `[table]`,
  `[[table]]` and `key =` lines that skips multi-line strings and arrays. Where a locator can't tell (a TOML
  inline table, say), the file alone: never a wrong line.
- **Errors get the line too**, the same way: each problem's `ctx["line"]`, and the message says
  `app.toml:12: db.port: ...` (a click away). This changes today's message for one file
  (`    db.port: ...`): the owner's call whether one file gets the `path:line` prefix or only layered loads do.
  `loc` stays the file then the key path (pydantic's shape).
- `sources_of(app)`: the sources are kept on the loaded instance (an attribute that is no field: not in `==`,
  `repr` or `to_dict`; not kept by `dataclasses.replace`), and `sources_of` of an instance not loaded from
  files is a `ValueError`. A section: `sources_of(app.db)` with its own keys.
- A list of sections: each item's keys (`servers[1].port`). A list value: the line of its key.
- Cost: the locators run only where a line is wanted (on an error, or when the sources are kept: a
  `load_cfg(..., keep_sources = True)`?, the owner's call), so a load that goes well costs nothing more.

### 14. A template file
```python
write_template(App, "app.toml")
```

- Every key with its default as a value, every key without one as a comment to fill in
  (`# host = ""   # required: str`), each with its `doc` (9) as a comment above it, and the type when it
  says more than the value (`Port`, `Literal[...]`).
- Sections as sections; a list of sections with one item to copy, commented; a dictionary of sections with
  one named example.
- **A `SecretStr` default is never written**: a comment (`# password = "..."   # secret`), as for a required key.
- TOML, YAML and ini. **JSON is refused** (no comments: the required keys couldn't be said); the JSON Schema
  is the way for JSON, if it comes.
- **An existing file is not overwritten** (`FileExistsError`) unless `overwrite = True`: a template written over a
  real configuration would lose it.
- A template loads: with every required key filled in, `load_cfg` gives the defaults (a test in every format).
- Written whole or not at all (1).

### 15. Maybe, and to be explained
Not decided (2026-11-15). Kept so the reasons are at hand.

**Overrides at load time** (explanation asked). A value for one run, from code or the command line, without
editing a file:

```python
app = load_cfg("app.toml", App, overrides = {"db.port": 9090})          # from code
app = load_cfg("app.toml", App, overrides = parse_overrides(sys.argv[1:]))
# python run.py db.port=9090 debug=true servers=a,b
```

- The last layer, over files and environment: checked like a file, problems name `overrides` (or the
  argument, `db.port=9090`).
- What it is for: an experiment or a CI job changing one or two values (Hydra's `key=value` on the command
  line); tests building a configuration from a file with one difference. Today the way is an environment
  variable (`APP_DB__PORT=9090 python run.py`) or a second file in the layers.
- Against: two ways already exist (variables, layers); `parse_overrides` is a small command-line parser of its
  own, and the program has one (`argparse`).

**Renamed keys** (maybe): `field(was = "db_host")` takes the old key with a `ConfigWarning` naming the new one,
so a file written for an older version still loads. Both keys given: an error.

**A union with a section** (maybe): `db: Database | str` and `list[Server | None]` are built as the section only
today, so the other member is said to be "expected a section". Try the section, then the rest. Rare.

**Not proposed**: interpolation (`${db.host}`, `${env:HOME}`: a language of its own, and a value is no longer
what is written), reloading on change, `include` directives, keeping comments when writing, a JSON Schema
(proposed in the first list; not answered: here until it is).

## Later

- The static typing of `load` / `from_dict` beyond 7 (a plugin), if a and b are not enough.
- enforced: the `date` plan; then cfgcast's tests for it (in that plan).
