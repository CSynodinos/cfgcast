"""`cfgcast.field` and keys that are no Python names: `field(key = ...)`, `@cfg(keys = "kebab")`."""
from typing import ClassVar
import dataclasses
import warnings

import pytest

from enforced.data_types import SecretStr
from cfgcast import ConfigError, ConfigWarning, cfg, field, from_dict, load_cfg, to_dict, write
from cfgcast._base import SECTIONS_ATTR
from cfgcast._hints import key_of, keys_of, said


@cfg(keys = "kebab")
class Pool:
    max_size: int = 10
    min_size: int = field(default = 1, doc = "connections kept open")
    class_: str = "a"
    raw_name: str = field(default = "x", key = "raw_name")              # its own key wins over the style


@cfg
class App:
    name: str
    type_: str = field(key = "type")
    pool: Pool = field(default_factory = Pool, key = "db-pool")
    tags: list[str] = field(default_factory = list, key = "Tags")
    token: SecretStr = field(default = SecretStr("t"), key = "api-token")


# ── field ───────────────────────────────────────────────────────────────────

def test_field_is_a_dataclasses_field_that_remembers_key_and_doc():
    by_name = {f.name: f for f in dataclasses.fields(Pool)}
    assert said(by_name["min_size"], "doc") == "connections kept open" and said(by_name["min_size"], "key") is None
    assert said(by_name["max_size"], "key") is None and said(by_name["raw_name"], "key") == "raw_name"
    assert Pool().min_size == 1 and App(name = "a", type_ = "b").pool == Pool()


def test_field_keeps_the_callers_metadata_and_the_other_arguments():
    @cfg
    class C:
        a: int = field(default = 1, metadata = {"mine": 2}, repr = False)
        b: list[int] = field(default_factory = list)
        c: int = field(init = False, default = 3)

    by_name = {f.name: f for f in dataclasses.fields(C)}
    assert by_name["a"].metadata["mine"] == 2 and by_name["a"].repr is False
    assert C().b == [] and C().c == 3 and "c" not in to_dict(C())


@pytest.mark.parametrize("bad", [{"key": ""}, {"key": 3}, {"doc": "  "}, {"doc": b"x"}])
def test_field_refuses_a_key_or_doc_that_is_no_text(bad):
    with pytest.raises(TypeError, match = "must be text that isn't empty"):
        field(**bad)


def test_field_refuses_a_default_and_a_factory_together():
    with pytest.raises(ValueError):
        field(default = 1, default_factory = int)


def test_dataclasses_field_still_works_in_a_cfg_class():
    @cfg
    class C:
        items: list[int] = dataclasses.field(default_factory = list)

    assert C.from_dict({"items": ["1"]}).items == [1]


# ── keys ────────────────────────────────────────────────────────────────────

def test_the_keys_of_a_class():
    assert list(keys_of(Pool)) == ["max-size", "min-size", "class", "raw_name"]
    assert list(keys_of(App)) == ["name", "type", "db-pool", "Tags", "api-token"]
    assert key_of(App, dataclasses.fields(App)[1]) == "type"
    assert list(getattr(App, SECTIONS_ATTR)) == ["db-pool"]


def test_from_dict_and_to_dict_use_the_keys_and_attributes_the_names():
    data = {"name": "n", "type": "web", "db-pool": {"max-size": "5", "class": "b"}, "Tags": ["x"], "api-token": "s"}
    app = from_dict(App, data)
    assert (app.type_, app.pool.max_size, app.pool.class_, app.tags) == ("web", 5, "b", ["x"])
    assert to_dict(app, reveal_secrets = True, plain = True) == {
        "name": "n", "type": "web", "db-pool": {"max-size": 5, "min-size": 1, "class": "b", "raw_name": "x"},
        "Tags": ["x"], "api-token": "s"}
    assert from_dict(App, to_dict(app)) == app


def test_a_kebab_style_is_of_its_class_only():
    assert "db-pool" in keys_of(App) and "type_" not in keys_of(App) and "api_token" not in keys_of(App)

    @cfg(keys = "kebab")
    class Outer:
        inner_part: App

    assert list(keys_of(Outer)) == ["inner-part"] and "type" in keys_of(App)


def test_the_fields_name_in_a_file_is_an_unknown_key_that_names_the_key():
    with pytest.raises(ConfigError) as caught:
        from_dict(App, {"name": "n", "type_": "web", "db-pool": {"max_size": 3}})
    found = {tuple(e["loc"][1:]): e for e in caught.value.errors()}
    assert set(found) == {("type_",), ("type",), ("db-pool", "max_size")}
    assert found[("type_",)]["type"] == "unknown" and "the key of this field is 'type'" in found[("type_",)]["msg"]
    assert "the key of this field is 'max-size'" in found[("db-pool", "max_size")]["msg"]
    assert found[("type",)]["type"] == "missing"


def test_a_wrong_value_is_reported_at_its_key_and_a_secret_stays_masked():
    with pytest.raises(ConfigError) as caught:
        from_dict(App, {"name": "n", "type": "w", "db-pool": {"max-size": "many"}, "Tags": [1, "a"], "api-token": 5})
    found = {tuple(e["loc"][1:]): e for e in caught.value.errors()}
    assert set(found) == {("db-pool", "max-size"), ("Tags", 0), ("api-token",)}
    assert found[("api-token",)]["input"] == "**********"
    assert "db-pool.max-size" in str(caught.value) and "max_size" not in str(caught.value)


def test_an_ignored_key_under_lenient_names_the_keys():
    @cfg(strict = False, keys = "kebab")
    class Loose:
        max_size: int = 1

    with pytest.warns(ConfigWarning, match = "max_size"):
        assert Loose.from_dict({"max_size": 2, "max-size": 3}).max_size == 3


@pytest.mark.parametrize("suffix", ["json", "toml", "yaml", "ini"])
def test_every_format_writes_and_reads_the_keys(tmp_path, suffix):
    if suffix == "yaml":
        pytest.importorskip("yaml")
    app = App(name = "n", type_ = "web", pool = Pool(max_size = 7), tags = ["a", "b"])
    path = tmp_path / f"app.{suffix}"
    write(app, path, {"db-pool": {"min-size": 2}})
    text = path.read_text()
    assert "max-size" in text and "db-pool" in text and "type_" not in text and "max_size" not in text
    loaded = load_cfg(path, App)
    assert loaded.pool == Pool(max_size = 7, min_size = 2) and loaded.tags == ["a", "b"] and loaded.type_ == "web"


def test_an_override_by_the_fields_name_is_refused(tmp_path):
    with pytest.raises(ConfigError, match = "the key of this field is 'type'"):
        write(App(name = "n", type_ = "web"), tmp_path / "a.json", {"type_": "x"})
    assert not (tmp_path / "a.json").exists()


def test_an_environment_variable_names_a_key_with_underscores_in_any_case(tmp_path):
    path = tmp_path / "app.json"
    path.write_text('{"name": "n", "type": "web"}')
    env = {"APP_DB_POOL__MAX_SIZE": "3", "app_type": "job", "APP_TAGS": "a,b", "APP_API_TOKEN": "s", "APP_TYPE_": "no"}
    app = load_cfg(path, App, env_prefix = "APP_", env = env)
    assert (app.pool.max_size, app.type_, app.tags, str.__str__(app.token)) == (3, "job", ["a", "b"], "s")


# ── what @cfg refuses ───────────────────────────────────────────────────────

def test_two_fields_with_one_key_are_refused():
    with pytest.raises(TypeError, match = "the fields a and b have the same key 'x'"):
        @cfg
        class C:
            a: int = field(key = "x")
            b: int = field(key = "x")

    with pytest.raises(TypeError, match = "the fields max_size and other have the same key 'max-size'"):
        @cfg(keys = "kebab")
        class D:
            max_size: int
            other: int = field(key = "max-size")

    with pytest.raises(TypeError, match = "the same key 'class'"):
        @cfg(keys = "kebab")
        class E:
            class_: int
            class__: int


def test_a_class_variable_is_no_field_and_takes_no_key():
    @cfg(keys = "kebab")
    class C:
        max_size_: ClassVar[int] = 3
        max_size: int = 1

    assert list(keys_of(C)) == ["max-size"]


def test_a_key_style_that_does_not_exist_is_refused():
    with pytest.raises(TypeError, match = "keys must be None or one of 'kebab'"):
        cfg(keys = "camel")                                             # type: ignore[call-overload]


def test_no_warning_where_every_key_is_known():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        from_dict(App, {"name": "n", "type": "w"})
