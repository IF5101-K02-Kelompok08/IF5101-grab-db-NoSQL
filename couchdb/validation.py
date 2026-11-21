"""Compare CouchDB results with the expected results from couchdb.reference."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from couchdb import model


@lru_cache(maxsize=4)
def load_reference(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(
            f'{path} not found. Compute the expected results first with: '
            'uv run --no-sync python -m couchdb.reference')
    return json.loads(path.read_text(encoding='utf-8'))


def _project(rows, keys):
    if isinstance(rows, dict):
        return {k: rows.get(k) for k in keys}
    return [{k: row.get(k) for k in keys} for row in rows]


def comparable(operation_id: str, actual, expected):
    """Bring a CouchDB result into the shape of the expected result."""
    if operation_id == 'PF-01-Q01':
        # The order document must hold exactly the original Part 2 rows.
        rows = model.order_rows(actual)
        return {table: values for table, values in rows.items() if values}
    if isinstance(expected, dict):
        return _project(actual, expected.keys())
    if expected and isinstance(expected[0], dict) and actual and isinstance(actual[0], dict):
        return _project(actual, expected[0].keys())
    return actual


def compare(operation_id: str, actual, expected) -> tuple[bool, str]:
    """Return (passed, message). Rows must match in content and in order."""
    got = json.loads(json.dumps(comparable(operation_id, actual, expected)))
    if got == expected:
        size = len(expected) if isinstance(expected, list) else 1
        return True, f'{size} rows match'
    if isinstance(got, list) and isinstance(expected, list):
        if len(got) != len(expected):
            return False, f'{len(got)} rows, expected {len(expected)}'
        for index, (a, b) in enumerate(zip(got, expected)):
            if a != b:
                return False, f'row {index}: got {a}, expected {b}'
    return False, f'got {str(got)[:300]}, expected {str(expected)[:300]}'
