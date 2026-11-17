from pathlib import Path
from typing import Literal
from cfgcast import Config#, turn_colours_off, turn_colours_on
from enforced.data_types import Port, SecretStr, Probability, Url, Email, PhoneNumber
# turn_colours_off()
# turn_colours_on()

CFGS = Path(__file__).parent / "cfgs"


class SimpleCfg(Config):
    name: str
    version: str
    debug: bool
    port: int
    timeout: float
    workers: int
    log_level: str
    hosts: list[str]


class Pool(Config):
    min_size: int
    max_size: int
    timeout: float


class Replica(Config):
    host: str
    port: Port
    read_only: bool


class Database(Config):
    host: str
    port: Port
    name: str
    user: str
    password: SecretStr
    pool: Pool
    replica: Replica


class Cache(Config):
    backend: str
    ttl: float
    max_items: int
    namespaces: list[str]


class Tls(Config):
    enabled: bool
    cert_path: str
    key_path: str


class Server(Config):
    host: str
    port: Port
    workers: int
    open_ports: list[Port]
    tls: Tls


class Fleet(Config):
    name: str
    servers: list[Server]                                   # a list of sections: TOML's [[servers]]


class Rotation(Config):
    max_mb: int
    backups: int


class Logging(Config):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    format: str
    handlers: list[str]
    rotation: Rotation


class Features(Config):
    new_checkout: bool
    wishlist: bool
    rollout: Probability
    beta_users: list[str]
    weights: list[Probability]


class Monitoring(Config):
    metrics_url: Url
    sample_rate: Probability
    tags: list[str]


class ComplexCfg(Config):
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


class BadComplexCfg(Config, strict = False):
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
    config = ComplexCfg.load(CFGS / "complex.ini")
    print(config)

    BadComplexCfg.load(CFGS / "complex_bad.ini")

    fleet = Fleet.load(CFGS / "fleet.yaml")

    print(fleet)
    fleet.name = "new_fleet_name"


if __name__ == "__main__":
    main()