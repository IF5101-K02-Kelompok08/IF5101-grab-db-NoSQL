"""The essential operations of the system, implemented on CouchDB.

Every operation is mapped to the functional (PF) and non-functional (PNF) requirements of
report section 1.2 and, where it has one, to the Part 2 SQL query it re-implements (R1-R10,
W1-W10). OPERATIONS at the bottom of this file is the catalog used by the benchmark adapter
and by docs/couchdb_design.md.

Result rows use the column names of the Part 2 queries so the two implementations can be
compared. Ties that SQL leaves in arbitrary order are broken by ID so results are stable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from couchdb import model
from couchdb.client import CouchClient
from couchdb.rounding import as_float, money, percent, ratio
from couchdb.settings import Settings

RUNNING_ORDER_STATUSES = ('PENDING', 'CONFIRMED', 'IN_PROGRESS')
ACTIVE_ASSIGNMENT_STATUSES = ('PENDING', 'ACCEPTED')
HIGH_KEY = {}  # sorts after every other JSON value in CouchDB collation


class IntegrityError(Exception):
    """A business or referential rule that the application enforces was violated."""


@dataclass(frozen=True)
class Operation:
    operation_id: str
    requirement_ids: tuple[str, ...]
    name: str
    kind: str              # read, analytics, create, update, delete
    part2_query: str | None
    description: str


class GrabCouch:
    def __init__(self, client: CouchClient, settings: Settings) -> None:
        self.c = client
        self.orders = settings.db('orders')
        self.fleet = settings.db('fleet')
        self.customers = settings.db('customers')
        self.geo = settings.db('geo')

    # ==================================================================================
    # Helpers
    # ==================================================================================

    def _docs(self, db: str, ids: list[str]) -> dict[str, dict]:
        """Fetch several documents in one request; missing ones are left out."""
        if not ids:
            return {}
        rows = self.c.all_docs(db, keys=list(ids), include_docs=True)['rows']
        return {row['id']: row['doc'] for row in rows if row.get('doc')}

    def _areas(self, area_ids=None) -> dict[str, dict]:
        if area_ids is None:
            rows = self.c.all_docs(self.geo, startkey='SAR', endkey='SAR￰',
                                   include_docs=True)['rows']
            return {row['id']: row['doc'] for row in rows}
        return self._docs(self.geo, sorted(area_ids))

    def _require_customer(self, customer_id: str) -> None:
        response = self.c.request('HEAD', f'/{self.customers}/{customer_id}', ok=(200, 404))
        if response.status_code == 404:
            raise IntegrityError(f'Customer {customer_id} does not exist')

    def _locations(self, *location_ids: str) -> dict[str, dict]:
        found = self._docs(self.geo, list(location_ids))
        missing = [i for i in location_ids if i not in found or found[i].get('type') != 'location']
        if missing:
            raise IntegrityError(f'Location(s) {", ".join(missing)} do not exist')
        return found

    @staticmethod
    def _trip(doc: dict, trip_id: str) -> dict:
        for trip in doc['trips']:
            if trip['trip_id'] == trip_id:
                return trip
        raise IntegrityError(f'{doc["_id"]} has no trip {trip_id}')

    @staticmethod
    def _assignment(doc: dict, assignment_id: str) -> tuple[dict, dict]:
        for trip in doc['trips']:
            for assignment in trip['assignments']:
                if assignment['assignment_id'] == assignment_id:
                    return trip, assignment
        raise IntegrityError(f'{doc["_id"]} has no assignment {assignment_id}')

    @staticmethod
    def _new_trip(trip_id: str, start: dict, end: dict) -> dict:
        return {'trip_id': trip_id, 'trip_status': 'SEARCHING', 'started_at': None,
                'completed_at': None, 'start_location_id': start['_id'],
                'end_location_id': end['_id'], 'start_service_area_id': start['service_area_id'],
                'assignments': []}

    # ==================================================================================
    # Operational reads (PF-01, PF-03, PF-05)
    # ==================================================================================

    def get_order(self, order_id: str) -> dict:
        """PF-01-Q01: an order with its subtype, payment, trips and assignments (1 request)."""
        return self.c.get_doc(self.orders, order_id)

    def customer_order_history(self, customer_id: str, limit: int = 20) -> list[dict]:
        """PF-01-Q02: a customer's latest orders, newest first (1 view request)."""
        rows = self.c.view(self.orders, 'ops', 'by_customer', descending=True,
                           startkey=[customer_id, HIGH_KEY], endkey=[customer_id],
                           limit=limit)['rows']
        return [{'order_id': row['id'], 'created_at': row['key'][1], **row['value']}
                for row in rows]

    def trip_status(self, trip_id: str) -> dict | None:
        """PF-03-Q01: the status of a trip by trip_id, through the by_trip view (1 request)."""
        rows = self.c.view(self.orders, 'ops', 'by_trip', key=trip_id)['rows']
        if not rows:
            return None
        return {'trip_id': trip_id, 'order_id': rows[0]['id'], **rows[0]['value']}

    def open_outlets_in_area(self, area_name: str = 'Tebet', city: str = 'Jakarta',
                             limit: int = 20) -> list[dict]:
        """PF-05-Q01 (R10): first page of OPEN outlets in an ACTIVE service area, by name.

        One view request resolves the area, then one Mango query reads only the page it
        returns from the open-outlets-by-area index (2 requests).
        """
        rows = self.c.view(self.geo, 'geo', 'areas_by_city_name', key=[city, area_name])['rows']
        if not rows or rows[0]['value'] != 'ACTIVE':
            return []
        area_id = rows[0]['id']
        result = self.c.find(self.geo, {
            'selector': {'location.service_area_id': area_id, 'outlet_status': 'OPEN'},
            'sort': [{'location.service_area_id': 'asc'}, {'outlet_status': 'asc'},
                     {'outlet_name': 'asc'}, {'merchant_outlet_id': 'asc'}],
            'fields': ['merchant_outlet_id', 'outlet_name', 'location.address',
                       'location.latitude', 'location.longitude'],
            'use_index': ['mango-outlets', 'open-outlets-by-area'],
            'limit': limit,
        })
        return [{'merchant_outlet_id': d['merchant_outlet_id'], 'outlet_name': d['outlet_name'],
                 'address': d['location']['address'], 'latitude': d['location']['latitude'],
                 'longitude': d['location']['longitude']} for d in result['docs']]

    # ==================================================================================
    # Analytics (PF-02, PF-08, PF-09, PF-10): Part 2 R1-R9
    # ==================================================================================

    def driver_acceptance_rates(self, min_decisions: int = 5) -> list[dict]:
        """PF-08-Q01 (R1): acceptance rate per driver with at least `min_decisions`
        ACCEPTED/REJECTED offers. One grouped view (_sum of [accepted, rejected, expired])
        plus the driver_names view, joined on the client."""
        counts = {}
        for row in self.c.iter_view(self.orders, 'analytics', 'driver_decisions', group=True):
            accepted, rejected, expired = (list(row['value']) + [0, 0, 0])[:3]
            if accepted + rejected >= min_decisions:
                counts[row['key']] = (int(accepted), int(rejected), int(expired))
        names = {row['key']: row['value']
                 for row in self.c.iter_view(self.fleet, 'fleet', 'driver_names')}
        rows = []
        for driver_id, (accepted, rejected, expired) in counts.items():
            rate = percent(accepted, accepted + rejected)
            rows.append((rate, accepted, driver_id, {
                'driver_partner_id': driver_id, 'full_name': names.get(driver_id),
                'total_accepted': accepted, 'total_rejected': rejected,
                'total_expired': expired, 'acceptance_rate_percentage': as_float(rate)}))
        rows.sort(key=lambda r: (-r[0], -r[1], r[2]))
        return [r[3] for r in rows]

    def top_drivers_by_duration(self, limit: int = 10) -> list[dict]:
        """PF-08-Q02 (R2): drivers with the longest total duration of completed trips.
        A grouped _stats view gives count and total seconds per driver; the client picks
        the top `limit` and fetches their names (2 requests)."""
        stats = [(int(row['value']['sum']), int(row['value']['count']), row['key'])
                 for row in self.c.iter_view(self.orders, 'analytics',
                                             'driver_completed_duration', group=True)]
        stats.sort(key=lambda s: (-s[0], s[2]))
        top = stats[:limit]
        drivers = self._docs(self.fleet, [driver_id for _, _, driver_id in top])
        return [{'driver_partner_id': driver_id,
                 'full_name': drivers.get(driver_id, {}).get('full_name'),
                 'total_completed_trips': count,
                 'total_duration_minutes': as_float(ratio(seconds, 60, 2)),
                 'avg_duration_per_trip_minutes': as_float(ratio(seconds, 60 * count, 2))}
                for seconds, count, driver_id in top]

    def vehicle_type_hourly_utilization(self) -> list[dict]:
        """PF-08-Q03 (R3): trips and distinct vehicles per vehicle type and start hour.
        One grouped view returns, per vehicle, its trip count for each hour; summing them
        and counting the non-zero hours gives COUNT(*) and COUNT(DISTINCT vehicle)."""
        totals = defaultdict(lambda: [0, 0])
        for row in self.c.iter_view(self.orders, 'analytics', 'vehicle_hourly_usage', group=True):
            vehicle_type = row['key'][0]
            value = row['value'] if isinstance(row['value'], list) else [row['value']]
            for hour, trips in enumerate(value):
                if trips:
                    totals[hour, vehicle_type][0] += int(trips)
                    totals[hour, vehicle_type][1] += 1
        rows = [{'vehicle_type': vehicle_type, 'trip_hour': hour, 'total_trips': trips,
                 'unique_vehicles_used': vehicles}
                for (hour, vehicle_type), (trips, vehicles) in totals.items()]
        rows.sort(key=lambda r: (r['trip_hour'], -r['total_trips'], r['vehicle_type']))
        return rows

    def reassignment_history(self, min_attempts: int = 3) -> list[dict]:
        """PF-02-Q04 (R4): trips with at least `min_attempts` offers and an ACCEPTED one,
        most attempts first, then latest start. Reads a key range of a view (1 request)."""
        rows = []
        for row in self.c.iter_view(self.orders, 'analytics', 'trip_attempts', descending=True,
                                    startkey=[HIGH_KEY], endkey=[min_attempts]):
            attempts, started_at, trip_id = row['key']
            rows.append({'trip_id': trip_id, 'order_id': row['id'],
                         'trip_status': row['value']['trip_status'], 'total_attempts': attempts,
                         'started_at': started_at, 'completed_at': row['value']['completed_at']})
        # PostgreSQL sorts NULL first in DESC order; CouchDB collates null lowest.
        rows.sort(key=lambda r: (-r['total_attempts'], r['started_at'] is not None))
        return rows

    def trips_per_service_area(self) -> list[dict]:
        """PF-09-Q01 (R5): trips per service area of the pickup location (2 requests)."""
        counts = {row['key']: int(row['value']) for row in
                  self.c.view(self.orders, 'analytics', 'trips_by_start_area', group=True)['rows']}
        areas = self._areas(counts)
        rows = [{'service_area_id': area_id, 'area_name': areas[area_id]['area_name'],
                 'city': areas[area_id]['city'], 'total_trips': total}
                for area_id, total in counts.items() if area_id in areas]
        rows.sort(key=lambda r: (-r['total_trips'], r['service_area_id']))
        return rows

    def revenue_and_refunds_per_area(self) -> list[dict]:
        """PF-10-Q01 (R6): settled payments, net revenue and refunds per service area of
        the order's first trip (2 requests)."""
        sums = {row['key']: row['value'] for row in
                self.c.view(self.orders, 'analytics', 'settled_payments_by_area', group=True)['rows']}
        areas = self._areas(sums)
        rows = []
        for area_id, (settled, paid_cents, refunded_cents, refunded) in sums.items():
            if area_id not in areas:
                continue
            rows.append((int(paid_cents), area_id, {
                'city': areas[area_id]['city'], 'area_name': areas[area_id]['area_name'],
                'settled_payments': int(settled), 'net_revenue': as_float(money(paid_cents)),
                'refunded_amount': as_float(money(refunded_cents)),
                'refund_rate_pct': as_float(percent(refunded, settled))}))
        rows.sort(key=lambda r: (-r[0], r[2]['city'], r[2]['area_name']))
        return [r[2] for r in rows]

    def monthly_payment_summary(self) -> list[dict]:
        """PF-10-Q02 (R7): payments per order month and method (1 request, group_level=2)."""
        rows = []
        for row in self.c.view(self.orders, 'analytics', 'payments_by_month_method',
                               group_level=2)['rows']:
            month, method = row['key']
            total, paid, refunded, failed, pending, paid_cents, settle_seconds, settled = (
                int(x) for x in row['value'])
            rows.append((month, -paid_cents, method, {
                'order_month': f'{month}-01', 'payment_method': method,
                'total_payments': total, 'paid': paid, 'refunded': refunded, 'failed': failed,
                'pending': pending, 'paid_pct': as_float(percent(paid, total - pending)),
                'net_revenue': as_float(money(paid_cents)),
                'avg_settle_minutes': as_float(ratio(settle_seconds, 60 * settled, 1))}))
        rows.sort(key=lambda r: r[:3])
        return [r[3] for r in rows]

    def top_outlets_by_food_revenue(self, limit: int = 10) -> list[dict]:
        """PF-10-Q03 (R8): outlets with the highest net revenue from COMPLETED food orders.
        Grouped view per outlet, top `limit` on the client, then the outlet and area
        documents for those rows (3 requests)."""
        sums = [(int(row['value'][1]), row['key'], [int(x) for x in row['value']])
                for row in self.c.iter_view(self.orders, 'analytics', 'food_revenue_by_outlet',
                                            group=True)]
        sums.sort(key=lambda s: (-s[0], s[1]))
        top = sums[:limit]
        outlets = self._docs(self.geo, [outlet_id for _, outlet_id, _ in top])
        areas = self._areas({o['location']['service_area_id'] for o in outlets.values()})
        rows = []
        for paid_cents, outlet_id, (orders, _, amount_cents, refunded) in top:
            outlet = outlets[outlet_id]
            area = areas[outlet['location']['service_area_id']]
            rows.append({'merchant_outlet_id': outlet_id, 'outlet_name': outlet['outlet_name'],
                         'outlet_status': outlet['outlet_status'], 'city': area['city'],
                         'area_name': area['area_name'], 'completed_orders': orders,
                         'net_revenue': as_float(money(paid_cents)),
                         'avg_order_value': as_float(ratio(amount_cents, 100 * orders, 2)),
                         'refund_rate_pct': as_float(percent(refunded, orders))})
        return rows

    def outlet_supply_vs_food_demand(self, start_day: str = '2026-10-01',
                                     end_day: str = '2026-11-01') -> list[dict]:
        """PF-09-Q02 (R9): outlets (total, OPEN) against food orders created in
        [start_day, end_day) per service area (3 requests)."""
        supply = defaultdict(lambda: [0, 0])
        for row in self.c.view(self.geo, 'geo', 'outlets_by_area_status', group_level=2)['rows']:
            area_id, status = row['key']
            supply[area_id][0] += int(row['value'])
            if status == 'OPEN':
                supply[area_id][1] += int(row['value'])
        demand = defaultdict(int)
        for row in self.c.view(self.orders, 'analytics', 'food_orders_by_day_area', group_level=2,
                               startkey=[start_day], endkey=[end_day], inclusive_end=False)['rows']:
            demand[row['key'][1]] += int(row['value'])
        rows = []
        for area_id, area in self._areas().items():
            total, open_outlets = supply.get(area_id, (0, 0))
            orders = demand.get(area_id)
            per_outlet = ratio(orders, open_outlets, 2) if orders is not None else None
            rows.append({'city': area['city'], 'area_name': area['area_name'],
                         'area_status': area['area_status'], 'total_outlets': total,
                         'open_outlets': open_outlets, 'food_orders': orders or 0,
                         'orders_per_open_outlet': as_float(per_outlet)})
        rows.sort(key=lambda r: (r['orders_per_open_outlet'] is None,
                                 -(r['orders_per_open_outlet'] or 0), r['city'], r['area_name']))
        return rows

    # ==================================================================================
    # Trip lifecycle writes (PF-01, PF-02, PF-03): Part 2 W1-W5
    # ==================================================================================

    def create_confirmed_order_with_trip(self, order_id: str, payment_id: str, trip_id: str,
                                         customer_id: str = 'CUS0000001',
                                         start_location_id: str = 'LOC0000001',
                                         end_location_id: str = 'LOC0000002',
                                         amount: float = 15000.0) -> dict:
        """PF-01-Q03 (W1): a confirmed cash GrabBike order whose trip starts SEARCHING.

        The order, its subtype, payment and trip are one document, so they are created
        together by a single PUT. Foreign keys are checked first (2 reads + 1 write).
        """
        self._require_customer(customer_id)
        places = self._locations(start_location_id, end_location_id)
        doc = {'_id': order_id, 'type': 'service_order', 'order_id': order_id,
               'customer_id': customer_id, 'created_at': model.now(),
               'order_status': 'CONFIRMED', 'total_amount': amount, 'service_type': 'MOBILITY',
               'mobility': {'ride_type': 'GrabBike', 'passenger_count': 1, 'pickup_notes': None},
               'payment': {'payment_id': payment_id, 'payment_method': 'CASH',
                           'payment_status': 'PENDING', 'amount': amount, 'paid_at': None},
               'trips': [self._new_trip(trip_id, places[start_location_id],
                                        places[end_location_id])]}
        self.c.put_doc(self.orders, doc)
        return doc

    def dispatch_offer(self, order_id: str, trip_id: str, assignment_id: str,
                       vehicle_type: str = 'Motorcycle') -> dict:
        """PF-02-Q01 (W2): offer a SEARCHING trip to an available driver as a PENDING
        assignment. Refused while the trip already has an active offer, so a trip never
        holds two active assignments; a concurrent writer gets a 409 conflict and re-checks
        (1 view + read-modify-write)."""
        rows = self.c.view(self.fleet, 'fleet', 'available_vehicles', startkey=[vehicle_type],
                           endkey=[vehicle_type, HIGH_KEY], limit=1)['rows']
        if not rows:
            raise IntegrityError(f'No available {vehicle_type}')
        vehicle_id, driver_id = rows[0]['key'][1], rows[0]['value']

        def offer(doc: dict) -> None:
            trip = self._trip(doc, trip_id)
            if trip['trip_status'] != 'SEARCHING':
                raise IntegrityError(f'{trip_id} is {trip["trip_status"]}, not SEARCHING')
            if any(a['assignment_status'] in ACTIVE_ASSIGNMENT_STATUSES for a in trip['assignments']):
                raise IntegrityError(f'{trip_id} already has an active assignment')
            trip['assignments'].append({
                'assignment_id': assignment_id, 'assignment_status': 'PENDING',
                'assigned_at': model.now(), 'responded_at': None, 'ended_at': None,
                'end_reason': None, 'vehicle_id': vehicle_id, 'driver_partner_id': driver_id,
                'vehicle_type': vehicle_type})

        doc = self.c.update_doc(self.orders, order_id, offer)
        return self._assignment(doc, assignment_id)[1]

    def accept_offer_and_start_trip(self, order_id: str, assignment_id: str) -> dict:
        """PF-02-Q02 (W3): the driver accepts (trip ALLOCATED), then picks up the customer
        (trip IN_PROGRESS). Two writes, each atomic, because each is a status transition
        that validate_doc_update checks (1 read + 2 writes)."""
        def accept(doc: dict) -> None:
            trip, assignment = self._assignment(doc, assignment_id)
            if assignment['assignment_status'] != 'PENDING':
                raise IntegrityError(f'{assignment_id} is {assignment["assignment_status"]}')
            assignment['assignment_status'] = 'ACCEPTED'
            assignment['responded_at'] = model.now()
            trip['trip_status'] = 'ALLOCATED'

        doc = self.c.update_doc(self.orders, order_id, accept)
        trip, _ = self._assignment(doc, assignment_id)
        trip['trip_status'] = 'IN_PROGRESS'
        trip['started_at'] = model.now()
        self.c.put_doc(self.orders, doc)
        return trip

    def reject_offer(self, order_id: str, assignment_id: str,
                     reason: str = 'Too far from pickup location') -> dict:
        """PF-02-Q03 (W4): the driver rejects a PENDING offer; the trip keeps SEARCHING
        (read-modify-write, 1 read + 1 write)."""
        def reject(doc: dict) -> None:
            _, assignment = self._assignment(doc, assignment_id)
            if assignment['assignment_status'] != 'PENDING':
                raise IntegrityError(f'{assignment_id} is {assignment["assignment_status"]}')
            moment = model.now()
            assignment.update(assignment_status='REJECTED', responded_at=moment,
                              ended_at=moment, end_reason=reason)

        doc = self.c.update_doc(self.orders, order_id, reject)
        return self._assignment(doc, assignment_id)[1]

    def complete_trip_and_release_driver(self, order_id: str, trip_id: str) -> dict:
        """PF-03-Q02 (W5): the trip becomes COMPLETED, the accepted assignment ends, and the
        driver is set ACTIVE again. The order and the driver are two documents and CouchDB
        has no multi-document transaction: the order is written first, and the driver
        update is idempotent so it can simply be retried (2 reads + 2 writes)."""
        def complete(doc: dict) -> None:
            trip = self._trip(doc, trip_id)
            moment = model.now()
            trip['trip_status'] = 'COMPLETED'
            trip['completed_at'] = moment
            for assignment in trip['assignments']:
                if assignment['assignment_status'] == 'ACCEPTED':
                    assignment['ended_at'] = moment

        doc = self.c.update_doc(self.orders, order_id, complete)
        trip = self._trip(doc, trip_id)
        drivers = [a['driver_partner_id'] for a in trip['assignments']
                   if a['assignment_status'] == 'ACCEPTED']
        driver = None
        if drivers:
            driver = self.c.update_doc(self.fleet, drivers[0],
                                       lambda d: d.__setitem__('driver_status', 'ACTIVE'))
        return {'trip': trip, 'driver_partner_id': drivers[0] if drivers else None,
                'driver_status': driver['driver_status'] if driver else None}

    # ==================================================================================
    # Payments (PF-04): Part 2 W7-W9
    # ==================================================================================

    def checkout_non_cash_with_retry(self, order_id: str, payment_id: str, trip_id: str,
                                     customer_id: str = 'CUS0000001',
                                     start_location_id: str = 'LOC0000001',
                                     end_location_id: str = 'LOC0000002',
                                     amount: float = 20000.0) -> dict:
        """PF-04-Q01 (W7): a GrabCar order paid by E_WALLET. The first charge FAILS, the
        retry succeeds; then the order is CONFIRMED and its trip starts SEARCHING. Each
        payment status change is one document revision, validated as a transition
        (2 reads + 4 writes)."""
        self._require_customer(customer_id)
        places = self._locations(start_location_id, end_location_id)
        doc = {'_id': order_id, 'type': 'service_order', 'order_id': order_id,
               'customer_id': customer_id, 'created_at': model.now(), 'order_status': 'PENDING',
               'total_amount': amount, 'service_type': 'MOBILITY',
               'mobility': {'ride_type': 'GrabCar', 'passenger_count': 2,
                            'pickup_notes': 'Saya tunggu di lobi'},
               'payment': {'payment_id': payment_id, 'payment_method': 'E_WALLET',
                           'payment_status': 'PENDING', 'amount': amount, 'paid_at': None},
               'trips': []}
        self.c.put_doc(self.orders, doc)
        doc['payment']['payment_status'] = 'FAILED'          # gateway declines
        self.c.put_doc(self.orders, doc)
        doc['payment']['payment_status'] = 'PENDING'         # customer retries
        self.c.put_doc(self.orders, doc)
        doc['payment'].update(payment_status='PAID', paid_at=model.now())
        doc['order_status'] = 'CONFIRMED'
        doc['trips'].append(self._new_trip(trip_id, places[start_location_id],
                                           places[end_location_id]))
        self.c.put_doc(self.orders, doc)
        return doc

    def cancel_before_pickup(self, order_id: str, reason: str = 'Cancelled by customer') -> dict:
        """PF-04-Q02 (W8): cancel a CONFIRMED order before pickup. Order, trips, active
        assignments and the payment (PAID -> REFUNDED, PENDING -> FAILED) change in ONE
        atomic document write; an order that is no longer CONFIRMED is left unchanged
        (1 read + at most 1 write)."""
        outcome = {'cancelled': False}

        def cancel(doc: dict) -> bool:
            outcome['cancelled'] = doc['order_status'] == 'CONFIRMED'
            if not outcome['cancelled']:
                return False  # already picked up, finished or cancelled: nothing to write
            moment = model.now()
            doc['order_status'] = 'CANCELLED'
            for trip in doc['trips']:
                if trip['trip_status'] in ('SEARCHING', 'ALLOCATED'):
                    trip['trip_status'] = 'CANCELLED'
                    for a in trip['assignments']:
                        if a['assignment_status'] in ACTIVE_ASSIGNMENT_STATUSES:
                            a.update(assignment_status='CANCELLED', ended_at=moment,
                                     end_reason=reason)
            payment = doc.get('payment')
            if payment and payment['payment_status'] == 'PAID':
                payment['payment_status'] = 'REFUNDED'
            elif payment and payment['payment_status'] == 'PENDING':
                payment['payment_status'] = 'FAILED'
            return True

        doc = self.c.update_doc(self.orders, order_id, cancel)
        return {'cancelled': outcome['cancelled'], 'order': doc}

    def complete_cash_ride(self, order_id: str, trip_id: str) -> dict:
        """PF-04-Q03 (W9): finish a cash ride and collect the money. Trip and order become
        COMPLETED and the CASH payment PAID in ONE document write, so a ride can never be
        recorded as finished without its payment. If any transition is invalid (e.g. the
        order was cancelled), validate_doc_update rejects the whole write (1 read + 1 write)."""
        def complete(doc: dict) -> None:
            moment = model.now()
            trip = self._trip(doc, trip_id)
            trip['trip_status'] = 'COMPLETED'
            trip['completed_at'] = moment
            doc['order_status'] = 'COMPLETED'
            payment = doc.get('payment')
            if payment and payment['payment_method'] == 'CASH':
                payment['payment_status'] = 'PAID'
                payment['paid_at'] = moment

        return self.c.update_doc(self.orders, order_id, complete)

    # ==================================================================================
    # Service areas and outlets (PF-06, PF-07): Part 2 W6, W10 and deletion
    # ==================================================================================

    def onboard_service_area(self, area: dict, locations: list[dict],
                             outlets: list[dict]) -> dict:
        """PF-06-Q01 (W6): a new service area with its first locations and outlets.

        All documents go to grab_geo in one _bulk_docs request. CouchDB applies each
        document on its own (no all-or-nothing), so if any is rejected the ones already
        written are deleted again (compensation). Uniqueness of (city, area_name) and the
        rule that an area has at least one location are checked first (1 view + 1 bulk).
        """
        if not locations:
            raise IntegrityError('A service area needs at least one location')
        same_name = self.c.view(self.geo, 'geo', 'areas_by_city_name',
                                key=[area['city'], area['area_name']])['rows']
        if same_name:
            raise IntegrityError(f'{area["area_name"]}, {area["city"]} already exists')
        location_docs = [{'_id': loc['location_id'], 'type': 'location',
                          'location_id': loc['location_id'], 'latitude': float(loc['latitude']),
                          'longitude': float(loc['longitude']), 'address': loc['address'],
                          'service_area_id': area['service_area_id']} for loc in locations]
        by_id = {doc['_id']: doc for doc in location_docs}
        docs = [model.service_area_doc(area), *location_docs,
                *(model.outlet_doc(outlet, by_id[outlet['location_id']]) for outlet in outlets)]
        results = self.c.bulk_docs(self.geo, docs)
        failed = [r for r in results if 'error' in r]
        if failed:
            written = {r['id']: r['rev'] for r in results if 'rev' in r}
            if written:
                self.c.bulk_docs(self.geo, [{'_id': i, '_rev': rev, '_deleted': True}
                                            for i, rev in written.items()])
            raise IntegrityError(f'Onboarding rolled back: {failed[0]}')
        return {'service_area_id': area['service_area_id'], 'documents_written': len(results)}

    def deactivate_service_area(self, area_id: str) -> dict:
        """PF-06-Q02 (W10): set an area INACTIVE and delist its outlets: outlets without a
        running food order become INACTIVE, outlets that still have one are only CLOSED so
        their orders can finish (2 reads, 1 write, 1 view, 1 bulk write)."""
        area = self.c.update_doc(self.geo, area_id,
                                 lambda d: d.__setitem__('area_status', 'INACTIVE'))
        outlets = [row['doc'] for row in self.c.view(
            self.geo, 'geo', 'outlets_by_area_status', reduce=False, include_docs=True,
            startkey=[area_id], endkey=[area_id, HIGH_KEY])['rows']]
        running = set()
        if outlets:
            keys = [[o['_id'], status] for o in outlets for status in RUNNING_ORDER_STATUSES]
            for row in self.c.view(self.orders, 'ops', 'food_by_outlet', keys=keys,
                                   group=True)['rows']:
                if row['value']:
                    running.add(row['key'][0])
        changed = []
        for outlet in outlets:
            if outlet['outlet_status'] != 'INACTIVE' and outlet['_id'] not in running:
                outlet['outlet_status'] = 'INACTIVE'
                changed.append(outlet)
            elif outlet['outlet_status'] == 'OPEN':
                outlet['outlet_status'] = 'CLOSED'
                changed.append(outlet)
        results = self.c.bulk_docs(self.geo, changed) if changed else []
        failed = [r for r in results if 'error' in r]
        if failed:
            raise IntegrityError(f'Some outlets were not updated: {failed}')
        return {'service_area_id': area_id, 'area_status': area['area_status'],
                'outlets': {o['_id']: o['outlet_status'] for o in outlets}}

    def delete_outlet(self, outlet_id: str) -> dict:
        """PF-07-Q01/Q02: delete a merchant outlet only if it is INACTIVE (delisted) and no
        food order refers to it. CouchDB has no foreign keys, so the reference check is a
        reduce query on the food_by_outlet view (2 reads + at most 1 delete)."""
        outlet = self.c.get_doc(self.geo, outlet_id)
        if outlet.get('type') != 'merchant_outlet':
            raise IntegrityError(f'{outlet_id} is not a merchant outlet')
        rows = self.c.view(self.orders, 'ops', 'food_by_outlet', startkey=[outlet_id],
                           endkey=[outlet_id, HIGH_KEY])['rows']
        references = int(rows[0]['value']) if rows else 0
        if outlet['outlet_status'] != 'INACTIVE':
            return {'deleted': False, 'reason': f'outlet is {outlet["outlet_status"]}',
                    'food_orders': references}
        if references:
            return {'deleted': False, 'reason': 'outlet is referenced by food orders',
                    'food_orders': references}
        rev = self.c.delete_doc(self.geo, outlet_id, outlet['_rev'])
        return {'deleted': True, 'rev': rev, 'food_orders': 0}


OPERATIONS: tuple[Operation, ...] = (
    Operation('PF-01-Q01', ('PF-01', 'PNF-03'), 'get_order', 'read', None,
              'Read one order with its subtype, payment, trips and assignments'),
    Operation('PF-01-Q02', ('PF-01', 'PNF-03'), 'customer_order_history', 'read', None,
              "Read a customer's 20 latest orders"),
    Operation('PF-01-Q03', ('PF-01', 'PNF-04'), 'create_confirmed_order_with_trip', 'create', 'W1',
              'Create a confirmed order with its payment and SEARCHING trip'),
    Operation('PF-02-Q01', ('PF-02', 'PNF-04'), 'dispatch_offer', 'update', 'W2',
              'Offer a trip to an available driver'),
    Operation('PF-02-Q02', ('PF-02', 'PF-03', 'PNF-04'), 'accept_offer_and_start_trip', 'update', 'W3',
              'Driver accepts the offer and starts the trip'),
    Operation('PF-02-Q03', ('PF-02', 'PNF-04'), 'reject_offer', 'update', 'W4',
              'Driver rejects the offer'),
    Operation('PF-02-Q04', ('PF-02', 'PF-08'), 'reassignment_history', 'analytics', 'R4',
              'Trips offered more than twice and eventually accepted'),
    Operation('PF-03-Q01', ('PF-03', 'PNF-03'), 'trip_status', 'read', None,
              'Read the status of a trip by trip_id'),
    Operation('PF-03-Q02', ('PF-03', 'PNF-04'), 'complete_trip_and_release_driver', 'update', 'W5',
              'Complete a trip and make the driver available'),
    Operation('PF-04-Q01', ('PF-04', 'PNF-04'), 'checkout_non_cash_with_retry', 'create', 'W7',
              'Non-cash checkout that fails once and succeeds on retry'),
    Operation('PF-04-Q02', ('PF-04', 'PNF-04'), 'cancel_before_pickup', 'update', 'W8',
              'Cancel before pickup and settle the payment'),
    Operation('PF-04-Q03', ('PF-04', 'PNF-04'), 'complete_cash_ride', 'update', 'W9',
              'Complete a cash ride and collect the payment atomically'),
    Operation('PF-05-Q01', ('PF-05', 'PNF-03'), 'open_outlets_in_area', 'read', 'R10',
              'First page of open outlets in a service area'),
    Operation('PF-06-Q01', ('PF-06', 'PNF-04'), 'onboard_service_area', 'create', 'W6',
              'Onboard a service area with its locations and outlets'),
    Operation('PF-06-Q02', ('PF-06', 'PNF-04'), 'deactivate_service_area', 'update', 'W10',
              'Deactivate a service area and delist its outlets'),
    Operation('PF-07-Q01', ('PF-07', 'PNF-04'), 'delete_outlet', 'delete', None,
              'Delete a delisted outlet that no order refers to'),
    Operation('PF-07-Q02', ('PF-07', 'PNF-04'), 'delete_outlet', 'delete', None,
              'Refuse to delete an outlet that orders still refer to'),
    Operation('PF-08-Q01', ('PF-08', 'PNF-07'), 'driver_acceptance_rates', 'analytics', 'R1',
              'Acceptance rate per driver'),
    Operation('PF-08-Q02', ('PF-08', 'PNF-07'), 'top_drivers_by_duration', 'analytics', 'R2',
              'Top 10 drivers by total completed trip duration'),
    Operation('PF-08-Q03', ('PF-08', 'PNF-07'), 'vehicle_type_hourly_utilization', 'analytics', 'R3',
              'Trips and distinct vehicles per vehicle type and hour'),
    Operation('PF-09-Q01', ('PF-09', 'PNF-07'), 'trips_per_service_area', 'analytics', 'R5',
              'Trips per service area'),
    Operation('PF-09-Q02', ('PF-09', 'PNF-07'), 'outlet_supply_vs_food_demand', 'analytics', 'R9',
              'Outlet supply against food demand per area, October 2026'),
    Operation('PF-10-Q01', ('PF-10', 'PNF-07'), 'revenue_and_refunds_per_area', 'analytics', 'R6',
              'Net revenue and refunds per service area'),
    Operation('PF-10-Q02', ('PF-10', 'PNF-07'), 'monthly_payment_summary', 'analytics', 'R7',
              'Monthly payment summary per payment method'),
    Operation('PF-10-Q03', ('PF-10', 'PNF-07'), 'top_outlets_by_food_revenue', 'analytics', 'R8',
              'Top 10 outlets by food revenue'),
)
