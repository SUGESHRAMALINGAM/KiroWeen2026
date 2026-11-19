"""Read-only DuckDB sandbox over uploaded CSVs, safe for a public app.

Two layers of protection:
1. Every query is parsed with sqlglot first. Only one read-only SELECT over the loaded tables may run.
2. After the CSVs are loaded, DuckDB is locked: no file or URL access, configuration frozen, memory and
   threads capped. Even a query that slipped past layer 1 cannot read server files.

Previews are capped at 100 rows and 5 seconds. Errors come back as plain messages, never exceptions.
The public surface (`schema()`, `table_rows`, `preview()`, `count_rows()`) is what a future warehouse
connector would also provide.
"""
from __future__ import annotations

import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import sqlglot
from sqlglot import exp

from core.connectors import CsvConnector

ROOT = Path(__file__).resolve().parents[1]
RETAIL = ROOT / "core" / "domain_packs" / "retail"
MAX_CSV_BYTES = 50 * 1024 * 1024
MAX_FILES = 10
PREVIEW_ROWS = 100
TIMEOUT_S = 5.0
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_FORBIDDEN = tuple(getattr(exp, n) for n in (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Command", "Copy", "Attach", "Detach",
    "Pragma", "Set", "Use", "Install", "Into", "Transaction", "Commit", "Rollback", "Describe", "Kill")
    if hasattr(exp, n))
_BANNED_FUNCS = {"glob", "getenv", "query", "query_table", "load_extension", "current_setting", "sniff_csv",
                 "duckdb_secrets", "which_secret", "pg_read_file", "read_file"}


@dataclass(frozen=True)
class PreviewResult:
    df: pd.DataFrame | None = None
    error: str | None = None
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


def _clean(err: Exception) -> str:
    lines = _ANSI.sub("", str(err)).strip().splitlines()
    return (lines[0] if lines else err.__class__.__name__).strip()


def validate_read_only(sql: str, dialect: str, known_tables: set[str]) -> tuple[exp.Expr | None, str | None]:
    """Return (tree, None) for a single read-only query over known tables, else (None, plain reason)."""
    try:
        stmts = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except Exception as e:
        return None, f"The SQL could not be read: {_clean(e)}"
    if len(stmts) != 1:
        return None, "Preview runs one statement at a time."
    tree = stmts[0]
    while isinstance(tree, exp.Subquery):
        tree = tree.this
    if not isinstance(tree, (exp.Select, exp.SetOperation, exp.Values)):
        return None, "Not run: this statement changes data or settings. Preview only runs read-only queries."
    bad = tree.find(*_FORBIDDEN)
    if bad is not None:
        return None, "Not run: this query contains a part that changes data or settings."
    ctes = {c.alias.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            return None, "Not allowed: preview can only read your loaded tables, not files or functions."
        if (t.catalog and t.catalog.lower() != "memory") or (t.db and t.db.lower() != "main"):
            return None, f"Not allowed: '{t.sql()}' is not one of your loaded tables."
        if t.name.lower() not in ctes and t.name.lower() not in known_tables:
            return None, f"Not run: table '{t.name}' is not loaded."
    for f in tree.find_all(exp.Func):
        name = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if name.startswith("read_") or name.endswith("_scan") or name in _BANNED_FUNCS:
            return None, f"Not allowed: the function {name}() is blocked in preview."
    return tree, None


class Sandbox:
    def __init__(self, connector: CsvConnector, table_rows: dict[str, int], notes: list[str]):
        self._con = connector
        self.table_rows = table_rows
        self.notes = notes          # plain messages from loading (rejected rows, skipped files)
        connector.lock()            # previews use their own 5 s timer below

    # ---- construction
    @classmethod
    def from_files(cls, files: list[tuple[str, bytes]]) -> "Sandbox":
        con, rows, notes = CsvConnector(), {}, []
        if len(files) > MAX_FILES:
            notes.append(f"Only the first {MAX_FILES} files were loaded.")
            files = files[:MAX_FILES]
        with tempfile.TemporaryDirectory() as d:
            for name, data in files:
                if len(data) > MAX_CSV_BYTES:
                    notes.append(f"{name} was skipped: it is larger than {MAX_CSV_BYTES // 1024 // 1024} MB.")
                    continue
                safe = re.sub(r"[^\w.-]", "_", Path(name).name) or "data.csv"
                path = Path(d) / safe
                path.write_bytes(data)
                try:
                    res = con.load_csv(path)
                except Exception as e:
                    notes.append(f"{name} could not be read: {_clean(e)}")
                    continue
                rows[res.table] = res.rows
                if res.rejected:
                    notes.append(f"{res.table}: {len(res.rejected)} row(s) could not be read and were skipped.")
        return cls(con, rows, notes)

    @classmethod
    def retail_sample(cls) -> "Sandbox":
        return cls.from_files([(p.name, p.read_bytes()) for p in sorted(RETAIL.glob("*.csv"))])

    # ---- metadata
    def schema(self) -> dict[str, dict[str, str]]:
        return {t: {c.name: c.type for c in self._con.get_schema(t)} for t in self._con.list_tables()}

    # ---- running
    def _run(self, sql: str, timeout_s: float) -> pd.DataFrame:
        cur = self._con.cursor()
        timer = threading.Timer(timeout_s, cur.interrupt)
        timer.start()
        try:
            return cur.execute(sql).fetchdf()
        finally:
            timer.cancel()

    def _guarded(self, sql: str, dialect: str, wrap, timeout_s: float):
        tree, reason = validate_read_only(sql, dialect, {t.lower() for t in self.table_rows})
        if reason:
            return None, reason
        try:
            return self._run(wrap(tree.sql(dialect="duckdb")), timeout_s), None
        except Exception as e:
            msg = _clean(e)
            if "INTERRUPT" in msg.upper():
                return None, f"The query took longer than {timeout_s:g} seconds and was stopped."
            return None, f"The query failed: {msg}"

    def preview(self, sql: str, dialect: str = "duckdb", limit: int = PREVIEW_ROWS,
                timeout_s: float = TIMEOUT_S) -> PreviewResult:
        df, err = self._guarded(sql, dialect, lambda q: f"SELECT * FROM ({q}) AS q LIMIT {int(limit) + 1}",
                                timeout_s)
        if err:
            return PreviewResult(error=err)
        return PreviewResult(df.head(limit), truncated=len(df) > limit)

    def count_rows(self, sql: str, dialect: str = "duckdb", timeout_s: float = TIMEOUT_S) -> int | None:
        df, err = self._guarded(sql, dialect, lambda q: f"SELECT COUNT(*) AS n FROM ({q}) AS q", timeout_s)
        return None if err else int(df["n"][0])


def row_explosion_issue(sql: str, dialect: str, count_rows, table_rows: dict[str, int]):
    """High-severity Issue when the joins produce >10x the rows of the largest input table, else None.
    `count_rows(sql, dialect)` and `table_rows` can come from any connector."""
    from core.sql_analyzer import Issue

    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return None
    if not isinstance(tree, exp.Select) or not tree.args.get("joins"):
        return None
    q = tree.copy()
    q.set("expressions", [exp.Literal.number(1)])
    for k in ("group", "having", "order", "limit", "offset", "distinct", "qualify"):
        q.set(k, None)
    dropped = False
    where = q.args.get("where")
    if where is not None:   # keep simple filters, drop the ones with sub-queries (they only remove rows)
        parts = list(where.this.flatten()) if isinstance(where.this, exp.And) else [where.this]
        keep = [p for p in parts if p.find(exp.Select) is None]
        dropped = len(keep) < len(parts)
        q.set("where", exp.Where(this=exp.and_(*keep)) if keep else None)
    used = [t.name.lower() for t in tree.find_all(exp.Table)]
    sizes = [n for t, n in table_rows.items() if t.lower() in used]
    if not sizes:
        return None
    n = count_rows(q.sql(dialect=dialect), dialect)
    biggest = max(sizes)
    if n is None or biggest == 0 or n <= 10 * biggest:
        return None
    note = " (before filters with sub-queries)" if dropped else ""
    return Issue(
        "row_explosion", "high", "The joins multiply your rows",
        f"On your loaded data the joins produce {n:,} rows{note}, but the biggest table has only "
        f"{biggest:,}. Each row is matched many times, so sums and counts come out too big.",
        "Join on the full key so each row matches once, and remove joins without a condition.")
