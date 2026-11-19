import time

import pytest

from core.sandbox import Sandbox, row_explosion_issue
from core.sql_analyzer import analyze
from core.sql_script import analyze_script


@pytest.fixture(scope="module")
def box():
    return Sandbox.retail_sample()


def test_schema_and_rows(box):
    s = box.schema()
    assert set(s) == {"customers", "orders", "products"}
    assert "quantity" in s["orders"] and box.table_rows["orders"] == 301


def test_preview_is_capped(box):
    r = box.preview("SELECT * FROM orders")
    assert r.ok and len(r.df) == 100 and r.truncated
    small = box.preview("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")
    assert small.ok and len(small.df) == 3 and not small.truncated


def test_preview_other_dialect_is_translated(box):
    r = box.preview("SELECT TOP 5 order_id FROM orders", "tsql")
    assert r.ok and len(r.df) == 5


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('C:/Windows/win.ini')",
    "SELECT * FROM '/etc/passwd'",
    "COPY orders TO 'out.csv'",
    "ATTACH 'x.db'",
    "SET enable_external_access = true",
    "INSTALL httpfs",
    "DROP TABLE orders",
    "INSERT INTO orders SELECT * FROM orders",
    "UPDATE orders SET status = 'x'",
    "SELECT 1 FROM orders; SELECT 2 FROM orders",
    "PRAGMA database_list",
    "SELECT getenv('PATH')",
    "SELECT * FROM other_db.main.orders",
    "SELECT * INTO new_table FROM orders",
    "SELECT * FROM returns",
])
def test_unsafe_or_unknown_sql_is_refused(box, sql):
    r = box.preview(sql)
    assert not r.ok and r.error and "Traceback" not in r.error


def test_database_itself_is_locked(box):
    cur = box._con.cursor()  # bypass validation on purpose
    for sql in ["SELECT * FROM read_csv('C:/Windows/win.ini')", "SET enable_external_access = true",
                "COPY orders TO 'out.csv'"]:
        with pytest.raises(Exception):
            cur.execute(sql)


def test_slow_query_is_stopped(box):
    t0 = time.time()
    r = box.preview("SELECT COUNT(*) FROM orders a, orders b, orders c, orders d, orders e", timeout_s=1)
    assert not r.ok and "stopped" in r.error and time.time() - t0 < 6


def test_upload_limits_and_names():
    box = Sandbox.from_files([("../../evil name.csv", b"a,b\n1,2\n"), ("big.csv", b"x" * (51 * 1024 * 1024))])
    assert list(box.table_rows) == ["evil_name"]
    assert any("big.csv was skipped" in n for n in box.notes)


# ---------------------------------------------------------------- schema checks

def rules(sql, box, dialect="duckdb"):
    return {i.rule: i for i in analyze(sql, dialect, box.schema()).issues}


def test_unknown_column_with_suggestion(box):
    i = rules("SELECT qty FROM orders", box)["unknown_column"]
    assert i.title == "Column 'qty' does not exist in orders" and "Did you mean quantity?" in i.why


def test_unknown_table_and_ambiguous_column(box):
    assert "unknown_table" in rules("SELECT * FROM returns", box)
    amb = rules("SELECT customer_id FROM orders o JOIN customers c ON o.customer_id = c.customer_id", box)
    assert "ambiguous_column" in amb


def test_cte_columns_and_aliases_are_known(box):
    sql = ("WITH b AS (SELECT customer_id, SUM(quantity) AS q FROM orders GROUP BY customer_id) "
           "SELECT b.q, c.region FROM b JOIN customers c ON b.customer_id = c.customer_id ORDER BY q")
    assert not {"unknown_column", "unknown_table", "ambiguous_column"} & set(rules(sql, box))


def test_schema_resolves_lineage(box):
    sql = "SELECT region, quantity FROM orders o JOIN customers c ON o.customer_id = c.customer_id"
    lin = {l.output: l.sources for l in analyze(sql, "duckdb", box.schema()).lineage}
    assert lin == {"region": ("customers.region",), "quantity": ("orders.quantity",)}


def test_row_explosion(box):
    bad = "SELECT c.region, SUM(o.quantity) FROM orders o JOIN customers c ON o.customer_id = c.customer_id, products p GROUP BY 1"
    i = row_explosion_issue(bad, "duckdb", box.count_rows, box.table_rows)
    assert i is not None and i.rule == "row_explosion" and "8,970" in i.why  # 299 matching orders x 30 products
    ok = "SELECT c.region FROM orders o JOIN customers c ON o.customer_id = c.customer_id"
    assert row_explosion_issue(ok, "duckdb", box.count_rows, box.table_rows) is None


def test_script_with_schema(box):
    r = analyze_script("SELECT qty FROM orders;\nSELECT region FROM customers;", "duckdb", box.schema())
    assert [len(s.analysis.issues) for s in r.statements] == [1, 0]
