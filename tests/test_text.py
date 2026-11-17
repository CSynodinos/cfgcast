"""A configuration as text and as plain data: `to_text`, `to_dict(plain = True)`, and what JSON can't hold."""
from dataclasses import field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID
import json
import tomllib

import pytest

from enforced.data_types import Port, SecretStr
from cfgcast import Config, ConfigError, to_dict, to_text, write
from cfgcast._writers import write_json, write_toml


class Level(Enum):
    LOW = "low"
    HIGH = "high"


class Db(Config):
    host: str
    port: Port = 5432
    password: SecretStr = SecretStr("hunter2")
    keys: list[SecretStr] = field(default_factory = lambda: [SecretStr("k1"), SecretStr("k2")])


class App(Config):
    name: str
    db: Db
    home: Path = Path("/srv/app")
    started: datetime = datetime(2026, 10, 17, 12, 0, tzinfo = timezone.utc)
    day: date = date(2026, 10, 17)
    at: time = time(8, 30)
    price: Decimal = Decimal("9.99")
    id: UUID = UUID("12345678-1234-5678-1234-567812345678")
    level: Level = Level.HIGH
    by_day: dict[date, int] = field(default_factory = lambda: {date(2026, 1, 1): 1})
    nothing: str | None = None


APP = App(name = "shop", db = Db(host = "h"))
FORMATS = ["json", "toml", "ini", "yaml"]


def test_plain_is_what_json_holds_with_the_secrets_masked():
    plain = to_dict(APP, plain = True)
    assert plain == {
        "name": "shop",
        "db": {"host": "h", "port": 5432, "password": "**********", "keys": ["**********", "**********"]},
        "home": str(Path("/srv/app")), "started": "2026-10-17T12:00:00+00:00", "day": "2026-10-17", "at": "08:30:00",
        "price": "9.99", "id": "12345678-1234-5678-1234-567812345678", "level": "high",
        "by_day": {"2026-01-01": 1}, "nothing": None,
        }
    assert type(plain["db"]["port"]) is int and type(plain["db"]["password"]) is str
    text = json.dumps(plain)                                        # what the plain form is for
    assert "hunter2" not in text and "k1" not in text
    assert App.from_dict(json.loads(json.dumps(to_dict(APP, plain = True, reveal_secrets = True)))) == APP


def test_without_plain_the_values_stay_as_they_are():
    assert to_dict(APP)["home"] == Path("/srv/app") and isinstance(to_dict(APP)["db"]["password"], SecretStr)
    with pytest.raises(TypeError, match = "reveal_secrets goes with plain"):
        to_dict(APP, reveal_secrets = True)
    with pytest.raises(TypeError, match = "plain must be True or False"):
        to_dict(APP, plain = 1)                                       # type: ignore[arg-type]


@pytest.mark.parametrize("cfg_type", FORMATS)
def test_text_is_what_write_writes_but_for_the_secrets(tmp_path, cfg_type):
    path = tmp_path / f"app.{cfg_type}"
    write(APP, path)
    written = path.read_text(encoding = "utf-8")
    assert to_text(APP, cfg_type, reveal_secrets = True) == written and "hunter2" in written
    masked = to_text(APP, cfg_type)
    mask = "'**********'" if cfg_type == "yaml" else "**********"       # YAML quotes it: a bare * starts an alias
    assert "hunter2" not in masked and "k1" not in masked
    assert masked == written.replace("hunter2", mask).replace("k1", mask).replace("k2", mask)


def test_json_is_the_default_and_the_text_loads(tmp_path):
    assert json.loads(to_text(APP))["db"]["password"] == "**********"
    assert tomllib.loads(to_text(APP, "toml", reveal_secrets = True))["db"]["password"] == "hunter2"
    (tmp_path / "app.json").write_text(to_text(APP, reveal_secrets = True))
    assert App.load(tmp_path / "app.json") == APP


def test_what_to_text_refuses():
    with pytest.raises(TypeError, match = "to_text takes a configuration"):
        to_text(App)
    with pytest.raises(ValueError, match = "Unsupported configuration type"):
        to_text(APP, "xml")                                           # type: ignore[arg-type]
    with pytest.raises(ValueError, match = "Unsupported configuration type"):
        to_text(APP, "infer")                                         # type: ignore[arg-type]
    with pytest.raises(TypeError, match = "reveal_secrets must be True or False"):
        to_text(APP, reveal_secrets = "yes")                          # type: ignore[arg-type]


def test_json_refuses_nan_and_the_infinities_and_names_the_key(tmp_path):
    for value, where in (({"a": {"b": [1.0, float("nan")]}}, r"a\.b\[1\]"), ({"x": float("-inf")}, "x")):
        with pytest.raises(ValueError, match = f"^{where}: JSON has no NaN and no infinity"):
            write_json(tmp_path / "x.json", value)
    assert list(tmp_path.iterdir()) == []
    write_toml(tmp_path / "x.toml", {"x": float("inf")})            # TOML has them


def test_what_cant_be_written_is_said_by_its_type_never_its_value(tmp_path):
    class Holder:
        def __repr__(self) -> str:
            return "Holder(hunter2)"

    with pytest.raises(TypeError) as info:
        write_json(tmp_path / "x.json", {"a": Holder()})
    assert str(info.value) == "a Holder can't be written to a configuration file"
