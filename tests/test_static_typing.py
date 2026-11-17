"""What a type checker sees of a configuration: mypy on the samples in `tests/typing_samples/`."""
from pathlib import Path
import re

import pytest

from cfgcast import Config, ConfigError

api = pytest.importorskip("mypy.api")
SAMPLES = Path(__file__).parent / "typing_samples"


def _mypy(sample: str) -> tuple[str, int]:
    out, err, status = api.run([str(SAMPLES / sample), "--no-incremental", "--no-error-summary", "--hide-error-context"])
    assert not err, err
    return out, status


def test_a_checker_knows_load_and_from_dict_and_what_they_return():
    out, _ = _mypy("sample_good.py")
    lines = [line for line in out.splitlines() if line.strip()]
    assert all("note: Revealed type" in line for line in lines), out      # nothing but the reveals: no error
    revealed = [re.search(r'Revealed type is "([^"]+)"', line).group(1).split(".")[-1] for line in lines]
    assert revealed == ["App", "int", "App", "App", "str", "Plain", "Plain", "Prod", "str", "int", "list[str]", "dict[str, str]", "dict[str, Any]"]


def test_a_checker_refuses_what_is_wrong():
    source = (SAMPLES / "sample_bad.py").read_text().splitlines()
    expected = {number for number, line in enumerate(source, 1) if "# E:" in line}
    out, status = _mypy("sample_bad.py")
    flagged = {int(match.group(1)) for match in re.finditer(r"sample_bad\.py:(\d+): error", out)}
    assert status == 1 and flagged == expected, out


# ── the same at run time ────────────────────────────────────────────────────

class Db(Config):
    host: str
    port: int = 5432


def test_what_a_checker_sees_is_what_runs(tmp_path):
    (tmp_path / "db.json").write_text('{"host": "h", "port": "81"}')
    assert Db.load(tmp_path / "db.json") == Db(host = "h", port = 81) == Db.from_dict({"host": "h", "port": 81})
    assert Db.from_dict({"host": "h"}) == Db(host = "h")
    assert Db.__mro__ == (Db, Config, object)
    with pytest.raises(ConfigError):
        Db.from_dict({})
