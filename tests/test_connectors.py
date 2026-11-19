import pandas as pd
import pytest

from core.connectors import Column, Connector, CsvConnector, quote_ident

RETAIL = __import__("pathlib").Path(__file__).resolve().parents[1] / "core" / "domain_packs" / "retail"


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_is_connector():
    assert isinstance(CsvConnector(), Connector)


def test_load_and_schema(tmp_path):
    con = CsvConnector()
    res = con.load_csv(write(tmp_path, "People.csv", "id,name\n1,a\n2,b\n"))
    assert (res.table, res.rows, res.rejected) == ("people", 2, [])
    assert con.list_tables() == ["people"]
    assert con.get_schema("people") == [Column("id", "BIGINT"), Column("name", "VARCHAR")]


def test_sample_and_query(tmp_path):
    con = CsvConnector()
    con.load_csv(write(tmp_path, "t.csv", "x\n1\n2\n3\n"))
    assert len(con.sample("t", 2)) == 2
    assert con.query("SELECT SUM(x) AS s FROM t")["s"][0] == 6


def test_unknown_table():
    with pytest.raises(KeyError):
        CsvConnector().get_schema("nope")


def test_rejected_rows_reported(tmp_path):
    con = CsvConnector()
    res = con.load_csv(write(tmp_path, "bad.csv", "a,b\n1,2\n3,4,5\n6,7\n"))
    assert res.rows == 2
    assert len(res.rejected) == 1
    assert res.rejected[0].line == 3
    assert con.rejected_rows("bad") == res.rejected


def test_multiple_files_hide_internal_tables(tmp_path):
    con = CsvConnector()
    con.load_csv(write(tmp_path, "a.csv", "a,b\n1,2\n3,4,5\n"))
    con.load_csv(write(tmp_path, "b.csv", "x\n1\n"))
    assert con.list_tables() == ["a", "b"]


def test_reload_replaces_table(tmp_path):
    con = CsvConnector()
    p = write(tmp_path, "t.csv", "x\n1\n")
    con.load_csv(p)
    p.write_text("x\n1\n2\n", encoding="utf-8")
    assert con.load_csv(p).rows == 2


def test_quote_ident():
    assert quote_ident('a"b') == '"a""b"'


def test_retail_sample():
    con = CsvConnector()
    for f in sorted(RETAIL.glob("*.csv")):
        assert con.load_csv(f).rejected == []
    assert con.list_tables() == ["customers", "orders", "products"]
    assert con.query("SELECT COUNT(*) AS n FROM orders")["n"][0] == 301


def test_type_conflict_reloads_column_as_text(tmp_path):
    con = CsvConnector()
    res = con.load_csv(write(tmp_path, "m.csv", "d\n2026-01-01\n2026-01-02\n05/01/2026\n"))
    assert res.rows == 3 and res.rejected == [] and res.text_columns == ["d"]
    assert con.get_schema("m")[0].type == "VARCHAR"


def test_concurrent_use_from_many_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    con = CsvConnector()
    con.load_csv(write(tmp_path, "t.csv", "x\n" + "\n".join(str(i) for i in range(200)) + "\n"))

    def work(_):
        return (con.list_tables(), len(con.get_schema("t")), len(con.sample("t", 5)),
                int(con.query("SELECT SUM(x) AS s FROM t")["s"][0]))

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(work, range(200)))
    assert all(r == (["t"], 1, 5, 19900) for r in results)
