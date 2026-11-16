"""What a type checker must refuse: one error on each marked line (`tests/test_static_typing.py`)."""
from cfgcast import Loadable, cfg, field, from_dict, sources_of, write_template


@cfg
class Db(Loadable):
    host: str
    port: int = 5432


@cfg
class Plain:
    name: str


@cfg
class Pool:
    max_size: int = field(default = 10, key = "max-size")


Db.load("db.toml").nope                             # E: no such attribute
Db.load("db.toml", show_warnings = "no")            # E: not a bool
Db.from_dict({"host": "h"}).host = "x"              # E: frozen
Db("h")                                             # E: keyword-only
from_dict(Plain, {"name": "n"}).nope                # E: no such attribute
Plain.load("plain.toml")                            # E: members a decorator adds are not seen without the base
Pool(max_size = "many")                             # E: a cfgcast.field keeps the type of its field
Pool(10)                                            # E: keyword-only
sources_of(Pool()).nope                             # E: a dictionary
write_template(Pool, "pool.toml", True)             # E: overwrite is keyword-only
