"""The public way in and out: `load_cfg`, `Config.load` / `from_dict`, `to_dict`, `write`, `infer_type`."""
from dataclasses import field
from pathlib import Path
import json
import warnings

import pytest

from enforced.data_types import Port, SecretStr
from cfgcast import ConfigError, ConfigWarning, cfg, load_cfg, to_dict, write
from cfgcast._base import SUFFIXES, infer_type

CFGS = Path(__file__).parent.parent / "cfgs"


@cfg
class Db:
    host: str
    port: Port = 5432
    password: SecretStr = SecretStr("pw")


@cfg
class App:
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

@cfg
class Simple:
    name: str
    version: str
    debug: bool
    port: int
    timeout: float
    workers: int
    log_level: str
    hosts: list[str]


@pytest.mark.parametrize("suffix", ["json", "toml", "yaml", "ini", "cfg"])
def test_load_cfg_and_the_load_member_read_every_format(suffix):
    if suffix == "yaml":
        pytest.importorskip("yaml")
    loaded = load_cfg(CFGS / f"simple.{suffix}", Simple)
    assert loaded == Simple.load(CFGS / f"simple.{suffix}") == Simple.load(str(CFGS / f"simple.{suffix}"))
    assert loaded.port == 8080 and loaded.debug is True and loaded.hosts == ["localhost", "shop.example.com"]


def test_cfg_type_overrides_the_suffix_positionally_or_by_keyword(tmp_path):
    path = tmp_path / "settings.conf"
    path.write_text((CFGS / "simple.toml").read_text())
    assert load_cfg(path, Simple, "toml") == load_cfg(path, Simple, cfg_type = "toml") == Simple.load(path, "toml")
    with pytest.raises(ValueError, match = "cfg_type"):
        load_cfg(path, Simple)


def test_an_unknown_cfg_type_is_refused():
    with pytest.raises(ValueError, match = "Unsupported configuration type"):
        load_cfg(CFGS / "simple.json", Simple, "xml")                # type: ignore[arg-type]


@pytest.mark.parametrize("schema", [object, dict, 5, Simple(name = "x", version = "1", debug = True, port = 1, timeout = 1.0,
                                                           workers = 1, log_level = "x", hosts = [])], ids = repr)
def test_load_cfg_takes_a_configuration_class_only(schema):
    with pytest.raises(TypeError, match = "not a @cfg class"):
        load_cfg(CFGS / "simple.json", schema)


def test_errors_of_a_loaded_file_start_with_the_file(tmp_path):
    path = tmp_path / "app.json"
    path.write_text(json.dumps({"name": "x", "db": {"host": "h", "port": 0}, "typo": 1}))
    with pytest.raises(ConfigError) as info:
        load_cfg(path, App)
    assert {tuple(p["loc"][1:]) for p in info.value.errors()} == {("db", "port"), ("typo",)}
    assert all(p["loc"][0] == str(path) for p in info.value.errors())
    assert info.value.file == str(path)


def test_a_missing_file():
    with pytest.raises(FileNotFoundError):
        load_cfg(CFGS / "nope.json", Simple)


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
    with pytest.raises(TypeError, match = "instance of a @cfg class"):
        to_dict(wrong)
    with pytest.raises(TypeError, match = "instance of a @cfg class"):
        write(wrong, tmp_path / "x.json")


# ── write ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("suffix", ["json", "toml", "yaml", "ini", "cfg"])
def test_write_then_load_gives_the_same_configuration(tmp_path, suffix):
    if suffix == "yaml":
        pytest.importorskip("yaml")
    write(APP, tmp_path / f"out.{suffix}")
    assert load_cfg(tmp_path / f"out.{suffix}", App) == APP


def test_write_reveals_secrets_and_writes_every_key(tmp_path):
    write(APP, tmp_path / "out.json")
    written = json.loads((tmp_path / "out.json").read_text())
    assert written["db"] == {"host": "h", "port": 5432, "password": "pw"}  # the default port is written too


def test_overrides_go_to_the_file_only(tmp_path):
    write(APP, tmp_path / "out.toml", {"db": {"port": "9090"}, "name": "variant"})
    variant = load_cfg(tmp_path / "out.toml", App)
    assert variant.db.port == 9090 and variant.name == "variant" and variant.db.host == "h"
    assert APP.db.port == 5432 and APP.name == "shop"


def test_overrides_are_written_as_converted(tmp_path):
    write(APP, tmp_path / "out.json", {"db": {"port": "9090"}})
    assert json.loads((tmp_path / "out.json").read_text())["db"]["port"] == 9090   # a number, not the text


@pytest.mark.parametrize("overrides", [{"db": {"port": "abc"}}, {"nope": 1}, {"db": {"nope": 1}}, {"db": "flat"}], ids = str)
def test_bad_overrides_are_refused_and_nothing_is_written(tmp_path, overrides):
    with pytest.raises(ConfigError):
        write(APP, tmp_path / "never.json", overrides)
    assert not (tmp_path / "never.json").exists()


def test_overrides_must_be_a_dictionary(tmp_path):
    with pytest.raises(TypeError, match = "dictionary"):
        write(APP, tmp_path / "x.json", [("name", "x")])           # type: ignore[arg-type]


def test_write_with_cfg_type_positionally_or_by_keyword(tmp_path):
    write(APP, tmp_path / "a.txt", None, "json")
    write(APP, tmp_path / "b.txt", cfg_type = "toml")
    assert load_cfg(tmp_path / "a.txt", App, "json") == load_cfg(tmp_path / "b.txt", App, "toml") == APP
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
    assert load_cfg(tmp_path / "x.json", App).name == "line\nbreak"


# ── unknown keys under strict = False ───────────────────────────────────────

@cfg
class PlainSection:
    host: str


@cfg(strict = False)
class Lenient:
    name: str
    db: PlainSection


NEWER = {"name": "shop", "added": 1, "db": {"host": "h", "added": "hunter2"}}


def test_every_way_in_warns_and_points_at_the_line_that_called_it(tmp_path):
    file = tmp_path / "new.json"
    file.write_text(json.dumps(NEWER))
    for read in (lambda: load_cfg(file, Lenient), lambda: Lenient.load(file), lambda: Lenient.from_dict(NEWER)):
        with warnings.catch_warnings(record = True) as caught:
            warnings.simplefilter("always")
            assert read().db.host == "h"
        assert [w.category for w in caught] == [ConfigWarning]
        assert caught[0].filename == __file__
        assert str(caught[0].message).endswith(f"for Lenient ({__file__}:{Lenient.__firstlineno__}):\n    -> added\n    -> db.added")
        assert "hunter2" not in str(caught[0].message)


def test_every_way_in_can_leave_the_warning_out(tmp_path):
    file = tmp_path / "new.json"
    file.write_text(json.dumps(NEWER))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        load_cfg(file, Lenient, show_warnings = False)
        Lenient.load(file, show_warnings = False)
        Lenient.from_dict(NEWER, show_warnings = False)


def test_show_warnings_is_keyword_only(tmp_path):
    with pytest.raises(TypeError):
        load_cfg(tmp_path / "new.json", Lenient, "json", False)


def test_the_warning_can_be_made_an_error(tmp_path):
    file = tmp_path / "new.json"
    file.write_text(json.dumps(NEWER))
    with warnings.catch_warnings():
        warnings.filterwarnings("error", category = ConfigWarning)
        with pytest.raises(ConfigWarning):
            load_cfg(file, Lenient)


def test_an_override_that_is_ignored_warns_and_is_not_written(tmp_path):
    file = tmp_path / "out.json"
    loose = Lenient(name = "shop", db = PlainSection(host = "h"))
    with pytest.warns(ConfigWarning, match = "db.prot"):
        write(loose, file, {"db": {"prot": 5}})
    assert json.loads(file.read_text()) == {"name": "shop", "db": {"host": "h"}}


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
    @cfg
    class Derived:
        name: str
        shout: str = field(init = False, default = "")

        def __post_init__(self) -> None:
            object.__setattr__(self, "shout", self.name.upper())

    made = Derived(name = "shop")
    assert made.shout == "SHOP" and to_dict(made) == {"name": "shop"}
    write(made, tmp_path / "d.toml", {"name": "other"})
    assert load_cfg(tmp_path / "d.toml", Derived).shout == "OTHER"


def test_keys_that_were_converted_are_written_and_load_again(tmp_path):
    @cfg
    class Limits:
        by_port: dict[int, str]

    limits = Limits.from_dict({"by_port": {"80": "web", "5432": "db"}})
    assert limits.by_port == {80: "web", 5432: "db"}
    for suffix in ("json", "toml", "ini"):
        write(limits, tmp_path / f"l.{suffix}")
        assert load_cfg(tmp_path / f"l.{suffix}", Limits) == limits
