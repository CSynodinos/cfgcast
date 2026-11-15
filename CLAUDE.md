# cfgcast

Typed configuration files for Python 3.13+: a configuration is a class (`@cfg`), and `load_cfg` reads JSON, TOML,
ini/cfg or YAML into it, every value checked and converted, every problem reported at once with its file and key
path. Built on **enforced** (`/mnt/d/dev/enforced`, the owner's other project): cfgcast does what enforced doesn't
(files, sections, unknown and missing keys, writing), and leaves every check of a value to enforced. Its only
dependency is enforced; PyYAML is an optional extra, for YAML only.

It was called `configured` until 2026-11-01 (the owner's choice of the new name). It started as a prototype that
found bugs in enforced (CLAUDE.md there: `cfgcast` is named in "How it works"); a change that cfgcast needs in
enforced is made in enforced, with that project's rules, and not worked around here.

`README.md` is the user's guide, `PLAN.md` the decisions and what comes next. Read `PLAN.md` before a change.

## Commands

```bash
source env.sh                          # the venv: ~/venvs/cfgcast (3.14), or CFGCAST_VENV=~/venvs/cfgcast_3.13
python -m pytest -q tests              # unit tests (must all pass); 152 on 2026-11-14
python test_formats.py                 # the app written against cfgcast: loads every file in cfgs/, "0 failed, 0 skipped"
python -m mypy cfgcast                 # must stay clean
```

Run them on 3.13 and 3.14: `~/venvs/cfgcast/bin/python` and `~/venvs/cfgcast_3.13/bin/python`. To make either again:

```bash
pip install -e /mnt/d/dev/enforced -e ".[dev]" --config-settings editable_mode=compat
```

enforced is installed editable from `/mnt/d/dev/enforced`, so a change there is seen here at once. `editable_mode=compat`
is needed for mypy: the default editable install (an import hook) is invisible to it, and every `enforced` import
becomes `import-not-found`. Without PyYAML 13 tests skip; the enforced venvs (`~/venvs/python_validate_3.1x`) have
neither cfgcast's dependencies nor PyYAML, so don't judge cfgcast by a run there. `tests/test_formats_app.py` runs
`test_formats.py` as one test, so pytest covers it too. `basic_test.py` is the owner's scratch script, not a test.

The project lives in `/mnt/d/dev/cfgcast` (moved out of enforced's `prototypes/` on 2026-11-15), in its own git
repository. Commit only when asked; no `git stash`.

## Public API

`cfg`, `load_cfg`, `ConfigError`, `ConfigWarning`, `to_dict`, `write`, and enforced's `turn_colours_off` /
`turn_colours_on` (re-exported). Each `@cfg` class also gets `load` and `from_dict`.

- **`@cfg` / `@cfg(strict = True, frozen = True)`**: a decorator, not a base class (the owner's decision,
  2026-10-17). It makes the class an `enforced.dataclass(kw_only = True, frozen = ..., coerce = True, deep = True,
  error = ConfigError)`, in place (no slots, so `isinstance` and pickle see the class that was written).
  `coerce` and `deep` are always on, never options (a file is text, and nested).
- **`strict = False`** ignores unknown keys in that class *and every section below it*, whatever those say (the
  owner's decision, 2026-11-01: it was per class). What it ignores is named in one `ConfigWarning` (keys, never
  values); `show_warnings = False` on `load_cfg` / `load` / `from_dict` leaves it out. `write`'s overrides always warn.
- **`load_cfg(file, schema, cfg_type = "infer", *, show_warnings = True) -> T`**: typed (`type[T] -> T`), the way an
  editor understands. `"infer"` reads the suffix, in any case (`SUFFIXES`: `.cfg` is ini).
- **`write(obj, file, overrides = None, cfg_type = "infer")`**: every format, with writers of our own. Overrides go to
  the file only: merged into `to_dict(obj)`, built with the schema (so checked, converted, strict as for loading),
  written, dropped; `obj` never changes. Every key is written, also one equal to its default. A `SecretStr` is
  written as its real text. Nothing is written when anything is refused.
- **`ConfigError`** is an `enforced.InvalidDataError` *and* a `ValueError`. `errors()` / `json()` in enforced's
  (pydantic's) shape, `loc` = the file, then the key path. Types: `unknown`, `missing`, `section`, and enforced's own.
- **Not inheritable**: a class with a `@cfg` class among its bases is refused, at `@cfg` and at the class statement.

## Layout

```
cfgcast/
  __init__.py      the public names, __version__ (the one place the version is written: pyproject reads it)
  _errors.py       ConfigError, ConfigWarning, MASK, what is a secret (members, mentions_secret), the message
  _readers.py      read_json / read_toml / read_ini / read_yaml (CONFIG_READERS), CfgType, RawConfigData
  _writers.py      write_json / write_toml / write_ini / write_yaml (WRITERS), _plain
  _base.py         cfg, Config (the members cfg copies), to_dict, write, is_config, SUFFIXES, __cfg_sections__
  _build.py        build / problems_of: a dictionary to a schema instance, every problem gathered
  _load.py         load_cfg
tests/             pytest, one file per module (test_build, test_cfg, test_readers, test_writers, test_load_and_write),
                   test_blueprint (it imports), test_formats_app (runs test_formats.py)
test_formats.py    an application written against cfgcast as a user would: every file in cfgs/, 45 checks
cfgs/              simple.* and complex.* in every format, the same data in each (cfgs/README.md: the format traps in them)
```

Imports go `_errors` <- `_readers`, `_writers` <- `_base` <- `_build`, `_load`. `_build` imports `_base`, so `_base`
imports `build` inside the functions that need it (`load`, `from_dict`, `write`).

## How it works (read before changing anything)

- **`cfg`** (`_create_the_config_object`) checks the class first (`_validate_class_input`: a class, not a dataclass
  or configuration already, no configuration among its bases, no field or member of its own named `load` /
  `from_dict`), stores the options as `__cfg_options__` (`(strict, frozen)`; `is_config` asks `vars`, not `getattr`:
  an undecorated subclass is no configuration), calls `enforced.dataclass`, copies `Config.load` / `Config.from_dict`
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
- **Secrets are never shown.** A value's input is masked (`MASK`) where its field's annotation has a `SecretStr`
  anywhere in it (`mentions_secret`: unions, `Annotated`, aliases, `list[SecretStr]`). An unknown key has no `input`
  at all: nothing says its value isn't a secret. `msg` never holds a value. Both doors are covered: a file (`_build`)
  and a direct construction or assignment (`App(name = 1)`, which enforced raises as a `ConfigError` through
  `error = ConfigError`; `ConfigError._enforced_entries` masks those).
- **`ConfigError` made by enforced** never runs `__init__` (enforced's rule for an `error =` class), so `file`,
  `schema` and `entries` are class attributes (`None`) and `errors()` / `message` come from enforced's problems,
  their `loc`s without a file. `__reduce__` handles both kinds.
- **`ConfigWarning`** is an `enforced.InvalidDataWarning`: `str()` is plain (a filter's `message` regex reads it),
  `shown()` is coloured (enforced's warning formatter calls it). It is issued with `skip_file_prefixes` of the
  package's own directory, so it points at the caller's line whichever way it was loaded. It comes before a
  `ConfigError` of the same file: a `prot` ignored explains a `port` missing.
- **Readers** know no schema, except `read_ini`, which needs it to split lists (`_split_lists`, only where the
  field is a `list[...]` and friends: `_LISTS`). ini: `[DEFAULT]` is the top level and *not* copied into other
  sections (`default_section` set to a name nobody writes), a dotted section is nesting, keys as written
  (`optionxform = str`), no interpolation. YAML: `safe_load` with a `SafeLoader` whose bool resolver takes only
  `true` / `false` (YAML 1.2): `country: no` stays `"no"`. PyYAML is imported inside `read_yaml` only.
- **Writers** get plain data (`_plain`: a `TypeModel` as its base value, `SecretStr` through `str.__str__` for the real
  text, an `Enum` as its value, paths / `Decimal` / `UUID` as text, anything else a `TypeError`). The whole text is
  made before the file is opened. TOML and ini have no null: a `None` is left out, and loading gives the default.
  YAML quotes any string a 1.1 or 1.2 reader would take for something else (`_YAML_SPECIAL`, `_YAML_FIRST`). ini
  refuses what it can't hold (a line break, surrounding spaces, a list of sections, a comma in a list item).

## What cfgcast relies on in enforced

`enforced.dataclass(..., coerce, deep, error = ...)`, `InvalidDataError.errors()` and its entry shape, `Problem`
(`.parameter`, `.annotation`, `.origin`), a subclass's `__init__` never being run, `InvalidDataWarning.shown()`,
`enforced.data_types.SecretStr`, and one private name: `enforced._formatting_tool.formatter`, imported in
`_errors.py` for the warning's colours. A change to any of these in enforced is a change to cfgcast: run its tests.
enforced's coercion also has a rule made for cfgcast (2026-10-18): a `date` for a `datetime` field is its midnight
(YAML reads an unquoted `2026-10-17` as a date).

## Rules for changes

1. **Run the tests, `test_formats.py` and mypy, on 3.13 and 3.14.** New behaviour needs a test, and a README note
   where a user sees it. A new format rule gets a case in `cfgs/` (in every format) and a check in `test_formats.py`.
2. **Every problem at once.** Nothing is raised at the first problem: `_build` gathers, then raises one `ConfigError`.
3. **No value of a secret anywhere**: a message, `errors()`, a warning, `repr`. A new kind of problem says whether
   it carries an `input`, and is masked by `mentions_secret` if it does.
4. **A file is written whole or not at all.**
5. Style as in enforced: 4-space indent, `x = y` spacing, `kwarg = value` with spaces, `#*` for important comments,
   docstrings on public functions, type hints everywhere.

## Known issues and open questions

- **Checkers don't see `App.load` / `App.from_dict`** (members a decorator adds at run time): the README says so and
  points to `load_cfg`. The ways out are in `PLAN.md` ("Methods").
- **Planned, in this order** (`PLAN.md`, "Next features"): lists and dicts of sections (`servers: list[Server]`,
  TOML `[[servers]]`), dates in the writers, layered files (`load_cfg(["base.toml", "prod.toml"], App)`, later wins,
  errors naming the file a value came from), environment variables (`env_prefix = "APP_"`, `__` between levels,
  unknown variables ignored also under `strict`). Item 5 (`ConfigError` raised by the class itself) is built.
- `_build._section` takes any dataclass as a section, not only a `@cfg` class; `_Sections` only `@cfg` ones.
