# Tech

- Python 3.11+. UI: Streamlit. Engine: DuckDB. SQL parsing: sqlglot. Display: pandas (small results only). Config: PyYAML.
- Tests: pytest and Hypothesis. Integration: MCP Python SDK.
- Optional LLM: Gemini or Groq free tier, user supplies the key. Always fall back to template text.

Rules:
- `core/` must not import Streamlit. UI code lives in `apps/` and `ui_kit/`.
- The profiler only calls `Connector.query()` with aggregate SQL so future warehouse connectors reuse it.
- Connector interface: `list_tables()`, `get_schema(table)`, `sample(table, n)`, `query(sql)`.
- Use parameterized or properly quoted identifiers when building SQL from column names.
- No secrets in code or logs. Use env vars or Streamlit secrets.
- Type hints and dataclasses for results. Keep functions small and deterministic.

Commands:
- Install: `pip install -r requirements.txt`
- Test: `pytest -q`
- Run app 1: `streamlit run apps/zombie_hunter/app.py`
- Run app 2: `streamlit run apps/sql_autopsy/app.py`
