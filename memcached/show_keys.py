from memcached import schema as k

c = k.client()
for key in ["area:SAR0000007", "order:ORD1997331", "payment:ORD1997331", "trip:TRP1955477",
            "driver:DRV0000001", "driver:DRV0000001:stats", "lock:assign:TRP0000001",
            "counter:area:SAR0000007:trips:20261031"]:
    v = c.get(key)
    print(key, "->", v.decode()[:150] if v else None)
