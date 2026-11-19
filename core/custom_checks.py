"""User-defined checks that sit next to the automatic ones.

A custom check is described by a CustomSpec and turned into the same `Check` object the generator makes
(one query that counts violations, passes at 0), so it is exported and run exactly like an automatic check.

Rules:
- Works for any column type. A key can be text or a number, and unique/no-blank checks can combine several
  columns into one composite key.
- Everything is validated and dry-run when it is built. Mistakes raise `CustomCheckError` with a readable
  message instead of a raw database error.
- Free SQL rules are limited to a WHERE-style condition over the loaded tables. Files, table functions, WITH
  clauses and other statements are rejected.
"""
from __future__ import annotations

import math
import re
import zlib
from dataclasses import dataclass
from typing import Callable

import sqlglot
from sqlglot import exp

from core.connectors import Connector, quote_ident
from core.profiler import _NUMERIC, _TEXT, _lit
from core.testgen import Check, CheckKind

MAIN_ALIAS = "t"  # the table being checked is called t inside a free SQL rule

KIND_LABELS = {
    "not_null": "Must not be empty",
    "unique": "Must not repeat (one or more columns together)",
    "accepted_values": "Must be one of a list",
    "range": "Must be between two numbers or dates",
    "pattern": "Must look like a format (letters, digits, email...)",
    "condition": "Advanced: write my own SQL rule",
}

# Ready-made formats so nobody has to write a regular expression.
# name -> (pattern, example that fits). The whole value must match.
PRESET_PATTERNS: dict[str, tuple[str, str]] = {
    "Letters only (spaces, - and ' allowed)": (r"[A-Za-zÀ-ÖØ-öø-ÿ .'-]+", "São Paulo"),
    "Digits only": (r"[0-9]+", "40213"),
    "Letters and digits only": (r"[A-Za-z0-9]+", "AB12"),
    "Email address": (r"[^@\s]+@[^@\s]+\.[^@\s]+", "ana@example.com"),
    "Phone number": (r"\+?[0-9][0-9 ()-]{6,}", "+1 555 123 4567"),
    "Date like 2026-01-31": (r"[0-9]{4}-[0-9]{2}-[0-9]{2}", "2026-01-31"),
    "Exact length (digits)": (r"[0-9]{N}", "N digits, e.g. 6 -> 560001"),
}
_PRESET_BY_PATTERN = {p: name for name, (p, _) in PRESET_PATTERNS.items()}
_BANNED_FUNCS = {"glob", "sniff_csv", "getenv", "query", "query_table", "current_setting", "duckdb_secrets",
                 "which_secret", "load_extension"}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class CustomCheckError(ValueError):
    """A custom check is invalid. The message is meant for the end user."""


@dataclass(frozen=True)
class CustomSpec:
    kind: str
    columns: tuple[str, ...] = ()
    values: tuple[str, ...] = ()          # accepted_values, typed as text
    minimum: str | None = None            # range
    maximum: str | None = None
    pattern: str | None = None            # pattern (whole value must match)
    condition: str | None = None          # condition: true for BAD rows
    normalize_text: bool = False          # unique: ignore case and surrounding spaces in text columns


def parse_values(text: str) -> tuple[str, ...]:
    """One value per line. A single line with commas is split on the commas."""
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    lines = [ln for ln in lines if ln.strip() != ""]
    if len(lines) == 1 and "," in lines[0]:
        lines = [p.strip() for p in lines[0].split(",") if p.strip() != ""]
    return tuple(lines)


# ---------------------------------------------------------------- helpers

def _clean(err: Exception) -> str:
    msg = _ANSI.sub("", str(err)).strip().splitlines()
    return (msg[0] if msg else err.__class__.__name__).strip()


def _names(cols: list[str]) -> str:
    return cols[0] if len(cols) == 1 else "(" + ", ".join(cols) + ")"


def _is_numeric(t: str) -> bool:
    return bool(_NUMERIC.match(t))


def _is_text(t: str) -> bool:
    return bool(_TEXT.match(t))


def _is_date_like(t: str) -> bool:
    return t.upper().startswith(("DATE", "TIMESTAMP", "TIME"))


def _columns(spec: CustomSpec, schema: dict[str, str], *, many: bool) -> list[str]:
    cols = list(spec.columns)
    if not cols:
        raise CustomCheckError("Pick at least one column." if many else "Pick a column.")
    if not many and len(cols) != 1:
        raise CustomCheckError("This kind of check works on exactly one column.")
    if len(set(cols)) != len(cols):
        raise CustomCheckError("A column is listed twice.")
    missing = [c for c in cols if c not in schema]
    if missing:
        raise CustomCheckError(f"Unknown column(s): {', '.join(missing)}. Available: {', '.join(schema)}.")
    return cols


def _literal(con: Connector, col: str, col_type: str, raw: str) -> str:
    """SQL literal for a value typed by the user, matched to the column's type."""
    if _is_text(col_type):
        return _lit(raw)
    raw = raw.strip()
    if _is_numeric(col_type):
        try:
            return str(int(raw))
        except ValueError:
            pass
        try:
            f = float(raw)
        except ValueError:
            raise CustomCheckError(f"'{raw}' is not a number, but column '{col}' holds numbers.") from None
        if not math.isfinite(f):
            raise CustomCheckError(f"'{raw}' is not a usable number.")
        return repr(f)
    expr = f"CAST({_lit(raw)} AS {col_type})"
    try:
        con.query(f"SELECT {expr}")
    except Exception:
        raise CustomCheckError(f"'{raw}' is not a valid {col_type.lower()} value for column '{col}'.") from None
    return expr


def _key_expr(col: str, col_type: str, normalize: bool) -> str:
    q = quote_ident(col)
    return f"LOWER(TRIM({q}))" if normalize and _is_text(col_type) else q


# ---------------------------------------------------------------- builders
# Each returns (description, sql, extra_tables). `t` is the quoted table name.

def _not_null(con, t, table, spec, schema):
    cols = _columns(spec, schema, many=True)
    cond = " OR ".join(f"{quote_ident(c)} IS NULL" for c in cols)
    return (f"{_names(cols)} has no missing values",
            f"SELECT COUNT(*) AS violations FROM {t} WHERE {cond}", ())


def _unique(con, t, table, spec, schema):
    cols = _columns(spec, schema, many=True)
    exprs = ", ".join(_key_expr(c, schema[c], spec.normalize_text) for c in cols)
    present = " AND ".join(f"{quote_ident(c)} IS NOT NULL" for c in cols)
    note = " (ignoring case and spaces)" if spec.normalize_text and any(_is_text(schema[c]) for c in cols) else ""
    return (f"{_names(cols)} is unique{note}",
            f"SELECT COUNT(*) AS violations FROM (SELECT {exprs} FROM {t} WHERE {present} "
            f"GROUP BY {exprs} HAVING COUNT(*) > 1) AS d", ())


def _accepted(con, t, table, spec, schema):
    (col,) = _columns(spec, schema, many=False)
    values = [v for v in spec.values if v != ""]
    if not values:
        raise CustomCheckError("Enter at least one allowed value.")
    lits = ", ".join(_literal(con, col, schema[col], v) for v in values)
    q = quote_ident(col)
    return (f"{col} is one of {list(values)}",
            f"SELECT COUNT(*) AS violations FROM {t} WHERE {q} IS NOT NULL AND {q} NOT IN ({lits})", ())


def _range(con, t, table, spec, schema):
    (col,) = _columns(spec, schema, many=False)
    ctype = schema[col]
    if not (_is_numeric(ctype) or _is_date_like(ctype)):
        raise CustomCheckError(
            f"Range checks need a number or date column, but '{col}' is {ctype.lower()}. "
            "Use an allowed-values or text-pattern check instead.")
    lo = (spec.minimum or "").strip() or None
    hi = (spec.maximum or "").strip() or None
    if lo is None and hi is None:
        raise CustomCheckError("Enter a minimum, a maximum, or both.")
    q = quote_ident(col)
    parts, shown = [], []
    lo_l = _literal(con, col, ctype, lo) if lo is not None else None
    hi_l = _literal(con, col, ctype, hi) if hi is not None else None
    if lo_l and hi_l:
        try:
            if con.query(f"SELECT ({lo_l}) > ({hi_l}) AS bad")["bad"][0]:
                raise CustomCheckError("The minimum is larger than the maximum.")
        except CustomCheckError:
            raise
        except Exception:
            pass
    if lo_l:
        parts.append(f"{q} < {lo_l}")
        shown.append(f">= {lo}")
    if hi_l:
        parts.append(f"{q} > {hi_l}")
        shown.append(f"<= {hi}")
    return (f"{col} is {' and '.join(shown)}",
            f"SELECT COUNT(*) AS violations FROM {t} WHERE {' OR '.join(parts)}", ())


def _pattern(con, t, table, spec, schema):
    (col,) = _columns(spec, schema, many=False)
    pat = spec.pattern or ""
    if pat == "":
        raise CustomCheckError("Enter a pattern, for example [A-Z]{3}-[0-9]{4}.")
    try:
        con.query(f"SELECT regexp_full_match('', {_lit(pat)})")
    except Exception as e:
        raise CustomCheckError(f"The pattern is not valid: {_clean(e)}") from None
    q = quote_ident(col)
    shape = _PRESET_BY_PATTERN.get(pat)
    if shape is None and re.fullmatch(r"\[0-9\]\{\d+\}", pat):
        shape = f"exactly {pat[6:-1]} digits"
    return (f"every {col} matches the pattern {pat}" if shape is None
            else f"every {col} looks like: {shape.lower()}",
            f"SELECT COUNT(*) AS violations FROM {t} WHERE {q} IS NOT NULL "
            f"AND NOT regexp_full_match(CAST({q} AS VARCHAR), {_lit(pat)})", ())


def _validate_condition(sql: str, allowed: set[str]) -> set[str]:
    """Only a single SELECT over the loaded tables may run. Returns the extra tables it reads."""
    try:
        stmts = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.SqlglotError as e:
        raise CustomCheckError(f"The rule is not valid SQL: {_clean(e)}") from None
    if len(stmts) != 1 or not isinstance(stmts[0], exp.Select):
        raise CustomCheckError("The rule must be a single condition, for example: quantity <= 0")
    tree = stmts[0]
    if tree.find(exp.With) or tree.find(exp.Command):
        raise CustomCheckError("WITH clauses are not allowed in a rule.")
    used: set[str] = set()
    for node in tree.find_all(exp.Table):
        if not isinstance(node.this, exp.Identifier) or node.name.lower() not in allowed:
            raise CustomCheckError(
                f"The rule can only read the loaded tables ({', '.join(sorted(allowed))}), not '{node.name or node.sql()}'.")
        used.add(node.name.lower())
    for node in tree.find_all(exp.Func):
        name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
        if name.startswith("read_") or name.endswith("_scan") or name in _BANNED_FUNCS:
            raise CustomCheckError(f"The function {name}() is not allowed in a rule.")
    return used


def _condition(con, t, table, spec, schema):
    cond = (spec.condition or "").strip()
    if not cond:
        raise CustomCheckError("Enter a rule. It should be true for the BAD rows, for example: quantity <= 0")
    if ";" in cond:
        raise CustomCheckError("Remove the semicolon. A rule is one condition.")
    sql = f"SELECT COUNT(*) AS violations FROM {t} AS {MAIN_ALIAS} WHERE ({cond})"
    allowed = {x.lower() for x in con.list_tables()}
    used = _validate_condition(sql, allowed)
    extra = tuple(sorted(u for u in used if u != table.lower()))
    return f"No rows where {' '.join(cond.split())}", sql, extra


_BUILDERS: dict[str, Callable] = {
    "not_null": _not_null, "unique": _unique, "accepted_values": _accepted,
    "range": _range, "pattern": _pattern, "condition": _condition,
}


def looks_like_plain_text(pat: str) -> bool:
    """True when a 'pattern' has no pattern symbols, so it only matches that exact text."""
    return pat != "" and re.fullmatch(r"[\w\s@.,'-]*", pat) is not None


def failing_examples(con: Connector, check: Check, n: int = 5):
    """Up to n rows (or repeated key values) that break a check. None if it cannot be shown."""
    prefix = "SELECT COUNT(*) AS violations FROM "
    if not check.sql.startswith(prefix):
        return None
    body = check.sql[len(prefix):]
    star = "c.*" if check.kind is CheckKind.REFERENTIAL_INTEGRITY else "*"
    try:
        return con.query(f"SELECT {star} FROM {body} LIMIT {int(n)}")
    except Exception:
        return None


def build_custom_check(con: Connector, table: str, spec: CustomSpec) -> Check:
    """Validate a spec, dry-run it, and return a normal Check. Raises CustomCheckError with a readable message."""
    if spec.kind not in _BUILDERS:
        raise CustomCheckError(f"Unknown check type '{spec.kind}'.")
    try:
        schema = {c.name: c.type for c in con.get_schema(table)}
    except KeyError:
        raise CustomCheckError(f"Unknown table '{table}'.") from None
    desc, sql, extra = _BUILDERS[spec.kind](con, quote_ident(table), table, spec, schema)
    try:
        con.query("EXPLAIN " + sql)  # dry run without executing: catches unknown columns, bad types...
    except Exception as e:
        raise CustomCheckError(f"This check could not run: {_clean(e)}") from None
    label = ", ".join(spec.columns) or "(rule)"
    crc = zlib.crc32(repr(spec).encode("utf-8")) & 0xFFFFFFFF
    cid = re.sub(r"\W+", "_", f"custom__{spec.kind}__{table}__{label}__{crc:08x}").strip("_")
    return Check(cid, CheckKind.CUSTOM, table, label, f"Custom: {desc}", sql, extra)
