"""Expected results of the CouchDB read operations, computed straight from the canonical CSV.

    uv run --no-sync python -m couchdb.reference            # writes data/reference/...json

The benchmark adapter compares every CouchDB result against this file ("Hasil Benar?").
The computation re-implements the Part 2 SQL (R1-R10) with plain Python over the CSV
files and shares no code with the CouchDB transformation or views, so a mistake in the
document model or a view shows up as a mismatch. It was itself checked against PostgreSQL
running the original Part 2 SQL on sampled data (see docs/couchdb_design.md).

Rows are ordered like the SQL ORDER BY; ties that SQL leaves arbitrary are broken by ID,
the same way couchdb/queries.py does. Memory stays small by keeping per-order and per-trip
values in arrays indexed by the numeric part of the ID.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import unicodedata
from array import array
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from couchdb.rounding import as_float, money, percent, ratio
from couchdb.settings import DEFAULT_DATA_DIR, DEFAULT_REFERENCE_FILE

TRIP_STATUS = ('SEARCHING', 'ALLOCATED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')
PAYMENT_STATUS = ('PENDING', 'PAID', 'FAILED', 'REFUNDED')
ORDER_STATUS = ('PENDING', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', 'FAILED')

# Fixed parameters of the operational reads. The sample order and trip are the first rows
# of service_orders.csv and trips.csv (ORD0000001 and TRP0000001 in the full dataset).
R9_START, R9_END = '2026-10-01', '2026-11-01'
R10_AREA, R10_CITY = 'Tebet', 'Jakarta'


def rows(data_dir: Path, table: str):
    with (data_dir / f'{table}.csv').open(newline='', encoding='utf-8') as file:
        yield from csv.DictReader(file)


def num(identifier: str) -> int:
    return int(identifier[3:])


def epoch(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())


def iso(value: str) -> str | None:
    return datetime.fromisoformat(value).astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ') if value else None


def cents(value: str) -> int:
    return int(Decimal(value) * 100)


def iso_epoch(seconds: int) -> str | None:
    return None if seconds < 0 else datetime.fromtimestamp(seconds, UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def collation_key(text: str) -> str:
    """Approximates CouchDB's ICU collation for the outlet names (case-insensitive first)."""
    return unicodedata.normalize('NFKD', text).casefold()


class Grid:
    """A growable array indexed by the numeric part of an ID."""

    def __init__(self, typecode: str, default: int) -> None:
        self.data, self.default = array(typecode), default

    def __setitem__(self, index: int, value: int) -> None:
        if index >= len(self.data):
            self.data.extend([self.default] * (index + 1 - len(self.data)))
        self.data[index] = value

    def __getitem__(self, index: int) -> int:
        return self.data[index] if index < len(self.data) else self.default


def compute(data_dir: Path) -> dict:
    results: dict[str, list] = {}
    params: dict[str, dict] = {}

    # -- reference data -------------------------------------------------------------------
    areas = {r['service_area_id']: r for r in rows(data_dir, 'service_areas')}
    area_ids = sorted(areas)
    area_index = {a: i for i, a in enumerate(area_ids)}
    outlets = {r['merchant_outlet_id']: r for r in rows(data_dir, 'merchant_outlets')}
    outlet_locations = {o['location_id'] for o in outlets.values()}
    location_area = Grid('h', -1)
    location_details = {}
    for r in rows(data_dir, 'locations'):
        location_area[num(r['location_id'])] = area_index[r['service_area_id']]
        if r['location_id'] in outlet_locations:
            location_details[r['location_id']] = r
    outlet_area = {o: area_ids[location_area[num(r['location_id'])]] for o, r in outlets.items()}
    driver_names = {r['driver_partner_id']: r['full_name'] for r in rows(data_dir, 'driver_partners')}
    vehicles = {r['vehicle_id']: (r['driver_partner_id'], r['vehicle_type'])
                for r in rows(data_dir, 'vehicles')}

    # -- orders ---------------------------------------------------------------------------
    created = Grid('q', 0)
    order_status = Grid('b', -1)
    order_customer = Grid('i', 0)
    per_customer = Counter()
    sample = defaultdict(list)
    sample_order = sample_trip = None
    for r in rows(data_dir, 'service_orders'):
        sample_order = sample_order or r['order_id']
        n = num(r['order_id'])
        created[n] = epoch(r['created_at'])
        order_status[n] = ORDER_STATUS.index(r['order_status'])
        order_customer[n] = num(r['customer_id'])
        per_customer[r['customer_id']] += 1
        if r['order_id'] == sample_order:
            sample['service_orders'].append(r)
    food_outlet = Grid('i', 0)
    for table in ('food_orders', 'mobility_orders', 'delivery_orders'):
        for r in rows(data_dir, table):
            if table == 'food_orders':
                food_outlet[num(r['order_id'])] = num(r['merchant_outlet_id'])
            if r['order_id'] == sample_order:
                sample[table].append(r)

    # R7 and the per-order payment values used by R6 and R8.
    pay_status = Grid('b', -1)
    pay_cents = Grid('q', 0)
    monthly = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0, 0])
    for r in rows(data_dir, 'payments'):
        n = num(r['order_id'])
        status, amount = r['payment_status'], cents(r['amount'])
        pay_status[n] = PAYMENT_STATUS.index(status)
        pay_cents[n] = amount
        month = datetime.fromtimestamp(created[n], UTC).strftime('%Y-%m')
        m = monthly[month, r['payment_method']]
        m[0] += 1
        m[1 + ('PAID', 'REFUNDED', 'FAILED', 'PENDING').index(status)] += 1
        if status == 'PAID':
            m[5] += amount
        if r['paid_at']:
            m[6] += epoch(r['paid_at']) - created[n]
            m[7] += 1
        if r['order_id'] == sample_order:
            sample['payments'].append(r)
    r7 = []
    for (month, method), (total, paid, refunded, failed, pending, paid_c, settle, settled) in monthly.items():
        r7.append((month, -paid_c, method, {
            'order_month': f'{month}-01', 'payment_method': method, 'total_payments': total,
            'paid': paid, 'refunded': refunded, 'failed': failed, 'pending': pending,
            'paid_pct': as_float(percent(paid, total - pending)),
            'net_revenue': as_float(money(paid_c)),
            'avg_settle_minutes': as_float(ratio(settle, 60 * settled, 1))}))
    results['PF-10-Q02'] = [r[3] for r in sorted(r7, key=lambda r: r[:3])]

    # -- trips ----------------------------------------------------------------------------
    trip_status = Grid('b', -1)
    trip_started = Grid('q', -1)
    trip_completed = Grid('q', -1)
    trip_order = Grid('i', 0)
    first_trip_area = Grid('h', -1)
    first_trip_num = Grid('i', 0)
    trips_per_area = Counter()
    for r in rows(data_dir, 'trips'):
        t, o = num(r['trip_id']), num(r['order_id'])
        sample_trip = sample_trip or r['trip_id']
        trip_status[t] = TRIP_STATUS.index(r['trip_status'])
        trip_order[t] = o
        area = location_area[num(r['start_location_id'])]
        trips_per_area[area_ids[area]] += 1
        if r['started_at']:
            trip_started[t] = epoch(r['started_at'])
        if r['completed_at']:
            trip_completed[t] = epoch(r['completed_at'])
        if first_trip_num[o] == 0 or t < first_trip_num[o]:
            first_trip_num[o] = t
            first_trip_area[o] = area
        if r['order_id'] == sample_order:
            sample['trips'].append(r)
        if r['trip_id'] == sample_trip:
            results['PF-03-Q01'] = {'trip_id': r['trip_id'], 'order_id': r['order_id'],
                                    'trip_status': r['trip_status'],
                                    'started_at': iso(r['started_at']),
                                    'completed_at': iso(r['completed_at'])}

    r5 = [(-count, a, {'service_area_id': a, 'area_name': areas[a]['area_name'],
                       'city': areas[a]['city'], 'total_trips': count})
          for a, count in trips_per_area.items()]
    results['PF-09-Q01'] = [r[2] for r in sorted(r5, key=lambda r: r[:2])]

    # -- driver assignments ---------------------------------------------------------------
    decisions = defaultdict(lambda: [0, 0, 0])
    durations = defaultdict(lambda: [0, 0])
    hourly_trips = Counter()
    hours_by_vehicle = defaultdict(int)       # vehicle -> bit mask of start hours
    attempts = Grid('b', 0)
    accepted = Grid('b', 0)
    sample_trips = {r['trip_id'] for r in sample['trips']}
    for r in rows(data_dir, 'driver_assignments'):
        t = num(r['trip_id'])
        status = r['assignment_status']
        driver, vehicle_type = vehicles[r['vehicle_id']]
        attempts[t] = attempts[t] + 1
        if r['trip_id'] in sample_trips:
            sample['driver_assignments'].append(r)
        if status in ('ACCEPTED', 'REJECTED', 'EXPIRED'):
            decisions[driver][('ACCEPTED', 'REJECTED', 'EXPIRED').index(status)] += 1
        if status != 'ACCEPTED':
            continue
        accepted[t] = 1
        tstatus = TRIP_STATUS[trip_status[t]]
        started, completed = trip_started[t], trip_completed[t]
        if tstatus == 'COMPLETED':
            d = durations[driver]
            d[0] += completed - started
            d[1] += 1
        if tstatus in ('COMPLETED', 'IN_PROGRESS') and started >= 0:
            hour = started // 3600 % 24
            hourly_trips[hour, vehicle_type] += 1
            hours_by_vehicle[r['vehicle_id']] |= 1 << hour

    r1 = []
    for driver, (acc, rej, exp) in decisions.items():
        if acc + rej >= 5:
            rate = percent(acc, acc + rej)
            r1.append((-rate, -acc, driver, {
                'driver_partner_id': driver, 'full_name': driver_names[driver],
                'total_accepted': acc, 'total_rejected': rej, 'total_expired': exp,
                'acceptance_rate_percentage': as_float(rate)}))
    results['PF-08-Q01'] = [r[3] for r in sorted(r1, key=lambda r: r[:3])]

    r2 = sorted(((-s, d, c) for d, (s, c) in durations.items()))[:10]
    results['PF-08-Q02'] = [{
        'driver_partner_id': d, 'full_name': driver_names[d], 'total_completed_trips': c,
        'total_duration_minutes': as_float(ratio(-s, 60, 2)),
        'avg_duration_per_trip_minutes': as_float(ratio(-s, 60 * c, 2))} for s, d, c in r2]

    distinct = Counter()
    for vehicle_id, mask in hours_by_vehicle.items():
        vehicle_type = vehicles[vehicle_id][1]
        for hour in range(24):
            if mask >> hour & 1:
                distinct[hour, vehicle_type] += 1
    r3 = [{'vehicle_type': vt, 'trip_hour': h, 'total_trips': n, 'unique_vehicles_used': distinct[h, vt]}
          for (h, vt), n in hourly_trips.items()]
    results['PF-08-Q03'] = sorted(r3, key=lambda r: (r['trip_hour'], -r['total_trips'],
                                                     r['vehicle_type']))

    r4 = []
    for t in range(len(attempts.data)):
        if attempts[t] > 2 and accepted[t]:
            r4.append({'trip_id': f'TRP{t:07d}', 'order_id': f'ORD{trip_order[t]:07d}',
                       'trip_status': TRIP_STATUS[trip_status[t]], 'total_attempts': attempts[t],
                       'started_at': iso_epoch(trip_started[t]),
                       'completed_at': iso_epoch(trip_completed[t])})
    r4.sort(key=lambda r: (r['total_attempts'], r['started_at'] is None, r['started_at'] or '',
                           r['trip_id']), reverse=True)
    results['PF-02-Q04'] = r4

    # -- per-order joins: R6, R8, R9 ------------------------------------------------------
    by_area = defaultdict(lambda: [0, 0, 0, 0])
    by_outlet = defaultdict(lambda: [0, 0, 0, 0])
    october_demand = Counter()
    start, end = epoch(f'{R9_START} 00:00:00+00:00'), epoch(f'{R9_END} 00:00:00+00:00')
    for n in range(1, len(order_status.data)):
        if order_status[n] < 0:
            continue
        status = pay_status[n]
        if status >= 0 and PAYMENT_STATUS[status] in ('PAID', 'REFUNDED') and first_trip_area[n] >= 0:
            a = by_area[area_ids[first_trip_area[n]]]
            a[0] += 1
            if PAYMENT_STATUS[status] == 'PAID':
                a[1] += pay_cents[n]
            else:
                a[2] += pay_cents[n]
                a[3] += 1
        outlet_n = food_outlet[n]
        if not outlet_n:
            continue
        outlet_id = f'OUT{outlet_n:07d}'
        if ORDER_STATUS[order_status[n]] == 'COMPLETED' and status >= 0:
            o = by_outlet[outlet_id]
            o[0] += 1
            o[2] += pay_cents[n]
            if PAYMENT_STATUS[status] == 'PAID':
                o[1] += pay_cents[n]
            if PAYMENT_STATUS[status] == 'REFUNDED':
                o[3] += 1
        if start <= created[n] < end:
            october_demand[outlet_area[outlet_id]] += 1

    r6 = [(-paid, areas[a]['city'], areas[a]['area_name'], {
        'city': areas[a]['city'], 'area_name': areas[a]['area_name'], 'settled_payments': settled,
        'net_revenue': as_float(money(paid)), 'refunded_amount': as_float(money(refunded_c)),
        'refund_rate_pct': as_float(percent(refunded, settled))})
        for a, (settled, paid, refunded_c, refunded) in by_area.items()]
    results['PF-10-Q01'] = [r[3] for r in sorted(r6, key=lambda r: r[:3])]

    top = sorted(((-v[1], o) for o, v in by_outlet.items()))[:10]
    r8 = []
    for _, outlet_id in top:
        orders_n, paid, amount, refunded = by_outlet[outlet_id]
        outlet, area = outlets[outlet_id], areas[outlet_area[outlet_id]]
        r8.append({'merchant_outlet_id': outlet_id, 'outlet_name': outlet['outlet_name'],
                   'outlet_status': outlet['outlet_status'], 'city': area['city'],
                   'area_name': area['area_name'], 'completed_orders': orders_n,
                   'net_revenue': as_float(money(paid)),
                   'avg_order_value': as_float(ratio(amount, 100 * orders_n, 2)),
                   'refund_rate_pct': as_float(percent(refunded, orders_n))})
    results['PF-10-Q03'] = r8

    supply = defaultdict(lambda: [0, 0])
    for outlet_id, outlet in outlets.items():
        s = supply[outlet_area[outlet_id]]
        s[0] += 1
        s[1] += outlet['outlet_status'] == 'OPEN'
    r9 = []
    for a, area in areas.items():
        total, open_outlets = supply.get(a, (0, 0))
        demand = october_demand.get(a)
        per_outlet = ratio(demand, open_outlets, 2) if demand is not None else None
        r9.append({'city': area['city'], 'area_name': area['area_name'],
                   'area_status': area['area_status'], 'total_outlets': total,
                   'open_outlets': open_outlets, 'food_orders': demand or 0,
                   'orders_per_open_outlet': as_float(per_outlet)})
    results['PF-09-Q02'] = sorted(r9, key=lambda r: (r['orders_per_open_outlet'] is None,
                                                     -(r['orders_per_open_outlet'] or 0),
                                                     r['city'], r['area_name']))

    # -- operational reads ----------------------------------------------------------------
    tebet = [a for a, r in areas.items()
             if r['area_name'] == R10_AREA and r['city'] == R10_CITY and r['area_status'] == 'ACTIVE']
    r10 = []
    for outlet_id, outlet in outlets.items():
        if tebet and outlet_area[outlet_id] == tebet[0] and outlet['outlet_status'] == 'OPEN':
            loc = location_details[outlet['location_id']]
            r10.append({'merchant_outlet_id': outlet_id, 'outlet_name': outlet['outlet_name'],
                        'address': loc['address'], 'latitude': float(loc['latitude']),
                        'longitude': float(loc['longitude'])})
    r10.sort(key=lambda r: (collation_key(r['outlet_name']), r['merchant_outlet_id']))
    results['PF-05-Q01'] = r10[:20]
    params['PF-05-Q01'] = {'area_name': R10_AREA, 'city': R10_CITY, 'limit': 20}

    busiest, _ = max(per_customer.items(), key=lambda item: (item[1], item[0]))
    customer_n = num(busiest)
    history = [(created[n], f'ORD{n:07d}') for n in range(1, len(order_customer.data))
               if order_customer[n] == customer_n and order_status[n] >= 0]
    history.sort(reverse=True)
    results['PF-01-Q02'] = [{'order_id': o, 'created_at': datetime.fromtimestamp(c, UTC)
                             .strftime('%Y-%m-%dT%H:%M:%SZ'),
                             'order_status': ORDER_STATUS[order_status[num(o)]]}
                            for c, o in history[:20]]
    params['PF-01-Q02'] = {'customer_id': busiest, 'limit': 20}
    results['PF-01-Q01'] = {table: rows_ for table, rows_ in sample.items()}
    params['PF-01-Q01'] = {'order_id': sample_order}
    params['PF-03-Q01'] = {'trip_id': sample_trip}
    params['PF-09-Q02'] = {'start_day': R9_START, 'end_day': R9_END}
    return {'params': params, 'results': results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Compute expected results from the canonical CSV.')
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument('--output', type=Path, default=DEFAULT_REFERENCE_FILE)
    args = parser.parse_args(argv)
    started = time.perf_counter()
    reference = compute(args.data_dir)
    manifest = args.data_dir / 'manifest.json'
    reference['meta'] = {
        'generated_at_utc': datetime.now(UTC).isoformat(),
        'data_dir': str(args.data_dir),
        'seconds': round(time.perf_counter() - started, 1),
        'manifest_sha256': ({t: v['sha256'] for t, v in json.loads(manifest.read_text())['tables'].items()}
                            if manifest.is_file() else None),
        'row_counts': {k: (len(v) if isinstance(v, list) else 1)
                       for k, v in reference['results'].items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(reference, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'[DONE] {args.output} in {reference["meta"]["seconds"]}s')
    for op, count in sorted(reference['meta']['row_counts'].items()):
        print(f'  {op}: {count} rows')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
