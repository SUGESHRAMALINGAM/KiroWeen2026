import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from core.connectors import CsvConnector, QueryTimeout
from core.profiler import profile_table
from core.rules import ZOMBIE_LABELS, Link, Zombie, find_infections
from core.connectors import quote_ident
from core.custom_checks import (KIND_LABELS as CUSTOM_LABELS, PRESET_PATTERNS, CustomCheckError, CustomSpec,
                                build_custom_check, failing_examples, looks_like_plain_text, parse_values)
from core.testgen import generate_checks, run_checks, suggest_links, to_json, to_pytest, to_sql
from ui_kit import (ZOMBIES, finding_html, hero_html, inject_theme, overlay_html, steps_html,
                    verdict_html, verdict_state, verdict_text, zombie_cards_html)

PACKS = ROOT / "core" / "domain_packs"
SAMPLES = {
    "Retail (mostly healthy)": [PACKS / "retail" / n for n in ("customers.csv", "orders.csv", "products.csv")],
    "Retail outbreak (demo)": [PACKS / "retail" / "customers.csv", PACKS / "retail_outbreak" / "orders.csv",
                               PACKS / "retail" / "products.csv"],
}
SAMPLE_LINKS = [
    Link("orders", "customer_id", "customers", "customer_id"),
    Link("orders", "product_id", "products", "product_id"),
]
QUERY_TIMEOUT_S = 10
KIND_LABELS = {"not_null": "No blanks", "unique": "No repeats", "accepted_values": "Allowed values",
               "range": "Normal range", "referential_integrity": "Links exist", "custom": "Your check"}

st.set_page_config(page_title="Zombie Hunter", page_icon="🧟", layout="wide")


def pick_sample(name: str) -> None:
    st.session_state["sample"] = name
    st.session_state["sample_pick"] = name


def load_connector(sources: list[tuple[str, bytes | Path]]) -> tuple[CsvConnector, list]:
    """Load CSVs into a fresh DuckDB. Bytes go through a temp file that is deleted right after."""
    con, results = CsvConnector(), []
    with tempfile.TemporaryDirectory() as d:
        for name, src in sources:
            path = Path(d) / name
            path.write_bytes(src if isinstance(src, bytes) else Path(src).read_bytes())
            results.append(con.load_csv(path))
    con.lock(timeout_s=QUERY_TIMEOUT_S)  # public app: no file access after loading, slow queries stopped
    return con, results


# ---------------------------------------------------------------- sidebar: mode, alert level, data
with st.sidebar:
    st.markdown("### Settings")
    plain = st.toggle("Plain Mode", key="plain_mode",
                      help="Turns off the zombie theme, pictures and alert so you see the same findings as simple tables.")
    threshold = st.slider("Alert level (% of rows infected)", 1, 100, 10, key="threshold",
                          help="The full-screen alert appears when the infection score reaches this level.")
    st.markdown("### Your data")
    uploads = st.file_uploader("Upload CSV file(s)", type="csv", accept_multiple_files=True,
                               help="You can add several files, for example orders and customers.")
    sample_name = st.selectbox("Or try a sample", list(SAMPLES), key="sample_pick")
    st.button("Load sample", key="load_sample", on_click=lambda: pick_sample(st.session_state["sample_pick"]))

inject_theme(plain)


def icon(e: str) -> str:
    return "" if plain else e + " "


# ---------------------------------------------------------------- header
if plain:
    st.title("Zombie Hunter")
    st.caption("Find data problems in your CSV files and get checks to prevent them.")
else:
    st.markdown(hero_html(), unsafe_allow_html=True)

# ---------------------------------------------------------------- choose data source
if uploads:
    signature = tuple((u.name, u.size) for u in uploads)
    sources = [(u.name, u.getvalue()) for u in uploads]
    links: list[Link] = []
elif st.session_state.get("sample"):
    signature = ("sample", st.session_state["sample"])
    sources = [(p.name, p) for p in SAMPLES[st.session_state["sample"]]]
    links = SAMPLE_LINKS
else:
    if not plain:
        st.markdown(steps_html(), unsafe_allow_html=True)
    st.subheader("Start here")
    st.write("Upload a CSV file in the sidebar, or try one of the samples. "
             "The outbreak demo has a lot of problems, so you can see the full alert.")
    c1, c2 = st.columns(2)
    for col, name in zip((c1, c2), SAMPLES):
        col.button(f"Try: {name}", key=f"cta_{name}", width="stretch", on_click=pick_sample, args=(name,))
    st.stop()

if st.session_state.get("signature") != signature:
    con, results = load_connector(sources)
    for k in ("table_select", "key_cols", "lk_cc", "lk_pt", "lk_pc"):
        st.session_state.pop(k, None)
    st.session_state.update(
        signature=signature, con=con, results=results, links=list(links), check_results=None,
        suggestions=suggest_links(con, con.list_tables()) if len(results) > 1 else [],
        overlay_seen=set(), custom_specs={})
    for k in [k for k in st.session_state if str(k).startswith(("skip_", "cc_"))]:
        st.session_state.pop(k, None)
con: CsvConnector = st.session_state["con"]
results = st.session_state["results"]

for r in results:
    if r.rejected:
        with st.expander(f"{icon('⚠️')}{r.table}: {len(r.rejected)} row(s) could not be read"):
            st.dataframe(pd.DataFrame([x.__dict__ for x in r.rejected]), hide_index=True)
    if r.text_columns:
        st.caption(f"{r.table}: kept as text because some values did not fit the detected type: "
                   f"{', '.join(r.text_columns)}")

# ---------------------------------------------------------------- sidebar: table, keys, links
tables = con.list_tables()
biggest = max(range(len(tables)), key=lambda i: next(r.rows for r in results if r.table == tables[i]))
table = st.sidebar.selectbox("Table to inspect", tables, index=biggest, key="table_select")
key_cols = st.sidebar.multiselect("Key columns (optional)", [c.name for c in con.get_schema(table)],
                                  key="key_cols", help="Columns that should identify a row. Used to find repeated keys.")

with st.sidebar.expander("Links between tables"):
    st.caption("A link says a column points to a record in another table. It is used to find broken links.")
    if len(tables) > 1:
        child_col = st.selectbox("Column in this table", [c.name for c in con.get_schema(table)], key="lk_cc")
        parent = st.selectbox("Points to table", [t for t in tables if t != table], key="lk_pt")
        parent_col = st.selectbox("Points to column", [c.name for c in con.get_schema(parent)], key="lk_pc")
        if st.button("Add link"):
            lk = Link(table, child_col, parent, parent_col)
            if lk not in st.session_state["links"]:
                st.session_state["links"].append(lk)
    for lk in st.session_state["links"]:
        st.write(f"`{lk.child}.{lk.child_column}` → `{lk.parent}.{lk.parent_column}`")
    pending = [s for s in st.session_state["suggestions"] if s.link not in st.session_state["links"]]
    if pending:
        st.caption("Suggested links (guessed from matching names and values, please confirm):")
    for i, s in enumerate(pending):
        lk = s.link
        st.write(f"`{lk.child}.{lk.child_column}` → `{lk.parent}.{lk.parent_column}` "
                 f"(suggested, {s.match_ratio:.0%} of values match)")
        if st.button("Use this link", key=f"use_{i}_{lk.child}_{lk.child_column}"):
            st.session_state["links"].append(lk)
            st.rerun()

# ---------------------------------------------------------------- analysis
try:
    profile = profile_table(con, table, key_cols or None)
    report = find_infections(con, table, key_cols or None, st.session_state["links"], profile)
except QueryTimeout:
    st.error(f"This table is too big to check here: it took longer than {QUERY_TIMEOUT_S} seconds. "
             "Try a smaller file, or run the app on your own computer.")
    st.stop()
counts = report.counts_by_zombie()
by_key = {z.value: n for z, n in counts.items()}
attack = report.percent >= threshold and report.infected_rows > 0

# Full-screen alert: once per table/settings, never in Plain Mode.
event = (table, tuple(key_cols), len(st.session_state["links"]), threshold)
if attack and not plain and event not in st.session_state["overlay_seen"]:
    st.session_state["overlay_seen"].add(event)
    st.markdown(overlay_html(table, report.percent, report.infected_rows, report.total_rows, by_key,
                             uid=f"zh-ov-{len(st.session_state['overlay_seen'])}"),
                unsafe_allow_html=True)

st.subheader(f"Results for “{table}”")
if plain:
    headline, detail = verdict_text(report.percent, threshold, report.infected_rows, report.total_rows)
    st.write(f"**{headline}** {detail}")
else:
    st.markdown(verdict_html(report.percent, threshold, report.infected_rows, report.total_rows),
                unsafe_allow_html=True)
if attack:
    st.warning(f"Infection score {report.percent:g}% is at or above your alert level of {threshold}%.")

m1, m2, m3 = st.columns(3)
m1.metric("Infection score", f"{report.percent}%",
          help="Rows with at least one problem, divided by all rows.")
m2.metric("Infected rows", report.infected_rows)
m3.metric("Total rows", report.total_rows)

if plain:
    for col, z in zip(st.columns(len(Zombie)), Zombie):
        col.metric(ZOMBIE_LABELS[z], counts[z], help=ZOMBIES[z.value].meaning)
else:
    st.markdown(zombie_cards_html(by_key), unsafe_allow_html=True)

tab_find, tab_checks, tab_cols, tab_guide = st.tabs(
    [f"{icon('🔎')}What we found", f"{icon('✅')}Checks", f"{icon('📊')}Column details", f"{icon('📖')}Guide"])

# ---- what we found
with tab_find:
    if not report.findings:
        st.success("No issues found.")
    elif plain:
        st.dataframe(pd.DataFrame([{"Type": f.label, "Column": f.column or "(row)", "Severity": f.severity,
                                    "Rows": f.count, "What it means": f.plain} for f in report.findings]),
                     hide_index=True, width="stretch")
    else:
        order = {"high": 0, "medium": 1, "low": 2}
        for f in sorted(report.findings, key=lambda f: (order[f.severity], -f.count)):
            st.markdown(finding_html(f.zombie.value, f.severity, f.plain, f.column or "whole row"),
                        unsafe_allow_html=True)
    if report.findings:
        st.caption("Example rows for each finding:")
    for f in report.findings:
        with st.expander(f"{f.label}: {f.column or 'whole row'} ({f.count})"):
            st.write(f.plain)
            st.dataframe(pd.DataFrame(f.examples), hide_index=True)

# ---- checks
with tab_checks:
    auto_checks = generate_checks(con, profile, st.session_state["links"])
    specs: list[CustomSpec] = st.session_state["custom_specs"].setdefault(table, [])
    total = report.total_rows
    st.markdown("#### Rules for your data")
    st.write("A **rule** is something that should always be true, like “every order has a customer”. "
             "We made some rules for you. You can add your own and turn off any you don't need.")

    colnames = [c.name for c in con.get_schema(table)]
    coltypes = {c.name: c.type for c in con.get_schema(table)}

    def sample_values(col: str) -> str:
        df = con.query(f"SELECT DISTINCT CAST({quote_ident(col)} AS VARCHAR) AS v FROM {quote_ident(table)} "
                       f"WHERE {quote_ident(col)} IS NOT NULL LIMIT 5")
        vals = [str(v) for v in df["v"].tolist()]
        return ", ".join(vals) if vals else "(the column is empty)"

    def explain(res) -> tuple[str, str]:
        """(kind, sentence) where kind is ok / bad / all / error, in plain words."""
        if res.error:
            return "error", "This rule could not be checked."
        if res.passed:
            return "ok", f"Good news: all {total:,} rows follow this rule."
        n = res.violations
        if total and n >= total:
            return "all", (f"None of the {total:,} rows follow this rule. "
                           "That usually means the rule itself is set up wrong, not the data.")
        share = f" ({n / total:.0%})" if total else ""
        return "bad", f"{n:,} of {total:,} rows{share} break this rule. Examples are below."

    def rule_card(check, res, key: str | None = None, spec: CustomSpec | None = None) -> bool:
        """Show one rule with a clear answer. Returns True if Remove was pressed."""
        state, sentence = explain(res)
        icon_txt = {"ok": "✅", "bad": "❌", "all": "⚠️", "error": "⚠️"}[state]
        with st.container(border=True):
            left, right = st.columns([8, 1])
            left.markdown(f"**{icon(icon_txt)}{check.description.removeprefix('Custom: ').capitalize()}**")
            left.write(sentence)
            if state == "all" and spec is not None and spec.kind == "pattern" and spec.pattern \
                    and looks_like_plain_text(spec.pattern):
                left.info(f"“{spec.pattern}” has no format symbols, so it only accepts values that are exactly "
                          f"“{spec.pattern}”. To check what values look like, pick a ready-made format instead.")
            if state in ("bad", "all"):
                ex = failing_examples(con, check)
                if ex is not None and len(ex):
                    with left.expander("Show examples"):
                        st.dataframe(ex, hide_index=True)
            removed = bool(key) and right.button("Remove", key=key)
        return removed

    # ---- add a rule
    with st.expander("➕ Add your own rule", expanded=not specs):
        kind = st.selectbox("What should be true?", list(CUSTOM_LABELS), format_func=CUSTOM_LABELS.get,
                            key="cc_kind")
        cols: tuple[str, ...] = ()
        vals, lo, hi, pat, cond, norm = (), None, None, None, None, False
        if kind in ("not_null", "unique"):
            cols = tuple(st.multiselect(
                "Which column(s)?", colnames, key=f"cc_cols_{table}",
                help="Pick more than one to check them together, e.g. region + product."))
            if kind == "unique":
                norm = st.checkbox("Treat “Paris”, “paris” and “ Paris ” as the same", key="cc_norm")
        elif kind != "condition":
            col = st.selectbox("Which column?", colnames, key=f"cc_col_{table}")
            cols = (col,)
            st.caption(f"Some values in {col}: {sample_values(col)}")
        if kind == "accepted_values":
            vals = parse_values(st.text_area("Allowed values (one per line)", key=f"cc_values_{table}",
                                             placeholder="NEW\nSHIPPED\nDONE"))
        elif kind == "range":
            c1, c2 = st.columns(2)
            lo = c1.text_input("Smallest allowed", key=f"cc_min_{table}", placeholder="0",
                               help="Leave empty for no lower limit. Dates: 2026-01-31.")
            hi = c2.text_input("Largest allowed", key=f"cc_max_{table}", placeholder="100",
                               help="Leave empty for no upper limit. Dates: 2026-01-31.")
        elif kind == "pattern":
            shapes = list(PRESET_PATTERNS) + ["Something else (advanced)"]
            shape = st.radio("Values should look like", shapes, key=f"cc_shape_{table}")
            if shape in PRESET_PATTERNS:
                pat, example = PRESET_PATTERNS[shape]
                if "{N}" in pat:
                    n = st.number_input("How many digits?", 1, 30, 6, key=f"cc_len_{table}")
                    pat, example = pat.replace("N", str(int(n))), "1" * int(n)
                st.caption(f"Example that fits: {example}")
            else:
                pat = st.text_input("Pattern (regular expression)", key=f"cc_pattern_{table}",
                                    placeholder="[A-Z]{2}-[0-9]{4}")
                st.caption("[A-Z] = one capital letter, [0-9] = one digit, {4} = four times. "
                           "So [A-Z]{2}-[0-9]{4} accepts AB-1234.")
        elif kind == "condition":
            cond = st.text_area("Describe the BAD rows in SQL", key=f"cc_condition_{table}",
                                placeholder="quantity <= 0 OR status IS NULL")
            others = ", ".join(t for t in con.list_tables() if t != table)
            st.caption(f"This table is called `t`. You can also read: {others or 'no other tables'}.")
        if st.button("Add rule", key="cc_add", type="primary"):
            spec = CustomSpec(kind, cols, vals, lo, hi, pat, cond, norm)
            try:
                build_custom_check(con, table, spec)
            except CustomCheckError as e:
                st.error(str(e))
            else:
                if spec not in specs:
                    specs.append(spec)
                st.session_state["check_results"] = None
                st.session_state["cc_added"] = True
                st.rerun()

    # ---- your rules, each with a clear answer
    if st.session_state.pop("cc_added", False):
        st.success("Rule added. The answer is shown below.")
    custom_checks = []
    for i, spec in enumerate(specs):
        try:
            custom_checks.append((i, spec, build_custom_check(con, table, spec)))
        except CustomCheckError as e:
            st.warning(f"One of your rules no longer works: {e}")
    if custom_checks:
        st.markdown("#### Your rules")
        for i, spec, ch in custom_checks:
            if rule_card(ch, run_checks(con, [ch])[0], key=f"cc_rm_{i}", spec=spec):
                specs.pop(i)
                st.session_state["check_results"] = None
                st.rerun()

    # ---- automatic rules
    st.markdown("#### Rules we made for you")
    desc_by_id = {c.id: f"{c.description} ({c.column})" for c in auto_checks}
    with st.expander("Turn off rules you don't need"):
        skipped = st.multiselect("Rules to turn off", list(desc_by_id), key=f"skip_{table}",
                                 format_func=desc_by_id.get,
                                 help="For example the “normal range” rule on an ID column.")
    checks = [c for c in auto_checks if c.id not in skipped] + [c for _, _, c in custom_checks]
    run, d1, d2, d3 = st.columns([2, 1, 1, 1])
    if run.button("Run checks now", type="primary"):
        st.session_state["check_results"] = (table, run_checks(con, checks))
    saved = st.session_state.get("check_results")
    if saved and saved[0] == table:
        results_ = saved[1]
        failed = sum(not r.passed for r in results_)
        (st.error if failed else st.success)(
            f"{failed} of {len(results_)} checks failed" if failed else f"All {len(results_)} checks passed")
        for r in sorted(results_, key=lambda r: r.passed):
            if r.check.kind.value != "custom":
                rule_card(r.check, r)
    else:
        st.dataframe(pd.DataFrame([{"Rule": c.description, "Kind": KIND_LABELS[c.kind.value]}
                                   for c in checks]), hide_index=True, width="stretch")
        st.caption("Press “Run checks now” to see which rules your data breaks.")

    with st.expander("Download these rules (for engineers)"):
        st.write("Use these files to run the same rules every day in your own pipeline.")
        e1, e2, e3 = st.columns(3)
        e1.download_button("SQL file", to_sql(checks), f"{table}_checks.sql", "text/plain")
        e2.download_button("Python test file", to_pytest(checks), f"test_{table}_checks.py", "text/x-python")
        e3.download_button("JSON file", to_json(checks), f"{table}_checks.json", "application/json")
        st.caption("“Normal range” rules use the same limits as Giant zombies. Allowed values are learned from this data.")
# ---- column details
with tab_cols:
    col_name = st.selectbox("Column", [c.name for c in profile.columns])
    cp = profile.column(col_name)
    st.table(pd.DataFrame({"value": {
        "type": cp.type, "rows": cp.row_count, "blank": cp.null_count, "distinct values": cp.distinct_count,
        "smallest": cp.min, "largest": cp.max, "average": cp.mean, "spread (stddev)": cp.stddev,
        "extreme values": cp.outlier_count}}).astype(str))
    st.caption(f"Columns that could identify a row: {', '.join(profile.candidate_keys) or 'none'}")

# ---- guide
with tab_guide:
    st.markdown("#### What the zombies mean")
    for z in ZOMBIES.values():
        st.markdown(f"**{z.name} ({z.short}).** {z.meaning}")
    st.markdown("#### How the score works")
    st.write("The infection score is the share of rows with at least one problem. A row with three problems "
             "still counts once. Severity is high when a problem touches 5% or more of the rows, "
             "medium from 1%, otherwise low.")
    st.markdown("#### Your data")
    st.write("Files are read in this session only. Nothing is stored or sent anywhere.")
