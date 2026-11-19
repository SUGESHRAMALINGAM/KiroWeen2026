import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from hypothesis import given, settings, strategies as st

from core.connectors import CsvConnector
from core.profiler import profile_table
from core.rules import Link
from core.testgen import (CheckKind, generate_checks, run_checks, suggest_links,
                          to_json, to_pytest, to_sql)

RETAIL = Path(__file__).resolve().parents[1] / "core" / "domain_packs" / "retail"
LINKS = [Link("orders", "customer_id", "customers", "customer_id"),
         Link("orders", "product_id", "products", "product_id")]


def retail():
    con = CsvConnector()
    for f in sorted(RETAIL.glob("*.csv")):
        con.load_csv(f)
    return con


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows([["" if v is None else v for v in r] for r in rows])


def load_table(rows, name="t", header=("id", "val", "status")):
    with tempfile.TemporaryDirectory() as d:
        write_csv(Path(d) / f"{name}.csv", header, rows)
        con = CsvConnector()
        con.load_csv(Path(d) / f"{name}.csv")
    return con


def outcomes(con, checks):
    return {r.check.id: r.passed for r in run_checks(con, checks)}


# ---- property: checks pass on clean data and the matching check fails on injected defects ----

@settings(max_examples=30, deadline=None)
@given(st.lists(st.sampled_from(["A", "B"]), min_size=12, max_size=50),
       st.sampled_from(["null", "duplicate", "bad_value", "outlier"]),
       st.data())
def test_checks_pass_on_clean_and_fail_on_bad(statuses, defect, data):
    clean = [(i, i, s) for i, s in enumerate(statuses)]
    con_clean = load_table(clean)
    checks = generate_checks(con_clean, profile_table(con_clean, "t"))
    assert all(outcomes(con_clean, checks).values())

    k = data.draw(st.integers(1, len(clean) - 1))
    bad = [list(r) for r in clean]
    expected = {
        "null": ("not_null__t__val", lambda: bad[k].__setitem__(1, None)),
        "duplicate": ("unique__t__id", lambda: bad[k].__setitem__(0, 0)),
        "bad_value": ("accepted_values__t__status", lambda: bad[k].__setitem__(2, "Z")),
        "outlier": ("range__t__val", lambda: bad[k].__setitem__(1, 10**6)),
    }[defect]
    expected[1]()
    result = outcomes(load_table(bad), checks)
    assert result[expected[0]] is False
    assert [i for i, ok in result.items() if not ok] == [expected[0]]


@settings(max_examples=25, deadline=None)
@given(st.integers(3, 15), st.lists(st.integers(0, 14), min_size=1, max_size=30))
def test_referential_check(n_parent, child_refs):
    refs = [r % n_parent for r in child_refs]
    link = [Link("child", "pid", "parent", "id")]

    def build(child_values):
        with tempfile.TemporaryDirectory() as d:
            write_csv(Path(d) / "parent.csv", ["id", "x"], [(i, i) for i in range(n_parent)])
            write_csv(Path(d) / "child.csv", ["pid", "y"], [(v, 1) for v in child_values])
            con = CsvConnector()
            con.load_csv(Path(d) / "parent.csv")
            con.load_csv(Path(d) / "child.csv")
        return con

    con = build(refs)
    checks = generate_checks(con, profile_table(con, "child"), link)
    ri = [c for c in checks if c.kind is CheckKind.REFERENTIAL_INTEGRITY]
    assert len(ri) == 1
    assert run_checks(con, ri)[0].violations == 0
    bad = build(refs + [n_parent + 7])
    assert run_checks(bad, ri)[0].violations == 1


# ---- unit tests ----

def test_retail_orders_checks():
    con = retail()
    checks = generate_checks(con, profile_table(con, "orders", ["order_id"]), LINKS)
    res = {r.check.id: r for r in run_checks(con, checks)}
    assert res["not_null__orders__customer_id"].violations == 1
    assert res["unique__orders__order_id"].violations == 1
    assert res["referential_integrity__orders__customer_id"].violations == 1
    assert res["referential_integrity__orders__product_id"].passed
    assert res["accepted_values__orders__status"].passed
    assert res["range__orders__quantity"].violations == 2
    assert res["not_null__orders__order_id"].passed


def test_exports():
    con = retail()
    checks = generate_checks(con, profile_table(con, "orders", ["order_id"]), LINKS)
    data = json.loads(to_json(checks))
    assert len(data) == len(checks) and all(d["expect"] == 0 and d["sql"] for d in data)
    sql = to_sql(checks)
    assert sql.count("-- expect 0") == len(checks)
    compile(to_pytest(checks), "generated_test.py", "exec")


def test_exported_pytest_runs_and_detects_defects(tmp_path):
    con = retail()
    checks = generate_checks(con, profile_table(con, "orders", ["order_id"]), LINKS)
    (tmp_path / "test_generated.py").write_text(to_pytest(checks), encoding="utf-8")
    for f in RETAIL.glob("*.csv"):
        (tmp_path / f.name).write_bytes(f.read_bytes())
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
                       capture_output=True, text=True, env={**os.environ, "ZOMBIE_DATA_DIR": str(tmp_path)})
    # 5 fail: null, duplicate key, orphan, quantity range, and customer_id range (999); the rest pass
    assert "5 failed" in r.stdout, r.stdout[-800:]
    assert "passed" in r.stdout


def test_failing_query_is_reported_not_raised():
    con = retail()
    from core.testgen import Check
    bad = Check("x", CheckKind.NOT_NULL, "orders", "c", "d", "SELECT COUNT(*) FROM nope")
    r = run_checks(con, [bad])[0]
    assert not r.passed and r.error


def test_suggest_links_on_retail():
    s = suggest_links(retail(), ["customers", "orders", "products"])
    pairs = {(x.link.child, x.link.child_column, x.link.parent) for x in s}
    assert pairs == {("orders", "customer_id", "customers"), ("orders", "product_id", "products")}
    assert all(x.status == "suggested" for x in s)
    cust = next(x for x in s if x.link.parent == "customers")
    assert 0.9 <= cust.match_ratio < 1.0


def test_quoting_of_awkward_names():
    con = load_table([(1, 2, "A")] * 3, header=('i"d', "va l", "st'atus"))
    checks = generate_checks(con, profile_table(con, "t", ['i"d']))
    assert all(r.error is None for r in run_checks(con, checks))

