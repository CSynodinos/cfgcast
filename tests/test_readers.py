"""The readers: a file in, a plain dictionary out (`_readers`)."""
from dataclasses import field
from pathlib import Path
import configparser
import json
import tomllib

import pytest

from cfgcast import Config
from cfgcast._readers import CONFIG_READERS, read_ini, read_json, read_toml, read_yaml

CFGS = Path(__file__).parent.parent / "cfgs"


def _file(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding = "utf-8")
    return path


# ── JSON / TOML ─────────────────────────────────────────────────────────────

def test_json_and_toml_read_the_examples_alike():
    assert read_json(CFGS / "complex.json") == read_toml(CFGS / "complex.toml")
    assert read_json(CFGS / "simple.json")["hosts"] == ["localhost", "shop.example.com"]


def test_json_top_level_must_be_an_object(tmp_path):
    for text in ("[1, 2]", "3", '"x"', "null"):
        with pytest.raises(ValueError, match = "top level must be a mapping"):
            read_json(_file(tmp_path, "x.json", text))


def test_invalid_files_raise_what_the_parser_raises(tmp_path):
    with pytest.raises(json.JSONDecodeError):
        read_json(_file(tmp_path, "x.json", "{nope"))
    with pytest.raises(tomllib.TOMLDecodeError):
        read_toml(_file(tmp_path, "x.toml", "a = "))
    with pytest.raises(configparser.Error):
        read_ini(_file(tmp_path, "x.ini", "no section here = 1\n"))


def test_files_are_utf8(tmp_path):
    assert read_json(_file(tmp_path, "x.json", '{"name": "café ✓"}'))["name"] == "café ✓"
    assert read_ini(_file(tmp_path, "x.ini", "[DEFAULT]\nname = café ✓\n"))["name"] == "café ✓"


# ── ini ─────────────────────────────────────────────────────────────────────

def test_default_holds_the_top_level_keys_and_is_not_copied_into_sections(tmp_path):
    data = read_ini(_file(tmp_path, "x.ini", "[DEFAULT]\nname = shop\n\n[db]\nhost = h\n"))
    assert data == {"name": "shop", "db": {"host": "h"}}


def test_dotted_sections_nest(tmp_path):
    text = "[db]\nhost = h\n\n[db.pool]\nsize = 2\n\n[db.pool.deep]\nx = 1\n\n[cache.redis]\nttl = 3\n"
    assert read_ini(_file(tmp_path, "x.ini", text)) == {
        "db": {"host": "h", "pool": {"size": "2", "deep": {"x": "1"}}}, "cache": {"redis": {"ttl": "3"}}}


def test_keys_keep_their_case_and_values_stay_text(tmp_path):
    data = read_ini(_file(tmp_path, "x.ini", "[DEFAULT]\nLogLevel = INFO\nport = 8080\ndebug = true\n"))
    assert data == {"LogLevel": "INFO", "port": "8080", "debug": "true"}


def test_interpolation_is_off(tmp_path):
    assert read_ini(_file(tmp_path, "x.ini", "[DEFAULT]\nrate = 100%\nref = %(name)s\n")) == {"rate": "100%", "ref": "%(name)s"}


def test_a_section_that_clashes_with_a_key_is_refused(tmp_path):
    with pytest.raises(ValueError, match = "clashes"):
        read_ini(_file(tmp_path, "x.ini", "[DEFAULT]\ndb = x\n\n[db]\nhost = h\n"))
    with pytest.raises(ValueError, match = "clashes"):
        read_ini(_file(tmp_path, "x.ini", "[db]\npool = x\n\n[db.pool]\nsize = 2\n"))
    with pytest.raises(ValueError, match = "empty part"):
        read_ini(_file(tmp_path, "x.ini", "[db..pool]\nsize = 2\n"))


def test_lists_are_split_only_where_the_schema_says_list(tmp_path):
    class Pool(Config):
        hosts: list[str]

    class App(Config):
        tags: list[str]
        ports: list[int] = field(default_factory = list)
        empty: list[str] = field(default_factory = list)
        motto: str = ""
        pool: Pool | None = None

    text = "[DEFAULT]\ntags = a, b ,c\nports = 80,443\nempty =\nmotto = one, two\n\n[pool]\nhosts = h1, h2\n"
    data = read_ini(_file(tmp_path, "x.ini", text), App)
    assert data == {"tags": ["a", "b", "c"], "ports": ["80", "443"], "empty": [], "motto": "one, two",
                    "pool": {"hosts": ["h1", "h2"]}}
    assert read_ini(_file(tmp_path, "y.ini", text))["tags"] == "a, b ,c"          # without a schema: text


# ── YAML ────────────────────────────────────────────────────────────────────

def test_yaml_reads_like_json():
    pytest.importorskip("yaml")
    assert read_yaml(CFGS / "complex.yaml") == read_json(CFGS / "complex.json")


def test_yaml_booleans_are_those_of_yaml_1_2(tmp_path):
    pytest.importorskip("yaml")
    text = "a: no\nb: yes\nc: on\nd: off\ne: true\nf: False\ng: y\n"
    assert read_yaml(_file(tmp_path, "x.yaml", text)) == {"a": "no", "b": "yes", "c": "on", "d": "off", "e": True,
                                                         "f": False, "g": "y"}


def test_plain_pyyaml_is_left_as_it_was(tmp_path):
    yaml = pytest.importorskip("yaml")
    read_yaml(_file(tmp_path, "x.yaml", "a: no\n"))
    assert yaml.safe_load("a: no") == {"a": False}                  # our loader is a subclass of its own


def test_empty_yaml_is_an_empty_mapping_and_a_list_is_refused(tmp_path):
    pytest.importorskip("yaml")
    assert read_yaml(_file(tmp_path, "x.yaml", "")) == {}
    with pytest.raises(ValueError, match = "mapping"):
        read_yaml(_file(tmp_path, "y.yaml", "- a\n- b\n"))


def test_without_pyyaml_the_error_says_what_to_install(monkeypatch, tmp_path):
    import builtins
    import cfgcast._readers as readers

    real = builtins.__import__

    def no_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ModuleNotFoundError("No module named 'yaml'", name = "yaml")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(readers, "_YAML", None)
    monkeypatch.setattr(builtins, "__import__", no_yaml)
    with pytest.raises(ModuleNotFoundError, match = r"pip install cfgcast\[yaml\]"):
        read_yaml(_file(tmp_path, "x.yaml", "a: 1\n"))


def test_every_format_has_a_reader():
    assert set(CONFIG_READERS) == {"json", "toml", "ini", "yaml"}


def test_ini_and_sections_in_lists_and_dictionaries(tmp_path):
    class Region(Config):
        hosts: list[str]

    class App(Config):
        regions: dict[str, Region] = field(default_factory = dict)
        servers: list[Region] = field(default_factory = list)

    class Outer(Config):
        app: App

    #* A dictionary of sections is a dotted section per name, its lists split as anywhere.
    data = read_ini(_file(tmp_path, "x.ini", "[regions.eu]\nhosts = a, b\n\n[regions.us]\nhosts =\n"), App)
    assert data == {"regions": {"eu": {"hosts": ["a", "b"]}, "us": {"hosts": []}}}
    #* A list of sections can't be written in ini: said with the field, also in a section.
    with pytest.raises(ValueError, match = r"x\.ini: servers is a list of sections \(Region\)"):
        read_ini(_file(tmp_path, "x.ini", "[DEFAULT]\nservers = a, b\n"), App)
    with pytest.raises(ValueError, match = r"app\.servers is a list of sections"):
        read_ini(_file(tmp_path, "x.ini", "[app]\nservers = a, b\n"), Outer)


def test_a_clash_never_shows_the_value(tmp_path):
    with pytest.raises(ValueError, match = r"\[token\.x\] clashes with the key token$") as info:
        read_ini(_file(tmp_path, "x.ini", "[DEFAULT]\ntoken = hunter2\n\n[token.x]\na = 1\n"))
    assert "hunter2" not in str(info.value)
