# DQLens

> Find data problems automatically — no config, no test writing.

DQLens auto-generates data quality tests by profiling your database. No YAML, no Python, no configuration files. Just point it at your database and get instant visibility into data quality issues.

## Quick Start

```bash
pip install dqlens

# Initialize (stores connection config)
dqlens init postgres://localhost/mydb --schema public

# Profile your database (auto-generates tests)
dqlens profile

# Run checks and see problems
dqlens run
```

## What It Does

DQLens connects to your database, profiles every table, and automatically generates tests based on what it finds:

- **Null anomalies** — detects columns with unexpected null rates or null rate drift
- **Uniqueness violations** — finds duplicate values in columns that should be unique
- **Foreign key mismatches** — discovers orphaned rows referencing non-existent records
- **Pattern violations** — identifies columns where values don't match detected patterns (email, UUID, URL, etc.)
- **Row count anomalies** — flags unusual growth or shrinkage compared to baseline
- **Freshness checks** — alerts when data hasn't been updated recently
- **Distribution shifts** — catches value range changes between profiles

## Signal Over Coverage

DQLens shows problems first, not 20 green checkmarks:

```
public.orders: 14 tests, 11 passed, 3 PROBLEMS FOUND

  PROBLEMS:
  HIGH   customer_id: 142 rows reference non-existent customers (FK mismatch)
  HIGH   email: 3.2% null (was 0.1% in baseline) — 32x increase
  MEDIUM orders grew 47% today (usual daily growth: 2-5%)

  ✓ 11 checks passed (use --verbose to see all)
```

Every finding includes:
- **Severity level** (HIGH / MEDIUM / LOW)
- **Explanation** of why it was flagged
- **Baseline comparison** when available

## Commands

| Command | Description |
|---|---|
| `dqlens init <url>` | Initialize config with database connection |
| `dqlens profile` | Profile tables and save baseline |
| `dqlens run` | Run checks, show problems |
| `dqlens run --verbose` | Show all checks including passing |
| `dqlens run --focus high` | Only HIGH severity findings |
| `dqlens run --ci` | Exit code 1 on failure (for CI/CD) |
| `dqlens run --json-output` | Output as JSON |
| `dqlens ignore <key>` | Suppress a known finding |

## Python API

```python
import dqlens

suite = dqlens.profile("postgres://localhost/mydb", schema="public")
results = suite.run()

for table in results:
    for test in table.tests:
        if test.failed:
            print(f"{table.name}.{test.column}: {test.message}")
```

## Supported Databases

- PostgreSQL (Phase 1)
- MySQL, SQLite, Parquet, CSV (Phase 2)

## License

MIT
