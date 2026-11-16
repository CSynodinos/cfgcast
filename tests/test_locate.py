"""The line of each key (`_locate`), lines in the problems of a file, and `sources_of`."""
import json

import pytest

from enforced.data_types import Port, SecretStr
from cfgcast import ConfigError, ConfigWarning, cfg, field, from_dict, load_cfg, sources_of
from cfgcast._locate import locate
import tomllib


def _located(cfg_type: str, text: str) -> dict:
    if cfg_type == "json":
        data = json.loads(text)
    elif cfg_type == "toml":
        data = tomllib.loads(text)
    elif cfg_type == "yaml":
        from cfgcast._readers import parse_yaml
        data = parse_yaml(text, "f.yaml")
    else:
        from cfgcast._readers import parse_ini
        data = parse_ini(text, "f.ini")
    return locate(cfg_type, text, data)


# ── JSON ────────────────────────────────────────────────────────────────────

def test_json_keys_items_and_nesting():
    text = '''{
  "name": "x",
  "db": {
    "host": "h", "port": 1,
    "tags": [
      "a",
      {"k": [1,
             2]}
    ]
  },
  "e": {}, "l": [],
  "tricky \\" } ] , :": "{\\"not\\": [a key]}\\n",
  "n": -1.5e3, "t": true, "z": null
}'''
    assert _located("json", text) == {
        ("name",): 2, ("db",): 3, ("db", "host"): 4, ("db", "port"): 4, ("db", "tags"): 5, ("db", "tags", 0): 6,
        ("db", "tags", 1): 7, ("db", "tags", 1, "k"): 7, ("db", "tags", 1, "k", 0): 7, ("db", "tags", 1, "k", 1): 8,
        ("e",): 11, ("l",): 11, ('tricky " } ] , :',): 12, ("n",): 13, ("t",): 13, ("z",): 13}


def test_json_a_key_written_twice_is_where_the_later_one_is():
    text = '{"a": {"x": 1, "gone": 2},\n "a": {"x": 3},\n "b": [1, 2, 3],\n "b": [1]}'
    assert _located("json", text) == {("a",): 2, ("a", "x"): 2, ("b",): 4, ("b", 0): 4}


def test_one_line_and_crlf_json():
    assert _located("json", '{"a": {"b": 1}}') == {("a",): 1, ("a", "b"): 1}
    assert _located("json", '{\r\n"a": 1,\r\n"b": 2}') == {("a",): 2, ("b",): 3}


# ── TOML ────────────────────────────────────────────────────────────────────

def test_toml_tables_dotted_keys_and_what_runs_over_lines():
    text = '''# a comment with = and [brackets]
name = "x"   # trailing = [x]
a.b.c = 1
"quoted.key" = 2
'lit = eral' = 3
text = """
not = a key
[not.a.table]
"""
also = \'\'\'
fake = 1 \'\'\'
array = [
  1,  # ] not the end
  "]", [2,
  3],
]
inline = { x = 1, y = { z = 2 } }

[db]
host = "h"
  port = 5432

[db.pool]
size = 1

[ "odd table" . 'x' ]   # a comment
k = "a # no comment"
'''
    assert _located("toml", text) == {
        ("name",): 2, ("a",): 3, ("a", "b"): 3, ("a", "b", "c"): 3, ("quoted.key",): 4, ("lit = eral",): 5, ("text",): 6,
        ("also",): 10, ("array",): 12, ("inline",): 17, ("db",): 19, ("db", "host"): 20, ("db", "port"): 21,
        ("db", "pool"): 23, ("db", "pool", "size"): 24, ("odd table",): 26, ("odd table", "x"): 26,
        ("odd table", "x", "k"): 27}


def test_toml_arrays_of_tables_have_the_index_of_each_item():
    text = '''[[servers]]
host = "a"
[servers.tls]
on = true
[[servers.ports]]
n = 1
[[servers.ports]]
n = 2

[[servers]]
host = "b"
[servers.tls]
on = false
[[servers.ports]]
n = 3
'''
    assert _located("toml", text) == {
        ("servers",): 1, ("servers", 0): 1, ("servers", 0, "host"): 2, ("servers", 0, "tls"): 3, ("servers", 0, "tls", "on"): 4,
        ("servers", 0, "ports"): 5, ("servers", 0, "ports", 0): 5, ("servers", 0, "ports", 0, "n"): 6,
        ("servers", 0, "ports", 1): 7, ("servers", 0, "ports", 1, "n"): 8,
        ("servers", 1): 10, ("servers", 1, "host"): 11, ("servers", 1, "tls"): 12, ("servers", 1, "tls", "on"): 13,
        ("servers", 1, "ports"): 14, ("servers", 1, "ports", 0): 14, ("servers", 1, "ports", 0, "n"): 15}


def test_toml_quotes_at_the_end_of_a_long_string_and_escapes():
    text = 'a = """x""""\nb = "q\\"=\\\\"\nc = """e\\"""still"""\nd = 1\n'
    assert _located("toml", text) == {("a",): 1, ("b",): 2, ("c",): 3, ("d",): 4}
    assert _located("toml", 'a = 1\r\nb = 2\r\n') == {("a",): 1, ("b",): 2}


# ── ini ─────────────────────────────────────────────────────────────────────

def test_ini_sections_keys_and_values_over_lines():
    text = '''; comment = not a key
[DEFAULT]
name = x
hosts: a,
    b = not a key
    [not a section]

# another
[db.pool]
size=1
url = http://h:80/?a=b

[db]
host : h
'''
    assert _located("ini", text) == {
        ("name",): 3, ("hosts",): 4, ("db",): 9, ("db", "pool"): 9, ("db", "pool", "size"): 10, ("db", "pool", "url"): 11,
        ("db", "host"): 14}


# ── YAML ────────────────────────────────────────────────────────────────────

def test_yaml_keys_items_flow_and_block_text():
    pytest.importorskip("yaml")
    text = '''# comment
name: x
db:
  host: h
  ports: [1,
          2]
  text: |
    not: a key
    - not an item
servers:
  - host: a
    tls: {on: true}
  -
    host: b
"quoted: key": 1
1: number key
'''
    assert _located("yaml", text) == {
        ("name",): 2, ("db",): 3, ("db", "host"): 4, ("db", "ports"): 5, ("db", "ports", 0): 5, ("db", "ports", 1): 6,
        ("db", "text"): 7, ("servers",): 10, ("servers", 0): 11, ("servers", 0, "host"): 11, ("servers", 0, "tls"): 12,
        ("servers", 0, "tls", "on"): 12, ("servers", 1): 14, ("servers", 1, "host"): 14, ("quoted: key",): 15, (1,): 16}


def test_yaml_an_anchor_and_a_merge_point_at_where_the_keys_are_written():
    pytest.importorskip("yaml")
    text = "base: &b\n  host: h\n  port: 1\nprod:\n  <<: *b\n  port: 2\ncopy: *b\n"
    assert _located("yaml", text) == {
        ("base",): 1, ("base", "host"): 2, ("base", "port"): 3, ("prod",): 4, ("prod", "host"): 2, ("prod", "port"): 6,
        ("copy",): 7, ("copy", "host"): 2, ("copy", "port"): 3}


def test_an_empty_yaml_file_has_no_lines():
    pytest.importorskip("yaml")
    assert _located("yaml", "") == {} and _located("yaml", "# only a comment\n") == {}


# ── never a wrong line ──────────────────────────────────────────────────────

def test_a_locator_that_fails_gives_no_lines():
    assert locate("json", '{"a": 1', {"a": 1}) == {}                        # not the text that was parsed
    assert locate("toml", 'a = 1\n[a', {"a": 1}) == {}
    assert locate("nope", "a = 1", {"a": 1}) == {}
    assert locate("json", "[" * 5000 + "]" * 5000, []) == {}                # too deep: a RecursionError inside


def test_a_path_the_data_does_not_have_is_dropped():
    assert locate("json", '{"a": {"b": 1}, "c": [1, 2]}', {"a": {}, "c": [1]}) == {("a",): 1, ("c",): 1, ("c", 0): 1}
    assert locate("ini", "[s]\nk = 1\n", {"other": {}}) == {}


# ── lines in the problems of a file ─────────────────────────────────────────

@cfg
class Db:
    host: str
    port: Port = 5432
    password: SecretStr = SecretStr("p")


@cfg
class Server:
    host: str
    port: Port


@cfg
class App:
    name: str
    db: Db
    hosts: list[str] = field(default_factory = list)
    servers: list[Server] = field(default_factory = list)
    debug: bool = False


BAD = {
    "toml": 'name = 1\nhosts = ["a",\n  2]\nextra = 1\n\n[db]\nport = 0\npassword = 5\n\n[[servers]]\nhost = "a"\nport = 1\n\n[[servers]]\nhost = "b"\n',
    "yaml": "name: [1]\nhosts:\n  - a\n  - 2\nextra: 1\ndb:\n  port: 0\n  password: 5\nservers:\n  - host: a\n    port: 1\n  - host: b\n",
    "json": '{\n"name": 1,\n"hosts": ["a",\n 2],\n"extra": 1,\n"db": {\n  "port": 0,\n  "password": 5},\n"servers": [\n  {"host": "a", "port": 1},\n  {"host": "b"}\n]}',
    }
#* {key path: the line in each format}; a missing key has the line of the section it is missing from.
LINES = {
    ("name",): {"toml": 1, "yaml": 1, "json": 2},
    ("hosts", 1): {"toml": 2, "yaml": 4, "json": 4},                # TOML: an item has the line of its key
    ("extra",): {"toml": 4, "yaml": 5, "json": 5},
    ("db", "port"): {"toml": 7, "yaml": 7, "json": 7},
    ("db", "password"): {"toml": 8, "yaml": 8, "json": 8},
    ("db", "host"): {"toml": 6, "yaml": 6, "json": 6},
    ("servers", 1, "port"): {"toml": 14, "yaml": 12, "json": 11},
    }


@pytest.mark.parametrize("suffix", ["toml", "yaml", "json"])
def test_every_problem_of_a_file_has_its_line(tmp_path, suffix):
    if suffix == "yaml":
        pytest.importorskip("yaml")
    path = tmp_path / f"app.{suffix}"
    path.write_text(BAD[suffix])
    with pytest.raises(ConfigError) as caught:
        load_cfg(path, App)
    found = {tuple(e["loc"][1:]): e for e in caught.value.errors()}
    assert {where: e["ctx"]["line"] for where, e in found.items()} == {where: lines[suffix] for where, lines in LINES.items()}
    assert all(e["loc"][0] == str(path) for e in found.values())
    assert found[("db", "password")]["input"] == "**********"
    text = str(caught.value)
    assert f"    {path}:{LINES[('db', 'port')][suffix]}: db.port: invalid 'Port'" in text
    assert f"    {path}:{LINES[('servers', 1, 'port')][suffix]}: servers[1].port: missing key" in text
    assert caught.value.errors(include_context = False)[0].get("ctx") is None


def test_ini_problems_have_their_line_and_a_top_level_missing_key_has_none(tmp_path):
    path = tmp_path / "app.ini"
    path.write_text("[DEFAULT]\nhosts = a, b\nextra = 1\n\n[db]\nhost = h\nport = 0\n")
    with pytest.raises(ConfigError) as caught:
        load_cfg(path, App)
    found = {tuple(e["loc"][1:]): (e.get("ctx") or {}).get("line") for e in caught.value.errors()}
    assert found == {("extra",): 3, ("db", "port"): 7, ("name",): None}
    assert f"\n    name: missing key" in str(caught.value) and f"\n    {path}:3: extra: key not in the schema" in str(caught.value)


def test_layers_each_problem_has_the_line_in_its_own_file(tmp_path):
    base, prod = tmp_path / "base.toml", tmp_path / "prod.json"
    base.write_text('name = "n"\n\n[db]\nhost = 3\nport = 1\n')
    prod.write_text('{\n\n"db": {\n\n"port": 0}}')
    with pytest.raises(ConfigError) as caught:
        load_cfg([base, prod], App, env_prefix = "APP_", env = {"APP_DEBUG": "perhaps"})
    assert {e["loc"][0]: e.get("ctx", {}).get("line") for e in caught.value.errors()} == {
        "APP_DEBUG": None, str(base): 4, str(prod): 5}
    assert f"    {base}:4: db.host:" in str(caught.value) and "    APP_DEBUG: debug:" in str(caught.value)


def test_a_section_of_a_file_keeps_the_lines_of_the_file(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "p"\n\n[tool.app]\nname = "n"\n\n[tool.app.db]\nhost = "h"\nport = 0\n')
    with pytest.raises(ConfigError, match = r"pyproject\.toml:9: tool\.app\.db\.port") as caught:
        load_cfg(path, App, section = "tool.app")
    assert caught.value.errors()[0]["ctx"]["line"] == 9


def test_an_ignored_key_has_its_line_in_the_warning(tmp_path):
    @cfg(strict = False)
    class Loose:
        name: str

    path = tmp_path / "a.yaml"
    pytest.importorskip("yaml")
    path.write_text("name: n\n\nextra: 1\n")
    with pytest.warns(ConfigWarning) as caught:
        Loose.load(path)
    assert str(caught[0].message).endswith(f"    -> {path}:3: extra")
    assert caught[0].message.shown().count(f"{path}:3: ") == 1


def test_a_dictionary_has_no_lines():
    with pytest.raises(ConfigError) as caught:
        from_dict(App, {"name": 1, "db": {"host": "h"}})
    assert str(caught.value) == "1 problem in <dict> for App:\n    name: expected type: 'str', got type 'int'"
    assert "line" not in caught.value.errors()[0].get("ctx", {})


# ── sources_of ──────────────────────────────────────────────────────────────

def test_sources_of_a_layered_load(tmp_path):
    pytest.importorskip("yaml")
    base, prod, env_file = tmp_path / "base.toml", tmp_path / "prod.yaml", tmp_path / ".env"
    base.write_text('name = "n"\nhosts = ["a"]\n\n[db]\nhost = "h"\nport = 1\n\n[[servers]]\nhost = "s"\nport = 2\n')
    prod.write_text("db:\n  host: prod\nhosts:\n  - x\n  - y\n")
    env_file.write_text("# local\nAPP_DB__PASSWORD=s3cret\n")
    app = load_cfg([base, prod], App, env_prefix = "APP_", env = {"APP_DB__PORT": "9"}, env_file = env_file)
    assert sources_of(app) == {
        "name": f"{base}:1", "db.host": f"{prod}:2", "db.port": "APP_DB__PORT", "db.password": f"{env_file}:2",
        "hosts": f"{prod}:3", "servers[0].host": f"{base}:9", "servers[0].port": f"{base}:10", "debug": "(default)"}
    assert sources_of(app.db) == {"host": f"{prod}:2", "port": "APP_DB__PORT", "password": f"{env_file}:2"}
    assert sources_of(app.servers[0]) == {"host": f"{base}:9", "port": f"{base}:10"}
    assert "s3cret" not in str(sources_of(app))


def test_sources_of_a_default_section_a_dictionary_of_sections_and_a_section_of_a_file(tmp_path):
    @cfg
    class Pool:
        size: int = 1

    @cfg
    class Top:
        pool: Pool = field(default_factory = Pool)
        pools: dict[str, Pool] = field(default_factory = dict)
        max_size: int = field(default = 1, key = "max-size")

    path = tmp_path / "pyproject.toml"
    path.write_text("[tool.x]\nmax-size = 3\n[tool.x.pools.a]\nsize = 2\n[tool.x.pools.b]\n")
    top = load_cfg(path, Top, section = "tool.x")
    assert sources_of(top) == {"pool.size": "(default)", "pools.a.size": f"{path}:4", "pools.b.size": "(default)", "max-size": f"{path}:2"}
    assert sources_of(from_dict(Top, {"pool": {}, "pools": {}})) == {"pool.size": "(default)", "pools": "<dict>", "max-size": "(default)"}


def test_sources_of_what_was_not_loaded_is_refused():
    with pytest.raises(ValueError, match = "this Db was not loaded"):
        sources_of(Db(host = "h"))
    for bad in (Db, 3, {"a": 1}):
        with pytest.raises(TypeError, match = "sources_of takes an instance of a @cfg class"):
            sources_of(bad)


def test_what_is_kept_for_sources_of_changes_nothing_else(tmp_path):
    import pickle

    loaded = from_dict(Db, {"host": "h"})
    assert loaded == Db(host = "h") and repr(loaded) == repr(Db(host = "h")) and hash(loaded) == hash(Db(host = "h"))
    again = pickle.loads(pickle.dumps(loaded))
    assert again == loaded and sources_of(again) == sources_of(loaded) == {"host": "<dict>", "port": "(default)", "password": "(default)"}
