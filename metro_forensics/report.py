"""Read-only, reproducible exports of canonical SQLite ledger views."""

import csv
import sqlite3
from pathlib import Path


REPORTS = {
    "package_inventory.csv": "v_package_inventory",
    "request_element_crosswalk.csv": "v_request_element_crosswalk",
    "level2_records.csv": "v_level2_records",
    "occurrences.csv": "v_occurrences",
    "referenced_not_located.csv": "v_referenced_not_located",
    "existence_conflicts.csv": "v_existence_conflicts",
    "withholding_redaction.csv": "v_withholding_redaction",
    "review_queue.csv": "v_review_queue",
    "audit_history.csv": "v_audit_history",
    "legal_assessments.csv": "v_legal_assessments",
}


_REPORT_ORDER_KEYS = {
    "v_package_inventory": ("package_id",),
    "v_request_element_crosswalk": ("package_id", "sort_order", "request_element_id"),
    "v_level2_records": ("record_id",),
    "v_occurrences": ("package_id", "source_file_id", "source_locator", "occurrence_id"),
    "v_referenced_not_located": ("package_id", "record_reference_id"),
    "v_existence_conflicts": ("package_id", "finding_id"),
    "v_withholding_redaction": ("package_id", "withholding_item_type", "withholding_item_id"),
    "v_review_queue": ("package_id", "task_state", "review_task_id"),
    "v_audit_history": ("changed_at", "event_id"),
    "v_legal_assessments": ("legal_assessment_id",),
}


def generate_reports(conn: sqlite3.Connection, output_dir: Path) -> list[Path]:
    """Regenerate CSV and Markdown reporting views without mutating the ledger."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for filename, view_name in REPORTS.items():
        path = output_dir / filename
        _write_view_csv(conn, view_name, path, _REPORT_ORDER_KEYS[view_name])
        paths.append(path)

    summary_path = output_dir / "summary.md"
    _write_summary(conn, summary_path)
    paths.append(summary_path)
    return paths


def _write_view_csv(
    conn: sqlite3.Connection, view_name: str, path: Path, order_keys: tuple[str, ...]
) -> None:
    query = f"SELECT * FROM {view_name} ORDER BY {', '.join(order_keys)}"
    cursor = conn.execute(query)
    fieldnames = [column[0] for column in cursor.description]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, lineterminator="\n", extrasaction="raise"
        )
        writer.writeheader()
        for row in cursor:
            writer.writerow(dict(zip(fieldnames, row)))


def _write_summary(conn: sqlite3.Connection, path: Path) -> None:
    counts = conn.execute("SELECT * FROM v_summary_counts").fetchone()
    columns = [column[0] for column in conn.execute("SELECT * FROM v_summary_counts").description]
    values = dict(zip(columns, counts))
    package_rows = conn.execute(
        "SELECT * FROM v_package_inventory ORDER BY package_id"
    ).fetchall()
    package_columns = [
        column[0]
        for column in conn.execute("SELECT * FROM v_package_inventory LIMIT 0").description
    ]
    corpus_rows = conn.execute(
        "SELECT * FROM v_corpus_summary_counts ORDER BY corpus_id"
    ).fetchall()
    corpus_columns = [
        column[0]
        for column in conn.execute("SELECT * FROM v_corpus_summary_counts LIMIT 0").description
    ]

    lines = [
        "# Metro forensic ledger summary",
        "",
        "## Ledger-wide totals",
        "",
        "Scope: all packages currently present in this SQLite ledger. This is not a corpus-completeness assertion.",
        "",
        f"- Packages (package unit; ledger scope): {values['package_count']}",
        f"- Level 1 source files (file unit; ledger scope): {values['level1_source_files']}",
        f"- Unique Level 2 records (record unit; ledger scope): {values['unique_level2_records']}",
        f"- Level 2 occurrences (occurrence unit; ledger scope): {values['level2_occurrences']}",
        f"- Record references (reference unit; ledger scope): {values['record_references']}",
        f"- Referenced-but-not-located items (reference unit; ledger scope): {values['referenced_not_located_items']}",
        f"- Provisional findings (finding unit; ledger scope): {values['provisional_findings']}",
        f"- Verified findings (finding unit; ledger scope): {values['verified_findings']}",
        f"- Open review tasks (review-task unit; ledger scope): {values['open_review_tasks']}",
        f"- Unresolved review tasks (review-task unit; ledger scope): {values['unresolved_review_tasks']}",
        "",
        "## Package scope",
        "",
        "| Package ID | Expected Level 1 files | Actual Level 1 source files | Package completeness state |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in package_rows:
        package = dict(zip(package_columns, row))
        lines.append(
            "| {package_id} | {expected_level1_count} | {level1_source_file_count} | "
            "{package_completeness_state} |".format(**package)
        )
    lines.extend(
        [
            "",
            "## Corpus scope",
            "",
            "Scope: each row aggregates only the declared corpus-package membership; it does not alter package-specific production facts.",
            "",
            "| Corpus ID | Level 1 source files | Unique Level 2 records | Level 2 occurrences | Record references | Referenced-but-not-located items | Provisional findings | Verified findings | Open review tasks | Unresolved review tasks | Corpus completeness state |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in corpus_rows:
        corpus = dict(zip(corpus_columns, row))
        lines.append(
            "| {corpus_id} | {level1_source_files} | {unique_level2_records} | "
            "{level2_occurrences} | {record_references} | {referenced_not_located_items} | "
            "{provisional_findings} | {verified_findings} | {open_review_tasks} | "
            "{unresolved_review_tasks} | {corpus_completeness_state} |".format(**corpus)
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
