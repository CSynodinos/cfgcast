"""The writers: a dictionary in, a file out, read back by the readers as it was (`_writers`)."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePosixPath
from uuid import UUID

import pytest

from enforced.data_types import Port, SecretStr
from cfgcast._readers import read_ini, read_json, read_toml, read_yaml
from cfgcast._writers import WRITERS, _plain, write_ini, write_json, write_toml, write_yaml

#* Strings a YAML reader (1.1 or 1.2) would take for something else, or that need quoting anyway.
TRICKY = ["no", "on", "Yes", "08", "007", "1e3", "1_000", "1:20", "null", "~", "", " lead", "trail ", "a: b",
          "a #b", "#x", "-x", "x:", "2026-10-17", "'q'", '"dq"', "tab\there", "line\nbreak", "uni ✓ é",
          "back\\slash", "{a}", "[a]", "*star", "&amp", "!bang", "%pct", "@at", "`tick", "|pipe", ">gt",
          "?q", ".inf", "+1", "-1.5", "0x1F", "0o7", "true", "False", "<<", "=",
          "del\x7f", "nel\x85here", "sep\u2028here", "\ufeffbom", "emoji 🙂"]

DATA = {
    "name": "shop", "debug": False, "port": 8080, "ratio": 0.25, "big": 1e16, "small": 1e-5,
    "tags": ["a", "b"], "ports": [80, 443], "empty": [],
    "db": {"host": "h", "pool": {"size": 2, "timeout": 30.5}, "replica": {"read_only": True}},
    "nothing": {},
    }


@pytest.fixture(params = ["json", "toml", "yaml"])
def nested_format(request):
    if request.param == "yaml":
        pytest.importorskip("yaml")
    return request.param


READERS = {"json": read_json, "toml": read_toml, "yaml": read_yaml}


def test_json_toml_and_yaml_round_trip(tmp_path, nested_format):
    path = tmp_path / f"x.{nested_format}"
    WRITERS[nested_format](path, DATA)
    assert READERS[nested_format](path) == DATA


def test_tricky_strings_round_trip(tmp_path, nested_format):
    path = tmp_path / f"x.{nested_format}"
    WRITERS[nested_format](path, {"s": TRICKY, "keys": {text: 1 for text in TRICKY if text}})
    back = READERS[nested_format](path)
    assert back["s"] == TRICKY and list(back["keys"]) == [text for text in TRICKY if text]


def test_yaml_is_read_the_same_by_plain_pyyaml(tmp_path):
    yaml = pytest.importorskip("yaml")
    path = tmp_path / "x.yaml"
    write_yaml(path, {"s": TRICKY, **DATA})
    assert yaml.safe_load(path.read_text()) == {"s": TRICKY, **DATA}      # YAML 1.1 readers get strings too


def test_floats_and_infinities(tmp_path):
    values = [0.0, -0.0, 1.5, 1e16, 1e-5, -2.5e-300, float("inf"), float("-inf")]
    write_toml(tmp_path / "x.toml", {"v": values})
    assert read_toml(tmp_path / "x.toml")["v"] == values
    pytest.importorskip("yaml")
    write_yaml(tmp_path / "x.yaml", {"v": values})
    assert read_yaml(tmp_path / "x.yaml")["v"] == values


def test_json_refuses_nan(tmp_path):
    with pytest.raises(ValueError):
        write_json(tmp_path / "x.json", {"v": float("nan")})
    assert not (tmp_path / "x.json").exists()


def test_toml_tables_and_arrays_of_tables(tmp_path):
    data = {"servers": [{"name": "a", "tls": {"on": True}}, {"name": "b"}], "mixed": [1, {"a": 1}]}
    write_toml(tmp_path / "x.toml", data)
    text = (tmp_path / "x.toml").read_text()
    assert "[[servers]]" in text and "[servers.tls]" in text
    assert read_toml(tmp_path / "x.toml") == data


def test_toml_leaves_out_none_and_refuses_it_in_a_list(tmp_path):
    write_toml(tmp_path / "x.toml", {"a": None, "b": 1})
    assert read_toml(tmp_path / "x.toml") == {"b": 1}
    with pytest.raises(ValueError, match = "no null"):
        write_toml(tmp_path / "y.toml", {"l": [1, None]})


def test_null_in_json_and_yaml(tmp_path):
    write_json(tmp_path / "x.json", {"a": None})
    assert read_json(tmp_path / "x.json") == {"a": None}
    pytest.importorskip("yaml")
    write_yaml(tmp_path / "x.yaml", {"a": None, "l": [None]})
    assert read_yaml(tmp_path / "x.yaml") == {"a": None, "l": [None]}


# ── ini ─────────────────────────────────────────────────────────────────────

def test_ini_layout_and_round_trip(tmp_path):
    from cfgcast import cfg

    @cfg
    class Pool:
        size: int
        hosts: list[str]

    @cfg
    class Db:
        host: str
        pool: Pool

    @cfg
    class App:
        name: str
        tags: list[str]
        none: list[str]
        db: Db

    data = {"name": "shop", "tags": ["a", "b"], "none": [], "db": {"host": "h", "pool": {"size": 2, "hosts": ["x", "y"]}}}
    write_ini(tmp_path / "x.ini", data)
    text = (tmp_path / "x.ini").read_text()
    assert text.startswith("[DEFAULT]\n") and "[db]\n" in text and "[db.pool]\n" in text and "none =\n" in text
    back = read_ini(tmp_path / "x.ini", App)
    assert back == {"name": "shop", "tags": ["a", "b"], "none": [], "db": {"host": "h", "pool": {"size": "2", "hosts": ["x", "y"]}}}


def test_ini_writes_booleans_lower_case_and_skips_none(tmp_path):
    write_ini(tmp_path / "x.ini", {"a": True, "b": False, "c": None, "pct": "100%"})
    assert (tmp_path / "x.ini").read_text() == "[DEFAULT]\na = true\nb = false\npct = 100%\n"


@pytest.mark.parametrize("data, reason", [
    ({"s": "line\nbreak"}, "line break"),
    ({"s": " lead"}, "spaces around"),
    ({"l": ["a,b"]}, "comma"),
    ({"l": [{"a": 1}]}, "list of sections"),
    ({"l": [[1]]}, "list of list"),
    ({"DEFAULT": {"a": 1}}, "DEFAULT"),
    ({"a.b": {"c": 1}}, "section name"),
    ({"k=y": 1}, "ini key"),
    ({"[k": 1}, "ini key"),
    ], ids = str)
def test_ini_refuses_what_it_cannot_hold(tmp_path, data, reason):
    with pytest.raises(ValueError, match = reason):
        write_ini(tmp_path / "x.ini", data)
    assert not (tmp_path / "x.ini").exists()                        # nothing written


# ── what every writer is handed ─────────────────────────────────────────────

class Colour(Enum):
    RED = "red"


def test_plain_turns_subclasses_and_a_few_types_into_built_ins():
    plain = _plain({"secret": SecretStr("real-text"), "port": Port(80), "colour": Colour.RED,
                    "path": PurePosixPath("/etc/x"), "amount": Decimal("1.50"),
                    "id": UUID(int = 1), "set": frozenset({1}), "tuple": (1, 2), "flag": True})
    assert plain == {"secret": "real-text", "port": 80, "colour": "red", "path": "/etc/x", "amount": "1.50",
                     "id": "00000000-0000-0000-0000-000000000001", "set": [1], "tuple": [1, 2], "flag": True}
    assert type(plain["secret"]) is str and type(plain["port"]) is int and type(plain["flag"]) is bool


def test_a_secret_is_written_as_its_real_text(tmp_path):
    for fmt, reader in (("json", read_json), ("toml", read_toml)):
        WRITERS[fmt](tmp_path / f"x.{fmt}", {"key": SecretStr("real-text")})
        assert reader(tmp_path / f"x.{fmt}") == {"key": "real-text"}
        assert "*" not in (tmp_path / f"x.{fmt}").read_text()


def test_what_no_format_can_hold_is_a_type_error_before_the_file_is_opened(tmp_path):
    for fmt in ("json", "toml", "ini", "yaml"):
        with pytest.raises(TypeError, match = "can't be written"):
            WRITERS[fmt](tmp_path / f"x.{fmt}", {"when": object()})
        with pytest.raises(TypeError, match = "a key of a configuration"):
            WRITERS[fmt](tmp_path / f"y.{fmt}", {(1, 2): "x"})
        assert not (tmp_path / f"x.{fmt}").exists() and not (tmp_path / f"y.{fmt}").exists()


def test_every_format_has_a_writer():
    assert set(WRITERS) == {"json", "toml", "ini", "yaml"}


def test_files_end_with_a_newline_and_use_unix_line_ends(tmp_path):
    for fmt in ("json", "toml", "ini", "yaml"):
        WRITERS[fmt](tmp_path / f"x.{fmt}", {"a": 1, "s": {"b": 2}})
        raw = (tmp_path / f"x.{fmt}").read_bytes()
        assert raw.endswith(b"\n") and b"\r\n" not in raw, fmt


def test_empty_data(tmp_path):
    write_json(tmp_path / "x.json", {})
    assert read_json(tmp_path / "x.json") == {}
    write_toml(tmp_path / "x.toml", {})
    assert read_toml(tmp_path / "x.toml") == {}
    pytest.importorskip("yaml")
    write_yaml(tmp_path / "x.yaml", {})
    assert read_yaml(tmp_path / "x.yaml") == {}


def _unused(_: Path) -> None: ...


# ── dates and times ─────────────────────────────────────────────────────────

WHEN = {
    "day": date(2026, 10, 17),
    "at": datetime(2026, 10, 17, 12, 30, 5),
    "exact": datetime(2026, 10, 17, 12, 30, 5, 123456, tzinfo = timezone(timedelta(hours = 2))),
    "utc": datetime(2026, 10, 17, 12, 30, tzinfo = timezone.utc),
    "clock": time(2, 15),
    "days": [date(2026, 1, 1), date(2026, 12, 31)],
    "in": {"day": date(2000, 2, 29)},
    }


def test_toml_writes_dates_and_times_as_its_own_literals(tmp_path):
    write_toml(tmp_path / "x.toml", WHEN)
    text = (tmp_path / "x.toml").read_text()
    assert "day = 2026-10-17\n" in text and "at = 2026-10-17T12:30:05\n" in text and "clock = 02:15:00\n" in text
    assert "exact = 2026-10-17T12:30:05.123456+02:00\n" in text and "days = [2026-01-01, 2026-12-31]\n" in text
    assert read_toml(tmp_path / "x.toml") == WHEN                   # the same objects back, offsets included


def test_toml_writes_a_time_with_an_offset_as_text(tmp_path):
    write_toml(tmp_path / "x.toml", {"clock": time(2, 15, tzinfo = timezone.utc)})
    assert read_toml(tmp_path / "x.toml") == {"clock": "02:15:00+00:00"}       # TOML's time has no offset


def test_yaml_writes_dates_bare_and_a_time_quoted(tmp_path):
    pytest.importorskip("yaml")
    write_yaml(tmp_path / "x.yaml", WHEN)
    text = (tmp_path / "x.yaml").read_text()
    assert "day: 2026-10-17\n" in text and 'clock: "02:15:00"\n' in text and "  - 2026-01-01\n" in text
    assert read_yaml(tmp_path / "x.yaml") == {**WHEN, "clock": "02:15:00"}    # a date and a datetime back


def test_json_and_ini_write_dates_as_iso_text(tmp_path):
    write_json(tmp_path / "x.json", WHEN)
    assert read_json(tmp_path / "x.json") == {
        "day": "2026-10-17", "at": "2026-10-17T12:30:05", "exact": "2026-10-17T12:30:05.123456+02:00",
        "utc": "2026-10-17T12:30:00+00:00", "clock": "02:15:00", "days": ["2026-01-01", "2026-12-31"],
        "in": {"day": "2000-02-29"}}
    write_ini(tmp_path / "x.ini", WHEN)
    assert read_ini(tmp_path / "x.ini") == {
        "day": "2026-10-17", "at": "2026-10-17T12:30:05", "exact": "2026-10-17T12:30:05.123456+02:00",
        "utc": "2026-10-17T12:30:00+00:00", "clock": "02:15:00", "days": "2026-01-01, 2026-12-31",
        "in": {"day": "2000-02-29"}}


def test_json_still_refuses_what_is_no_configuration_value(tmp_path):
    with pytest.raises(TypeError, match = "can't be written"):
        write_json(tmp_path / "x.json", {"a": object()})
    assert not (tmp_path / "x.json").exists()


# ── found by review ─────────────────────────────────────────────────────────

def test_keys_that_are_no_text_are_written_as_loading_reads_them_back(tmp_path):
    class Colour(Enum):
        RED = "red"

    data = {"by_number": {1: "a", 2.5: "b"}, "by_flag": {True: "yes"}, "by_enum": {Colour.RED: 1},
            "by_day": {date(2026, 1, 1): 1}, "by_id": {UUID(int = 1): 1}}
    write_json(tmp_path / "x.json", data)
    assert read_json(tmp_path / "x.json") == {
        "by_number": {"1": "a", "2.5": "b"}, "by_flag": {"true": "yes"}, "by_enum": {"red": 1},
        "by_day": {"2026-01-01": 1}, "by_id": {"00000000-0000-0000-0000-000000000001": 1}}
    write_toml(tmp_path / "x.toml", data)
    assert read_toml(tmp_path / "x.toml") == read_json(tmp_path / "x.json")


def test_a_file_that_was_there_is_kept_when_the_text_cannot_be_encoded(tmp_path):
    for fmt in ("json", "toml", "ini"):                             # YAML writes it as an escape ("\\uDCFF")
        path = tmp_path / f"keep.{fmt}"
        path.write_text("as it was")
        with pytest.raises(UnicodeEncodeError):
            WRITERS[fmt](path, {"s": "bad\udcff"})                  # a lone surrogate is no UTF-8
        assert path.read_text() == "as it was"


def test_files_are_written_with_unix_line_ends(tmp_path):
    write_toml(tmp_path / "x.toml", {"a": 1, "b": {"c": 2}})
    assert (tmp_path / "x.toml").read_bytes() == b"a = 1\n\n[b]\nc = 2\n"


def test_ini_messages_never_hold_the_value(tmp_path):
    for data in ({"s": " hunter2"}, {"l": ["hunter2,x"]}, {"s": "hunter2\nx"}):
        with pytest.raises(ValueError) as info:
            write_ini(tmp_path / "x.ini", data)
        assert "hunter2" not in str(info.value)


def test_ini_refuses_a_list_of_one_empty_text(tmp_path):
    with pytest.raises(ValueError, match = "empty"):
        write_ini(tmp_path / "x.ini", {"l": [""]})
    write_ini(tmp_path / "x.ini", {"l": ["", "a"]})                 # two items: the comma tells them apart
    assert read_ini(tmp_path / "x.ini")["l"] == ", a"
