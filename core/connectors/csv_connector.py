"""CSV connector backed by an in-memory DuckDB database."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

from .base import Column, Connector, quote_ident


@dataclass(frozen=True)
class RejectedRow:
    line: int | None
    column: str | None
    error_type: str
    message: str
    raw: str


@dataclass(frozen=True)
class LoadResult:
    table: str
    rows: int
    rejected: list[RejectedRow]
    text_columns: list[str] = field(default_factory=list)  # columns reloaded as text after type conflicts


def _table_name(path: Path, name: str | None) -> str:
    raw = name or path.stem
    clean = re.sub(r"\W+", "_", raw).strip("_").lower() or "table"
    return "t_" + clean if clean[0].isdigit() else clean


class CsvConnector(Connector):
    def __init__(self) -> None:
        self._con = duckdb.connect(":memory:")
        self._rejects: dict[str, list[RejectedRow]] = {}

    def _exec(self, sql: str, params: list | None = None):
        """Run SQL on a fresh cursor. Streamlit calls us from several threads, and one DuckDB
        connection refuses concurrent calls; cursors share the data but not the busy state."""
        cur = self._con.cursor()
        return cur.execute(sql, params) if params is not None else cur.execute(sql)

    def cursor(self):
        """A fresh DuckDB cursor on the same database (used by core.sandbox)."""
        return self._con.cursor()

    def load_csv(self, path: str | Path, name: str | None = None) -> LoadResult:
        """Load a CSV as a table. Rows DuckDB cannot parse are reported, not dropped silently."""
        path = Path(path)
        table = _table_name(path, name)
        rej = f"_rejects_{table}"
        text_columns: list[str] = []
        cur = self._con.cursor()  # reject tables are private to the cursor that read the file
        for _ in range(3):
            self._create(cur, table, rej, path, text_columns)
            rejected = self._read_rejects(cur, rej)
            # A value that breaks the sniffed type is a data problem to report (Mutant),
            # not a row to drop. Reload those columns as text and let the profiler judge them.
            bad = sorted({r.column for r in rejected if r.error_type == "CAST" and r.column} - set(text_columns))
            if not bad:
                break
            text_columns += bad
        rows = self._exec(f"SELECT COUNT(*) FROM {quote_ident(table)}").fetchone()[0]
        self._rejects[table] = rejected

        return LoadResult(table, rows, rejected, text_columns)

    def _create(self, cur, table: str, rej: str, path: Path, text_columns: list[str]) -> None:
        cur.execute(f"DROP TABLE IF EXISTS {quote_ident(table)}")
        cur.execute(f"DROP TABLE IF EXISTS {quote_ident(rej)}")
        cur.execute("DROP TABLE IF EXISTS reject_scans")
        types = ""
        if text_columns:
            types = ", types={" + ", ".join(f"{_lit(c)}: 'VARCHAR'" for c in text_columns) + "}"
        cur.execute(
            f"CREATE TABLE {quote_ident(table)} AS SELECT * FROM read_csv(?, header=true, "
            f"store_rejects=true, rejects_table={_lit(rej)}, rejects_limit=0{types})",
            [str(path)],
        )

    def _read_rejects(self, cur, rej: str) -> list[RejectedRow]:
        exists = cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [rej]
        ).fetchone()[0]
        if not exists:
            return []
        df = cur.execute(
            f"SELECT line, column_name, error_type, error_message, csv_line FROM {quote_ident(rej)} "
            "ORDER BY line"
        ).fetchdf()
        return [
            RejectedRow(
                None if pd.isna(r.line) else int(r.line),
                None if pd.isna(r.column_name) else str(r.column_name),
                str(r.error_type),
                str(r.error_message),
                str(r.csv_line),
            )
            for r in df.itertuples()
        ]

    def rejected_rows(self, table: str) -> list[RejectedRow]:
        return list(self._rejects.get(table, []))

    def list_tables(self) -> list[str]:
        rows = self._exec(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name NOT LIKE '\\_rejects\\_%' ESCAPE '\\' "
            "AND table_name <> 'reject_scans' ORDER BY table_name"
        ).fetchall()
        return [r[0] for r in rows]

    def get_schema(self, table: str) -> list[Column]:
        rows = self._exec(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
        if not rows:
            raise KeyError(f"Unknown table: {table}")
        return [Column(n, t, nl == "YES") for n, t, nl in rows]

    def sample(self, table: str, n: int) -> pd.DataFrame:
        self.get_schema(table)
        return self._exec(f"SELECT * FROM {quote_ident(table)} LIMIT {int(n)}").fetchdf()

    def query(self, sql: str) -> pd.DataFrame:
        return self._exec(sql).fetchdf()


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


