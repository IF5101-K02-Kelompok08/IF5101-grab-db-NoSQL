"""Dependency-free smoke tests for CouchDB and Memcached.

Run only against isolated development/CI services. The CouchDB test creates a
uniquely named temporary database and deletes it when finished.
"""

import base64
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

COUCHDB_URL = os.getenv("COUCHDB_URL", "http://127.0.0.1:5984").rstrip("/")
COUCHDB_USER = os.environ["COUCHDB_USER"]
COUCHDB_PASSWORD = os.environ["COUCHDB_PASSWORD"]
MEMCACHED_HOST = os.getenv("MEMCACHED_HOST", "127.0.0.1")
MEMCACHED_PORT = int(os.getenv("MEMCACHED_PORT", "11211"))


def couch_request(method, path, body=None, expected=(200,)):
    auth = base64.b64encode(f"{COUCHDB_USER}:{COUCHDB_PASSWORD}".encode()).decode()
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{COUCHDB_URL}{path}",
        data=payload,
        method=method,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status in expected, (method, path, response.status)
        content = response.read()
        return json.loads(content) if content else {}


def wait_for_service(name, probe, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            probe()
            print(f"[READY] {name}", flush=True)
            return
        except (OSError, ValueError, AssertionError, urllib.error.HTTPError):
            time.sleep(2)
    raise RuntimeError(f"{name} did not become ready within {timeout}s")


def memcached_command(command):
    with socket.create_connection((MEMCACHED_HOST, MEMCACHED_PORT), timeout=5) as s:
        s.settimeout(5)
        f = s.makefile("rwb")
        f.write(command)
        f.flush()
        # Commands used below all terminate with a single line or END marker.
        first = f.readline()
        if first.startswith(b"VALUE "):
            data = f.readline()
            end = f.readline()
            assert end == b"END\r\n", end
            return first + data + end
        return first


def test_couchdb():
    db = f"if5101_smoke_{uuid.uuid4().hex[:12]}"
    db_path = "/" + urllib.parse.quote(db)
    created = False
    try:
        result = couch_request("PUT", db_path, expected=(201, 202))
        assert result.get("ok") is True
        created = True

        doc_path = db_path + "/smoke_trip"
        result = couch_request("PUT", doc_path, {"type": "trip", "status": "SEARCHING"}, (201, 202))
        assert result.get("ok") is True
        document = couch_request("GET", doc_path)
        assert document["status"] == "SEARCHING"

        result = couch_request(
            "PUT", doc_path,
            {"_id": "smoke_trip", "_rev": document["_rev"], "type": "trip", "status": "COMPLETED"},
            (201, 202),
        )
        assert result.get("ok") is True
        document = couch_request("GET", doc_path)
        assert document["status"] == "COMPLETED"

        query_rev = urllib.parse.quote(document["_rev"], safe="")
        result = couch_request("DELETE", f"{doc_path}?rev={query_rev}", expected=(200, 202))
        assert result.get("ok") is True
        print("[PASS] CouchDB: create, read, update, delete document", flush=True)
    finally:
        if created:
            couch_request("DELETE", db_path, expected=(200, 202))
            print("[CLEANUP] CouchDB temporary database deleted", flush=True)


def test_memcached():
    key = f"if5101_smoke_{uuid.uuid4().hex[:12]}"
    value = b"SEARCHING"
    header = f"set {key} 0 60 {len(value)}\r\n".encode("ascii")
    assert memcached_command(header + value + b"\r\n") == b"STORED\r\n"
    assert f"VALUE {key} 0 {len(value)}\r\n".encode() in memcached_command(f"get {key}\r\n".encode())
    assert b"SEARCHING\r\n" in memcached_command(f"get {key}\r\n".encode())

    updated = b"COMPLETED"
    header = f"set {key} 0 60 {len(updated)}\r\n".encode("ascii")
    assert memcached_command(header + updated + b"\r\n") == b"STORED\r\n"
    assert b"COMPLETED\r\n" in memcached_command(f"get {key}\r\n".encode())

    assert memcached_command(f"delete {key}\r\n".encode()) == b"DELETED\r\n"
    assert memcached_command(f"get {key}\r\n".encode()) == b"END\r\n"
    print("[PASS] Memcached: set, get, update, delete", flush=True)


def check_memcached_ready():
    response = memcached_command(b"version\r\n")
    assert response.startswith(b"VERSION "), response


def main():
    wait_for_service("CouchDB", lambda: couch_request("GET", "/_up"))
    wait_for_service("Memcached", check_memcached_ready)
    test_couchdb()
    test_memcached()
    print("[PASS] All smoke tests passed", flush=True)


if __name__ == "__main__":
    main()
