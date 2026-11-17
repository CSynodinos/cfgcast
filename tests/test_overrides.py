"""Overrides at load time: `(overrides = {...}).load(...)` and `parse_overrides`."""
import pytest

from enforced.data_types import Port, SecretStr
from cfgcast import Config, ConfigError, ConfigWarning, field, parse_overrides, sources_of


class Pool(Config, keys = "kebab-case"):
    max_size: int = 1


class Db(Config):
    host: str = "h"
    port: Port = 5432
    password: SecretStr = SecretStr("p")
    pool: Pool = field(default_factory = Pool)


class App(Config):
    name: str
    db: Db = field(default_factory = Db)
    hosts: list[str] = field(default_factory = list)
    limits: dict[str, int] = field(default_factory = dict)
    debug: bool = False


@pytest.fixture
def file(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text('name = "n"\nhosts = ["a"]\n\n[db]\nhost = "file"\nport = 1\n')
    return path


# ── load(overrides = ...) ───────────────────────────────────────────────────

def test_overrides_go_over_files_and_variables_and_sections_merge(file):
    given = {"db": {"port": "9090"}, "debug": True}
    app = App.load(file, env_prefix = "APP_", env = {"APP_DB__PORT": "2", "APP_DB__HOST": "env"}, overrides = given)
    assert (app.db.port, app.db.host, app.debug, app.name, app.hosts) == (9090, "env", True, "n", ["a"])
    assert given == {"db": {"port": "9090"}, "debug": True}             # the caller's is not changed
    assert sources_of(app)["db.port"] == "overrides" and sources_of(app)["db.host"] == "APP_DB__HOST"
    assert App.load(file, overrides = {"name": "other"}).name == "other"


def test_none_and_an_empty_dictionary_change_nothing(file):
    assert App.load(file, overrides = {}) == App.load(file, overrides = None) == App.load(file)


def test_overrides_alone_are_a_load(file):
    assert App.load([], overrides = {"name": "only"}).name == "only"
    with pytest.raises(ValueError, match = "no file to load"):
        App.load([])


def test_a_problem_of_an_override_names_overrides_and_masks_a_secret(file):
    with pytest.raises(ConfigError) as caught:
        App.load(file, overrides = {"db": {"port": 0, "password": 5, "prot": 1}, "hosts": ["a", 2]})
    assert sorted(e["loc"] for e in caught.value.errors()) == [
        ["overrides", "db", "password"], ["overrides", "db", "port"], ["overrides", "db", "prot"], ["overrides", "hosts", 1]]
    assert caught.value.files == (str(file), "overrides")
    assert "    overrides: db.port: invalid 'Port'" in str(caught.value)
    assert next(e for e in caught.value.errors() if e["loc"][-1] == "password")["input"] == "**********"


def test_an_override_a_lenient_schema_ignores_is_warned_about(file):
    class Loose(Config, strict = False):
        name: str

    with pytest.warns(ConfigWarning, match = "-> overrides: nope"):
        assert Loose.load(file, overrides = {"nope": 1}).name == "n"


def test_overrides_are_not_under_the_section_of_a_file(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.app]\nname = "n"\n')
    assert App.load(path, section = "tool.app", overrides = {"db": {"port": 7}}).db.port == 7
    with pytest.raises(ConfigError) as caught:
        App.load(path, section = "tool.app", overrides = {"db": {"port": 0}})
    assert caught.value.errors()[0]["loc"] == ["overrides", "db", "port"]


@pytest.mark.parametrize("bad", [[("a", 1)], "a=1", 3])
def test_overrides_that_are_no_dictionary_are_refused(file, bad):
    with pytest.raises(TypeError, match = "overrides must be a dictionary of keys"):
        App.load(file, overrides = bad)


# ── parse_overrides ─────────────────────────────────────────────────────────

def test_texts_of_a_command_line():
    arguments = ["db.port=9090", "debug=true", "hosts=a, b", "name=a=b", "db.password=", "limits={\"cpu\": 2}"]
    assert parse_overrides(arguments, App) == {
        "db": {"port": "9090", "password": ""}, "debug": "true", "hosts": ["a", "b"], "name": "a=b", "limits": {"cpu": 2}}
    assert parse_overrides([], App) == {} and parse_overrides(("hosts=[\"x,y\"]",), App) == {"hosts": ["x,y"]}


def test_keys_are_matched_to_the_schema_and_an_unknown_one_is_kept_for_the_load(file):
    assert parse_overrides(["DB.POOL.max_size=3", "db.pool.max-size=4", "Debug=1"], App) == {
        "db": {"pool": {"max-size": "4"}}, "debug": "1"}
    given = parse_overrides(["db.prot=1", "nope.deep=x"], App)
    assert given == {"db": {"prot": "1"}, "nope": {"deep": "x"}}
    with pytest.raises(ConfigError) as caught:
        App.load(file, overrides = given)
    assert sorted(e["loc"] for e in caught.value.errors()) == [["overrides", "db", "prot"], ["overrides", "nope"]]


def test_parsed_overrides_load(file):
    app = App.load(file, overrides = parse_overrides(["db.port=9090", "debug=yes", "hosts=x,y"], App))
    assert (app.db.port, app.debug, app.hosts, app.db.host) == (9090, True, ["x", "y"], "file")


@pytest.mark.parametrize("arguments", [["debug"], ["=x"], ["db..port=1"], ["a=1", ".b=2"], ["  =1"]])
def test_an_argument_that_is_no_key_and_value_names_its_position_only(arguments):
    with pytest.raises(ValueError, match = rf"override {len(arguments)} is not KEY=VALUE") as caught:
        parse_overrides(arguments, App)
    assert "debug" not in str(caught.value)


def test_a_key_and_a_key_inside_it_are_refused_without_a_value():
    for arguments in (["db=hunter2", "db.port=1"], ["db.port=1", "db=hunter2"], ["name=hunter2", "name.x=1"]):
        with pytest.raises(ValueError, match = "overrides give both") as caught:
            parse_overrides(arguments, App)
        assert "hunter2" not in str(caught.value)


def test_a_list_that_starts_as_json_and_is_not_names_the_key_only():
    with pytest.raises(ValueError, match = r"hosts: starts with '\['") as caught:
        parse_overrides(["hosts=[hunter2"], App)
    assert "hunter2" not in str(caught.value)


def test_what_is_no_list_of_texts_or_no_schema_is_refused():
    for bad in ("db.port=1", 3, None):
        with pytest.raises(TypeError, match = "takes a list of KEY=VALUE texts"):
            parse_overrides(bad, App)
    with pytest.raises(TypeError, match = "argument 2 is no text"):
        parse_overrides(["a=1", 2], App)
    with pytest.raises(TypeError, match = "is no configuration"):
        parse_overrides([], dict)
