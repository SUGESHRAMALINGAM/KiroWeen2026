# Structure

```
.kiro/            specs, steering, hooks (always committed)
core/             connectors/, profiler, rules, testgen, sql_analyzer, docgen, llm, domain_packs/
ui_kit/           theme.css, sprites.py, overlay.py
apps/zombie_hunter/app.py
apps/sql_autopsy/app.py
mcp_server/server.py
tests/            unit + Hypothesis
docs/             BRD, tracker
```

Conventions:
- One Kiro spec per delivery task in `.kiro/specs/<NN>-<name>/`.
- Task numbers and requirement IDs (FR-xx, NFR-xx) come from docs/DE_Bro_Code_Plan_Tracker.xlsx. Reference them in specs and commit messages.
- Each app adds the repo root to `sys.path` at the top of `app.py` so `core` and `ui_kit` import when launched by Streamlit.
- Tests mirror core modules: `tests/test_<module>.py`.
