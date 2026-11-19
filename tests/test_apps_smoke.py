from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
HUNTER = str(ROOT / "apps" / "zombie_hunter" / "app.py")
AUTOPSY = str(ROOT / "apps" / "sql_autopsy" / "app.py")
OVERLAY_TEXT = "You are under zombie attack"


def fresh(plain: bool = False, sample: str | None = None) -> AppTest:
    at = AppTest.from_file(HUNTER, default_timeout=30)
    at.run()
    if plain:
        at.toggle(key="plain_mode").set_value(True).run()
    if sample:
        at.selectbox(key="sample_pick").select(sample)
        at.button(key="load_sample").click().run()
    return at


def overlay_shown(at: AppTest) -> bool:
    return any(OVERLAY_TEXT in m.value for m in at.markdown)


@pytest.mark.parametrize("app", ["zombie_hunter", "sql_autopsy"])
def test_app_starts(app):
    at = AppTest.from_file(str(ROOT / "apps" / app / "app.py"), default_timeout=30).run()
    assert not at.exception


def test_sql_autopsy_landing():
    at = AppTest.from_file(AUTOPSY, default_timeout=30).run()
    assert not at.exception and at.info
    assert any("zh-hero" in m.value for m in at.markdown)


def test_kiro_folder_present():
    assert (ROOT / ".kiro" / "steering").is_dir()


def test_landing_shows_hero_and_steps():
    at = fresh()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Zombie" in text and "zh-hero" in text and "zh-steps" in text
    assert not overlay_shown(at)


def test_healthy_sample_no_overlay():
    at = fresh(sample="Retail (mostly healthy)")
    assert not at.exception
    assert [m.label for m in at.metric][:3] == ["Infection score", "Infected rows", "Total rows"]
    assert at.metric[0].value == "1.66%" and at.metric[1].value == "5"
    assert not overlay_shown(at)
    assert any("zh-card" in m.value for m in at.markdown)


def test_outbreak_triggers_overlay_once():
    at = fresh(sample="Retail outbreak (demo)")
    assert not at.exception
    assert overlay_shown(at)
    assert float(at.metric[0].value.rstrip("%")) >= 10
    assert any("at or above your alert level" in w.value for w in at.warning)
    at.selectbox(key="table_select").select("customers").run()
    at.selectbox(key="table_select").select("orders").run()
    assert not overlay_shown(at)  # already shown for this table and settings


def test_plain_mode_hides_theme_and_overlay():
    at = fresh(plain=True, sample="Retail outbreak (demo)")
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "zh-" not in text and not overlay_shown(at)
    assert len(at.title) == 1
    assert any("at or above your alert level" in w.value for w in at.warning)
    assert len(at.dataframe) >= 1


def test_alert_level_slider_controls_overlay():
    at = fresh()
    at.slider(key="threshold").set_value(90).run()
    at.selectbox(key="sample_pick").select("Retail outbreak (demo)")
    at.button(key="load_sample").click().run()
    assert not overlay_shown(at)


def test_checks_run():
    at = fresh(sample="Retail (mostly healthy)")
    next(b for b in at.button if b.label == "Run checks now").click().run()
    assert not at.exception
    assert any("checks failed" in e.value for e in at.error)


# ---------------------------------------------------------------- custom checks in the UI

def run_checks_now(at):
    next(b for b in at.button if b.label == "Run checks now").click().run()
    assert not at.exception


def summary(at) -> str:
    return next(e.value for e in at.error if "checks failed" in e.value)


def test_custom_composite_key_check_runs_with_the_automatic_ones():
    at = fresh(sample="Retail (mostly healthy)")
    run_checks_now(at)
    before = int(summary(at).split(" of ")[1].split(" ")[0])
    at.selectbox(key="cc_kind").select("unique").run()
    at.multiselect(key="cc_cols_orders").set_value(["customer_id", "product_id"]).run()
    at.button(key="cc_add").click().run()
    assert not at.exception and not at.error
    assert any("(customer_id, product_id) is unique" in m.value for m in at.markdown)
    run_checks_now(at)
    assert int(summary(at).split(" of ")[1].split(" ")[0]) == before + 1


def test_bad_custom_rule_shows_a_friendly_message():
    at = fresh(sample="Retail (mostly healthy)")
    at.selectbox(key="cc_kind").select("condition").run()
    at.text_area(key="cc_condition_orders").set_value("no_such_column > 1").run()
    at.button(key="cc_add").click().run()
    assert not at.exception
    assert any("could not run" in e.value for e in at.error)
    at.text_area(key="cc_condition_orders").set_value("quantity IN (SELECT 1 FROM read_csv('x.csv'))").run()
    at.button(key="cc_add").click().run()
    assert not at.exception and any("loaded tables" in e.value for e in at.error)


def test_automatic_checks_can_be_switched_off():
    at = fresh(sample="Retail (mostly healthy)")
    run_checks_now(at)
    before = int(summary(at).split(" of ")[1].split(" ")[0])
    at.multiselect(key="skip_orders").select("range__orders__customer_id").run()
    run_checks_now(at)
    assert int(summary(at).split(" of ")[1].split(" ")[0]) == before - 1

def test_wrong_pattern_gets_a_plain_hint():
    at = fresh(sample="Retail (mostly healthy)")
    at.selectbox(key="cc_kind").select("pattern").run()
    at.selectbox(key="cc_col_orders").select("status").run()
    at.radio(key="cc_shape_orders").set_value("Something else (advanced)").run()
    at.text_input(key="cc_pattern_orders").set_value("090909909").run()
    at.button(key="cc_add").click().run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "None of the 301 rows follow this rule" in text
    assert any("has no format symbols" in i.value for i in at.info)

# ---------------------------------------------------------------- SQL Autopsy

def autopsy(sql: str | None = None, example: str | None = None, plain: bool = False, dialect: str | None = None):
    at = AppTest.from_file(AUTOPSY, default_timeout=30)
    at.run()
    if plain:
        at.toggle(key="plain_mode").set_value(True).run()
    if dialect:
        at.selectbox(key="dialect").select(dialect).run()
    if example:
        at.selectbox(key="example_pick").select(example)
        at.button(key="load_example").click().run()
    if sql is not None:
        at.text_area(key="sql_text").set_value(sql).run()
    assert not at.exception
    return at


def test_autopsy_example_with_problems():
    at = autopsy(example="Sales by region (has problems)")
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Tables read"] == "4" and metrics["Problems found"] == "4"
    assert any("likely bug" in e.value for e in at.error)
    text = " ".join(m.value for m in at.markdown)
    assert "In plain English" in text and "keeps only rows where" in text
    assert "NOT IN with a sub-query" in text and "How to fix" in text


def test_autopsy_clean_example_and_lineage():
    at = autopsy(example="Monthly report (clean)")
    assert any("No common problems" in s.value for s in at.success)
    lin = next(df.value for df in at.dataframe if "Result column" in df.value)
    assert dict(zip(lin["Result column"], lin["Comes from"]))["region"] == "customers.region"


def test_autopsy_parse_error_is_friendly():
    at = autopsy(sql="SELECT a FROM WHERE")
    assert any("line 1, column 19" in e.value for e in at.error)
    assert any(">>WHERE<<" in c.value for c in at.code)


def test_autopsy_plain_mode_and_dialect():
    at = autopsy(sql="SELECT TOP 5 * FROM dbo.t", plain=True, dialect="tsql")
    assert len(at.title) == 1
    assert "zh-" not in " ".join(m.value for m in at.markdown)
    assert {m.label: m.value for m in at.metric}["Problems found"] == "1"

def test_autopsy_script_summary_and_picker():
    at = autopsy(sql="SELECT a FROM t;\nINSERT INTO mart.x SELECT b FROM u;\nSELECT FROM WHERE;")
    m = {x.label: x.value for x in at.metric}
    assert m["Statements"] == "2" and m["Tables written"] == "1"
    assert any("Not analysed (1)" in e.label for e in at.expander)
    at.selectbox(key="stmt_pick").select(2).run()
    assert not at.exception
    assert "writes the result into mart.x" in " ".join(x.value for x in at.markdown)


def test_autopsy_procedure_example():
    at = autopsy(example="Script with a stored procedure (SQL Server)", dialect="tsql")
    m = {x.label: x.value for x in at.metric}
    assert m["Procedures"] == "1" and m["Statements"] == "3"


def test_autopsy_tables_preview_and_schema_issues():
    at = autopsy(sql="SELECT qty FROM orders")
    at.button(key="use_sample").click().run()
    assert not at.exception
    assert "does not exist in orders" in " ".join(x.value for x in at.markdown)
    at.text_area(key="sql_text").set_value("SELECT status, COUNT(*) AS n FROM orders GROUP BY status").run()
    at.button(key="preview_btn").click().run()
    assert not at.exception and len(at.dataframe[-1].value) == 3
    at.text_area(key="sql_text").set_value("UPDATE orders SET status = 'x' WHERE order_id = 1").run()
    at.button(key="preview_btn").click().run()
    assert any("Preview only runs read-only queries" in w.value for w in at.warning)


def test_autopsy_row_explosion_with_sample():
    at = autopsy(example="Totals that are too big (load the retail sample)")
    at.button(key="use_sample").click().run()
    assert "The joins multiply your rows" in " ".join(x.value for x in at.markdown)


def test_sql_file_size_limit_constant():
    src = Path(AUTOPSY).read_text(encoding="utf-8")
    assert "MAX_SQL_FILE_BYTES = 1024 * 1024" in src and "is too big" in src

def test_autopsy_shows_syntax_problem_for_extra_semicolons():
    at = autopsy(sql="SELECT 1 FROM a;\n;;;\nSELECT 2 FROM b;")
    assert any("1 syntax problem" in e.value for e in at.error)
    assert "Line 2" in " ".join(m.value for m in at.markdown)