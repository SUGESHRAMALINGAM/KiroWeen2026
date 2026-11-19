"""Inline SVG zombies. No hosted assets. Keyed by Zombie value (ghost, clone, mutant, lost, giant)."""
from __future__ import annotations

from dataclasses import dataclass

_STROKE = "#1a2a1a"


def _svg(body: str, size: int = 64, label: str = "") -> str:
    aria = f'role="img" aria-label="{label}"' if label else 'aria-hidden="true"'
    return f'<svg class="zh-sprite" viewBox="0 0 64 64" width="{size}" height="{size}" {aria}>{body}</svg>'


def _eye(cx: float, cy: float, r: float = 4.2, pupil: float = 1.8) -> str:
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#f4f1de" stroke="{_STROKE}" stroke-width="1"/>'
            f'<circle cx="{cx + 0.8}" cy="{cy + 0.6}" r="{pupil}" fill="#1b1b1b"/>')


def _head(skin: str, shirt: str = "#3d5a36", eyes: str | None = None, extra: str = "") -> str:
    eyes = eyes if eyes is not None else _eye(25, 27) + _eye(39, 27, 3.4, 1.5)
    return (
        f'<path d="M8 64 Q8 46 32 46 Q56 46 56 64Z" fill="{shirt}" stroke="{_STROKE}" stroke-width="1.5"/>'
        f'<path d="M20 52 l4 5 M44 54 l-3 5" stroke="{_STROKE}" stroke-width="1.2"/>'
        f'<ellipse cx="32" cy="28" rx="16" ry="18" fill="{skin}" stroke="{_STROKE}" stroke-width="1.5"/>'
        f'<path d="M22 12 l4 6 M40 11 l-3 7" stroke="{_STROKE}" stroke-width="1.4" fill="none"/>'
        f"{eyes}"
        f'<path d="M23 38 Q32 45 41 38" fill="#2a0f12" stroke="{_STROKE}" stroke-width="1.4"/>'
        f'<path d="M27 40 v3 M32 41 v3 M37 40 v3" stroke="#f4f1de" stroke-width="1.6"/>'
        f"{extra}"
    )


def _ghost() -> str:
    return (
        '<path d="M13 58 V27 a19 19 0 0 1 38 0 V58 l-6.3 -5 -6.3 5 -6.4 -5 -6.3 5 -6.4 -5 -6.3 5Z" '
        f'fill="#dff3e6" fill-opacity="0.92" stroke="{_STROKE}" stroke-width="1.5"/>'
        '<ellipse cx="25" cy="28" rx="3.6" ry="5" fill="#16241a"/>'
        '<ellipse cx="39" cy="28" rx="3.6" ry="5" fill="#16241a"/>'
        '<ellipse cx="32" cy="40" rx="4" ry="5.5" fill="#16241a"/>'
        '<path d="M16 22 q-5 -3 -8 2 M48 22 q5 -3 8 2" stroke="#dff3e6" stroke-width="2" fill="none"/>'
    )


def _clone() -> str:
    back = (f'<g transform="translate(-9 4) scale(0.82)" opacity="0.75">'
            f'{_head("#6f9a58", "#34502f")}</g>')
    front = f'<g transform="translate(9 0) scale(0.9)">{_head("#8dc063")}</g>'
    return back + front


def _mutant() -> str:
    eyes = _eye(23, 29, 3.6, 1.6) + _eye(41, 29, 3.6, 1.6) + _eye(32, 18, 3.4, 1.5)
    spikes = ('<path d="M16 14 l-3 -9 l8 6 M32 8 l1 -8 l4 8 M48 14 l3 -9 l-8 6" '
              f'fill="#7a56c8" stroke="{_STROKE}" stroke-width="1.2"/>')
    return spikes + _head("#a58be0", "#4b3a7a", eyes)


def _lost() -> str:
    eyes = (f'<path d="M20 23 l8 8 M28 23 l-8 8" stroke="{_STROKE}" stroke-width="2.2"/>' + _eye(40, 27, 3.6, 1.6))
    q = ('<text x="49" y="16" font-family="Georgia, serif" font-size="20" font-weight="700" '
         'fill="#ffd166" stroke="#1a2a1a" stroke-width="0.6">?</text>')
    return f'<g transform="rotate(-8 32 40)">{_head("#7fb5a6", "#2f4f4a", eyes)}</g>' + q


def _giant() -> str:
    return (
        f'<path d="M0 64 Q0 44 32 44 Q64 44 64 64Z" fill="#44602f" stroke="{_STROKE}" stroke-width="1.5"/>'
        f'<path d="M10 14 Q32 -4 54 14 L58 36 Q56 58 32 60 Q8 58 6 36Z" fill="#6aa84f" '
        f'stroke="{_STROKE}" stroke-width="1.8"/>'
        f'<path d="M14 24 L28 28 M50 24 L36 28" stroke="{_STROKE}" stroke-width="3" stroke-linecap="round"/>'
        + _eye(23, 32, 4.6, 2.2) + _eye(41, 32, 4.6, 2.2)
        + f'<path d="M16 46 Q32 54 48 46" fill="#2a0f12" stroke="{_STROKE}" stroke-width="1.6"/>'
        '<path d="M20 47 l3 -8 l3 9 M44 47 l-3 -8 l-3 9" fill="#f4f1de" stroke="#1a2a1a" stroke-width="1"/>'
        f'<path d="M44 16 l6 8 M47 14 l5 7" stroke="#b23a48" stroke-width="2"/>'
    )


@dataclass(frozen=True)
class ZombieInfo:
    key: str
    name: str
    short: str       # plain-English name of the problem
    meaning: str     # one sentence for non-engineers
    color: str


ZOMBIES: dict[str, ZombieInfo] = {
    "ghost": ZombieInfo("ghost", "Ghost Zombie", "Blank values",
                        "Cells that are empty where a value should be.", "#cfe9d8"),
    "clone": ZombieInfo("clone", "Clone Zombie", "Copies",
                        "Rows that appear more than once, or repeat a value that should be unique.", "#8dc063"),
    "mutant": ZombieInfo("mutant", "Mutant Zombie", "Odd formats",
                         "Values written differently from the rest, like text in a number column or mixed date styles.",
                         "#b79cf0"),
    "lost": ZombieInfo("lost", "Lost Zombie", "Broken links",
                       "Rows that point to something missing, like an order for a customer that does not exist.",
                       "#7fb5a6"),
    "giant": ZombieInfo("giant", "Giant Zombie", "Extreme values",
                        "Numbers far bigger or smaller than the rest.", "#6aa84f"),
}

_BODIES = {"ghost": _ghost, "clone": _clone, "mutant": _mutant, "lost": _lost, "giant": _giant}


def sprite(key: str, size: int = 64) -> str:
    """Inline SVG for one zombie type."""
    return _svg(_BODIES[key](), size, label=ZOMBIES[key].name)
