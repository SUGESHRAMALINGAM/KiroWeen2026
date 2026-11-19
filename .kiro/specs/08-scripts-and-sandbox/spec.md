# SQL files, stored procedures and DuckDB checks (base build)
Extends Task 7 (SQL Autopsy). Cloud warehouses (Databricks, BigQuery) are future work through the same interfaces.

## Requirements
- Upload or paste `.sql` files with many statements; analyse every statement; show a file summary.
- Stored procedures / functions: analyse the statements inside the body (best effort). List anything not analysed.
- Load CSVs (or the retail sample) into a DuckDB sandbox inside SQL Autopsy.
- Check SQL against the real tables: unknown tables, unknown columns ("Did you mean ...?"), ambiguous columns; use the schema for lineage; warn when joins multiply rows.
- Preview results safely on a public link: read-only, 100 rows, 5 seconds.

## Design
- `core/sql_script.py`: `analyze_script(text, dialect, schema=None) -> ScriptAnalysis` (statements, unreadable, procedures; summary helpers `tables_read`, `tables_written`, `problem_counts`).
  - Procedure blocks are found first (`CREATE [OR REPLACE|ALTER] PROCEDURE|PROC|FUNCTION`), body from `BEGIN ... END` (depth-matched, `END IF/LOOP` ignored) or `$tag$ ... $tag$`, or T-SQL `AS ...` up to `GO`. Blocks are blanked out before the top-level split.
  - Top level is split on sqlglot SEMICOLON tokens (`;` in strings is safe), with T-SQL `GO` as a separator. Fallback: string-aware splitter.
  - Inside bodies, a step that starts with control flow (IF, SET, ...) is searched for an embedded SELECT/INSERT/UPDATE/DELETE/MERGE that parses; otherwise it is listed as not analysed.
  - Limits: 1,000,000 characters, 200 statements per file. Error line numbers are made absolute.
- `core/sql_analyzer.py`: `analyze(sql, dialect, schema=None)`. UPDATE / DELETE / MERGE are now explained (target, tables read, filters) with a `no_where` problem. `check_against_schema(tree, dialect, schema)` uses sqlglot scopes; CTE outputs and SELECT aliases count as known; outer scopes are searched for correlated columns.
- `core/sandbox.py`: `Sandbox.from_files` / `retail_sample`, `schema()`, `table_rows`, `preview()`, `count_rows()`, and `row_explosion_issue(sql, dialect, count_rows, table_rows)` (>10x the largest input table; filters with sub-queries are dropped for the count and the message says so). Any future connector that offers `schema()`, `table_rows` and `count_rows()` plugs into the same checks.
- `apps/sql_autopsy/app.py`: sidebar "Your tables (optional)" (CSV upload, retail sample, table list, security note); `.sql` upload next to the paste box; file summary, "Not analysed" list, statement picker; new tab "Try it on your tables".

## Security model (public link)
1. sqlglot allow-list before anything runs: exactly one statement; root is SELECT / set operation / VALUES; no DML, DDL, COPY, ATTACH, PRAGMA, SET, INSTALL, INTO; tables must be loaded tables or CTEs (no files, no table functions, no other catalogs); `read_*`, `*_scan`, `glob`, `getenv`, `query`, ... blocked. The validated tree is regenerated as DuckDB SQL and wrapped in a LIMIT.
2. DuckDB is locked after loading: `enable_external_access = false`, `memory_limit = 512MB`, `threads = 2`, `lock_configuration = true`. Tested by running forbidden statements directly on the locked connection.
3. Timeout with `cursor.interrupt()` after 5 s; 101 rows fetched to detect truncation.
4. Uploads: CSV up to 50 MB each and 10 files; `.sql` up to 1 MB. File names are sanitised. Everything lives in the Streamlit session; nothing is written except a temp file during CSV load, deleted right after.

## Tests
- `tests/test_sql_script.py`: multi-statement scripts, `;` in strings, broken statement in the middle, limits, `GO`, UPDATE/DELETE, procedure fixtures for T-SQL, PostgreSQL, Snowflake, BigQuery (`tests/fixtures/`).
- `tests/test_sandbox.py`: preview cap, dialect translation, 15 unsafe or unknown queries refused, locked database, timeout, upload limits, schema checks, lineage with schema, row explosion.
- `tests/test_apps_smoke.py`: script summary and picker, procedure example, sample tables + schema issue + preview + refused UPDATE, row explosion.

## Addendum: MySQL files and syntax problems
- `DELIMITER //` lines are handled (offsets kept, custom delimiter turned into `;`), so MySQL procedure files split correctly.
- Empty statements (`;;`, `;;;`) are reported as syntax problems with their line, at top level and inside procedure bodies. MySQL rejects them ("Query was empty").
- `INSERT ... VALUES` and `CALL` / `EXEC` are explained; a CALL to a procedure defined in the same file says where.
- The app shows a red "syntax problem(s)" box above the summary. Leading comments are no longer shown as part of a statement.
- Fixture: `tests/fixtures/mysql_procs.sql`.

## Known limits
- T-SQL procedures without semicolons are read as one step per `;`-separated piece.
- DuckDB has no stored procedures; procedure code is analysed as text, and only its read-only statements can be previewed.
- Previews run in DuckDB; SQL from other dialects is translated by sqlglot, which may not cover every function.
