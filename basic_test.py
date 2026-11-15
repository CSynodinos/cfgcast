from pathlib import Path
from typing import Literal
from cfgcast import cfg, load_cfg#, turn_colours_off, turn_colours_on
from enforced.data_types import Port, SecretStr, Probability, Url, Email, PhoneNumber
# turn_colours_off()
# turn_colours_on()

CFGS = Path(__file__).parent / "cfgs"


@cfg
class SimpleCfg:
    name: str
    version: str
    debug: bool
    port: int
    timeout: float
    workers: int
    log_level: str
    hosts: list[str]


@cfg
class Pool:
    min_size: int
    max_size: int
    timeout: float


@cfg
class Replica:
    host: str
    port: Port
    read_only: bool


@cfg
class Database:
    host: str
    port: Port
    name: str
    user: str
    password: SecretStr
    pool: Pool
    replica: Replica


@cfg
class Cache:
    backend: str
    ttl: float
    max_items: int
    namespaces: list[str]


@cfg
class Tls:
    enabled: bool
    cert_path: str
    key_path: str


@cfg
class Server:
    host: str
    port: Port
    workers: int
    open_ports: list[Port]
    tls: Tls


@cfg
class Fleet:
    name: str
    servers: list[Server]                                   # a list of sections: TOML's [[servers]]


@cfg
class Rotation:
    max_mb: int
    backups: int


@cfg
class Logging:
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    format: str
    handlers: list[str]
    rotation: Rotation


@cfg
class Features:
    new_checkout: bool
    wishlist: bool
    rollout: Probability
    beta_users: list[str]
    weights: list[Probability]


@cfg
class Monitoring:
    metrics_url: Url
    sample_rate: Probability
    tags: list[str]


@cfg
class ComplexCfg:
    name: str
    environment: str
    debug: bool
    secret_key: SecretStr
    base_url: Url
    support_email: Email
    support_phone: PhoneNumber
    fallback_locale: str
    allowed_hosts: list[str]
    db: Database
    cache: Cache
    server: Server
    logging: Logging
    features: Features
    monitoring: Monitoring


@cfg(strict = False)
class BadComplexCfg:
    name: str
    environment: str
    debug: bool
    secret_key: SecretStr
    base_url: Url
    support_email: Email
    support_phone: PhoneNumber
    fallback_locale: str
    allowed_hosts: list[str]
    db: Database
    cache: Cache
    server: Server
    logging: Logging
    features: Features
    monitoring: Monitoring


def main():
    config = load_cfg(
        file = CFGS / "complex.ini",
        schema = ComplexCfg
        )
    print(config)

    load_cfg(
        file = CFGS / "complex_bad.ini",
        schema = BadComplexCfg
        )

    fleet = load_cfg(
        file = CFGS / "fleet.yaml",
        schema = Fleet
        )

    print(fleet)


if __name__ == "__main__":
    main()