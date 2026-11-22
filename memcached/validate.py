"""Validate the Memcached working set against the canonical CSVs (Bab 3.1).

Checks (load must use --ttl none so nothing expires mid-check):
  1. expected key count per family (recomputed from CSV) is present (get_many)
  2. field-level equality on a random sample of orders, payments, trips, drivers
  3. R10 page 1 for Tebet equals the Part 2 SQL semantics recomputed in pandas
  4. report consistency: sum(R5 trips) == trips rows, R2 has 10 rows
Usage: uv run --no-sync python -m memcached.validate
"""
import json
import random
from pathlib import Path

import pandas as pd

from memcached import load as L, reports, schema as k

ROOT = Path(__file__).resolve().parent.parent


def present(c, keys):
    found = 0
    for i in range(0, len(keys), 500):
        found += len(c.get_many(keys[i:i + 500]))
    return found


def main():
    c, rd, rng = k.client(), reports.read, random.Random(42)
    res = {}
    so = rd("service_orders")
    created = pd.to_datetime(so.created_at, utc=True)
    hot = so[so.order_status.isin(L.RUNNING) | (created >= L.SNAPSHOT - pd.Timedelta(hours=24))]
    trips = rd("trips")
    hot_trips = trips[trips.order_id.isin(set(hot.order_id))]
    pays = rd("payments")
    hot_pays = pays[pays.order_id.isin(set(hot.order_id))]
    areas, drivers = rd("service_areas"), rd("driver_partners")

    expected = {
        "area": [k.area(x) for x in areas.service_area_id],
        "driver": [k.driver(x) for x in drivers.driver_partner_id],
        "order": [k.order(x) for x in hot.order_id],
        "payment": [k.payment(x) for x in hot_pays.order_id],
        "trip": [k.trip(x) for x in hot_trips.trip_id],
        "report": [k.report(x) for x in ("r1_acceptance_rate", "r2_top10_duration", "r3_vehicle_hourly",
                                          "r4_churn_trips", "r5_trips_per_area", "r6_revenue_per_area",
                                          "r7_monthly_payments", "r8_top10_outlets", "r9_supply_demand_oct")],
    }
    res["counts"] = {f: {"expected": len(v), "found": present(c, v)} for f, v in expected.items()}

    # field-level sample comparison
    mism = []
    for r in hot.sample(1000, random_state=42).itertuples(index=False):
        v = k.loads(c.get(k.order(r.order_id)))
        if not v or (v["order_status"], v["customer_id"], v["created_at"], float(v["total_amount"])) != (
                r.order_status, r.customer_id, r.created_at, float(r.total_amount)):
            mism.append(r.order_id)
    for r in hot_pays.sample(1000, random_state=42).itertuples(index=False):
        v = k.loads(c.get(k.payment(r.order_id)))
        if not v or (v["payment_id"], v["payment_status"], float(v["amount"])) != (
                r.payment_id, r.payment_status, float(r.amount)):
            mism.append(r.payment_id)
    for r in hot_trips.sample(1000, random_state=42).itertuples(index=False):
        v = k.loads(c.get(k.trip(r.trip_id)))
        if not v or (v["trip_status"], v["order_id"], v["start_location_id"]) != (
                r.trip_status, r.order_id, r.start_location_id):
            mism.append(r.trip_id)
    for r in drivers.sample(1000, random_state=42).itertuples(index=False):
        v = k.loads(c.get(k.driver(r.driver_partner_id)))
        if not v or v["driver_status"] != r.driver_status:
            mism.append(r.driver_partner_id)
    res["sample_field_check"] = {"checked": 4000, "mismatches": len(mism), "examples": mism[:5]}

    # R10 Tebet page 1
    tebet = areas[(areas.area_name == "Tebet") & (areas.city == "Jakarta")].service_area_id.iloc[0]
    o = rd("merchant_outlets").merge(rd("locations"), on="location_id")
    o = o[(o.service_area_id == tebet) & (o.outlet_status == "OPEN")].sort_values(
        ["outlet_name", "merchant_outlet_id"]).head(20)
    page = k.loads(c.get(k.open_outlets(tebet, 1)))
    res["r10_tebet_page1"] = {"service_area_id": tebet, "expected_ids": list(o.merchant_outlet_id)[:3],
                              "match": [x["merchant_outlet_id"] for x in page["items"]] == list(o.merchant_outlet_id),
                              "total_open_outlets": page["total"], "pages": page["pages"]}

    r5 = k.loads(c.get(k.report("r5_trips_per_area")))
    r2 = k.loads(c.get(k.report("r2_top10_duration")))
    res["report_consistency"] = {"r5_sum_trips": sum(x["total_trips"] for x in r5), "trips_rows": len(trips),
                                 "r2_rows": len(r2)}
    ok = (all(v["expected"] == v["found"] for v in res["counts"].values()) and not mism
          and res["r10_tebet_page1"]["match"] and res["report_consistency"]["r5_sum_trips"] == len(trips)
          and len(r2) == 10)
    res["passed"] = ok
    out = ROOT / "benchmarks" / "published" / "memcached_validation.json"
    out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
