"""Precompute analytic results (R1-R9 of Part 2) from the canonical CSVs with pandas.

In the key-value design these results are produced by a batch job and cached as
report:<name> values, because Memcached cannot scan or aggregate (PF-08..PF-10).
"""
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data" / "canonical"


def read(table, cols=None):
    return pd.read_csv(DATA / f"{table}.csv", usecols=cols, keep_default_na=True)


def records(df):
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict("records")


def driver_stats(asg, veh, trips):
    """Per-driver R1 + R2 figures: accepted/rejected/expired, rate, completed trips, duration."""
    a = asg.merge(veh[["vehicle_id", "driver_partner_id"]], on="vehicle_id")
    s = a.groupby("driver_partner_id").assignment_status.value_counts().unstack(fill_value=0)
    for col in ("ACCEPTED", "REJECTED", "EXPIRED"):
        if col not in s:
            s[col] = 0
    out = pd.DataFrame({"total_accepted": s.ACCEPTED, "total_rejected": s.REJECTED, "total_expired": s.EXPIRED})
    decided = out.total_accepted + out.total_rejected
    out["acceptance_rate_percentage"] = (out.total_accepted / decided.where(decided > 0) * 100).round(2)
    t = trips[trips.trip_status == "COMPLETED"][["trip_id", "started_at", "completed_at"]]
    acc = a[a.assignment_status == "ACCEPTED"][["trip_id", "driver_partner_id"]].merge(t, on="trip_id")
    acc["minutes"] = (pd.to_datetime(acc.completed_at) - pd.to_datetime(acc.started_at)).dt.total_seconds() / 60
    g = acc.groupby("driver_partner_id").minutes.agg(["count", "sum", "mean"])
    out["total_completed_trips"] = g["count"].reindex(out.index).fillna(0).astype(int)
    out["total_duration_minutes"] = g["sum"].reindex(out.index).round(2)
    out["avg_duration_per_trip_minutes"] = g["mean"].reindex(out.index).round(2)
    return out


def build(top_n=100):
    """Return {report_name: value} plus the per-driver stats frame."""
    areas = read("service_areas")
    locs = read("locations", ["location_id", "service_area_id"])
    outlets = read("merchant_outlets")
    drivers = read("driver_partners", ["driver_partner_id", "full_name"])
    veh = read("vehicles", ["vehicle_id", "vehicle_type", "driver_partner_id"])
    trips = read("trips")
    asg = read("driver_assignments", ["assignment_id", "assignment_status", "trip_id", "vehicle_id"])
    orders = read("service_orders", ["order_id", "created_at", "order_status"])
    pays = read("payments")
    food = read("food_orders", ["order_id", "merchant_outlet_id"])
    rep = {}

    # R1 acceptance rate (>= 5 decisions), R2 top 10 by duration
    ds = driver_stats(asg, veh, trips).join(drivers.set_index("driver_partner_id"))
    r1 = ds[(ds.total_accepted + ds.total_rejected) >= 5].sort_values(
        ["acceptance_rate_percentage", "total_accepted"], ascending=False)
    cols1 = ["full_name", "total_accepted", "total_rejected", "total_expired", "acceptance_rate_percentage"]
    rep["r1_acceptance_rate"] = {"row_count": len(r1), "top": records(r1[cols1].head(top_n).reset_index())}
    r2 = ds[ds.total_completed_trips > 0].sort_values("total_duration_minutes", ascending=False).head(10)
    rep["r2_top10_duration"] = records(r2[["full_name", "total_completed_trips", "total_duration_minutes",
                                           "avg_duration_per_trip_minutes"]].reset_index())

    # R3 vehicle type x start hour
    acc = asg[asg.assignment_status == "ACCEPTED"].merge(
        trips[trips.trip_status.isin(["COMPLETED", "IN_PROGRESS"])][["trip_id", "started_at"]], on="trip_id"
    ).merge(veh[["vehicle_id", "vehicle_type"]], on="vehicle_id")
    acc["trip_hour"] = pd.to_datetime(acc.started_at).dt.hour
    r3 = acc.groupby(["vehicle_type", "trip_hour"]).agg(total_trips=("trip_id", "count"),
                                                       unique_vehicles_used=("vehicle_id", "nunique"))
    rep["r3_vehicle_hourly"] = records(r3.reset_index().sort_values(["trip_hour", "total_trips"],
                                                                    ascending=[True, False]))

    # R4 trips with > 2 attempts and at least one ACCEPTED
    c = asg.groupby("trip_id").agg(total_attempts=("assignment_id", "count"),
                                   has_accepted=("assignment_status", lambda s: (s == "ACCEPTED").sum()))
    r4 = trips.merge(c[(c.total_attempts > 2) & (c.has_accepted > 0)].reset_index(), on="trip_id")
    r4 = r4.sort_values(["total_attempts", "started_at"], ascending=[False, False])
    rep["r4_churn_trips"] = {"row_count": len(r4), "top": records(
        r4[["trip_id", "order_id", "trip_status", "total_attempts", "started_at", "completed_at"]].head(top_n))}

    # R5 trips per service area (by start location)
    t_area = trips.merge(locs, left_on="start_location_id", right_on="location_id")
    r5 = t_area.groupby("service_area_id").size().rename("total_trips").reset_index().merge(areas)
    rep["r5_trips_per_area"] = records(r5.sort_values("total_trips", ascending=False)[
        ["service_area_id", "area_name", "city", "total_trips"]])

    # R6 revenue and refunds per area (first trip per order)
    first = trips.sort_values("trip_id").drop_duplicates("order_id")[["order_id", "start_location_id"]]
    p6 = pays[pays.payment_status.isin(["PAID", "REFUNDED"])].merge(first, on="order_id").merge(
        locs, left_on="start_location_id", right_on="location_id").merge(areas, on="service_area_id")
    g6 = p6.groupby(["service_area_id", "city", "area_name"])
    r6 = pd.DataFrame({
        "settled_payments": g6.size(),
        "net_revenue": g6.apply(lambda d: d.amount[d.payment_status == "PAID"].sum()),
        "refunded_amount": g6.apply(lambda d: d.amount[d.payment_status == "REFUNDED"].sum()),
        "refund_rate_pct": g6.apply(lambda d: round(100 * (d.payment_status == "REFUNDED").mean(), 2)),
    }).reset_index().sort_values("net_revenue", ascending=False)
    rep["r6_revenue_per_area"] = records(r6.round(2))

    # R7 monthly payment summary per method
    p7 = pays.merge(orders[["order_id", "created_at"]], on="order_id")
    created = pd.to_datetime(p7.created_at, utc=True)
    p7["order_month"] = created.dt.strftime("%Y-%m-01")
    p7["settle_min"] = (pd.to_datetime(p7.paid_at, utc=True) - created).dt.total_seconds() / 60
    rows = []
    for (m, meth), d in p7.groupby(["order_month", "payment_method"]):
        st = d.payment_status
        done = (st != "PENDING").sum()
        rows.append({"order_month": m, "payment_method": meth, "total_payments": len(d),
                     "paid": int((st == "PAID").sum()), "refunded": int((st == "REFUNDED").sum()),
                     "failed": int((st == "FAILED").sum()), "pending": int((st == "PENDING").sum()),
                     "paid_pct": round(100 * (st == "PAID").sum() / done, 2) if done else None,
                     "net_revenue": round(float(d.amount[st == "PAID"].sum()), 2),
                     "avg_settle_minutes": round(float(d.settle_min.mean()), 1)})
    rep["r7_monthly_payments"] = sorted(rows, key=lambda r: (r["order_month"], -r["net_revenue"]))

    # R8 top 10 outlets by food revenue (COMPLETED orders)
    o_area = outlets.merge(read("locations", ["location_id", "service_area_id"]), on="location_id").merge(
        areas, on="service_area_id")
    f8 = food.merge(orders[orders.order_status == "COMPLETED"][["order_id"]], on="order_id").merge(
        pays[["order_id", "amount", "payment_status"]], on="order_id")
    g8 = f8.groupby("merchant_outlet_id")
    r8 = pd.DataFrame({
        "completed_orders": g8.size(),
        "net_revenue": g8.apply(lambda d: d.amount[d.payment_status == "PAID"].sum()),
        "avg_order_value": g8.amount.mean().round(2),
        "refund_rate_pct": g8.apply(lambda d: round(100 * (d.payment_status == "REFUNDED").mean(), 2)),
    }).reset_index().merge(o_area, on="merchant_outlet_id")
    r8 = r8.sort_values(["net_revenue", "merchant_outlet_id"], ascending=[False, True]).head(10)
    rep["r8_top10_outlets"] = records(r8[["merchant_outlet_id", "outlet_name", "outlet_status", "city",
                                          "area_name", "completed_orders", "net_revenue", "avg_order_value",
                                          "refund_rate_pct"]])

    # R9 outlet supply vs food demand, October 2026
    ao = o_area.groupby("service_area_id").agg(total_outlets=("merchant_outlet_id", "count"),
                                               open_outlets=("outlet_status", lambda s: (s == "OPEN").sum()))
    oc = orders[(orders.created_at >= "2026-10-01") & (orders.created_at < "2026-11-01")][["order_id"]]
    od = food.merge(oc, on="order_id").merge(o_area[["merchant_outlet_id", "service_area_id"]],
                                             on="merchant_outlet_id").groupby("service_area_id").size()
    r9 = areas.set_index("service_area_id").join(ao).join(od.rename("food_orders")).fillna(
        {"total_outlets": 0, "open_outlets": 0, "food_orders": 0})
    r9["orders_per_open_outlet"] = (r9.food_orders / r9.open_outlets.where(r9.open_outlets > 0)).round(2)
    r9 = r9.reset_index().sort_values(["orders_per_open_outlet", "city", "area_name"],
                                      ascending=[False, True, True], na_position="last")
    rep["r9_supply_demand_oct"] = records(r9[["city", "area_name", "area_status", "total_outlets",
                                              "open_outlets", "food_orders", "orders_per_open_outlet"]])
    return rep, ds
