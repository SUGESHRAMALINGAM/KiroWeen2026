import csv
import statistics
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from core.connectors import CsvConnector
from core.profiler import profile_table

RETAIL = Path(__file__).resolve().parents[1] / "core" / "domain_packs" / "retail"


def load(rows, header=("a", "b")):
    """Write rows to a temp CSV and load it. Returns (connector, table)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.csv"
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows([["" if v is None else v for v in r] for r in rows])
        con = CsvConnector()
        con.load_csv(p)
    return con, "t"


# ---- property tests: injected defects are found exactly ----

@settings(max_examples=40, deadline=None)
@given(st.lists(st.one_of(st.none(), st.integers(0, 50)), min_size=1, max_size=60))
def test_null_and_distinct_counts(values):
    con, t = load([(v, 1) for v in values])
    c = profile_table(con, t).column("a")
    assert c.row_count == len(values)
    assert c.null_count == sum(v is None for v in values)
    assert c.distinct_count == len({v for v in values if v is not None})


@settings(max_examples=40, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 4), st.integers(0, 4)), min_size=1, max_size=60))
def test_duplicate_rows_and_keys(rows):
    con, t = load(rows)
    p = profile_table(con, t, key_columns=["a"])
    assert p.duplicate_rows == len(rows) - len(set(rows))
    assert p.duplicate_keys == len(rows) - len({r[0] for r in rows})


@settings(max_examples=40, deadline=None)
@given(st.lists(st.integers(-1000, 1000), min_size=2, max_size=60))
def test_outliers_match_iqr_rule(values):
    con, t = load([(v, 1) for v in values])
    c = profile_table(con, t).column("a")
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    assert c.outlier_count == sum(v < lo or v > hi for v in values)
    assert c.min == min(values) and c.max == max(values)


@settings(max_examples=30, deadline=None)
@given(st.lists(st.integers(-100, 100), min_size=2, max_size=40))
def test_mean_matches(values):
    con, t = load([(v, 1) for v in values])
    c = profile_table(con, t).column("a")
    assert c.mean == pytest.approx(statistics.fmean(values))
    assert c.stddev == pytest.approx(statistics.stdev(values))


@settings(max_examples=30, deadline=None)
@given(st.lists(st.integers(0, 10**6), min_size=2, max_size=40, unique=True))
def test_candidate_keys(ids):
    con, t = load([(i, 1) for i in ids])
    assert profile_table(con, t).candidate_keys == ["a"]


# ---- unit tests ----

def test_injected_outlier_is_found():
    con, t = load([(v, 1) for v in [10, 11, 12, 13, 14, 15, 16, 9999]])
    assert profile_table(con, t).column("a").outlier_count == 1


def test_numbers_stored_as_text_and_mixed():
    con, t = load([("x", 1), ("2", 1), ("3", 1)])
    c = profile_table(con, t).column("a")
    assert c.mixed_numeric_text and (c.numeric_text_count, c.non_numeric_text_count) == (2, 1)
    assert not c.numbers_stored_as_text


def test_numbers_stored_as_text_all_numeric():
    con = CsvConnector()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.csv"
        p.write_text('a\n"007"\n"010"\n', encoding="utf-8")
        con._con.execute(f"CREATE TABLE t AS SELECT * FROM read_csv('{p.as_posix()}', all_varchar=true)")
    c = profile_table(con, "t").column("a")
    assert c.numbers_stored_as_text and c.type == "VARCHAR"


def test_mixed_date_formats():
    con, t = load([("2026-01-05", 1), ("05/01/2026", 1), ("2026-02-01", 1)])
    c = profile_table(con, t).column("a")
    assert c.mixed_date_formats
    assert c.date_format_counts["YYYY-MM-DD"] == 2 and c.date_format_counts["DD/MM/YYYY"] == 1


def test_unknown_key_column():
    con, t = load([(1, 1)])
    with pytest.raises(KeyError):
        profile_table(con, t, key_columns=["nope"])


def test_quoted_awkward_column_names():
    con, t = load([(1, 2), (1, 2)], header=('we"ird', "has space"))
    p = profile_table(con, t)
    assert p.duplicate_rows == 1 and p.column('we"ird').distinct_count == 1


def test_retail_planted_defects():
    con = CsvConnector()
    for f in sorted(RETAIL.glob("*.csv")):
        con.load_csv(f)
    o = profile_table(con, "orders", key_columns=["order_id"])
    assert o.row_count == 301
    assert o.duplicate_rows == 1
    assert o.duplicate_keys == 1
    assert o.column("customer_id").null_count == 1
    assert o.column("quantity").outlier_count >= 1
    assert o.column("quantity").max == 5000 and o.column("quantity").min == -3
    cust = profile_table(con, "customers")
    assert "customer_id" in cust.candidate_keys and "region" not in cust.candidate_keys
    assert cust.column("email").null_count == 5


