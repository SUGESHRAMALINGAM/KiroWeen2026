"""SQL analyzer: parse, explain in plain English, lint and trace lineage. No LLM, no database.

Built on sqlglot. `analyze(sql, dialect)` never raises for bad SQL; it returns an Analysis whose `error`
holds a readable message with the line and column.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.lineage import lineage as _lineage

DIALECTS = {
    "duckdb": "DuckDB", "postgres": "PostgreSQL", "mysql": "MySQL", "snowflake": "Snowflake",
    "bigquery": "BigQuery", "databricks": "Databricks", "spark": "Spark SQL", "redshift": "Redshift",
    "tsql": "SQL Server", "oracle": "Oracle", "sqlite": "SQLite",
}
MAX_SQL_CHARS = 200_000
_CALL = re.compile(r"^(CALL|EXEC|EXECUTE)\s+([\w.$`\"\[\]]+)\s*(?:\((.*)\))?\s*;?\s*$", re.I | re.S)
logging.getLogger("sqlglot").setLevel(logging.ERROR)  # "falling back to Command" notes are not for users


def _check_insert_columns(tree: exp.Insert, schema: dict[str, dict[str, str]]) -> list["Issue"]:
    tbl = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
    cols = tree.this.expressions if isinstance(tree.this, exp.Schema) else []
    known = {t.lower(): {c.lower() for c in cs} for t, cs in schema.items()}
    if not isinstance(tbl, exp.Table) or tbl.name.lower() not in known:
        return []
    out = []
    for c in cols:
        if c.name.lower() not in known[tbl.name.lower()]:
            opts = list(next(cs for t, cs in schema.items() if t.lower() == tbl.name.lower()))
            out.append(Issue("unknown_column", "high", f"Column '{c.name}' does not exist in {tbl.name}",
                             f"{tbl.name} has no column called '{c.name}'." + _suggest(c.name, opts),
                             "Check the spelling, or pick a column from the table list."))
    return out
_COMPARE = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike, exp.In, exp.Between)


# ---------------------------------------------------------------- result types

@dataclass(frozen=True)
class SqlError:
    message: str
    line: int | None = None
    col: int | None = None
    context: str = ""          # the text around the error, with the bad part marked


@dataclass(frozen=True)
class JoinInfo:
    table: str                 # display name, with alias if there is one
    kind: str                  # INNER / LEFT / RIGHT / FULL / CROSS / MISSING CONDITION
    condition: str | None


@dataclass(frozen=True)
class Issue:
    rule: str                  # select_star / function_on_filter / not_in_subquery / cross_join / distinct_join
    severity: str              # high / medium / low
    title: str                 # short, plain
    why: str                   # why it matters, plain
    fix: str                   # what to do
    snippet: str = ""


@dataclass(frozen=True)
class ColumnLineage:
    output: str                # column name in the result
    sources: tuple[str, ...]   # "table.column" items; empty for constants
    expression: str            # how it is computed


@dataclass
class Analysis:
    sql: str
    dialect: str
    error: SqlError | None = None
    statement: str = ""                  # SELECT / INSERT / CREATE / ...
    target: str | None = None            # table written by INSERT / CREATE ... AS
    tables: list[str] = field(default_factory=list)       # real source tables (no CTE names)
    ctes: list[str] = field(default_factory=list)
    joins: list[JoinInfo] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    aggregations: list[str] = field(default_factory=list)
    having: str | None = None
    order_by: list[str] = field(default_factory=list)
    limit: str | None = None
    distinct: bool = False
    output_columns: list[str] = field(default_factory=list)
    lineage: list[ColumnLineage] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)  # one plain sentence per step
    statements_found: int = 1

    @property
    def ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------- helpers

def _table_name(t: exp.Table) -> str:
    return ".".join(p for p in (t.catalog, t.db, t.name) if p)


def _display(t: exp.Table) -> str:
    name = _table_name(t)
    alias = t.alias
    return f"{name} ({alias})" if alias and alias != t.name else name


def _sql(node: exp.Expr | None, dialect: str) -> str:
    return node.sql(dialect=dialect) if node is not None else ""


def _parse_error(sql: str, e: Exception) -> SqlError:
    if isinstance(e, ParseError) and e.errors:
        info = e.errors[0]
        line, col = info.get("line"), info.get("col")
        desc = str(info.get("description", "")).split(" <Token")[0].split(". Line")[0].strip()
        bad = info.get("highlight") or ""
        if desc.endswith(" but got") and bad:
            desc = f"{desc} “{bad}”"
        elif bad:
            desc = f"{desc} (near “{bad}”)"
        context = f"{info.get('start_context', '')}>>{bad}<<{info.get('end_context', '')}".strip()
        at = f" at line {line}, column {col}" if line else ""
        desc = desc[:1].lower() + desc[1:]
        return SqlError(f"The SQL could not be read{at}: {desc}.", line, col, context)
    return SqlError(f"The SQL could not be read: {str(e).splitlines()[0]}")


def _main_select(tree: exp.Expr) -> exp.Query | None:
    if isinstance(tree, exp.Query):
        return tree
    q = tree.expression if isinstance(tree, (exp.Insert, exp.Create)) else None
    if isinstance(q, exp.Query):
        return q
    return tree.find(exp.Query)


def _outer_selects(q: exp.Query) -> list[exp.Select]:
    """The SELECTs that produce the final rows (both sides of a UNION)."""
    if isinstance(q, exp.Select):
        return [q]
    if isinstance(q, exp.SetOperation):
        return _outer_selects(q.left) + _outer_selects(q.right)
    if isinstance(q, exp.Subquery):
        return _outer_selects(q.this)
    return []


# ---------------------------------------------------------------- lint

def _lint(tree: exp.Expr, dialect: str) -> list[Issue]:
    issues: list[Issue] = []

    for sel in tree.find_all(exp.Select):
        if any(isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
               for e in sel.expressions):
            issues.append(Issue(
                "select_star", "medium", "Reads every column (SELECT *)",
                "It reads columns you may not need, which is slower and costs more on a warehouse. "
                "The query can also break or change silently when someone adds a column to the table.",
                "List only the columns you need.", _sql(sel, dialect)[:120]))
            break

    for where in tree.find_all(exp.Where):
        for cmp in where.find_all(*_COMPARE):
            for side in (cmp.this,):
                if isinstance(side, (exp.Func, exp.Cast)) and not isinstance(side, exp.AggFunc) \
                        and side.find(exp.Column) is not None:
                    issues.append(Issue(
                        "function_on_filter", "medium", "A function wraps a filtered column",
                        "Wrapping the column (for example UPPER(name) or CAST(day AS DATE)) stops the database "
                        "from using indexes or skipping data partitions, so it has to read everything.",
                        "Compare the column directly, and change the value instead, e.g. "
                        "day >= '2026-01-01' AND day < '2026-02-01'.", _sql(cmp, dialect)))

    for not_ in tree.find_all(exp.Not):
        inner = not_.this
        if isinstance(inner, exp.In) and inner.args.get("query") is not None:
            issues.append(Issue(
                "not_in_subquery", "high", "NOT IN with a sub-query",
                "If the sub-query returns even one empty (NULL) value, NOT IN returns no rows at all, "
                "without any error. This is a common silent bug.",
                "Use NOT EXISTS (SELECT 1 FROM ... WHERE ...) or a LEFT JOIN ... WHERE ... IS NULL.",
                _sql(not_, dialect)))

    for j in tree.find_all(exp.Join):
        kind = (j.args.get("kind") or "").upper()
        has_cond = j.args.get("on") is not None or j.args.get("using")
        if kind == "CROSS" or (not has_cond and not j.args.get("method") and kind not in ("SEMI", "ANTI")
                               and not isinstance(j.this, (exp.Unnest, exp.Lateral))):
            issues.append(Issue(
                "cross_join", "high",
                "Cross join: every row is paired with every row" if kind == "CROSS" else "A join has no condition",
                "Each row of one table is matched with every row of the other. 1,000 × 1,000 rows "
                "becomes 1,000,000 rows, which is slow and usually wrong.",
                "Add an ON condition that says how the tables match, e.g. ON a.id = b.a_id. "
                "Keep CROSS JOIN only if you really want every combination.", _sql(j, dialect)))

    for sel in tree.find_all(exp.Select):
        if sel.args.get("distinct") and sel.args.get("joins"):
            issues.append(Issue(
                "distinct_join", "low", "DISTINCT on a query with joins",
                "DISTINCT is often added to hide repeated rows caused by a join that matches more than "
                "one row. The totals can still be wrong, and the real problem stays hidden.",
                "Check that each join matches at most one row (join on the full key). "
                "If you need one row per item, group or filter before joining.", _sql(sel, dialect)[:120]))
    return issues


# ---------------------------------------------------------------- lineage

def _lineage_for(q: exp.Query, sql_tree: exp.Expr, dialect: str, cte_names: set[str],
                 real_tables: list[str], schema: dict | None = None) -> list[ColumnLineage]:
    out: list[ColumnLineage] = []
    selects = _outer_selects(q)
    if not selects:
        return out
    first = selects[0]
    single = real_tables[0] if len(real_tables) == 1 else None
    # alias -> real table (or CTE) name, for the fallback path
    alias_map: dict[str, str] = {}
    for t in sql_tree.find_all(exp.Table):
        alias_map[(t.alias or t.name).lower()] = t.name if t.name.lower() in cte_names else _table_name(t)
    # Filters do not change where a column comes from, and unqualified names inside them are a common
    # reason lineage cannot resolve. Trace on a copy without WHERE / HAVING.
    traced = q.copy()
    for sel in list(traced.find_all(exp.Select)):
        sel.set("where", None)
        sel.set("having", None)
    for e in first.expressions:
        if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star)):
            src = (f"{single}.*",) if single else tuple(f"{t}.*" for t in real_tables)
            out.append(ColumnLineage("*", src, "all columns"))
            continue
        name = e.alias_or_name
        expr_sql = _sql(e.unalias() if isinstance(e, exp.Alias) else e, dialect)
        sources: set[str] = set()
        if e.find(exp.Column) is None:  # constant or COUNT(*): no source column
            out.append(ColumnLineage(name, (), expr_sql))
            continue
        try:
            node = None
            for sch in ([schema, None] if schema else [None]):
                try:
                    node = _lineage(name, traced, sch, dialect=dialect)
                    break
                except (SqlglotError, KeyError, ValueError):
                    if sch is None:
                        raise
            for leaf in node.walk():
                if leaf.downstream:
                    continue
                col = leaf.name.split(".")[-1]
                if isinstance(leaf.source, exp.Table) and leaf.source.name.lower() not in cte_names:
                    sources.add(f"{_table_name(leaf.source)}.{col}")
                elif col and single and not isinstance(leaf.expression, exp.Literal):
                    sources.add(f"{single}.{col}")
                elif col and not isinstance(leaf.expression, exp.Literal):
                    sources.add(f"?.{col}")
        except (SqlglotError, KeyError, ValueError, AttributeError, TypeError, IndexError):
            sources.clear()
            for c in e.find_all(exp.Column):
                tbl = alias_map.get(c.table.lower(), c.table) if c.table else (single or "?")
                sources.add(f"{tbl}.{c.name}")
        out.append(ColumnLineage(name, tuple(sorted(sources)), expr_sql))
    return out


# ---------------------------------------------------------------- explanation

def _join_phrase(j: JoinInfo) -> str:
    return {
        "INNER": "keeps only rows that match in both",
        "LEFT": "keeps every row from the left side, even with no match",
        "RIGHT": "keeps every row from the right side, even with no match",
        "FULL": "keeps every row from both sides",
        "CROSS": "pairs every row with every row",
        "MISSING CONDITION": "has no matching condition, so it pairs every row with every row (check this)",
    }.get(j.kind, "")


def _explain(a: Analysis) -> list[str]:
    s: list[str] = []
    if a.statements_found > 1:
        s.append(f"The text has {a.statements_found} statements. This explains the first one.")
    if a.ctes:
        s.append(f"First it builds {len(a.ctes)} helper step(s) (CTEs): {', '.join(a.ctes)}. "
                 "Think of them as temporary tables used by the main query.")
    if a.tables:
        s.append(f"It reads data from: {', '.join(a.tables)}.")
    else:
        s.append("It does not read any table (it only computes values).")
    for j in a.joins:
        cond = f" where {j.condition}" if j.condition else ""
        if j.kind == "MISSING CONDITION":
            s.append(f"It joins {j.table}, but {_join_phrase(j)}.")
        else:
            article = "An" if j.kind[0] in "AEIOU" else "A"
            s.append(f"It joins {j.table}{cond}. {article} {j.kind.lower()} join {_join_phrase(j)}.")
    if a.filters:
        s.append(f"It keeps only rows where {' and '.join(a.filters)}.")
    if a.group_by:
        s.append(f"It puts rows into groups by {', '.join(a.group_by)}.")
    if a.aggregations:
        what = "for each group" if a.group_by else "over all rows"
        s.append(f"It calculates {', '.join(a.aggregations)} {what}.")
    if a.having:
        s.append(f"It then keeps only groups where {a.having}.")
    if a.distinct:
        s.append("It removes repeated rows (DISTINCT).")
    if a.output_columns:
        cols = a.output_columns if len(a.output_columns) <= 8 else a.output_columns[:8] + ["…"]
        s.append(f"The result has these columns: {', '.join(cols)}.")
    if a.order_by:
        s.append(f"It sorts the result by {', '.join(a.order_by)}.")
    if a.limit:
        s.append(f"It returns at most {a.limit} rows.")
    if a.target:
        verb = "creates the table" if a.statement == "CREATE" else "writes the result into"
        s.append(f"Finally it {verb} {a.target}.")
    return s


# ---------------------------------------------------------------- entry point

def analyze(sql: str, dialect: str = "duckdb", schema: dict[str, dict[str, str]] | None = None) -> Analysis:
    """Analyse one statement. With `schema` ({table: {column: type}}) it also checks names against
    real tables and uses the schema for lineage."""
    a = Analysis(sql=sql, dialect=dialect)
    if dialect not in DIALECTS:
        a.error = SqlError(f"Unknown SQL dialect '{dialect}'. Choose one of: {', '.join(DIALECTS.values())}.")
        return a
    if not sql or not sql.strip():
        a.error = SqlError("Paste a SQL query to analyse.")
        return a
    if len(sql) > MAX_SQL_CHARS:
        a.error = SqlError(f"The SQL is too long ({len(sql):,} characters). The limit is {MAX_SQL_CHARS:,}.")
        return a
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except (SqlglotError, RecursionError) as e:
        a.error = _parse_error(sql, e)
        return a
    if not statements:
        a.error = SqlError("No SQL statement was found.")
        return a
    a.statements_found = len(statements)
    tree = statements[0]
    a.statement = tree.key.upper()
    if isinstance(tree, (exp.Insert, exp.Create)):
        tgt = tree.this.find(exp.Table) if tree.this is not None else None
        a.target = _table_name(tgt) if tgt is not None else None
    call = _CALL.match(sql.strip()) if isinstance(tree, exp.Command) else None
    if call:
        a.statement, a.target = "CALL", call.group(2)
        args = (call.group(3) or "").strip()
        a.explanation = [f"It runs the stored procedure {call.group(2)}"
                         + (f" with these values: {args}." if args else " with no values.")]
        return a
    if isinstance(tree, exp.Insert) and isinstance(tree.expression, exp.Values):
        rows = len(tree.expression.expressions)
        cols = [_sql(c, dialect) for c in tree.this.expressions] if isinstance(tree.this, exp.Schema) else []
        a.tables = []
        a.explanation = [f"It adds {rows} row(s) with fixed values into {a.target}"
                         + (f" (columns: {', '.join(cols)})." if cols else ".")]
        a.issues = _lint(tree, dialect)
        if schema:
            a.issues += _check_insert_columns(tree, schema)
        return a
    if isinstance(tree, (exp.Update, exp.Delete, exp.Merge)):
        _analyze_change(a, tree, dialect)
        if schema:
            a.issues += check_against_schema(tree, dialect, schema)
        return a
    q = _main_select(tree)
    if q is None:
        word = a.statement if not isinstance(tree, exp.Command) else str(tree.this).upper()
        article = "an" if word[:1] in "AEIOU" else "a"
        a.error = SqlError(f"This is {article} {word} statement. SQL Autopsy explains SELECT, INSERT, UPDATE, "
                           "DELETE, MERGE, CREATE ... AS SELECT and CALL.")
        return a

    cte_names = {c.alias.lower() for c in tree.find_all(exp.CTE)}
    a.ctes = [c.alias for c in tree.find_all(exp.CTE)]
    seen: dict[str, None] = {}
    for t in tree.find_all(exp.Table):
        if t.name.lower() in cte_names or not t.name:
            continue
        name = _table_name(t)
        if a.target and name == a.target and t is (tree.this.find(exp.Table) if tree.this is not None else None):
            continue
        seen.setdefault(name, None)
    a.tables = list(seen)

    outer = _outer_selects(q)
    main = outer[0] if outer else None
    if main is not None:
        for j in main.args.get("joins") or []:
            side = (j.args.get("side") or "").upper()
            kind = (j.args.get("kind") or "").upper()
            on, using = j.args.get("on"), j.args.get("using")
            if kind == "CROSS":
                label = "CROSS"
            elif on is None and not using:
                label = "MISSING CONDITION"
            else:
                label = side or "INNER"
            cond = _sql(on, dialect) if on is not None else (
                "same " + ", ".join(_sql(u, dialect) for u in using) if using else None)
            tbl = j.this
            a.joins.append(JoinInfo(_display(tbl) if isinstance(tbl, exp.Table) else _sql(tbl, dialect)[:60],
                                    label, cond))
        where = main.args.get("where")
        if where is not None:
            cond = where.this
            parts = list(cond.flatten()) if isinstance(cond, exp.And) else [cond]
            a.filters = [_sql(p, dialect) for p in parts]
        group = main.args.get("group")
        if group is not None:
            a.group_by = [_sql(g, dialect) for g in group.expressions]
        having = main.args.get("having")
        a.having = _sql(having.this, dialect) if having is not None else None
        a.distinct = bool(main.args.get("distinct"))
        a.aggregations = list(dict.fromkeys(
            _sql(f, dialect) for e in main.expressions for f in e.find_all(exp.AggFunc)
            if f.find_ancestor(exp.AggFunc) is None))
        a.output_columns = [e.alias_or_name if not isinstance(e, exp.Star) else "*" for e in main.expressions]
    order = q.args.get("order")
    if order is not None:
        a.order_by = [_sql(o, dialect) for o in order.expressions]
    limit = q.args.get("limit")
    if limit is not None:
        a.limit = _sql(limit.expression, dialect)

    a.issues = _lint(tree, dialect)
    if schema:
        a.issues += check_against_schema(tree, dialect, schema)
    a.lineage = _lineage_for(q, tree, dialect, cte_names, a.tables, schema)
    a.explanation = _explain(a)
    return a


def _analyze_change(a: Analysis, tree: exp.Expr, dialect: str) -> None:
    """UPDATE / DELETE / MERGE: what is changed, from which tables, and under which condition."""
    tgt = tree.this if isinstance(tree.this, exp.Table) else tree.find(exp.Table)
    a.target = _table_name(tgt) if tgt is not None else None
    cte_names = {c.alias.lower() for c in tree.find_all(exp.CTE)}
    seen: dict[str, None] = {}
    for t in tree.find_all(exp.Table):
        if t is tgt or not t.name or t.name.lower() in cte_names:
            continue
        seen.setdefault(_table_name(t), None)
    a.tables = list(seen)
    where = tree.args.get("where")
    if where is not None:
        cond = where.this
        parts = list(cond.flatten()) if isinstance(cond, exp.And) else [cond]
        a.filters = [_sql(p, dialect) for p in parts]
    a.issues = _lint(tree, dialect)
    target = a.target or "the table"
    s: list[str] = []
    if isinstance(tree, exp.Update):
        s.append(f"It changes rows in {target}.")
        sets = [_sql(e, dialect) for e in tree.expressions]
        if sets:
            s.append(f"It sets {', '.join(sets)}.")
    elif isinstance(tree, exp.Delete):
        s.append(f"It deletes rows from {target}.")
    else:
        using = tree.args.get("using")
        src = _sql(using, dialect)[:60] if using is not None else "another table"
        s.append(f"It merges rows from {src} into {target}: matching rows are changed, new rows can be added.")
    if a.tables:
        s.append(f"It also reads: {', '.join(a.tables)}.")
    if a.filters:
        s.append(f"It only touches rows where {' and '.join(a.filters)}.")
    elif not isinstance(tree, exp.Merge):
        s.append(f"It has no WHERE, so it touches every row in {target}.")
        a.issues.append(Issue(
            "no_where", "high", "Changes every row (no WHERE)",
            f"Without a WHERE condition this {a.statement} runs on every row of {target}. "
            "That is rarely what was meant and cannot be undone easily.",
            "Add a WHERE condition that picks only the rows to change.", _sql(tree, dialect)[:120]))
    a.explanation = s


# ---------------------------------------------------------------- schema check

def _suggest(name: str, options, cutoff: float = 0.5) -> str:
    import difflib
    match = difflib.get_close_matches(name.lower(), [o.lower() for o in options], n=1, cutoff=cutoff)
    if not match:
        return ""
    real = next(o for o in options if o.lower() == match[0])
    return f" Did you mean {real}?"


def check_against_schema(tree_or_sql, dialect: str, schema: dict[str, dict[str, str]]) -> list[Issue]:
    """Unknown tables, unknown columns and ambiguous columns, checked against real table definitions.
    `schema` is {table: {column: type}} from any connector."""
    from sqlglot.optimizer.scope import traverse_scope

    tree = tree_or_sql if isinstance(tree_or_sql, exp.Expr) else sqlglot.parse_one(tree_or_sql, read=dialect)
    tables = {t.lower(): {c.lower(): c for c in cols} for t, cols in schema.items()}
    real_names = {t.lower(): t for t in schema}
    issues: list[Issue] = []
    seen: set[tuple[str, str]] = set()

    def add(rule: str, title: str, why: str, fix: str) -> None:
        if (rule, title) not in seen:
            seen.add((rule, title))
            issues.append(Issue(rule, "high", title, why, fix))

    cte_names = {c.alias.lower() for c in tree.find_all(exp.CTE)}
    target = None
    if isinstance(tree, (exp.Insert, exp.Create)) and tree.this is not None:
        target = tree.this.find(exp.Table)
    for t in tree.find_all(exp.Table):
        if t is target or not isinstance(t.this, exp.Identifier):
            continue
        name = t.name.lower()
        if name in cte_names or name in tables:
            continue
        add("unknown_table", f"Table '{t.name}' does not exist",
            f"'{t.name}' is not one of your loaded tables ({', '.join(schema) or 'none'})."
            + _suggest(t.name, list(schema), 0.7),
            "Check the spelling, or load that table first.")

    if isinstance(tree, (exp.Update, exp.Delete)) and isinstance(tree.this, exp.Table):
        tname = tree.this.name.lower()
        names = {tname, (tree.this.alias or "").lower()}
        if tname in tables and not tree.args.get("from") and not tree.args.get("joins"):
            for col in tree.find_all(exp.Column):
                if col.find_ancestor(exp.Select) is not None or isinstance(col.this, exp.Star):
                    continue
                if (not col.table or col.table.lower() in names) and col.name.lower() not in tables[tname]:
                    where = real_names[tname]
                    add("unknown_column", f"Column '{col.name}' does not exist in {where}",
                        f"{where} has no column called '{col.name}'." + _suggest(col.name, list(tables[tname].values())),
                        "Check the spelling, or pick a column from the table list.")

    try:
        scopes = traverse_scope(tree)
    except Exception:
        return issues

    def source_columns(src) -> set[str] | None:
        """Lower-case column names a source provides; None if unknown (then we do not judge)."""
        if isinstance(src, exp.Table):
            cols = tables.get(src.name.lower())
            return set(cols) if cols is not None else None
        sel = getattr(src, "expression", None)
        if sel is None or not hasattr(sel, "named_selects"):
            return None
        if any(isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
               for e in getattr(sel, "expressions", [])):
            return None
        return {n.lower() for n in sel.named_selects}

    def label(src, alias: str) -> str:
        return real_names.get(src.name.lower(), src.name) if isinstance(src, exp.Table) else alias

    for scope in scopes:
        aliases = {e.alias.lower() for e in getattr(scope.expression, "expressions", []) if isinstance(e, exp.Alias)}
        for col in scope.columns:
            cname = col.name.lower()
            if not cname or isinstance(col.this, exp.Star):
                continue
            chain, sc = [], scope
            while sc is not None:
                chain.append(sc)
                sc = sc.parent
            if col.table:
                owner = None
                for sc in chain:
                    if col.table.lower() in {k.lower() for k in sc.sources}:
                        owner = next(v for k, v in sc.sources.items() if k.lower() == col.table.lower())
                        break
                if owner is None:
                    continue
                cols = source_columns(owner)
                if cols is not None and cname not in cols:
                    where = label(owner, col.table)
                    opts = list(tables.get(owner.name.lower(), {}).values()) if isinstance(owner, exp.Table) \
                        else sorted(cols)
                    add("unknown_column", f"Column '{col.name}' does not exist in {where}",
                        f"{where} has no column called '{col.name}'." + _suggest(col.name, opts),
                        "Check the spelling, or pick a column from the table list.")
                continue
            if cname in aliases and col.find_ancestor(exp.Alias) is None:
                continue
            for sc in chain:
                found, unknown = [], False
                for alias, src in sc.sources.items():
                    cols = source_columns(src)
                    if cols is None:
                        unknown = True
                    elif cname in cols:
                        found.append(label(src, alias))
                if found or unknown:
                    break
            if len(found) > 1:
                add("ambiguous_column", f"Column '{col.name}' is in more than one table",
                    f"'{col.name}' exists in {', '.join(found)}, and the query does not say which one. "
                    "Some databases stop with an error, others pick one silently.",
                    f"Write it with the table in front, e.g. {found[0]}.{col.name}.")
            elif not found and not unknown:
                srcs = list(scope.sources.items())
                opts = sorted({c for _, src in srcs for c in (source_columns(src) or set())})
                if isinstance(scope.expression, exp.Select) and len(srcs) == 1:
                    where = label(srcs[0][1], srcs[0][0])
                    if isinstance(srcs[0][1], exp.Table):
                        opts = list(tables[srcs[0][1].name.lower()].values())
                    title, why = (f"Column '{col.name}' does not exist in {where}",
                                  f"{where} has no column called '{col.name}'.")
                else:
                    title, why = (f"Column '{col.name}' does not exist",
                                  f"None of the tables in this query has a column called '{col.name}'.")
                add("unknown_column", title, why
                    + _suggest(col.name, opts),
                    "Check the spelling, or pick a column from the table list.")
    return issues


def lineage_dot(a: Analysis) -> str:
    """Graphviz DOT for table-level lineage: source tables -> (CTEs) -> result or target."""
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    end = a.target or "query result"
    lines = ["digraph G {", "rankdir=LR;", 'bgcolor="transparent";',
             'node [shape=box, style="rounded,filled", fillcolor="#251b33", color="#ff9f43", '
             'fontcolor="#ece8d8", fontname="Helvetica"];', 'edge [color="#c9a7ff"];']
    for t in a.tables:
        lines.append(f"{q(t)} -> {q(end)};")
    lines.append(f'{q(end)} [fillcolor="#ff9f43", fontcolor="#120d1a"];')
    lines.append("}")
    return "\n".join(lines)
