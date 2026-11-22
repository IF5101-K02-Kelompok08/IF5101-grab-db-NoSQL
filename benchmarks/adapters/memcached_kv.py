"""Memcached benchmark scenarios for the essential key-value operations (Bab 3.2).

Each scenario maps to PF/PNF ids from Bab 1.2. Requires the working set loaded by
`python -m memcached.load`. Mutating scenarios work on copies under a `bench:` key
prefix (or restore the original value in teardown) so every run starts equal.
Run:
  uv run --no-sync python -m benchmarks.run --adapter benchmarks.adapters.memcached_kv \
      --output-dir benchmarks/results/memcached
"""
import time
from types import SimpleNamespace
from uuid import uuid4

from memcached import schema as k

_cache = {}


def c():
    if "c" not in _cache:
        _cache["c"] = k.client()
    return _cache["c"]


def sample_ids():
    """Pick existing ids from the loaded set (stable across runs)."""
    if "ids" not in _cache:
        cl = c()
        r5 = k.loads(cl.get(k.report("r5_trips_per_area")))
        r2 = k.loads(cl.get(k.report("r2_top10_duration")))
        r4 = k.loads(cl.get(k.report("r4_churn_trips")))
        tebet = next(a["service_area_id"] for a in r5 if a["area_name"] == "Tebet")
        # a running order: last ids of the dataset are created right before the snapshot
        order_id = next(f"ORD{n:07d}" for n in range(2_000_000, 1_990_000, -1)
                        if (o := k.loads(cl.get(k.order(f"ORD{n:07d}")))) and o["trip_id"]
                        and o["order_status"] == "IN_PROGRESS")
        order = k.loads(cl.get(k.order(order_id)))
        drivers = [f"DRV{n:07d}" for n in range(1, 51)]
        _cache["ids"] = dict(tebet=tebet, order=order_id, trip=order["trip_id"],
                             driver=r2[0]["driver_partner_id"], drivers=drivers, churn=r4["row_count"])
    return _cache["ids"]


def scenario(op_id, reqs, operation, scen, setup, execute, validate, teardown=lambda ctx: None, hit=None):
    return SimpleNamespace(operation_id=op_id, requirement_ids=reqs, dbms="memcached", operation=operation,
                           scenario=scen, cache_hit=hit, setup=setup, execute=execute, validate=validate,
                           teardown=teardown)


def copy_key(src):
    """Copy a real value to a disposable bench key so mutations never touch the dataset."""
    dst = f"bench:{uuid4().hex[:12]}:{src}"
    assert c().set(dst, c().get(src), expire=300, noreply=False)
    return dst


def create_scenarios():
    ids = sample_ids()
    cl = c()
    s = []

    # --- PF-01 / PF-03 / PNF-03: point reads of hot operational data -------------------------
    s.append(scenario("MC-01", ["PF-01", "PNF-03"], "get_order", "cache_hit",
                      lambda: k.order(ids["order"]), lambda key: k.loads(cl.get(key)),
                      lambda key, v: v is not None and v["order_id"] == ids["order"], hit=True))
    s.append(scenario("MC-02", ["PF-01", "PNF-03"], "get_order", "cache_miss",
                      lambda: k.order("ORD0000001"), lambda key: cl.get(key),  # 6-month-old order: not cached
                      lambda key, v: v is None, hit=False))
    s.append(scenario("MC-03", ["PF-03", "PNF-03"], "get_trip_status", "cache_hit",
                      lambda: k.trip(ids["trip"]), lambda key: k.loads(cl.get(key))["trip_status"],
                      lambda key, v: v == "IN_PROGRESS", hit=True))

    def fill_setup():
        key = f"bench:{uuid4().hex[:12]}:order"
        return {"key": key, "doc": k.loads(cl.get(k.order(ids["order"])))}

    def fill_exec(ctx):  # cache-aside: miss -> (source of record) -> set
        v = cl.get(ctx["key"])
        if v is None:
            cl.set(ctx["key"], k.dumps(ctx["doc"]), expire=k.TTL["order"], noreply=False)
            return {"filled": True, "value": ctx["doc"]}
        return {"filled": False, "value": k.loads(v)}
    s.append(scenario("MC-04", ["PF-01", "PNF-03"], "get_order_read_through", "cache_miss_fill", fill_setup,
                      fill_exec, lambda ctx, v: v["filled"] and cl.get(ctx["key"]) is not None,
                      lambda ctx: cl.delete(ctx["key"]), hit=False))

    # --- PF-03 / PNF-04: optimistic update of trip status with gets + cas --------------------
    def cas_setup():
        return {"key": copy_key(k.trip(ids["trip"]))}

    def cas_exec(ctx):
        raw, token = cl.gets(ctx["key"])
        doc = k.loads(raw)
        doc["trip_status"], doc["completed_at"] = "COMPLETED", "2026-11-01 00:05:00+00:00"
        return cl.cas(ctx["key"], k.dumps(doc), token, expire=k.TTL["trip"], noreply=False)
    s.append(scenario("MC-05", ["PF-03", "PNF-04"], "update_trip_status_cas", "update", cas_setup, cas_exec,
                      lambda ctx, v: v is True and k.loads(cl.get(ctx["key"]))["trip_status"] == "COMPLETED",
                      lambda ctx: cl.delete(ctx["key"])))

    def stale_setup():  # another writer changes the value after our gets -> cas must be rejected
        key = copy_key(k.trip(ids["trip"]))
        raw, token = cl.gets(key)
        cl.set(key, raw, expire=300)
        return {"key": key, "raw": raw, "token": token}
    s.append(scenario("MC-06", ["PF-03", "PNF-04"], "update_trip_status_cas", "cas_conflict", stale_setup,
                      lambda ctx: cl.cas(ctx["key"], ctx["raw"], ctx["token"], noreply=False),
                      lambda ctx, v: v is False, lambda ctx: cl.delete(ctx["key"])))

    # --- PF-02 / PNF-04: assignment lock, only one driver may win a trip ----------------------
    def lock_setup():
        return {"key": k.assign_lock(f"bench{uuid4().hex[:8]}")}

    def lock_exec(ctx):
        first = cl.add(ctx["key"], b"VEH0000001", expire=k.TTL["lock"], noreply=False)
        second = cl.add(ctx["key"], b"VEH0000002", expire=k.TTL["lock"], noreply=False)
        return [first, second]
    s.append(scenario("MC-07", ["PF-02", "PNF-04"], "acquire_assignment_lock", "add_twice", lock_setup,
                      lock_exec, lambda ctx, v: v == [True, False] and cl.get(ctx["key"]) == b"VEH0000001",
                      lambda ctx: cl.delete(ctx["key"])))

    # --- W3 equivalent: driver accepts -> trip ALLOCATED, driver unavailable (multi-key) -----
    def accept_setup():
        tkey = copy_key(k.trip(ids["trip"]))
        dkey = copy_key(k.driver(ids["driver"]))
        lkey = k.assign_lock(f"bench{uuid4().hex[:8]}")
        return {"t": tkey, "d": dkey, "l": lkey}

    def accept_exec(ctx):
        if not cl.add(ctx["l"], b"VEH-BENCH", expire=k.TTL["lock"], noreply=False):
            return False
        raw, tok = cl.gets(ctx["t"])
        t = k.loads(raw)
        t["trip_status"] = "ALLOCATED"
        d = k.loads(cl.get(ctx["d"]))
        d["available"], d["current_trip_id"] = False, t["trip_id"]
        ok = cl.cas(ctx["t"], k.dumps(t), tok, expire=k.TTL["trip"], noreply=False)
        cl.set(ctx["d"], k.dumps(d), expire=k.TTL["driver"], noreply=False)
        return ok
    s.append(scenario("MC-08", ["PF-02", "PF-03", "PNF-04"], "accept_assignment", "multi_key_update",
                      accept_setup, accept_exec,
                      lambda ctx, v: v is True and k.loads(cl.get(ctx["t"]))["trip_status"] == "ALLOCATED"
                      and k.loads(cl.get(ctx["d"]))["available"] is False,
                      lambda ctx: cl.delete_many([ctx["t"], ctx["d"], ctx["l"]])))

    # --- PF-02: batch availability check of 50 candidate drivers (multi-get) ------------------
    dkeys = [k.driver(d) for d in ids["drivers"]]
    s.append(scenario("MC-09", ["PF-02", "PNF-03"], "get_driver_availability_batch", "get_many_50",
                      lambda: dkeys, lambda keys: cl.get_many(keys),
                      lambda keys, v: len(v) == 50, hit=True))

    # --- PF-04: payment status PENDING -> PAID with cas ----------------------------------------
    def pay_setup():
        key = f"bench:{uuid4().hex[:12]}:payment"
        cl.set(key, k.dumps({"payment_id": "PAY9999101", "payment_method": "E_WALLET",
                             "payment_status": "PENDING", "amount": 20000.0, "paid_at": None}), expire=300)
        return {"key": key}

    def pay_exec(ctx):
        raw, tok = cl.gets(ctx["key"])
        p = k.loads(raw)
        if p["payment_status"] != "PENDING":
            return False
        p["payment_status"], p["paid_at"] = "PAID", "2026-11-01 00:01:00+00:00"
        return cl.cas(ctx["key"], k.dumps(p), tok, expire=k.TTL["payment"], noreply=False)
    s.append(scenario("MC-10", ["PF-04", "PNF-04"], "settle_payment_cas", "update", pay_setup, pay_exec,
                      lambda ctx, v: v is True and k.loads(cl.get(ctx["key"]))["payment_status"] == "PAID",
                      lambda ctx: cl.delete(ctx["key"])))

    # --- PF-05: R10 open outlets in Tebet, page 1 ----------------------------------------------
    s.append(scenario("MC-11", ["PF-05", "PNF-03"], "get_open_outlets_page", "cache_hit",
                      lambda: k.open_outlets(ids["tebet"], 1), lambda key: k.loads(cl.get(key)),
                      lambda key, v: v is not None and len(v["items"]) == 20, hit=True))

    # --- PF-06 / PF-07: invalidate outlet pages of an area after an outlet change --------------
    def inv_setup():
        keys = [copy_key(k.open_outlets(ids["tebet"], p)) for p in (1, 2, 3)]
        return {"keys": keys}
    s.append(scenario("MC-12", ["PF-06", "PF-07", "PNF-04"], "invalidate_area_outlets", "delete_many",
                      inv_setup, lambda ctx: cl.delete_many(ctx["keys"], noreply=False),
                      lambda ctx, v: v is True and not cl.get_many(ctx["keys"])))

    # --- PF-01 W1 equivalent: new order + payment + trip written together --------------------
    def w1_setup():
        p = f"bench:{uuid4().hex[:12]}"
        return {"keys": {f"{p}:order": k.dumps({"order_id": "ORD9999001", "order_status": "CONFIRMED",
                                                 "total_amount": 15000.0, "customer_id": "CUS0000001",
                                                 "order_type": "mobility", "trip_id": "TRP9999001"}),
                         f"{p}:payment": k.dumps({"payment_id": "PAY9999001", "payment_method": "CASH",
                                                   "payment_status": "PENDING", "amount": 15000.0}),
                         f"{p}:trip": k.dumps({"trip_id": "TRP9999001", "trip_status": "SEARCHING",
                                                "assignments": []})}}
    s.append(scenario("MC-13", ["PF-01"], "create_order_trip", "set_many_3", w1_setup,
                      lambda ctx: cl.set_many(ctx["keys"], expire=k.TTL["order"], noreply=False),
                      lambda ctx, v: v == [] and len(cl.get_many(list(ctx["keys"]))) == 3,
                      lambda ctx: cl.delete_many(list(ctx["keys"]))))

    # --- PF-08 / PF-10: precomputed analytics --------------------------------------------------
    s.append(scenario("MC-14", ["PF-08", "PNF-03"], "get_driver_stats", "cache_hit",
                      lambda: k.driver_stats(ids["driver"]), lambda key: k.loads(cl.get(key)),
                      lambda key, v: v is not None and v["total_completed_trips"] > 0, hit=True))
    s.append(scenario("MC-15", ["PF-10", "PNF-03"], "get_report_monthly_payments", "cache_hit",
                      lambda: k.report("r7_monthly_payments"), lambda key: k.loads(cl.get(key)),
                      lambda key, v: v is not None and sum(r["total_payments"] for r in v) == 2_000_000,
                      hit=True))
    s.append(scenario("MC-16", ["PF-08", "PNF-03"], "get_report_acceptance_rate", "cache_hit",
                      lambda: k.report("r1_acceptance_rate"), lambda key: k.loads(cl.get(key)),
                      lambda key, v: v is not None and len(v["top"]) == 100, hit=True))

    # --- PF-09: atomic counter of trips per area/day --------------------------------------------
    def ctr_setup():
        key = f"bench:{uuid4().hex[:12]}:counter"
        cl.set(key, b"100", expire=300)
        return {"key": key}
    s.append(scenario("MC-17", ["PF-09", "PNF-04"], "incr_area_trip_counter", "incr", ctr_setup,
                      lambda ctx: cl.incr(ctx["key"], 1, noreply=False),
                      lambda ctx, v: v == 101, lambda ctx: cl.delete(ctx["key"])))

    # --- TTL expiry (PNF-07): value must disappear after its TTL --------------------------------
    def ttl_setup():
        key = f"bench:{uuid4().hex[:12]}:ttl"
        cl.set(key, b"SEARCHING", expire=1, noreply=False)
        time.sleep(2.1)  # memcached clock has 1 s granularity
        return {"key": key}
    s.append(scenario("MC-18", ["PNF-07", "PF-03"], "get_after_ttl", "expired", ttl_setup,
                      lambda ctx: cl.get(ctx["key"]), lambda ctx, v: v is None, hit=False))
    return s
