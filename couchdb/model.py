"""Document model: how Part 2 rows become CouchDB documents, and back.

Four databases, one per aggregate:

* grab_orders     one document per service order. It embeds the order's subtype (food,
                  mobility or delivery), its payment, its trips and each trip's driver
                  assignments, so an order's whole lifecycle changes in one atomic write.
* grab_fleet      one document per driver partner, embedding the driver's vehicles.
* grab_customers  one document per customer.
* grab_geo        service areas, locations and merchant outlets (outlets embed a copy of
                  their location).

Every document uses the Part 2 identifier as its _id (ORD0000001, DRV0000001, ...).

Denormalised copies (values that never change once written):
  trip.start_service_area_id      area of the pickup location       (R5, R6)
  assignment.driver_partner_id    driver that owns the vehicle      (R1, R2, W5)
  assignment.vehicle_type         type of the vehicle               (R3)
  food.merchant_service_area_id   area of the outlet's location     (R9)
  outlet.location                 address/coordinates/area of outlet (R10)

The *_rows functions do the reverse mapping. verify_load uses them to show that a document
holds exactly the original rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

SUBTYPE_FIELD = {'FOOD': 'food', 'MOBILITY': 'mobility', 'DELIVERY': 'delivery'}
SUBTYPE_TABLE = {'FOOD': 'food_orders', 'MOBILITY': 'mobility_orders', 'DELIVERY': 'delivery_orders'}


# -- value conversion ------------------------------------------------------------------

def timestamp(value: str | None) -> str | None:
    """Canonical CSV time ('2026-05-01 00:00:02+00:00') -> '2026-05-01T00:00:02Z'."""
    if not value:
        return None
    if len(value) == 25 and value.endswith('+00:00'):
        return f'{value[:10]}T{value[11:19]}Z'
    return datetime.fromisoformat(value).astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def csv_timestamp(value: str | None) -> str:
    """Inverse of timestamp(): '2026-05-01T00:00:02Z' -> '2026-05-01 00:00:02+00:00'."""
    return '' if value is None else f'{value[:10]} {value[11:19]}+00:00'


def now() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def text(value: str) -> str | None:
    """Empty CSV fields are NULL in Part 2 (COPY ... FORMAT csv)."""
    return value if value != '' else None


def number(value: str) -> float:
    return float(value)


def fixed(value: float | None, places: int) -> str:
    return '' if value is None else f'{value:.{places}f}'


def plain(value) -> str:
    return '' if value is None else str(value)


# -- lookups used to denormalise -------------------------------------------------------

@dataclass
class Lookups:
    location_area: dict[str, str]           # location_id -> service_area_id
    outlet_area: dict[str, str]             # merchant_outlet_id -> service_area_id
    vehicles: dict[str, tuple[str, str]]    # vehicle_id -> (driver_partner_id, vehicle_type)


# -- geo --------------------------------------------------------------------------------

def service_area_doc(row: dict) -> dict:
    return {'_id': row['service_area_id'], 'type': 'service_area',
            'service_area_id': row['service_area_id'], 'area_name': row['area_name'],
            'city': row['city'], 'area_status': row['area_status']}


def location_doc(row: dict) -> dict:
    return {'_id': row['location_id'], 'type': 'location', 'location_id': row['location_id'],
            'latitude': number(row['latitude']), 'longitude': number(row['longitude']),
            'address': row['address'], 'service_area_id': row['service_area_id']}


def outlet_doc(row: dict, location: dict) -> dict:
    """`location` is the outlet's location document (or the same fields)."""
    return {'_id': row['merchant_outlet_id'], 'type': 'merchant_outlet',
            'merchant_outlet_id': row['merchant_outlet_id'], 'outlet_name': row['outlet_name'],
            'outlet_status': row['outlet_status'],
            'location': {'location_id': location['location_id'], 'address': location['address'],
                         'latitude': location['latitude'], 'longitude': location['longitude'],
                         'service_area_id': location['service_area_id']}}


def geo_rows(doc: dict) -> tuple[str, dict]:
    if doc['type'] == 'service_area':
        return 'service_areas', {k: doc[k] for k in ('service_area_id', 'area_name', 'city',
                                                     'area_status')}
    if doc['type'] == 'location':
        return 'locations', {'location_id': doc['location_id'],
                             'latitude': fixed(doc['latitude'], 6),
                             'longitude': fixed(doc['longitude'], 6),
                             'address': doc['address'], 'service_area_id': doc['service_area_id']}
    return 'merchant_outlets', {'merchant_outlet_id': doc['merchant_outlet_id'],
                                'outlet_name': doc['outlet_name'],
                                'outlet_status': doc['outlet_status'],
                                'location_id': doc['location']['location_id']}


# -- customers and fleet ----------------------------------------------------------------

def customer_doc(row: dict) -> dict:
    return {'_id': row['customer_id'], 'type': 'customer', 'customer_id': row['customer_id'],
            'full_name': row['full_name'], 'phone_number': text(row['phone_number']),
            'email': text(row['email'])}


def customer_rows(doc: dict) -> dict:
    return {'customer_id': doc['customer_id'], 'full_name': doc['full_name'],
            'phone_number': plain(doc['phone_number']), 'email': plain(doc['email'])}


def driver_doc(row: dict, vehicles: list[dict]) -> dict:
    return {'_id': row['driver_partner_id'], 'type': 'driver_partner',
            'driver_partner_id': row['driver_partner_id'], 'full_name': row['full_name'],
            'phone_number': row['phone_number'], 'email': row['email'],
            'driver_status': row['driver_status'],
            'vehicles': [{'vehicle_id': v['vehicle_id'], 'plate_number': v['plate_number'],
                          'vehicle_type': v['vehicle_type'], 'vehicle_status': v['vehicle_status']}
                         for v in vehicles]}


def driver_rows(doc: dict) -> dict[str, list[dict]]:
    driver = {k: doc[k] for k in ('driver_partner_id', 'full_name', 'phone_number', 'email',
                                  'driver_status')}
    vehicles = [{'vehicle_id': v['vehicle_id'], 'plate_number': v['plate_number'],
                 'vehicle_type': v['vehicle_type'], 'vehicle_status': v['vehicle_status'],
                 'driver_partner_id': doc['driver_partner_id']} for v in doc['vehicles']]
    return {'driver_partners': [driver], 'vehicles': vehicles}


# -- orders -----------------------------------------------------------------------------

def assignment_doc(row: dict, lookups: Lookups) -> dict:
    driver_id, vehicle_type = lookups.vehicles[row['vehicle_id']]
    return {'assignment_id': row['assignment_id'], 'assignment_status': row['assignment_status'],
            'assigned_at': timestamp(row['assigned_at']),
            'responded_at': timestamp(row['responded_at']),
            'ended_at': timestamp(row['ended_at']), 'end_reason': text(row['end_reason']),
            'vehicle_id': row['vehicle_id'], 'driver_partner_id': driver_id,
            'vehicle_type': vehicle_type}


def trip_doc(row: dict, assignments: list[dict], lookups: Lookups) -> dict:
    return {'trip_id': row['trip_id'], 'trip_status': row['trip_status'],
            'started_at': timestamp(row['started_at']),
            'completed_at': timestamp(row['completed_at']),
            'start_location_id': row['start_location_id'],
            'end_location_id': row['end_location_id'],
            'start_service_area_id': lookups.location_area[row['start_location_id']],
            'assignments': [assignment_doc(a, lookups) for a in assignments]}


def subtype_doc(service_type: str, row: dict, lookups: Lookups) -> dict:
    if service_type == 'FOOD':
        return {'preparation_status': row['preparation_status'],
                'delivery_notes': text(row['delivery_notes']),
                'merchant_outlet_id': row['merchant_outlet_id'],
                'merchant_service_area_id': lookups.outlet_area[row['merchant_outlet_id']]}
    if service_type == 'MOBILITY':
        return {'ride_type': row['ride_type'], 'passenger_count': int(row['passenger_count']),
                'pickup_notes': text(row['pickup_notes'])}
    return {'package_description': row['package_description'],
            'package_weight': number(row['package_weight']),
            'recipient_name': row['recipient_name'],
            'recipient_contact': row['recipient_contact']}


def payment_doc(row: dict) -> dict:
    return {'payment_id': row['payment_id'], 'payment_method': row['payment_method'],
            'payment_status': row['payment_status'], 'amount': number(row['amount']),
            'paid_at': timestamp(row['paid_at'])}


def order_doc(order: dict, service_type: str, subtype: dict, payment: dict | None,
              trips: list[tuple[dict, list[dict]]], lookups: Lookups) -> dict:
    doc = {'_id': order['order_id'], 'type': 'service_order', 'order_id': order['order_id'],
           'customer_id': order['customer_id'], 'created_at': timestamp(order['created_at']),
           'order_status': order['order_status'], 'total_amount': number(order['total_amount']),
           'service_type': service_type,
           SUBTYPE_FIELD[service_type]: subtype_doc(service_type, subtype, lookups),
           'payment': payment_doc(payment) if payment else None,
           'trips': [trip_doc(trip, assignments, lookups) for trip, assignments in trips]}
    if doc['payment'] is None:
        del doc['payment']
    return doc


def order_rows(doc: dict) -> dict[str, list[dict]]:
    """Rebuild the Part 2 rows (as canonical CSV strings) that an order document holds."""
    oid = doc['order_id']
    rows: dict[str, list[dict]] = {table: [] for table in (
        'service_orders', 'food_orders', 'mobility_orders', 'delivery_orders', 'payments',
        'trips', 'driver_assignments')}
    rows['service_orders'].append({
        'order_id': oid, 'created_at': csv_timestamp(doc['created_at']),
        'order_status': doc['order_status'], 'total_amount': fixed(doc['total_amount'], 2),
        'customer_id': doc['customer_id']})
    sub = doc[SUBTYPE_FIELD[doc['service_type']]]
    if doc['service_type'] == 'FOOD':
        row = {'order_id': oid, 'preparation_status': sub['preparation_status'],
               'delivery_notes': plain(sub['delivery_notes']),
               'merchant_outlet_id': sub['merchant_outlet_id']}
    elif doc['service_type'] == 'MOBILITY':
        row = {'order_id': oid, 'ride_type': sub['ride_type'],
               'passenger_count': str(sub['passenger_count']),
               'pickup_notes': plain(sub['pickup_notes'])}
    else:
        row = {'order_id': oid, 'package_description': sub['package_description'],
               'package_weight': fixed(sub['package_weight'], 2),
               'recipient_name': sub['recipient_name'],
               'recipient_contact': sub['recipient_contact']}
    rows[SUBTYPE_TABLE[doc['service_type']]].append(row)
    if doc.get('payment'):
        p = doc['payment']
        rows['payments'].append({
            'payment_id': p['payment_id'], 'payment_method': p['payment_method'],
            'payment_status': p['payment_status'], 'amount': fixed(p['amount'], 2),
            'paid_at': csv_timestamp(p['paid_at']), 'order_id': oid})
    for trip in doc['trips']:
        rows['trips'].append({
            'trip_id': trip['trip_id'], 'started_at': csv_timestamp(trip['started_at']),
            'completed_at': csv_timestamp(trip['completed_at']),
            'trip_status': trip['trip_status'], 'order_id': oid,
            'start_location_id': trip['start_location_id'],
            'end_location_id': trip['end_location_id']})
        for a in trip['assignments']:
            rows['driver_assignments'].append({
                'assignment_id': a['assignment_id'], 'assigned_at': csv_timestamp(a['assigned_at']),
                'assignment_status': a['assignment_status'],
                'responded_at': csv_timestamp(a['responded_at']),
                'ended_at': csv_timestamp(a['ended_at']), 'end_reason': plain(a['end_reason']),
                'trip_id': trip['trip_id'], 'vehicle_id': a['vehicle_id']})
    return rows
