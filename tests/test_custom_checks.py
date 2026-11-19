import csv
import tempfile
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from core.connectors import CsvConnector
from core.custom_checks import CustomCheckError, CustomSpec, build_custom_check, parse_values
from core.testgen import CheckKind, run_checks, to_json, to_pytest, to_sql

RETAIL = Path(__file__).resolve().parents[1] / "core" / "domain_packs" / "retail"
KEYS = ["A-1", "a-1", "O'Neil", 'say "hi"', "B 2", "ß-9"]


def retail():
    con = CsvConnector()
    for f in sorted(RETAIL.glob("*.csv")):
        con.load_csv(f)
    return con


def load_rows(rows, header, name="t"):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"{name}.csv"
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows([["" if v is None else v for v in r] for r in rows])
        con = CsvConnector()
        con.load_csv(p)
    return con


def violations(con, table, spec) -> int:
    check = build_custom_check(con, table, spec)
    (res,) = run_checks(con, [check])
    assert res.error is None, res.error
    return res.violations


# ---------------------------------------------------------------- properties

@settings(max_examples=40, deadline=None)
@given(st.lists(st.tuples(st.one_of(st.none(), st.sampled_from(KEYS)), st.one_of(st.none(), st.integers(0, 2))),
                min_size=1, max_size=40))
def test_composite_text_and_number_key_matches_python(rows):
    con = load_rows(rows, ["k1", "k2"])
    counts = Counter(r for r in rows if None not in r)
    expected = sum(1 for n in counts.values() if n > 1)
    assert violations(con, "t", CustomSpec("unique", ("k1", "k2"))) == expected


@settings(max_examples=40, deadline=None)
@given(st.lists(st.one_of(st.none(), st.sampled_from(KEYS)), min_size=1, max_size=40),
       st.sets(st.sampled_from(KEYS), min_size=1, max_size=4))
def test_accepted_values_with_awkward_text(values, allowed):
    con = load_rows([(v, 1) for v in values], ["k", "x"])
    expected = sum(1 for v in values if v is not None and v not in allowed)
    assert violations(con, "t", CustomSpec("accepted_values", ("k",), tuple(allowed))) == expected


@settings(max_examples=40, deadline=None)
@given(st.lists(st.integers(-50, 50), min_size=1, max_size=40), st.integers(-50, 50), st.integers(0, 40))
def test_range_matches_python(values, lo, width):
    hi = lo + width
    con = load_rows([(v, 1) for v in values], ["v", "x"])
    spec = CustomSpec("range", ("v",), minimum=str(lo), maximum=str(hi))
    assert violations(con, "t", spec) == sum(v < lo or v > hi for v in values)


# ---------------------------------------------------------------- keys: text, composite, normalised

def test_text_key_unique_and_normalised():
    con = load_rows([("A1", 1), (" a1", 2), ("b2", 3)], ["sku", "n"])
    assert violations(con, "t", CustomSpec("unique", ("sku",))) == 0
    spec = CustomSpec("unique", ("sku",), normalize_text=True)
    assert violations(con, "t", spec) == 1
    assert "ignoring case" in build_custom_check(con, "t", spec).description


def test_composite_key_where_each_column_repeats_but_pair_does_not():
    con = load_rows([("N", "A-1"), ("N", "A-2"), ("S", "A-1"), ("S", "A-1")], ["region", "sku"])
    assert violations(con, "t", CustomSpec("unique", ("region",))) == 2
    assert violations(con, "t", CustomSpec("unique", ("region", "sku"))) == 1


def test_not_null_on_composite():
    con = load_rows([("a", 1), (None, 2), ("c", None)], ["p", "q"])
    assert violations(con, "t", CustomSpec("not_null", ("p", "q"))) == 2
    assert violations(con, "t", CustomSpec("not_null", ("p",))) == 1


# ---------------------------------------------------------------- retail: dates, patterns, rules

def test_date_range():
    con = retail()
    expected = int(con.query("SELECT COUNT(*) AS k FROM orders WHERE order_date > DATE '2026-01-15'")["k"][0])
    spec = CustomSpec("range", ("order_date",), minimum="2026-01-01", maximum="2026-01-15")
    assert violations(con, "orders", spec) == expected > 0


def test_pattern_is_a_full_match():
    con = retail()
    done = int(con.query("SELECT COUNT(*) AS k FROM orders WHERE status = 'DONE'")["k"][0])
    assert violations(con, "orders", CustomSpec("pattern", ("status",), pattern="NEW|SHIPPED")) == done
    assert violations(con, "orders", CustomSpec("pattern", ("status",), pattern="NE")) > 0  # partial is not a match
    assert violations(con, "orders", CustomSpec("pattern", ("status",), pattern="[A-Z]+")) == 0


def test_pattern_on_text_key():
    con = load_rows([("AB-001", 1), ("CD-123", 2), ("ab-1", 3)], ["sku", "n"])
    assert violations(con, "t", CustomSpec("pattern", ("sku",), pattern="[A-Z]{2}-[0-9]{3}")) == 1


def test_sql_rule_and_rule_reading_another_table():
    con = retail()
    assert violations(con, "orders", CustomSpec("condition", condition="quantity <= 0")) == 1
    orphan = CustomSpec("condition", condition=(
        "customer_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM customers c WHERE c.customer_id = t.customer_id)"))
    check = build_custom_check(con, "orders", orphan)
    assert run_checks(con, [check])[0].violations == 1
    assert check.tables == ("customers",) and check.kind is CheckKind.CUSTOM


def test_custom_checks_export_with_the_others():
    con = retail()
    check = build_custom_check(con, "orders", CustomSpec("condition", condition=(
        "NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id = t.customer_id)")))
    assert "custom" in to_json([check]) and "-- expect 0" in to_sql([check])
    code = to_pytest([check])
    compile(code, "generated.py", "exec")
    assert "'customers'" in code and "'orders'" in code


def test_ids_are_stable_and_distinct():
    con = retail()
    a = build_custom_check(con, "orders", CustomSpec("not_null", ("status",)))
    b = build_custom_check(con, "orders", CustomSpec("not_null", ("status",)))
    c = build_custom_check(con, "orders", CustomSpec("not_null", ("status", "quantity")))
    assert a.id == b.id != c.id


# ---------------------------------------------------------------- friendly errors, never a raw crash

@pytest.mark.parametrize("spec, fragment", [
    (CustomSpec("not_null", ("nope",)), "Unknown column"),
    (CustomSpec("not_null", ()), "Pick at least one column"),
    (CustomSpec("not_null", ("status", "status")), "listed twice"),
    (CustomSpec("accepted_values", ("status",), ()), "at least one allowed value"),
    (CustomSpec("accepted_values", ("quantity",), ("abc",)), "not a number"),
    (CustomSpec("accepted_values", ("order_date",), ("not-a-date",)), "valid date"),
    (CustomSpec("accepted_values", ("status", "quantity"), ("a",)), "exactly one column"),
    (CustomSpec("range", ("status",), minimum="1"), "number or date column"),
    (CustomSpec("range", ("quantity",)), "minimum, a maximum"),
    (CustomSpec("range", ("quantity",), minimum="10", maximum="1"), "larger than the maximum"),
    (CustomSpec("pattern", ("status",), pattern=""), "Enter a pattern"),
    (CustomSpec("pattern", ("status",), pattern="["), "not valid"),
    (CustomSpec("condition", condition=""), "Enter a rule"),
    (CustomSpec("condition", condition="quantity <"), "not valid SQL"),
    (CustomSpec("condition", condition="no_such_column > 1"), "could not run"),
    (CustomSpec("condition", condition="quantity < 0; DROP TABLE orders"), "semicolon"),
    (CustomSpec("nonsense"), "Unknown check type"),
])
def test_invalid_specs_give_readable_errors(spec, fragment):
    with pytest.raises(CustomCheckError) as e:
        build_custom_check(retail(), "orders", spec)
    assert fragment in str(e.value)
    assert "Traceback" not in str(e.value) and "\x1b" not in str(e.value)


def test_unknown_table():
    with pytest.raises(CustomCheckError, match="Unknown table"):
        build_custom_check(retail(), "ghosts", CustomSpec("not_null", ("a",)))


@pytest.mark.parametrize("cond", [
    "quantity IN (SELECT 1 FROM read_csv('secret.csv'))",
    "quantity IN (SELECT * FROM 'secret.csv')",
    "read_text('secret.txt') IS NULL",
    "quantity IN (SELECT 1 FROM nosuchtable)",
    "1=1) UNION SELECT 1 FROM customers --",
    "quantity IN (WITH x AS (SELECT 1) SELECT * FROM x)",
    "glob('*') IS NULL",
])
def test_rules_cannot_reach_files_or_other_statements(cond):
    with pytest.raises(CustomCheckError):
        build_custom_check(retail(), "orders", CustomSpec("condition", condition=cond))


def test_parse_values():
    assert parse_values("NEW\nSHIPPED\n\nDONE\n") == ("NEW", "SHIPPED", "DONE")
    assert parse_values("NEW, SHIPPED ,DONE") == ("NEW", "SHIPPED", "DONE")
    assert parse_values("Smith, John\nDoe, Jane") == ("Smith, John", "Doe, Jane")
    assert parse_values("") == ()


def test_ready_made_formats_work_on_real_values():
    from core.custom_checks import PRESET_PATTERNS
    rows = [("São Paulo", "40213", "ana@example.com", "+1 555 123 4567", "2026-01-31"),
            ("New York", "x12", "not-an-email", "12", "31/01/2026")]
    con = load_rows(rows, ["city", "zip", "email", "phone", "day"])
    for col, shape in [("city", "Letters only (spaces, - and ' allowed)"), ("zip", "Digits only"),
                       ("email", "Email address"), ("phone", "Phone number"), ("day", "Date like 2026-01-31")]:
        spec = CustomSpec("pattern", (col,), pattern=PRESET_PATTERNS[shape][0])
        expected = 0 if col == "city" else 1
        assert violations(con, "t", spec) == expected, col
        assert "looks like" in build_custom_check(con, "t", spec).description


def test_plain_text_pattern_is_detected_and_examples_are_shown():
    from core.custom_checks import failing_examples, looks_like_plain_text
    assert looks_like_plain_text("090909909") and looks_like_plain_text("abc")
    assert not looks_like_plain_text("[0-9]+") and not looks_like_plain_text("")
    con = load_rows([("Paris", 1), ("Rome", 2)], ["City", "n"])
    check = build_custom_check(con, "t", CustomSpec("pattern", ("City",), pattern="090909909"))
    assert run_checks(con, [check])[0].violations == 2
    ex = failing_examples(con, check)
    assert list(ex["City"]) == ["Paris", "Rome"]


def test_examples_for_unique_show_the_repeated_key():
    from core.custom_checks import failing_examples
    con = load_rows([("A", 1), ("A", 2), ("B", 3)], ["k", "n"])
    check = build_custom_check(con, "t", CustomSpec("unique", ("k",)))
    assert list(failing_examples(con, check)["k"]) == ["A"]

def test_heavy_custom_rule_is_stopped_not_crashing():
    con = retail()
    con.lock(timeout_s=1)
    heavy = CustomSpec("condition", condition=(
        "(SELECT COUNT(*) FROM orders a, orders b, orders c, orders d) > 0"))
    check = build_custom_check(con, "orders", heavy)   # EXPLAIN only, so building is instant
    res = run_checks(con, [check])[0]
    assert not res.passed and "took longer than 1 seconds" in res.error