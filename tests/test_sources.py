"""Several sources for one configuration: files in layers, and environment variables over them (`_sources`, `_load`)."""
from dataclasses import field
from pathlib import Path
import json
import pickle
import warnings

import pytest

from enforced.data_types import Port, SecretStr
from cfgcast import ConfigError, ConfigWarning, cfg, load_cfg
from cfgcast._sources import Sources, layer, read_env


@cfg
class Pool:
    size: int = 1
    hosts: list[str] = field(default_factory = list)


@cfg
class Db:
    host: str
    port: Port = 5432
    password: SecretStr | None = None
    pool: Pool = field(default_factory = Pool)


@cfg
class App:
    name: str
    db: Db
    debug: bool = False
    tags: list[str] = field(default_factory = list)
    limits: dict[str, int] = field(default_factory = dict)
    replicas: list[Db] = field(default_factory = list)
    keys: list[SecretStr] = field(default_factory = list)


@cfg(strict = False)
class Loose:
    name: str
    db: Db


def _files(tmp_path: Path, **layers: dict) -> list[Path]:
    paths = []
    for name, data in layers.items():
        paths.append(tmp_path / f"{name}.json")
        paths[-1].write_text(json.dumps(data))
    return paths


def _error(*args, **kwargs) -> ConfigError:
    with pytest.raises(ConfigError) as info:
        load_cfg(*args, **kwargs)
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
    app = load_cfg(files, App)
    assert app == App(name = "shop", debug = True, tags = ["c"], db = Db(host = "h", port = 9090, pool = Pool(size = 5, hosts = ["x"])))
    assert App.load(tuple(str(f) for f in files)) == app            # the member; a tuple; text paths
    assert load_cfg(files[:1], App).tags == ["a", "b"]              # a list of one is that file


def test_each_file_is_read_in_its_own_format_or_all_in_the_one_named(tmp_path):
    (tmp_path / "base.toml").write_text('name = "shop"\n[db]\nhost = "h"\n')
    (tmp_path / "over.ini").write_text("[DEFAULT]\ntags = a, b\n\n[db]\nport = 81\n")
    app = load_cfg([tmp_path / "base.toml", tmp_path / "over.ini"], App)
    assert app.tags == ["a", "b"] and app.db.port == 81 and app.db.host == "h"
    (tmp_path / "one.conf").write_text('name = "shop"\n')
    (tmp_path / "two.conf").write_text('[db]\nhost = "h"\n')
    assert load_cfg([tmp_path / "one.conf", tmp_path / "two.conf"], App, "toml").db.host == "h"
    with pytest.raises(ValueError, match = "cfg_type"):             # before anything is read: base.toml is fine
        load_cfg([tmp_path / "base.toml", tmp_path / "one.conf"], App)


def test_strict_and_missing_are_checked_once_on_the_result(tmp_path):
    files = _files(tmp_path, base = {"name": "shop"}, prod = {"db": {"host": "h"}})
    assert load_cfg(files, App).db.host == "h"                      # neither file is whole; together they are
    with pytest.raises(ConfigError):
        load_cfg(files[:1], App)


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
    assert f"    {base}: db.port: invalid 'Port': must be between 1 and 65535" in lines
    assert f"    {prod}: db.host: missing key" in lines
    back = pickle.loads(pickle.dumps(error))
    assert back.files == error.files and str(back) == str(error) and back.errors() == error.errors()


def test_one_file_reads_as_it_always_did(tmp_path):
    (base,) = _files(tmp_path, base = {"name": 1, "db": {"host": "h"}})
    for file in (base, [base]):
        error = _error(file, App)
        assert error.file == str(base) and error.files == (str(base),)
        assert str(error) == f"1 problem in {base} for App:\n    name: expected type: 'str', got type 'int'"


def test_ignored_keys_name_their_file_too(tmp_path):
    base, prod = _files(tmp_path, base = {"name": "x", "old": 1, "db": {"host": "h"}}, prod = {"db": {"new": 1}})
    with pytest.warns(ConfigWarning) as caught:
        load_cfg([base, prod], Loose)
    warning = caught[0].message
    assert str(warning) == f"2 unknown keys ignored in {base}, {prod} for Loose ({__file__}:{Loose.__firstlineno__}):\n    -> {base}: old\n    -> {prod}: db.new"
    assert caught[0].filename == __file__ and warning.files == (str(base), str(prod))
    assert str(pickle.loads(pickle.dumps(warning))) == str(warning)


@pytest.mark.parametrize("file", [[], (), 5, None, {"a.json"}], ids = repr)
def test_what_is_no_file_or_list_of_files(file):
    with pytest.raises((TypeError, ValueError), match = "file"):
        load_cfg(file, App)


def test_a_missing_layer_is_the_usual_error(tmp_path):
    files = _files(tmp_path, base = {"name": "shop", "db": {"host": "h"}})
    with pytest.raises(FileNotFoundError):
        load_cfg([*files, tmp_path / "nope.json"], App)


# ── environment variables ───────────────────────────────────────────────────

BASE = {"name": "shop", "db": {"host": "h"}}


def test_variables_go_over_the_files_and_reach_into_sections(tmp_path):
    files = _files(tmp_path, base = BASE, prod = {"db": {"port": 81}})
    env = {"APP_DB__PORT": "9090", "APP_DEBUG": "on", "APP_DB__POOL__SIZE": "7", "APP_NAME": "from env"}
    app = load_cfg(files, App, env_prefix = "APP_", env = env)
    assert app.db.port == 9090 and app.debug is True and app.db.pool.size == 7 and app.name == "from env"
    assert app.db.host == "h"                                       # the rest of the section is the files'
    assert App.load(files[0], env_prefix = "APP_", env = env) == app
    assert load_cfg(files, App, env_prefix = "APP_", env = {}) == load_cfg(files, App)


def test_names_match_without_regard_to_case_prefix_included(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    app = load_cfg(base, App, env_prefix = "App_", env = {"app_db__PORT": "81", "APP_Debug": "1"})
    assert app.db.port == 81 and app.debug is True


def test_variables_that_name_no_field_are_ignored_also_under_strict(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    env = {"APP_NOPE": "1", "APP_DB__NOPE": "1", "APP_NAME__DEEPER": "1", "APP_": "1", "APP_DB____PORT": "1",
           "APP_TAGS__0": "x", "OTHER_NAME": "no", "PATH": "/bin", "NAME": "no"}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert load_cfg(base, App, env_prefix = "APP_", env = env) == load_cfg(base, App)
        assert load_cfg(base, Loose, env_prefix = "APP_", env = env).name == "shop"


def test_an_empty_prefix_takes_every_variable_that_names_a_field(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    assert load_cfg(base, App, env_prefix = "", env = {"NAME": "bare", "PATH": "/bin", "DB__PORT": "81"}).name == "bare"


def test_a_list_takes_commas_or_a_json_array_and_a_dictionary_a_json_object(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    def tags(text: str) -> list[str]:
        return load_cfg(base, App, env_prefix = "APP_", env = {"APP_TAGS": text}).tags
    assert tags("a, b ,c") == ["a", "b", "c"] and tags("") == [] and tags("one") == ["one"]
    assert tags('["a,b", "c"]') == ["a,b", "c"] and tags(' ["x"] ') == ["x"]
    env = {"APP_LIMITS": '{"cpu": "4", "mem": 8}', "APP_REPLICAS": '[{"host": "r1"}, {"host": "r2", "port": "82"}]',
           "APP_DB__POOL__HOSTS": "p1,p2"}
    app = load_cfg(base, App, env_prefix = "APP_", env = env)
    assert app.limits == {"cpu": 4, "mem": 8} and app.db.pool.hosts == ["p1", "p2"]
    assert app.replicas == [Db(host = "r1"), Db(host = "r2", port = 82)]


def test_text_that_starts_as_json_and_is_not_is_refused_without_its_value(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    for env in ({"APP_KEYS": '["hunter2", '}, {"APP_LIMITS": '{"hunter2"}'}):
        with pytest.raises(ValueError, match = "APP_(KEYS|LIMITS): starts with") as info:
            load_cfg(base, App, env_prefix = "APP_", env = env)
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
    assert f"    {base}: tags[0]: " in str(error)


def test_a_secret_from_the_environment_is_masked(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    error = _error(base, App, env_prefix = "APP_", env = {"APP_KEYS": '["fine", 123456]'})
    assert error.errors()[0]["loc"] == ["APP_KEYS", "keys", 1] and "123456" not in str(error) + error.json()


def test_the_environment_alone_and_missing_keys(tmp_path):
    env = {"APP_NAME": "shop", "APP_DB__HOST": "h"}
    assert load_cfg([], App, env_prefix = "APP_", env = env) == App(name = "shop", db = Db(host = "h"))
    error = _error([], App, env_prefix = "APP_", env = {"APP_NAME": "shop"})
    assert error.errors()[0]["loc"] == ["APP_*", "db"] and error.files == ("APP_*",)
    (base,) = _files(tmp_path, base = {"db": {"host": "h"}})
    error = _error(base, App, env_prefix = "APP_", env = {"APP_DEBUG": "1"})
    assert error.errors()[0]["loc"] == [str(base), "name"]          # missing: the last file, not the environment


def test_os_environ_is_read_when_no_env_is_given(tmp_path, monkeypatch):
    (base,) = _files(tmp_path, base = BASE)
    monkeypatch.setenv("CFGCAST_TEST_DB__PORT", "81")
    assert load_cfg(base, App, env_prefix = "CFGCAST_TEST_").db.port == 81
    assert load_cfg(base, App).db.port == 5432                      # no prefix: the environment is not read


def test_env_options_are_checked(tmp_path):
    (base,) = _files(tmp_path, base = BASE)
    with pytest.raises(TypeError, match = "env_prefix"):
        load_cfg(base, App, env = {"APP_NAME": "x"})                # env without a prefix would be read by nobody
    with pytest.raises(TypeError, match = "env_prefix"):
        load_cfg(base, App, env_prefix = 5)                         # type: ignore[arg-type]
    with pytest.raises(TypeError):
        load_cfg(base, App, "json", "APP_")                         # type: ignore[misc]  # keyword-only


def test_read_env_gives_one_layer_per_variable_in_the_order_of_their_names():
    env = {"APP_TAGS": "a", "APP_DB__PORT": "1", "APP_X": "1"}
    assert read_env(App, "APP_", env) == [("APP_DB__PORT", {"db": {"port": "1"}}), ("APP_TAGS", {"tags": ["a"]})]
