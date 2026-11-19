# DE Bro Code: Zombie Edition

A data engineer's AI teammate for Kiroween 2026 (Skeleton Crew). One shared core, two apps:

- **Zombie Hunter**: profile CSVs, find data "zombies", generate and run checks.
- **SQL Autopsy**: explain, lint and trace lineage for SQL files and stored procedures, check them against real tables, and download ETL docs.

Status: all planned features are built (see `TODO.md`). Live links come after deployment.

## Run

```
pip install -r requirements.txt
streamlit run apps/zombie_hunter/app.py
streamlit run apps/sql_autopsy/app.py --server.port 8502
pytest -q
```

On Windows without Python on PATH, put `py -m` in front (for example `py -m streamlit run ...`).

## Zombie Hunter

![Results](docs/img/zombie_hunter_results.png)

- **Alert:** a full-screen alert appears above your alert level (default 10%). Try the *Retail outbreak (demo)* sample.
- **Rules:** automatic checks (no blanks, no repeats, allowed values, normal range, links exist), plus your own rules: combined keys (text or numbers), allowed values, ranges, ready-made formats (email, digits, dates...) and SQL rules. Each rule gets a plain answer with example rows.
- **Plain Mode:** turns off the theme, pictures and alert, and shows the same findings as tables.
- **Reduced motion:** users who prefer reduced motion get no animation and no overlay, only a banner.

## SQL Autopsy

- **Paste or upload SQL:** `.sql` files with many statements and stored procedures (SQL Server, PostgreSQL, MySQL with `DELIMITER`, Snowflake, BigQuery and more). You get a file summary, a statement picker, syntax problems such as extra `;;`, and a list of steps that were not analysed (IF, DECLARE, ...).
- **Per statement:** plain-English steps, possible problems with how to fix them, and where every column comes from.
- **Your tables (optional):** upload CSVs or use the retail sample. SQL Autopsy then finds tables and columns that do not exist (with "Did you mean ...?"), columns that are in more than one table, and joins that multiply rows. **Try it on your tables** previews the result.
- **Download documentation:** a Markdown file with a summary, a Mermaid data-flow diagram, and every statement explained. From the command line: `py scripts/gen_docs.py path/to/file.sql --dialect mysql` (writes to `docs/generated/`).

## Security on the public apps

- Both apps lock their DuckDB database right after loading your CSVs: no file or URL access, settings frozen, 512 MB memory, 2 threads.
- SQL Autopsy previews run only one read-only SELECT. Changes, file access, COPY, ATTACH, PRAGMA, SET and INSTALL are refused before anything runs. Previews return at most 100 rows and stop after 5 seconds.
- Zombie Hunter stops any check or custom rule after 10 seconds.
- Uploads: CSV up to 50 MB (10 files) in SQL Autopsy, `.sql` up to 1 MB.
- Nothing you upload or paste is stored or logged.

## Kiro

- **Specs:** one per task in `.kiro/specs/`. **Steering:** `.kiro/steering/` (product, tech, structure).
- **MCP server:** `mcp_server/server.py` with tools `profile_csv` and `analyze_sql`, registered in `.kiro/settings/mcp.json`. It ships switched off (`"disabled": true`); set it to `false` to let Kiro call it. It runs locally over stdio and never executes SQL.
- **Property-based tests:** Hypothesis checks the profiler, the generated checks, custom rules and the SQL parser against plain Python.

## Later

Databricks and BigQuery connections, through the same `Connector` interface.

## License

MIT
