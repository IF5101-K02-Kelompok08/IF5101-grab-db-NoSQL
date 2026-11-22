"""Key naming and TTL policy for the Memcached key-value model (IF5101 Part 3).

Key pattern: <entity>:<id>[:<facet>]  (ASCII, no spaces, < 250 bytes).
Values are compact UTF-8 JSON, except counters (plain integers for incr/decr).
Memcached is a cache in front of the system of record (CouchDB), not a store
of the full history, so only the operational working set is loaded.
"""
import json
import os

from pymemcache.client.base import Client

# TTL in seconds per key family, with the requirement that motivates it.
TTL = {
    "order": 3600,           # PF-01: order screen is reopened for ~1 h after ordering
    "payment": 3600,         # PF-04: payment status checked during/after checkout
    "trip": 900,             # PF-03: active trip polled every few seconds
    "driver": 120,           # PF-02/03: availability changes often, short TTL
    "driver_stats": 86400,   # PF-08: daily batch aggregate
    "lock": 30,              # PF-02/PNF-04: = ASSIGNMENT_TIMEOUT of a driver offer
    "area": 86400,           # PF-06: master data, rarely changes
    "outlets": 600,          # PF-05: outlet list per area, 10 min staleness accepted
    "report": 3600,          # PF-09/10: precomputed analytics, hourly refresh
    "counter": 172800,       # PF-09: per-day counters, kept two days
}


def order(order_id): return f"order:{order_id}"
def payment(order_id): return f"payment:{order_id}"
def trip(trip_id): return f"trip:{trip_id}"
def driver(driver_id): return f"driver:{driver_id}"
def driver_stats(driver_id): return f"driver:{driver_id}:stats"
def assign_lock(trip_id): return f"lock:assign:{trip_id}"
def area(area_id): return f"area:{area_id}"
def open_outlets(area_id, page=1): return f"area:{area_id}:outlets:open:p{page}"
def report(name): return f"report:{name}"
def area_trip_counter(area_id, yyyymmdd): return f"counter:area:{area_id}:trips:{yyyymmdd}"


def dumps(value) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def loads(raw):
    return None if raw is None else json.loads(raw)


def client() -> Client:
    return Client((os.getenv("MEMCACHED_HOST", "127.0.0.1"), int(os.getenv("MEMCACHED_PORT", "11211"))),
                  connect_timeout=5, timeout=5, no_delay=True)


def stats(c: Client) -> dict:
    return {k.decode(): (v.decode() if isinstance(v, bytes) else v) for k, v in c.stats().items()}
