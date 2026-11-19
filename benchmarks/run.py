
"""Generic NoSQL operation benchmark harness."""

import argparse
import csv
import importlib
import json
from pathlib import Path
import statistics
import time

CSV_FIELDS = (
    "operation_id",
    "dbms",
    "scenario",
    "dataset_size",
    "run",
    "execution_time_ms",
    "success",
    "validation_passed",
    "cache_hit",
)


def run_scenario(scenario, warmups, repeats, dataset_size):
    measurements = []
    details = []

    for run_index in range(-warmups + 1, repeats + 1):
        is_warmup = run_index <= 0

        context = scenario.setup()

        try:
            start = time.perf_counter_ns()
            result = scenario.execute(context)
            elapsed = (
                time.perf_counter_ns() - start
            ) / 1_000_000

            valid = bool(scenario.validate(context, result))

            if not valid:
                raise AssertionError(
                    f"Validation failed: {scenario.operation_id}"
                )

            if not is_warmup:
                measurements.append(elapsed)

                details.append({
                    "operation_id": scenario.operation_id,
                    "dbms": scenario.dbms,
                    "scenario": scenario.scenario,
                    "dataset_size": dataset_size,
                    "run": run_index,
                    "execution_time_ms": round(elapsed, 6),
                    "success": True,
                    "validation_passed": True,
                    "cache_hit": getattr(
                        scenario, "cache_hit", ""
                    ),
                })

        finally:
            scenario.teardown(context)

    summary = {
        "operation_id": scenario.operation_id,
        "requirement_ids": scenario.requirement_ids,
        "dbms": scenario.dbms,
        "operation": scenario.operation,
        "scenario": scenario.scenario,
        "success": True,
        "validation": {"passed": True},
        "measurements_ms": [
            round(value, 6) for value in measurements
        ],
        "mean_ms": round(
            statistics.mean(measurements), 6
        ),
        "median_ms": round(
            statistics.median(measurements), 6
        ),
    }

    return details, summary


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--adapter",
        action="append",
        required=True,
        help="Importable Python adapter module",
    )
    parser.add_argument(
        "--dataset-size",
        type=int,
        default=200000,
    )
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmarks/results"),
    )

    args = parser.parse_args()

    if args.warmups < 0 or args.repeats < 1:
        parser.error("Invalid repetition settings")

    rows = []
    summaries = []

    for module_path in args.adapter:
        module = importlib.import_module(module_path)

        for scenario in module.create_scenarios():
            print(
                f"Running {scenario.operation_id} "
                f"[{scenario.dbms}/{scenario.scenario}]"
            )

            result_rows, summary = run_scenario(
                scenario,
                args.warmups,
                args.repeats,
                args.dataset_size,
            )

            rows.extend(result_rows)
            summaries.append(summary)

    args.output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    with (
        args.output_dir / "runs.csv"
    ).open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output, fieldnames=CSV_FIELDS
        )
        writer.writeheader()
        writer.writerows(rows)

    (args.output_dir / "results.json").write_text(
        json.dumps(summaries, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Saved {len(rows)} measurements "
        f"across {len(summaries)} scenarios"
    )


if __name__ == "__main__":
    main()
