"""What a type checker must refuse: one error on each marked line (`tests/test_static_typing.py`)."""
from cfgcast import Config, field, sources_of, write_template


class Db(Config):
    host: str
    port: int = 5432


class Plain(Config):
    name: str


class Pool(Config):
    max_size: int = field(default = 10, key = "max-size")


class Prod(Plain):
    replicas: int = 3


Db.load("db.toml").nope                             # E: no such attribute
Db.load("db.toml", show_warnings = False)           # E: no such argument
Db.from_dict({"host": "h"}).host = "x"              # E: frozen
Db("h")                                             # E: keyword-only
Plain.from_dict({"name": "n"}).nope                # E: no such attribute
Prod(name = "n", replicas = "3")                    # E: a field of its own keeps its type
Prod("n")                                           # E: keyword-only, the fields above too
Pool(max_size = "many")                             # E: a cfgcast.field keeps the type of its field
Pool(10)                                            # E: keyword-only
sources_of(Pool()).nope                             # E: a dictionary
write_template(Pool, "pool.toml", True)             # E: overwrite is keyword-only


class Wrong(Config, strict = 1):                    # E: an option keeps its type
    pass
