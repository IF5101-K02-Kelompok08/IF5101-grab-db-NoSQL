"""Exact number of view/document rows each analytic operation pulls from CouchDB on the full data."""
import csv, json, os, sys
from collections import defaultdict
D = sys.argv[1]
rd = lambda t: csv.DictReader(open(f'{D}/{t}.csv', newline='', encoding='utf-8'))
veh = {r['vehicle_id']: (r['driver_partner_id'], r['vehicle_type']) for r in rd('vehicles')}
loc_area = {r['location_id']: r['service_area_id'] for r in rd('locations')}
out_area = {r['merchant_outlet_id']: loc_area[r['location_id']] for r in rd('merchant_outlets')}
out_status = {r['merchant_outlet_id']: r['outlet_status'] for r in rd('merchant_outlets')}
order = {r['order_id']: (r['created_at'], r['order_status']) for r in rd('service_orders')}
pay = {r['order_id'] for r in rd('payments')}
pay_settled = {r['order_id'] for r in rd('payments') if r['payment_status'] in ('PAID', 'REFUNDED')}
trip = {r['trip_id']: (r['trip_status'], r['started_at'], r['order_id'], loc_area[r['start_location_id']]) for r in rd('trips')}
dec, dur, hourly = set(), set(), set()
for r in rd('driver_assignments'):
    d, _ = veh[r['vehicle_id']]; st, started, _, _ = trip[r['trip_id']]
    if r['assignment_status'] in ('ACCEPTED', 'REJECTED', 'EXPIRED'): dec.add(d)
    if r['assignment_status'] == 'ACCEPTED':
        if st == 'COMPLETED': dur.add(d)
        if st in ('COMPLETED', 'IN_PROGRESS') and started: hourly.add(r['vehicle_id'])
food = {r['order_id']: r['merchant_outlet_id'] for r in rd('food_orders')}
r8 = {o for oid, o in food.items() if order[oid][1] == 'COMPLETED' and oid in pay}
r9_days = {(order[oid][0][:10], out_area[o]) for oid, o in food.items() if '2026-10-01' <= order[oid][0][:10] < '2026-11-01'}
r9_supply = {(out_area[o], s) for o, s in out_status.items()}
months = {(order[o][0][:7]) for o in pay}
r6 = {trip_area for (_, _, oid, trip_area) in trip.values() if oid in pay_settled}
rows = {
 'PF-08-Q01': {'view_rows': len(dec) + 100000, 'detail': f'{len(dec):,} grup driver_decisions + 100,000 baris driver_names'},
 'PF-08-Q02': {'view_rows': len(dur) + 10, 'detail': f'{len(dur):,} grup driver_completed_duration + 10 dokumen driver'},
 'PF-08-Q03': {'view_rows': len(hourly), 'detail': f'{len(hourly):,} grup vehicle_hourly_usage (satu per kendaraan)'},
 'PF-02-Q04': {'view_rows': 260024, 'detail': '260,024 baris trip_attempts'},
 'PF-10-Q03': {'view_rows': len(r8) + 10 + 10, 'detail': f'{len(r8):,} grup food_revenue_by_outlet + 10 outlet + area'},
 'PF-09-Q02': {'view_rows': len(r9_supply) + len(r9_days) + 23, 'detail': f'{len(r9_supply)} grup outlet + {len(r9_days)} grup hari×area + 23 area'},
 'PF-10-Q02': {'view_rows': len(months) * 4, 'detail': 'grup bulan×metode'},
 'PF-10-Q01': {'view_rows': len(r6) + 23, 'detail': f'{len(r6)} grup area + 23 dokumen area'},
 'PF-09-Q01': {'view_rows': 23 + 23, 'detail': '23 grup area + 23 dokumen area'},
 'PF-05-Q01': {'view_rows': 1 + 20, 'detail': '1 baris area + 20 dokumen outlet (Mango)'},
 'PF-01-Q01': {'view_rows': 1, 'detail': '1 dokumen order'},
 'PF-01-Q02': {'view_rows': 20, 'detail': '20 baris by_customer'},
 'PF-03-Q01': {'view_rows': 1, 'detail': '1 baris by_trip'},
}
print(json.dumps(rows, indent=1))
json.dump(rows, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'view_rows_from_dataset.json'), 'w'), indent=1)
