"""Cache hit/miss rate simulation (metodologi_benchmark.md section 7).

Replays a synthetic stream of order-status lookups against the loaded working set:
80% of requests target recent orders (last 24 h, cached), 20% target older orders
(history, not cached). Misses are filled read-through with the order TTL, so a repeated
old order becomes a hit (cache-aside). Uses memcached's own get_hits/get_misses counters.
Usage: uv run --no-sync python -m memcached.hit_rate --requests 20000
"""
import argparse
import json
import random
import time
from pathlib import Path

from memcached import schema as k

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=20000)
    ap.add_argument("--hot-share", type=float, default=0.8)
    args = ap.parse_args(argv)
    rng, c = random.Random(42), k.client()
    # hot orders = last 50k ids (created in the final day before the snapshot)
    hot = [f"ORD{n:07d}" for n in range(1_950_001, 2_000_001)]
    old_pool = [f"ORD{n:07d}" for n in rng.sample(range(1, 1_900_000), 5000)]  # repeated history lookups
    before = k.stats(c)
    lat_hit, lat_miss, fills = [], [], 0
    for _ in range(args.requests):
        oid = rng.choice(hot) if rng.random() < args.hot_share else rng.choice(old_pool)
        key = k.order(oid)
        t0 = time.perf_counter_ns()
        v = c.get(key)
        dt = (time.perf_counter_ns() - t0) / 1e6
        if v is None:
            lat_miss.append(dt)
            # read-through fill: in production the value comes from CouchDB; here a stub doc
            c.set(key, k.dumps({"order_id": oid, "source": "system_of_record"}), expire=k.TTL["order"])
            fills += 1
        else:
            lat_hit.append(dt)
    after = k.stats(c)
    hits = int(after["get_hits"]) - int(before["get_hits"])
    misses = int(after["get_misses"]) - int(before["get_misses"])
    # remove the stub fills so the dataset stays equivalent to the canonical one
    for i in range(0, len(old_pool), 500):
        c.delete_many([k.order(o) for o in old_pool[i:i + 500]])
    out = {"requests": args.requests, "hot_share": args.hot_share, "server_get_hits": hits,
           "server_get_misses": misses, "hit_rate": round(hits / (hits + misses), 4),
           "miss_rate": round(misses / (hits + misses), 4), "read_through_fills": fills,
           "mean_hit_ms": round(sum(lat_hit) / len(lat_hit), 4),
           "mean_miss_ms": round(sum(lat_miss) / len(lat_miss), 4)}
    (ROOT / "benchmarks" / "published" / "memcached_hit_rate.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
