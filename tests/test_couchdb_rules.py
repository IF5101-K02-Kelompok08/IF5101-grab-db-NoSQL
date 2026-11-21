"""Functional tests of the integrity rules enforced by CouchDB (PNF-04).

Each rule ported from the Part 2 DDL (CHECK constraints, status-transition triggers,
specialization trigger, ID immutability) is checked with one valid and one invalid write.
The tests run against a live CouchDB (settings from .env) in temporary databases with a
random prefix, which are deleted afterwards. They are skipped when CouchDB is unreachable.

    uv run --no-sync python -m pytest tests/test_couchdb_rules.py -v
"""

from __future__ import annotations

import copy
import dataclasses
import uuid

import pytest

from couchdb import design
from couchdb.client import CouchClient, CouchError
from couchdb.settings import APP_ROLE, DATABASES, get_settings


def _reachable() -> bool:
    try:
        settings = get_settings()
        CouchClient.admin(settings, timeout=5).request('GET', '/_up')
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason='CouchDB is not reachable')


@pytest.fixture(scope='module')
def env():
    base = get_settings()
    token = uuid.uuid4().hex[:8]
    user, password = f'grabtest_{token}', uuid.uuid4().hex
    settings = dataclasses.replace(base, db_prefix=f'grabtest_{token}', app_user=user,
                                   app_password=password)
    admin = CouchClient.admin(settings)
    for name in DATABASES:
        db = settings.db(name)
        admin.create_db(db)
        admin.request('PUT', f'/{db}/_security', body={
            'admins': {'names': [], 'roles': []}, 'members': {'names': [], 'roles': [APP_ROLE]}})
        admin.put_doc(db, design.validation_ddoc(name))
    user_doc = {'_id': f'org.couchdb.user:{user}', 'name': user, 'type': 'user',
                'roles': [APP_ROLE], 'password': password}
    admin.put_doc('_users', user_doc)
    app = CouchClient.app(settings)
    yield settings, admin, app
    for name in DATABASES:
        admin.delete_db(settings.db(name))
    admin.delete_doc('_users', user_doc['_id'], user_doc['_rev'])


def order(order_id: str = 'ORD8000001') -> dict:
    return {
        '_id': order_id, 'type': 'service_order', 'order_id': order_id,
        'customer_id': 'CUS0000001', 'created_at': '2026-10-01T10:00:00Z',
        'order_status': 'CONFIRMED', 'total_amount': 25000.0, 'service_type': 'MOBILITY',
        'mobility': {'ride_type': 'GrabCar', 'passenger_count': 2, 'pickup_notes': None},
        'payment': {'payment_id': 'PAY' + order_id[3:], 'payment_method': 'E_WALLET',
                    'payment_status': 'PAID', 'amount': 25000.0, 'paid_at': '2026-10-01T10:00:30Z'},
        'trips': [{
            'trip_id': 'TRP' + order_id[3:], 'trip_status': 'ALLOCATED', 'started_at': None,
            'completed_at': None, 'start_location_id': 'LOC0000001',
            'end_location_id': 'LOC0000002', 'start_service_area_id': 'SAR0000001',
            'assignments': [{
                'assignment_id': 'ASN' + order_id[3:], 'assignment_status': 'ACCEPTED',
                'assigned_at': '2026-10-01T10:00:40Z', 'responded_at': '2026-10-01T10:00:50Z',
                'ended_at': None, 'end_reason': None, 'vehicle_id': 'VEH0000001',
                'driver_partner_id': 'DRV0000001', 'vehicle_type': 'Car'}]}],
    }


_counter = iter(range(8000100, 8999999))


def fresh_order(**changes) -> dict:
    doc = order(f'ORD{next(_counter):07d}')
    for path, value in changes.items():
        target = doc
        *parents, last = path.split('.')
        for part in parents:
            target = target[int(part)] if part.isdigit() else target[part]
        if value is DELETE:
            del target[last]
        else:
            target[last] = value
    return doc


DELETE = object()


def rejected(client: CouchClient, db: str, doc: dict) -> str:
    with pytest.raises(CouchError) as info:
        client.put_doc(db, copy.deepcopy(doc))
    assert info.value.status == 403, info.value
    return info.value.reason


def stored(client: CouchClient, db: str, doc: dict) -> dict:
    doc = copy.deepcopy(doc)
    client.put_doc(db, doc)
    return doc


# -- service orders ---------------------------------------------------------------------

def test_valid_order_is_accepted(env):
    settings, admin, app = env
    doc = stored(app, settings.db('orders'), fresh_order())
    assert doc['_rev'].startswith('1-')


@pytest.mark.parametrize('changes, message', [
    ({'order_id': 'ORD123'}, 'order_id'),
    ({'order_status': 'DONE'}, 'order_status'),
    ({'total_amount': -1}, 'total_amount'),
    ({'created_at': '2026-10-01 10:00:00'}, 'created_at'),
    ({'payment.amount': 0}, 'payment.amount'),
    ({'payment.paid_at': None}, 'paid_at'),
    ({'payment.payment_method': 'BITCOIN'}, 'payment_method'),
    ({'mobility.passenger_count': 0}, 'passenger_count'),
    ({'trips.0.trip_status': 'COMPLETED'}, 'completed_at'),
    ({'trips.0.started_at': '2026-10-01T10:30:00Z', 'trips.0.completed_at': '2026-10-01T10:10:00Z'},
     'earlier'),
    ({'trips.0.assignments.0.vehicle_id': 'CAR1'}, 'vehicle_id'),
])
def test_check_constraints(env, changes, message):
    settings, admin, app = env
    assert message in rejected(app, settings.db('orders'), fresh_order(**changes))


def test_specialization_is_total_and_disjoint(env):
    settings, admin, app = env
    db = settings.db('orders')
    both = fresh_order(**{'food': {'preparation_status': 'PENDING', 'delivery_notes': None,
                                   'merchant_outlet_id': 'OUT0000001',
                                   'merchant_service_area_id': 'SAR0000001'}})
    assert 'exactly one subtype' in rejected(app, db, both)
    assert 'exactly one subtype' in rejected(app, db, fresh_order(mobility=DELETE))
    wrong = fresh_order(service_type='DELIVERY')
    assert 'exactly one subtype' in rejected(app, db, wrong)


def test_status_transitions(env):
    settings, admin, app = env
    db = settings.db('orders')
    doc = stored(app, db, fresh_order())
    doc['order_status'] = 'PENDING'                       # CONFIRMED -> PENDING
    assert 'order_status transition' in rejected(app, db, doc)
    doc['order_status'] = 'CONFIRMED'
    doc['trips'][0]['trip_status'] = 'SEARCHING'          # ALLOCATED -> SEARCHING
    assert 'trip_status transition' in rejected(app, db, doc)
    doc['trips'][0]['trip_status'] = 'IN_PROGRESS'        # ALLOCATED -> IN_PROGRESS
    doc['trips'][0]['started_at'] = '2026-10-01T10:05:00Z'
    doc['order_status'] = 'IN_PROGRESS'
    app.put_doc(db, doc)
    doc['payment']['payment_status'] = 'PENDING'          # PAID -> PENDING
    doc['payment']['paid_at'] = None
    assert 'payment_status transition' in rejected(app, db, doc)


def test_refund_keeps_paid_at(env):
    settings, admin, app = env
    db = settings.db('orders')
    doc = stored(app, db, fresh_order())
    doc['payment'].update(payment_status='REFUNDED', paid_at='2026-10-02T00:00:00Z')
    assert 'original paid_at' in rejected(app, db, doc)
    doc['payment']['paid_at'] = '2026-10-01T10:00:30Z'
    app.put_doc(db, doc)


def test_identifiers_and_history_cannot_change(env):
    settings, admin, app = env
    db = settings.db('orders')
    doc = stored(app, db, fresh_order())
    renamed = copy.deepcopy(doc)
    renamed['payment']['payment_id'] = 'PAY7777777'
    assert 'payment_id cannot be changed' in rejected(app, db, renamed)
    no_trip = copy.deepcopy(doc)
    no_trip['trips'] = []
    assert 'cannot be removed' in rejected(app, db, no_trip)
    no_assignment = copy.deepcopy(doc)
    no_assignment['trips'][0]['assignments'] = []
    assert 'cannot be removed' in rejected(app, db, no_assignment)


def test_application_cannot_delete_orders(env):
    settings, admin, app = env
    db = settings.db('orders')
    doc = stored(app, db, fresh_order())
    with pytest.raises(CouchError) as info:
        app.delete_doc(db, doc['_id'], doc['_rev'])
    assert info.value.status == 403


def test_concurrent_update_conflicts(env):
    """Optimistic concurrency: a write based on an old revision is refused (409)."""
    settings, admin, app = env
    db = settings.db('orders')
    first = stored(app, db, fresh_order())
    second = copy.deepcopy(first)
    first['trips'][0]['trip_status'] = 'CANCELLED'
    app.put_doc(db, first)
    second['trips'][0]['trip_status'] = 'IN_PROGRESS'
    second['trips'][0]['started_at'] = '2026-10-01T10:05:00Z'
    with pytest.raises(CouchError) as info:
        app.put_doc(db, second)
    assert info.value.status == 409


# -- other databases --------------------------------------------------------------------

def test_geo_rules(env):
    settings, admin, app = env
    db = settings.db('geo')
    location = {'_id': 'LOC8000001', 'type': 'location', 'location_id': 'LOC8000001',
                'latitude': -6.2, 'longitude': 106.8, 'address': 'Jl. Uji No. 1',
                'service_area_id': 'SAR0000001'}
    stored(app, db, location)
    assert 'latitude' in rejected(app, db, dict(location, _id='LOC8000002',
                                                location_id='LOC8000002', latitude=91.0))
    outlet = {'_id': 'OUT8000001', 'type': 'merchant_outlet', 'merchant_outlet_id': 'OUT8000001',
              'outlet_name': 'Warung Uji', 'outlet_status': 'OPENED',
              'location': {k: location[k] for k in ('location_id', 'address', 'latitude',
                                                    'longitude', 'service_area_id')}}
    assert 'outlet_status' in rejected(app, db, outlet)


def test_customer_and_fleet_rules(env):
    settings, admin, app = env
    customers, fleet = settings.db('customers'), settings.db('fleet')
    customer = {'_id': 'CUS8000001', 'type': 'customer', 'customer_id': 'CUS8000001',
                'full_name': 'Pelanggan Uji', 'phone_number': None, 'email': None}
    stored(app, customers, customer)
    assert 'email' in rejected(app, customers, dict(customer, _id='CUS8000002',
                                                    customer_id='CUS8000002', email='bukan-email'))
    driver = {'_id': 'DRV8000001', 'type': 'driver_partner', 'driver_partner_id': 'DRV8000001',
              'full_name': 'Mitra Uji', 'phone_number': '+6281200000001',
              'email': 'mitra.uji@example.com', 'driver_status': 'ACTIVE',
              'vehicles': [{'vehicle_id': 'VEH8000001', 'plate_number': 'D 1 UJI',
                            'vehicle_type': 'Motorcycle', 'vehicle_status': 'ACTIVE'}]}
    driver = stored(app, fleet, driver)
    driver['vehicles'] = []
    assert 'cannot be removed' in rejected(app, fleet, driver)
