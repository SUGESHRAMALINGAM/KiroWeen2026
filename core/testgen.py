"""Generate data checks from a profile; export as SQL, pytest or JSON; run them in DuckDB.

Every check is a query returning the number of violations. A check passes when it returns 0.
Checks mirror the infection definitions, so a check fails when the matching zombies exist.
"""
from __future__ import annotations

import json
import pprint
import re
from dataclasses import asdict, dataclass
from enum import Enum

from core.connectors import Connector, quote_ident
from core.profiler import TableProfile, _lit
from core.rules import Link

MAX_ACCEPTED_VALUES = 10
ID_NAME = re.compile(r"(^id$|_id$|_key$)", re.I)


class CheckKind(str, Enum):
    NOT_NULL = "not_null"
    UNIQUE = "unique"
    ACCEPTED_VALUES = "accepted_values"
    RANGE = "range"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    CUSTOM = "custom"  # user-defined, see core/custom_checks.py


@dataclass(frozen=True)
class Check:
    id: str
    kind: CheckKind
    table: str
    column: str
    description: str
    sql: str  # returns one row, one column: the violation count. Expect 0.
    tables: tuple[str, ...] = ()  # extra tables the SQL reads (custom rules may look at other tables)


@dataclass(frozen=True)
class CheckResult:
    check: Check
    violations: int | None
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and self.violations == 0


@dataclass(frozen=True)
class SuggestedLink:
    link: Link
    match_ratio: float      # share of distinct child values found in the parent column
    status: str = "suggested"


def _check_id(kind: CheckKind, table: str, column: str) -> str:
    return re.sub(r"\W+", "_", f"{kind.value}__{table}__{column}").strip("_")


def generate_checks(
    con: Connector, profile: TableProfile, links: list[Link] | None = None
) -> list[Check]:
    t = quote_ident(profile.table)
    checks: list[Check] = []

    def add(kind: CheckKind, column: str, desc: str, sql: str) -> None:
        checks.append(Check(_check_id(kind, profile.table, column), kind, profile.table, column, desc, sql))

    for c in profile.columns:
        q = quote_ident(c.name)
        add(CheckKind.NOT_NULL, c.name, f"{c.name} is never empty",
            f"SELECT COUNT(*) AS violations FROM {t} WHERE {q} IS NULL")

    unique_sets: list[list[str]] = []
    if profile.key_columns:
        unique_sets.append(profile.key_columns)
    else:
        for c in profile.columns:
            non_null = c.row_count - c.null_count
            if ID_NAME.search(c.name) and non_null and c.distinct_count / non_null >= 0.95:
                unique_sets.append([c.name])
    for cols in unique_sets:
        keys = ", ".join(quote_ident(k) for k in cols)
        not_null = " AND ".join(f"{quote_ident(k)} IS NOT NULL" for k in cols)
        label = ", ".join(cols)
        add(CheckKind.UNIQUE, label, f"({label}) is unique",
            f"SELECT COUNT(*) AS violations FROM (SELECT {keys} FROM {t} WHERE {not_null} "
            f"GROUP BY {keys} HAVING COUNT(*) > 1) AS d")

    for c in profile.columns:
        q = quote_ident(c.name)
        non_null = c.row_count - c.null_count
        if c.is_text and 0 < c.distinct_count <= MAX_ACCEPTED_VALUES and non_null >= 2 * c.distinct_count:
            vals = con.query(f"SELECT DISTINCT {q} AS v FROM {t} WHERE {q} IS NOT NULL ORDER BY 1")["v"].tolist()
            lits = ", ".join(_lit(str(v)) for v in vals)
            add(CheckKind.ACCEPTED_VALUES, c.name, f"{c.name} is one of {vals}",
                f"SELECT COUNT(*) AS violations FROM {t} WHERE {q} IS NOT NULL AND {q} NOT IN ({lits})")
        if c.is_numeric and c.lower_fence is not None:
            add(CheckKind.RANGE, c.name,
                f"{c.name} stays in its normal range ({c.lower_fence:.6g} to {c.upper_fence:.6g})",
                f"SELECT COUNT(*) AS violations FROM {t} "
                f"WHERE {q} < {c.lower_fence!r} OR {q} > {c.upper_fence!r}")

    for l in links or []:
        if l.child != profile.table:
            continue
        cc, pc = quote_ident(l.child_column), quote_ident(l.parent_column)
        add(CheckKind.REFERENTIAL_INTEGRITY, l.child_column,
            f"every {l.child_column} in {l.child} exists in {l.parent}",
            f"SELECT COUNT(*) AS violations FROM {t} c LEFT JOIN {quote_ident(l.parent)} p "
            f"ON c.{cc} = p.{pc} WHERE c.{cc} IS NOT NULL AND p.{pc} IS NULL")
    return checks


def run_checks(con: Connector, checks: list[Check]) -> list[CheckResult]:
    out = []
    for ch in checks:
        try:
            out.append(CheckResult(ch, int(con.query(ch.sql).iloc[0, 0])))
        except Exception as e:  # a failing query is reported, not raised
            out.append(CheckResult(ch, None, str(e)))
    return out


# ---- exporters ----

def to_sql(checks: list[Check]) -> str:
    lines = ["-- DE Bro Code generated checks. Each query returns a violation count; expect 0.", ""]
    for ch in checks:
        lines += [f"-- [{ch.id}] {ch.description}", ch.sql + ";  -- expect 0", ""]
    return "\n".join(lines)


def to_json(checks: list[Check]) -> str:
    return json.dumps([{**asdict(c), "kind": c.kind.value, "expect": 0} for c in checks], indent=2)


_PYTEST_HEAD = '''"""Generated by DE Bro Code. Set ZOMBIE_DATA_DIR to the folder holding {files}."""
import os
from pathlib import Path

import duckdb
import pytest

TABLES = {tables}
CHECKS = {checks}


@pytest.fixture(scope="module")
def con():
    data_dir = Path(os.environ.get("ZOMBIE_DATA_DIR", "."))
    c = duckdb.connect()
    for t in TABLES:
        c.execute(f'CREATE TABLE "{{t}}" AS SELECT * FROM read_csv(?, header=true)', [str(data_dir / f"{{t}}.csv")])
    return c


@pytest.mark.parametrize("sql", [c[1] for c in CHECKS], ids=[c[0] for c in CHECKS])
def test_check(con, sql):
    assert con.execute(sql).fetchone()[0] == 0
'''


def _referenced_tables(checks: list[Check]) -> list[str]:
    names = {c.table for c in checks}
    for c in checks:
        names.update(c.tables)
    for c in checks:
        if c.kind is CheckKind.REFERENTIAL_INTEGRITY:
            m = re.search(r'JOIN "((?:[^"]|"")+)" p', c.sql)
            if m:
                names.add(m.group(1).replace('""', '"'))
    return sorted(names)


def to_pytest(checks: list[Check]) -> str:
    tables = _referenced_tables(checks)
    return _PYTEST_HEAD.format(
        files=", ".join(f"{t}.csv" for t in tables),
        tables=pprint.pformat(tables),
        checks=pprint.pformat([(c.id, c.sql) for c in checks], width=100),
    )


# ---- FK suggestions (FR-12) ----

def suggest_links(con: Connector, tables: list[str], min_match: float = 0.9) -> list[SuggestedLink]:
    """Suggest child->parent links: same column name, parent column unique, values mostly present."""
    from core.profiler import profile_table

    profiles = {t: profile_table(con, t) for t in tables}
    out: list[SuggestedLink] = []
    for parent, pp in profiles.items():
        for pcol in pp.candidate_keys:
            ptype = pp.column(pcol).is_numeric
            for child, cp in profiles.items():
                if child == parent:
                    continue
                for ccol in cp.columns:
                    if ccol.name != pcol or ccol.is_numeric != ptype or ccol.distinct_count == 0:
                        continue
                    cq, pq = quote_ident(ccol.name), quote_ident(pcol)
                    try:
                        found = int(con.query(
                            f"SELECT COUNT(*) AS k FROM (SELECT DISTINCT {cq} AS v FROM {quote_ident(child)} "
                            f"WHERE {cq} IS NOT NULL) c WHERE EXISTS "
                            f"(SELECT 1 FROM {quote_ident(parent)} p WHERE p.{pq} = c.v)")["k"][0])
                    except Exception:
                        continue
                    ratio = found / ccol.distinct_count
                    if ratio >= min_match:
                        out.append(SuggestedLink(Link(child, ccol.name, parent, pcol), round(ratio, 4)))
    return sorted(out, key=lambda s: (s.link.child, s.link.child_column))
