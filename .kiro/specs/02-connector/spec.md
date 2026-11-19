# Task 2: Connector + CSV/DuckDB (Phase A)
Requirements: FR-01, FR-02. Design ref: BRD 7.5.

## Requirements
- Load one or more CSV files into DuckDB through the common `Connector` interface (`list_tables`, `get_schema`, `sample`, `query`).
- Report rows rejected during CSV parsing instead of failing silently.
- Ship retail sample data (customers, products, orders) with planted defects for later tasks.

## Design
- `core/connectors/base.py`: `Connector` ABC, `Column` dataclass, `quote_ident`.
- `core/connectors/csv_connector.py`: `CsvConnector.load_csv(path)` returns `LoadResult(table, rows, rejected)`. Uses DuckDB `store_rejects`. Internal reject tables are hidden from `list_tables`.
- `scripts/make_retail_sample.py` generates `core/domain_packs/retail/*.csv` (seeded, deterministic). Planted in orders: 1 null customer_id, 1 orphan customer_id, quantity outlier 5000, negative quantity, 1 duplicate row.
- `scripts/inspect_csv.py` prints schema and row counts.

## Tasks
- [x] Connector base class
- [x] CSV connector with reject reporting
- [x] Retail sample data and generator
- [x] Tests in `tests/test_connectors.py`
- [x] Script prints schema and row counts

Done when: script prints schema and row counts; tests pass.
