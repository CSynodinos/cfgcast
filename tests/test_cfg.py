"""`Config`: what it makes of a class below it, what a class below that inherits, and what it refuses."""
from dataclasses import FrozenInstanceError, field, fields, is_dataclass
import copy
import dataclasses
import pickle

import pytest

from enforced import InvalidDataError
from enforced.data_types import Port, SecretStr
from cfgcast import Config, FrozenConfigError, field as cfg_field
from cfgcast._base import SECTIONS_ATTR, is_config
from cfgcast._hints import OPTIONS_ATTR


class Database(Config):
    host: str
    port: Port = 5432
    replicas: list[str] = field(default_factory = list)


class App(Config, strict = False, frozen = False):
    name: str
    db: Database
    cache: Database | None = None
    later: "Later | None" = None                                    # a section written further down


class Later(Config):
    path: str


# ── what it makes ───────────────────────────────────────────────────────────

def test_it_is_an_enforced_dataclass_in_place_with_keyword_only_fields():
    assert is_dataclass(Database) and Database.__bases__ == (Config,) and not is_dataclass(Config)
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
    assert vars(Database)[OPTIONS_ATTR] == (True, True, None, None) and vars(App)[OPTIONS_ATTR][:2] == (False, False)


# ── a frozen configuration: FrozenConfigError ──────────────────────────────

class Pool(Config, keys = "kebab-case"):
    max_size: int = 1


class Frozen(Config):
    name: str = "n"
    token: SecretStr = SecretStr("t")
    pool: Pool = field(default_factory = Pool)
    pools: list[Pool] = field(default_factory = list)


def _frozen(change) -> FrozenConfigError:
    with pytest.raises(FrozenConfigError) as info:
        change()
    return info.value


def test_an_assignment_to_a_frozen_configuration_is_a_frozen_config_error():
    error = _frozen(lambda: setattr(Frozen(), "name", "other"))
    assert isinstance(error, FrozenInstanceError) and isinstance(error, AttributeError)
    assert str(error) == ("Frozen is frozen: name can't be assigned "
                          "(dataclasses.replace makes a changed copy; frozen = False on the class allows it)")
    assert error.__suppress_context__ and error.__cause__ is None   # no FrozenInstanceError behind it in the traceback
    assert "pool can't be assigned" in str(_frozen(lambda: setattr(Frozen(), "pool", Pool())))
    assert "nope can't be assigned" in str(_frozen(lambda: setattr(Frozen(), "nope", 1)))     # no field: refused the same


def test_a_deletion_is_one_too():
    assert "Frozen is frozen: name can't be deleted" in str(_frozen(lambda: delattr(Frozen(), "name")))


def test_a_loaded_section_says_its_key_path_with_the_files_keys():
    loaded = Frozen.from_dict({"pool": {"max-size": 2}, "pools": [{}, {"max-size": "3"}]})
    assert str(_frozen(lambda: setattr(loaded.pool, "max_size", 5))).startswith("Pool is frozen: pool.max-size can't be assigned")
    assert str(_frozen(lambda: setattr(loaded.pools[1], "max_size", 5))).startswith("Pool is frozen: pools[1].max-size can't")
    assert str(_frozen(lambda: setattr(Pool(), "max_size", 5))).startswith("Pool is frozen: max-size can't")   # built in code


def test_the_value_is_never_in_it():
    for value in ("hunter2", SecretStr("hunter2"), ["hunter2"]):
        error = _frozen(lambda value = value: setattr(Frozen(), "token", value))
        assert "hunter2" not in str(error) + repr(error) + repr(error.args)


def test_a_class_below_is_frozen_the_same_way_and_one_that_is_not_takes_assignments():
    class Below(Frozen):
        more: int = 0

    assert str(_frozen(lambda: setattr(Below(), "more", 1))).startswith("Below is frozen: more can't be assigned")
    assert "name can't be assigned" in str(_frozen(lambda: setattr(Below(), "name", "x")))
    app = App(name = "a", db = Database(host = "h"))                # frozen = False
    app.name = "b"
    del app.cache
    assert app.name == "b" and "cache" not in vars(app)


def test_what_goes_around_it_works_as_it_did():
    loaded = Frozen.from_dict({"name": "x", "pools": [{"max-size": 2}]})
    assert dataclasses.replace(loaded, name = "y").name == "y" and loaded.name == "x"
    assert copy.deepcopy(loaded) == loaded and pickle.loads(pickle.dumps(loaded)) == loaded
    object.__setattr__(loaded, "name", "z")                          # what build and a __post_init__ do
    assert loaded.name == "z"
    error = pickle.loads(pickle.dumps(_frozen(lambda: setattr(loaded, "name", "w"))))
    assert type(error) is FrozenConfigError and "name can't be assigned" in str(error)


def test_sections_are_found_when_first_read_also_one_written_further_down():
    assert getattr(App, SECTIONS_ATTR) == {"db": Database, "cache": Database, "later": Later}
    assert isinstance(vars(App)[SECTIONS_ATTR], dict)               # worked out once, then kept
    assert getattr(Database, SECTIONS_ATTR) == {}


def test_the_members_are_inherited_and_bind_to_the_class():
    for name in ("load", "from_dict"):
        assert name not in vars(Database)
        assert getattr(Database, name).__self__ is Database and getattr(App, name).__self__ is App


def test_a_class_written_in_a_function():
    class Inner(Config):
        n: int

    class Outer(Config):
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

def test_config_itself_is_no_configuration(tmp_path):
    with pytest.raises(TypeError, match = "Config is no configuration"):
        Config.load(tmp_path / "app.toml")
    with pytest.raises(TypeError, match = "Config is no configuration"):
        Config.from_dict({})


@pytest.mark.parametrize("options", [{"strict": 1}, {"frozen": "no"}, {"strict": None}], ids = str)
def test_options_must_be_bool(options):
    with pytest.raises(TypeError, match = "True or False"):
        type("Wrong", (Config,), {}, **options)


def test_an_option_that_does_not_exist_is_refused():
    with pytest.raises(TypeError, match = "strct is no option of a configuration"):
        type("Wrong", (Config,), {}, strct = True)


@pytest.mark.parametrize("member", ["load", "from_dict"])
def test_a_field_may_not_take_the_name_of_a_member(member):
    with pytest.raises(TypeError, match = f"{member}: the name is taken"):
        type("Field", (Config,), {"__annotations__": {member: int}})


def test_a_member_may_be_overridden():
    class Defaulted(Config):
        name: str = "a"

        @classmethod
        def load(cls, file = "app.toml", *args, **kwargs):
            return super().load(file, *args, **kwargs)

    assert is_config(Defaulted) and Defaulted.from_dict({}) == Defaulted()


def test_a_class_s_own_init_subclass_is_kept():
    class Open(Config):
        x: int = 0

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__(**kwargs)
            cls.seen = True

    class Below(Open):
        y: int = 1

    assert is_config(Below) and Below.seen and [f.name for f in fields(Below)] == ["x", "y"]


# ── inheritance ─────────────────────────────────────────────────────────────

class Base(Config, strict = False, env_prefix = "APP_", keys = "kebab-case"):
    max_size: int = 1
    db: Database | None = None


class Prod(Base):
    replicas: int = 3
    max_size: int = 10                                              # a field again: its new default


def test_a_class_below_a_configuration_is_one_with_its_fields_and_its_own():
    assert is_config(Prod) and is_dataclass(Prod) and Prod.__bases__ == (Base,)
    assert [f.name for f in fields(Prod)] == ["max_size", "db", "replicas"]
    assert Prod() == Prod(max_size = 10, replicas = 3) and Prod(replicas = "4").replicas == 4     # type: ignore[arg-type]
    with pytest.raises(InvalidDataError):
        Prod(max_size = "many")                                     # type: ignore[arg-type]  # inherited fields are checked too
    assert not isinstance(Base(), Prod) and isinstance(Prod(), Base)


def test_strict_env_prefix_and_keys_are_inherited_and_frozen_is_said_by_each_class():
    assert vars(Prod)[OPTIONS_ATTR] == (False, True, "APP_", "kebab-case")

    class Plain(Prod, strict = True, env_prefix = None, keys = None):
        pass
    assert vars(Plain)[OPTIONS_ATTR] == (True, True, None, None)
    assert Prod.from_dict({"max-size": "2"}).max_size == 2               # keys = "kebab-case", from Base


def test_a_class_below_has_its_own_sections():
    class WithCache(Prod):
        cache: Database | None = None

    assert getattr(WithCache, SECTIONS_ATTR) == {"db": Database, "cache": Database}
    assert getattr(Prod, SECTIONS_ATTR) == {"db": Database}
    built = WithCache.from_dict({"db": {"host": "h"}, "cache": {"host": "c", "port": "1"}})
    assert built.db == Database(host = "h") and built.cache == Database(host = "c", port = 1)


def test_frozen_must_be_as_the_class_above():
    with pytest.raises(TypeError, match = r"Thawed: frozen must be as Prod's, True \(class Thawed\(Prod, frozen = True\)\)"):
        class Thawed(Prod, frozen = False):
            pass

    with pytest.raises(TypeError, match = "frozen must be as App's, False"):
        class Below(App):
            pass

    class Open(App, frozen = False):
        more: int = 0
    below = Open(name = "a", db = Database(host = "h"))
    below.more = 1
    assert below.more == 1


def test_two_fields_with_one_key_are_refused_across_the_classes():
    with pytest.raises(TypeError, match = "the fields max_size and maxsize have the same key 'max-size'"):
        class Clash(Prod):
            maxsize: int = cfg_field(default = 0, key = "max-size")


def test_a_class_below_two_configurations():
    class Left(Config):
        a: int = 1

    class Right(Config):
        b: int = 2

    class Both(Left, Right):
        c: int = 3

    assert [f.name for f in fields(Both)] == ["b", "a", "c"] and Both.from_dict({"a": "5"}) == Both(a = 5)


def test_a_class_below_loads_and_writes(tmp_path):
    from cfgcast import to_dict, write
    file = tmp_path / "prod.toml"
    write(Prod(replicas = 5, db = Database(host = "h")), file)
    assert Prod.load(file, env = {}) == Prod(replicas = 5, db = Database(host = "h"))
    assert to_dict(Prod.load(file, env = {"APP_REPLICAS": "6"}))["replicas"] == 6


# ── a direct construction or assignment raises a ConfigError too (enforced's error = ConfigError) ──

from enforced.data_types import SecretStr                          # noqa: E402
from cfgcast import ConfigError                                 # noqa: E402
from cfgcast._errors import MASK                                # noqa: E402


class Vault(Config):
    host: str
    password: SecretStr
    keys: list[SecretStr] = field(default_factory = list)


class Settings(Config, frozen = False):
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


class Outer(Config):
    inner: "Written_later"


class Written_later(Config):
    n: int



def test_lists_and_dictionaries_of_sections_are_marked_as_such():
    class Fleet(Config):
        main: Database
        servers: list[Database]
        pair: tuple[Database, ...] = ()
        by_region: dict[str, Database] | None = None
        names: list[str] = field(default_factory = list)            # a list of values is no section

    assert getattr(Fleet, SECTIONS_ATTR) == {"main": Database, "servers": list[Database],
                                             "pair": tuple[Database, ...], "by_region": dict[str, Database]}
