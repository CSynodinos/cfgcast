"""`write_template`: a file with every key of a schema, the ones without a value commented out."""
from enum import Enum
from typing import Any, Literal
import re

import pytest

from enforced.data_types import SecretStr
from cfgcast import ConfigError, cfg, field, load_cfg, write_template
from cfgcast._template import template_text

FORMATS = ["toml", "yaml", "ini"]


class Level(Enum):
    LOW = "low"
    HIGH = "high"


@cfg
class Pool:
    size: int = field(default = 5, doc = "connections kept open")
    timeout: float | None = None


@cfg(keys = "kebab-case")
class Database:
    host: str = field(doc = "where the database is\nname or address")
    port: int = 5432
    password: SecretStr = SecretStr("hunter2")
    pool: Pool = field(default_factory = Pool)
    read_only: bool = False


@cfg
class Server:
    host: str
    tags: list[str] = field(default_factory = list)


@cfg
class App:
    name: str
    mode: Literal["dev", "prod"] = "dev"
    level: Level = Level.LOW
    hosts: list[str] = field(default_factory = lambda: ["a", "b"])
    limits: dict[str, int] = field(default_factory = lambda: {"cpu": 2, "mem": 4})
    db: Database
    replica: Database | None = None
    named: dict[str, Server] = field(default_factory = dict)


@cfg
class Fleet:
    servers: list[Server] = field(default_factory = list)


@cfg
class Defaults:
    name: str = "app"
    pool: Pool = field(default_factory = lambda: Pool(size = 9))
    token: SecretStr = SecretStr("s3cret-token")


@cfg
class Node:
    name: str = "n"
    children: list["Node"] = field(default_factory = list)


def _needs(suffix: str) -> None:
    if suffix == "yaml":
        pytest.importorskip("yaml")


def _uncommented(text: str, *keys: str) -> str:
    """The template with these commented keys (or headers) put back, the first of each: its `#` removed."""
    for key in keys:
        text, count = re.subn(rf"(?m)^(\s*)#({re.escape(key)})(?=\s*[=:]|\s*$)", r"\1\2", text, count = 1)
        assert count, key
    return text


@pytest.mark.parametrize("suffix", FORMATS)
def test_a_template_without_required_keys_loads_and_gives_the_defaults(tmp_path, suffix):
    _needs(suffix)
    file = tmp_path / f"app.{suffix}"
    write_template(Defaults, file)
    assert load_cfg(file, Defaults) == Defaults()


@pytest.mark.parametrize("suffix", FORMATS)
def test_the_required_keys_are_what_a_template_misses(tmp_path, suffix):
    _needs(suffix)
    file = tmp_path / f"app.{suffix}"
    write_template(App, file)
    with pytest.raises(ConfigError) as caught:
        load_cfg(file, App)
    assert sorted((e["type"], tuple(e["loc"][1:])) for e in caught.value.errors()) == [("missing", ("db", "host")), ("missing", ("name",))]


@pytest.mark.parametrize("suffix", FORMATS)
def test_a_template_with_its_required_keys_filled_in_gives_the_defaults(tmp_path, suffix):
    _needs(suffix)
    file = tmp_path / f"app.{suffix}"
    write_template(App, file)
    file.write_text(_uncommented(file.read_text(), "name", "host"))
    assert load_cfg(file, App) == App(name = "", db = Database(host = ""))


@pytest.mark.parametrize("suffix", FORMATS)
def test_the_default_of_a_secret_is_never_written(tmp_path, suffix):
    file = tmp_path / f"app.{suffix}"
    write_template(Defaults, file)
    text = file.read_text()
    assert "s3cret-token" not in text
    assert "# secret: SecretStr" in text
    assert re.search(r"(?m)^#token", text)


def test_what_is_said_above_a_key():
    text = template_text(App, "toml")
    assert '# required: str\n#name = ""\n' in text
    assert "# Literal['dev', 'prod']\nmode = \"dev\"\n" in text
    assert "# Level ('low', 'high')\nlevel = \"low\"\n" in text
    assert '# where the database is\n# name or address\n# required: str\n#host = ""\n' in text
    assert "# connections kept open\nsize = 5\n" in text
    assert "# float | None\n#timeout = 0.0\n" in text
    assert "\nport = 5432\n" in text                                # an int with its value says it all: nothing above it


def test_the_keys_are_the_ones_of_a_file():
    text = template_text(App, "toml")
    assert "read-only = false" in text and "read_only" not in text


def test_a_section_that_is_none_by_default_is_commented_out_with_its_keys():
    text = template_text(App, "toml")
    assert "# Database | None\n#[replica]\n" in text
    assert "#[replica.pool]\n" in text
    assert "#port = 5432\n" in text


def test_a_section_takes_the_values_of_its_default():
    assert "size = 9" in template_text(Defaults, "toml")


def test_a_dictionary_of_values_is_a_section():
    assert "# dict[str, int]\n[limits]\ncpu = 2\nmem = 4\n" in template_text(App, "toml")
    assert "# dict[str, int]\n[limits]\ncpu = 2\nmem = 4\n" in template_text(App, "ini")
    assert "# dict[str, int]\nlimits:\n  cpu: 2\n  mem: 4\n" in template_text(App, "yaml")


@pytest.mark.parametrize("suffix", ["toml", "yaml"])
def test_the_example_of_a_list_of_sections_can_be_copied(tmp_path, suffix):
    _needs(suffix)
    file = tmp_path / f"fleet.{suffix}"
    write_template(Fleet, file)
    assert load_cfg(file, Fleet) == Fleet()
    header = ["servers", "-"] if suffix == "yaml" else ["[[servers]]"]
    file.write_text(_uncommented(file.read_text(), *header, "host", "tags"))
    assert load_cfg(file, Fleet) == Fleet(servers = [Server(host = "")])


@pytest.mark.parametrize("suffix", FORMATS)
def test_the_example_of_a_dictionary_of_sections_can_be_copied(tmp_path, suffix):
    _needs(suffix)
    file = tmp_path / f"app.{suffix}"
    write_template(App, file)
    header = ["named", "name"] if suffix == "yaml" else ["[named.name]"]
    text = file.read_text()
    start = text.index("# one of these for each entry")
    file.write_text(_uncommented(text[:start], "name", "host") + _uncommented(text[start:], *header, "host", "tags"))
    assert load_cfg(file, App).named == {"name": Server(host = "")}


def test_ini_says_that_it_cant_hold_a_list_of_sections(tmp_path):
    file = tmp_path / "fleet.ini"
    write_template(Fleet, file)
    assert "# servers: ini can't hold a list of sections (use TOML or YAML)" in file.read_text()
    assert load_cfg(file, Fleet) == Fleet()


def test_a_default_that_ini_cant_hold_is_left_to_the_default(tmp_path):
    @cfg
    class Hosts:
        hosts: list[str] = field(default_factory = lambda: ["a,b", "c"])

    file = tmp_path / "hosts.ini"
    write_template(Hosts, file)
    assert "ini can't hold the default of this key" in file.read_text()
    assert load_cfg(file, Hosts) == Hosts()


def test_a_yaml_section_with_nothing_to_read_yet_is_not_null(tmp_path):
    pytest.importorskip("yaml")

    @cfg
    class Login:
        token: SecretStr = SecretStr("")

    @cfg
    class Both:
        given: Login = field(default_factory = Login)
        asked: Login

    file = tmp_path / "both.yaml"
    write_template(Both, file)
    text = file.read_text()
    assert "\n#given:\n" in text                                   # it has a default: left out is the same
    assert "# remove the {} when a key below is filled in\nasked: {}\n" in text
    assert load_cfg(file, Both) == Both(asked = Login())


def test_a_yaml_section_of_required_keys_is_commented_out_whole_and_put_back_with_them(tmp_path):
    pytest.importorskip("yaml")

    @cfg
    class Login:
        host: str

    @cfg
    class Holder:
        server: Login

    file = tmp_path / "holder.yaml"
    write_template(Holder, file)
    assert "# required\n#server:\n\n  # required: str\n  #host: \"\"\n" in file.read_text()
    with pytest.raises(ConfigError) as caught:
        load_cfg(file, Holder)
    assert [tuple(e["loc"][1:]) for e in caught.value.errors()] == [("server",)]
    file.write_text(_uncommented(file.read_text(), "server", "host"))
    assert load_cfg(file, Holder) == Holder(server = Login(host = ""))


def test_a_schema_that_holds_itself_ends():
    text = template_text(Node, "toml")
    assert text.count("#[[children]]") == 1
    assert "# list[Node]\n# as Node, above\n#children = []\n" in text


def test_a_default_no_file_can_hold_is_commented_out():
    @cfg
    class Odd:
        thing: Any = field(default_factory = object)

    assert "#thing = " in template_text(Odd, "toml")


def test_json_is_refused_and_nothing_is_written(tmp_path):
    file = tmp_path / "app.json"
    with pytest.raises(ValueError, match = "JSON has no comments"):
        write_template(App, file)
    assert not file.exists()


def test_a_file_that_is_there_is_kept_unless_overwrite(tmp_path):
    file = tmp_path / "app.toml"
    file.write_text("name = 'mine'\n")
    with pytest.raises(FileExistsError, match = "overwrite = True"):
        write_template(App, file)
    assert file.read_text() == "name = 'mine'\n"
    write_template(App, file, overwrite = True)
    assert file.read_text().startswith("# A template for App")


def test_the_format_can_be_named(tmp_path):
    file = tmp_path / "app.conf"
    with pytest.raises(ValueError):
        write_template(App, file)
    write_template(App, file, "ini")
    assert "[DEFAULT]" in file.read_text()


@pytest.mark.parametrize("bad", [App(name = "a", db = Database(host = "h")), dict, "App", None])
def test_only_a_cfg_class_has_a_template(tmp_path, bad):
    with pytest.raises(TypeError, match = "write_template takes a @cfg class"):
        write_template(bad, tmp_path / "app.toml")


def test_overwrite_is_a_bool(tmp_path):
    with pytest.raises(TypeError, match = "overwrite"):
        write_template(App, tmp_path / "app.toml", overwrite = 1)   # type: ignore[arg-type]
