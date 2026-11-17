"""Several sources for one configuration: files in layers, and environment variables over them (`_sources`, `_load`)."""
from dataclasses import field
from pathlib import Path
from typing import Annotated
import json
import pickle
import warnings

import pytest

from enforced.data_types import Port, SecretStr
from cfgcast import Config, ConfigError, ConfigWarning, optional
from cfgcast._sources import Sources, layer, read_env, read_env_file
import os


class Pool(Config):
    size: int = 1
    hosts: list[str] = field(default_factory = list)


class Db(Config):
    host: str
    port: Port = 5432
    password: SecretStr | None = None
    pool: Pool = field(default_factory = Pool)


class App(Config):
    name: str
    db: Db
    debug: bool = False
    tags: list[str] = field(default_factory = list)
    limits: dict[str, int] = field(default_factory = dict)
    replicas: list[Db] = field(default_factory = list)
    keys: list[SecretStr] = field(default_factory = list)


class Loose(Config, strict = False):
    name: str
    db: Db


def _files(tmp_path: Path, **layers: dict) -> list[Path]:
    paths = []
    for name, data in layers.items():
        paths.append(tmp_path / f"{name}.json")
        paths[-1].write_text(json.dumps(data))
    return paths


def _error(file, schema, **kwargs) -> ConfigError:
    with pytest.raises(ConfigError) as info:
        schema.load(file, **kwargs)
    return info.value


# ── Sources, layer ──────────────────────────────────────────────────────────

def test_layer_merges_sections_replaces_values_and_notes_who_gave_them():
    sources = Sources(("a", "b"))
    data: dict = {}
    layer(data, {"name": "x", "tags": [1, 2], "db": {"host": "h", "pool": {"size": 1}}}, "a", sources)
    layer(data, {"tags": [3], "db": {"pool": {"size": 2, "hosts": ["p"]}}, "new": {"k": 1}}, "b", sources)
    assert data == {"name": "x", "tags": [3], "db": {"host": "h", "pool": {"size": 2, "hosts": ["p"]}}, "new": {"k": 1}}
    assert [sources.of(path) for path in (("name",), ("tags",), ("tags", 0), ("db", "host"), ("db", "pool", "size"),
                                         ("db", "pool", "hosts", 0), ("new", "k"))] == ["a", "b", "b", "a", "b", "b", "b"]
    assert sources.of(("nowhere",)) == "b" and sources.of(()) == "b"         # not noted: the last


def test_a_value_that_replaces_a_section_takes_over_what_was_noted_below():
    sources = Sources(("a", "b", "c"))
    data: dict = {}
    layer(data, {"db": {"host": "h"}}, "a", sources)
    layer(data, {"db": {"port": 1}}, "b", sources)
    layer(data, {"db": "flat"}, "c", sources)
    assert data == {"db": "flat"} and sources.of(("db",)) == "c" and sources.of(("db", "port")) == "c"


# ── layers ──────────────────────────────────────────────────────────────────

def test_later_files_win_section_by_section_and_a_list_is_replaced(tmp_path):
    files = _files(tmp_path,
                   base = {"name": "shop", "tags": ["a", "b"], "db": {"host": "h", "pool": {"size": 2, "hosts": ["x"]}}},
                   prod = {"tags": ["c"], "db": {"port": 9090, "pool": {"size": 5}}},
                   local = {"debug": True})
    app = App.load(files)
    assert app == App(name = "shop", debug = True, tags = ["c"], db = Db(host = "h", port = 9090, pool = Pool(size = 5, hosts = ["x"])))
    assert App.load(tuple(str(f) for f in files)) == app            # the member; a tuple; text paths
    assert App.load(files[:1]).tags == ["a", "b"]              # a list of one is that file


def test_each_file_is_read_in_its_own_format_or_all_in_the_one_named(tmp_path):
    (tmp_path / "base.toml").write_text('name = "shop"\n[db]\nhost = "h"\n')
    (tmp_path / "over.ini").write_text("[DEFAULT]\ntags = a, b\n\n[db]\nport = 81\n")
    app = App.load([tmp_path / "base.toml", tmp_path / "over.ini"])
    assert app.tags == ["a", "b"] and app.db.port == 81 and app.db.host == "h"
    (tmp_path / "one.conf").write_text('name = "shop"\n')
    (tmp_path / "two.conf").write_text('[db]\nhost = "h"\n')
    assert App.load([tmp_path / "one.conf", tmp_path / "two.conf"], "toml").db.host == "h"
    with pytest.raises(ValueError, match = "cfg_type"):             # before anything is read: base.toml is fine
        App.load([tmp_path / "base.toml", tmp_path / "one.conf"])


def test_strict_and_missing_are_checked_once_on_the_result(tmp_path):
    files = _files(tmp_path, base = {"name": "shop"}, prod = {"db": {"host": "h"}})
    assert App.load(files).db.host == "h"                      # neither file is whole; together they are
    with pytest.raises(ConfigError):
        App.load(files[:1])


def test_a_problem_names_the_file_its_value_came_from(tmp_path):
    base, prod = _files(tmp_path,
                        base = {"name": 1, "typo": 1, "tags": ["a", 2], "db": {"port": 0, "pool": {"size": "x"}}},
                        prod = {"debug": "maybe", "db": {"prot": 1, "pool": {"hosts": [1]}}})
    error = _error([base, prod], App)
    assert {(Path(p["loc"][0]).name, *p["loc"][1:]): p["type"] for p in error.errors()} == {
        ("base.json", "name"): "type", ("base.json", "typo"): "unknown", ("base.json", "tags", 1): "type",
        ("base.json", "db", "port"): "invalid", ("base.json", "db", "pool", "size"): "type",
        ("prod.json", "debug"): "type", ("prod.json", "db", "prot"): "unknown",
        ("prod.json", "db", "pool", "hosts", 0): "type",
        ("prod.json", "db", "host"): "missing",                     # in no file: the last one is named
        }
    assert error.files == (str(base), str(prod)) and error.file == f"{base}, {prod}"
    lines = str(error).splitlines()
    assert lines[0] == f"9 problems in {base}, {prod} for App:"
    assert f"    {base}:1: db.port: invalid 'Port': must be between 1 and 65535" in lines
    assert f"    {prod}:1: db.host: missing key" in lines
    back = pickle.loads(pickle.dumps(error))
    assert back.files == error.files and str(back) == str(error) and back.errors() == error.errors()


def test_one_file_reads_as_it_always_did(tmp_path):
    (base,) = _files(tmp_path, base = {"name": 1, "db": {"host": "h"}})
    for file in (base, [base]):
        error = _error(file, App)
        assert error.file == str(base) and error.files == (str(base),)
        assert str(error) == f"1 problem in {base} for App:\n    {base}:1: name: expected type: 'str', got type 'int'"


def test_ignored_keys_name_their_file_too(tmp_path):
    base, prod = _files(tmp_path, base = {"name": "x", "old": 1, "db": {"host": "h"}}, prod = {"db": {"new": 1}})
    with pytest.warns(ConfigWarning) as caught:
        Loose.load([base, prod])
    warning = caught[0].message
    assert str(warning) == f"2 unknown keys ignored in {base}, {prod} for Loose ({__file__}:{Loose.__firstlineno__}):\n    -> {base}:1: old\n    -> {prod}:1: db.new"
    assert caught[0].filename == __file__ and warning.files == (str(base), str(prod))
    assert str(pickle.loads(pickle.dumps(warning))) == str(warning)


@pytest.mark.parametrize("file", [[], (), 5, None, {"a.json"}], ids = repr)
def test_what_is_no_file_or_list_of_files(file):
    with pytest.raises((TypeError, ValueError), match = "file"):
        App.load(file)


def test_a_missing_layer_is_the_usual_error(tmp_path):
    files = _files(tmp_path, base = {"name": "shop", "db": {"host": "h"}})
    with pytest.raises(FileNotFoundError):
        App.load([*files, tmp_path / "nope.json"])


# ── environment variables ───────────────────────────────────────────────────

BASE = {"name": "shop", "db": {"host": "h"}}


def test_variables_go_over_the_files_and_reach_into_sections(tmp_path):
    files = _files(tmp_path, base = BASE, prod = {"db": {"port": 81}})
    env = {"APP_DB__PORT": "9090", "APP_DEBUG": "on", "APP_DB__POOL__SIZE": "7", "APP_NAME": "from env"}
    app = App.load(files, env_prefix = "APP_", env = env)
    assert app.db.port == 9090 and app.debug is True and app.db.pool.size == 7 and app.name == "from env"
    assert app.db.host == "h"                                       # the rest of the section is the files'
    assert App.load(files[0], env_prefix = "APP_", env = env) == app
    assert App.load(files, env_prefix = "APP_", env = {}) == App.load(files)


def test_names_match_without_regard_to_case_prefix_included(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    app = App.load(base, env_prefix = "App_", env = {"app_db__PORT": "81", "APP_Debug": "1"})
    assert app.db.port == 81 and app.debug is True


def test_variables_that_name_no_field_are_ignored_also_under_strict(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    env = {"APP_NOPE": "1", "APP_DB__NOPE": "1", "APP_NAME__DEEPER": "1", "APP_": "1", "APP_DB____PORT": "1",
           "APP_TAGS__0": "x", "OTHER_NAME": "no", "PATH": "/bin", "NAME": "no"}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert App.load(base, env_prefix = "APP_", env = env) == App.load(base)
        assert Loose.load(base, env_prefix = "APP_", env = env).name == "shop"


def test_an_empty_prefix_takes_every_variable_that_names_a_field(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    assert App.load(base, env_prefix = "", env = {"NAME": "bare", "PATH": "/bin", "DB__PORT": "81"}).name == "bare"


def test_a_list_takes_commas_or_a_json_array_and_a_dictionary_a_json_object(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    def tags(text: str) -> list[str]:
        return App.load(base, env_prefix = "APP_", env = {"APP_TAGS": text}).tags
    assert tags("a, b ,c") == ["a", "b", "c"] and tags("") == [] and tags("one") == ["one"]
    assert tags('["a,b", "c"]') == ["a,b", "c"] and tags(' ["x"] ') == ["x"]
    env = {"APP_LIMITS": '{"cpu": "4", "mem": 8}', "APP_REPLICAS": '[{"host": "r1"}, {"host": "r2", "port": "82"}]',
           "APP_DB__POOL__HOSTS": "p1,p2"}
    app = App.load(base, env_prefix = "APP_", env = env)
    assert app.limits == {"cpu": 4, "mem": 8} and app.db.pool.hosts == ["p1", "p2"]
    assert app.replicas == [Db(host = "r1"), Db(host = "r2", port = 82)]


def test_text_that_starts_as_json_and_is_not_is_refused_without_its_value(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    for env in ({"APP_KEYS": '["hunter2", '}, {"APP_LIMITS": '{"hunter2"}'}):
        with pytest.raises(ValueError, match = "APP_(KEYS|LIMITS): starts with") as info:
            App.load(base, env_prefix = "APP_", env = env)
        assert "hunter2" not in str(info.value) and info.value.__cause__ is None and info.value.__suppress_context__


def test_a_problem_names_the_variable(tmp_path):
    (base,) = _files(tmp_path, base = {**BASE, "tags": [1]})
    env = {"APP_DB__PORT": "0", "APP_DB__PASSWORD": "", "APP_REPLICAS": "r1, r2", "APP_LIMITS": '{"cpu": "many"}'}
    error = _error(base, App, env_prefix = "APP_", env = env)
    assert sorted(p["loc"] for p in error.errors()) == sorted([
        ["APP_REPLICAS", "replicas"], ["APP_DB__PORT", "db", "port"], [str(base), "tags", 0],
        ["APP_LIMITS", "limits", "cpu"]])
    assert error.files == (str(base), "APP_*")
    assert "    APP_DB__PORT: db.port: invalid 'Port'" in str(error)
    assert "    APP_REPLICAS: replicas: expected a list of sections, got str" in str(error)
    assert f"    {base}:1: tags[0]: " in str(error)


def test_a_secret_from_the_environment_is_masked(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    error = _error(base, App, env_prefix = "APP_", env = {"APP_KEYS": '["fine", 123456]'})
    assert error.errors()[0]["loc"] == ["APP_KEYS", "keys", 1] and "123456" not in str(error) + error.json()


def test_the_environment_alone_and_missing_keys(tmp_path):
    env = {"APP_NAME": "shop", "APP_DB__HOST": "h"}
    assert App.load([], env_prefix = "APP_", env = env) == App(name = "shop", db = Db(host = "h"))
    error = _error([], App, env_prefix = "APP_", env = {"APP_NAME": "shop"})
    assert error.errors()[0]["loc"] == ["APP_*", "db"] and error.files == ("APP_*",)
    (base,) = _files(tmp_path, base = {"db": {"host": "h"}})
    error = _error(base, App, env_prefix = "APP_", env = {"APP_DEBUG": "1"})
    assert error.errors()[0]["loc"] == [str(base), "name"]          # missing: the last file, not the environment


def test_os_environ_is_read_when_no_env_is_given(tmp_path, monkeypatch):
    (base,) = _files(tmp_path, base = BASE)
    monkeypatch.setenv("CFGCAST_TEST_DB__PORT", "81")
    assert App.load(base, env_prefix = "CFGCAST_TEST_").db.port == 81
    assert App.load(base).db.port == 5432                      # no prefix: the environment is not read


def test_env_options_are_checked(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    with pytest.raises(TypeError, match = "env_prefix"):
        App.load(base, env = {"APP_NAME": "x"})                # env without a prefix would be read by nobody
    with pytest.raises(TypeError, match = "env_prefix"):
        App.load(base, env_prefix = 5)                         # type: ignore[arg-type]
    with pytest.raises(TypeError):
        App.load(base, "json", "APP_")                         # type: ignore[misc]  # keyword-only


def test_read_env_gives_one_layer_per_variable_in_the_order_of_their_names():
    env = {"APP_TAGS": "a", "APP_DB__PORT": "1", "APP_X": "1"}
    assert read_env(App, "APP_", env) == [
        ("APP_DB__PORT", ("db", "port"), {"db": {"port": "1"}}), ("APP_TAGS", ("tags",), {"tags": ["a"]})]


# ── optional files ──────────────────────────────────────────────────────────

def test_an_optional_file_is_read_if_it_is_there_and_left_out_if_not(tmp_path):
    base, local = _files(tmp_path, base = BASE, local = {"debug": True})
    assert App.load([base, optional(local)]).debug is True
    assert App.load([base, optional(tmp_path / "nope.json")]) == App.load(base)
    assert App.load([optional(tmp_path / "nope.json"), base]) == App.load(base)
    assert App.load([base, optional(tmp_path / "no_dir" / "nope.json")]) == App.load(base)


def test_a_file_not_marked_must_be_there_also_next_to_an_optional_one(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    with pytest.raises(FileNotFoundError):
        App.load([base, optional(tmp_path / "a.json"), tmp_path / "b.json"])


def test_a_problem_in_an_optional_file_that_is_there_is_a_problem(tmp_path):
    base, local = _files(tmp_path, base = BASE, local = {"db": {"port": 0}})
    error = _error([base, optional(local)], App)
    assert error.errors()[0]["loc"] == [str(local), "db", "port"] and error.files == (str(base), str(local))
    local.write_text("{ not json")
    with pytest.raises(json.JSONDecodeError):
        App.load([base, optional(local)])


def test_only_a_file_that_isnt_there_is_skipped(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    (tmp_path / "dir.json").mkdir()
    with pytest.raises(OSError):
        App.load([base, optional(tmp_path / "dir.json")])
    if os.name != "nt":
        (tmp_path / "link.json").symlink_to(tmp_path / "gone.json")     # a broken link: a file that was meant to be there
        with pytest.raises(FileNotFoundError):
            App.load([base, optional(tmp_path / "link.json")])
    with pytest.raises(ValueError, match = "Cannot infer"):             # the suffix is checked, there or not
        App.load([base, optional(tmp_path / "nope.conf")])


def test_the_sources_of_an_error_are_the_files_that_were_read(tmp_path):
    (base,) = _files(tmp_path, base = {"name": "shop"})
    error = _error([optional(tmp_path / "nope.json"), base, optional(tmp_path / "nope2.json")], App)
    assert error.files == (str(base),) and error.errors()[0]["loc"] == [str(base), "db"]


def test_nothing_read_at_all_is_a_load_from_the_defaults(tmp_path):
    assert Pool.load(optional(tmp_path / "nope.json")) == Pool()
    error = _error([optional(tmp_path / "nope.json")], App)
    assert error.files == ("(no file)",) and [e["type"] for e in error.errors()] == ["missing", "missing"]
    assert App.load(optional(tmp_path / "nope.json"), env_prefix = "A_", env = {"A_NAME": "n", "A_DB__HOST": "h"}).name == "n"


def test_the_marker_is_no_path():
    marker = optional("local.toml")
    assert repr(marker) == "optional('local.toml')" and optional(Path("a") / "b.toml").path == os.path.join("a", "b.toml")
    with pytest.raises(TypeError):
        os.fspath(marker)                                               # type: ignore[call-overload]
    with pytest.raises(TypeError, match = "optional takes a path"):
        optional(5)                                                     # type: ignore[arg-type]
    with pytest.raises(TypeError, match = "in the list"):
        App.load(["a.json", 5])                                    # type: ignore[list-item]


# ── .env files ──────────────────────────────────────────────────────────────

def _env_file(tmp_path: Path, text: str, name: str = ".env") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding = "utf-8")
    return path


def test_the_format_of_an_env_file(tmp_path):
    text = (
        "# a comment\n"
        "\n"
        "PLAIN=value\n"
        "export EXPORTED = spaced out   # a comment\n"
        "HASH=a#b\n"
        "EMPTY=\n"
        "ONLY_COMMENT= # nothing\n"
        "SINGLE='as $written \\n #here'  # after\n"
        'DOUBLE="tab\\there \\"q\\" back\\\\slash \\x ${HOME}"\n'
        'MULTI="line one\n'
        'line two"\n'
        "TWICE=1\r\n"
        "TWICE=2\r\n"
        "URL=postgres://u:p@h/db?x=1\n"
        )
    found = read_env_file(_env_file(tmp_path, "\ufeff" + text))
    assert {key: value for key, (value, _) in found.items()} == {
        "PLAIN": "value", "EXPORTED": "spaced out", "HASH": "a#b", "EMPTY": "", "ONLY_COMMENT": "",
        "SINGLE": "as $written \\n #here", "DOUBLE": 'tab\there "q" back\\slash \\x ${HOME}',
        "MULTI": "line one\nline two", "TWICE": "2", "URL": "postgres://u:p@h/db?x=1",
        }
    assert found["PLAIN"][1] == 3 and found["MULTI"][1] == 10 and found["TWICE"][1] == 13


@pytest.mark.parametrize("text, said", [
    ("A=1\nhunter2 is not a line\n", ":2: not a KEY=VALUE line"),
    ("A='hunter2\n", ":1: the quote of A is never closed"),
    ('A="hunter2" trailing\n', ":1: text after the closing quote of A"),
    ("=hunter2\n", ":1: not a KEY=VALUE line"),
    ])
def test_a_line_that_is_no_variable_is_said_without_its_text(tmp_path, text, said):
    with pytest.raises(ValueError) as info:
        read_env_file(_env_file(tmp_path, text))
    assert str(info.value).endswith(said) and "hunter2" not in str(info.value)


def test_an_env_file_is_over_the_files_and_under_the_environment(tmp_path):
    (base,) = _files(tmp_path, base = {**BASE, "debug": False, "tags": ["file"]})
    env_file = _env_file(tmp_path, "APP_DB__PORT=81\nAPP_DEBUG=yes\napp_name=from file\nOTHER=1\nAPP_NOPE=1\nAPP_TAGS=a, b\n")
    app = App.load(base, env_prefix = "APP_", env_file = env_file, env = {"APP_DB__PORT": "82"})
    assert (app.db.port, app.debug, app.name, app.tags) == (82, True, "from file", ["a", "b"])
    assert App.load(base, env_prefix = "APP_", env_file = env_file, env = {}).db.port == 81
    assert "APP_DB__PORT" not in os.environ                         # the process environment is never changed
    assert App.load([], env_prefix = "APP_", env_file = _env_file(tmp_path, "APP_NAME=n\nAPP_DB__HOST=h\n", "only.env"), env = {}).name == "n"


def test_a_problem_of_an_env_file_names_the_file_and_the_line(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    env_file = _env_file(tmp_path, "# settings\nAPP_DEBUG=maybe\n\nAPP_DB__PORT=0\nAPP_DB__PASSWORD='hunter2'\n")
    error = _error(base, App, env_prefix = "APP_", env_file = env_file, env = {"APP_NAME": ""})
    assert error.files == (str(base), str(env_file), "APP_*")
    port = next(e for e in error.errors() if e["loc"][1:] == ["db", "port"])
    assert port["loc"][0] == str(env_file) and port["ctx"]["line"] == 4
    assert f"    {env_file}:4: db.port: " in str(error) and f"    {env_file}:2: debug: " in str(error)
    assert "hunter2" not in str(error) and "hunter2" not in error.json()


def test_an_env_file_must_be_there_unless_it_is_optional_and_needs_a_prefix(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    with pytest.raises(FileNotFoundError):
        App.load(base, env_prefix = "APP_", env_file = tmp_path / ".env", env = {})
    assert App.load(base, env_prefix = "APP_", env_file = optional(tmp_path / ".env"), env = {}) == App.load(base)
    present = _env_file(tmp_path, "APP_DEBUG=1\n")
    assert App.load(base, env_prefix = "APP_", env_file = optional(present), env = {}).debug is True
    with pytest.raises(TypeError, match = "env_file is read only with an env_prefix"):
        App.load(base, env_file = present)
    with pytest.raises(TypeError, match = "env_file must be a path"):
        App.load(base, env_prefix = "APP_", env_file = 5)        # type: ignore[arg-type]


# ── class App(Config, env_prefix = ...) ─────────────────────────────────────────────

class Prefixed(Config, env_prefix = "SHOP_"):
    name: str = "n"
    pool: Pool = field(default_factory = Pool)


class PrefixedSection(Config, env_prefix = "DB_"):
    port: int = 1


class Holder(Config):
    section: PrefixedSection = field(default_factory = PrefixedSection)


def test_a_class_with_a_prefix_reads_the_environment_without_being_told(tmp_path, monkeypatch):
    (base,) = _files(tmp_path, base = {"name": "file"})
    monkeypatch.setenv("SHOP_POOL__SIZE", "81")
    monkeypatch.setenv("APP_NAME", "other")
    assert Prefixed.load(base).pool.size == 81 and Prefixed.load(base).pool.size == 81
    assert Prefixed.load([]).pool.size == 81                     # the environment alone
    assert Prefixed.load(base, env = {}).pool.size == 1       # env = {}: no variables
    assert Prefixed.load(base, env = {"SHOP_NAME": "handed"}).name == "handed"
    assert Prefixed.load(base, env_prefix = "APP_").name == "other"        # the call's prefix wins
    assert Prefixed.load(base, env_file = _env_file(tmp_path, "SHOP_NAME=dotenv\n"), env = {}).name == "dotenv"
    assert Prefixed.from_dict({"name": "d"}).pool.size == 1        # from_dict builds what it is given


def test_only_the_prefix_of_the_class_that_is_loaded_counts(tmp_path, monkeypatch):
    (base,) = _files(tmp_path, base = {})
    monkeypatch.setenv("DB_PORT", "81")
    assert Holder.load(base).section.port == 1                 # a section's prefix is not read
    assert PrefixedSection.load(base).port == 81               # loaded by itself, it is


def test_a_prefix_that_is_no_text_is_refused_at_the_class_statement():
    with pytest.raises(TypeError, match = "env_prefix must be text"):
        type("Wrong", (Config,), {}, env_prefix = 5)


# ── secrets from files: a variable with _FILE ───────────────────────────────

class _Db(Config):
    password: SecretStr
    hosts: list[str] = field(default_factory = list)
    key_file: str = "id_rsa"                                        # a field that really ends in _file


class _Service(Config):
    name: str
    db: _Db
    token: SecretStr = SecretStr("none")


def _secret(tmp_path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding = "utf-8")
    return str(path)


def test_a_file_variable_gives_the_text_of_its_file(tmp_path):
    env = {"APP_DB__PASSWORD_FILE": _secret(tmp_path, "pw", "hunter2\n"), "app_token_file": _secret(tmp_path, "tok", "t0ken"),
           "APP_NAME": "svc", "APP_DB__HOSTS_FILE": _secret(tmp_path, "hosts", "a, b\n")}
    service = _Service.load([], env_prefix = "APP_", env = env)
    assert service.db.password.get_secret_value() == "hunter2" and service.token.get_secret_value() == "t0ken"
    assert service.db.hosts == ["a", "b"]


def test_only_one_line_break_is_taken_off_and_the_rest_is_kept(tmp_path):
    env = {"APP_DB__PASSWORD_FILE": _secret(tmp_path, "pw", "  two\nlines\n\n"), "APP_NAME": "svc"}
    assert _Service.load([], env_prefix = "APP_", env = env).db.password.get_secret_value() == "  two\nlines\n"


def test_a_field_that_ends_in_file_is_a_field_first(tmp_path):
    env = {"APP_DB__KEY_FILE": "other_key", "APP_DB__PASSWORD": "p", "APP_NAME": "svc",
           "APP_DB__KEY_FILE_FILE": ""}                             # empty: as good as unset
    assert _Service.load([], env_prefix = "APP_", env = env).db.key_file == "other_key"


def test_a_field_given_both_ways_is_refused_and_no_value_is_shown(tmp_path):
    env = {"APP_DB__PASSWORD_FILE": _secret(tmp_path, "pw", "from-file"), "APP_DB__PASSWORD": "from-env", "APP_NAME": "svc"}
    with pytest.raises(ValueError, match = "APP_DB__PASSWORD and APP_DB__PASSWORD_FILE both give db.password") as caught:
        _Service.load([], env_prefix = "APP_", env = env)
    assert "from-file" not in str(caught.value) and "from-env" not in str(caught.value)


def test_a_file_that_is_not_there_names_the_variable_and_the_path(tmp_path):
    env = {"APP_DB__PASSWORD_FILE": str(tmp_path / "nope"), "APP_NAME": "svc"}
    with pytest.raises(FileNotFoundError, match = "APP_DB__PASSWORD_FILE: No such file") as caught:
        _Service.load([], env_prefix = "APP_", env = env)
    assert caught.value.filename == str(tmp_path / "nope") and "nope" in str(caught.value)


def test_a_file_that_is_no_text_says_so_without_its_content(tmp_path):
    (tmp_path / "bin").write_bytes(b"\xff\xfesecret")
    with pytest.raises(ValueError, match = "APP_DB__PASSWORD_FILE: the file it names is not UTF-8 text") as caught:
        _Service.load([], env_prefix = "APP_", env = {"APP_DB__PASSWORD_FILE": str(tmp_path / "bin"), "APP_NAME": "svc"})
    assert "secret" not in str(caught.value)


def test_a_problem_of_a_file_variable_names_the_variable_and_masks_the_secret(tmp_path):
    class Limits(Config):
        size: int
        key: Annotated[SecretStr, "x"] = SecretStr("k")

    env = {"L_SIZE_FILE": _secret(tmp_path, "size", "big"), "L_KEY_FILE": _secret(tmp_path, "key", "s3cret")}
    with pytest.raises(ConfigError) as caught:
        Limits.load([], env_prefix = "L_", env = env)
    assert [e["loc"] for e in caught.value.errors()] == [["L_SIZE_FILE", "size"]]
    assert "L_SIZE_FILE: size:" in str(caught.value) and "s3cret" not in str(caught.value)


def test_a_file_variable_of_a_section_or_of_nothing_is_ignored(tmp_path):
    env = {"APP_DB_FILE": _secret(tmp_path, "db", "x"), "APP_NOPE_FILE": str(tmp_path / "absent"), "APP_NAME": "svc",
           "APP_DB__PASSWORD": "p"}
    assert _Service.load([], env_prefix = "APP_", env = env).db.password.get_secret_value() == "p"


def test_a_file_variable_in_an_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"APP_NAME=svc\nAPP_DB__PASSWORD_FILE={_secret(tmp_path, 'pw', 'filed')}\n")
    service = _Service.load([], env_prefix = "APP_", env = {}, env_file = env_file)
    assert service.db.password.get_secret_value() == "filed"
