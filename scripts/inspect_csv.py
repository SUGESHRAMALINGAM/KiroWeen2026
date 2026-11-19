"""Load CSVs and print schema and row counts. Usage: py scripts/inspect_csv.py [file.csv ...]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.connectors import CsvConnector

RETAIL = Path(__file__).resolve().parents[1] / "core" / "domain_packs" / "retail"


def main(paths: list[str]) -> None:
    files = [Path(p) for p in paths] or sorted(RETAIL.glob("*.csv"))
    con = CsvConnector()
    for f in files:
        res = con.load_csv(f)
        print(f"{res.table}: {res.rows} rows, {len(res.rejected)} rejected")
        for r in res.rejected:
            print(f"  rejected line {r.line}: {r.error_type} - {r.message}")
    for t in con.list_tables():
        print(f"\n{t}")
        for c in con.get_schema(t):
            print(f"  {c.name:<14} {c.type}")


if __name__ == "__main__":
    main(sys.argv[1:])
