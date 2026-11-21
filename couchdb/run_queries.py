"""Run each essential operation once and show its result (functional check, report 3.2).

    uv run --no-sync python -m couchdb.run_queries                 # all 25 operations
    uv run --no-sync python -m couchdb.run_queries PF-08-Q01 PF-05-Q01 --rows 10

Uses the same setup / execute / validate / teardown steps as the benchmark adapter, so
write operations leave the database as they found it. For timings, use benchmarks.run.
"""

from __future__ import annotations

import argparse
import json
import time

from benchmarks.adapters import couchdb as adapter
from couchdb.queries import OPERATIONS

META = {op.operation_id: op for op in OPERATIONS}


def preview(result, rows: int) -> list[str]:
    if isinstance(result, list):
        if not result:
            return ['(no rows)']
        columns = list(result[0].keys()) if isinstance(result[0], dict) else ['value']
        table = [[str(r.get(c) if isinstance(r, dict) else r) for c in columns]
                 for r in result[:rows]]
        widths = [min(28, max(len(c), *(len(t[i]) for t in table))) for i, c in enumerate(columns)]
        line = lambda cells: '  '.join(c[:w].ljust(w) for c, w in zip(cells, widths))  # noqa: E731
        out = [line(columns), line(['-' * w for w in widths]), *(line(t) for t in table)]
        if len(result) > rows:
            out.append(f'... {len(result) - rows:,} more rows ({len(result):,} in total)')
        return out
    text = json.dumps(result, ensure_ascii=False, indent=1, default=str)
    lines = text.splitlines()
    return lines[:rows * 3] + (['...'] if len(lines) > rows * 3 else [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run each CouchDB essential operation once.')
    parser.add_argument('operations', nargs='*', help='operation IDs, e.g. PF-08-Q01')
    parser.add_argument('--rows', type=int, default=5, help='result rows to show')
    args = parser.parse_args(argv)
    scenarios = [s for s in adapter.create_scenarios()
                 if not args.operations or s.operation_id in args.operations]
    failures = 0
    for s in scenarios:
        meta = META[s.operation_id]
        source = f' = Part 2 {meta.part2_query}' if meta.part2_query else ''
        print(f'\n=== {s.operation_id}{source} | {meta.description} '
              f'[{", ".join(meta.requirement_ids)}]')
        ctx = s.setup()
        try:
            started = time.perf_counter()
            result = s.execute(ctx)
            elapsed = (time.perf_counter() - started) * 1000
            passed = s.validate(ctx, result)
        finally:
            s.teardown(ctx)
        failures += not passed
        count = f'{len(result):,} rows, ' if isinstance(result, list) else ''
        print(f'{count}{elapsed:.1f} ms, validation {"PASSED" if passed else "FAILED"}')
        for line in preview(result, args.rows):
            print('  ' + line)
    print(f'\n{len(scenarios) - failures}/{len(scenarios)} operations passed validation')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
