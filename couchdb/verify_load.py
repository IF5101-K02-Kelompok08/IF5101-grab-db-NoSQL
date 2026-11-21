"""Check that CouchDB holds exactly the canonical dataset (report section 3.1).

    uv run --no-sync python -m couchdb.verify_load

1. Counts. For every Part 2 table, the number of rows held by the documents (counted by the
   entity_counts views, e.g. trips embedded in order documents) must equal the row count
   in data/canonical/manifest.json. Document counts per database are reported as well.
2. Sample. A random sample of documents (seed 42) is turned back into Part 2 rows
   (couchdb/model.py *_rows functions) and compared field by field with the CSV rows.

The result is printed as a table and written to benchmarks/results/couchdb_verify_report.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from couchdb import model
from couchdb.client import CouchClient
from couchdb.settings import DATABASES, DEFAULT_DATA_DIR, RESULTS_DIR, get_settings

DDOC = {'orders': 'analytics', 'fleet': 'fleet', 'customers': 'customers', 'geo': 'geo'}
ORDER_TABLES = DATABASES['orders']


def read(data_dir: Path, table: str):
    with (data_dir / f'{table}.csv').open(newline='', encoding='utf-8') as file:
        yield from csv.DictReader(file)


def count_rows(client: CouchClient, settings) -> dict[str, int]:
    counts = {}
    for name in DATABASES:
        rows = client.view(settings.db(name), DDOC[name], 'entity_counts', group=True)['rows']
        counts.update({row['key']: int(row['value']) for row in rows})
    return counts


def document_counts(client: CouchClient, settings) -> dict[str, int]:
    out = {}
    for name in DATABASES:
        db = settings.db(name)
        info = client.db_info(db)
        design = client.all_docs(db, startkey='_design/', endkey='_design0')['rows']
        out[db] = info['doc_count'] - len(design)
    return out


def sample_orders(client, settings, data_dir: Path, ids: set[str]) -> list[str]:
    expected = defaultdict(lambda: defaultdict(list))
    trips = {}
    for table in ORDER_TABLES[:-1]:
        for row in read(data_dir, table):
            if row['order_id'] in ids:
                expected[row['order_id']][table].append(row)
                if table == 'trips':
                    trips[row['trip_id']] = row['order_id']
    for row in read(data_dir, 'driver_assignments'):
        if row['trip_id'] in trips:
            expected[trips[row['trip_id']]]['driver_assignments'].append(row)
    docs = {r['key']: r.get('doc') for r in client.all_docs(
        settings.db('orders'), keys=sorted(ids), include_docs=True)['rows']}
    problems = []
    for order_id in sorted(ids):
        doc = docs.get(order_id)
        if doc is None:
            problems.append(f'{order_id}: document missing')
            continue
        got = {t: rows for t, rows in model.order_rows(doc).items() if rows}
        want = {t: rows for t, rows in expected[order_id].items() if rows}
        if got != want:
            problems.append(f'{order_id}: rows differ')
    return problems


def reservoir(items, k: int, rng: random.Random) -> list:
    """A uniform random sample of k items from a stream (Algorithm R)."""
    sample = []
    for index, item in enumerate(items):
        if index < k:
            sample.append(item)
        else:
            j = rng.randrange(index + 1)
            if j < k:
                sample[j] = item
    return sample


def sample_simple(client, settings, data_dir: Path, db: str, table: str, id_column: str,
                  to_rows, k: int, rng: random.Random) -> tuple[int, list[str]]:
    chosen = {r[id_column]: r for r in reservoir(read(data_dir, table), k, rng)}
    docs = {r['key']: r.get('doc') for r in client.all_docs(
        settings.db(db), keys=sorted(chosen), include_docs=True)['rows']}
    problems = []
    for doc_id, row in chosen.items():
        doc = docs.get(doc_id)
        if doc is None:
            problems.append(f'{doc_id}: document missing')
        elif to_rows(doc) != row:
            problems.append(f'{doc_id}: fields differ')
    return len(chosen), problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Verify the CouchDB load against the CSV.')
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument('--sample', type=int, default=1000, help='documents sampled per entity')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--report', type=Path, default=RESULTS_DIR / 'couchdb_verify_report.json')
    args = parser.parse_args(argv)

    settings = get_settings()
    client = CouchClient.admin(settings, timeout=None)
    manifest_file = args.data_dir / 'manifest.json'
    if manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    else:  # e.g. a sampled dataset: count the CSV rows instead
        manifest = {'tables': {t: {'rows': sum(1 for _ in read(args.data_dir, t))}
                               for tables in DATABASES.values() for t in tables}}
    started = time.perf_counter()
    counts = count_rows(client, settings)
    table_rows = []
    print(f'{"Part 2 table":<20} {"database":<16} {"CSV rows":>12} {"in CouchDB":>12}  match')
    for name, tables in DATABASES.items():
        for table in tables:
            expected = manifest['tables'][table]['rows']
            actual = counts.get(table, 0)
            table_rows.append({'table': table, 'database': settings.db(name),
                               'csv_rows': expected, 'couchdb_rows': actual,
                               'match': expected == actual})
            print(f'{table:<20} {settings.db(name):<16} {expected:>12,} {actual:>12,}  '
                  f'{"yes" if expected == actual else "NO"}')
    documents = document_counts(client, settings)
    print('\nDocuments per database: ' + ', '.join(f'{db}={n:,}' for db, n in documents.items()))

    rng = random.Random(args.seed)
    order_ids = set(reservoir((r['order_id'] for r in read(args.data_dir, 'service_orders')),
                              args.sample, rng))
    samples = {'orders': (len(order_ids), sample_orders(client, settings, args.data_dir, order_ids))}
    samples['customers'] = sample_simple(client, settings, args.data_dir, 'customers', 'customers',
                                         'customer_id', model.customer_rows, args.sample, rng)
    vehicles = defaultdict(list)
    for row in read(args.data_dir, 'vehicles'):
        vehicles[row['driver_partner_id']].append(row)
    samples['driver_partners+vehicles'] = sample_simple(
        client, settings, args.data_dir, 'fleet', 'driver_partners', 'driver_partner_id',
        lambda d, v=vehicles: (model.driver_rows(d)['driver_partners'][0]
                               if model.driver_rows(d)['vehicles'] == v[d['_id']] else None),
        args.sample, rng)
    for table, id_column in (('service_areas', 'service_area_id'), ('locations', 'location_id'),
                             ('merchant_outlets', 'merchant_outlet_id')):
        samples[table] = sample_simple(client, settings, args.data_dir, 'geo', table, id_column,
                                       lambda d: model.geo_rows(d)[1], args.sample, rng)
    print('\nSampled documents compared field by field with the CSV rows:')
    for entity, (size, problems) in samples.items():
        print(f'  {entity:<18} {size:>6} sampled, {len(problems)} mismatches')
        for problem in problems[:5]:
            print(f'      {problem}')

    passed = all(r['match'] for r in table_rows) and not any(p for _, p in samples.values())
    report = {'checked_at_utc': datetime.now(UTC).isoformat(), 'passed': passed,
              'seconds': round(time.perf_counter() - started, 1), 'tables': table_rows,
              'documents_per_database': documents,
              'samples': {k: {'sampled': n, 'mismatches': p[:20]} for k, (n, p) in samples.items()}}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'\n[{"PASS" if passed else "FAIL"}] report written to {args.report}')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
