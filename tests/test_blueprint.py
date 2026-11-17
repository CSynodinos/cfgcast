"""The blueprint imports, and a schema can be written against it. Behaviour comes with the implementation."""
from dataclasses import field

from cfgcast import Config, ConfigError


def test_the_package_imports_and_a_schema_can_be_declared():
    class Database(Config):
        host: str
        port: int = 5432

    class App(Config, strict = False):
        name: str
        db: Database
        hosts: list[str] = field(default_factory = list)

    assert issubclass(ConfigError, ValueError) and callable(App.load) and callable(App.from_dict)
