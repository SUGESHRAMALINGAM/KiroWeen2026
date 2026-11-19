"""Convert profile results into infection findings and an infection score.

Row-level counts are computed with SQL through Connector.query(), so the score is
"rows with at least one issue / total rows" and each row is counted once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.connectors import Connector, quote_ident
from core.profiler import DATE_PATTERNS, TableProfile, _lit, profile_table

EXAMPLE_ROWS = 5


class Zombie(str, Enum):
    GHOST = "ghost"    # nulls
    CLONE = "clone"    # duplicate rows or keys
    MUTANT = "mutant"  # type / format errors
    LOST = "lost"      # orphaned foreign keys
    GIANT = "giant"    # numeric outliers


ZOMBIE_LABELS = {
    Zombie.GHOST: "Ghost Zombie",
    Zombie.CLONE: "Clone Zombie",
    Zombie.MUTANT: "Mutant Zombie",
    Zombie.LOST: "Lost Zombie",
    Zombie.GIANT: "Giant Zombie",
}


@dataclass(frozen=True)
class Link:
    """A foreign-key relationship: child.child_column should exist in parent.parent_column."""
    child: str
    child_column: str
    parent: str
    parent_column: str


@dataclass
class Finding:
    zombie: Zombie
    table: str
    column: str | None
    count: int                 # rows affected by this finding
    severity: str              # low / medium / high
    description: str
    examples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return ZOMBIE_LABELS[self.zombie]

    @property
    def plain(self) -> str:
        """One sentence a non-engineer can read."""
        one = self.count == 1
        rows = f"{self.count} row" + ("" if one else "s")
        have, are, repeat = ("has", "is", "repeats") if one else ("have", "are", "repeat")
        col = self.column
        if self.zombie is Zombie.GHOST:
            return f"{rows} {have} nothing filled in for '{col}'."
        if self.zombie is Zombie.CLONE:
            if col is None:
                return f"{rows} {have} exactly the same values as an earlier row."
            return f"{rows} {repeat} a value that should be unique ({col})."
        if self.zombie is Zombie.MUTANT:
            return f"{rows} {have} a value in '{col}' written differently from the rest of the column."
        if self.zombie is Zombie.LOST:
            target = self.description.removeprefix("No matching ")
            return f"{rows} {have} a '{col}' with no matching record in {target}."
        return f"{rows} {have} a value in '{col}' far outside the normal range."


@dataclass
class InfectionReport:
    table: str
    total_rows: int
    infected_rows: int
    findings: list[Finding]

    @property
    def score(self) -> float:
        """Fraction of rows (0..1) with at least one issue."""
        return self.infected_rows / self.total_rows if self.total_rows else 0.0

    @property
    def percent(self) -> float:
        return round(self.score * 100, 2)

    def counts_by_zombie(self) -> dict[Zombie, int]:
        out = {z: 0 for z in Zombie}
        for f in self.findings:
            out[f.zombie] += f.count
        return out


def severity_for(count: int, total: int) -> str:
    share = count / total if total else 0.0
    if share >= 0.05:
        return "high"
    if share >= 0.01:
        return "medium"
    return "low"


@dataclass
class _Issue:
    zombie: Zombie
    column: str | None
    description: str
    predicate: str  # SQL over alias b


def _issues(profile: TableProfile, links: list[Link]) -> list[_Issue]:
    issues: list[_Issue] = []
    if profile.duplicate_rows:
        issues.append(_Issue(Zombie.CLONE, None, "Fully duplicated rows (extra copies)", "b._rn > 1"))
    if profile.duplicate_keys:
        keys = ", ".join(profile.key_columns)
        issues.append(_Issue(Zombie.CLONE, ", ".join(profile.key_columns),
                             f"Rows repeating a key ({keys})", "b._kn > 1"))
    for c in profile.columns:
        q = f"b.{quote_ident(c.name)}"
        if c.null_count:
            issues.append(_Issue(Zombie.GHOST, c.name, "Missing values", f"{q} IS NULL"))
        if c.mixed_numeric_text:
            issues.append(_Issue(Zombie.MUTANT, c.name, "Non-numeric text in a mostly numeric column",
                                 f"({q} IS NOT NULL AND TRY_CAST({q} AS DOUBLE) IS NULL)"))
        elif c.mixed_date_formats:
            fmt = max(c.date_format_counts, key=c.date_format_counts.get)
            pat = _lit(DATE_PATTERNS[fmt])
            issues.append(_Issue(Zombie.MUTANT, c.name, f"Date not in the dominant format ({fmt})",
                                 f"({q} IS NOT NULL AND NOT REGEXP_MATCHES({q}, {pat}))"))
        if c.outlier_count and c.lower_fence is not None:
            issues.append(_Issue(Zombie.GIANT, c.name,
                                 f"Outside IQR fences [{c.lower_fence:.4g}, {c.upper_fence:.4g}]",
                                 f"({q} < {c.lower_fence!r} OR {q} > {c.upper_fence!r})"))
    for l in links:
        if l.child != profile.table:
            continue
        q = f"b.{quote_ident(l.child_column)}"
        issues.append(_Issue(
            Zombie.LOST, l.child_column,
            f"No matching {l.parent}.{l.parent_column}",
            f"({q} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {quote_ident(l.parent)} p "
            f"WHERE p.{quote_ident(l.parent_column)} = {q}))",
        ))
    return issues


def _base_cte(profile: TableProfile, col_names: list[str]) -> str:
    cols = ", ".join(quote_ident(n) for n in col_names)
    extra = f"ROW_NUMBER() OVER (PARTITION BY {cols}) AS _rn"
    if profile.key_columns:
        keys = ", ".join(quote_ident(k) for k in profile.key_columns)
        not_null = " AND ".join(f"{quote_ident(k)} IS NOT NULL" for k in profile.key_columns)
        extra += (f", CASE WHEN {not_null} THEN ROW_NUMBER() OVER (PARTITION BY {keys}) "
                  "ELSE 1 END AS _kn")
    return f"WITH b AS (SELECT {cols}, {extra} FROM {quote_ident(profile.table)})"


def find_infections(
    con: Connector,
    table: str,
    key_columns: list[str] | None = None,
    links: list[Link] | None = None,
    profile: TableProfile | None = None,
) -> InfectionReport:
    profile = profile or profile_table(con, table, key_columns)
    col_names = [c.name for c in profile.columns]
    select_cols = ", ".join(f"b.{quote_ident(n)}" for n in col_names)
    cte = _base_cte(profile, col_names)
    issues = _issues(profile, links or [])
    total = profile.row_count

    findings: list[Finding] = []
    for i in issues:
        count = int(con.query(f"{cte} SELECT COUNT(*) AS k FROM b WHERE {i.predicate}")["k"][0])
        if not count:
            continue
        ex = con.query(f"{cte} SELECT {select_cols} FROM b WHERE {i.predicate} LIMIT {EXAMPLE_ROWS}")
        examples = ex.astype(object).where(ex.notna(), None).to_dict("records")
        findings.append(Finding(i.zombie, table, i.column, count, severity_for(count, total),
                                i.description, examples))

    infected = 0
    if issues and total:
        any_issue = " OR ".join(i.predicate for i in issues)
        infected = int(con.query(f"{cte} SELECT COUNT(*) AS k FROM b WHERE {any_issue}")["k"][0])
    return InfectionReport(table, total, infected, findings)



