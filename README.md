# DE Bro Code: Zombie Edition

A data engineer's AI teammate for Kiroween 2026 (Skeleton Crew). One shared core, two apps:

- **Zombie Hunter**: profile CSVs, find data "zombies", generate and run checks.
- **SQL Autopsy**: explain, lint and trace lineage for SQL, and generate ETL docs.

Status: Tasks 1-7 done, plus SQL files, stored procedures and DuckDB checks in SQL Autopsy. Docs download is next. See `TODO.md`.

## Run

```
pip install -r requirements.txt
streamlit run apps/zombie_hunter/app.py
streamlit run apps/sql_autopsy/app.py
pytest -q
```

## Zombie Hunter

![Results](docs/img/zombie_hunter_results.png)

- **Alert:** a full-screen alert appears above your alert level (default 10%). Try the *Retail outbreak (demo)* sample.
- **Plain Mode:** turns off the theme, pictures and alert, and shows the same findings as tables.
- **Reduced motion:** users who prefer reduced motion get no animation and no overlay, only a banner.

## SQL Autopsy

- **Paste or upload SQL:** `.sql` files with many statements and stored procedures (SQL Server, PostgreSQL, Snowflake, BigQuery and more). You get a file summary, a statement picker, and a list of steps that were not analysed (IF, DECLARE, ...).
- **Per statement:** plain-English steps, possible problems with how to fix them, and where every column comes from.
- **Your tables (optional):** upload CSVs or use the retail sample. SQL Autopsy then finds tables and columns that do not exist (with "Did you mean ...?"), columns that are in more than one table, and joins that multiply rows. **Try it on your tables** previews the result.

### Security on the public app
- Only one read-only SELECT can run. Changes, file access, COPY, ATTACH, PRAGMA, SET and INSTALL are refused before anything runs.
- The DuckDB database is locked after loading: no file or URL access, settings frozen, 512 MB memory, 2 threads.
- Previews return at most 100 rows and stop after 5 seconds. Uploads: CSV up to 50 MB (10 files), `.sql` up to 1 MB.
- Files and SQL stay in your browser session. Nothing is stored or logged.

### Later
Databricks and BigQuery connections, through the same `Connector` interface.

## License

MIT
