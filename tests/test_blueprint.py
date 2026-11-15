"""The blueprint imports, and a schema can be written against it. Behaviour comes with the implementation."""
from dataclasses import field

from cfgcast import ConfigError, cfg, load_cfg


def test_the_package_imports_and_a_schema_can_be_declared():
    @cfg
    class Database:
        host: str
        port: int = 5432

    @cfg(strict = False)
    class App:
        name: str
        db: Database
        hosts: list[str] = field(default_factory = list)

    assert issubclass(ConfigError, ValueError) and callable(load_cfg) and callable(cfg)
