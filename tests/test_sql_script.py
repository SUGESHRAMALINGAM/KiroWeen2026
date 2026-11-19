from pathlib import Path

import pytest

from core.sql_analyzer import analyze
from core.sql_script import MAX_STATEMENTS, analyze_script

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- scripts

def test_three_statements_with_lines_and_summary():
    r = analyze_script("INSERT INTO mart.t SELECT a FROM raw.a;\n\nSELECT b FROM raw.b;\nDELETE FROM mart.t WHERE a < 0;")
    assert r.ok
    assert [(s.index, s.line) for s in r.all_statements] == [(1, 1), (2, 3), (3, 4)]
    assert r.tables_read == ["raw.a", "raw.b"]
    assert r.tables_written == ["mart.t"]
    assert set(r.problem_counts) == {1, 2, 3}


def test_semicolon_inside_a_string_does_not_split():
    r = analyze_script("SELECT ';' AS x FROM a; SELECT 'a;b' FROM b")
    assert [s.analysis.tables for s in r.statements] == [["a"], ["b"]]


def test_broken_statement_does_not_stop_the_others():
    r = analyze_script("SELECT 1 FROM a;\nSELECT FROM WHERE;\nSELECT 2 FROM b;\n-- only a comment\n")
    assert len(r.statements) == 2 and len(r.unreadable) == 1
    assert r.unreadable[0].line == 2 and "line 2" in r.unreadable[0].message


def test_statement_limit_and_empty_input():
    r = analyze_script("SELECT 1;" * (MAX_STATEMENTS + 1))
    assert not r.ok and "limit is 200" in r.error.message
    assert not analyze_script("  ").ok


def test_tsql_go_separator():
    r = analyze_script("SELECT a FROM t\nGO\nSELECT b FROM u\nGO\n", "tsql")
    assert [s.analysis.tables for s in r.statements] == [["t"], ["u"]]


def test_single_statement_analyze_still_works():
    assert analyze("SELECT a FROM t").ok


# ---------------------------------------------------------------- update / delete / merge

def test_update_and_delete_are_explained():
    u = analyze("UPDATE orders SET status = 'X' WHERE order_id IN (SELECT order_id FROM returns)")
    assert u.ok and u.target == "orders" and u.tables == ["returns"]
    assert "changes rows in orders" in " ".join(u.explanation)
    d = analyze("DELETE FROM orders")
    assert d.ok and [i.rule for i in d.issues] == ["no_where"]


# ---------------------------------------------------------------- stored procedures

@pytest.mark.parametrize("fixture, dialect, name, expected", [
    ("tsql_proc.sql", "tsql", "dbo.load_daily_sales",
     [("DELETE", "mart.daily_sales"), ("INSERT", "mart.daily_sales"), ("UPDATE", "dbo.load_log")]),
    ("postgres_proc.sql", "postgres", "refresh_totals",
     [("DELETE", "totals"), ("INSERT", "totals"), ("UPDATE", "audit")]),
    ("snowflake_proc.sql", "snowflake", "analytics.clean_orders",
     [("DELETE", "raw.orders"), ("INSERT", "analytics.orders_clean")]),
    ("bigquery_proc.sql", "bigquery", "proj.ds.rebuild",
     [("CREATE", "proj.ds.daily"), ("UPDATE", "proj.ds.status")]),
])
def test_procedure_bodies(fixture, dialect, name, expected):
    r = analyze_script(load(fixture), dialect)
    assert r.ok and [p.name for p in r.procedures] == [name]
    proc = r.procedures[0]
    assert [(s.analysis.statement, s.analysis.target) for s in proc.statements] == expected
    assert proc.not_analysed, "control-flow lines should be listed"
    assert all(s.procedure == name for s in proc.statements)


def test_tsql_procedure_details():
    r = analyze_script(load("tsql_proc.sql"), "tsql")
    proc = r.procedures[0]
    assert proc.params == "@run_date DATE, @min_qty INT" and proc.line == 2
    insert = next(s for s in proc.statements if s.analysis.statement == "INSERT")
    assert insert.analysis.tables == ["dbo.orders", "dbo.customers"] and insert.line == 8
    assert any(u.sql.startswith("DECLARE") for u in proc.not_analysed)
    assert [s.analysis.tables for s in r.statements] == [["mart.daily_sales"]]  # the SELECT after GO
    assert "dbo.orders" in r.tables_read and "mart.daily_sales" in r.tables_written


def test_mysql_delimiter_procedures_calls_and_empty_statement():
    r = analyze_script(load("mysql_procs.sql"), "mysql")
    assert r.ok and [p.name for p in r.procedures] == ["AddCustomer", "GetCustomerOrders", "UpdateOrderAmount",
                                                        "DeleteCustomer"]
    kinds = [s.analysis.statement for s in r.all_statements]
    assert kinds == ["INSERT", "SELECT", "UPDATE", "DELETE", "CALL", "CALL", "CALL", "CALL"]
    assert not r.all_unreadable  # DELIMITER lines and comments are no longer errors
    assert [(u.line, u.sql) for u in r.syntax_problems] == [(18, ";;;")]
    call = r.all_statements[4].analysis
    assert "runs the stored procedure AddCustomer with these values: 'Charlie'" in " ".join(call.explanation)
    assert "defined in this file on line 3" in " ".join(call.explanation)


def test_empty_statements_are_reported_but_normal_endings_are_not():
    assert analyze_script("SELECT 1 FROM a;\n").empty_statements == []
    r = analyze_script("SELECT 1 FROM a;;\nSELECT 2 FROM b;")
    assert [u.line for u in r.empty_statements] == [1] and len(r.statements) == 2
    p = analyze_script("CREATE PROCEDURE p() BEGIN SELECT 1 FROM a;; SELECT 2 FROM b; END;", "mysql")
    assert [u.procedure for u in p.empty_statements] == ["p"]


def test_insert_values_is_explained():
    a = analyze("INSERT INTO customers (customer_name) VALUES ('a'), ('b')", "mysql")
    assert a.ok and a.target == "customers"
    assert "adds 2 row(s) with fixed values into customers" in a.explanation[0]
    bad = analyze("INSERT INTO orders (qty) VALUES (1)", "duckdb", {"orders": {"quantity": "BIGINT"}})
    assert [i.rule for i in bad.issues] == ["unknown_column"]