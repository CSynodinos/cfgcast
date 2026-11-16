"""What a type checker must accept, and what it must know (`tests/test_static_typing.py` runs mypy on this)."""
from cfgcast import Loadable, cfg, field, from_dict, load_cfg, optional, parse_overrides, sources_of, write_template


@cfg
class Db(Loadable):
    host: str
    port: int = 5432


@cfg(strict = False, frozen = False, env_prefix = "APP_")
class App(Loadable):
    name: str
    db: Db


@cfg
class Plain:
    name: str


@cfg(keys = "kebab-case")
class Pool:
    max_size: int = field(default = 10, doc = "connections at most")
    tags: list[str] = field(default_factory = list, key = "Tags")


app = App.load("app.toml")
reveal_type(app)                                    # App
reveal_type(app.db.port)                            # int
reveal_type(App.load(["base.toml", optional("local.toml")], env_prefix = "APP_", env_file = ".env"))    # App
reveal_type(App.from_dict({"name": "n"}))           # App
reveal_type(Db.from_dict({"host": "h"}).host)       # str
reveal_type(load_cfg("app.toml", Plain))            # Plain
reveal_type(from_dict(Plain, {"name": "n"}))        # Plain
App(name = "n", db = Db(host = "h"))
reveal_type(Pool().max_size)                        # int: a cfgcast.field with a default is no required argument
reveal_type(Pool(tags = ["a"]).tags)                # list[str]
reveal_type(sources_of(app))                        # dict[str, str]
reveal_type(parse_overrides(["db.port=1"], App))    # dict[str, Any]
App.load("pyproject.toml", section = "tool.app", overrides = {"name": "n"})
load_cfg([], Plain, section = ["tool", "app"], overrides = parse_overrides(["name=n"], Plain))
write_template(App, "app.toml", overwrite = True)
