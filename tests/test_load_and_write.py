"""The public way in and out: `Config.load` / `from_dict`, `to_dict`, `write`, `infer_type`."""
from dataclasses import field
from pathlib import Path
import json
import warnings

import pytest

from enforced.data_types import Port, SecretStr
from cfgcast import Config, ConfigError, ConfigWarning, to_dict, write
from cfgcast._base import SUFFIXES, infer_type

CFGS = Path(__file__).parent.parent / "cfgs"


class Db(Config):
    host: str
    port: Port = 5432
    password: SecretStr = SecretStr("pw")


class App(Config):
    name: str
    hosts: list[str] = field(default_factory = list)
    db: Db


APP = App(name = "shop", hosts = ["a", "b"], db = Db(host = "h"))


# ── infer_type ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, kind", [
    ("a.json", "json"), ("a.toml", "toml"), ("a.ini", "ini"), ("a.cfg", "ini"), ("a.yaml", "yaml"), ("a.yml", "yaml"),
    ("A.JSON", "json"), ("dir.yaml/a.Toml", "toml"), ("a.b.ini", "ini"),
    ])
def test_infer_type(name, kind):
    assert infer_type(name) == kind and infer_type(Path(name)) == kind


@pytest.mark.parametrize("name", ["a.conf", "a", "json", "a.json.bak", ".env", "a.xml"])
def test_infer_type_refuses_other_suffixes_and_says_to_pass_cfg_type(name):
    with pytest.raises(ValueError, match = "cfg_type"):
        infer_type(name)


def test_suffixes_name_the_four_formats():
    assert set(SUFFIXES.values()) == {"json", "toml", "ini", "yaml"}


# ── loading ─────────────────────────────────────────────────────────────────

class Simple(Config):
    name: str
    version: str
    debug: bool
    port: int
    timeout: float
    workers: int
    log_level: str
    hosts: list[str]


@pytest.mark.parametrize("suffix", ["json", "toml", "yaml", "ini", "cfg"])
def test_load_reads_every_format(suffix):
    if suffix == "yaml":
        pytest.importorskip("yaml")
    loaded = Simple.load(CFGS / f"simple.{suffix}")
    assert type(loaded) is Simple and loaded == Simple.load(str(CFGS / f"simple.{suffix}"))
    assert loaded.port == 8080 and loaded.debug is True and loaded.hosts == ["localhost", "shop.example.com"]


def test_cfg_type_overrides_the_suffix_positionally_or_by_keyword(tmp_path):
    path = tmp_path / "settings.conf"
    path.write_text((CFGS / "simple.toml").read_text())
    assert Simple.load(path, "toml") == Simple.load(path, cfg_type = "toml")
    with pytest.raises(ValueError, match = "cfg_type"):
        Simple.load(path)


def test_an_unknown_cfg_type_is_refused():
    with pytest.raises(ValueError, match = "Unsupported configuration type"):
        Simple.load(CFGS / "simple.json", "xml")                # type: ignore[arg-type]


def test_errors_of_a_loaded_file_start_with_the_file(tmp_path):
    path = tmp_path / "app.json"
    path.write_text(json.dumps({"name": "x", "db": {"host": "h", "port": 0}, "typo": 1}))
    with pytest.raises(ConfigError) as info:
        App.load(path)
    assert {tuple(p["loc"][1:]) for p in info.value.errors()} == {("db", "port"), ("typo",)}
    assert all(p["loc"][0] == str(path) for p in info.value.errors())
    assert info.value.file == str(path)


def test_a_missing_file():
    with pytest.raises(FileNotFoundError):
        Simple.load(CFGS / "nope.json")


def test_from_dict_builds_and_checks():
    app = App.from_dict({"name": "x", "db": {"host": "h", "port": "81"}})
    assert app.db.port == 81 and type(app.db) is Db
    with pytest.raises(ConfigError) as info:
        App.from_dict({"name": "x", "db": {"host": "h"}, "typo": 1})
    assert info.value.errors()[0]["loc"] == ["<dict>", "typo"]


# ── to_dict ─────────────────────────────────────────────────────────────────

def test_to_dict_nests_sections_and_round_trips_through_from_dict():
    data = to_dict(APP)
    assert data == {"name": "shop", "hosts": ["a", "b"], "db": {"host": "h", "port": 5432, "password": SecretStr("pw")}}
    assert App.from_dict(data) == APP


def test_to_dict_keeps_secrets_masked_and_copies_containers():
    data = to_dict(APP)
    assert type(data["db"]["password"]) is SecretStr and "pw" not in repr(data)
    data["hosts"].append("c")
    assert APP.hosts == ["a", "b"]                                  # the configuration is not changed


@pytest.mark.parametrize("wrong", [App, {"a": 1}, 5], ids = repr)
def test_to_dict_and_write_take_an_instance_of_a_configuration(wrong, tmp_path):
    with pytest.raises(TypeError, match = "takes a configuration"):
        to_dict(wrong)
    with pytest.raises(TypeError, match = "takes a configuration"):
        write(wrong, tmp_path / "x.json")


# ── write ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("suffix", ["json", "toml", "yaml", "ini", "cfg"])
def test_write_then_load_gives_the_same_configuration(tmp_path, suffix):
    if suffix == "yaml":
        pytest.importorskip("yaml")
    write(APP, tmp_path / f"out.{suffix}")
    assert App.load(tmp_path / f"out.{suffix}") == APP


def test_write_reveals_secrets_and_writes_every_key(tmp_path):
    write(APP, tmp_path / "out.json")
    written = json.loads((tmp_path / "out.json").read_text())
    assert written["db"] == {"host": "h", "port": 5432, "password": "pw"}  # the default port is written too


def test_write_with_cfg_type_positionally_or_by_keyword(tmp_path):
    write(APP, tmp_path / "a.txt", "json")
    write(APP, tmp_path / "b.txt", cfg_type = "toml")
    assert App.load(tmp_path / "a.txt", "json") == App.load(tmp_path / "b.txt", "toml") == APP
    with pytest.raises(ValueError, match = "cfg_type"):
        write(APP, tmp_path / "c.txt")
    with pytest.raises(ValueError, match = "Unsupported"):
        write(APP, tmp_path / "d.json", cfg_type = "xml")          # type: ignore[arg-type]


def test_what_ini_cannot_hold_is_refused_before_writing(tmp_path):
    odd = App(name = "line\nbreak", db = Db(host = "h"))
    with pytest.raises(ValueError, match = "line break"):
        write(odd, tmp_path / "x.ini")
    assert not (tmp_path / "x.ini").exists()
    write(odd, tmp_path / "x.json")                                 # JSON can
    assert App.load(tmp_path / "x.json").name == "line\nbreak"


# ── unknown keys under strict = False ───────────────────────────────────────

class PlainSection(Config):
    host: str


class Lenient(Config, strict = False):
    name: str
    db: PlainSection


NEWER = {"name": "shop", "added": 1, "db": {"host": "h", "added": "hunter2"}}


def test_every_way_in_warns_and_points_at_the_line_that_called_it(tmp_path):
    file = tmp_path / "new.json"
    file.write_text(json.dumps(NEWER))
    #* A file's keys have their line (the whole of this one is on line 1), a dictionary's have none.
    for read, at in ((lambda: Lenient.load(file), f"{file}:1: "), (lambda: Lenient.from_dict(NEWER), "")):
        with warnings.catch_warnings(record = True) as caught:
            warnings.simplefilter("always")
            assert read().db.host == "h"
        assert [w.category for w in caught] == [ConfigWarning]
        assert caught[0].filename == __file__
        assert str(caught[0].message).endswith(
            f"for Lenient ({__file__}:{Lenient.__firstlineno__}):\n    -> {at}added\n    -> {at}db.added")
        assert "hunter2" not in str(caught[0].message)


def test_every_way_in_leaves_the_warning_out_under_a_warnings_filter(tmp_path):
    file = tmp_path / "new.json"
    file.write_text(json.dumps(NEWER))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        warnings.filterwarnings("ignore", category = ConfigWarning)
        Lenient.load(file)
        Lenient.from_dict(NEWER)


def test_the_warning_can_be_made_an_error(tmp_path):
    file = tmp_path / "new.json"
    file.write_text(json.dumps(NEWER))
    with warnings.catch_warnings():
        warnings.filterwarnings("error", category = ConfigWarning)
        with pytest.raises(ConfigWarning):
            Lenient.load(file)


def test_the_ini_file_that_found_this_loads_with_a_warning():
    from cfgcast._readers import read_ini
    schema = _ini_schema()
    with pytest.warns(ConfigWarning, match = "monitoring.random_flag"):
        schema.from_dict(read_ini(CFGS / "complex_bad.ini", schema))


def _ini_schema() -> type:
    import importlib.util
    spec = importlib.util.spec_from_file_location("basic_test_schemas", CFGS.parent / "basic_test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BadComplexCfg


# ── found by review ─────────────────────────────────────────────────────────

def test_a_field_the_class_works_out_is_no_key(tmp_path):
    class Derived(Config):
        name: str
        shout: str = field(init = False, default = "")

        def __post_init__(self) -> None:
            object.__setattr__(self, "shout", self.name.upper())

    made = Derived(name = "shop")
    assert made.shout == "SHOP" and to_dict(made) == {"name": "shop"}
    write(Derived(name = "other"), tmp_path / "d.toml")
    assert Derived.load(tmp_path / "d.toml").shout == "OTHER"


def test_keys_that_were_converted_are_written_and_load_again(tmp_path):
    class Limits(Config):
        by_port: dict[int, str]

    limits = Limits.from_dict({"by_port": {"80": "web", "5432": "db"}})
    assert limits.by_port == {80: "web", 5432: "db"}
    for suffix in ("json", "toml", "ini"):
        write(limits, tmp_path / f"l.{suffix}")
        assert Limits.load(tmp_path / f"l.{suffix}") == limits
