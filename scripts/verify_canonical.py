
"""
Verify canonical CSV files against the committed manifest.json.

Checks:
- Expected CSV files exist
- No unexpected CSV files
- Column names and order
- Row counts
- File sizes
- SHA-256 checksums

Usage:
    python scripts/verify_canonical.py

    python scripts/verify_canonical.py \
        --data-dir data/canonical

Exit codes:
    0 = all checks passed
    1 = verification failed
    2 = invalid arguments
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "canonical"

CHUNK_SIZE = 1024 * 1024


def calculate_sha256(path: Path) -> str:
    """Calculate checksum without loading the file into memory."""
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(
            lambda: stream.read(CHUNK_SIZE), b""
        ):
            digest.update(chunk)

    return digest.hexdigest()


def inspect_csv(path: Path) -> tuple[list[str], int]:
    """Read CSV header and count data records."""
    with path.open(
        "r", encoding="utf-8", newline=""
    ) as stream:
        reader = csv.reader(stream)

        try:
            columns = next(reader)
        except StopIteration:
            return [], 0

        rows = sum(1 for _ in reader)

    return columns, rows


def verify_table(
    name: str,
    expected: dict,
    data_dir: Path,
) -> list[str]:
    """Return a list of mismatches for one table."""
    path = data_dir / f"{name}.csv"
    errors = []

    if not path.is_file():
        return [f"Missing file: {path.name}"]

    actual_size = path.stat().st_size

    if actual_size != expected["size_bytes"]:
        errors.append(
            "File size mismatch: "
            f"expected {expected['size_bytes']:,}, "
            f"got {actual_size:,} bytes"
        )

    try:
        columns, rows = inspect_csv(path)

        if columns != expected["columns"]:
            errors.append(
                "Column mismatch:\n"
                f"      Expected: {expected['columns']}\n"
                f"      Actual:   {columns}"
            )

        if rows != expected["rows"]:
            errors.append(
                "Row count mismatch: "
                f"expected {expected['rows']:,}, "
                f"got {rows:,}"
            )

    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(f"Could not read CSV: {exc}")

    try:
        actual_hash = calculate_sha256(path)

        if actual_hash.lower() != expected["sha256"].lower():
            errors.append(
                "SHA-256 mismatch:\n"
                f"      Expected: {expected['sha256']}\n"
                f"      Actual:   {actual_hash}"
            )

    except OSError as exc:
        errors.append(f"Could not calculate SHA-256: {exc}")

    return errors


def verify_dataset(data_dir: Path) -> bool:
    """Verify the complete canonical dataset."""
    manifest_path = data_dir / "manifest.json"

    if not manifest_path.is_file():
        print(f"[ERROR] Manifest not found: {manifest_path}")
        return False

    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )

        tables = manifest["tables"]

        if not isinstance(tables, dict) or not tables:
            raise ValueError("Manifest contains no tables")

    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"[ERROR] Invalid manifest: {exc}")
        return False

    print("=" * 60)
    print("IF5101 CANONICAL DATASET VERIFICATION")
    print("=" * 60)
    print(f"Dataset : {manifest.get('dataset_name', 'Unknown')}")
    print(f"Profile : {manifest.get('profile', 'Unknown')}")
    print(f"Seed    : {manifest.get('random_seed', 'Unknown')}")
    print(f"Location: {data_dir}")
    print(f"Tables  : {len(tables)}")
    print()

    expected_files = {
        f"{name}.csv" for name in tables
    }

    actual_files = {
        path.name for path in data_dir.glob("*.csv")
        if path.is_file()
    }

    failures = 0

    for filename in sorted(actual_files - expected_files):
        print(f"[FAIL] Unexpected file: {filename}")
        failures += 1

    for name, expected in sorted(tables.items()):
        started = time.perf_counter()

        print(f"Checking {name}.csv ...", end=" ", flush=True)

        errors = verify_table(name, expected, data_dir)

        elapsed = time.perf_counter() - started

        if errors:
            failures += 1
            print(f"FAIL ({elapsed:.2f}s)")

            for error in errors:
                print(f"  - {error}")
        else:
            print(
                f"PASS "
                f"({expected['rows']:,} rows, "
                f"{elapsed:.2f}s)"
            )

    print()
    print("=" * 60)

    if failures == 0:
        print("[PASS] All canonical dataset checks passed.")
        print("All CSV files match the committed manifest.")
        return True

    print(f"[FAIL] {failures} verification issue(s) detected.")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify canonical CSV files against manifest.json"
        )
    )

    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing CSV files and manifest.json",
    )

    args = parser.parse_args()

    if not args.data_dir.is_dir():
        print(
            f"[ERROR] Dataset directory not found: "
            f"{args.data_dir}"
        )
        return 1

    return 0 if verify_dataset(args.data_dir) else 1


if __name__ == "__main__":
    sys.exit(main())
