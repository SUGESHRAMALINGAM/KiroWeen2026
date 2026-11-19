import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from core.sandbox import Sandbox, row_explosion_issue
from core.sql_analyzer import DIALECTS, analyze, lineage_dot
from core.sql_script import analyze_script
from ui_kit import inject_theme, issue_html, page_hero_html

EXAMPLES = {
    "Sales by region (has problems)": (
        "SELECT DISTINCT c.region, SUM(o.quantity) AS units\n"
        "FROM orders o\n"
        "JOIN customers c ON o.customer_id = c.customer_id, products p\n"
        "WHERE UPPER(c.region) = 'NORTH'\n"
        "  AND o.order_id NOT IN (SELECT order_id FROM returns)\n"
        "GROUP BY c.region\n"
        "ORDER BY units DESC"),
    "Monthly report (clean)": (
        "WITH monthly AS (\n"
        "  SELECT customer_id, DATE_TRUNC('month', order_date) AS month, SUM(quantity) AS units\n"
        "  FROM orders\n"
        "  WHERE order_date >= DATE '2026-01-01'\n"
        "  GROUP BY customer_id, DATE_TRUNC('month', order_date)\n"
        ")\n"
        "SELECT c.region, m.month, SUM(m.units) AS units\n"
        "FROM monthly m\n"
        "LEFT JOIN customers c ON m.customer_id = c.customer_id\n"
        "GROUP BY c.region, m.month\n"
        "ORDER BY m.month\n"
        "LIMIT 100"),
    "Totals that are too big (load the retail sample)": (
        "SELECT c.region, SUM(o.quantity) AS units\n"
        "FROM orders o\n"
        "JOIN customers c ON o.customer_id = c.customer_id, products p\n"
        "GROUP BY c.region"),
    "Script with a stored procedure (SQL Server)": (
        "CREATE PROCEDURE dbo.refresh_sales @day DATE\n"
        "AS\n"
        "BEGIN\n"
        "    DELETE FROM mart.sales WHERE sale_date = @day;\n"
        "    INSERT INTO mart.sales (sale_date, units)\n"
        "    SELECT order_date, SUM(quantity) FROM orders WHERE order_date = @day GROUP BY order_date;\n"
        "    IF @@ROWCOUNT = 0 RETURN;\n"
        "END\n"
        "GO\n"
        "SELECT * FROM mart.sales;"),
    "Load a mart table": (
        "INSERT INTO mart.daily_sales\n"
        "SELECT order_date, status, COUNT(*) AS orders, SUM(quantity) AS units\n"
        "FROM raw.orders\n"
        "GROUP BY order_date, status"),
}
MAX_SQL_FILE_BYTES = 1024 * 1024
SEVERITY_WORDS = {"high": "Likely bug", "medium": "Slow or fragile", "low": "Worth a look"}

st.set_page_config(page_title="SQL Autopsy", page_icon="🔬", layout="wide")


def load_example(name: str) -> None:
    st.session_state["sql_text"] = EXAMPLES[name]


with st.sidebar:
    st.markdown("### Settings")
    plain = st.toggle("Plain Mode", key="plain_mode", help="Simple look with no theme or pictures.")
    dialect = st.selectbox("Which database is this SQL for?", list(DIALECTS), format_func=DIALECTS.get,
                           key="dialect", help="Each database writes some SQL a little differently.")
    st.markdown("### Try an example")
    example = st.selectbox("Example query", list(EXAMPLES), key="example_pick")
    st.button("Load example", key="load_example", on_click=lambda: load_example(st.session_state["example_pick"]))

inject_theme(plain)
icon = (lambda e: "") if plain else (lambda e: e + " ")

if plain:
    st.title("SQL Autopsy")
    st.caption("Paste a SQL query. We explain what it does, point out risky parts and show where the data comes from.")
else:
    st.markdown(page_hero_html(
        "SQL", "Autopsy",
        "Paste SQL or upload a .sql file (stored procedures too). We take it apart: what it does in plain English, "
        "which parts are risky, and where every column comes from. Load your tables to check it on real data.",
        ("mutant", "lost", "giant")), unsafe_allow_html=True)

# ---------------------------------------------------------------- your tables (optional DuckDB sandbox)
def use_sample() -> None:
    st.session_state["box_source"] = "sample"


def clear_tables() -> None:
    st.session_state["box_source"] = None
    st.session_state.pop("box", None)
    st.session_state.pop("box_sig", None)


with st.sidebar:
    st.markdown("### Your tables (optional)")
    st.caption("Load CSV files to check your SQL against real tables and preview the result.")
    csvs = st.file_uploader("CSV files", type="csv", accept_multiple_files=True, key="csv_files")
    c1, c2 = st.columns(2)
    c1.button("Use retail sample", key="use_sample", on_click=use_sample)
    c2.button("Clear tables", key="clear_tables", on_click=clear_tables)
    st.caption("🔒 Files stay in this session only. Queries are read-only, at most 100 rows and 5 seconds. "
               "Nothing is stored.")

if csvs:
    sig = ("files",) + tuple((u.name, u.size) for u in csvs)
elif st.session_state.get("box_source") == "sample":
    sig = ("sample",)
else:
    sig = None
if sig is None:
    st.session_state.pop("box", None)
elif st.session_state.get("box_sig") != sig:
    with st.spinner("Loading your tables..."):
        st.session_state["box"] = (Sandbox.retail_sample() if sig == ("sample",)
                                   else Sandbox.from_files([(u.name, u.getvalue()) for u in csvs]))
    st.session_state["box_sig"] = sig
    st.session_state["previews"] = {}
    st.session_state["explosions"] = {}
box: Sandbox | None = st.session_state.get("box")
schema = box.schema() if box else None
if box:
    with st.sidebar.expander(f"Loaded tables ({len(schema)})", expanded=False):
        for t, cols in schema.items():
            st.markdown(f"**{t}** ({box.table_rows.get(t, 0):,} rows)")
            st.caption(", ".join(f"{c} ({ty.lower()})" for c, ty in cols.items()))
        for n in box.notes:
            st.warning(n)

# ---------------------------------------------------------------- SQL input: paste or upload
sql = st.text_area("Your SQL", key="sql_text", height=220,
                   placeholder="SELECT region, SUM(amount) AS total FROM sales GROUP BY region")
files = st.file_uploader("Or upload .sql files (scripts or stored procedures, up to 1 MB each)", type="sql",
                         accept_multiple_files=True, key="sql_files")
st.button("Explain this query", type="primary", key="explain")

source_name = None
if files:
    names = [f.name for f in files]
    source_name = st.selectbox("File to analyse", names, key="file_pick") if len(names) > 1 else names[0]
    up = files[names.index(source_name)]
    data = up.getvalue()
    if len(data) > MAX_SQL_FILE_BYTES:
        st.error(f"{up.name} is too big ({len(data) / 1024 / 1024:.1f} MB). The limit is 1 MB per file.")
        st.stop()
    try:
        sql = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        sql = data.decode("latin-1")

if not sql.strip():
    st.info("Paste a query above, upload a .sql file, or load an example from the sidebar.")
    st.stop()

script = analyze_script(sql, dialect, schema)
if not script.ok:
    st.error(script.error.message)
    st.stop()
statements = script.all_statements
if not statements:
    first = script.all_unreadable[0] if script.all_unreadable else None
    err = analyze(first.sql, dialect).error if first else None
    st.error(err.message if err else "No SQL statement was found.")
    if err and err.context:
        st.caption("The problem is between >> and <<:")
        st.code(err.context, language=None)
    st.caption(f"Tip: check that the database chosen in the sidebar ({DIALECTS[dialect]}) is the one this SQL was written for.")
    st.stop()

# ---------------------------------------------------------------- file summary
syntax = script.syntax_problems
if syntax:
    st.error(f"{icon('🧟')}{len(syntax)} syntax problem(s) in this SQL. A database would stop with an error here:")
    for u in syntax:
        where = f" (inside {u.procedure})" if u.procedure else ""
        st.markdown(f"- **Line {u.line}**{where}: `{' '.join(u.sql.split())[:60]}` — {u.message}")

if len(statements) > 1 or script.procedures or script.all_unreadable or syntax:
    st.markdown(f"#### {'File summary' if source_name else 'Summary'}" + (f": {source_name}" if source_name else ""))
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Statements", len(statements))
    s2.metric("Procedures", len(script.procedures))
    s3.metric("Tables read", len(script.tables_read))
    s4.metric("Tables written", len(script.tables_written))
    s5.metric("Problems", sum(script.problem_counts.values()) + len(syntax))
    for p in script.procedures:
        st.caption(f"{icon('⚙️')}{p.kind.lower()} **{p.name}**({p.params}) on line {p.line}: "
                   f"{len(p.statements)} statement(s) analysed, {len(p.not_analysed)} step(s) not analysed.")
    if script.all_unreadable:
        with st.expander(f"Not analysed ({len(script.all_unreadable)})"):
            st.caption("Control-flow steps (IF, DECLARE, SET, RETURN...) and statements we could not read.")
            st.dataframe(pd.DataFrame([{"Line": u.line, "In": u.procedure or "-",
                                        "Text": " ".join(u.sql.split())[:80], "Why": u.message}
                                       for u in script.all_unreadable]), hide_index=True, width="stretch")


def label_for(s) -> str:
    where = f" in {s.procedure}" if s.procedure else ""
    return f"Statement {s.index} (line {s.line}){where}: {' '.join(s.sql.split())[:60]}"


chosen = statements[0]
if len(statements) > 1:
    pick = st.selectbox("Pick a statement to look at", [s.index for s in statements], key="stmt_pick",
                        format_func=lambda i: label_for(statements[i - 1]))
    chosen = statements[pick - 1]
a = chosen.analysis

# Row explosion needs real data, so it is only checked with loaded tables (cached per statement).
if box and a.statement == "SELECT":
    cache = st.session_state.setdefault("explosions", {})
    key = (chosen.sql, dialect)
    if key not in cache:
        cache[key] = row_explosion_issue(chosen.sql, dialect, box.count_rows, box.table_rows)
    if cache[key] is not None:
        a.issues = a.issues + [cache[key]]
high = sum(i.severity == "high" for i in a.issues)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Tables read", len(a.tables))
m2.metric("Joins", len(a.joins))
m3.metric("Problems found", len(a.issues), help="Things that are often bugs or slow. See the Problems tab.")
m4.metric("Result columns", len(a.output_columns))
if high:
    st.error(f"{icon('🧟')}{high} likely bug(s) found. See “Possible problems”.")
elif a.issues:
    st.warning(f"{len(a.issues)} thing(s) worth a look. See “Possible problems”.")
else:
    st.success("No common problems found in this query.")

t_what, t_issues, t_lineage, t_details, t_try = st.tabs(
    [f"{icon('📖')}What it does", f"{icon('🩺')}Possible problems ({len(a.issues)})",
     f"{icon('🧭')}Where data comes from", f"{icon('🔍')}Details", f"{icon('▶️')}Try it on your tables"])

with t_what:
    st.markdown("#### In plain English")
    st.markdown("\n".join(f"{n}. {step}" for n, step in enumerate(a.explanation, 1)))

with t_issues:
    if not a.issues:
        st.success("Nothing to fix. The query avoids the common mistakes we look for.")
    order = {"high": 0, "medium": 1, "low": 2}
    for i in sorted(a.issues, key=lambda i: order[i.severity]):
        if plain:
            with st.container(border=True):
                st.markdown(f"**{SEVERITY_WORDS[i.severity]}: {i.title}**")
                st.write(i.why)
                st.write(f"**How to fix:** {i.fix}")
        else:
            st.markdown(issue_html(i.severity, f"{SEVERITY_WORDS[i.severity]}: {i.title}", i.why, i.fix),
                        unsafe_allow_html=True)
        if i.snippet:
            with st.expander("Show the part of the query"):
                st.code(i.snippet, language="sql")

with t_lineage:
    st.markdown("#### Tables")
    st.write("Which tables feed the result." + (f" The result is written into **{a.target}**." if a.target else ""))
    if a.tables:
        st.graphviz_chart(lineage_dot(a))
    st.markdown("#### Columns")
    st.write("For each column in the result, the original column(s) it is made from.")
    rows = [{"Result column": l.output,
             "Comes from": ", ".join(l.sources) if l.sources else "(a fixed value, no table)",
             "How it is made": l.expression} for l in a.lineage]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if any(s.startswith("?.") for l in a.lineage for s in l.sources):
        st.caption("“?” means the column name has no table prefix and several tables could have it. "
                   "Write it as table.column to make it clear.")

with t_details:
    details = {
        "Statement": a.statement,
        "Tables read": ", ".join(a.tables) or "-",
        "Helper steps (CTEs)": ", ".join(a.ctes) or "-",
        "Joins": "; ".join(f"{j.kind.lower()} join {j.table}" + (f" on {j.condition}" if j.condition else "")
                           for j in a.joins) or "-",
        "Filters": " AND ".join(a.filters) or "-",
        "Grouped by": ", ".join(a.group_by) or "-",
        "Calculations": ", ".join(a.aggregations) or "-",
        "Sorted by": ", ".join(a.order_by) or "-",
        "Row limit": a.limit or "-",
        "Writes into": a.target or "-",
    }
    st.table(pd.DataFrame({"": details}))

with t_try:
    if not box:
        st.info("Load CSV files or the retail sample in the sidebar (“Your tables”) to run this query on real data.")
    else:
        st.write("Runs this statement on your loaded tables. Read-only, first 100 rows, stopped after 5 seconds.")
        if st.button("Preview result (first 100 rows)", key="preview_btn", type="primary"):
            st.session_state.setdefault("previews", {})[(chosen.sql, dialect)] = box.preview(chosen.sql, dialect)
        res = st.session_state.get("previews", {}).get((chosen.sql, dialect))
        if res is not None:
            if res.ok:
                st.dataframe(res.df, hide_index=True, width="stretch")
                st.caption(f"Showing the first {len(res.df)} rows." if res.truncated
                           else f"{len(res.df)} row(s) in total.")
            else:
                st.warning(res.error)