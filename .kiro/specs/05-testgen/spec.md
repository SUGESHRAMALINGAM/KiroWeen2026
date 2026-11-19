# Task 5: Test case generator (Phase B)
Requirements: FR-09, FR-10, FR-11, FR-12. Design ref: BRD 7.7.

## Requirements
- Generate null, uniqueness, accepted-value, range and referential-integrity checks.
- Export as SQL assertions, a pytest file and JSON.
- Run the checks against the loaded data and show pass/fail.
- Suggest foreign-key links across uploaded files, labelled "suggested".

## Design
- `core/testgen.py`: `generate_checks(connector, profile, links) -> list[Check]`. Every check is one query returning a violation count; it passes at 0.
- Checks mirror the infection model, so a check fails when the matching zombies exist:
  - not_null: every column.
  - unique: the user's key columns, else ID-named columns (`id`, `*_id`, `*_key`) that are at least 95% distinct.
  - accepted_values: text columns with at most 10 distinct values and at least 2 rows per value. Values come from the data.
  - range: numeric columns, using the IQR fences from the profile (same as Giant).
  - referential_integrity: one per confirmed link.
- `run_checks` runs each query and reports errors per check instead of raising.
- Exports: `to_sql`, `to_pytest` (reads `<table>.csv` from `ZOMBIE_DATA_DIR` into DuckDB), `to_json`.
- `suggest_links`: same column name, parent column unique, at least 90% of distinct child values found in the parent. Users confirm a suggestion before it is used (risk R-07).
- UI: suggested links in the sidebar, then a "Generated checks" section with downloads and a run button.

## Tests (`tests/test_testgen.py`)
- Hypothesis: checks generated from clean data pass on it; after injecting one null, duplicate id, bad value or outlier, exactly the matching check fails. Referential check fails on an injected orphan.
- Unit: retail counts, exports, generated pytest file executed in a subprocess, error handling, awkward names, link suggestions.

## Notes
- Range checks also cover ID columns, so an out-of-range ID is flagged (same as in Task 4).
- Accepted values are learned from the data being profiled. On data that already has typos they are accepted. They guard future data.

## Tasks
- [x] Check generation and runner
- [x] SQL / pytest / JSON exporters
- [x] Link suggestions
- [x] UI section and tests

Done when: a check fails on bad data and passes on clean data.


## Addendum: custom checks (`core/custom_checks.py`)
Why: automatic checks guess. They can flag an ID column as a range outlier, or miss that a key is text or made of several columns. Users now add their own checks and switch automatic ones off.

- `CustomSpec` + `build_custom_check(connector, table, spec) -> Check`. A custom check is a normal `Check` (kind `custom`), so it is exported to SQL / pytest / JSON and run like the automatic ones.
- Types: no blanks, unique, allowed values, number or date range, text pattern (whole value must match), custom SQL rule.
- Keys: unique and no-blank accept one or more columns of any type. Text keys can ignore case and surrounding spaces. Rows with a blank part are ignored by the unique check (the no-blank check reports them).
- Values are typed from the column: text is quoted, numbers must parse, dates are validated by DuckDB.
- Errors: every spec is validated and dry-run when added. Problems raise `CustomCheckError` with a readable message, and the UI shows it, not a database traceback.
- SQL rules: a condition that is TRUE for bad rows. The checked table is `t`. Parsed with sqlglot and limited to one SELECT over the loaded tables. Files, table functions (`read_*`), WITH, other statements and semicolons are rejected. This matters on the hosted app, where a rule must not read server files.
- Rules that read other tables are recorded in `Check.tables` so the exported pytest file loads them too.
- UI: Checks tab has "Turn off automatic checks you do not need" and "Add your own check". Custom checks live in the session only.

Tests: `tests/test_custom_checks.py` (Hypothesis against plain Python for composite text+number keys, awkward text values and ranges; date and pattern checks; 17 error cases; 7 rules that must be rejected) and three AppTest flows.