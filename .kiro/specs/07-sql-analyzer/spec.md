# Task 7: SQL analyzer + SQL Autopsy (Phase C, App 2)
Requirements: FR-15, FR-16, FR-17, FR-18, NFR-07. Design ref: BRD 7.2, 7.8, 7.9.

## Requirements
- Parse SQL in a chosen dialect; extract tables, joins, filters, aggregations, CTEs.
- Template-based plain-English explanation (no LLM).
- Lint: SELECT *, function on a filtered column, NOT IN with a sub-query, cross join / missing join condition, DISTINCT on joins.
- Table and column lineage; parse errors shown as a readable message.

## Design
- `core/sql_analyzer.py`: `analyze(sql, dialect) -> Analysis`. Never raises on bad input; `Analysis.error` holds a message with line, column and the text around the problem.
- Dialects: DuckDB, PostgreSQL, MySQL, Snowflake, BigQuery, Databricks, Spark, Redshift, SQL Server, Oracle, SQLite. Input limit 200,000 characters.
- Handles SELECT, UNION, CTEs, and INSERT / CREATE ... AS SELECT (records the target table). Other statements get a clear "not supported" message. With several statements, the first one is explained and the user is told.
- Explanation: one plain sentence per step (helper steps, tables, each join and what it keeps, filters, groups, calculations, distinct, columns, sort, limit, target).
- Lint issues carry severity (high = likely bug, medium = slow or fragile, low = worth a look), a plain "why" and a "how to fix".
- Column lineage uses `sqlglot.lineage` on a copy without WHERE / HAVING (unqualified names in filters otherwise stop resolution), and follows aliases and CTEs back to real tables. Fallback resolves aliases directly. Constants have no source.
- `lineage_dot()` gives a Graphviz graph of tables -> result / target (names escaped).
- `apps/sql_autopsy/app.py`: sidebar (Plain Mode, database, examples); SQL box; four summary cards; tabs: What it does, Possible problems, Where data comes from, Details. Same theme and Plain Mode as Zombie Hunter.

## Tests
- `tests/test_sql_analyzer.py`: structure, explanation phrases, INSERT/CREATE targets, UNION, each lint rule and clean counter-examples, lineage through aliases/CTEs, readable errors, dialect syntax.
- Hypothesis: random text never raises; parse -> regenerate keeps the same table set across 5 dialects.
- AppTest: examples with and without problems, lineage values, friendly parse error, Plain Mode + dialect.

## Tasks
- [x] Analyzer, lint, lineage
- [x] SQL Autopsy UI
- [x] Tests and browser check

Done when: SQL Autopsy shows explanation, issues and lineage.