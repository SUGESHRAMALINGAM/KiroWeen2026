"""Write Markdown docs for .sql files. Used by the Kiro hook and by hand.

Usage:
  py scripts/gen_docs.py path/to/file.sql [more.sql ...] [--dialect duckdb] [--out docs/generated]

With no paths, reads a Kiro hook event from stdin and documents the saved .sql file in it.
Writes <out>/<file name>.md and prints the path. Only reads .sql files; never runs SQL.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.docgen import script_doc  # noqa: E402
from core.sql_analyzer import DIALECTS  # noqa: E402
from core.sql_script import MAX_SCRIPT_CHARS, analyze_script  # noqa: E402


def paths_from_hook_event(raw: str) -> list[Path]:
    """Pull .sql file paths out of a hook event (JSON on stdin). Unknown shapes give no paths."""
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    found: list[str] = []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and v.lower().endswith(".sql"):
            found.append(v)

    walk(event)
    return [Path(p) for p in dict.fromkeys(found)]


def document(path: Path, dialect: str, out_dir: Path) -> Path:
    if path.suffix.lower() != ".sql":
        raise ValueError(f"{path} is not a .sql file")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if len(text) > MAX_SCRIPT_CHARS:
        raise ValueError(f"{path} is larger than {MAX_SCRIPT_CHARS:,} characters")
    script = analyze_script(text, dialect)
    if not script.ok:
        raise ValueError(f"{path}: {script.error.message}")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{path.stem}.md"
    target.write_text(script_doc(script, f"{path.name}"), encoding="utf-8")
    return target


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--dialect", default="duckdb", choices=sorted(DIALECTS))
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "generated")
    args = ap.parse_args(argv)
    paths = args.paths or (paths_from_hook_event(sys.stdin.read()) if not sys.stdin.isatty() else [])
    if not paths:
        print("No .sql file given.", file=sys.stderr)
        return 1
    code = 0
    for p in paths:
        try:
            print(document(p, args.dialect, args.out))
        except (OSError, ValueError) as e:
            print(f"Skipped: {e}", file=sys.stderr)
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
