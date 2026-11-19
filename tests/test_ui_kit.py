import re

from core.rules import Finding, Zombie
from ui_kit import (ZOMBIES, finding_html, hero_html, overlay_html, sprite, steps_html, theme_css,
                    verdict_html, verdict_state, verdict_text, zombie_cards_html)


def test_every_zombie_has_a_wellformed_sprite():
    assert set(ZOMBIES) == {z.value for z in Zombie}
    for k in ZOMBIES:
        svg = sprite(k)
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        assert svg.count("<g") == svg.count("</g>")
        assert ZOMBIES[k].name in svg  # aria-label


def test_html_has_no_blank_lines_or_indented_blocks():
    """Streamlit's markdown parser turns blank lines or 4-space indents into code blocks."""
    parts = [hero_html(), steps_html(), verdict_html(5, 10, 5, 100), zombie_cards_html({"ghost": 2}),
             overlay_html("t", 20, 20, 100, {"ghost": 20}), finding_html("ghost", "low", "x", "y")]
    for p in parts:
        assert "\n" not in p


def test_user_text_is_escaped():
    evil = '<script>alert(1)</script>"><img src=x onerror=alert(1)>'
    for html in (overlay_html(evil, 20, 1, 5, {"ghost": 1}, uid=evil),
                 finding_html("ghost", "low", evil, evil)):
        assert "<script>" not in html and "<img" not in html
        assert "&lt;script&gt;" in html or "&lt;" in html


def test_verdict_states():
    assert verdict_state(0, 10) == "clean"
    assert verdict_state(3, 10) == "mild"
    assert verdict_state(10, 10) == "attack"
    assert "All" in verdict_text(0, 10, 0, 50)[1]
    assert "5 of 100" in verdict_text(5, 10, 5, 100)[1]


def test_ring_is_clamped():
    assert "stroke-dasharray" in verdict_html(250, 10, 5, 2)


def test_css_respects_reduced_motion_and_overlay_rules():
    css = theme_css()
    assert "prefers-reduced-motion: reduce" in css and "prefers-reduced-motion: no-preference" in css
    reduce_block = css.split("prefers-reduced-motion: reduce")[1].split("}")[0]
    assert ".zh-overlay" in reduce_block and "display: none" in reduce_block
    # all keyframe animations live inside the no-preference block
    outside = css.split("prefers-reduced-motion: no-preference")[0]
    assert "animation:" not in outside


def test_theme_contrast_ratios():
    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    def ratio(a, b):
        la, lb = sorted((lum(a), lum(b)), reverse=True)
        return (la + 0.05) / (lb + 0.05)

    css = theme_css()
    var = lambda n: re.search(rf"--zh-{n}:\s*(#[0-9a-fA-F]{{6}})", css).group(1)
    for fg in ("bone", "muted", "green", "red"):
        assert ratio(var(fg), var("panel")) >= 4.5, fg
    assert ratio("#240606", var("red")) >= 4.5      # badge text
    assert ratio("#0c1a05", var("green")) >= 4.5
    assert ratio("#06210f", "#5fd38d") >= 4.5      # low badge


def test_finding_plain_sentences():
    f = Finding(Zombie.GHOST, "t", "email", 1, "low", "Missing values")
    assert f.plain == "1 row has nothing filled in for 'email'."
    assert Finding(Zombie.GHOST, "t", "email", 4, "low", "x").plain == "4 rows have nothing filled in for 'email'."
    lost = Finding(Zombie.LOST, "orders", "customer_id", 3, "low", "No matching customers.customer_id")
    assert "3 rows" in lost.plain and "customers.customer_id" in lost.plain
    assert "same values as an earlier row" in Finding(Zombie.CLONE, "t", None, 2, "low", "d").plain

