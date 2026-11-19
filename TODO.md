# To-do list

Each item is ticked when it is done.

## Done so far
- [x] Task 1: Repo, steering, CI
- [x] Task 2: CSV connector + retail sample data
- [x] Task 3: Profiler
- [x] Task 4: Infection rules + Zombie Hunter (plain tables)
- [x] Task 5: Automatic checks + custom rules
- [x] Task 6: Zombie UI (theme, overlay, Plain Mode)
- [x] Task 7: SQL Autopsy (explain, problems, lineage)
- [x] SQL files, stored procedures and DuckDB checks in SQL Autopsy
- [x] CI fix (pytest.ini), CI green on GitHub

## Now: finish everything before the final step
- [x] 1. Security: lock Zombie Hunter's database (no file access, memory cap) and stop slow queries after 10 s
- [x] 2. Pin library versions in requirements.txt (the versions we tested)
- [x] 3. Docs download (BRD Task 8, FR-19): Markdown + Mermaid diagram from SQL Autopsy, plus a command-line script
- [x] 4. MCP server (FR-24): tools `profile_csv` and `analyze_sql`, with config added but switched off until you approve
- [x] 5. Specs + README updated, full test run
- [ ] 6. Kiro hooks (FR-23): needs you to open the `KiroWeen2026` folder as the workspace first (see notes)

## Cut on purpose
- Task 9: rewrite verification + HR / Finance / Marketing packs (the plan says cut this first)
- Optional LLM wording (the plan says cut it; the app works without a key)

## Final (after the list above)
- [ ] You push to GitHub (I give the exact commands, you run them)
- [ ] You deploy both apps on Streamlit Community Cloud
- [ ] README gets the live links, then demo video and Devpost write-up

## Future
- Databricks / BigQuery connectors through the same `Connector` interface
