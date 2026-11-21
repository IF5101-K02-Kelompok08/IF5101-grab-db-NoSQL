# CouchDB implementation

Document-database implementation of the Grab Mobility & Fulfillment model (Part 3).
Everything below runs from the repository root, after `uv sync --frozen` and with the
containers started (see the main README). The easiest way to run the whole flow and see
the results is `notebook/couchdb_queries.ipynb`.

## Steps

```bash
# 1. Load the canonical dataset: creates the databases, security, app user and validation
#    rules, writes ~2.76 million documents, then builds the views and Mango index.
#    --reset drops the four grab_* databases first. Timings: benchmarks/results/couchdb_load_report.json
uv run --no-sync python -m couchdb.load --reset

# 2. Check the load (report 3.1): row counts per Part 2 table and a field-by-field sample.
uv run --no-sync python -m couchdb.verify_load

# 3. Expected results of the read operations, computed from the CSV (about 1-2 minutes).
uv run --no-sync python -m couchdb.reference

# 4. Run every operation once and check it (report 3.2).
uv run --no-sync python -m couchdb.run_queries            # or: ... run_queries PF-08-Q01 --rows 10

# 5. Benchmark: 1 warm-up + 3 measured runs per operation.
uv run --no-sync python -m benchmarks.run --adapter benchmarks.adapters.couchdb --dataset-size 2000000

# Integrity-rule tests (temporary databases) and unit tests.
uv run --no-sync python -m pytest tests/test_couchdb_rules.py tests/test_couchdb_units.py -v
```

`.env` needs `COUCHDB_USER`/`COUCHDB_PASSWORD` (admin) and, optionally,
`COUCHDB_APP_USER`/`COUCHDB_APP_PASSWORD`: the load creates that user with role `grab_app`,
and the operations and benchmark run as it. Without it they run as the admin.

## Databases and documents

| Database | One document per | Embeds | Part 2 tables |
|---|---|---|---|
| `grab_orders` | service order | subtype (`food`/`mobility`/`delivery`), `payment`, `trips[]` with `assignments[]` | service_orders, food/mobility/delivery_orders, payments, trips, driver_assignments |
| `grab_fleet` | driver partner | `vehicles[]` | driver_partners, vehicles |
| `grab_customers` | customer | | customers |
| `grab_geo` | service area, location or merchant outlet (`type`) | outlet: copy of its `location` | service_areas, locations, merchant_outlets |

`_id` is the Part 2 ID (`ORD0000001`, `DRV0000001`, ...). Values that never change after
they are written are copied where queries need them: the pickup area of a trip, the driver
and vehicle type of an assignment, the area of a food order's outlet, the location of an
outlet. See `couchdb/model.py`.

## Code

| Path | What it does |
|---|---|
| `design/<db>/validate_doc_update.js` | Part 2 CHECK constraints and triggers, run by CouchDB on every write |
| `design/<db>/<ddoc>/*.map.js`, `*.reduce.js` | JavaScript views with built-in reducers (`_count`, `_sum`, `_stats`) |
| `design/geo/mango_indexes.json` | Mango index for the outlet search (R10) |
| `model.py`, `transform.py` | CSV rows to documents (streaming merge join) and back |
| `setup_db.py`, `load.py` | Databases, security, design documents; bulk load and index builds |
| `queries.py` | The 25 essential operations and their catalog (`OPERATIONS`) |
| `reference.py`, `validation.py` | Expected results from the CSV, and the comparison |
| `verify_load.py`, `run_queries.py` | Load check and one-off run of the operations |
| `../benchmarks/adapters/couchdb.py` | Benchmark scenarios (setup, execute, validate, teardown) |

## Operations

| ID | Part 2 | Operation |
|---|---|---|
| PF-01-Q01 | | Read one order with its subtype, payment, trips and assignments |
| PF-01-Q02 | | Read a customer's 20 latest orders |
| PF-01-Q03 | W1 | Create a confirmed order with its payment and SEARCHING trip |
| PF-02-Q01 | W2 | Offer a trip to an available driver |
| PF-02-Q02 | W3 | Driver accepts the offer and starts the trip |
| PF-02-Q03 | W4 | Driver rejects the offer |
| PF-02-Q04 | R4 | Trips offered more than twice and eventually accepted |
| PF-03-Q01 | | Read the status of a trip by trip_id |
| PF-03-Q02 | W5 | Complete a trip and make the driver available |
| PF-04-Q01 | W7 | Non-cash checkout that fails once and succeeds on retry |
| PF-04-Q02 | W8 | Cancel before pickup and settle the payment |
| PF-04-Q03 | W9 | Complete a cash ride and collect the payment atomically |
| PF-05-Q01 | R10 | First page of open outlets in a service area |
| PF-06-Q01 | W6 | Onboard a service area with its locations and outlets |
| PF-06-Q02 | W10 | Deactivate a service area and delist its outlets |
| PF-07-Q01 | | Delete a delisted outlet that no order refers to |
| PF-07-Q02 | | Refuse to delete an outlet that orders still refer to |
| PF-08-Q01 | R1 | Acceptance rate per driver |
| PF-08-Q02 | R2 | Top 10 drivers by total completed trip duration |
| PF-08-Q03 | R3 | Trips and distinct vehicles per vehicle type and hour |
| PF-09-Q01 | R5 | Trips per service area |
| PF-09-Q02 | R9 | Outlet supply against food demand per area, October 2026 |
| PF-10-Q01 | R6 | Net revenue and refunds per service area |
| PF-10-Q02 | R7 | Monthly payment summary per payment method |
| PF-10-Q03 | R8 | Top 10 outlets by food revenue |

## Correctness

Read results are compared, in full and in order, with `couchdb/reference.py`, which
re-implements the Part 2 SQL in plain Python over the CSV files and shares no code with the
views. During development the reference itself was checked against PostgreSQL running the
original Part 2 SQL on a 40,000-order sample: the same rows for R1-R10, with order differing
only among rows the SQL `ORDER BY` leaves tied. Ties are broken by ID in both implementations.

## Known limits

* CouchDB has no multi-document transactions. Order changes are atomic because an order is
  one document; W5 (order + driver) and W6/W10 (several geo documents) are not. W5 writes the
  order first and makes the idempotent driver update afterwards; W6 deletes what it wrote if
  any document is rejected.
* Foreign keys and uniqueness across documents (e.g. `(city, area_name)`, phone numbers)
  are checked by the application, not by CouchDB.
* Text ordering follows CouchDB's ICU collation, which can differ from PostgreSQL's for
  names that differ only in spaces or case.
