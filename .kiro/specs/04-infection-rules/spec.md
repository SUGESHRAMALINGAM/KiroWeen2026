# Task 4: Infection rules + Zombie Hunter (App 1) (Phase B)
Requirements: FR-07, FR-08. Design ref: BRD 7.6, 7.9.

## Requirements
- Convert profile results into findings with type, severity, count and example rows.
- Infection score = rows with at least one issue / total rows.
- Zombie Hunter shows upload, score, findings and drill-down as plain tables.

## Design
- `core/rules.py`: `find_infections(connector, table, key_columns, links, profile) -> InfectionReport`. Row-level counts come from SQL (window functions and per-issue predicates), so each row counts once in the score.
- Zombie types: Ghost (nulls), Clone (duplicate rows/keys), Mutant (non-numeric text in numeric column, minority date format), Lost (orphan FK, needs a `Link`), Giant (IQR outliers).
- Severity by share of rows: >=5% high, >=1% medium, else low.
- CSV connector now reloads a column as text when values conflict with the sniffed type, so bad values become Mutant findings instead of rejected rows. Other parse errors stay in the rejected list.
- `apps/zombie_hunter/app.py`: sidebar upload / retail sample, table and key selection, FK links; main area score, zombie counters, findings table, examples, column drill-down. Uploads go through a temp file that is deleted after loading.

## Notes
- IQR is applied to every numeric column, including ID columns, so an out-of-range ID (999) is also flagged Giant. Revisit in a later task if it is noisy.
- FK link suggestion is Task 5 (FR-12). Retail sample ships with its links preset.

## Tasks
- [x] Findings model and score
- [x] Connector text fallback
- [x] Zombie Hunter plain-table UI
- [x] Tests (`tests/test_rules.py`, AppTest flow)

Done when: upload CSV shows infections and score.
