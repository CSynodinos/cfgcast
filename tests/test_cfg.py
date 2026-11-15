"""`@cfg`: what it makes of a class, and what it refuses."""
from dataclasses import FrozenInstanceError, field, fields, is_dataclass
import dataclasses
import pickle

import pytest

from enforced import InvalidDataError
from enforced.data_types import Port
from cfgcast import cfg
from cfgcast._base import OPTIONS_ATTR, SECTIONS_ATTR, Config, is_config


@cfg
class Database:
    host: str
    port: Port = 5432
    replicas: list[str] = field(default_factory = list)


@cfg(strict = False, frozen = False)
class App:
    name: str
    db: Database
    cache: Database | None = None
    later: "Later | None" = None                                    # a section written further down


@cfg
class Later:
    path: str


# ── what it makes ───────────────────────────────────────────────────────────

def test_it_is_an_enforced_dataclass_in_place_with_keyword_only_fields():
    assert is_dataclass(Database) and Database.__bases__ == (object,)
    assert Database.__name__ == "Database" and Database.__module__ == __name__
    assert [f.name for f in fields(Database)] == ["host", "port", "replicas"]
    with pytest.raises(TypeError):
        Database("h")                                               # type: ignore[misc]  # keyword-only
    db = Database(host = "h")
    assert db.port == 5432 and db.replicas == []


def test_values_are_checked_with_coerce_and_deep_always_on():
    assert Database(host = "h", port = "8080").port == 8080         # type: ignore[arg-type]  # coerce
    assert type(Database(host = "h", port = 80).port) is Port
    with pytest.raises(InvalidDataError, match = "65535"):
        Database(host = "h", port = 0)
    with pytest.raises(InvalidDataError):
        Database(host = "h", replicas = ["a", 1])                   # type: ignore[list-item]  # deep: items checked


def test_frozen_by_default_and_options_on_the_class():
    db = Database(host = "h")
    with pytest.raises(FrozenInstanceError):
        db.host = "x"                                               # type: ignore[misc]
    app = App(name = "a", db = db)
    app.name = "b"                                                  # frozen = False
    assert app.name == "b"
    with pytest.raises(InvalidDataError):
        app.name = 5                                                # type: ignore[assignment]  # still checked
    assert vars(Database)[OPTIONS_ATTR] == (True, True) and vars(App)[OPTIONS_ATTR] == (False, False)


def test_sections_are_found_when_first_read_also_one_written_further_down():
    assert getattr(App, SECTIONS_ATTR) == {"db": Database, "cache": Database, "later": Later}
    assert isinstance(vars(App)[SECTIONS_ATTR], dict)               # worked out once, then kept
    assert getattr(Database, SECTIONS_ATTR) == {}


def test_the_members_it_adds_bind_to_the_class():
    for name in ("load", "from_dict"):
        assert vars(Database)[name] is vars(Config)[name]
        assert getattr(Database, name).__self__ is Database and getattr(App, name).__self__ is App


def test_a_class_written_in_a_function():
    @cfg
    class Inner:
        n: int

    @cfg
    class Outer:
        inner: Inner

    assert Outer(inner = Inner(n = "3")).inner.n == 3               # type: ignore[arg-type]
    assert getattr(Outer, SECTIONS_ATTR) == {"inner": Inner}


def test_instances_pickle_and_compare():
    app = App(name = "a", db = Database(host = "h", replicas = ["r"]))
    assert pickle.loads(pickle.dumps(app)) == app
    assert dataclasses.replace(app, name = "b").name == "b"


# ── is_config ───────────────────────────────────────────────────────────────

def test_is_config():
    assert is_config(Database) and is_config(Database(host = "h"))
    assert not is_config(Config) and not is_config(object) and not is_config(5)

    @dataclasses.dataclass
    class Plain:
        x: int
    assert not is_config(Plain)


# ── what it refuses ─────────────────────────────────────────────────────────

def test_it_refuses_what_is_no_plain_class():
    for wrong in (5, "x", lambda: None):
        with pytest.raises(TypeError, match = "decorates a class"):
            cfg(wrong)                                              # type: ignore[call-overload]
    with pytest.raises(TypeError, match = "configuration already"):
        cfg(Database)

    @dataclasses.dataclass
    class Made:
        x: int
    with pytest.raises(TypeError, match = "dataclass already"):
        cfg(Made)


@pytest.mark.parametrize("options", [{"strict": 1}, {"frozen": "no"}, {"strict": None}], ids = str)
def test_options_must_be_bool(options):
    with pytest.raises(TypeError, match = "True or False"):
        cfg(**options)


def test_a_configuration_is_not_inheritable():
    with pytest.raises(TypeError, match = "not inheritable"):
        class Below(Database):                                      # refused at the class statement
            extra: int = 1

    with pytest.raises(TypeError, match = "not inheritable"):
        @cfg
        class Decorated(Database):
            extra: int = 1


def test_a_class_s_own_init_subclass_is_kept():
    @cfg
    class Open:
        x: int = 0

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__(**kwargs)

    class Below(Open):                                              # the owner chose to allow it
        pass
    assert not is_config(Below)


@pytest.mark.parametrize("member", ["load", "from_dict"])
def test_the_names_it_adds_are_reserved(member):
    with pytest.raises(TypeError, match = f"{member}: the name is taken"):
        cfg(type("Field", (), {"__annotations__": {member: int}}))
    with pytest.raises(TypeError, match = f"{member}: the name is taken"):
        cfg(type("Method", (), {member: lambda self: None}))


def test_nothing_is_changed_on_a_class_it_refuses():
    class Taken:
        load: int
    with pytest.raises(TypeError):
        cfg(Taken)
    assert OPTIONS_ATTR not in vars(Taken) and not is_dataclass(Taken)


# ── a direct construction or assignment raises a ConfigError too (enforced's error = ConfigError) ──

from enforced.data_types import SecretStr                          # noqa: E402
from cfgcast import ConfigError                                 # noqa: E402
from cfgcast._errors import MASK                                # noqa: E402


@cfg
class Vault:
    host: str
    password: SecretStr
    keys: list[SecretStr] = field(default_factory = list)


@cfg(frozen = False)
class Settings:
    name: str
    vault: Vault | None = None


def _config_error(call) -> ConfigError:
    for _ in range(2):                                              # the general path, then the compiled wrapper
        with pytest.raises(ConfigError) as info:
            call()
    return info.value


def test_a_direct_construction_raises_a_config_error_without_a_file():
    error = _config_error(lambda: Database(host = 5))
    assert isinstance(error, InvalidDataError) and isinstance(error, ValueError)
    assert error.file is None and error.schema is None and error.entries is None
    assert error.errors()[0]["loc"] == ["host"] and error.errors()[0]["type"] == "type"
    assert str(error).splitlines() == ["1 problem in Database:", "    host: expected type: 'str', got type 'int'"]


def test_every_kind_of_wrong_construction_and_an_assignment():
    assert [e["type"] for e in _config_error(lambda: Database()).errors()] == ["missing"]
    assert _config_error(lambda: Database(host = "h", port = 0)).errors()[0]["type"] == "invalid"
    assert _config_error(lambda: Database(host = "h", nope = 1)).errors()[0]["type"] == "binding"
    assert _config_error(lambda: Database("h")).errors()[0]["type"] == "binding"         # keyword-only
    settings = Settings(name = "a")
    error = _config_error(lambda: setattr(settings, "name", 5))
    assert error.errors()[0]["type"] == "field" and error.errors()[0]["loc"] == ["name"]
    assert str(error).startswith("1 problem in Settings:")


def test_a_secret_is_masked_through_both_doors():
    direct = _config_error(lambda: Vault(host = "h", password = ["hunter2"], keys = ["fine", 7777]))
    assert [(e["loc"], e["input"]) for e in direct.errors()] == [(["password"], MASK), (["keys", 1], MASK)]
    assert "hunter2" not in str(direct) + direct.json() + repr(direct) and "7777" not in str(direct) + direct.json()
    settings = Settings(name = "a", vault = Vault(host = "h", password = "p"))
    loaded = _config_error(lambda: Settings.from_dict({"name": "a", "vault": {"host": "h", "password": ["hunter2"]}}))
    assert loaded.errors()[0]["loc"] == ["<dict>", "vault", "password"] and loaded.errors()[0]["input"] == MASK
    assert "hunter2" not in str(loaded) + loaded.json()
    assert settings.vault is not None


def test_both_kinds_pickle():
    direct = _config_error(lambda: Vault(host = 1, password = ["hunter2"]))
    back = pickle.loads(pickle.dumps(direct))
    assert type(back) is ConfigError and str(back) == str(direct) and back.file is None
    loaded = _config_error(lambda: Vault.from_dict({"host": 1, "password": "p"}))
    back = pickle.loads(pickle.dumps(loaded))
    assert type(back) is ConfigError and back.errors() == loaded.errors() and back.file == "<dict>"


def test_a_loaded_file_s_errors_name_the_file_as_before():
    error = _config_error(lambda: Database.from_dict({"host": 5}))
    assert error.file == "<dict>" and error.errors()[0]["loc"] == ["<dict>", "host"]
    assert str(error).startswith("1 problem in <dict> for Database:")


def test_a_quoted_section_may_be_written_after_the_class_that_names_it():
    #* The form for every version: 3.13 evaluates an unquoted annotation where it stands.
    assert Outer.from_dict({"inner": {"n": "4"}}).inner == Written_later(n = 4)


@cfg
class Outer:
    inner: "Written_later"


@cfg
class Written_later:
    n: int

