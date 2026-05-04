# DQLens Demo

This demo walks you through DQLens end-to-end using a local PostgreSQL database
with realistic data quality issues planted in it.

**Time to complete: ~5 minutes**

## Prerequisites

- PostgreSQL running locally (accepts connections via Unix socket)
- Python 3.9+
- DQLens installed: `pip install -e ".[dev]"` from the `dqlens/` directory

## 1. Create the demo database

```bash
psql postgres -c "CREATE DATABASE dqlens_demo;"
psql dqlens_demo -f seed_data_v2.sql
```

This creates an e-commerce schema with 6 tables and ~7,600 rows:

| Table | Rows | Planted Issues |
|---|---|---|
| `customers` | 500 | 20% null phones, some invalid phone formats |
| `products` | 50 | 2 products with negative prices |
| `orders` | 2,000 | 10% null emails, negative amounts, orphaned customer_ids |
| `order_items` | 4,000 | Orphaned product_ids |
| `daily_reports` | 77 | Stale data (last entry 14 days ago) |
| `audit_log` | 0 | Intentionally empty |

## 2. Initialize DQLens

```bash
dqlens init "postgresql:///dqlens_demo?host=/tmp" --schema public
```

This creates a `.dqlens/` directory with your connection config:

```
.dqlens/
  config.yaml      # Connection string + options
  baselines/       # Where profiles are stored
  ignores.yaml     # Suppressed findings
```

## 3. Profile your database

```bash
dqlens profile
```

DQLens connects to your database, profiles every table (row counts, nulls,
uniqueness, patterns, foreign keys, freshness), and saves the results as a
baseline. It also generates `.dqlens/tests.yaml`, an inspectable file listing
every check it will run and why.

Expected output:

```
Profiling schema 'public'...

  public.audit_log      0 rows, 4 columns, 1 checks
  public.customers    500 rows, 5 columns, 15 checks
  public.daily_reports 77 rows, 5 columns, 14 checks
  public.order_items 4,000 rows, 5 columns, 11 checks
  public.orders      2,000 rows, 6 columns, 12 checks
  public.products       50 rows, 5 columns, 13 checks

Profile saved. (1 baseline stored)
Generated 66 checks → .dqlens/tests.yaml
```

## 4. Run checks

```bash
dqlens run
```

DQLens compares the current database state against the baseline and shows
problems first:

```
public.audit_log: 5 tests, 4 passed, 1 PROBLEM FOUND

  PROBLEMS:
  HIGH   Table is empty (0 rows)

public.customers: 13 tests, 11 passed, 2 PROBLEMS FOUND

  PROBLEMS:
  LOW    phone: 14.2% of values don't match phone pattern
  LOW    created_at: Last row is 1 day old

public.daily_reports: 13 tests, 12 passed, 1 PROBLEM FOUND

  PROBLEMS:
  MEDIUM report_date: Last row is 14 days old

public.orders: 11 tests, 10 passed, 1 PROBLEM FOUND

  PROBLEMS:
  MEDIUM total_amount: Contains negative values (min: -10.0) but column name
         suggests positive-only

public.products: 12 tests, 11 passed, 1 PROBLEM FOUND

  PROBLEMS:
  MEDIUM price: Contains negative values (min: -9.99) but column name suggests
         positive-only

Summary: 6 tables, 64 tests, 58 passed, 6 failed (1 HIGH, 3 MEDIUM, 2 LOW)
```

## 5. See all checks (including passing)

```bash
dqlens run --verbose
```

This shows every check DQLens ran, not just the failures.

## 6. Filter by severity

```bash
dqlens run --focus high
```

Only shows HIGH severity findings. Useful for CI gates.

## 7. Simulate data degradation (drift detection)

Now let's break some data and see DQLens catch the drift:

```bash
psql dqlens_demo -c "
  UPDATE orders SET email = NULL WHERE id BETWEEN 500 AND 800;
  INSERT INTO orders (customer_id, status, total_amount, email, created_at)
  SELECT (i % 500) + 1, 'pending', round((random() * 500 + 5)::numeric, 2),
         'user' || i || '@example.com', NOW()
  FROM generate_series(1, 3000) AS i;
  ANALYZE;
"
```

Run checks again:

```bash
dqlens run
```

DQLens now detects drift from the baseline:

```
public.orders: 13 tests, 9 passed, 4 PROBLEMS FOUND

  PROBLEMS:
  HIGH   created_at: Was unique in baseline, no longer unique
  MEDIUM Row count grew 150% (2,000 → 5,000)
  MEDIUM total_amount: Contains negative values (min: -10.0)
  LOW    id: Max value expanded significantly (2000.0 → 5000.0)
```

## 8. Suppress a known finding

If a finding is expected, suppress it:

```bash
dqlens ignore "audit_log.row_count_anomaly"
```

It won't appear in future runs. To undo, edit `.dqlens/ignores.yaml`.

## 9. Inspect the generated tests

Open `.dqlens/tests.yaml` to see every check DQLens generated:

```yaml
- check: pattern
  column: email
  pattern: email
  expect: match_above
  value: 95.0
  unit: percent
  current_match: 100.0
  reason: Detected email pattern with 100.0% match rate.
          Threshold set 5pp below current rate.
```

You can edit thresholds, delete checks you don't need, or add your own.

## 10. JSON output (for CI/CD)

```bash
dqlens run --json-output
dqlens run --ci  # exits with code 1 if any failures
```

## Cleanup

```bash
psql postgres -c "DROP DATABASE dqlens_demo;"
rm -rf .dqlens/
```
