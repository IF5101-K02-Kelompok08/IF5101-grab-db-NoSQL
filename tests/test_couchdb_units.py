"""Unit tests of the CouchDB transformation helpers (no server needed).

    uv run --no-sync python -m pytest tests/test_couchdb_units.py -v
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from couchdb import design, model
from couchdb.client import iter_rows
from couchdb.rounding import percent, ratio
from couchdb.transform import Cursor


def test_rounding_matches_postgresql_half_away_from_zero():
    assert ratio(1, 8, 2) == Decimal('0.13')          # 0.125 -> 0.13 (not banker's 0.12)
    assert percent(1, 32) == Decimal('3.13')           # 3.125
    assert ratio(2, 3, 2) == Decimal('0.67')
    assert percent(5, 0) is None                       # NULLIF(..., 0)


def test_timestamps_round_trip():
    assert model.timestamp('2026-05-01 00:00:02+00:00') == '2026-05-01T00:00:02Z'
    assert model.csv_timestamp('2026-05-01T00:00:02Z') == '2026-05-01 00:00:02+00:00'
    assert model.timestamp('') is None


def test_cursor_takes_rows_of_one_key_and_detects_orphans():
    rows = iter([{'k': 'A'}, {'k': 'A'}, {'k': 'C'}])
    cursor = Cursor(rows, 'k', 't')
    assert len(cursor.take('A')) == 2
    assert cursor.take('B') == []
    with pytest.raises(ValueError):
        cursor.take('D')                               # 'C' was skipped: no parent row


def test_order_document_round_trip():
    lookups = model.Lookups({'LOC0000001': 'SAR0000007', 'LOC0000002': 'SAR0000007'},
                            {}, {'VEH0000001': ('DRV0000001', 'Car')})
    order = {'order_id': 'ORD0000001', 'created_at': '2026-05-01 00:00:02+00:00',
             'order_status': 'COMPLETED', 'total_amount': '15000.00', 'customer_id': 'CUS0000001'}
    mobility = {'order_id': 'ORD0000001', 'ride_type': 'GrabCar', 'passenger_count': '2',
                'pickup_notes': ''}
    payment = {'payment_id': 'PAY0000001', 'payment_method': 'CASH', 'payment_status': 'PAID',
               'amount': '15000.00', 'paid_at': '2026-05-01 00:20:00+00:00',
               'order_id': 'ORD0000001'}
    trip = {'trip_id': 'TRP0000001', 'started_at': '2026-05-01 00:05:00+00:00',
            'completed_at': '2026-05-01 00:20:00+00:00', 'trip_status': 'COMPLETED',
            'order_id': 'ORD0000001', 'start_location_id': 'LOC0000001',
            'end_location_id': 'LOC0000002'}
    assignment = {'assignment_id': 'ASN0000001', 'assigned_at': '2026-05-01 00:00:30+00:00',
                  'assignment_status': 'ACCEPTED', 'responded_at': '2026-05-01 00:00:40+00:00',
                  'ended_at': '2026-05-01 00:20:00+00:00', 'end_reason': '',
                  'trip_id': 'TRP0000001', 'vehicle_id': 'VEH0000001'}
    doc = model.order_doc(order, 'MOBILITY', mobility, payment, [(trip, [assignment])], lookups)
    assert doc['trips'][0]['start_service_area_id'] == 'SAR0000007'
    assert doc['trips'][0]['assignments'][0]['driver_partner_id'] == 'DRV0000001'
    rows = {t: r for t, r in model.order_rows(doc).items() if r}
    assert rows == {'service_orders': [order], 'mobility_orders': [mobility],
                    'payments': [payment], 'trips': [trip], 'driver_assignments': [assignment]}


def test_streamed_view_rows_survive_chunk_boundaries():
    body = '{"total_rows":3,"offset":0,"rows":[\r\n{"id":"a","key":"é","value":1},\r\n' \
           '{"id":"b","key":["x",{}],"value":[1,2]},\r\n{"id":"c","key":null,"value":null}\r\n]}'
    data = body.encode('utf-8')
    chunks = [data[i:i + 7] for i in range(0, len(data), 7)]  # splits the 2-byte 'é'
    assert [row['id'] for row in iter_rows(chunks)] == ['a', 'b', 'c']


def test_design_documents_are_well_formed():
    for db in ('orders', 'fleet', 'customers', 'geo'):
        assert design.validation_ddoc(db)['validate_doc_update'].startswith('function')
        for ddoc in design.view_ddocs(db):
            for view in ddoc['views'].values():
                assert view['map'].startswith('function')
                assert view.get('reduce', '_count') in ('_count', '_sum', '_stats')
