"""`build` (a dictionary to a schema instance) and `ConfigError` (`_build`, `_errors`)."""
from dataclasses import field
import json
import pickle
import warnings

import pytest

from enforced import InvalidDataError
import clitint._tint as tint                                   # the colour switches are clitint's state
from enforced.data_types import Port, SecretStr
from cfgcast import ConfigError, cfg, to_dict
from cfgcast._build import MASK, build, problems_of
from cfgcast._errors import ConfigWarning


@cfg
class Pool:
    size: int = 2
    timeout: float = 1.0


@cfg
class Db:
    host: str
    port: Port = 5432
    password: SecretStr
    tags: list[str] = field(default_factory = list)
    pool: Pool = field(default_factory = Pool)


@cfg
class App:
    name: str
    db: Db
    cache: Db | None = None


@cfg(strict = False)
class Loose:
    name: str
    db: Db


LOOSE_AT = f"{__file__}:{Loose.__firstlineno__}"                    # where the warning says Loose is written
GOOD = {"name": "shop", "db": {"host": "h", "password": "pw", "pool": {"size": "4"}}}


def _error(schema, data) -> ConfigError:
    with pytest.raises(ConfigError) as info:
        build(schema, data, "app.toml")
    return info.value


def _locs(error: ConfigError) -> list[list]:
    return [p["loc"] for p in error.errors()]


# ── what it builds ──────────────────────────────────────────────────────────

def test_sections_are_built_and_values_coerced():
    app = build(App, GOOD, "app.toml")
    assert type(app.db) is Db and type(app.db.pool) is Pool
    assert app.db.pool.size == 4 and app.db.port == 5432 and app.cache is None
    assert app.db.password.get_secret_value() == "pw"


def test_an_optional_section_takes_none_or_a_section():
    assert build(App, {**GOOD, "cache": None}, "f").cache is None
    assert build(App, {**GOOD, "cache": {"host": "c", "password": "p"}}, "f").cache.host == "c"


def test_strict_false_ignores_unknown_keys_at_every_level():
    with pytest.warns(ConfigWarning) as caught:
        loose = build(Loose, {**GOOD, "extra": 1, "db": {**GOOD["db"], "typo": 1, "pool": {"size": 4, "deep": 1}}}, "f")
    assert loose.name == "shop" and loose.db.pool.size == 4         # Db and Pool are plain @cfg: Loose decides for them
    assert [p["loc"] for p in caught[0].message.errors()] == [["f", "db", "pool", "deep"], ["f", "db", "typo"], ["f", "extra"]]


def test_a_strict_class_above_a_lenient_section_refuses_only_its_own_keys():
    @cfg(strict = False)
    class Inner:
        a: int = 1

    @cfg
    class Middle:
        inner: Inner

    @cfg
    class Outer:
        middle: Middle

    with pytest.warns(ConfigWarning, match = "middle.inner.x"):
        assert build(Outer, {"middle": {"inner": {"x": 1}}}, "f").middle.inner.a == 1
    with pytest.warns(ConfigWarning, match = "middle.inner.x"):
        error = _error(Outer, {"y": 1, "middle": {"z": 1, "inner": {"x": 1}}})
    assert _locs(error) == [["app.toml", "y"], ["app.toml", "middle", "z"]]


def test_ignored_keys_give_one_warning_with_paths_and_no_values():
    with warnings.catch_warnings(record = True) as caught:
        warnings.simplefilter("always")
        build(Loose, {**GOOD, "token": "hunter2", "other": {"secret": "hunter3"}}, "app.toml")
    assert len(caught) == 1 and caught[0].category is ConfigWarning
    warning = caught[0].message
    assert str(warning) == f"2 unknown keys ignored in app.toml for Loose ({LOOSE_AT}):\n    -> token\n    -> other"
    assert "hunter" not in repr(warning.errors()) and "hunter" not in str(warning)
    assert warning.errors()[0] == {"type": "unknown", "loc": ["app.toml", "token"], "msg": "key not in the schema",
                                   "ctx": {"schema": "Loose"}}
    assert "ctx" not in warning.errors(include_context = False)[0]
    copy = pickle.loads(pickle.dumps(warning))
    assert str(copy) == str(warning) and copy.errors() == warning.errors()


def test_show_warnings_false_says_nothing():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert build(Loose, {**GOOD, "extra": 1}, "f", show_warnings = False).name == "shop"


def test_nothing_ignored_is_no_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build(Loose, GOOD, "f")


def test_show_warnings_is_true_or_false():
    with pytest.raises(TypeError, match = "show_warnings"):
        build(Loose, GOOD, "f", show_warnings = "no")


def test_the_warning_comes_before_an_error_of_the_same_file():
    with pytest.warns(ConfigWarning, match = "db.prot"):
        error = _error(Loose, {"name": "x", "db": {"password": "p", "prot": 5}})
    assert _locs(error) == [["app.toml", "db", "host"]]


def test_an_unknown_key_of_a_strict_schema_holds_no_value():
    error = _error(App, {**GOOD, "token": "hunter2"})
    assert "input" not in error.errors()[0] and "hunter2" not in error.json()


# ── what it reports ─────────────────────────────────────────────────────────

def test_unknown_missing_and_wrong_keys_with_their_paths():
    error = _error(App, {"name": 1, "db": {"port": "x", "password": "p", "prot": 5}, "extra": 1})
    kinds = {tuple(p["loc"]): p["type"] for p in error.errors()}
    assert kinds == {("app.toml", "db", "prot"): "unknown", ("app.toml", "db", "host"): "missing",
                     ("app.toml", "db", "port"): "type", ("app.toml", "extra"): "unknown",
                     ("app.toml", "name"): "type"}


def test_a_refused_value_has_its_reason():
    error = _error(App, {"name": "x", "db": {"host": "h", "password": "p", "port": 0}})
    assert error.errors()[0]["type"] == "invalid" and "65535" in error.errors()[0]["msg"]


def test_nested_items_have_their_index():
    error = _error(App, {"name": "x", "db": {"host": "h", "password": "p", "tags": ["a", 2]}})
    assert _locs(error) == [["app.toml", "db", "tags", 1]]


def test_a_section_that_is_no_mapping():
    error = _error(App, {"name": "x", "db": "nope"})
    assert error.errors()[0]["type"] == "section" and _locs(error) == [["app.toml", "db"]]
    error = _error(App, {"name": "x", "db": {"host": "h", "password": "p", "pool": [1]}})
    assert _locs(error) == [["app.toml", "db", "pool"]]


def test_a_failed_section_is_not_also_reported_missing():
    error = _error(App, {"name": "x", "db": {"password": "p"}})
    assert _locs(error) == [["app.toml", "db", "host"]]


def test_the_top_level_must_be_a_mapping():
    assert _locs(_error(App, ["a"])) == [["app.toml"]]


def test_defaults_fill_what_is_left_out():
    app = build(App, {"name": "x", "db": {"host": "h", "password": "p"}}, "f")
    assert app.db.tags == [] and app.db.pool == Pool()


def test_problems_of_lists_without_raising():
    assert problems_of(App, GOOD, ("f",)) == []
    assert [p["loc"] for p in problems_of(App, {"db": GOOD["db"]}, ("f",))] == [["f", "name"]]


# ── secrets ─────────────────────────────────────────────────────────────────

def test_the_input_of_a_secret_field_is_masked_everywhere():
    error = _error(App, {"name": "x", "db": {"host": "h", "password": ["hunter2"]}})
    assert error.errors()[0]["input"] == MASK
    assert "hunter2" not in str(error) + error.json() + repr(error)


def test_a_secret_inside_a_container_is_masked():
    @cfg
    class Keys:
        keys: list[SecretStr]

    with pytest.raises(ConfigError) as info:
        build(Keys, {"keys": ["fine", 123]}, "f")
    assert "123" not in info.value.json()


# ── ConfigError ─────────────────────────────────────────────────────────────

def test_config_error_is_both_an_invalid_data_error_and_a_value_error():
    error = _error(App, {"name": "x"})
    assert isinstance(error, InvalidDataError) and isinstance(error, ValueError) and isinstance(error, TypeError)
    assert error.file == "app.toml" and error.schema is App


def test_its_message_names_the_file_the_schema_and_every_key():
    error = _error(App, {"name": 1, "db": {"host": "h", "password": "p", "tags": [1]}})
    lines = str(error).splitlines()
    assert lines[0] == "2 problems in app.toml for App:"
    assert any(line.strip().startswith("db.tags[0]:") for line in lines) and any(line.strip().startswith("name:") for line in lines)


def test_errors_and_json_options():
    error = _error(App, {"name": 1, "db": {"host": "h", "password": "p"}})
    assert "input" not in error.errors(include_input = False)[0]
    assert "ctx" not in error.errors(include_context = False)[0]
    assert json.loads(error.json()) == error.errors()
    assert json.loads(error.json(include_input = False)) == error.errors(include_input = False)
    entries = error.errors()
    entries[0]["loc"].append("changed")                              # a copy: the error is unchanged
    assert error.errors()[0]["loc"] == ["app.toml", "name"]


def test_it_pickles():
    error = _error(App, {"name": 1, "db": {"host": "h", "password": "p"}})
    back = pickle.loads(pickle.dumps(error))
    assert type(back) is ConfigError and back.errors() == error.errors() and str(back) == str(error)


def test_one_problem_reads_in_the_singular():
    assert str(_error(App, {"db": GOOD["db"]})).startswith("1 problem in app.toml")


def test_the_warning_is_plain_as_text_and_coloured_on_a_terminal(monkeypatch):
    monkeypatch.setattr(tint, "_CHOSEN", True)
    warning = ConfigWarning("app.ini", Loose, [{"type": "unknown", "loc": ["app.ini", "db", "typo"], "msg": "m"}])
    assert str(warning) == f"1 unknown key ignored in app.ini for Loose ({LOOSE_AT}):\n    -> db.typo"
    assert warning.shown() == ("\x1b[1m\x1b[31m1\x1b[0m unknown key ignored in \x1b[36mapp.ini\x1b[0m "
                               f"for \x1b[1m\x1b[33mLoose\x1b[0m (\x1b[36m{LOOSE_AT}\x1b[0m):\n    -> \x1b[1m\x1b[31mdb.typo\x1b[0m")
    text = warnings.formatwarning(warning, ConfigWarning, "run.py", 142, "load_cfg()")
    assert text.startswith("\x1b[36mrun.py\x1b[0m:\x1b[1m\x1b[33m142\x1b[0m: \x1b[1m\x1b[38;2;255;165;0mConfigWarning\x1b[0m: ")
    assert text.endswith(warning.shown() + "\n  load_cfg()\n")


# ── lists and dictionaries of sections ──────────────────────────────────────

@cfg
class Server:
    host: str
    port: Port = 80
    token: SecretStr | None = None


@cfg
class Fleet:
    servers: list[Server]
    pair: tuple[Server, ...] = ()
    by_region: dict[str, Server] = field(default_factory = dict)
    spare: list[Server] | None = None


def test_a_list_a_tuple_and_a_dictionary_of_sections_are_built_item_by_item():
    fleet = build(Fleet, {"servers": [{"host": "a"}, {"host": "b", "port": "81"}], "pair": [{"host": "c"}],
                          "by_region": {"eu": {"host": "d"}}, "spare": None}, "f")
    assert fleet.servers == [Server(host = "a"), Server(host = "b", port = 81)]
    assert fleet.pair == (Server(host = "c"),) and fleet.by_region == {"eu": Server(host = "d")} and fleet.spare is None
    assert build(Fleet, {"servers": []}, "f") == Fleet(servers = [])
    assert Fleet.from_dict(to_dict(fleet)) == fleet


def test_problems_in_them_have_the_index_or_the_key():
    error = _error(Fleet, {"servers": [{"host": "a"}, {"port": 0, "x": 1}, "text"], "by_region": {"eu": {"host": 1}}})
    assert {tuple(p["loc"][1:]): p["type"] for p in error.errors()} == {
        ("servers", 1, "x"): "unknown", ("servers", 1, "host"): "missing", ("servers", 1, "port"): "invalid",
        ("servers", 2): "section", ("by_region", "eu", "host"): "type"}
    assert "    servers[1].port: " in str(error) and "    by_region.eu.host: " in str(error)
    assert "    servers[2]: expected a section of keys, got str" in str(error)


@pytest.mark.parametrize("data, where, said", [
    ({"servers": "a, b"}, "servers", "expected a list of sections, got str"),
    ({"servers": {"host": "a"}}, "servers", "expected a list of sections, got dict"),
    ({"servers": [], "pair": 5}, "pair", "expected a list of sections, got int"),
    ({"servers": [], "by_region": [{"host": "a"}]}, "by_region", "expected sections by name, got list"),
    ({"servers": None}, "servers", "expected a list of sections, got NoneType"),
    ], ids = str)
def test_what_is_no_list_or_dictionary_of_sections_is_one_problem(data, where, said):
    error = _error(Fleet, data)
    assert [(p["type"], p["loc"], p["msg"]) for p in error.errors()] == [("section", ["app.toml", where], said)]


def test_what_stands_in_a_sections_place_is_never_shown():
    error = _error(Fleet, {"servers": "hunter2", "by_region": {"eu": ["hunter3"]}})
    assert all("input" not in p for p in error.errors())
    assert "hunter" not in str(error) + error.json() + repr(error)


def test_a_secret_in_a_list_of_sections_is_masked():
    error = _error(Fleet, {"servers": [{"host": "a", "token": ["hunter2"]}]})
    assert error.errors()[0]["loc"] == ["app.toml", "servers", 0, "token"] and error.errors()[0]["input"] == MASK
    assert "hunter2" not in str(error) + error.json()


def test_strict_false_reaches_the_items_too():
    @cfg(strict = False)
    class LooseFleet:
        servers: list[Server]
        by_region: dict[str, Server]

    with pytest.warns(ConfigWarning) as caught:
        build(LooseFleet, {"servers": [{"host": "a", "x": 1}], "by_region": {"eu": {"host": "b", "y": 1}}}, "f")
    assert [p["loc"] for p in caught[0].message.errors()] == [["f", "servers", 0, "x"], ["f", "by_region", "eu", "y"]]


def test_the_warning_says_where_the_class_is_written():
    warning = ConfigWarning("app.toml", Loose, [{"type": "unknown", "loc": ["app.toml", "x"], "msg": "m"}])
    assert f"for Loose ({__file__}:39):" in str(warning)            # the line of @cfg(strict = False) above


def test_the_warning_leaves_the_place_out_where_it_cant_be_found():
    made = cfg(strict = False)(type("Made", (), {"__annotations__": {"name": str}}))    # no file, no first line
    namespace: dict = {}
    exec("from cfgcast import cfg\n@cfg(strict = False)\nclass Typed:\n    name: str\n", namespace)    # a file that isn't there
    for schema in (made, namespace["Typed"]):
        with pytest.warns(ConfigWarning) as caught:
            build(schema, {"name": "x", "y": 1}, "app.toml")
        assert str(caught[0].message) == f"1 unknown key ignored in app.toml for {schema.__name__}:\n    -> y"


# ── a __post_init__ that raises ─────────────────────────────────────────────

@cfg
class Checked:
    host: str
    password: SecretStr = SecretStr("")
    port: int = 1

    def __post_init__(self) -> None:
        if self.host == "bad":
            raise ValueError(f"host may not be 'bad' (password {self.password.get_secret_value()})")
        if self.host == "stop":
            raise KeyboardInterrupt
        if self.host == "quiet":
            raise RuntimeError


@cfg
class Guarded:
    name: str
    main: Checked
    others: list[Checked] = field(default_factory = list)
    token: SecretStr = SecretStr("")

    def __post_init__(self) -> None:
        if self.name == "bad":
            raise LookupError(f"no {self.name}: {self.token.get_secret_value()} / {self.main.password.get_secret_value()}")


def test_every_post_init_that_raises_is_a_problem_next_to_the_others():
    data = {"name": 1, "main": {"host": "bad"}, "others": [{"host": "h"}, {"host": "bad"}, {"host": "h", "port": "x"}]}
    error = _error(Guarded, data)
    assert [(e["type"], e["loc"]) for e in error.errors()] == [
        ("post_init", ["app.toml", "main"]),
        ("post_init", ["app.toml", "others", 1]),
        ("type", ["app.toml", "others", 2, "port"]),
        ("type", ["app.toml", "name"]),
        ]
    entry = error.errors()[0]
    assert entry["msg"].startswith("ValueError: host may not be 'bad'") and "input" not in entry
    assert entry["ctx"] == {"schema": "Checked", "exception": "ValueError"}
    assert "    main: ValueError: host may not be 'bad'" in str(error)
    assert "    others[1]: ValueError" in str(error)


def test_the_first_exception_is_the_cause_and_the_top_level_is_the_whole_file():
    error = _error(Guarded, {"name": "bad", "main": {"host": "h"}})
    assert isinstance(error.__cause__, LookupError)
    assert error.errors()[0]["loc"] == ["app.toml"] and "    (the whole file): LookupError: no bad" in str(error)
    assert _error(Guarded, {"name": 1, "main": {"host": "h"}}).__cause__ is None


def test_a_secret_in_the_text_of_an_exception_is_masked():
    error = _error(Guarded, {"name": "n", "main": {"host": "bad", "password": "hunter2"}})
    assert "hunter2" not in str(error) and "hunter2" not in error.json() and MASK in str(error)
    #* The class above knows the secrets of its sections too.
    error = _error(Guarded, {"name": "bad", "token": "t0ken", "main": {"host": "h", "password": "hunter2"}})
    assert "hunter2" not in str(error) and "t0ken" not in str(error) and str(error).count(MASK) == 2


def test_an_exception_without_text_and_one_that_is_no_exception():
    assert _error(Guarded, {"name": "n", "main": {"host": "quiet"}}).errors()[0]["msg"] == "RuntimeError"
    with pytest.raises(KeyboardInterrupt):
        Guarded.from_dict({"name": "n", "main": {"host": "stop"}})


def test_a_direct_construction_raises_the_exception_as_it_is():
    with pytest.raises(ValueError, match = "host may not be 'bad'"):
        Checked(host = "bad")
