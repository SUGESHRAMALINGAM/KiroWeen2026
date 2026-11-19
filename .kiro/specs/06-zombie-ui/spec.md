# Task 6: Zombie UI kit (Phase B)
Requirements: FR-13, FR-14, NFR-05, NFR-06. Design ref: BRD 7.6, 7.9.

## Requirements
- Each infection type is a zombie with a counter. A full-screen overlay appears above the score threshold.
- Plain Mode toggle shows the same findings as simple tables. Honour prefers-reduced-motion.
- Usable by people who are not data engineers: plain-English wording, a guided flow, no jargon.

## Design
- `ui_kit/sprites.py`: inline SVG zombies (no hosted assets) plus a plain-English name and meaning for each type.
- `ui_kit/theme.css`: dark theme, Georgia headings, cards, severity badges, verdict ring. Animations exist only inside `prefers-reduced-motion: no-preference`.
- `ui_kit/overlay.py`: overlay built with HTML and CSS only. A checkbox label dismisses it. It fades on its own after 14 s and is hidden by CSS for reduced-motion users. The input and overlay sit inside one wrapper div, because Streamlit's markdown parser wraps a bare `<input>` in a `<p>`, which breaks the sibling selector.
- `ui_kit/components.py`: hero, steps, verdict ring, zombie cards, finding cards. All user text is HTML-escaped, since column names come from uploaded files.
- `.streamlit/config.toml`: dark base theme so tables match. Plain Mode removes only our CSS, pictures and overlay.
- `core/rules.py`: `Finding.plain` gives one readable sentence per finding.
- Zombie Hunter: sidebar (Plain Mode, alert level, upload, samples, table, keys, links), verdict banner, five zombie cards, tabs (What we found, Checks, Column details, Guide).
- The overlay shows once per table and settings, so it does not return on every click. A warning banner always shows above the alert level, including in Plain Mode and under reduced motion.
- A second demo dataset, `core/domain_packs/retail_outbreak/orders.csv`, is about 24% infected so the overlay can be shown on demand. The default table is the one with the most rows.

## Tests
- `tests/test_ui_kit.py`: sprites well-formed, HTML has no newlines, escaping, verdict states, CSS reduced-motion rules, contrast ratios at least 4.5:1, plain sentences.
- `tests/test_apps_smoke.py` (AppTest): landing, healthy sample (no overlay), outbreak (overlay once), Plain Mode, alert slider, run checks.
- Checked in a real browser (Edge via Playwright): overlay covers the sidebar and dismisses; screenshots in `docs/img/`.

## Tasks
- [x] Sprites, theme, overlay, components
- [x] Plain Mode and reduced motion
- [x] Zombie Hunter redesign and outbreak demo data
- [x] Tests and screenshots

Done when: bad data triggers the overlay; App 1 complete.