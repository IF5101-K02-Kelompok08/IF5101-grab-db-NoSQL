
"""Example: Memcached GET for cache hit and cache miss."""

import os
from types import SimpleNamespace
from uuid import uuid4

from pymemcache.client.base import Client

HOST = os.getenv("MEMCACHED_HOST", "localhost")
PORT = int(os.getenv("MEMCACHED_PORT", "11211"))


def setup(hit):
    client = Client(
        (HOST, PORT),
        connect_timeout=5,
        timeout=5,
    )

    key = "if5101:benchmark:" + uuid4().hex

    if hit:
        assert client.set(
            key, b"SEARCHING", expire=120
        )

    return {"client": client, "key": key}


def execute(ctx):
    return ctx["client"].get(ctx["key"])


def teardown(ctx):
    try:
        ctx["client"].delete(ctx["key"])
    finally:
        ctx["client"].close()


def create_scenarios():
    scenarios = []

    for hit in (True, False):
        scenarios.append(
            SimpleNamespace(
                operation_id=(
                    "PF-03-Q02" if hit else "PF-03-Q03"
                ),
                requirement_ids=[
                    "PF-03", "PNF-03"
                ],
                dbms="memcached",
                operation="get_trip_status",
                scenario=(
                    "cache_hit" if hit else "cache_miss"
                ),
                cache_hit=hit,
                setup=lambda hit=hit: setup(hit),
                execute=execute,
                validate=lambda ctx, value, hit=hit: (
                    value == b"SEARCHING"
                    if hit else value is None
                ),
                teardown=teardown,
            )
        )

    return scenarios
