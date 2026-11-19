
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("data/canonical")
SEED = 42


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b""
        ):
            digest.update(chunk)

    return digest.hexdigest()


def main():
    if not DATA_DIR.exists():
        raise FileNotFoundError(
            f"Dataset directory not found: {DATA_DIR}"
        )

    tables = {}

    for path in sorted(DATA_DIR.glob("*.csv")):
        with path.open(
            "r", encoding="utf-8", newline=""
        ) as file:
            reader = csv.reader(file)
            columns = next(reader)
            row_count = sum(1 for _ in reader)

        tables[path.stem] = {
            "rows": row_count,
            "columns": columns,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }

    if not tables:
        raise RuntimeError("No CSV files found")

    manifest = {
        "dataset_name": "IF5101 Grab NoSQL Canonical Dataset",
        "source": "Part 2 synthetic data generator",
        "profile": "nosql",
        "random_seed": SEED,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "tables": tables,
    }

    output = DATA_DIR / "manifest.json"
    output.write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    print(f"Manifest generated: {output}")

    for name, info in tables.items():
        print(f"{name}: {info['rows']:,} rows")


if __name__ == "__main__":
    main()
