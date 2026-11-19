import sqlglot
from hypothesis import given, settings, strategies as st

from core.sql_analyzer import DIALECTS, analyze, lineage_dot

BIG = """WITH big AS (SELECT customer_id, SUM(quantity) AS q FROM orders GROUP BY customer_id)
SELECT DISTINCT c.region, SUM(b.q) AS total, o.status
FROM big b LEFT JOIN customers c ON b.customer_id = c.customer_id, orders o
WHERE UPPER(c.region) = 'NORTH' AND o.order_id NOT IN (SELECT order_id FROM returns)
GROUP BY c.region, o.status ORDER BY total DESC LIMIT 10"""


def rules(sql, dialect="duckdb"):
    return sorted({i.rule for i in analyze(sql, dialect).issues})


# ---------------------------------------------------------------- structure

def test_structure_of_a_full_query():
    a = analyze(BIG)
    assert a.ok
    assert a.tables == ["customers", "orders", "returns"]  # CTE name is not a table
    assert a.ctes == ["big"]
    assert [(j.table, j.kind) for j in a.joins] == [("customers (c)", "LEFT"), ("orders (o)", "MISSING CONDITION")]
    assert len(a.filters) == 2
    assert a.group_by == ["c.region", "o.status"] and a.aggregations == ["SUM(b.q)"]
    assert a.order_by == ["total DESC"] and a.limit == "10" and a.distinct
    assert a.output_columns == ["region", "total", "status"]


def test_explanation_is_plain_and_complete():
    text = " ".join(analyze(BIG).explanation)
    for phrase in ["helper step", "reads data from: customers, orders, returns", "left join keeps every row",
                   "pairs every row with every row", "keeps only rows where", "groups by",
                   "calculates SUM(b.q) for each group", "sorts the result", "at most 10 rows"]:
        assert phrase in text, phrase


def test_insert_and_create_as_select_have_a_target():
    a = analyze("INSERT INTO mart.daily SELECT day, COUNT(*) AS n FROM raw.events GROUP BY day")
    assert a.ok and a.target == "mart.daily" and a.tables == ["raw.events"]
    assert "writes the result into mart.daily" in " ".join(a.explanation)
    b = analyze("CREATE TABLE mart.x AS SELECT id FROM raw.y")
    assert b.ok and b.target == "mart.x" and b.tables == ["raw.y"]
    assert "creates the table mart.x" in " ".join(b.explanation)


def test_union_and_multiple_statements():
    a = analyze("SELECT id FROM a UNION ALL SELECT id FROM b; SELECT 1")
    assert a.ok and a.tables == ["a", "b"] and a.statements_found == 2
    assert "2 statements" in a.explanation[0]


# ---------------------------------------------------------------- lint rules

def test_each_lint_rule_fires_on_its_example():
    assert rules("SELECT * FROM t") == ["select_star"]
    assert rules("SELECT t.* FROM t") == ["select_star"]
    assert rules("SELECT a FROM t WHERE UPPER(name) = 'X'") == ["function_on_filter"]
    assert rules("SELECT a FROM t WHERE CAST(ts AS DATE) = DATE '2026-01-01'") == ["function_on_filter"]
    assert rules("SELECT a FROM t WHERE id NOT IN (SELECT id FROM u)") == ["not_in_subquery"]
    assert rules("SELECT a FROM t CROSS JOIN u") == ["cross_join"]
    assert rules("SELECT a FROM t, u") == ["cross_join"]
    assert rules("SELECT DISTINCT t.a FROM t JOIN u ON t.id = u.id") == ["distinct_join"]


def test_clean_queries_have_no_issues():
    for sql in ["SELECT a, b FROM t WHERE a > 5",
                "SELECT t.a FROM t JOIN u ON t.id = u.id WHERE t.d >= DATE '2026-01-01'",
                "SELECT a FROM t WHERE id NOT IN (1, 2, 3)",
                "SELECT a FROM t WHERE NOT EXISTS (SELECT 1 FROM u WHERE u.id = t.id)",
                "SELECT DISTINCT a FROM t",
                "SELECT COUNT(*) FROM t WHERE a = LOWER('X')"]:
        assert rules(sql) == [], sql


def test_issues_are_plain_and_have_a_fix():
    for i in analyze(BIG).issues:
        assert i.title and i.why and i.fix and i.severity in ("high", "medium", "low")


# ---------------------------------------------------------------- lineage

def test_column_lineage_follows_aliases_and_ctes():
    sql = ("WITH big AS (SELECT customer_id, SUM(quantity) AS q FROM orders GROUP BY customer_id) "
           "SELECT c.region, SUM(b.q) AS total FROM big b JOIN customers c ON b.customer_id = c.customer_id "
           "GROUP BY c.region")
    lin = {l.output: l.sources for l in analyze(sql).lineage}
    assert lin == {"region": ("customers.region",), "total": ("orders.quantity",)}


def test_lineage_survives_unqualified_names_in_filters():
    lin = {l.output: l.sources for l in analyze(BIG).lineage}
    assert lin["region"] == ("customers.region",)
    assert lin["status"] == ("orders.status",)
    assert lin["total"] == ("orders.quantity",)


def test_lineage_single_table_and_constants():
    lin = {l.output: l.sources for l in analyze("SELECT a, b + 1 AS c, 'x' AS k FROM t").lineage}
    assert lin["a"] == ("t.a",) and lin["c"] == ("t.b",) and lin["k"] == ()


def test_lineage_dot_is_escaped():
    dot = lineage_dot(analyze('SELECT a FROM "we""ird"'))
    assert dot.startswith("digraph") and '\\"' in dot and "query result" in dot


# ---------------------------------------------------------------- errors

def test_readable_parse_errors():
    a = analyze("SELECT a FROM WHERE")
    assert not a.ok and a.error.line == 1 and a.error.col == 19
    assert "line 1, column 19" in a.error.message and "WHERE" in a.error.message
    assert "Token" not in a.error.message
    for bad, frag in [("", "Paste"), ("   ", "Paste"), ("CREATE TABLE x (a INT)", "CREATE statement"),
                      ("SELECT 1", None)]:
        res = analyze(bad)
        if frag:
            assert frag in res.error.message
        else:
            assert res.ok
    assert "Unknown SQL dialect" in analyze("SELECT 1", "klingon").error.message
    assert "too long" in analyze("SELECT 1 " + "x" * 300_000).error.message


@settings(max_examples=60, deadline=None)
@given(st.text(max_size=200))
def test_never_raises_on_garbage(text):
    a = analyze(text)
    assert a.ok or a.error.message


def test_dialect_specific_syntax():
    assert analyze("SELECT TOP 5 a FROM dbo.t", "tsql").ok
    assert analyze("SELECT a FROM `proj.ds.t` QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1",
                   "bigquery").ok
    assert analyze("SELECT a FROM cat.sch.t LIMIT 5", "databricks").tables == ["cat.sch.t"]
    assert set(DIALECTS) >= {"duckdb", "postgres", "snowflake", "bigquery", "databricks"}


# ---------------------------------------------------------------- property: parse -> regenerate keeps tables

ident = st.from_regex(r"[a-z][a-z0-9_]{0,8}", fullmatch=True).filter(
    lambda s: s.upper() not in {"AS", "ON", "OR", "AND", "IN", "IS", "BY", "TO", "IF", "DO", "AT", "END", "ALL",
                                "ANY", "SET", "TOP", "KEY", "USE", "NOT", "FOR", "ASC", "OUT", "ROW", "NEW"})


@settings(max_examples=60, deadline=None)
@given(st.lists(ident, min_size=1, max_size=4, unique=True), st.sampled_from(["duckdb", "postgres", "snowflake",
                                                                              "bigquery", "databricks"]))
def test_regenerated_sql_keeps_the_same_tables(tables, dialect):
    joins = " ".join(f"JOIN {t} ON {tables[0]}.id = {t}.id" for t in tables[1:])
    sql = f"SELECT {tables[0]}.id FROM {tables[0]} {joins}"
    a = analyze(sql, dialect)
    assert a.ok and sorted(a.tables) == sorted(tables)
    regenerated = sqlglot.transpile(sql, read=dialect, write=dialect)[0]
    assert sorted(analyze(regenerated, dialect).tables) == sorted(tables)
