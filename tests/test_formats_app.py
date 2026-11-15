"""`test_formats.py`, the application written against cfgcast, as one test: every check of it passes."""
from pathlib import Path
import runpy

import pytest

APP = Path(__file__).parent.parent / "test_formats.py"


def test_the_application_passes_every_check(capsys):
    pytest.importorskip("yaml")                                      # without PyYAML its YAML checks are skipped
    namespace = runpy.run_path(str(APP))                             # not as __main__: main() is called here
    status = namespace["main"]()
    report = capsys.readouterr().out
    assert status == 0, report
    assert "0 failed, 0 skipped" in report, report
