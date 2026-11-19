
"""Example: read trip status from a temporary CouchDB database."""

import os
from types import SimpleNamespace
from uuid import uuid4

import requests

BASE = os.getenv(
    "COUCHDB_URL", "http://localhost:5984"
).rstrip("/")

AUTH = (
    os.getenv("COUCHDB_USER", "admin"),
    os.environ.get("COUCHDB_PASSWORD", "password"),
)


def call(method, path, **kwargs):
    response = requests.request(
        method,
        BASE + path,
        auth=AUTH,
        timeout=15,
        **kwargs,
    )
    response.raise_for_status()
    return response.json()


def setup():
    db = "benchmark-" + uuid4().hex[:18]
    doc_id = "TRP0000001"

    call("PUT", "/" + db)

    call(
        "PUT",
        f"/{db}/{doc_id}",
        json={
            "trip_id": doc_id,
            "trip_status": "SEARCHING",
        },
    )

    return {"db": db, "doc_id": doc_id}


def read(ctx):
    return call(
        "GET",
        f"/{ctx['db']}/{ctx['doc_id']}",
    )


def validate(ctx, output):
    return (
        output["trip_id"] == ctx["doc_id"]
        and output["trip_status"] == "SEARCHING"
    )


def teardown(ctx):
    call("DELETE", "/" + ctx["db"])


def create_scenarios():
    return [
        SimpleNamespace(
            operation_id="PF-03-Q01",
            requirement_ids=["PF-03", "PNF-03"],
            dbms="couchdb",
            operation="read_trip_status",
            scenario="read",
            setup=setup,
            execute=read,
            validate=validate,
            teardown=teardown,
        )
    ]
