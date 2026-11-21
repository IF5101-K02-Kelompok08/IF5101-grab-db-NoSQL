"""Connection settings and database names, read from the environment (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data' / 'canonical'
RESULTS_DIR = ROOT / 'benchmarks' / 'results'
DEFAULT_REFERENCE_FILE = ROOT / 'data' / 'reference' / 'couchdb_reference.json'

# Logical database -> Part 2 tables whose rows it holds.
DATABASES = {
    'orders': ('service_orders', 'food_orders', 'mobility_orders', 'delivery_orders',
               'payments', 'trips', 'driver_assignments'),
    'fleet': ('driver_partners', 'vehicles'),
    'customers': ('customers',),
    'geo': ('service_areas', 'locations', 'merchant_outlets'),
}
# The role given to the application user. Database members must have it.
APP_ROLE = 'grab_app'


def load_dotenv(path: Path = ROOT / '.env') -> None:
    """Read KEY=VALUE lines from .env into os.environ without overriding existing values."""
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    url: str
    admin_user: str
    admin_password: str
    app_user: str
    app_password: str
    db_prefix: str

    def db(self, name: str) -> str:
        """Physical database name of a logical database ('orders' -> 'grab_orders')."""
        if name not in DATABASES:
            raise KeyError(f'Unknown database {name!r}; expected one of {sorted(DATABASES)}')
        return f'{self.db_prefix}_{name}'

    @property
    def has_app_user(self) -> bool:
        return bool(self.app_user and self.app_password)


def get_settings() -> Settings:
    load_dotenv()
    admin_user = os.getenv('COUCHDB_USER', '')
    admin_password = os.getenv('COUCHDB_PASSWORD', '')
    if not admin_user or not admin_password:
        raise RuntimeError('Set COUCHDB_USER and COUCHDB_PASSWORD (see .env.example).')
    return Settings(
        url=os.getenv('COUCHDB_URL', 'http://127.0.0.1:5984').rstrip('/'),
        admin_user=admin_user,
        admin_password=admin_password,
        app_user=os.getenv('COUCHDB_APP_USER', ''),
        app_password=os.getenv('COUCHDB_APP_PASSWORD', ''),
        db_prefix=os.getenv('COUCHDB_DB_PREFIX', 'grab'),
    )
