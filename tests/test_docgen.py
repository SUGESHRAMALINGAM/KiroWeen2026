import io
import json
import subprocess
import sys
from pathlib import Path

from core.docgen import mermaid, script_doc
from core.sql_script import analyze_script

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures"


def test_document_sections_for_a_procedure_file():
    script = analyze_script((FIX / "mysql_procs.sql").read_text(encoding="utf-8"), "mysql")
    doc = script_doc(script, "mysql_procs.sql")
    for part in ["# mysql_procs.sql", "## Summary", "Statements explained: 8", "Stored procedures / functions: 4",
                 "## Data flow", "```mermaid", "flowchart LR", "## Procedures", "`GetCustomerOrders`",
                 "## Syntax problems", "Line 18", "## Statements", "**What it does**",
                 "runs the stored procedure AddCustomer"]:
        assert part in doc, part


def test_mermaid_edges_and_problems():
    script = analyze_script("INSERT INTO mart.daily SELECT o.day, c.region FROM raw.orders o JOIN raw.customers c "
                            "ON o.cid = c.id;\nSELECT * FROM mart.daily;")
    m = mermaid(script)
    assert m.count("-->") == 4  # 2 sources -> step 1 -> target, target -> step 2
    doc = script_doc(script)
    assert "Likely bug" in doc or "Slow or fragile" in doc
    assert "| Result column | Comes from |" in doc


def test_awkward_names_do_not_break_mermaid_or_tables():
    script = analyze_script('SELECT "a|b" AS "x|y" FROM "we""ird<t>"')
    m = mermaid(script)
    assert "<" not in m and ">" not in m.replace("-->", "")
    doc = script_doc(script)
    assert "x\\|y" in doc


def test_empty_script_doc_has_placeholder_node():
    m = mermaid(analyze_script("SELECT 1"))
    assert "No tables are read or written" in m


def test_cli_writes_markdown(tmp_path):
    src = tmp_path / "load.sql"
    src.write_text("INSERT INTO mart.t SELECT a FROM raw.a;", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_docs.py"), str(src), "--out", str(tmp_path / "out")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = tmp_path / "out" / "load.md"
    assert out.exists() and "```mermaid" in out.read_text(encoding="utf-8")


def test_cli_reads_hook_event_and_skips_non_sql(tmp_path):
    from scripts.gen_docs import paths_from_hook_event
    event = {"hook_event_name": "PostFileSave", "file": {"path": str(tmp_path / "x.sql")}, "other": "a.py"}
    assert paths_from_hook_event(json.dumps(event)) == [tmp_path / "x.sql"]
    assert paths_from_hook_event("not json") == []
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_docs.py"), str(tmp_path / "notes.txt")],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "not a .sql file" in r.stderr


def test_calls_are_not_counted_as_tables_written():
    script = analyze_script((FIX / "mysql_procs.sql").read_text(encoding="utf-8"), "mysql")
    assert script.tables_written == ["customers", "orders"]
    assert script.procedures_called == ["AddCustomer", "GetCustomerOrders", "UpdateOrderAmount", "DeleteCustomer"]
    doc = script_doc(script)
    assert "Procedures called: `AddCustomer`" in doc and "Runs procedure: `AddCustomer`" in doc
    assert "[[" in mermaid(script)  # procedures drawn as sub-routine boxes