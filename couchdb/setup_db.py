"""Create the CouchDB databases, security objects, application user and design documents.

    uv run --no-sync python -m couchdb.setup_db            # create what is missing
    uv run --no-sync python -m couchdb.setup_db --reset    # drop and recreate the databases
    uv run --no-sync python -m couchdb.setup_db --views    # also install views and indexes

Normally couchdb.load runs these steps itself, in this order: databases and validation
functions before loading, views and Mango indexes after loading (see load.py for why).
"""

from __future__ import annotations

import argparse
import threading
import time

from couchdb import design
from couchdb.client import CouchClient, CouchError
from couchdb.settings import APP_ROLE, DATABASES, Settings, get_settings


def wait_until_up(client: CouchClient, timeout: float = 90) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        try:
            client.request('GET', '/_up')
            return client.server_info()
        except Exception:
            if time.monotonic() > deadline:
                raise RuntimeError(f'CouchDB at {client.url} did not answer within {timeout}s')
            time.sleep(2)


def ensure_databases(client: CouchClient, settings: Settings, reset: bool = False) -> None:
    for name in DATABASES:
        db = settings.db(name)
        if reset:
            client.delete_db(db)
        client.create_db(db)
        # Only admins and users with the application role may read or write the data.
        client.request('PUT', f'/{db}/_security', body={
            'admins': {'names': [], 'roles': []},
            'members': {'names': [], 'roles': [APP_ROLE]},
        })
        print(f'[DB] {db} ready', flush=True)


def ensure_app_user(client: CouchClient, settings: Settings) -> None:
    """Create or update the least-privileged user that the application and benchmark use."""
    if not settings.has_app_user:
        print('[USER] COUCHDB_APP_USER not set; operations will run as the admin', flush=True)
        return
    doc_id = f'org.couchdb.user:{settings.app_user}'
    existing = client.get_doc('_users', doc_id, missing_ok=True)
    doc = {'_id': doc_id, 'name': settings.app_user, 'type': 'user', 'roles': [APP_ROLE],
           'password': settings.app_password}
    if existing:
        doc['_rev'] = existing['_rev']
    client.put_doc('_users', doc)
    print(f'[USER] {settings.app_user} has role {APP_ROLE}', flush=True)


def put_design_doc(client: CouchClient, db: str, ddoc: dict) -> bool:
    """Install a design document; returns True when it changed (its indexes will rebuild)."""
    existing = client.get_doc(db, ddoc['_id'], missing_ok=True)
    if existing:
        current = {k: v for k, v in existing.items() if k != '_rev'}
        if current == ddoc:
            return False
        ddoc = dict(ddoc, _rev=existing['_rev'])
    client.put_doc(db, dict(ddoc))
    return True


def install_validation(client: CouchClient, settings: Settings) -> None:
    for name in DATABASES:
        ddoc = design.validation_ddoc(name)
        if ddoc and put_design_doc(client, settings.db(name), ddoc):
            print(f'[DDOC] {settings.db(name)}/{ddoc["_id"]} installed', flush=True)


def install_views(client: CouchClient, settings: Settings, build: bool = False) -> list[dict]:
    """Install the view design documents and Mango indexes, optionally building each one.

    CouchDB 3 starts indexing in the background as soon as a design document exists. To
    time each build on its own, every design document is installed and built before the
    next one is installed. Returns the build timings (empty when build=False).
    """
    timings = []
    monitor = TaskMonitor(client) if build else None
    if monitor:
        monitor.start()
    try:
        for name in DATABASES:
            db = settings.db(name)
            for ddoc in design.view_ddocs(name):
                if put_design_doc(client, db, ddoc):
                    print(f'[DDOC] {db}/{ddoc["_id"]} installed ({len(ddoc["views"])} views)',
                          flush=True)
                if build:
                    timings.append(build_view_index(client, db, ddoc))
            for index in design.mango_indexes(name):
                result = client.create_index(db, index)
                print(f'[INDEX] {db}/{result.get("id")} {result.get("name")}: '
                      f'{result.get("result")}', flush=True)
                if build:
                    timings.append(build_mango_index(client, db, index))
    finally:
        if monitor:
            monitor.stop()
    return timings


class TaskMonitor(threading.Thread):
    """Print indexer progress from /_active_tasks while indexes are being built."""

    def __init__(self, client: CouchClient, interval: float = 15) -> None:
        super().__init__(daemon=True)
        self.client, self.interval = client, interval
        self.stopped = threading.Event()

    def run(self) -> None:
        while not self.stopped.wait(self.interval):
            try:
                tasks = self.client.json('GET', '/_active_tasks')
            except Exception:
                continue
            for task in tasks:
                if task.get('type') in ('indexer', 'search_indexer'):
                    print(f'    indexing {task.get("database")} {task.get("design_document")}: '
                          f'{task.get("progress", "?")}% '
                          f'({task.get("changes_done")}/{task.get("total_changes")})', flush=True)

    def stop(self) -> None:
        self.stopped.set()


def build_view_index(client: CouchClient, db: str, ddoc: dict) -> dict:
    """Query a design document once so CouchDB builds its index, and time the build.

    All views of one design document share one index file and are built in the same pass
    over the database, so one query per design document is enough.
    """
    view_name, view = next(iter(ddoc['views'].items()))
    params = {'limit': 0}
    if 'reduce' in view:
        params['reduce'] = False
    started = time.perf_counter()
    client.request('GET', f'/{db}/{ddoc["_id"]}/_view/{view_name}', params=params, timeout=None)
    seconds = time.perf_counter() - started
    print(f'[BUILD] {db}/{ddoc["_id"]}: {seconds:.1f}s', flush=True)
    return {'database': db, 'design_doc': ddoc['_id'], 'kind': 'javascript views',
            'views': sorted(ddoc['views']), 'build_seconds': round(seconds, 3)}


def build_mango_index(client: CouchClient, db: str, index: dict) -> dict:
    """A one-row query with use_index forces a Mango index to be built."""
    fields = index['index']['fields']
    started = time.perf_counter()
    client.request('POST', f'/{db}/_find', timeout=None, body={
        'selector': {fields[0]: {'$gt': None}}, 'use_index': [index['ddoc'], index['name']],
        'sort': [{field: 'asc'} for field in fields], 'limit': 1})
    seconds = time.perf_counter() - started
    print(f'[BUILD] {db}/_design/{index["ddoc"]} (Mango): {seconds:.1f}s', flush=True)
    return {'database': db, 'design_doc': f'_design/{index["ddoc"]}', 'kind': 'mango json index',
            'views': [index['name']], 'build_seconds': round(seconds, 3)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--reset', action='store_true', help='drop and recreate the databases')
    parser.add_argument('--views', action='store_true', help='also install views and indexes')
    parser.add_argument('--build', action='store_true', help='also build the view indexes')
    args = parser.parse_args(argv)
    settings = get_settings()
    client = CouchClient.admin(settings)
    info = wait_until_up(client)
    print(f'[UP] CouchDB {info.get("version")} at {settings.url}', flush=True)
    try:
        ensure_databases(client, settings, reset=args.reset)
        ensure_app_user(client, settings)
        install_validation(client, settings)
        if args.views or args.build:
            install_views(client, settings, build=args.build)
    except CouchError as error:
        print(f'[FAIL] {error}', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
