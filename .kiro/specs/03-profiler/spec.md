# Task 3: Profiler (Phase A)
Requirements: FR-03, FR-04, FR-05, FR-06, NFR-07. Design ref: BRD 7.5, 7.6.

## Requirements
- Per column: row count, null count, distinct count, min/max, mean/stddev using SQL pushdown.
- Detect duplicate rows (full row, or user-selected key columns).
- Detect numeric outliers with the IQR rule; suggest candidate key columns.
- Detect type/format problems: numbers stored as text, mixed date formats.

## Design
- `core/profiler.py`: `profile_table(connector, table, key_columns=None) -> TableProfile` of `ColumnProfile` dataclasses. Only calls `Connector.query()` with aggregate SQL; identifiers are quoted.
- Duplicate counts are extra copies (rows minus distinct rows or distinct keys). Rows with a null key are ignored for key duplicates.
- Outliers: values outside Q1 - 1.5*IQR and Q3 + 1.5*IQR (linear-interpolated quartiles).
- Text columns: count values that cast to a number, and values matching known date patterns.
- Candidate key: no nulls and distinct count equals row count.
- `scripts/profile_csv.py` prints a profile.

## Tests (`tests/test_profiler.py`)
- Hypothesis: null, distinct, duplicate row/key, outlier (vs Python `statistics`), mean/stddev and candidate-key counts match exactly.
- Unit: mixed/numeric text, mixed dates, awkward column names, retail planted defects.

## Tasks
- [x] Profiler dataclasses and SQL
- [x] Hypothesis and unit tests
- [x] CLI

Done when: Hypothesis tests pass; CLI prints a profile.
