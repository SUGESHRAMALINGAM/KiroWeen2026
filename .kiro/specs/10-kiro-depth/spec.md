# Task 10: Kiro depth (MCP server, hooks, property-based tests) + public-app hardening
Requirements: FR-23, FR-24, NFR-07, NFR-02.

## MCP server (FR-24)
- `mcp_server/server.py` (MCP Python SDK 2.x, `MCPServer`, stdio only, no network port).
- Tools: `profile_csv(path, key_columns)` returns score, zombie counts and findings with plain sentences; `analyze_sql(sql, dialect)` returns tables, procedures, syntax problems and per-statement explanation, problems and lineage. SQL is never executed.
- Safety: only `.csv` files up to 200 MB; the DuckDB connection is locked right after loading (no other file can be read) with a 30 s query limit.
- Config: `.kiro/settings/mcp.json` registers `de-bro-code` with `"disabled": true`. The owner enables it after review.
- Tests: `tests/test_mcp_server.py` (both tools, refusals, tool registration).

## Hooks (FR-23)
- Planned: "Regenerate SQL docs" on `PostFileSave` for `\.sql$` runs `py scripts/gen_docs.py` (reads the event from stdin); "Run tests on save" on `PostFileSave` for `\.py$` runs `py -m pytest -q -x`.
- Created with Kiro's hook tool once the `KiroWeen2026` folder is opened as the workspace, so the files land in this repo's `.kiro/hooks/`.

## Property-based tests (NFR-07)
Hypothesis is used for: profiler counts vs plain Python, infected rows counted once, generated checks pass on clean data and fail on the injected defect, custom composite keys / allowed values / ranges, SQL parse -> regenerate keeps the table set, random text never crashes the analyzer.

## Public-app hardening (Zombie Hunter)
- `CsvConnector.lock(timeout_s)` applies `LOCK_SETTINGS` (no file/URL access, 512 MB, 2 threads, configuration locked) and stops any `query()` after the limit with `QueryTimeout`. Shared with `core/sandbox.py`.
- Zombie Hunter locks the database right after loading, with a 10 s limit; a timeout shows a plain message instead of a crash. Custom rules are dry-run with `EXPLAIN` (no execution) and a slow rule is reported as "took longer than ... and was stopped".
- `requirements.txt` is pinned to the tested versions.
- Tests: locked connector refuses file reads, COPY, SET and INSTALL; slow query stopped; heavy custom rule stopped; Zombie Hunter session database is locked.
