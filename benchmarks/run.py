"""IF5101 NoSQL benchmark runner. Execute from the repository root.

Adapter contract: create_scenarios() returns objects exposing
operation_id, requirement_ids, dbms, operation, scenario,
setup(), execute(ctx), validate(ctx, result), teardown(ctx).
Optional: cache_hit (expected hit/miss label for controlled scenarios).
"""
from __future__ import annotations

import argparse
import base64
import csv
import importlib
import json
import math
import statistics
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

CSV_FIELDS = (
    "operation_id", "dbms", "scenario", "dataset_size", "run",
    "execution_time_ms", "success", "validation_passed", "cache_hit",
    "error_stage", "error_type", "error_message",
)


def json_safe(value, depth=0):
    """Bound result size so query results do not make huge JSON artifacts."""
    if depth > 5:
        return "<max depth>"
    if value is None or isinstance(value, (str, bool, int)):
        return value[:500] if isinstance(value, str) else value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")[:500]
        except UnicodeDecodeError:
            return {"base64_preview": base64.b64encode(value[:64]).decode("ascii")}
    if isinstance(value, dict):
        out = {str(k): json_safe(v, depth + 1) for k, v in list(value.items())[:20]}
        if len(value) > 20:
            out["_truncated_keys"] = len(value) - 20
        return out
    if isinstance(value, (list, tuple)):
        out = [json_safe(v, depth + 1) for v in value[:20]]
        if len(value) > 20:
            out.append(f"<and {len(value) - 20} more items>")
        return out
    return repr(value)[:500]


def error_info(stage, exc):
    return {"stage": stage, "type": type(exc).__name__, "message": str(exc)[:500]}


def check_scenario(s):
    for attr in ("operation_id", "requirement_ids", "dbms", "operation", "scenario"):
        if not getattr(s, attr, None):
            raise ValueError(f"Scenario missing attribute: {attr}")
    for method in ("setup", "execute", "validate", "teardown"):
        if not callable(getattr(s, method, None)):
            raise ValueError(f"Scenario missing callable: {method}")


def run_once(s, run, dataset_size):
    """Measure ONLY execute(). Always attempt teardown after successful setup."""
    record = {
        "operation_id": s.operation_id, "dbms": s.dbms,
        "scenario": s.scenario, "dataset_size": dataset_size,
        "run": run, "execution_time_ms": None, "success": False,
        "validation_passed": None,
        "cache_hit": getattr(s, "cache_hit", None),
        "error_stage": None, "error_type": None, "error_message": None,
    }
    result_preview = None
    errors = []
    ctx = None
    setup_ok = False

    try:
        try:
            ctx = s.setup()
            setup_ok = True
        except Exception as exc:
            errors.append(error_info("setup", exc))

        if setup_ok:
            start = time.perf_counter_ns()
            try:
                value = s.execute(ctx)
            except Exception as exc:
                record["execution_time_ms"] = round((time.perf_counter_ns() - start) / 1e6, 6)
                errors.append(error_info("execute", exc))
            else:
                record["execution_time_ms"] = round((time.perf_counter_ns() - start) / 1e6, 6)
                record["success"] = True
                result_preview = json_safe(value)
                try:
                    record["validation_passed"] = bool(s.validate(ctx, value))
                    if not record["validation_passed"]:
                        errors.append({"stage": "validate", "type": "ValidationFailed", "message": "Result did not match expected behavior"})
                except Exception as exc:
                    record["validation_passed"] = False
                    errors.append(error_info("validate", exc))
    finally:
        if setup_ok:
            try:
                s.teardown(ctx)
            except Exception as exc:
                errors.append(error_info("teardown", exc))

    if errors:
        record["error_stage"] = errors[0]["stage"]
        record["error_type"] = errors[0]["type"]
        record["error_message"] = errors[0]["message"]
    return record, result_preview, errors


def run_scenario(s, warmups, repeats, dataset_size):
    check_scenario(s)
    samples = []
    warmup_records = []
    result_preview = None
    all_errors = []
    for index in range(-warmups + 1, repeats + 1):
        record, preview, errors = run_once(s, index, dataset_size)
        if preview is not None:
            result_preview = preview
        all_errors.extend([{"run": index, **err} for err in errors])
        if index <= 0:
            warmup_records.append(record)
        else:
            samples.append(record)

    # Only execution-success AND validation-passing measurements are valid latencies.
    valid = [x["execution_time_ms"] for x in samples
             if x["success"] and x["validation_passed"] is True
             and x["execution_time_ms"] is not None
             and not any(e["run"] == x["run"] and e["stage"] == "teardown" for e in all_errors)]
    failed_count = sum(not (x["success"] and x["validation_passed"] is True) for x in samples)
    complete = not all_errors and len(valid) == repeats
    summary = {
        "operation_id": s.operation_id,
        "requirement_ids": list(s.requirement_ids),
        "dbms": s.dbms, "operation": s.operation, "scenario": s.scenario,
        "dataset_size": dataset_size,
        "success": all(x["success"] for x in samples) and not any(e["stage"] in ("setup", "execute") for e in all_errors),
        "result": result_preview,
        "validation": {"passed": complete, "passed_runs": len(valid), "failed_runs": failed_count},
        "warmups": warmups, "repeats": repeats,
        "measurements_ms": [x["execution_time_ms"] for x in samples],
        "mean_ms": round(statistics.mean(valid), 6) if valid else None,
        "median_ms": round(statistics.median(valid), 6) if valid else None,
        "status": "passed" if complete else "failed",
        "errors": all_errors,
    }
    return samples, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="NoSQL benchmark runner")
    parser.add_argument("--adapter", action="append", required=True)
    parser.add_argument("--dataset-size", type=int, default=2_000_000)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmarks/results"))
    args = parser.parse_args(argv)
    if args.dataset_size <= 0 or args.warmups < 0 or args.repeats < 1:
        parser.error("dataset-size must be positive, warmups >= 0, repeats >= 1")

    rows, summaries = [], []
    failed = False
    for module_path in args.adapter:
        try:
            module = importlib.import_module(module_path)
            scenarios = list(module.create_scenarios())
            if not scenarios:
                raise ValueError("Adapter has no scenarios")
        except Exception as exc:
            failed = True
            summaries.append({"adapter": module_path, "status": "failed", "errors": [error_info("load_adapter", exc)]})
            print(f"[FAIL] {module_path}: {exc}", file=sys.stderr)
            continue

        for scenario in scenarios:
            label = getattr(scenario, "operation_id", "<invalid>")
            print(f"[RUN] {module_path}: {label}", flush=True)
            try:
                records, summary = run_scenario(scenario, args.warmups, args.repeats, args.dataset_size)
                rows.extend(records)
            except Exception as exc:
                failed = True
                summary = {"adapter": module_path, "operation_id": label,
                           "status": "failed", "errors": [error_info("runner", exc)]}
            summaries.append(summary)
            if summary["status"] != "passed":
                failed = True
            print(f"[{summary['status'].upper()}] {label}", flush=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "runs.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    document = {
        "metadata": {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                     "dataset_size": args.dataset_size,
                     "warmups": args.warmups, "repeats": args.repeats,
                     "adapters": args.adapter,
                     "timing_scope": "client-observed execute() duration only"},
        "results": summaries,
    }
    (args.output_dir / "results.json").write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved {len(rows)} measured runs and {len(summaries)} scenario summaries to {args.output_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
