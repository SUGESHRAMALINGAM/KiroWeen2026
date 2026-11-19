import csv
import tempfile
from collections import Counter
from pathlib import Path

from hypothesis import given, settings, strategies as st

from core.connectors import CsvConnector
from core.profiler import profile_table
from core.rules import Link, Zombie, find_infections, severity_for

RETAIL = Path(__file__).resolve().parents[1] / "core" / "domain_packs" / "retail"
LINKS = [Link("orders", "customer_id", "customers", "customer_id"),
         Link("orders", "product_id", "products", "product_id")]


def retail():
    con = CsvConnector()
    for f in sorted(RETAIL.glob("*.csv")):
        con.load_csv(f)
    return con


def test_retail_findings_and_score():
    rep = find_infections(retail(), "orders", ["order_id"], LINKS)
    by = {(f.zombie, f.column): f.count for f in rep.findings}
    assert by[(Zombie.GHOST, "customer_id")] == 1
    assert by[(Zombie.CLONE, None)] == 1
    assert by[(Zombie.CLONE, "order_id")] == 1
    assert by[(Zombie.LOST, "customer_id")] == 1
    assert by[(Zombie.GIANT, "quantity")] == 2
    assert (Zombie.LOST, "product_id") not in by
    assert rep.total_rows == 301 and rep.infected_rows == 5
    assert rep.percent == 1.66
    assert all(f.examples for f in rep.findings)
    assert rep.counts_by_zombie()[Zombie.GIANT] == 3  # includes id 999 (IQR rule on customer_id)


def test_clean_table_has_no_findings():
    rep = find_infections(retail(), "products")
    assert rep.findings == [] and rep.infected_rows == 0 and rep.score == 0.0


def test_ghost_count_matches_profile():
    con = retail()
    p = profile_table(con, "customers")
    rep = find_infections(con, "customers", profile=p)
    assert {f.column: f.count for f in rep.findings if f.zombie is Zombie.GHOST} == {"email": 5}


def test_mixed_text_and_dates_are_mutants(tmp_path):
    f = tmp_path / "m.csv"
    f.write_text("qty,d\n1,2026-01-01\n2,2026-01-02\nabc,2026-01-03\n4,05/01/2026\n", encoding="utf-8")
    con = CsvConnector()
    con.load_csv(f)
    rep = find_infections(con, "m")
    by = {(x.zombie, x.column): x.count for x in rep.findings}
    assert by[(Zombie.MUTANT, "qty")] == 1
    assert by[(Zombie.MUTANT, "d")] == 1
    assert rep.infected_rows == 2


def test_empty_table(tmp_path):
    f = tmp_path / "e.csv"
    f.write_text("a,b\n", encoding="utf-8")
    con = CsvConnector()
    con.load_csv(f)
    rep = find_infections(con, "e")
    assert rep.total_rows == 0 and rep.score == 0.0


def test_severity():
    assert severity_for(1, 1000) == "low"
    assert severity_for(10, 1000) == "medium"
    assert severity_for(50, 1000) == "high"
    assert severity_for(0, 0) == "low"


@settings(max_examples=40, deadline=None)
@given(st.lists(st.tuples(st.sampled_from(["x", "y", "z", None]), st.sampled_from(["p", "q", None])),
                min_size=1, max_size=50))
def test_infected_rows_count_each_row_once(rows):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.csv"
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["a", "b"])
            w.writerows([["" if v is None else v for v in r] for r in rows])
        con = CsvConnector()
        con.load_csv(p)
    seen: Counter = Counter()
    expected = 0
    for r in rows:
        seen[r] += 1
        if None in r or seen[r] > 1:
            expected += 1
    rep = find_infections(con, "t")
    assert rep.infected_rows == expected
    assert rep.total_rows == len(rows)

