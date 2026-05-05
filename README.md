# DQLens

[![CI](https://github.com/vahid110/dqlens/actions/workflows/ci.yml/badge.svg)](https://github.com/vahid110/dqlens/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dqlens)](https://pypi.org/project/dqlens/)

> Find data problems automatically. No config, no test writing.

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

- **Null anomalies**: columns with unexpected null rates or null rate drift
- **Uniqueness violations**: duplicate values in columns that should be unique
- **Foreign key mismatches**: orphaned rows referencing non-existent records
- **Pattern violations**: values that don't match detected patterns (email, UUID, URL, etc.)
- **Row count anomalies**: unusual growth or shrinkage compared to baseline
- **Freshness checks**: data that hasn't been updated recently
- **Distribution shifts**: value range changes between profiles

## Signal Over Coverage

DQLens shows problems first, not 20 green checkmarks:

```
public.orders: 14 tests, 11 passed, 3 PROBLEMS FOUND

  PROBLEMS:
  HIGH   customer_id: 142 rows reference non-existent customers (FK mismatch)
  HIGH   email: 3.2% null (was 0.1% in baseline), 32x increase
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
| `dqlens profile --quick` | Quick mode: sample data, under 5 seconds |
| `dqlens run` | Run checks, show problems |
| `dqlens run --verbose` | Show all checks including passing |
| `dqlens run --focus high` | Only HIGH severity findings |
| `dqlens run --ci` | Exit code 1 on failure (for CI/CD) |
| `dqlens run --json-output` | Output as JSON |
| `dqlens diff` | Compare two most recent profiles |
| `dqlens diff --json-output` | Diff as JSON |
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

- PostgreSQL
- SQLite
- MySQL
- Parquet, CSV (coming soon)

## dbt Integration

Using dbt? [dbt-dqlens](https://github.com/vahid110/dbt-dqlens) auto-generates native dbt test YAML from profiling results. No more writing `not_null` and `unique` by hand.

```bash
pip install dbt-dqlens
dqlens-dbt profile        # profiles models using your profiles.yml
dqlens-dbt generate-tests # outputs _dqlens_tests.yml
dbt test --select tag:dqlens
```

## Development

```bash
# Clone and install
git clone https://github.com/vahid110/dqlens.git
cd dqlens
pip install -e ".[dev]"

# Run unit tests (no database needed)
pytest tests/ -k "unit" -v

# Run integration tests (needs PostgreSQL, see .env.example)
pytest tests/ -k "integration" -v

# Run all tests
pytest tests/ -v
```

### Demo

See [demo/README.md](demo/README.md) for a 5-minute walkthrough with a local PostgreSQL database.

## License

MIT
