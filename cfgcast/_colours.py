"""
The colours of the messages: clitint's, where it is installed (``pip install "cfgcast[colour]"``), else plain
text. The only module of the package that imports clitint; every other one imports from here (as enforced's
``_colours`` is for enforced).

Without clitint the functions below keep its signatures and return types, and every message is plain.
"""
from typing import Any

__all__ = ["HAS_CLITINT", "formatter", "turn_colours_off", "turn_colours_on"]

#* The plain versions come first and clitint's replace them where it is installed. The other way round
#* (`except ImportError:` defining them) a static checker without clitint takes each name for the import
#* that can't be resolved.


def formatter(text: Any, spec: str) -> str:
    """``text`` as it is: there are no colours without clitint."""
    return str(text)


def turn_colours_off() -> None:
    """Plain text from now on: always the case without clitint (``pip install "cfgcast[colour]"``)."""


def turn_colours_on() -> None:
    """Coloured text from now on, where clitint is installed (``pip install "cfgcast[colour]"``); without it, nothing."""


HAS_CLITINT: bool
try:
    from clitint import (                                # type: ignore[import-not-found, no-redef, unused-ignore]  # optional: cfgcast[colour]
        formatter,
        turn_colours_off,
        turn_colours_on,
        )
    HAS_CLITINT = True
except ImportError:
    HAS_CLITINT = False
