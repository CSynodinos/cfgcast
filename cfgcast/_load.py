"""`load_cfg`: read a file in the format its suffix says, and build the schema from it."""
from ._readers import CfgType
from ._base import SUFFIXES, is_config
from typing import Literal
import os

__all__ = ["SUFFIXES", "load_cfg"]


def load_cfg[T](
    file: str | os.PathLike[str],
    schema: type[T],
    cfg_type: CfgType | Literal["infer"] = "infer",
    *,
    show_warnings: bool = True) -> T:
    """
    Read `file` and return an instance of `schema`, or raise a `ConfigError` with every
    problem found (unknown keys, missing keys, wrong values), each with its file and key path.

    `cfg_type = "infer"` takes the format from the suffix (`SUFFIXES`).

    Unknown keys that `strict = False` ignores give one `ConfigWarning` that names them, unless
    `show_warnings = False`.
    """
    if not (isinstance(schema, type) and is_config(schema)):
        raise TypeError(f"load_cfg: {schema!r} is not a @cfg class")
    return schema.load(file, cfg_type, show_warnings = show_warnings)                             # type: ignore[attr-defined]  # added by cfg
