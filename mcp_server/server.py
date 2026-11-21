"""DE Bro Code MCP server (BRD FR-24): lets Kiro call the same engine the apps use.

Tools:
- profile_csv(path, key_columns): profile a local CSV and return its zombies (infections) and score.
- analyze_sql(sql, dialect): explain, lint and trace lineage for SQL. Nothing is executed.

Runs locally over stdio only (no network port). profile_csv reads one local .csv file (max 200 MB) into an
in-memory DuckDB that is locked right after loading, so no other file can be read.

Run: py mcp_server/server.py   (Kiro starts it from .kiro/settings/mcp.json)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.connectors import CsvConnector  # noqa: E402
from core.rules import find_infections  # noqa: E402
from core.sql_analyzer import DIALECTS  # noqa: E402
from core.sql_script import analyze_script  # noqa: E402

MAX_CSV_BYTES = 200 * 1024 * 1024
TIMEOUT_S = 30


def profile_csv(path: str, key_columns: list[str] | None = None) -> dict:
    """Profile ONE local CSV file: infection score, zombie counts and findings with example rows.
    key_columns (optional) are columns that should identify a row; they are used to find repeated keys.
    Lost zombies (broken links) need two tables, so they are never checked here: use the Zombie Hunter
    app with several files for that."""
    p = Path(path).expanduser()
    if p.suffix.lower() != ".csv":
        return {"error": "Only .csv files can be profiled."}
    if not p.is_file():
        return {"error": f"File not found: {p}"}
    if p.stat().st_size > MAX_CSV_BYTES:
        return {"error": f"The file is larger than {MAX_CSV_BYTES // 1024 // 1024} MB."}
    con = CsvConnector()
    loaded = con.load_csv(p)
    con.lock(timeout_s=TIMEOUT_S)
    try:
        report = find_infections(con, loaded.table, key_columns or None)
    except (KeyError, RuntimeError) as e:
        return {"error": str(e)}
    return {
        "table": report.table,
        "rows": report.total_rows,
        "infected_rows": report.infected_rows,
        "infection_score_percent": report.percent,
        "zombies": {z.value: n for z, n in report.counts_by_zombie().items()},
        "rejected_rows": len(loaded.rejected),
        "findings": [{"zombie": f.label, "column": f.column, "rows": f.count, "severity": f.severity,
                      "meaning": f.plain, "examples": [{k: str(v) for k, v in row.items()} for row in f.examples[:3]]}
                     for f in report.findings],
    }


def analyze_sql(sql: str, dialect: str = "duckdb") -> dict:
    """Explain SQL in plain English, list likely problems and trace column lineage. Works on whole files and
    stored procedures. The SQL is only parsed, never run."""
    if dialect not in DIALECTS:
        return {"error": f"Unknown dialect '{dialect}'. Use one of: {', '.join(DIALECTS)}."}
    script = analyze_script(sql, dialect)
    if not script.ok:
        return {"error": script.error.message}
    return {
        "dialect": dialect,
        "tables_read": script.tables_read,
        "tables_written": script.tables_written,
        "procedures": [{"name": p.name, "line": p.line, "statements": len(p.statements)} for p in script.procedures],
        "syntax_problems": [{"line": u.line, "message": u.message} for u in script.syntax_problems],
        "statements": [{
            "index": s.index, "line": s.line, "procedure": s.procedure, "statement": s.analysis.statement,
            "explanation": s.analysis.explanation,
            "problems": [{"severity": i.severity, "title": i.title, "why": i.why, "fix": i.fix}
                         for i in s.analysis.issues],
            "lineage": [{"column": l.output, "sources": list(l.sources)} for l in s.analysis.lineage],
        } for s in script.all_statements],
    }


def build_server():
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("de-bro-code", instructions="Data engineering helpers: profile CSV files for data "
                       "problems (zombies) and explain / lint SQL. Read-only.")
    server.tool(description=profile_csv.__doc__)(profile_csv)
    server.tool(description=analyze_sql.__doc__)(analyze_sql)
    return server


if __name__ == "__main__":
    build_server().run("stdio")
