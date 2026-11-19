"""Full-screen 'zombie attack' overlay. Pure HTML + CSS: dismiss with the button (a checkbox label),
auto-fades after 14 s, and is hidden entirely for prefers-reduced-motion users (theme.css)."""
from __future__ import annotations

from html import escape

from .sprites import ZOMBIES, sprite


def _flat(html: str) -> str:
    """Single line, so Streamlit's markdown parser keeps it as one HTML block."""
    return " ".join(line.strip() for line in html.splitlines() if line.strip())


def overlay_html(table: str, percent: float, infected_rows: int, total_rows: int,
                 counts: dict[str, int], uid: str = "zh-overlay-1") -> str:
    uid = escape(uid, quote=True)
    cards = "".join(
        f'<div class="z">{sprite(k, 56)}<b>{counts.get(k, 0)}</b>{escape(ZOMBIES[k].short)}</div>'
        for k in ZOMBIES if counts.get(k, 0)
    )
    return _flat(f"""
    <div class="zh-overlay-wrap"><input type="checkbox" id="{uid}" class="zh-overlay-toggle" aria-label="Dismiss zombie alert">
    <div class="zh-overlay" role="alertdialog" aria-live="assertive" aria-label="Zombie attack alert">
      <div>
        <h2>You are under zombie attack</h2>
        <p><b>{percent:g}%</b> of the rows in <b>{escape(table)}</b> are infected
        ({infected_rows:,} of {total_rows:,}).</p>
        <div class="row">{cards}</div>
        <label for="{uid}" tabindex="0">Dismiss and see the damage</label>
      </div>
    </div></div>
    """)
