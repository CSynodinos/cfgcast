"""
An application written against `cfgcast` as if it were built: it loads every file in `cfgs/`,
and checks what comes out. Run it to see how far the implementation has got:

    python tests/simulation.py

Each check is reported on its own (a failing one doesn't stop the others), a format whose reader
needs a package that isn't installed (PyYAML) is skipped, and the exit status is 1 if any failed.
"""
from collections.abc import Callable
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Literal
from cfgcast import Config, ConfigError, FrozenConfigError, field, sources_of, to_dict, write, write_template
import sys
sys.path.append(
    str(Path(__file__).parent.parent.parent)
    )
from enforced.data_types import Email, PhoneNumber, Port, Probability, SecretStr, Url
import json
import re
import tempfile
import traceback


# Static paths to the files
CFGS = Path(__file__).parent.parent / "cfgs"
FORMATS = ("json", "toml", "yaml", "ini", "cfg")
SIMPLE_PATHS = {fmt: CFGS / f"simple.{fmt}" for fmt in FORMATS}
COMPLEX_PATHS = {fmt: CFGS / f"complex.{fmt}" for fmt in FORMATS}
SIMPLE_CFG_PATH = SIMPLE_PATHS["json"]
COMPLEX_CFG_PATH = COMPLEX_PATHS["json"]


# Classes for each test format
class SimpleCfg(Config):
    name: str
    version: str
    debug: bool
    port: int
    timeout: float
    workers: int
    log_level: str
    hosts: list[str]


class Pool(Config):
    min_size: int
    max_size: int
    timeout: float


class Replica(Config):
    host: str
    port: Port
    read_only: bool


class Database(Config):
    host: str
    port: Port
    name: str
    user: str
    password: SecretStr
    pool: Pool
    replica: Replica


class Cache(Config):
    backend: str
    ttl: float
    max_items: int
    namespaces: list[str]


class Tls(Config):
    enabled: bool
    cert_path: str
    key_path: str


class Server(Config):
    host: str
    port: Port
    workers: int
    open_ports: list[Port]
    tls: Tls


class Rotation(Config):
    max_mb: int
    backups: int


class Logging(Config):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    format: str
    handlers: list[str]
    rotation: Rotation


class Features(Config):
    new_checkout: bool
    wishlist: bool
    rollout: Probability
    beta_users: list[str]
    weights: list[Probability]


class Monitoring(Config):
    metrics_url: Url
    sample_rate: Probability
    tags: list[str]


class ComplexCfg(Config):
    name: str
    environment: str
    debug: bool
    secret_key: SecretStr
    base_url: Url
    support_email: Email
    support_phone: PhoneNumber
    fallback_locale: str
    allowed_hosts: list[str]
    db: Database
    cache: Cache
    server: Server
    logging: Logging
    features: Features
    monitoring: Monitoring


class Lenient(Config, strict = False):
    name: str
    port: int


class WithDefault(Config, strict = False):
    name: str
    retries: int = 3


class Fleet(Config):
    name: str
    servers: list[Server]                                    # a list of sections, each with a section: TOML's [[servers]]


class Region(Config):
    host: str
    weight: Probability


class Schedule(Config):
    name: str
    starts: date
    built_at: datetime
    daily_at: time
    regions: dict[str, Region]                               # sections by name


class LayeredPool(Config):
    min_size: int
    max_size: int


class LayeredDb(Config):
    host: str
    port: Port
    user: str
    pool: LayeredPool


class Layered(Config):
    name: str
    debug: bool
    hosts: list[str]
    db: LayeredDb


class RateLimit(Config, keys = "kebab-case"):
    max_size: int
    per_user: bool
    retry_after: list[int]


class Keys(Config):
    name: str
    class_: str = field(key = "class")                       # a reserved word
    request_id: str = field(key = "X-Request-Id")
    rate_limit: RateLimit = field(key = "rate-limit")


LAYERS = [CFGS / "layers" / name for name in ("base.toml", "prod.yaml", "local.ini")]


# ── the checks ──────────────────────────────────────────────────────────────

RESULTS: list[tuple[str, str, str]] = []                     # (status, name, detail)


def check(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    """Run the function now and record how it went: 'ok', 'FAIL' (with the error) or 'skip'."""
    def run(fn: Callable[[], None]) -> Callable[[], None]:
        try:
            fn()
        except ModuleNotFoundError as error:                  # PyYAML is an optional extra
            RESULTS.append(("skip", name, str(error).splitlines()[0]))
        except Exception:
            RESULTS.append(("FAIL", name, traceback.format_exc(limit = -3).strip()))
        else:
            RESULTS.append(("ok", name, ""))
        return fn
    return run


def problem_locs(error: ConfigError) -> list[list[str | int]]:
    return [list(problem["loc"]) for problem in error.errors()]


def raised(fn: Callable[[], object]) -> ConfigError:
    try:
        fn()
    except ConfigError as error:
        return error
    raise AssertionError("no ConfigError was raised")


def baseline[T](path: Path, schema: type[T]) -> T:
    """The object the other checks compare with. If it can't even be loaded, say so and carry on:
    every check that needs it then fails on its own."""
    try:
        return schema.load(path)
    except Exception:
        RESULTS.append(("FAIL", f"baseline: load {path.name}", traceback.format_exc(limit = -3).strip()))
        return None                                                      # type: ignore[return-value]


def main() -> int:
    simple_cfg = baseline(SIMPLE_CFG_PATH, SimpleCfg)
    complex_cfg = baseline(COMPLEX_CFG_PATH, ComplexCfg)

    # 1. every file loads, and every format gives the same object -------------------------------
    for fmt in FORMATS:
        @check(f"load simple.{fmt}")
        def _simple(fmt = fmt) -> None:
            loaded = SimpleCfg.load(SIMPLE_PATHS[fmt])
            assert type(loaded) is SimpleCfg
            assert loaded == simple_cfg, f"differs from simple.json: {loaded}"

        @check(f"load complex.{fmt}")
        def complex_worker(fmt = fmt) -> None:
            loaded = ComplexCfg.load(COMPLEX_PATHS[fmt])
            assert type(loaded) is ComplexCfg
            assert loaded == complex_cfg, f"differs from complex.json: {loaded}"

    # 2. the values are of the types the schema says --------------------------------------------
    @check("simple: values and types")
    def _() -> None:
        assert simple_cfg.name == "storefront" and simple_cfg.version == "1.0"       # "1.0" stays a string
        assert simple_cfg.debug is True and simple_cfg.port == 8080 and simple_cfg.workers == 4
        assert type(simple_cfg.timeout) is float and simple_cfg.timeout == 2.5
        assert simple_cfg.hosts == ["localhost", "shop.example.com"]

    @check("complex: nested sections are configuration classes")
    def _() -> None:
        assert type(complex_cfg.db) is Database and type(complex_cfg.db.pool) is Pool
        assert type(complex_cfg.server.tls) is Tls and type(complex_cfg.logging.rotation) is Rotation
        assert complex_cfg.db.pool.max_size == 20 and complex_cfg.db.replica.read_only is True

    @check("complex: validated types")
    def _() -> None:
        assert type(complex_cfg.db.port) is Port and complex_cfg.db.port == 5432
        assert type(complex_cfg.base_url) is Url and complex_cfg.base_url.host == "shop.example.com"
        assert type(complex_cfg.support_email) is Email and type(complex_cfg.support_phone) is PhoneNumber
        assert complex_cfg.server.open_ports == [80, 443, 8080]
        assert complex_cfg.features.weights == [0.5, 0.3, 0.2]

    @check("complex: a SecretStr is masked, and keeps its value")
    def _() -> None:
        assert "k3y-9f2c71ab" not in repr(complex_cfg) and "pa55w0rd" not in repr(complex_cfg)
        assert complex_cfg.secret_key.get_secret_value() == "k3y-9f2c71ab-do-not-print"
        assert complex_cfg.db.password.get_secret_value() == "pa55w0rd-also-secret"

    @check("complex: the YAML 1.1 booleans trap (fallback_locale: no)")
    def _() -> None:
        loaded = ComplexCfg.load(COMPLEX_PATHS["yaml"])
        assert loaded.fallback_locale == "no", repr(loaded.fallback_locale)
        assert loaded.support_phone == "+4915112345678"

    @check("ini: lists, dotted sections and [DEFAULT]")
    def _() -> None:
        loaded = ComplexCfg.load(COMPLEX_PATHS["ini"])
        assert loaded.allowed_hosts == ["shop.example.com", "admin.example.com", "localhost"]
        assert loaded.db.pool.timeout == 30.5 and loaded.logging.format == "{asctime} {levelname} {message}"
        assert loaded.name == "storefront"                     # a [DEFAULT] key is the top-level one

    @check("a loaded configuration is frozen: a FrozenConfigError names the key, not the value")
    def _() -> None:
        assert isinstance(simple_cfg, SimpleCfg)
        try:
            complex_cfg.db.password = "leaked-text"            # type: ignore[assignment, misc]
        except FrozenConfigError as error:
            assert str(error).startswith("Database is frozen: db.password can't be assigned"), str(error)
            assert "leaked-text" not in str(error)
            return
        raise AssertionError("assignment was allowed")

    # 3. choosing the format ---------------------------------------------------------------------
    @check("cfg_type given explicitly, for every format")
    def _() -> None:
        for fmt, wanted in (("json", "json"), ("toml", "toml"), ("yaml", "yaml"), ("ini", "ini"), ("cfg", "ini")):
            loaded = SimpleCfg.load(SIMPLE_PATHS[fmt], wanted)
            assert type(loaded) is SimpleCfg and loaded == simple_cfg, fmt

    @check("an unknown suffix needs cfg_type, and then loads")
    def _() -> None:
        with tempfile.TemporaryDirectory() as folder:
            odd = Path(folder) / "settings.conf"
            odd.write_text(SIMPLE_PATHS["toml"].read_text())
            try:
                SimpleCfg.load(odd)
            except ValueError as error:
                assert "cfg_type" in str(error), error
            else:
                raise AssertionError("an unknown suffix was accepted")
            assert SimpleCfg.load(odd, "toml") == simple_cfg

    @check("a wrong cfg_type is refused at the call")
    def _() -> None:
        try:
            SimpleCfg.load(SIMPLE_CFG_PATH, "xml")        # type: ignore[call-overload]
        except Exception:
            return
        raise AssertionError("xml was accepted")

    @check("load gives the class it is called on")
    def _() -> None:
        simple, complex_ = SimpleCfg.load(SIMPLE_PATHS["toml"]), ComplexCfg.load(COMPLEX_PATHS["json"], cfg_type = "json")
        assert type(simple) is SimpleCfg and simple == simple_cfg
        assert type(complex_) is ComplexCfg and complex_ == complex_cfg

    # 4. what is wrong with a file ---------------------------------------------------------------
    def broken(change: Callable[[dict], None]) -> ConfigError:
        data = json.loads(COMPLEX_PATHS["json"].read_text())
        change(data)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broken.json"
            path.write_text(json.dumps(data))
            return raised(lambda: ComplexCfg.load(path))

    @check("strict (the default): an unknown key is refused, with its path")
    def _() -> None:
        error = broken(lambda d: d["db"].update(prot = 5432))
        assert any(loc[-2:] == ["db", "prot"] for loc in problem_locs(error)), error.errors()

    @check("a missing key is reported with its path")
    def _() -> None:
        error = broken(lambda d: d["server"]["tls"].pop("cert_path"))
        assert any(loc[-3:] == ["server", "tls", "cert_path"] for loc in problem_locs(error)), error.errors()

    @check("a wrong value is reported with its path")
    def _() -> None:
        error = broken(lambda d: d["db"].update(port = "not a port"))
        assert any(loc[-2:] == ["db", "port"] for loc in problem_locs(error)), error.errors()

    @check("every problem is reported at once")
    def _() -> None:
        def several(d: dict) -> None:
            d["db"]["port"] = 0
            d["cache"].pop("backend")
            d["surprise"] = 1
        assert len(broken(several).errors()) == 3

    @check("the problems have enforced's shape, and a file in their loc")
    def _() -> None:
        error = broken(lambda d: d["db"].update(port = 0))
        problem = error.errors()[0]
        assert {"type", "loc", "msg"} <= set(problem), problem
        assert str(problem["loc"][0]).endswith("broken.json"), problem["loc"]
        assert json.loads(error.json())[0]["loc"] == list(problem["loc"])
        assert "input" not in error.errors(include_input = False)[0]

    @check("a SecretStr is never shown in an error")
    def _() -> None:
        error = broken(lambda d: d.update(secret_key = 123))
        shown = str(error) + error.json()
        assert any(loc[-1] == "secret_key" for loc in problem_locs(error))
        for secret in ("k3y-9f2c71ab", "pa55w0rd"):
            assert secret not in shown, secret

    @check("a bad password value is not echoed")
    def _() -> None:
        error = broken(lambda d: d["db"].update(password = ["hunter2", "hunter3"]))
        assert "hunter2" not in str(error) + error.json(), "the input of a SecretStr field was shown"

    @check("strict = False ignores keys it doesn't know")
    def _() -> None:
        assert Lenient.load(SIMPLE_CFG_PATH) == Lenient(name = "storefront", port = 8080)

    @check("a class below a configuration is one too: the fields above, and its own")
    def _() -> None:
        class Below(SimpleCfg):
            extra: int = 1

        below = Below.load(SIMPLE_CFG_PATH)
        assert type(below) is Below and below.extra == 1 and below.port == simple_cfg.port
        assert isinstance(below, SimpleCfg) and to_dict(below) == {**to_dict(simple_cfg), "extra": 1}

    @check("a configuration is a plain enforced dataclass below Config")
    def _() -> None:
        import dataclasses
        assert dataclasses.is_dataclass(SimpleCfg) and SimpleCfg.__bases__ == (Config,)
        try:
            SimpleCfg(name = "x", version = "1", debug = True, port = "not a port", timeout = 1.0, workers = 1,
                      log_level = "INFO", hosts = [])
        except TypeError:
            return                                              # enforced's InvalidDataError
        raise AssertionError("construction was not checked")

    @check("a code default is used when the file has no key; the file wins when it has")
    def _() -> None:
        assert WithDefault.load(SIMPLE_CFG_PATH).retries == 3
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "retries.toml"
            path.write_text('name = "x"\nretries = 9\n')
            assert WithDefault.load(path).retries == 9

    @check("a missing file is the usual FileNotFoundError")
    def _() -> None:
        try:
            SimpleCfg.load(CFGS / "nope.json")
        except FileNotFoundError:
            return
        raise AssertionError("no FileNotFoundError")

    # 5. dictionaries ----------------------------------------------------------------------------
    @check("to_dict and from_dict round-trip")
    def _() -> None:
        as_dict = to_dict(complex_cfg)
        assert as_dict["db"]["pool"]["max_size"] == 20 and isinstance(as_dict["db"], dict)
        assert ComplexCfg.from_dict(as_dict) == complex_cfg

    # 6. writing ---------------------------------------------------------------------------------
    for fmt in FORMATS:
        @check(f"write complex.{fmt} and read it back")
        def _(fmt = fmt) -> None:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / f"out.{fmt}"
                write(complex_cfg, path)
                assert path.exists()
                assert ComplexCfg.load(path) == complex_cfg

    @check("write to a format other than the one it was loaded from")
    def _() -> None:
        with tempfile.TemporaryDirectory() as folder:
            as_ini = Path(folder) / "converted.ini"
            write(ComplexCfg.load(COMPLEX_PATHS["yaml"]), as_ini)
            assert ComplexCfg.load(as_ini) == complex_cfg

    @check("write with cfg_type for a suffix it doesn't know")
    def _() -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out.txt"
            write(simple_cfg, path, cfg_type = "json")
            assert json.loads(path.read_text())["port"] == 8080
            assert SimpleCfg.load(path, "json") == simple_cfg

    @check("a SecretStr is written as its real text")
    def _() -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out.json"
            write(complex_cfg, path)
            written = path.read_text()
            assert "k3y-9f2c71ab-do-not-print" in written and "**" not in written

    @check("overrides of a load are written as loaded, and the file loaded from is as it was")
    def _() -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "variant.toml"
            variant = ComplexCfg.load(COMPLEX_PATHS["json"], overrides = {"server": {"port": "9090"}, "debug": True})
            write(variant, path)
            again = ComplexCfg.load(path)
            assert again == variant and again.server.port == 9090 and again.debug is True
            assert again.server.workers == complex_cfg.server.workers            # the rest is untouched
        assert ComplexCfg.load(COMPLEX_PATHS["json"]) == complex_cfg

    @check("overrides are checked: a wrong value or an unknown key is refused")
    def _() -> None:
        for overrides in ({"server": {"port": "abc"}}, {"nope": 1}, {"db": {"pool": {"max_size": "many"}}}):
            error = raised(lambda overrides = overrides: ComplexCfg.load(COMPLEX_PATHS["json"], overrides = overrides))
            assert all(loc[0] == "overrides" for loc in problem_locs(error)), overrides

    @check("one configuration, several variants for a workflow")
    def _() -> None:
        import dataclasses
        with tempfile.TemporaryDirectory() as folder:
            for run, workers in enumerate((1, 2, 8)):
                path = Path(folder) / f"run_{run}.yaml"
                write(dataclasses.replace(complex_cfg, server = dataclasses.replace(complex_cfg.server, workers = workers)), path)
                assert ComplexCfg.load(path).server.workers == workers
            assert complex_cfg.server.workers == 4

    # 7. lists and dictionaries of sections ------------------------------------------------------
    fleet = baseline(CFGS / "fleet.json", Fleet)
    schedule = baseline(CFGS / "schedule.json", Schedule)

    for fmt in ("json", "toml", "yaml"):
        @check(f"load fleet.{fmt}: a list of sections")
        def _(fmt = fmt) -> None:
            loaded = Fleet.load(CFGS / f"fleet.{fmt}")
            assert loaded == fleet and [type(server) for server in loaded.servers] == [Server] * 3
            assert loaded.servers[2].port == 5432 and type(loaded.servers[2].port) is Port
            assert [type(server.tls) for server in loaded.servers] == [Tls] * 3
            assert loaded.servers[0].open_ports == [80, 443] and loaded.servers[1].open_ports == []
            assert loaded.servers[1].tls.enabled is False and loaded.servers[2].tls.key_path == "/etc/ssl/db-1.key"

        @check(f"write a list of sections as {fmt} and read it back")
        def _(fmt = fmt) -> None:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / f"fleet.{fmt}"
                write(fleet, path)
                assert Fleet.load(path) == fleet

    @check("a problem inside a list of sections has the item's index in its path")
    def _() -> None:
        data = json.loads((CFGS / "fleet.json").read_text())
        data["servers"][0]["tls"].pop("cert_path")
        data["servers"][1]["port"] = 0
        data["servers"][2].pop("host")
        error = raised(lambda: Fleet.from_dict(data))
        assert sorted(loc[1:] for loc in problem_locs(error)) == [
            ["servers", 0, "tls", "cert_path"], ["servers", 1, "port"], ["servers", 2, "host"]], error.errors()
        assert "servers[1].port" in str(error) and "servers[0].tls.cert_path" in str(error)

    @check("ini can't hold a list of sections: writing and loading one say so")
    def _() -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fleet.ini"
            for attempt in (lambda: write(fleet, path), lambda: Fleet.load(path)):
                path.write_text("[DEFAULT]\nname = fleet\nservers = web-1, web-2\n")
                try:
                    attempt()
                except ValueError as error:
                    assert "servers" in str(error) and "list of sections" in str(error), error
                else:
                    raise AssertionError("ini took a list of sections")

    # 8. dates and times, and a dictionary of sections, in every format ---------------------------
    for fmt in FORMATS:
        @check(f"load schedule.{fmt}: dates, times and sections by name")
        def _(fmt = fmt) -> None:
            loaded = Schedule.load(CFGS / f"schedule.{fmt}")
            assert loaded == schedule, f"differs from schedule.json: {loaded}"
            assert loaded.starts == date(2026, 10, 17) and type(loaded.starts) is date
            assert loaded.built_at == datetime(2026, 10, 17, 12, 30, tzinfo = timezone.utc)
            assert loaded.daily_at == time(2, 15)
            assert type(loaded.regions["us"]) is Region and loaded.regions["eu"].weight == 0.7

        @check(f"write dates and sections by name as {fmt} and read them back")
        def _(fmt = fmt) -> None:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / f"schedule.{fmt}"
                write(schedule, path)
                assert Schedule.load(path) == schedule

    @check("TOML and YAML get dates as their own literals, not as text")
    def _() -> None:
        with tempfile.TemporaryDirectory() as folder:
            write(schedule, Path(folder) / "s.toml")
            write(schedule, Path(folder) / "s.yaml")
            assert "starts = 2026-10-17\n" in (Path(folder) / "s.toml").read_text()
            assert "daily_at = 02:15:00\n" in (Path(folder) / "s.toml").read_text()
            assert "starts: 2026-10-17\n" in (Path(folder) / "s.yaml").read_text()
            assert "daily_at: '02:15:00'\n" in (Path(folder) / "s.yaml").read_text()   # bare, YAML 1.1 reads a number

    # 9. several files in layers -----------------------------------------------------------------
    @check("layers: sections merge key by key, a later file wins, a list is replaced")
    def _() -> None:
        loaded = Layered.load(LAYERS)
        assert loaded.name == "storefront" and loaded.debug is False
        assert loaded.hosts == ["shop.example.com", "admin.example.com"]
        assert loaded.db.host == "db.internal" and loaded.db.port == 5432 and loaded.db.user == "ana"
        assert loaded.db.pool == LayeredPool(min_size = 1, max_size = 50)
        assert Layered.load(LAYERS) == loaded and Layered.load(LAYERS[:1]).db.user == "dev"

    @check("layers: a problem names the file its value came from")
    def _() -> None:
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "bad.json"
            bad.write_text(json.dumps({"db": {"port": 0, "prot": 1}, "debug": None}))
            base_only = Path(folder) / "less.toml"
            base_only.write_text('name = "x"\nhosts = [1]\n')
            error = raised(lambda: Layered.load([base_only, bad]))
            found = {tuple(loc[1:]): Path(str(loc[0])).name for loc in problem_locs(error)}
            assert found == {("db", "prot"): "bad.json", ("db", "port"): "bad.json", ("debug",): "bad.json",
                             ("hosts", 0): "less.toml", ("db", "host"): "bad.json", ("db", "user"): "bad.json",
                             ("db", "pool"): "bad.json"}, found                   # missing keys name the last file
            assert error.files == (str(base_only), str(bad)) and f"{bad}:1: db.port:" in str(error)

    # 10. environment variables -------------------------------------------------------------------
    @check("environment variables go over every file, and others are ignored")
    def _() -> None:
        env = {"SHOP_DB__PORT": "9090", "SHOP_DEBUG": "yes", "shop_db__pool__max_size": "7", "SHOP_HOSTS": "a, b",
               "SHOP_UNKNOWN": "1", "SHOP_DB__NOPE": "1", "PATH": "/bin", "OTHER_NAME": "x"}
        loaded = Layered.load(LAYERS, env_prefix = "SHOP_", env = env)
        assert loaded.db.port == 9090 and loaded.debug is True and loaded.db.pool.max_size == 7
        assert loaded.hosts == ["a", "b"] and loaded.name == "storefront" and loaded.db.user == "ana"
        assert Layered.load(LAYERS, env_prefix = "SHOP_", env = {"SHOP_HOSTS": '["x,y", "z"]'}).hosts == ["x,y", "z"]
        assert Layered.load(LAYERS, env = {}, env_prefix = "SHOP_") == Layered.load(LAYERS)

    @check("a problem of an environment variable names the variable")
    def _() -> None:
        error = raised(lambda: Layered.load(LAYERS, env_prefix = "SHOP_", env = {"SHOP_DB__PORT": "0"}))
        assert problem_locs(error) == [["SHOP_DB__PORT", "db", "port"]], error.errors()
        assert "SHOP_DB__PORT: db.port: invalid 'Port'" in str(error), str(error)

    # 11. keys that are no Python names -------------------------------------------------------------
    keys = baseline(CFGS / "keys.json", Keys)

    for fmt in FORMATS:
        @check(f"load keys.{fmt}: keys that are no Python names")
        def _(fmt = fmt) -> None:
            loaded = Keys.load(CFGS / f"keys.{fmt}")
            assert loaded == keys, f"differs from keys.json: {loaded}"
            assert loaded.class_ == "gold" and loaded.request_id == "trace"
            assert loaded.rate_limit == RateLimit(max_size = 10, per_user = True, retry_after = [1, 5, 30])

        @check(f"write keys that are no Python names as {fmt} and read them back")
        def _(fmt = fmt) -> None:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / f"keys.{fmt}"
                write(Keys.from_dict({**to_dict(keys), "rate-limit": {**to_dict(keys)["rate-limit"], "max-size": 20}}), path)
                assert "max-size" in path.read_text() and "max_size" not in path.read_text()
                assert Keys.load(path).rate_limit.max_size == 20

    @check("a problem and a variable use the file's key, and the field's name in a file is unknown")
    def _() -> None:
        data = to_dict(keys)
        assert list(data) == ["name", "class", "X-Request-Id", "rate-limit"], data
        data["rate-limit"]["max-size"] = "many"
        data["class_"] = data.pop("class")
        error = raised(lambda: Keys.from_dict(data))
        assert sorted(loc[1:] for loc in problem_locs(error)) == [["class"], ["class_"], ["rate-limit", "max-size"]]
        assert "the key of this field is 'class'" in str(error), str(error)
        loaded = Keys.load(CFGS / "keys.toml", env_prefix = "EDGE_",
                          env = {"EDGE_RATE_LIMIT__MAX_SIZE": "3", "EDGE_CLASS": "tin", "EDGE_X_REQUEST_ID": "r"})
        assert (loaded.rate_limit.max_size, loaded.class_, loaded.request_id) == (3, "tin", "r")

    # 12. where a value came from: the file and the line ------------------------------------------
    lines = {"json": 6, "toml": 7, "yaml": 6, "ini": 8, "cfg": 8}       # of max-size, in cfgs/keys.*

    for fmt in FORMATS:
        @check(f"keys.{fmt}: the line of a value, in sources_of and in a problem")
        def _(fmt = fmt) -> None:
            path = CFGS / f"keys.{fmt}"
            sources = sources_of(Keys.load(path))
            assert sources["rate-limit.max-size"] == f"{path}:{lines[fmt]}", sources
            assert sources_of(Keys.load(path, overrides = {"name": "cli"}))["name"] == "overrides"
            with tempfile.TemporaryDirectory() as folder:
                wrong = Path(folder) / f"keys.{fmt}"
                wrong.write_text(path.read_text().replace("10", '"many"', 1))
                error = raised(lambda: Keys.load(wrong))
                assert f"{wrong}:{lines[fmt]}: rate-limit.max-size" in str(error), str(error)

    # 13. a template of a schema ------------------------------------------------------------------
    for fmt in ("toml", "yaml", "ini"):
        @check(f"a template as {fmt}: the required keys are missing, and filled in it loads")
        def _(fmt = fmt) -> None:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / f"keys.{fmt}"
                write_template(Keys, path)
                error = raised(lambda: Keys.load(path))
                assert {problem["type"] for problem in error.errors()} == {"missing"}, str(error)
                #* Every format alike: a YAML section with nothing under it is null, read as an empty section, so its keys are missing.
                assert len(error.errors()) == 6, str(error)
                #* Every key that is commented out is put back: it is '#key', and a note is '# note'.
                text = re.sub(r"(?m)^(\s*)#(?! )", r"\1", path.read_text())
                path.write_text(text)
                loaded = Keys.load(path)
                assert loaded == Keys(name = "", class_ = "", request_id = "",
                                      rate_limit = RateLimit(max_size = 0, per_user = False, retry_after = [])), loaded

    # ── the report ──────────────────────────────────────────────────────────────────────────
    marks = {"ok": "✓", "FAIL": "✗", "skip": "-"}
    for status, name, detail in RESULTS:
        print(f"  {marks[status]} {name}")
        if status != "ok":
            print("      " + detail.replace("\n", "\n      "))
    failed = sum(status == "FAIL" for status, *_ in RESULTS)
    skipped = sum(status == "skip" for status, *_ in RESULTS)
    passed = len(RESULTS) - failed - skipped
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped, of {len(RESULTS)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
