import asyncio
from pathlib import Path

from mcp_server.server import analyze_sql, build_server, profile_csv

ROOT = Path(__file__).resolve().parents[1]
ORDERS = ROOT / "core" / "domain_packs" / "retail" / "orders.csv"


def test_profile_csv_finds_planted_zombies():
    r = profile_csv(str(ORDERS), ["order_id"])
    assert r["rows"] == 301 and r["infection_score_percent"] > 0
    assert r["zombies"]["ghost"] == 1 and r["zombies"]["clone"] >= 1
    assert all({"zombie", "column", "rows", "meaning"} <= set(f) for f in r["findings"])


def test_profile_csv_refuses_non_csv_and_missing(tmp_path):
    assert "Only .csv" in profile_csv(str(ROOT / "README.md"))["error"]
    assert "not found" in profile_csv(str(tmp_path / "nope.csv"))["error"]


def test_analyze_sql_tool():
    r = analyze_sql("SELECT * FROM a CROSS JOIN b;;\nSELECT x FROM c", "duckdb")
    assert r["tables_read"] == ["a", "b", "c"]
    assert {p["title"] for p in r["statements"][0]["problems"]} >= {"Reads every column (SELECT *)"}
    assert r["syntax_problems"] and r["syntax_problems"][0]["line"] == 1
    assert "Unknown dialect" in analyze_sql("SELECT 1", "klingon")["error"]


def test_server_registers_both_tools():
    server = build_server()
    names = sorted(t.name for t in asyncio.run(server.list_tools()))
    assert names == ["analyze_sql", "profile_csv"]
