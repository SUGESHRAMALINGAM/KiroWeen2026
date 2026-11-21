"""Whole SQL files: many statements and stored procedures (best effort).

`analyze_script(text, dialect, schema=None)` splits a file into statements, analyses each one with
`core.sql_analyzer.analyze`, and pulls the statements out of CREATE PROCEDURE / FUNCTION bodies.
One broken statement never stops the others. Anything that cannot be read is listed with its line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot.tokens import TokenType

from core.sql_analyzer import Analysis, SqlError, analyze

MAX_SCRIPT_CHARS = 1_000_000
MAX_STATEMENTS = 200


@dataclass
class StatementResult:
    index: int                 # 1-based, in file order
    line: int
    sql: str
    analysis: Analysis
    procedure: str | None = None


@dataclass
class Unreadable:
    line: int
    sql: str
    message: str
    procedure: str | None = None


@dataclass
class ProcedureResult:
    name: str
    kind: str                  # PROCEDURE / FUNCTION
    params: str
    line: int
    statements: list[StatementResult] = field(default_factory=list)
    not_analysed: list[Unreadable] = field(default_factory=list)


@dataclass
class ScriptAnalysis:
    dialect: str
    statements: list[StatementResult] = field(default_factory=list)   # outside procedures
    unreadable: list[Unreadable] = field(default_factory=list)        # outside procedures
    procedures: list[ProcedureResult] = field(default_factory=list)
    empty_statements: list[Unreadable] = field(default_factory=list)  # stray ;; that real databases reject
    error: SqlError | None = None

    @property
    def syntax_problems(self) -> list[Unreadable]:
        """Statements that could not be read (real errors, not control-flow steps) plus empty statements."""
        bad = [u for u in self.all_unreadable if "could not be read" in u.message]
        return sorted(bad + self.empty_statements, key=lambda u: u.line)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def all_statements(self) -> list[StatementResult]:
        items = self.statements + [s for p in self.procedures for s in p.statements]
        return sorted(items, key=lambda s: s.index)

    @property
    def all_unreadable(self) -> list[Unreadable]:
        items = self.unreadable + [u for p in self.procedures for u in p.not_analysed]
        return sorted(items, key=lambda u: u.line)

    @property
    def tables_read(self) -> list[str]:
        return list(dict.fromkeys(t for s in self.all_statements for t in s.analysis.tables))

    @property
    def tables_written(self) -> list[str]:
        return list(dict.fromkeys(s.analysis.target for s in self.all_statements
                                  if s.analysis.target and s.analysis.statement != "CALL"))

    @property
    def procedures_called(self) -> list[str]:
        return list(dict.fromkeys(s.analysis.target for s in self.all_statements
                                  if s.analysis.statement == "CALL" and s.analysis.target))

    @property
    def problem_counts(self) -> dict[int, int]:
        return {s.index: len(s.analysis.issues) for s in self.all_statements}


# ---------------------------------------------------------------- low-level text helpers

_WORDS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/|\$(\w*)\$.*?\$\1\$|\b\w+\b|[();]",
                    re.S)
_BLOCK_ONLY = re.compile(r"^(END(\s+(IF|LOOP|WHILE|FOR|REPEAT|CASE|TRY|CATCH))?|BEGIN|ELSE|THEN|GO)$", re.I)
_DML = re.compile(r"\b(WITH|SELECT|INSERT|UPDATE|DELETE|MERGE)\b", re.I)
_PROC = re.compile(
    r"\bCREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?(?:DEFINER\s*=\s*\S+\s+)?(PROCEDURE|PROC|FUNCTION)\s+"
    r"((?:[\w$]+|\"[^\"]+\"|`[^`]+`|\[[^\]]+\])(?:\s*\.\s*(?:[\w$]+|\"[^\"]+\"|`[^`]+`|\[[^\]]+\]))*)", re.I)
_GO = re.compile(r"^[ \t]*GO[ \t]*$", re.I | re.M)


def _line_at(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _only_comments(piece: str) -> bool:
    return re.sub(r"--[^\n]*|/\*.*?\*/", "", piece, flags=re.S).strip() == ""


def _split_simple(text: str, base: int) -> list[tuple[int, str]]:
    """Split on ; at bracket depth 0, skipping strings and comments. Returns (absolute start, piece)."""
    out, start, depth = [], 0, 0
    for m in _WORDS.finditer(text):
        tok = m.group(0)
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth = max(depth - 1, 0)
        elif tok == ";" and depth == 0:
            out.append((base + start, text[start:m.start()]))
            start = m.end()
    out.append((base + start, text[start:]))
    return out


def _split_tokens(text: str, dialect: str) -> list[tuple[int, str]]:
    """Split on SEMICOLON tokens from sqlglot, so ; inside strings is safe."""
    tokens = sqlglot.tokenize(text, read=dialect)
    out, start = [], 0
    for t in tokens:
        if t.token_type == TokenType.SEMICOLON:
            out.append((start, text[start:t.start]))
            start = t.end + 1
    out.append((start, text[start:]))
    return out


def _match_end(text: str, begin_end: int) -> tuple[int, int] | None:
    """Given the end offset of a BEGIN keyword, find the matching END. Returns (end_start, end_end)."""
    depth = 1
    toks = list(_WORDS.finditer(text, begin_end))
    for i, m in enumerate(toks):
        w = m.group(0).upper()
        if w in ("BEGIN", "CASE"):
            nxt = toks[i + 1].group(0).upper() if i + 1 < len(toks) else ""
            if w == "BEGIN" and nxt in ("TRAN", "TRANSACTION", "DISTRIBUTED"):
                continue
            depth += 1
        elif w == "END":
            nxt = toks[i + 1].group(0).upper() if i + 1 < len(toks) else ""
            if nxt in ("IF", "LOOP", "WHILE", "FOR", "REPEAT"):
                continue
            depth -= 1
            if depth == 0:
                stop = m.end()
                if nxt in ("TRY", "CATCH", "CASE"):
                    stop = toks[i + 1].end()
                return m.start(), stop
    return None


def _balanced(text: str, open_pos: int) -> int:
    """Index just after the ) matching the ( at open_pos."""
    depth = 0
    for m in _WORDS.finditer(text, open_pos):
        if m.group(0) == "(":
            depth += 1
        elif m.group(0) == ")":
            depth -= 1
            if depth == 0:
                return m.end()
    return len(text)


# ---------------------------------------------------------------- procedures

def _find_procedures(text: str) -> list[dict]:
    """Locate CREATE PROCEDURE/FUNCTION blocks: header, params, body span, whole-block span."""
    found = []
    pos = 0
    while True:
        m = _PROC.search(text, pos)
        if not m:
            break
        kind = "FUNCTION" if m.group(1).upper() == "FUNCTION" else "PROCEDURE"
        name = re.sub(r"[\s`\"\[\]]", "", m.group(2))
        after = m.end()
        rest = text[after:]
        params = ""
        stripped = rest.lstrip()
        if stripped.startswith("("):
            p_open = after + (len(rest) - len(stripped))
            p_close = _balanced(text, p_open)
            params = text[p_open + 1:p_close - 1].strip()
            after = p_close
        else:
            as_m = re.search(r"\bAS\b|\bBEGIN\b", text[after:], re.I)
            if as_m:
                params = text[after:after + as_m.start()].strip()
        dollar = re.compile(r"\$(\w*)\$").search(text, after)
        begin = re.compile(r"\bBEGIN\b", re.I).search(text, after)
        body_start = body_end = block_end = None
        pre = ""
        if dollar and (not begin or dollar.start() < begin.start()):
            close = text.find(f"${dollar.group(1)}$", dollar.end())
            if close == -1:
                close = len(text)
            inner_start, inner_end = dollar.end(), close
            semi = text.find(";", close)
            block_end = len(text) if semi == -1 else semi + 1
            inner = text[inner_start:inner_end]
            b = re.search(r"\bBEGIN\b", inner, re.I)
            if b:
                e = _match_end(text, inner_start + b.end())
                pre = text[inner_start:inner_start + b.start()]
                body_start = inner_start + b.end()
                body_end = e[0] if e else inner_end
            else:
                body_start, body_end = inner_start, inner_end
            pre_start = inner_start
        elif begin:
            e = _match_end(text, begin.end())
            body_start = begin.end()
            body_end = e[0] if e else len(text)
            end_after = e[1] if e else len(text)
            semi = re.match(r"\s*;", text[end_after:])
            block_end = end_after + (semi.end() if semi else 0)
            pre = text[after:begin.start()]
            pre_start = after
        else:
            as_m = re.search(r"\bAS\b", text[after:], re.I)
            body_start = after + as_m.end() if as_m else after
            go = _GO.search(text, body_start)
            body_end = block_end = go.start() if go else len(text)
            pre_start = after
        found.append(dict(name=name, kind=kind, params=params, start=m.start(), body_start=body_start,
                          body_end=body_end, block_end=block_end, pre=pre, pre_start=pre_start))
        pos = block_end if block_end > m.end() else m.end()
    return found


def _inner_pieces(text: str, start: int, end: int) -> list[tuple[int, str]]:
    return _split_simple(text[start:end], start)


# ---------------------------------------------------------------- entry point

_DELIM_LINE = re.compile(r"^[ \t]*DELIMITER[ \t]+(\S+)[ \t]*$", re.I | re.M)
_LEADING_COMMENTS = re.compile(r"\A(?:\s*(?:--[^\n]*(?:\n|\Z)|/\*.*?\*/))*\s*", re.S)


def _lead(piece: str) -> int:
    """Characters of whitespace and comments before the statement starts."""
    return _LEADING_COMMENTS.match(piece).end()


def _apply_delimiters(text: str) -> str:
    """MySQL client `DELIMITER //` support, keeping every offset: the DELIMITER lines become spaces and
    the custom delimiter (outside strings and comments) becomes ';' padded with spaces."""
    out, pos, delim = [], 0, ";"
    for m in _DELIM_LINE.finditer(text):
        out.append(_replace_delim(text[pos:m.start()], delim))
        out.append(" " * (m.end() - m.start()))
        delim, pos = m.group(1), m.end()
    out.append(_replace_delim(text[pos:], delim))
    return "".join(out)


def _replace_delim(chunk: str, delim: str) -> str:
    if delim == ";":
        return chunk
    pat = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/|" + re.escape(delim), re.S)
    return pat.sub(lambda m: (";" + " " * (len(delim) - 1)) if m.group(0) == delim else m.group(0), chunk)


def _note_empty(res: "ScriptAnalysis", text: str, semi_pos: int, procedure: str | None) -> None:
    """Record an empty statement (two semicolons with nothing between). Runs like ;;; become one note."""
    line = _line_at(text, semi_pos)
    if res.empty_statements and res.empty_statements[-1].line == line \
            and res.empty_statements[-1].procedure == procedure:
        last = res.empty_statements[-1]
        res.empty_statements[-1] = Unreadable(line, last.sql + ";", last.message, procedure)
        return
    res.empty_statements.append(Unreadable(
        line, ";;", "Empty statement: extra semicolon(s) with nothing between them. Many databases stop "
        "here with an error (MySQL: 'Query was empty'). Remove the extra ;.", procedure))


def analyze_script(text: str, dialect: str = "duckdb",
                   schema: dict[str, dict[str, str]] | None = None) -> ScriptAnalysis:
    res = ScriptAnalysis(dialect=dialect)
    if not text or not text.strip():
        res.error = SqlError("Paste SQL or upload a .sql file to analyse.")
        return res
    if len(text) > MAX_SCRIPT_CHARS:
        res.error = SqlError(f"The file is too long ({len(text):,} characters). The limit is {MAX_SCRIPT_CHARS:,}.")
        return res

    original = text
    text = _apply_delimiters(text)  # same length; MySQL DELIMITER lines blanked, custom delimiters -> ;
    procs = _find_procedures(text)
    # Blank out procedure blocks (keep newlines) so the top-level split does not cut through them.
    chars = list(text)
    for p in procs:
        for i in range(p["start"], p["block_end"]):
            if chars[i] != "\n":
                chars[i] = " "
    top = _GO.sub(lambda m: ";" + " " * (len(m.group(0)) - 1), "".join(chars))

    try:
        pieces = _split_tokens(top, dialect)
    except Exception:
        pieces = _split_simple(top, 0)

    jobs: list[tuple[int, str, ProcedureResult | None]] = []
    for k, (start, piece) in enumerate(pieces):
        if piece.strip() and not _only_comments(piece):
            lead = _lead(piece)
            jobs.append((start + lead, piece[lead:].strip(), None))
        elif k < len(pieces) - 1 and text[start:start + len(piece)].strip() == "":
            _note_empty(res, original, start + len(piece), None)

    for p in procs:
        pr = ProcedureResult(p["name"], p["kind"], p["params"], _line_at(text, p["start"]))
        res.procedures.append(pr)
        for start, piece in _split_simple(p["pre"], p["pre_start"]):
            s = piece.strip()
            if s and not _only_comments(s) and not re.match(r"^(AS|LANGUAGE\b.*|RETURNS\b.*)$", s, re.I | re.S):
                if re.match(r"^\s*DECLARE\b", s, re.I):
                    pr.not_analysed.append(Unreadable(_line_at(text, start + len(piece) - len(piece.lstrip())),
                                                      s, "Variable declaration, not analysed.", pr.name))
        inner = _inner_pieces(text, p["body_start"], p["body_end"])
        for k, (start, piece) in enumerate(inner):
            if piece.strip() and not _only_comments(piece):
                lead = _lead(piece)
                jobs.append((start + lead, piece[lead:].strip(), pr))
            elif 0 < k < len(inner) - 1 and piece.strip() == "":
                _note_empty(res, original, start + len(piece), pr.name)

    if len(jobs) > MAX_STATEMENTS:
        res.error = SqlError(f"The file has {len(jobs)} statements. The limit is {MAX_STATEMENTS} per file. "
                             "Split it into smaller files.")
        return res

    done: list[tuple[int, StatementResult | Unreadable, ProcedureResult | None]] = []
    for start, sql, pr in sorted(jobs, key=lambda j: j[0]):
        line = _line_at(text, start)
        name = pr.name if pr else None
        if pr is not None:
            if _BLOCK_ONLY.match(sql):
                continue
            first = sql.split(None, 1)[0].upper()
            if first not in ("WITH", "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE"):
                picked = None
                for m in _DML.finditer(sql):
                    sub = re.sub(r"\s+END(\s+IF)?\s*$", "", sql[m.start():], flags=re.I)
                    a = analyze(sub, dialect, schema)
                    if a.ok:
                        picked = (m.start(), sub, a)
                        break
                if picked is None:
                    done.append((start, Unreadable(line, sql, "Control-flow or variable step, not analysed.", name),
                                 pr))
                    continue
                offset, sql, a = picked
                line = _line_at(text, start + offset)
                done.append((start + offset, StatementResult(0, line, sql, a, name), pr))
                continue
        a = analyze(sql, dialect, schema)
        if a.ok:
            if a.statement == "CALL" and a.target:
                defined = next((p for p in procs if p["name"].split(".")[-1].lower()
                                == a.target.split(".")[-1].lower()), None)
                if defined:
                    a.explanation.append(f"That procedure is defined in this file on line "
                                         f"{_line_at(text, defined['start'])}.")
            done.append((start, StatementResult(0, line, sql, a, name), pr))
        else:
            msg = a.error.message
            if a.error.line:
                msg = msg.replace(f"at line {a.error.line},", f"at line {line + a.error.line - 1},")
            done.append((start, Unreadable(line, sql, msg, name), pr))

    n = 0
    for _, item, pr in sorted(done, key=lambda d: d[0]):
        if isinstance(item, StatementResult):
            n += 1
            item.index = n
            (pr.statements if pr else res.statements).append(item)
        else:
            (pr.not_analysed if pr else res.unreadable).append(item)
    return res
