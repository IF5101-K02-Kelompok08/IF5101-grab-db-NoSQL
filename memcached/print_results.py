import json

d = json.load(open("benchmarks/published/memcached/results.json"))
print(f"{'ID':<6} {'Status':<7} {'Run 1':>7} {'Run 2':>7} {'Run 3':>7} {'Rerata':>7} {'Median':>7}")
for r in d["results"]:
    r1, r2, r3 = r["measurements_ms"]
    print(f"{r['operation_id']:<6} {r['status']:<7} {r1:>7.2f} {r2:>7.2f} {r3:>7.2f} "
          f"{r['mean_ms']:>7.2f} {r['median_ms']:>7.2f}")
