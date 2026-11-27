# Changelog

All notable changes to cfgcast. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project uses [semantic versioning](https://semver.org/) (before 1.0, a minor or patch release may still change
the API; such changes are listed under **Changed** and marked **breaking**).

## [0.1.1] - 2026-11-27

### Changed

- **Colours are optional.** clitint is no longer a dependency: `pip install "cfgcast[colour]"` installs it and
  colours messages on a terminal, as `enforced[colour]` does. Without it every message is plain text, and
  `turn_colours_off` / `turn_colours_on` are still there and do nothing. If you want coloured messages, install
  the `colour` extra: an upgrade from 0.1.0 without it gives plain ones.

## [0.1.0] - 2026-11-27

The first release. Requires Python 3.13 or later and enforced 0.2.0.

### Added

- **Configurations as classes.** A class below `Config` is a schema: each annotated attribute is a key, its type
  says what the value must be, a default makes it optional. Every value is checked and converted by enforced
  (`coerce` and `deep` always on). Instances are frozen, keyword-only dataclasses.
- **Options as class keywords**: `strict` (unknown keys refused, the default; `False` ignores them with a
  warning), `frozen`, `env_prefix`, and `keys = "kebab-case"`.
- **Inheritance**: a class below a configuration is one too, with the fields above it and its own. `strict`,
  `env_prefix` and `keys` are inherited; `frozen` is set by each class.
- **Sections**: a field whose type is another configuration (or a plain dataclass) is a nested table. Lists,
  tuples and dictionaries of sections (`list[Server]`, `dict[str, Server]`) are built item by item.
- **Formats**: JSON, TOML, ini (`.ini`, `.cfg`) and YAML (with `cfgcast[yaml]`), read and written. YAML is read
  with YAML 1.2 booleans (`no` stays `"no"`). ini gets nesting (`[db.pool]`) and lists (`a, b, c`).
- **Loading** with `App.load(file)` or `App.from_dict(data)`, both typed for checkers (`App.load` gives an
  `App`):
  - several files in layers, a later file winning; `optional("local.toml")` for one that may be absent;
  - environment variables with a prefix (`APP_DB__PORT` is `db.port`), a `.env` file, and secrets from files
    (`APP_DB__PASSWORD_FILE`);
  - `section = "tool.myapp"` for one table of a bigger file such as `pyproject.toml`;
  - `overrides` for one run, and `parse_overrides(sys.argv[1:], App)` to make them from `db.port=9090` texts.
- **Every problem at once**: one `ConfigError` with each problem's source, line and key path
  (`app.toml:12: db.port: ...`), and `errors()` / `json()` in pydantic's shape. An exception raised by a
  `__post_init__` is a problem like the others.
- **Secrets are never shown**: a `SecretStr` value is masked in every message, `errors()`, warning and `repr`;
  an unknown key's value is never shown at all.
- **`sources_of(app)`**: where each value came from, by key path (`file:line`, a variable, `overrides`, or
  `(default)`).
- **Writing**: `write(app, file)` in any format, every key written, the file replaced whole or not at all.
  `to_text(app)` gives the same text with secrets masked; `to_dict(app, plain = True)` only what JSON holds.
- **Templates**: `write_template(App, "app.toml")` writes every key with its default, type and doc, required
  keys and secrets commented out (TOML, YAML, ini). `field(doc = ...)` adds a line above a key.
- **`field(key = ...)`** for keys that aren't Python names (`max-size`, `class`).
- **Dates and durations**: dates and times in every format; a `timedelta` is text with a unit (`1h30m`,
  `PT30S`), written in the short form. A `date` field takes a day, not a moment.
- **`FrozenConfigError`** for an assignment to a frozen configuration: a `dataclasses.FrozenInstanceError` that
  names the key path, never the value.
- **`ConfigWarning`** names the keys `strict = False` ignored, and where the class is written.
- **Coloured messages** on a terminal (clitint), switched off with `CLITINT_COLOURS_OFF=1`, `NO_COLOR` or
  `turn_colours_off()`.

[0.1.1]: https://github.com/CSynodinos/cfgcast/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/CSynodinos/cfgcast/releases/tag/v0.1.0
