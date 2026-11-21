"""Load the canonical dataset into CouchDB and build the indexes (report section 3.1).

    uv run --no-sync python -m couchdb.load --reset

Steps, each timed and written to benchmarks/results/couchdb_load_report.json:

1. Create the four databases, their security objects, the application user and the
   validate_doc_update functions. Validation is installed *before* loading, so every
   canonical row passes the same rules as later application writes.
2. Transform the CSV files into documents and write them with _bulk_docs, several batches
   in parallel.
3. Install the view design documents and Mango indexes one at a time and build each one.
   Views are added after loading on purpose: CouchDB 3 indexes in the background ("ken")
   as soon as a design document exists, which would compete with loading and blur both
   timings.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path

from couchdb import setup_db, transform
from couchdb.client import CouchClient
from couchdb.settings import DATABASES, DEFAULT_DATA_DIR, RESULTS_DIR, Settings, get_settings

LOAD_ORDER = ('geo', 'customers', 'fleet', 'orders')


def batches(docs, size):
    iterator = iter(docs)
    while batch := list(islice(iterator, size)):
        yield batch


class BulkWriter:
    """Send batches to _bulk_docs from a pool of threads, each with its own HTTP session."""

    def __init__(self, settings: Settings, workers: int) -> None:
        self.settings, self.workers = settings, workers
        self.local = threading.local()
        self.errors: list[dict] = []

    def client(self) -> CouchClient:
        if not hasattr(self.local, 'client'):
            self.local.client = CouchClient.admin(self.settings, timeout=600)
        return self.local.client

    def send(self, db: str, batch: list[dict]) -> int:
        results = self.client().bulk_docs(db, batch)
        failed = [r for r in results if 'error' in r]
        self.errors.extend(failed)
        return len(batch) - len(failed)

    def load(self, db: str, docs, batch_size: int) -> tuple[int, float]:
        written = 0
        started = time.perf_counter()
        last_report = started
        with ThreadPoolExecutor(self.workers) as pool:
            pending = set()
            for batch in batches(docs, batch_size):
                # Keep at most two batches per worker in flight to bound memory.
                if len(pending) >= self.workers * 2:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    written += sum(f.result() for f in done)
                pending.add(pool.submit(self.send, db, batch))
                if time.perf_counter() - last_report > 15:
                    last_report = time.perf_counter()
                    rate = written / (last_report - started)
                    print(f'    {db}: {written:,} docs ({rate:,.0f} docs/s)', flush=True)
            written += sum(f.result() for f in pending)
        return written, time.perf_counter() - started


def database_sizes(client: CouchClient, settings: Settings) -> dict:
    sizes = {}
    for name in DATABASES:
        info = client.db_info(settings.db(name))
        sizes[settings.db(name)] = {'doc_count': info.get('doc_count'),
                                    'doc_del_count': info.get('doc_del_count'),
                                    'sizes': info.get('sizes')}
    return sizes


def view_sizes(client: CouchClient, settings: Settings) -> dict:
    from couchdb import design
    sizes = {}
    for name in DATABASES:
        db = settings.db(name)
        for ddoc in design.view_ddocs(name):
            info = client.json('GET', f'/{db}/{ddoc["_id"]}/_info')
            sizes[f'{db}/{ddoc["_id"]}'] = info.get('view_index', {}).get('sizes')
    return sizes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Load the canonical dataset into CouchDB.')
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument('--reset', action='store_true',
                        help='drop and recreate the databases first (required for a clean load)')
    parser.add_argument('--batch-size', type=int, default=2000, help='documents per _bulk_docs')
    parser.add_argument('--workers', type=int, default=4, help='parallel _bulk_docs requests')
    parser.add_argument('--skip-index-build', action='store_true',
                        help='install views but let CouchDB build them in the background')
    parser.add_argument('--report', type=Path, default=RESULTS_DIR / 'couchdb_load_report.json')
    args = parser.parse_args(argv)

    settings = get_settings()
    admin = CouchClient.admin(settings, timeout=None)
    info = setup_db.wait_until_up(admin)
    versions = admin.json('GET', '/_node/_local/_versions')
    print(f'[UP] CouchDB {info.get("version")} at {settings.url}', flush=True)

    report = {'started_at_utc': datetime.now(UTC).isoformat(), 'couchdb_version': info.get('version'),
              'javascript_engine': versions.get('javascript_engine'),
              'data_dir': str(args.data_dir), 'batch_size': args.batch_size,
              'workers': args.workers, 'databases': {}, 'index_builds': []}
    setup_db.ensure_databases(admin, settings, reset=args.reset)
    setup_db.ensure_app_user(admin, settings)
    setup_db.install_validation(admin, settings)

    writer = BulkWriter(settings, args.workers)
    started = time.perf_counter()
    lookups = transform.load_lookups(args.data_dir)
    report['lookup_seconds'] = round(time.perf_counter() - started, 3)
    total_started = time.perf_counter()
    for name in LOAD_ORDER:
        db = settings.db(name)
        print(f'[LOAD] {db}', flush=True)
        errors_before = len(writer.errors)
        written, seconds = writer.load(db, transform.database_docs(args.data_dir, name, lookups),
                                       args.batch_size)
        failed = len(writer.errors) - errors_before
        report['databases'][db] = {'documents_written': written, 'documents_failed': failed,
                                   'load_seconds': round(seconds, 3),
                                   'docs_per_second': round(written / seconds, 1) if seconds else None}
        print(f'[LOAD] {db}: {written:,} docs in {seconds:.1f}s'
              + (f', {failed:,} FAILED' if failed else ''), flush=True)
    report['load_seconds_total'] = round(time.perf_counter() - total_started, 3)
    if writer.errors:
        report['first_errors'] = writer.errors[:20]
        for error in writer.errors[:5]:
            print(f'[ERROR] {error}', file=sys.stderr, flush=True)

    started = time.perf_counter()
    report['index_builds'] = setup_db.install_views(admin, settings,
                                                    build=not args.skip_index_build)
    if not args.skip_index_build:
        report['index_build_seconds_total'] = round(time.perf_counter() - started, 3)
        report['view_index_sizes'] = view_sizes(admin, settings)
    report['database_sizes'] = database_sizes(admin, settings)
    report['finished_at_utc'] = datetime.now(UTC).isoformat()

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'[DONE] report written to {args.report}', flush=True)
    return 1 if writer.errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
