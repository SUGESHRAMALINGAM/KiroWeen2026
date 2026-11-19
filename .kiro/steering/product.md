# Product

DE Bro Code: Zombie Edition is a data engineer's AI teammate for the Kiroween 2026 hackathon (Skeleton Crew category). One shared core powers two apps.

- **Zombie Hunter**: upload CSVs, profile them, report data problems as zombies, generate and run checks.
- **SQL Autopsy**: paste SQL, get an explanation, lint findings, lineage and ETL docs.

Zombie types: Ghost (nulls), Clone (duplicates), Mutant (type/format errors), Lost (orphaned foreign keys), Giant (outliers).
Infection score = rows with at least one issue / total rows. Overlay threshold defaults to 10%.

Principles:
- Deterministic engine first. The LLM only polishes wording and is always optional.
- Everything works with no LLM key.
- Zero running cost. Nothing the user uploads is stored.
- Plain Mode shows the same findings with no theming. Honour prefers-reduced-motion.
- Label inferred results ("suggested", "suggestion only") and only claim equality after a DuckDB comparison.

Source of truth: docs/DE_Bro_Code_BRD_and_Design.pdf and docs/DE_Bro_Code_Plan_Tracker.xlsx.
