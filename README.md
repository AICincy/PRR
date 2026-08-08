# Metro forensic ledger

This project maintains a SQLite-backed, review-gated ledger for the Metro public-records corpus. The SQLite ledger is the analytical system of record; CSV and Markdown outputs are regenerated views, not hand-edited conclusions.

## Evidence handling

Everything beneath `upload/` is immutable, read-only evidence. Intake hashes and inventories archive members without extracting or changing the uploads. Never write derived output into `upload/`; use `analysis/derivatives/` instead.

## Staged workflow

Initialize a ledger, then inventory the declared Level 1 evidence:

```bash
python3 -m metro_forensics.cli init --db analysis/metro_forensics.sqlite3
python3 -m metro_forensics.cli ingest --db analysis/metro_forensics.sqlite3 \
  --manifest config/corpus.json --evidence-root upload
```

Process only after intake is established. This creates provenance-tracked derivatives and review exceptions; automated processing does not create `VERIFIED` material findings.

```bash
python3 -m metro_forensics.cli process --db analysis/metro_forensics.sqlite3 \
  --evidence-root upload --derivative-root analysis/derivatives
```

Inspect completeness at any time. The verified-complete gate returns nonzero until every applicable review requirement is genuinely complete.

```bash
python3 -m metro_forensics.cli qc --db analysis/metro_forensics.sqlite3
python3 -m metro_forensics.cli qc --db analysis/metro_forensics.sqlite3 \
  --require-verified-complete
```

## Review and promotion

Human reviewers resolve review tasks with an auditable decision and a source-backed citation in the same package. A reviewer must be a registered human identity. Material findings begin as `PROVISIONAL`; a human may promote one only through the review service, with one or more valid evidence citations. Automation must not promote a material finding to `VERIFIED`.

The legacy `.doc` member is intentionally retained as an explicit `UNSUPPORTED_LEGACY_DOC` review exception in this environment. It is not pseudo-extracted or silently treated as text; a reviewer must resolve the exception using source-backed evidence.

## Regenerate reports

Reports are generated anew from SQLite, preserving the ledger itself:

```bash
python3 -m metro_forensics.cli report --db analysis/metro_forensics.sqlite3 \
  --output analysis/reports
```

The output includes deterministic CSV views and `summary.md`. Re-run this command after review activity instead of editing report files by hand.

## Acceptance verification

The current-corpus acceptance matrix evaluates all 18 approved invariants as direct SQLite queries over a fresh intake fixture, before any real processing run:

```bash
python3 -m unittest tests.test_current_corpus -v
```

It verifies provenance, package-scoped counts, review/verification gates, auditability, and report-total reconciliation without promoting findings or resolving review tasks.
