"""CouchDB benchmark adapter: the 25 essential operations of couchdb/queries.py.

    uv run --no-sync python -m benchmarks.run --adapter benchmarks.adapters.couchdb \
        --dataset-size 2000000

Prerequisites: the canonical dataset is loaded (python -m couchdb.load) and the expected
results exist (python -m couchdb.reference).

* Reads and analytics: execute() runs the query; validate() compares the full result with
  the expected result computed from the canonical CSV (couchdb/reference.py).
* Writes: setup() purges leftovers and creates the state the operation starts from (with
  the admin user); execute() runs the operation as the application user; validate() reads
  the documents back and checks the new state; teardown() purges every document the
  scenario created and restores documents it changed, so each run starts from an
  equivalent state. IDs follow Part 2 (ORD9999001..., SAR9999101...).

Only execute() is timed by the runner.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from couchdb import validation
from couchdb.client import CouchClient, doc_path
from couchdb.queries import OPERATIONS, GrabCouch
from couchdb.settings import DEFAULT_REFERENCE_FILE, get_settings

_state: dict = {}


def env() -> SimpleNamespace:
    """Clients are created on first use so importing the adapter needs no running server."""
    if not _state:
        settings = get_settings()
        admin = CouchClient.admin(settings, timeout=None)
        app = CouchClient.app(settings, timeout=None)
        _state.update(settings=settings, admin=admin, app=app,
                      ops=GrabCouch(app, settings), setup_ops=GrabCouch(admin, settings))
    return SimpleNamespace(**_state)


def reference() -> dict:
    path = Path(os.getenv('COUCHDB_REFERENCE_FILE', DEFAULT_REFERENCE_FILE))
    return validation.load_reference(path)


def ts(minutes_ago: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')


def purge(db: str, ids) -> None:
    """Remove documents and their whole revision history, restoring the initial state."""
    admin = env().admin
    revs = {}
    for doc_id in ids:
        response = admin.request('GET', doc_path(db, doc_id), params={'open_revs': 'all'},
                                 ok=(200, 404))
        if response.status_code == 200:
            leaves = [entry['ok']['_rev'] for entry in response.json() if 'ok' in entry]
            if leaves:
                revs[doc_id] = leaves
    if revs:
        admin.purge(db, revs)


def restore(db: str, original: dict) -> None:
    """Put a changed document back to its original content (it gets a new revision)."""
    admin = env().admin
    current = admin.get_doc(db, original['_id'])
    strip = lambda d: {k: v for k, v in d.items() if k != '_rev'}  # noqa: E731
    if strip(current) != strip(original):
        admin.put_doc(db, dict(strip(original), _rev=current['_rev']))


def doc(db_name: str, doc_id: str) -> dict | None:
    e = env()
    return e.admin.get_doc(e.settings.db(db_name), doc_id, missing_ok=True)


# -- fixtures: documents in the state an operation starts from -------------------------

W6_AREA = {'service_area_id': 'SAR9999101', 'area_name': 'Cicendo', 'city': 'Bandung',
           'area_status': 'ACTIVE'}
W6_LOCATIONS = [
    {'location_id': 'LOC9999101', 'latitude': -6.905200, 'longitude': 107.596400,
     'address': 'Jl. Pajajaran No. 12, Cicendo, Bandung'},
    {'location_id': 'LOC9999102', 'latitude': -6.911800, 'longitude': 107.601700,
     'address': 'Jl. Kebon Kawung No. 5, Cicendo, Bandung'},
    {'location_id': 'LOC9999103', 'latitude': -6.899600, 'longitude': 107.589300,
     'address': 'Jl. Pasirkaliki No. 88, Cicendo, Bandung'},
]
W6_OUTLETS = [
    {'merchant_outlet_id': 'OUT9999101', 'outlet_name': 'Warung Bu Sri', 'outlet_status': 'OPEN',
     'location_id': 'LOC9999101'},
    {'merchant_outlet_id': 'OUT9999102', 'outlet_name': 'Kedai Kopi Pak Dadang',
     'outlet_status': 'OPEN', 'location_id': 'LOC9999103'},
]
W6_IDS = ['SAR9999101', *(l['location_id'] for l in W6_LOCATIONS),
          *(o['merchant_outlet_id'] for o in W6_OUTLETS)]


def food_order(order_id: str, outlet_id: str, area_id: str, status: str) -> dict:
    """A food order at a test outlet (used to make an outlet 'referenced' or 'running')."""
    return {'_id': order_id, 'type': 'service_order', 'order_id': order_id,
            'customer_id': 'CUS0000001', 'created_at': ts(30), 'order_status': status,
            'total_amount': 35000.0, 'service_type': 'FOOD',
            'food': {'preparation_status': 'READY', 'delivery_notes': None,
                     'merchant_outlet_id': outlet_id, 'merchant_service_area_id': area_id},
            'payment': {'payment_id': 'PAY' + order_id[3:], 'payment_method': 'E_WALLET',
                        'payment_status': 'PAID', 'amount': 35000.0, 'paid_at': ts(29)},
            'trips': []}


def test_outlet(outlet_id: str) -> dict:
    """An INACTIVE (delisted) outlet at an existing location."""
    location = doc('geo', 'LOC0000001')
    return {'_id': outlet_id, 'type': 'merchant_outlet', 'merchant_outlet_id': outlet_id,
            'outlet_name': f'Outlet Uji {outlet_id}', 'outlet_status': 'INACTIVE',
            'location': {k: location[k] for k in ('location_id', 'address', 'latitude',
                                                  'longitude', 'service_area_id')}}


# -- scenario building ---------------------------------------------------------------

def scenario(operation_id: str, setup, execute, validate, teardown=lambda ctx: None,
             scenario_name: str | None = None) -> SimpleNamespace:
    meta = next(op for op in OPERATIONS if op.operation_id == operation_id)
    return SimpleNamespace(operation_id=operation_id, requirement_ids=list(meta.requirement_ids),
                           dbms='couchdb', operation=meta.name,
                           scenario=scenario_name or meta.kind,
                           setup=setup, execute=execute, validate=validate, teardown=teardown)


def read_scenario(operation_id: str, call) -> SimpleNamespace:
    def setup():
        ref = reference()
        return {'params': ref['params'].get(operation_id, {}),
                'expected': ref['results'][operation_id]}

    def validate(ctx, result):
        passed, message = validation.compare(operation_id, result, ctx['expected'])
        if not passed:
            print(f'    [{operation_id}] {message}', file=sys.stderr, flush=True)
        return passed

    return scenario(operation_id, setup, lambda ctx: call(env().ops, **ctx['params']), validate)


def order_scenario(operation_id: str, ids: list[str], prepare, execute, check,
                   extra_teardown=None) -> SimpleNamespace:
    """A write on order documents `ids`: purge, prepare as admin, run, check, purge."""
    def setup():
        e = env()
        purge(e.settings.db('orders'), ids)
        ctx = {'ids': ids}
        prepare(e.setup_ops, ctx)
        return ctx

    def teardown(ctx):
        purge(env().settings.db('orders'), ids)
        if extra_teardown:
            extra_teardown(ctx)

    return scenario(operation_id, setup, lambda ctx: execute(env().ops, ctx),
                    lambda ctx, result: bool(check(ctx, result)), teardown)


def w1_state(g: GrabCouch, ctx: dict) -> None:
    g.create_confirmed_order_with_trip('ORD9999001', 'PAY9999001', 'TRP9999001')


def w2_state(g: GrabCouch, ctx: dict) -> None:
    w1_state(g, ctx)
    g.dispatch_offer('ORD9999001', 'TRP9999001', 'ASN9999001')


def w3_state(g: GrabCouch, ctx: dict) -> None:
    w2_state(g, ctx)
    g.accept_offer_and_start_trip('ORD9999001', 'ASN9999001')
    trip = doc('orders', 'ORD9999001')['trips'][0]
    driver_id = trip['assignments'][0]['driver_partner_id']
    ctx['driver'] = doc('fleet', driver_id)


def w7_state(g: GrabCouch, ctx: dict) -> None:
    g.checkout_non_cash_with_retry('ORD9999101', 'PAY9999101', 'TRP9999101')


def w8_state(g: GrabCouch, ctx: dict) -> None:
    w7_state(g, ctx)
    g.dispatch_offer('ORD9999101', 'TRP9999101', 'ASN9999101', vehicle_type='Car')
    ctx['paid_at'] = doc('orders', 'ORD9999101')['payment']['paid_at']


def w9_state(g: GrabCouch, ctx: dict) -> None:
    location = doc('geo', 'LOC0000001')
    order = {'_id': 'ORD9999102', 'type': 'service_order', 'order_id': 'ORD9999102',
             'customer_id': 'CUS0000001', 'created_at': ts(30), 'order_status': 'IN_PROGRESS',
             'total_amount': 18000.0, 'service_type': 'MOBILITY',
             'mobility': {'ride_type': 'GrabBike', 'passenger_count': 1, 'pickup_notes': None},
             'payment': {'payment_id': 'PAY9999102', 'payment_method': 'CASH',
                         'payment_status': 'PENDING', 'amount': 18000.0, 'paid_at': None},
             'trips': [{'trip_id': 'TRP9999102', 'trip_status': 'IN_PROGRESS',
                        'started_at': ts(20), 'completed_at': None,
                        'start_location_id': 'LOC0000001', 'end_location_id': 'LOC0000002',
                        'start_service_area_id': location['service_area_id'],
                        'assignments': []}]}
    g.c.put_doc(g.orders, order)


def restore_driver(ctx: dict) -> None:
    if ctx.get('driver'):
        restore(env().settings.db('fleet'), ctx['driver'])


def first_trip(order_id: str) -> dict:
    return doc('orders', order_id)['trips'][0]


def check_w1(ctx, result):
    d = doc('orders', 'ORD9999001')
    trip = d['trips'][0]
    return (d['order_status'] == 'CONFIRMED' and d['payment']['payment_status'] == 'PENDING'
            and d['payment']['payment_method'] == 'CASH' and trip['trip_status'] == 'SEARCHING'
            and trip['start_service_area_id'] == doc('geo', 'LOC0000001')['service_area_id'])


def check_w2(ctx, result):
    trip = first_trip('ORD9999001')
    if trip['trip_status'] != 'SEARCHING' or len(trip['assignments']) != 1:
        return False
    a = trip['assignments'][0]
    driver = doc('fleet', a['driver_partner_id'])
    vehicle = next(v for v in driver['vehicles'] if v['vehicle_id'] == a['vehicle_id'])
    return (a['assignment_status'] == 'PENDING' and driver['driver_status'] == 'ACTIVE'
            and vehicle['vehicle_status'] == 'ACTIVE' and vehicle['vehicle_type'] == a['vehicle_type'])


def check_w3(ctx, result):
    trip = first_trip('ORD9999001')
    a = trip['assignments'][0]
    return (a['assignment_status'] == 'ACCEPTED' and a['responded_at'] is not None
            and trip['trip_status'] == 'IN_PROGRESS' and trip['started_at'] is not None)


def check_w4(ctx, result):
    trip = first_trip('ORD9999002')
    a = trip['assignments'][0]
    return (a['assignment_status'] == 'REJECTED' and a['end_reason'] == 'Too far from pickup location'
            and a['ended_at'] is not None and trip['trip_status'] == 'SEARCHING')


def check_w5(ctx, result):
    trip = first_trip('ORD9999001')
    a = trip['assignments'][0]
    return (trip['trip_status'] == 'COMPLETED' and trip['completed_at'] >= trip['started_at']
            and a['ended_at'] == trip['completed_at']
            and doc('fleet', a['driver_partner_id'])['driver_status'] == 'ACTIVE')


def check_w7(ctx, result):
    d = doc('orders', 'ORD9999101')
    return (d['order_status'] == 'CONFIRMED' and d['payment']['payment_status'] == 'PAID'
            and d['payment']['paid_at'] is not None and d['trips'][0]['trip_status'] == 'SEARCHING'
            and d['_rev'].startswith('4-'))  # created, FAILED, PENDING, PAID: four revisions


def check_w8(ctx, result):
    d = doc('orders', 'ORD9999101')
    trip = d['trips'][0]
    return (result['cancelled'] and d['order_status'] == 'CANCELLED'
            and trip['trip_status'] == 'CANCELLED'
            and all(a['assignment_status'] == 'CANCELLED' and a['end_reason'] for a in trip['assignments'])
            and d['payment']['payment_status'] == 'REFUNDED'
            and d['payment']['paid_at'] == ctx['paid_at'])


def check_w9(ctx, result):
    d = doc('orders', 'ORD9999102')
    trip = d['trips'][0]
    return (d['order_status'] == 'COMPLETED' and trip['trip_status'] == 'COMPLETED'
            and d['payment']['payment_status'] == 'PAID'
            and d['payment']['paid_at'] == trip['completed_at'])


# -- geo scenarios -------------------------------------------------------------------

def geo_scenario(operation_id: str, geo_ids: list[str], order_ids: list[str], prepare,
                 execute, check) -> SimpleNamespace:
    def setup():
        e = env()
        purge(e.settings.db('geo'), geo_ids)
        purge(e.settings.db('orders'), order_ids)
        ctx = {}
        prepare(e.setup_ops, ctx)
        return ctx

    def teardown(ctx):
        e = env()
        purge(e.settings.db('geo'), geo_ids)
        purge(e.settings.db('orders'), order_ids)

    return scenario(operation_id, setup, lambda ctx: execute(env().ops, ctx),
                    lambda ctx, result: bool(check(ctx, result)), teardown)


def w6_execute(g: GrabCouch, ctx: dict):
    return g.onboard_service_area(W6_AREA, W6_LOCATIONS, W6_OUTLETS)


def check_w6(ctx, result):
    e = env()
    geo = e.settings.db('geo')
    count = e.admin.view(geo, 'geo', 'locations_by_area', key='SAR9999101')['rows']
    outlets = [doc('geo', o['merchant_outlet_id']) for o in W6_OUTLETS]
    return (doc('geo', 'SAR9999101')['area_status'] == 'ACTIVE'
            and count and count[0]['value'] == len(W6_LOCATIONS)
            and all(o and o['location']['service_area_id'] == 'SAR9999101' for o in outlets))


def w10_state(g: GrabCouch, ctx: dict) -> None:
    w6_execute(g, ctx)
    # OUT9999102 still has a running food order, so it may only be CLOSED.
    g.c.put_doc(g.orders, food_order('ORD9999103', 'OUT9999102', 'SAR9999101', 'IN_PROGRESS'))


def check_w10(ctx, result):
    return (doc('geo', 'SAR9999101')['area_status'] == 'INACTIVE'
            and doc('geo', 'OUT9999101')['outlet_status'] == 'INACTIVE'
            and doc('geo', 'OUT9999102')['outlet_status'] == 'CLOSED')


def pf07_free_state(g: GrabCouch, ctx: dict) -> None:
    g.c.put_doc(g.geo, test_outlet('OUT9999201'))


def pf07_referenced_state(g: GrabCouch, ctx: dict) -> None:
    outlet = test_outlet('OUT9999202')
    g.c.put_doc(g.geo, outlet)
    g.c.put_doc(g.orders, food_order('ORD9999202', 'OUT9999202',
                                     outlet['location']['service_area_id'], 'COMPLETED'))


def create_scenarios():
    return [
        # Operational reads
        read_scenario('PF-01-Q01', GrabCouch.get_order),
        read_scenario('PF-01-Q02', GrabCouch.customer_order_history),
        read_scenario('PF-03-Q01', GrabCouch.trip_status),
        read_scenario('PF-05-Q01', GrabCouch.open_outlets_in_area),
        # Trip lifecycle (W1-W5)
        order_scenario('PF-01-Q03', ['ORD9999001'], lambda g, ctx: None,
                       lambda g, ctx: g.create_confirmed_order_with_trip(
                           'ORD9999001', 'PAY9999001', 'TRP9999001'), check_w1),
        order_scenario('PF-02-Q01', ['ORD9999001'], w1_state,
                       lambda g, ctx: g.dispatch_offer('ORD9999001', 'TRP9999001', 'ASN9999001'),
                       check_w2),
        order_scenario('PF-02-Q02', ['ORD9999001'], w2_state,
                       lambda g, ctx: g.accept_offer_and_start_trip('ORD9999001', 'ASN9999001'),
                       check_w3),
        order_scenario('PF-02-Q03', ['ORD9999002'],
                       lambda g, ctx: (g.create_confirmed_order_with_trip(
                           'ORD9999002', 'PAY9999002', 'TRP9999002'),
                           g.dispatch_offer('ORD9999002', 'TRP9999002', 'ASN9999002')),
                       lambda g, ctx: g.reject_offer('ORD9999002', 'ASN9999002'), check_w4),
        order_scenario('PF-03-Q02', ['ORD9999001'], w3_state,
                       lambda g, ctx: g.complete_trip_and_release_driver('ORD9999001', 'TRP9999001'),
                       check_w5, extra_teardown=restore_driver),
        # Payments (W7-W9)
        order_scenario('PF-04-Q01', ['ORD9999101'], lambda g, ctx: None,
                       lambda g, ctx: g.checkout_non_cash_with_retry(
                           'ORD9999101', 'PAY9999101', 'TRP9999101'), check_w7),
        order_scenario('PF-04-Q02', ['ORD9999101'], w8_state,
                       lambda g, ctx: g.cancel_before_pickup('ORD9999101'), check_w8),
        order_scenario('PF-04-Q03', ['ORD9999102'], w9_state,
                       lambda g, ctx: g.complete_cash_ride('ORD9999102', 'TRP9999102'), check_w9),
        # Service areas and outlets (W6, W10, deletion)
        geo_scenario('PF-06-Q01', W6_IDS, [], lambda g, ctx: None, w6_execute, check_w6),
        geo_scenario('PF-06-Q02', W6_IDS, ['ORD9999103'], w10_state,
                     lambda g, ctx: g.deactivate_service_area('SAR9999101'), check_w10),
        geo_scenario('PF-07-Q01', ['OUT9999201'], [], pf07_free_state,
                     lambda g, ctx: g.delete_outlet('OUT9999201'),
                     lambda ctx, r: r['deleted'] and doc('geo', 'OUT9999201') is None),
        geo_scenario('PF-07-Q02', ['OUT9999202'], ['ORD9999202'], pf07_referenced_state,
                     lambda g, ctx: g.delete_outlet('OUT9999202'),
                     lambda ctx, r: (not r['deleted'] and r['food_orders'] == 1
                                     and doc('geo', 'OUT9999202') is not None)),
        # Analytics (R1-R9)
        read_scenario('PF-02-Q04', GrabCouch.reassignment_history),
        read_scenario('PF-08-Q01', GrabCouch.driver_acceptance_rates),
        read_scenario('PF-08-Q02', GrabCouch.top_drivers_by_duration),
        read_scenario('PF-08-Q03', GrabCouch.vehicle_type_hourly_utilization),
        read_scenario('PF-09-Q01', GrabCouch.trips_per_service_area),
        read_scenario('PF-09-Q02', GrabCouch.outlet_supply_vs_food_demand),
        read_scenario('PF-10-Q01', GrabCouch.revenue_and_refunds_per_area),
        read_scenario('PF-10-Q02', GrabCouch.monthly_payment_summary),
        read_scenario('PF-10-Q03', GrabCouch.top_outlets_by_food_revenue),
    ]
