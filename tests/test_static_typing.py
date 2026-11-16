"""What a type checker sees of a configuration: mypy on the samples in `tests/typing_samples/`."""
from pathlib import Path
import re

import pytest

from cfgcast import ConfigError, Loadable, cfg, from_dict

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
    assert revealed == ["App", "int", "App", "App", "str", "Plain", "Plain", "int", "list[str]", "dict[str, str]", "dict[str, Any]"]


def test_a_checker_refuses_what_is_wrong():
    source = (SAMPLES / "sample_bad.py").read_text().splitlines()
    expected = {number for number, line in enumerate(source, 1) if "# E:" in line}
    out, status = _mypy("sample_bad.py")
    flagged = {int(match.group(1)) for match in re.finditer(r"sample_bad\.py:(\d+): error", out)}
    assert status == 1 and flagged == expected, out


# ── the same at run time ────────────────────────────────────────────────────

@cfg
class Db(Loadable):
    host: str
    port: int = 5432


def test_the_base_changes_nothing_at_run_time(tmp_path):
    (tmp_path / "db.json").write_text('{"host": "h", "port": "81"}')
    assert Db.load(tmp_path / "db.json") == Db(host = "h", port = 81) == Db.from_dict({"host": "h", "port": 81})
    assert from_dict(Db, {"host": "h"}) == Db(host = "h")
    assert Db.__mro__ == (Db, Loadable, object)
    with pytest.raises(ConfigError):
        from_dict(Db, {})
    with pytest.raises(TypeError, match = "not inheritable"):
        class Below(Db):
            pass


def test_the_base_alone_makes_no_configuration():
    class Bare(Loadable):
        name: str

    with pytest.raises(TypeError, match = "is not a @cfg class"):
        Bare.load("x.toml")
    with pytest.raises(TypeError, match = "is not a @cfg class"):
        Bare.from_dict({})
    with pytest.raises(TypeError, match = "is not a @cfg class"):
        from_dict(dict, {})                                           # type: ignore[type-var]
