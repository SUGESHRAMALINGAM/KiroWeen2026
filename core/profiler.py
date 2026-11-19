"""SQL-pushdown profiler. Only calls Connector.query() with aggregate SQL."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from core.connectors import Connector, quote_ident

_NUMERIC = re.compile(r"^(U?(TINY|SMALL|BIG|HUGE)?INT(EGER)?|FLOAT|DOUBLE|REAL|DECIMAL.*|NUMERIC.*)$", re.I)
_TEXT = re.compile(r"^(VARCHAR|TEXT|STRING|CHAR.*)", re.I)

# Date-like formats counted on text columns. Order is the display order.
DATE_PATTERNS: dict[str, str] = {
    "YYYY-MM-DD": r"^\d{4}-\d{2}-\d{2}$",
    "YYYY/MM/DD": r"^\d{4}/\d{2}/\d{2}$",
    "DD/MM/YYYY": r"^\d{1,2}/\d{1,2}/\d{4}$",
    "DD-MM-YYYY": r"^\d{1,2}-\d{1,2}-\d{4}$",
}


@dataclass
class ColumnProfile:
    name: str
    type: str
    row_count: int
    null_count: int
    distinct_count: int
    min: Any = None
    max: Any = None
    mean: float | None = None
    stddev: float | None = None
    q1: float | None = None
    q3: float | None = None
    outlier_count: int = 0
    lower_fence: float | None = None
    upper_fence: float | None = None
    # Text columns only
    numeric_text_count: int = 0       # non-null values that cast to a number
    non_numeric_text_count: int = 0   # non-null values that do not
    date_format_counts: dict[str, int] = field(default_factory=dict)

    @property
    def is_numeric(self) -> bool:
        return bool(_NUMERIC.match(self.type))

    @property
    def is_text(self) -> bool:
        return bool(_TEXT.match(self.type))

    @property
    def numbers_stored_as_text(self) -> bool:
        """Text column where every non-null value is numeric."""
        return self.is_text and self.numeric_text_count > 0 and self.non_numeric_text_count == 0

    @property
    def mixed_numeric_text(self) -> bool:
        return self.is_text and self.numeric_text_count > 0 and self.non_numeric_text_count > 0

    @property
    def mixed_date_formats(self) -> bool:
        return sum(1 for c in self.date_format_counts.values() if c > 0) > 1


@dataclass
class TableProfile:
    table: str
    row_count: int
    columns: list[ColumnProfile]
    duplicate_rows: int                     # extra copies of fully identical rows
    key_columns: list[str] = field(default_factory=list)
    duplicate_keys: int | None = None       # extra rows sharing a key (non-null keys only)
    candidate_keys: list[str] = field(default_factory=list)

    def column(self, name: str) -> ColumnProfile:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)


def _py(v: Any) -> Any:
    """Convert pandas/numpy scalars to plain Python; missing values to None."""
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v.item() if hasattr(v, "item") else v


def _one(con: Connector, sql: str) -> dict[str, Any]:
    df = con.query(sql)
    return {k: _py(v) for k, v in df.iloc[0].items()}


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def profile_column(con: Connector, table: str, name: str, type_: str) -> ColumnProfile:
    t, c = quote_ident(table), quote_ident(name)
    cp = ColumnProfile(name, type_, 0, 0, 0)
    base = _one(
        con,
        f"SELECT COUNT(*) AS n, COUNT(*) - COUNT({c}) AS nulls, COUNT(DISTINCT {c}) AS d, "
        f"MIN({c}) AS mn, MAX({c}) AS mx FROM {t}",
    )
    cp.row_count, cp.null_count, cp.distinct_count = int(base["n"]), int(base["nulls"]), int(base["d"])
    cp.min, cp.max = base["mn"], base["mx"]

    if cp.is_numeric and cp.row_count > cp.null_count:
        st = _one(
            con,
            f"SELECT AVG({c}) AS mean, STDDEV_SAMP({c}) AS sd, "
            f"QUANTILE_CONT({c}, 0.25) AS q1, QUANTILE_CONT({c}, 0.75) AS q3 FROM {t}",
        )
        cp.mean, cp.stddev, cp.q1, cp.q3 = st["mean"], st["sd"], st["q1"], st["q3"]
        iqr = cp.q3 - cp.q1
        cp.lower_fence, cp.upper_fence = cp.q1 - 1.5 * iqr, cp.q3 + 1.5 * iqr
        cp.outlier_count = int(
            _one(
                con,
                f"SELECT COUNT(*) AS k FROM {t} WHERE {c} < {cp.lower_fence!r} OR {c} > {cp.upper_fence!r}",
            )["k"]
        )
    elif cp.is_text and cp.row_count > cp.null_count:
        date_cols = ", ".join(
            f"COUNT(*) FILTER (WHERE REGEXP_MATCHES({c}, {_lit(p)})) AS d{i}"
            for i, p in enumerate(DATE_PATTERNS.values())
        )
        r = _one(
            con,
            f"SELECT COUNT(TRY_CAST({c} AS DOUBLE)) AS num, COUNT({c}) AS nn, {date_cols} FROM {t}",
        )
        cp.numeric_text_count = int(r["num"])
        cp.non_numeric_text_count = int(r["nn"]) - int(r["num"])
        cp.date_format_counts = {fmt: int(r[f"d{i}"]) for i, fmt in enumerate(DATE_PATTERNS)}
    return cp


def profile_table(con: Connector, table: str, key_columns: list[str] | None = None) -> TableProfile:
    schema = con.get_schema(table)
    t = quote_ident(table)
    cols = [profile_column(con, table, col.name, col.type) for col in schema]
    n = int(_one(con, f"SELECT COUNT(*) AS n FROM {t}")["n"])
    all_cols = ", ".join(quote_ident(c.name) for c in schema)
    distinct_rows = int(_one(con, f"SELECT COUNT(*) AS k FROM (SELECT DISTINCT {all_cols} FROM {t}) AS d")["k"])
    prof = TableProfile(table, n, cols, n - distinct_rows)

    if key_columns:
        known = {c.name for c in schema}
        missing = [k for k in key_columns if k not in known]
        if missing:
            raise KeyError(f"Unknown key column(s): {missing}")
        keys = ", ".join(quote_ident(k) for k in key_columns)
        not_null = " AND ".join(f"{quote_ident(k)} IS NOT NULL" for k in key_columns)
        prof.key_columns = list(key_columns)
        prof.duplicate_keys = int(
            _one(
                con,
                f"SELECT COALESCE(SUM(c - 1), 0) AS k FROM (SELECT COUNT(*) AS c FROM {t} "
                f"WHERE {not_null} GROUP BY {keys} HAVING COUNT(*) > 1) AS g",
            )["k"]
        )
    prof.candidate_keys = [
        c.name for c in cols if n > 0 and c.null_count == 0 and c.distinct_count == n
    ]
    return prof
