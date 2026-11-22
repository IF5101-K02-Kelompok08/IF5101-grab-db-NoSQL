"""Transform the canonical CSVs into the Memcached key-value working set and load it.

Usage (repo root, Memcached running):
    uv run --no-sync python -m memcached.load [--ttl policy|none] [--hours 24]

Working set (see memcached/schema.py for keys and TTL):
  area:<SAR>                         23 service areas (master data)
  area:<SAR>:outlets:open:p<n>       R10 pages, 20 OPEN outlets per page, ACTIVE areas only
  driver:<DRV>                       availability of every driver + vehicle
  driver:<DRV>:stats                 per-driver R1/R2 aggregates
  order:<ORD> / payment:<ORD> / trip:<TRP>
                                     orders that are still running OR created in the last
                                     --hours before the snapshot (hot operational data)
  report:r1..r9                      precomputed analytic results
  counter:area:<SAR>:trips:<day>     trips started per area on the snapshot day
"""
import argparse
import json
import time
from pathlib import Path
import pandas as pd
from memcached import reports, schema as k

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = pd.Timestamp("2026-11-01", tz="UTC")
RUNNING = {"PENDING", "CONFIRMED", "IN_PROGRESS"}
BATCH = 500


def none(v):
    return None if pd.isna(v) else v


def build_items(hours):
    """Yield (family, key, value, ttl_family) for the whole working set."""
    rd = reports.read
    areas = rd("service_areas")
    locs = rd("locations")
    outlets = rd("merchant_outlets")
    for a in areas.itertuples():
        yield "area", k.area(a.service_area_id), {"service_area_id": a.service_area_id, "area_name": a.area_name,
                                                   "city": a.city, "area_status": a.area_status}, "area"

    # R10 pages: OPEN outlets of ACTIVE areas ordered by (outlet_name, id), 20 per page
    o = outlets[outlets.outlet_status == "OPEN"].merge(locs, on="location_id")
    o = o[o.service_area_id.isin(areas[areas.area_status == "ACTIVE"].service_area_id)]
    o = o.sort_values(["service_area_id", "outlet_name", "merchant_outlet_id"])
    for sar, g in o.groupby("service_area_id"):
        rows = g[["merchant_outlet_id", "outlet_name", "address", "latitude", "longitude"]].to_dict("records")
        pages = [rows[i:i + 20] for i in range(0, len(rows), 20)]
        for n, page in enumerate(pages, 1):
            yield "outlets_page", k.open_outlets(sar, n), {"page": n, "pages": len(pages), "total": len(rows),
                                                           "items": page}, "outlets"

    # drivers: availability = ACTIVE and not engaged in a running trip at the snapshot
    drivers = rd("driver_partners", ["driver_partner_id", "driver_status"])
    veh = rd("vehicles", ["vehicle_id", "vehicle_type", "vehicle_status", "driver_partner_id"])
    asg = rd("driver_assignments")
    engaged = asg[asg.assignment_status.isin(["ACCEPTED", "PENDING"]) & asg.ended_at.isna()]
    engaged = engaged.merge(veh[["vehicle_id", "driver_partner_id"]], on="vehicle_id")
    busy = dict(zip(engaged.driver_partner_id, engaged.trip_id))
    vlist = veh.groupby("driver_partner_id")[["vehicle_id", "vehicle_type", "vehicle_status"]].apply(
        lambda g: g.to_dict("records")).to_dict()
    for d in drivers.itertuples():
        trip = busy.get(d.driver_partner_id)
        yield "driver", k.driver(d.driver_partner_id), {
            "driver_status": d.driver_status, "available": d.driver_status == "ACTIVE" and trip is None,
            "current_trip_id": trip, "vehicles": vlist.get(d.driver_partner_id, [])}, "driver"

    rep, ds = reports.build()
    cols = ["total_accepted", "total_rejected", "total_expired", "acceptance_rate_percentage",
            "total_completed_trips", "total_duration_minutes", "avg_duration_per_trip_minutes"]
    for drv, row in ds[cols].iterrows():
        yield "driver_stats", k.driver_stats(drv), {c: none(row[c]) for c in cols}, "driver_stats"
    for name, value in rep.items():
        yield "report", k.report(name), value, "report"

    # hot orders: running now or created within the last `hours`
    so = rd("service_orders")
    created = pd.to_datetime(so.created_at, utc=True)
    hot = so[so.order_status.isin(RUNNING) | (created >= SNAPSHOT - pd.Timedelta(hours=hours))]
    ids = set(hot.order_id)
    sub = {}
    for kind, cols in (("food", ["preparation_status", "delivery_notes", "merchant_outlet_id"]),
                       ("delivery", ["package_description", "package_weight", "recipient_name",
                                     "recipient_contact"]),
                       ("mobility", ["ride_type", "passenger_count", "pickup_notes"])):
        t = rd(f"{kind}_orders")
        for r in t[t.order_id.isin(ids)].itertuples(index=False):
            sub[r.order_id] = {"order_type": kind, **{c: none(getattr(r, c)) for c in cols}}
    pays = rd("payments")
    pays = pays[pays.order_id.isin(ids)].set_index("order_id")
    trips = rd("trips")
    trips = trips[trips.order_id.isin(ids)]
    trip_of = trips.groupby("order_id").trip_id.first().to_dict()
    for r in hot.itertuples(index=False):
        yield "order", k.order(r.order_id), {
            "order_id": r.order_id, "created_at": r.created_at, "order_status": r.order_status,
            "total_amount": r.total_amount, "customer_id": r.customer_id, "trip_id": trip_of.get(r.order_id),
            **sub.get(r.order_id, {})}, "order"
    for oid, p in pays.iterrows():
        yield "payment", k.payment(oid), {"payment_id": p.payment_id, "payment_method": p.payment_method,
                                          "payment_status": p.payment_status, "amount": p.amount,
                                          "paid_at": none(p.paid_at)}, "payment"
    a = asg[asg.trip_id.isin(set(trips.trip_id))].merge(veh[["vehicle_id", "driver_partner_id"]], on="vehicle_id")
    a = a.drop(columns="vehicle_id").astype(object).where(a.notna(), None)
    by_trip = {t: g.drop(columns="trip_id").to_dict("records") for t, g in a.groupby("trip_id")}
    for r in trips.itertuples(index=False):
        yield "trip", k.trip(r.trip_id), {
            "trip_id": r.trip_id, "order_id": r.order_id, "trip_status": r.trip_status,
            "started_at": none(r.started_at), "completed_at": none(r.completed_at),
            "start_location_id": r.start_location_id, "end_location_id": r.end_location_id,
            "assignments": by_trip.get(r.trip_id, [])}, "trip"

    # PF-09 counters: trips started per area on the snapshot day (2026-10-31)
    day = trips[trips.started_at.fillna("").str.startswith("2026-10-31")].merge(
        locs[["location_id", "service_area_id"]], left_on="start_location_id", right_on="location_id")
    for sar, n in day.groupby("service_area_id").size().items():
        yield "counter", k.area_trip_counter(sar, "20261031"), int(n), "counter"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ttl", choices=["policy", "none"], default="policy",
                    help="none = exptime 0, used for the equivalence check so short TTLs do not expire mid-check")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--flush", action="store_true", help="flush_all before loading")
    ap.add_argument("--out", type=Path, default=ROOT / "benchmarks" / "published" / "memcached_load.json")
    args = ap.parse_args(argv)

    c = k.client()
    if args.flush:
        c.flush_all()
    t0 = time.perf_counter()
    families, batch, batch_ttl = {}, {}, None

    def flush():
        failed = c.set_many(batch, expire=batch_ttl, noreply=False)
        if failed:
            raise RuntimeError(f"set_many failed for {len(failed)} keys, e.g. {failed[:3]}")
        batch.clear()

    payload_bytes = 0
    for fam, key, value, ttl_fam in build_items(args.hours):
        ttl = 0 if args.ttl == "none" else k.TTL[ttl_fam]
        raw = str(value).encode() if fam == "counter" else k.dumps(value)
        if ttl != batch_ttl and batch:
            flush()
        batch_ttl = ttl
        batch[key] = raw
        payload_bytes += len(raw)
        f = families.setdefault(fam, {"keys": 0, "bytes": 0, "max_bytes": 0, "ttl_s": ttl})
        f["keys"] += 1
        f["bytes"] += len(raw)
        f["max_bytes"] = max(f["max_bytes"], len(raw))
        if len(batch) >= BATCH:
            flush()
    if batch:
        flush()
    total_s = time.perf_counter() - t0
    st = k.stats(c)
    out = {
        "ttl_mode": args.ttl, "hot_window_hours": args.hours, "total_seconds": round(total_s, 2),
        "keys": sum(f["keys"] for f in families.values()), "payload_bytes": payload_bytes,
        "families": families,
        "memcached": {x: st.get(x) for x in ("version", "curr_items", "bytes", "limit_maxbytes", "evictions",
                                              "total_items", "curr_connections")},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
