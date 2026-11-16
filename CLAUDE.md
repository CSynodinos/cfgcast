# cfgcast

Typed configuration files for Python 3.13+: a configuration is a class (`@cfg`), and `load_cfg` reads JSON, TOML,
ini/cfg or YAML into it, every value checked and converted, every problem reported at once with its file and key
path. Built on **enforced** (`/mnt/d/dev/enforced`, the owner's other project): cfgcast does what enforced doesn't
(files, sections, unknown and missing keys, writing), and leaves every check of a value to enforced. Its dependencies
are enforced and clitint (colours); PyYAML is an optional extra, for YAML only.

It was called `configured` until 2026-11-01 (the owner's choice of the new name). It started as a prototype that
found bugs in enforced (CLAUDE.md there: `cfgcast` is named in "How it works"); a change that cfgcast needs in
enforced is made in enforced, with that project's rules, and not worked around here.

`README.md` is the user's guide, `PLAN.md` the decisions and what comes next. Read `PLAN.md` before a change.

## Commands

```bash
source env.sh                          # the venv: ~/venvs/cfgcast (3.14), or CFGCAST_VENV=~/venvs/cfgcast_3.13
python -m pytest -q tests              # unit tests (must all pass); 381 on 2026-11-16
python tests/simulation.py             # the app written against cfgcast: loads every file in cfgs/, "0 failed, 0 skipped"
python -m mypy cfgcast                 # must stay clean
bash run_tests.sh --simulation         # the owner's script: pytest (verbose), then the simulation
```

Run them on 3.13 and 3.14: `~/venvs/cfgcast/bin/python` and `~/venvs/cfgcast_3.13/bin/python`. To make either again:

```bash
pip install -e /mnt/d/dev/clitint -e /mnt/d/dev/enforced -e ".[dev]" --config-settings editable_mode=compat
```

enforced is installed editable from `/mnt/d/dev/enforced`, so a change there is seen here at once. `editable_mode=compat`
is needed for mypy: the default editable install (an import hook) is invisible to it, and every `enforced` import
becomes `import-not-found`. Without PyYAML some tests skip; the enforced venvs (`~/venvs/python_validate_3.1x`) have
neither cfgcast's dependencies nor PyYAML, so don't judge cfgcast by a run there. `tests/simulation.py` was
`test_formats.py` until 2026-11-15 (the owner moved it, and took out the pytest test that ran it): pytest does **not**
cover it any more, so run it by itself. `basic_test.py` is the owner's scratch script, not a test.

The project lives in `/mnt/d/dev/cfgcast` (moved out of enforced's `prototypes/` on 2026-11-15), in its own git
repository. Commit only when asked; no `git stash`.

## Public API

`cfg`, `field`, `Loadable`, `load_cfg`, `from_dict`, `optional`, `parse_overrides`, `sources_of`, `to_dict`, `to_text`,
`write`, `write_template`, `ConfigError`, `ConfigWarning`, and clitint's `turn_colours_off` / `turn_colours_on`
(re-exported; colours follow clitint's variables, `CLITINT_COLOURS_OFF`, `NO_COLOR` ...). Each `@cfg` class also gets
`load` and `from_dict`.

- **`@cfg` / `@cfg(strict = True, frozen = True, env_prefix = None, keys = None)`**: a decorator, not a base class (the owner's decision,
  2026-10-17). It makes the class an `enforced.dataclass(kw_only = True, frozen = ..., coerce = True, deep = True,
  error = ConfigError)`, in place (no slots, so `isinstance` and pickle see the class that was written).
  `coerce` and `deep` are always on, never options (a file is text, and nested).
- **`strict = False`** ignores unknown keys in that class *and every section below it*, whatever those say (the
  owner's decision, 2026-11-01: it was per class). What it ignores is named in one `ConfigWarning` (keys, never
  values); `show_warnings = False` on `load_cfg` / `load` / `from_dict` leaves it out. `write`'s overrides always warn.
- **`env_prefix`** on the class is `load_cfg`'s, for every load of that class (the call's wins; a section's is not read;
  `from_dict` reads none). **`keys = "kebab-case"`**: `max_size` is the key `max-size`, in that class only.
- **`field(key =, doc =)`**: `dataclasses.field` with the two in its metadata (under `"cfgcast"`). **The key is what a
  file has**: `loc`, messages, `write`, `to_dict`, `from_dict`, `__cfg_sections__` use it; the attribute keeps its name.
- **`Loadable`**: the class that holds `load` / `from_dict`; as a base (`@cfg class App(Loadable)`) it is for checkers
  only, and no configuration. `from_dict(App, data)` is the typed function.
- **`load_cfg(file, schema, cfg_type = "infer", *, env_prefix = None, env = None, env_file = None, section = None,
  overrides = None, show_warnings = True) -> T`**:
  typed (`type[T] -> T`), the way an editor understands. `"infer"` reads the suffix, in any case (`SUFFIXES`: `.cfg`
  is ini). `file` may be a list, read in **layers**: sections merge, a later value replaces (a list too), unknown and
  missing keys are checked once on the result. `env_prefix = "APP_"` lays **environment variables** over the files
  (`APP_DB__PORT` is `db.port`, case ignored, a variable naming no field ignored also under `strict`); `env` is
  `os.environ` unless given; `file = []` with a prefix is the environment alone. `App.load` takes the same.
  **The order: files < `env_file` < the environment < `overrides`.** `optional("local.toml")` marks a file (also the
  `env_file`) that may be absent: only "nothing there" is skipped. `env_file`: `KEY=VALUE` lines, no interpolation, a
  problem names `.env:12`. A variable `APP_DB__PASSWORD_FILE` that names no field gives `db.password` the text of that
  file (both set: a `ValueError`). `section = "tool.myapp"` reads that table of every file; `loc` keeps the whole path;
  a file without it is a `missing` problem. `overrides` is a nested dictionary (`parse_overrides(argv, App)` makes one
  from `db.port=9090` texts), its problems name `overrides`.
- **`sources_of(obj)`**: `{key path: "file:line" | variable | "overrides" | "(default)"}` of a loaded configuration
  (a `ValueError` for one that wasn't loaded). Names only.
- **Sections of several**: `list[X]`, `tuple[X, ...]`, `dict[str, X]` (`X` a section) are built item by item, `loc`
  with the index or key (`servers[2].port`). ini holds the dictionary (`[by_region.eu]`), not the list.
- **`write(obj, file, overrides = None, cfg_type = "infer")`**: every format, with writers of our own. Overrides go to
  the file only: merged into `to_dict(obj)`, built with the schema (so checked, converted, strict as for loading),
  written, dropped; `obj` never changes. Every key is written, also one equal to its default. A `SecretStr` is
  written as its real text. Dates and times in every format. Nothing is written when anything is refused, and the
  file is replaced atomically (`_save`: a temporary file next to it, `os.replace`).
- **`to_text(obj, cfg_type = "json", *, overrides, reveal_secrets = False)`**: what `write` would write, **secrets
  masked** unless asked. **`to_dict(obj, plain = True)`**: only what JSON holds, secrets masked unless `reveal_secrets`.
- **`write_template(schema, file, cfg_type = "infer", *, overwrite = False)`**: every key, TOML / YAML / ini (JSON is
  refused: no comments). A key with a default is written; a required one, a `None` default and **every secret** are
  commented out as `#key = placeholder` (no space; a note is `# note`). A file that is there is kept unless `overwrite`.
- **`ConfigError`** is an `enforced.InvalidDataError` *and* a `ValueError`. `errors()` / `json()` in enforced's
  (pydantic's) shape, `loc` = the source (the file, or the variable, a value came from), then the key path. Types:
  `unknown`, `missing`, `section`, `post_init` (an exception of a `__post_init__`, no `input`), and enforced's own.
  Each line of the message starts with `source:line: ` where the line is known (`ctx["line"]`; never in `loc`). `files` is the tuple of sources, `file` their text; with more
  than one, each line of the message starts with its source (`    prod.toml: db.port: ...`).
- **Not inheritable**: a class with a `@cfg` class among its bases is refused, at `@cfg` and at the class statement.

## Layout

```
cfgcast/
  __init__.py      the public names, __version__ (the one place the version is written: pyproject reads it)
  _hints.py        what an annotation says: members, mentions_secret, section_of (one / list / tuple / dict), is_list, is_dict;
                   Options, and a field's key (key_of, keys_of, said, styled)
  _errors.py       ConfigError, ConfigWarning, MASK, the message (one source, or several)
  _readers.py      read_json / read_toml / read_ini / read_yaml (CONFIG_READERS), CfgType, RawConfigData
  _writers.py      write_json / write_toml / write_ini / write_yaml (WRITERS), _plain
  _locate.py       the line of each key of a file, one locator per format: {key path: line}, {} when it can't tell
  _template.py     write_template's text: a schema as a tree (_Value, _Section), then one renderer per format
  _base.py         cfg, field, Loadable (the members cfg copies), from_dict, to_dict, to_text, sources_of, write,
                   write_template, is_config, SUFFIXES, __cfg_sections__
  _sources.py      Sources (which file or variable gave each key path, and its line), layer (the merge), read_env,
                   read_env_file, optional, the _FILE variables, field_of / text_value (parse_overrides)
  _build.py        build / problems_of: a dictionary to a schema instance, every problem gathered
  _load.py         load_cfg, load (what it and the `load` member do: files, layers, a section, .env, the environment,
                   overrides), parse_overrides
tests/             pytest, one file per module (test_build, test_cfg, test_readers, test_writers, test_load_and_write,
                   test_sources: layers and the environment, test_locate) and per feature (test_keys, test_section,
                   test_overrides, test_text, test_template), test_static_typing (mypy on tests/typing_samples/),
                   test_blueprint (it imports)
tests/simulation.py  an application written against cfgcast as a user would: every file in cfgs/, 87 checks (not a pytest test)
run_tests.sh       the owner's runner: pytest, and the simulation with --simulation
cfgs/              simple.*, complex.*, schedule.*, keys.* in every format, fleet.* (no ini), layers/ (cfgs/README.md: what each holds)
```

Imports go `_hints` <- `_errors` <- `_readers` <- `_writers` <- `_sources`, `_template` <- `_base` <- `_build`, `_load`
(`_locate` imports `_readers` inside its YAML locator only). `_build` and
`_load` import `_base`, so `_base` imports them inside the functions that need them (`load`, `from_dict`, `write`).

## How it works (read before changing anything)

- **`cfg`** (`_create_the_config_object`) checks the class first (`_validate_class_input`: a class, not a dataclass
  or configuration already, no configuration among its bases, no field or member of its own named `load` /
  `from_dict`, no two fields with one key), stores the options as `__cfg_options__` (`_hints.Options`: `strict`,
  `frozen`, `env_prefix`, `keys`; `is_config` asks `vars`, not `getattr`:
  an undecorated subclass is no configuration), calls `enforced.dataclass`, copies `Loadable.load` / `Loadable.from_dict`
  onto the class (the classmethod objects, so they bind to it), and sets `__init_subclass__` to `_not_inherited`.
  `@dataclass_transform(kw_only_default, frozen_default, field_specifiers)` on `cfg` is what checkers read.
- **Sections** (`__cfg_sections__`, `_Sections`): worked out at the first read, not at decoration, because an
  annotation may name a section written further down. The descriptor replaces itself on the class with the
  dictionary. On 3.13 such a name must be quoted (`db: "Database"`): 3.13 evaluates annotations at the class
  statement. `_annotated_names` is the one place that asks the Python version.
- **`_build`** makes one level at a time: keys sorted into known and unknown, each section built first (its
  problems kept, the key left out of the arguments if it failed, so enforced doesn't call it missing again),
  missing keys found here, then the class constructed **once** with what is left. enforced reports every wrong
  value of that construction at once (`error.errors()`); its `missing` entries are dropped (said already), and
  each `loc` gets this level's path in front. `strict` is `options[0] and not lenient`; `lenient` passes down.
  A field of sections (`_hints.section_of`) goes through `_sections`: one, or each item of a list / dictionary. What
  isn't a section where one belongs is a `section` problem **without an `input`** (it may hold that section's secrets).
- **A `__post_init__` that raises** (anything but an `InvalidDataError`, and no `BaseException`) is a `post_init`
  problem at its section's path, gathered with the rest; the first is the `ConfigError`'s `__cause__`. The real text of
  that class's `SecretStr` arguments is replaced by `MASK` in its message (`_secret_texts`): a short secret is masked
  wherever its letters stand (`re**********lica`), which is the price of never showing one.
- **Lines** (`_locate.py`): each file is read once (`text_of`), parsed (`PARSERS`), and its keys located in the same
  text. **A wrong line is worse than none**: a locator that fails gives `{}`, a path the data doesn't have is dropped,
  a key without a line takes that of the nearest key above it. `Sources.lines` holds them; `_errors._from` writes
  `source:line: `. Always tracked (the owner's decision: it is cheap).
- **`sources_of`**: `build` sets `__cfg_sources__` (`(Sources, key path)`) on every instance it makes, with
  `object.__setattr__` (frozen classes). It is in the instance's `__dict__`: not a field (no `==`, `repr`, `to_dict`),
  pickled and copied with the instance, lost by `dataclasses.replace`.
- **Sources** (`_sources.py`): `build` takes a file name or a `Sources`. `layer` merges one source over the data and
  notes, per key path, who gave the value (a whole value noted replaces what was noted below it); `Sources.of(path)`
  is the nearest note above a path, and a missing key names `Sources.last` (the last *file*). One file without
  variables takes the old way, so its messages are unchanged. Each environment variable is a layer of its own
  (`read_env`, sorted by name), so `loc[0]` is the variable. Every format is resolved before any file is read.
- **Secrets are never shown.** A value's input is masked (`MASK`) where its field's annotation has a `SecretStr`
  anywhere in it (`mentions_secret`: unions, `Annotated`, aliases, `list[SecretStr]`). An unknown key has no `input`
  at all: nothing says its value isn't a secret. `msg` never holds a value. Both doors are covered: a file (`_build`)
  and a direct construction or assignment (`App(name = 1)`, which enforced raises as a `ConfigError` through
  `error = ConfigError`; `ConfigError._enforced_entries` masks those).
- **`ConfigError` made by enforced** never runs `__init__` (enforced's rule for an `error =` class), so `file`,
  `schema` and `entries` are class attributes (`None`) and `errors()` / `message` come from enforced's problems,
  their `loc`s without a file. `__reduce__` handles both kinds.
- **`ConfigWarning`** is an `enforced.InvalidDataWarning`: `str()` is plain (a filter's `message` regex reads it),
  `shown()` is coloured (enforced's warning formatter calls it). Its head names the schema and where it is written,
  `App (path:line)`, clickable in an editor (`_located`: `__firstlineno__` and `inspect.getsourcefile`, left out when either is missing).
  It is issued with `skip_file_prefixes` of the package's own directory, so it points at the caller's line whichever way it was loaded. It comes before a
  `ConfigError` of the same file: a `prot` ignored explains a `port` missing.
- **Readers** know no schema, except `read_ini`, which needs it to split lists (`_split_lists`, only where the
  field is a `list[...]` and friends: `_hints.is_list`; it goes into sections and dictionaries of sections, and
  text for a list of sections is a `ValueError` naming the field). ini: `[DEFAULT]` is the top level and *not* copied into other
  sections (`default_section` set to a name nobody writes), a dotted section is nesting, keys as written
  (`optionxform = str`), no interpolation. YAML: `safe_load` with a `SafeLoader` whose bool resolver takes only
  `true` / `false` (YAML 1.2): `country: no` stays `"no"`. PyYAML is imported inside `read_yaml` only.
- **Templates** (`_template.py`): the schema becomes a tree that knows no format, then `_toml` / `_ini` / `_yaml` write
  it with the writers' own rules for a key and a value. A secret is never written (`mentions_secret`, and
  `_plain(default, True)` for one hidden in a default). A schema that holds itself is cut after one example. YAML: a
  section with nothing active below it would be null, so it is `key: {}` or commented out whole (the comments there say which).
- **Writers dispatch on the type with `enforced.dispatch`** (the owner's rewrite, 2026-11-16): `_plain_of`, `_json_of`,
  `_key_of`, `_toml_value`, `_ini_text`. **The dispatchers are made once, at module level**: one made inside the
  function that calls it is built again at every call (300 µs against 0.2 µs; 10 000 keys took 10 s). What a handler
  needs besides the value (`mask`, `where`) is a second argument, not a closure.
- **Writers** get plain data (`_plain`: a `TypeModel` as its base value, `SecretStr` through `str.__str__` for the real
  text, an `Enum` as its value, paths / `Decimal` / `UUID` as text, `date` / `time` / `datetime` as they are for the
  writer to say in its format, a key that isn't text made text (`_key`), anything else a `TypeError`). The whole text
  is made **and encoded** before the file is opened (`_save`). TOML and ini have no null: a `None` is left out, and loading gives the default.
  YAML quotes any string a 1.1 or 1.2 reader would take for something else (`_YAML_SPECIAL`, `_YAML_FIRST`). ini
  refuses what it can't hold (a line break, surrounding spaces, a list of sections, a comma in a list item, `[""]`),
  in messages that name the key and never the value: `write` hands the writers a `SecretStr` as its real text.

## What cfgcast relies on in enforced

`enforced.dataclass(..., coerce, deep, error = ...)`, `InvalidDataError.errors()` and its entry shape, `Problem`
(`.parameter`, `.annotation`, `.origin`), a subclass's `__init__` never being run, `InvalidDataWarning.shown()`,
`enforced.dispatch` (the writers: `@dispatch`, `.register`, the handler chosen by the first argument's annotation, the
most specific one: `bool` before `int`, an `Enum` before `str`), `enforced.data_types.SecretStr`, and clitint (a dependency of its own, as of enforced, from 2026-11-15: `/mnt/d/dev/clitint`): `formatter` in `_errors.py` for the warning's colours, and the two switches. A change to any of these in enforced is a change to cfgcast: run its tests.
enforced's coercion also has a rule made for cfgcast (2026-10-18): a `date` for a `datetime` field is its midnight
(YAML reads an unquoted `2026-10-17` as a date).

## Rules for changes

1. **Run the tests, `tests/simulation.py` and mypy, on 3.13 and 3.14.** New behaviour needs a test, and a README note
   where a user sees it. A new format rule gets a case in `cfgs/` (in every format) and a check in `tests/simulation.py`.
2. **Every problem at once.** Nothing is raised at the first problem: `_build` gathers, then raises one `ConfigError`.
3. **No value of a secret anywhere**: a message, `errors()`, a warning, `repr`. A new kind of problem says whether
   it carries an `input`, and is masked by `mentions_secret` if it does. The same for a `ValueError` / `TypeError`
   of a reader, a writer or `read_env`: it names the key or the variable, never the value.
4. **A file is written whole or not at all.**
5. Style as in enforced: 4-space indent, `x = y` spacing, `kwarg = value` with spaces, `#*` for important comments,
   docstrings on public functions, type hints everywhere.

## Known issues and open questions

- **Checkers don't see `App.load` / `App.from_dict`** unless the class names `Loadable` as its base (members a
  decorator adds at run time): the README says so, and points to `load_cfg` / `from_dict`.
- **Built on 2026-11-15** (`PLAN.md`, the first "Next features"): lists and dicts of sections, dates in the writers,
  layered files, environment variables.
- **Built on 2026-11-16** (`PLAN.md`, "Next features (planned 2026-11-15)", 1 to 5 and 7 to 14, and load-time
  overrides): atomic writes, `__post_init__` problems, `optional`, `.env` files, `@cfg(env_prefix = ...)`, `Loadable` and
  `from_dict`, `to_text` and `to_dict(plain = True)`, `cfgcast.field`, keys, `section`, `_FILE` secrets, lines and
  `sources_of`, `write_template`, `overrides` and `parse_overrides`.
- **Next** (`PLAN.md`, "What is left"): durations (waiting on enforced). Maybe: renamed keys, unions with a section.
  Open questions from the review of 2026-11-16 are listed there too.
- `json.dumps(to_dict(app))` still writes a `SecretStr`'s real text: `to_dict(app, plain = True)` is the way (README).
- **Decided 2026-11-15**: a file not marked `optional` must be there (no `missing_ok`).
- **enforced**: a `date` field keeps a `datetime` it is given, so `write` then `load_cfg` of a `date` with a time fails.
  Planned in `/mnt/d/dev/enforced/date_plan_cfgcast.md`.
- `_hints.section_of` takes any dataclass as a section, not only a `@cfg` class; `_Sections` only `@cfg` ones.
