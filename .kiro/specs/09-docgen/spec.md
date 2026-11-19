# Task 8 (BRD): Doc generator, lean
Requirements: FR-19. FR-20 (optional LLM polish) is cut on purpose: the plan says cut it first, and every text is already template-based.

## Requirements
- Generate ETL documentation in Markdown with a Mermaid lineage diagram.
- Download it from SQL Autopsy; also generate it from the command line (used by the Kiro hook).

## Design
- `core/docgen.py`: `script_doc(ScriptAnalysis, title) -> str`, `mermaid(ScriptAnalysis) -> str`.
  - Sections: Summary, Data flow (Mermaid `flowchart LR`: source tables -> numbered step -> written table), Procedures, Syntax problems, one section per statement (SQL, plain steps, reads/writes, problems with fixes, column lineage table), Not analysed.
  - Mermaid labels are quoted and `"<>{}|` are replaced; Markdown table cells escape `|`.
- `scripts/gen_docs.py`: `py scripts/gen_docs.py file.sql [--dialect] [--out docs/generated]`. With no paths it reads a Kiro hook event (JSON on stdin) and documents any `.sql` path in it. Only reads `.sql` files, never runs SQL. `docs/generated/` is git-ignored.
- SQL Autopsy: "Download documentation (Markdown)" button under the summary.

## Tests
`tests/test_docgen.py`: sections for the MySQL procedure fixture, Mermaid edge count, awkward names, empty diagram, CLI writes a file, hook-event parsing, non-.sql refused. AppTest: download button present.
