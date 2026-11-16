"""`load_cfg(..., section = ...)`: a configuration that is one part of a bigger file."""
import pytest

from enforced.data_types import SecretStr
from cfgcast import ConfigError, ConfigWarning, cfg, field, load_cfg, optional


@cfg
class Pool:
    size: int = 1


@cfg
class App:
    name: str
    hosts: list[str] = field(default_factory = list)
    token: SecretStr = SecretStr("t")
    pool: Pool = Pool()


PYPROJECT = '''
[project]
name = "x"

[tool.myapp]
name = "shop"
hosts = ["a", "b"]

[tool.myapp.pool]
size = 5

[tool.other]
name = 3
'''


def test_a_table_of_pyproject_toml(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT)
    app = load_cfg(path, App, section = "tool.myapp")
    assert (app.name, app.hosts, app.pool.size) == ("shop", ["a", "b"], 5)
    assert App.load(path, section = ["tool", "myapp"]) == app


@pytest.mark.parametrize("suffix, text", [
    ("json", '{"services": {"web.api": {"name": "n", "hosts": ["a"]}}}'),
    ("yaml", "services:\n  web.api:\n    name: n\n    hosts: [a]\n"),
    ])
def test_a_list_of_keys_for_a_key_with_a_dot(tmp_path, suffix, text):
    if suffix == "yaml":
        pytest.importorskip("yaml")
    path = tmp_path / f"all.{suffix}"
    path.write_text(text)
    assert load_cfg(path, App, section = ["services", "web.api"]).hosts == ["a"]


def test_ini_lists_are_split_inside_the_section(tmp_path):
    path = tmp_path / "setup.cfg"
    path.write_text("[metadata]\nname = pkg\nhosts = no, split\n\n[myapp]\nname = shop\nhosts = a, b\n\n[myapp.pool]\nsize = 4\n")
    app = load_cfg(path, App, section = "myapp")
    assert (app.name, app.hosts, app.pool.size) == ("shop", ["a", "b"], 4)


def test_a_problem_has_the_whole_path_in_the_file(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT.replace("size = 5", "size = \"big\"\nextra = 1").replace('name = "shop"', "token = 5"))
    with pytest.raises(ConfigError) as caught:
        load_cfg(path, App, section = "tool.myapp")
    found = {tuple(e["loc"][1:]): e for e in caught.value.errors()}
    assert set(found) == {("tool", "myapp", "name"), ("tool", "myapp", "token"), ("tool", "myapp", "pool", "size"),
                          ("tool", "myapp", "pool", "extra")}
    assert found[("tool", "myapp", "name")]["type"] == "missing" and found[("tool", "myapp", "token")]["input"] == "**********"
    assert "tool.myapp.pool.size" in str(caught.value)


def test_a_file_without_the_section_is_a_missing_problem(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.other]\nname = "secret-ish"\n')
    with pytest.raises(ConfigError) as caught:
        load_cfg(path, App, section = "tool.myapp")
    assert caught.value.errors() == [
        {"type": "missing", "loc": [str(path), "tool", "myapp"], "msg": "missing section", "ctx": {"schema": "App"}}]
    assert "tool.myapp: missing section" in str(caught.value)


def test_a_section_that_is_a_value_is_a_section_problem_without_its_value(tmp_path):
    path = tmp_path / "a.json"
    path.write_text('{"tool": "hunter2"}')
    with pytest.raises(ConfigError) as caught:
        load_cfg(path, App, section = "tool.myapp")
    (problem,) = caught.value.errors()
    assert problem["type"] == "section" and problem["loc"][1:] == ["tool"] and "input" not in problem
    assert "hunter2" not in str(caught.value) and "expected a section of keys, got str" in str(caught.value)


def test_every_file_of_a_list_is_read_at_the_section_and_each_one_missing_it_is_said(tmp_path):
    base, local, none = tmp_path / "base.toml", tmp_path / "local.json", tmp_path / "none.toml"
    base.write_text('[app]\nname = "base"\nhosts = ["a"]\n[app.pool]\nsize = 2\n')
    local.write_text('{"app": {"name": "local"}, "other": 1}')
    none.write_text("x = 1\n")
    app = load_cfg([base, local, optional(tmp_path / "absent.toml")], App, section = "app")
    assert (app.name, app.hosts, app.pool.size) == ("local", ["a"], 2)
    with pytest.raises(ConfigError) as caught:
        load_cfg([none, base, none], App, section = "app")
    assert [e["loc"] for e in caught.value.errors()] == [[str(none), "app"], [str(none), "app"]]


def test_variables_name_the_schemas_keys_and_their_problems_have_no_section(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT)
    env = {"APP_POOL__SIZE": "9", "APP_NAME": "env"}
    app = load_cfg(path, App, section = "tool.myapp", env_prefix = "APP_", env = env)
    assert (app.name, app.pool.size) == ("env", 9)
    with pytest.raises(ConfigError) as caught:
        load_cfg(path, App, section = "tool.myapp", env_prefix = "APP_", env = {"APP_POOL__SIZE": "big"})
    assert [e["loc"] for e in caught.value.errors()] == [["APP_POOL__SIZE", "pool", "size"]]


def test_an_ignored_key_is_named_with_the_section(tmp_path):
    @cfg(strict = False)
    class Loose:
        name: str

    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT)
    with pytest.warns(ConfigWarning, match = r"tool\.myapp\.hosts") as caught:
        assert Loose.load(path, section = "tool.myapp").name == "shop"
    assert [e["loc"][1:] for e in caught[0].message.errors()] == [["tool", "myapp", "hosts"], ["tool", "myapp", "pool"]]


@pytest.mark.parametrize("bad", ["", "a..b", [], ["a", ""], ["a", 1], 3, ".a"])
def test_a_section_that_is_no_path_of_keys_is_refused(tmp_path, bad):
    path = tmp_path / "a.toml"
    path.write_text("")
    with pytest.raises(TypeError, match = "section must be keys"):
        load_cfg(path, App, section = bad)
