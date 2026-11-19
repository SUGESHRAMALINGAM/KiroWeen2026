"""HTML builders for the Zombie Hunter UI. Pure functions; every user-supplied string is escaped."""
from __future__ import annotations

import math
from html import escape

from .overlay import _flat
from .sprites import ZOMBIES, sprite

STEPS = [
    ("1", "Load your data", "Upload a CSV, or try a sample. Nothing is stored."),
    ("2", "Meet the zombies", "We find blanks, copies, odd formats, broken links and extreme values."),
    ("3", "Get ready-made checks", "Download checks that keep these problems from coming back."),
]


def hero_html() -> str:
    strip = "".join(sprite(k, 64) for k in ZOMBIES)
    return _flat(f"""
    <div class="zh-hero">
      <h1>Zombie <span>Hunter</span></h1>
      <p>Drop in a spreadsheet and find out what is wrong with it. Every data problem is a zombie,
      and we explain each one in plain English. No coding needed.</p>
      <div class="zh-strip">{strip}</div>
    </div>
    """)


def steps_html() -> str:
    items = "".join(f'<div class="zh-step"><b>{n}</b>{escape(t)}<span>{escape(d)}</span></div>' for n, t, d in STEPS)
    return f'<div class="zh-steps">{items}</div>'


def verdict_state(percent: float, threshold: float) -> str:
    if percent <= 0:
        return "clean"
    return "attack" if percent >= threshold else "mild"


def verdict_text(percent: float, threshold: float, infected: int, total: int) -> tuple[str, str]:
    state = verdict_state(percent, threshold)
    headline = {"clean": "All clear. No zombies found.",
                "mild": "A few zombies are loose.",
                "attack": "Zombie outbreak!"}[state]
    detail = (f"{infected:,} of {total:,} rows ({percent:g}%) have at least one problem."
              if infected else f"All {total:,} rows look healthy.")
    return headline, detail


def ring_svg(percent: float, state: str) -> str:
    color = {"clean": "#5fd38d", "mild": "#ffd166", "attack": "#ff6b6b"}[state]
    r = 52
    circ = 2 * math.pi * r
    filled = circ * min(max(percent, 0), 100) / 100
    return (f'<svg width="132" height="132" viewBox="0 0 132 132" role="img" '
            f'aria-label="Infection score {percent:g} percent">'
            f'<circle class="zh-ring-bg" cx="66" cy="66" r="{r}" fill="none" stroke-width="12"/>'
            f'<circle class="zh-ring-fg" cx="66" cy="66" r="{r}" fill="none" stroke-width="12" stroke="{color}" '
            f'stroke-dasharray="{filled:.1f} {circ:.1f}" transform="rotate(-90 66 66)"/>'
            f'<text class="zh-ring-text" x="66" y="74" text-anchor="middle">{percent:g}%</text></svg>')


def verdict_html(percent: float, threshold: float, infected: int, total: int) -> str:
    state = verdict_state(percent, threshold)
    headline, detail = verdict_text(percent, threshold, infected, total)
    return _flat(f"""
    <div class="zh-verdict {state}">
      {ring_svg(percent, state)}
      <div><h2>{escape(headline)}</h2><p>{escape(detail)}</p>
      <p>Infection score = rows with at least one problem, divided by all rows.</p></div>
    </div>
    """)


def zombie_cards_html(counts: dict[str, int]) -> str:
    cards = []
    for k, z in ZOMBIES.items():
        n = counts.get(k, 0)
        cls = "hit" if n else "none"
        note = escape(z.meaning) if n else "None found."
        cards.append(f'<div class="zh-card {cls}">{sprite(k, 56)}<h4>{escape(z.name)}</h4>'
                     f'<div class="n">{n}</div><small><b>{escape(z.short)}.</b> {note}</small></div>')
    return f'<div class="zh-cards">{"".join(cards)}</div>'


def page_hero_html(title: str, accent: str, text: str, sprite_keys: tuple[str, ...] = ()) -> str:
    """Hero banner for any app: '<title> <accent>' with a short plain-English line."""
    strip = "".join(sprite(k, 64) for k in sprite_keys)
    strip_html = f'<div class="zh-strip">{strip}</div>' if strip else ""
    return _flat(f"""
    <div class="zh-hero">
      <h1>{escape(title)} <span>{escape(accent)}</span></h1>
      <p>{escape(text)}</p>
      {strip_html}
    </div>
    """)


def issue_html(severity: str, title: str, why: str, fix: str) -> str:
    key = {"high": "giant", "medium": "mutant", "low": "ghost"}.get(severity, "mutant")
    return _flat(f"""
    <div class="zh-finding">{sprite(key, 44)}
      <div><p><span class="zh-badge {escape(severity)}">{escape(severity.upper())}</span><b>{escape(title)}</b></p>
      <p>{escape(why)}</p>
      <p class="meta"><b>How to fix:</b> {escape(fix)}</p></div>
    </div>
    """)


def finding_html(zombie_key: str, severity: str, plain: str, where: str) -> str:
    return _flat(f"""
    <div class="zh-finding">{sprite(zombie_key, 44)}
      <div><p><span class="zh-badge {escape(severity)}">{escape(severity.upper())}</span>{escape(plain)}</p>
      <p class="meta">{escape(ZOMBIES[zombie_key].name)} &middot; {escape(where)}</p></div>
    </div>
    """)
