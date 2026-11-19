"""Profile CSVs and print the result. Usage: py scripts/profile_csv.py [file.csv ...] [--key col1,col2]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.connectors import CsvConnector
from core.profiler import profile_table

RETAIL = Path(__file__).resolve().parents[1] / "core" / "domain_packs" / "retail"


def fmt(v) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def main(argv: list[str]) -> None:
    key = None
    if "--key" in argv:
        i = argv.index("--key")
        key = argv[i + 1].split(",")
        argv = argv[:i] + argv[i + 2:]
    files = [Path(p) for p in argv] or sorted(RETAIL.glob("*.csv"))
    con = CsvConnector()
    for f in files:
        res = con.load_csv(f)
        known = {c.name for c in con.get_schema(res.table)}
        p = profile_table(con, res.table, key if key and set(key) <= known else None)
        print(f"\n== {p.table}: {p.row_count} rows, {p.duplicate_rows} duplicate rows"
              + (f", {p.duplicate_keys} duplicate keys {p.key_columns}" if p.duplicate_keys is not None else ""))
        print(f"   candidate keys: {', '.join(p.candidate_keys) or '-'}")
        print(f"   {'column':<14}{'type':<10}{'nulls':>6}{'distinct':>9}{'min':>12}{'max':>12}{'mean':>9}{'outliers':>9}")
        for c in p.columns:
            print(f"   {c.name:<14}{c.type:<10}{c.null_count:>6}{c.distinct_count:>9}"
                  f"{fmt(c.min)[:11]:>12}{fmt(c.max)[:11]:>12}{fmt(c.mean):>9}{c.outlier_count:>9}")
            if c.numbers_stored_as_text or c.mixed_numeric_text:
                print(f"     ! numeric-looking text: {c.numeric_text_count} numeric, {c.non_numeric_text_count} not")
            if c.mixed_date_formats:
                print(f"     ! mixed date formats: {c.date_format_counts}")


if __name__ == "__main__":
    main(sys.argv[1:])

