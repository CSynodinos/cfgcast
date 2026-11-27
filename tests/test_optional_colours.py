"""
clitint is optional (``pip install "cfgcast[colour]"``): ``cfgcast._colours`` is the only module that imports
it. Without it cfgcast imports, every public name is there and every message is plain text. The interpreter
that runs these tests may have clitint or not, so the run without it is a fresh one in which clitint can't be
imported (``sys.modules["clitint"] = None``).
"""
import pathlib
import subprocess
import sys

import cfgcast
from cfgcast import _colours

ROOT = pathlib.Path(cfgcast.__file__).resolve().parent.parent

_WITHOUT_CLITINT = r'''
import sys, warnings
sys.modules["clitint"] = None                       # import clitint now raises ImportError
import cfgcast
from cfgcast import _colours, Config, ConfigError, ConfigWarning, FrozenConfigError

assert _colours.HAS_CLITINT is False
assert all(hasattr(cfgcast, name) for name in cfgcast.__all__)
assert sys.modules["clitint"] is None

class Db(Config):
    host: str
    port: int = 1

class Loose(Config, strict = False):
    name: str = "n"

try:
    Db.from_dict({"port": "x"})
except ConfigError as error:
    text = str(error)
assert "\x1b" not in text and "port: expected type: 'int', got type 'str'" in text and "host: missing key" in text, text

with warnings.catch_warnings(record = True) as seen:
    warnings.simplefilter("always")
    Loose.from_dict({"extra": 1})
assert issubclass(seen[0].category, ConfigWarning)
assert "\x1b" not in seen[0].message.shown() and "-> extra" in seen[0].message.shown()

try:
    Db(host = "h").host = "x"
except FrozenConfigError as error:
    assert "\x1b" not in str(error) and str(error).startswith("Db is frozen: host can't be assigned"), str(error)

assert cfgcast.turn_colours_on() is None            # nothing to switch on: still plain
try:
    Db.from_dict({})
except ConfigError as error:
    assert "\x1b" not in str(error)
assert cfgcast.turn_colours_off() is None
assert _colours.formatter(5, "bold_red") == "5"
print("plain")
'''


def test_without_clitint_every_name_is_there_and_every_message_is_plain():
    done = subprocess.run([sys.executable, "-c", _WITHOUT_CLITINT], cwd = ROOT, capture_output = True, text = True, timeout = 120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "plain"


def test_with_clitint_its_functions_are_the_ones_used():
    import importlib.util
    if importlib.util.find_spec("clitint") is None:
        return
    import clitint
    assert _colours.HAS_CLITINT is True
    assert _colours.formatter is clitint.formatter and cfgcast.turn_colours_off is clitint.turn_colours_off


def test_clitint_is_imported_by_one_module_only():
    for path in (ROOT / "cfgcast").glob("*.py"):
        if path.name != "_colours.py":
            assert "clitint" not in path.read_text(encoding = "utf-8"), path.name
