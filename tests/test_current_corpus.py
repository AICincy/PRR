import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import zipfile

from docx import Document

import metro_forensics.cli as cli_module
from metro_forensics.cli import main
from metro_forensics.db import connect, initialize
from metro_forensics.evidence import (
    add_citation,
    add_finding,
    add_metro_statement,
    add_record_reference,
    add_request_element,
    assign_reference_search_corpus,
    link_request_evidence,
    set_reference_absence_scope,
    set_reference_match,
)
from metro_forensics.extract import process_source, record_processing_result
from metro_forensics.ingest import ingest_manifest
from metro_forensics.records import create_occurrence, create_record, link_version_family
from metro_forensics.report import generate_reports
from metro_forensics.review import (
    add_corpus,
    add_corpus_package,
    change_with_audit,
    open_review_task,
    promote_finding_verified,
    promote_occurrence_verified,
    register_reviewer_identity,
    set_corpus_completeness,
    set_package_completeness,
)
from metro_forensics.temporal_legal import (
    add_date_fact,
    add_temporal_inference,
    create_legal_assessment,
    finalize_legal_assessment,
)


ROOT = Path(__file__).resolve().parents[1]


class CurrentCorpusTests(unittest.TestCase):
    def test_acceptance_matrix(self):
        """Run every approved invariant over a populated synthetic forensic ledger."""
        upload_hashes = _upload_hashes()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db, evidence_root, evidence_hashes = _seed_acceptance_fixture(root)
            verified_absence_db = _seed_verified_absence_fixture(root / "verified-absence")
            reports = root / "reports"
            generate_reports(db, reports)

            assertions = {
                # Required invariants 1--18, in specification order.
                "1 originals remain immutable": _originals_unchanged(
                    db, evidence_root, evidence_hashes
                ),
                "2 derivatives have source and successful run": _all_derivatives_have_source_and_run(db),
                "3 occurrences have exact source locator": _all_occurrences_have_exact_source_locator(db),
                "4 controls do not inflate Level 1 counts": _control_records_do_not_inflate_level1_counts(db),
                "5 canonical records retain every occurrence": _canonical_records_preserve_occurrences(db),
                "6 version links keep different versions distinct": _material_versions_are_not_deduplicated(db),
                "7 findings retain cumulative classifications": _findings_are_cumulative_and_audited(db),
                "8 Metro statements remain distinct": _metro_statements_are_separate_from_findings(db),
                "9 references are not production until located": _references_are_not_produced_until_located(db),
                "10 probable matches do not close references": _no_probable_match_closes_reference(db),
                "11 cross-package records do not recredit production": _no_cross_package_match_recredits_original_package(db),
                "12 existence does not imply possession": _no_existence_inference_implies_possession_without_support(db),
                "13 automation creates no verified material finding": _no_automated_material_finding_is_verified(db),
                "14 processing ambiguity opens review": _all_processing_ambiguities_have_review_tasks(db),
                "15 corpus absence requires verified completeness": _no_corpus_absence_precedes_completeness(verified_absence_db),
                "16 final legal conclusions exclude provisional facts": _no_final_legal_assessment_uses_provisional_findings(db),
                "17 substantive changes retain audit history": _substantive_changes_have_append_only_audit_history(db),
                "18 published totals reconcile to SQLite": _all_report_totals_reconcile(db, reports / "summary.md"),
            }
            for invariant, satisfied in assertions.items():
                with self.subTest(invariant=invariant):
                    self.assertTrue(satisfied)

            self.assertEqual(evidence_hashes, _tree_hashes(evidence_root))

        self.assertEqual(upload_hashes, _upload_hashes())

    def test_acceptance_violation_queries_detect_targeted_breaks(self):
        """Fail if the strengthened 5/6/12/14/17/18 checks become vacuous."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db, _, _ = _seed_acceptance_fixture(root)
            reports = root / "reports"
            generate_reports(db, reports)

            duplicate = db.execute(
                """
                SELECT o.occurrence_id FROM occurrence o
                WHERE (SELECT count(*) FROM occurrence duplicate
                       WHERE duplicate.record_id=o.record_id) >= 2
                  AND NOT EXISTS (
                      SELECT 1 FROM evidence_citation ec WHERE ec.occurrence_id=o.occurrence_id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM record_reference rr WHERE rr.occurrence_id=o.occurrence_id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM request_element_evidence ree
                      WHERE ree.occurrence_id=o.occurrence_id
                  )
                ORDER BY o.occurrence_id LIMIT 1
                """
            ).fetchone()[0]
            with db:
                db.execute("SAVEPOINT invariant_5")
                db.execute("DELETE FROM occurrence WHERE occurrence_id=?", (duplicate,))
                self.assertFalse(_canonical_records_preserve_occurrences(db))
                db.execute("ROLLBACK TO invariant_5")
                db.execute("RELEASE invariant_5")

                db.execute("SAVEPOINT invariant_6")
                db.execute(
                    "UPDATE record_version_link SET relationship_description='RELATED'"
                )
                self.assertFalse(_material_versions_are_not_deduplicated(db))
                db.execute("ROLLBACK TO invariant_6")
                db.execute("RELEASE invariant_6")

                source_id = db.execute(
                    """
                    SELECT source_file_id FROM processing_run
                    WHERE errors='UNSUPPORTED_LEGACY_DOC'
                    LIMIT 1
                    """
                ).fetchone()[0]
                db.execute("SAVEPOINT invariant_14")
                db.execute(
                    """
                    INSERT INTO processing_run(
                        processing_run_id, source_file_id, operation, tool_name,
                        started_at, completed_at, errors
                    ) VALUES('PR_VIOLATION', ?, 'TEST', 'acceptance-test',
                             '2026-08-07T00:00:00Z', '2026-08-07T00:00:01Z', 'UNREVIEWED')
                    """,
                    (source_id,),
                )
                self.assertFalse(_all_processing_ambiguities_have_review_tasks(db))
                db.execute("ROLLBACK TO invariant_14")
                db.execute("RELEASE invariant_14")

                db.execute("SAVEPOINT invariant_14_incomplete")
                db.execute(
                    """
                    INSERT INTO processing_run(
                        processing_run_id, source_file_id, operation, tool_name, started_at
                    ) VALUES('PR_UNREVIEWED_INCOMPLETE', ?, 'TEST',
                             'acceptance-test', '2026-08-07T00:00:02Z')
                    """,
                    (source_id,),
                )
                self.assertFalse(_all_processing_ambiguities_have_review_tasks(db))
                db.execute("ROLLBACK TO invariant_14_incomplete")
                db.execute("RELEASE invariant_14_incomplete")

                db.execute("SAVEPOINT invariant_14_prefix")
                db.execute(
                    """
                    INSERT INTO processing_run(
                        processing_run_id, source_file_id, operation, tool_name, started_at
                    ) VALUES('PR_ACCEPTANCE', ?, 'TEST',
                             'acceptance-test', '2026-08-07T00:00:03Z')
                    """,
                    (db.execute(
                        "SELECT source_file_id FROM processing_run "
                        "WHERE processing_run_id='PR_ACCEPTANCE_INCOMPLETE'"
                    ).fetchone()[0],),
                )
                self.assertFalse(_all_processing_ambiguities_have_review_tasks(db))
                db.execute("ROLLBACK TO invariant_14_prefix")
                db.execute("RELEASE invariant_14_prefix")

                db.execute("SAVEPOINT invariant_14_sqlite")
                sqlite_failure_source = db.execute(
                    "SELECT source_file_id FROM source_file ORDER BY source_file_id LIMIT 1"
                ).fetchone()[0]
                sqlite_failure = "PROCESSING_SQLITE_FAILURE: synthetic constraint"
                db.execute(
                    """
                    INSERT INTO processing_run(
                        processing_run_id, source_file_id, operation, tool_name,
                        started_at, completed_at, errors
                    ) VALUES('PR_REVIEWED_SQLITE_FAILURE', ?, 'TEST', 'acceptance-test',
                             '2026-08-07T00:00:04Z', '2026-08-07T00:00:05Z', ?)
                    """,
                    (sqlite_failure_source, sqlite_failure),
                )
                open_review_task(
                    db,
                    "SOURCE_FILE",
                    sqlite_failure_source,
                    "PROCESSING_SQLITE_FAILURE",
                    sqlite_failure,
                    task_type="EXTRACTION_EXCEPTION",
                )
                self.assertTrue(_all_processing_ambiguities_have_review_tasks(db))
                db.execute("ROLLBACK TO invariant_14_sqlite")
                db.execute("RELEASE invariant_14_sqlite")

                finding_id = db.execute(
                    """
                    SELECT entity_id FROM audit_event
                    WHERE entity_type='FINDING' AND field_name='proposition'
                    ORDER BY entity_id LIMIT 1
                    """
                ).fetchone()[0]
                current_proposition = db.execute(
                    "SELECT proposition FROM finding WHERE finding_id=?", (finding_id,)
                ).fetchone()[0]
                db.execute("SAVEPOINT invariant_17_disconnected")
                db.execute(
                    """
                    INSERT INTO audit_event(
                        event_id, entity_type, entity_id, field_name, changed_at,
                        reason, change_source, previous_value, new_value
                    ) VALUES('AE_DISCONNECTED', 'FINDING', ?, 'proposition',
                             '9999-01-01T00:00:00Z', 'bad audit', 'reviewer-1',
                             'DISCONNECTED', ?)
                    """,
                    (finding_id, current_proposition),
                )
                self.assertFalse(_substantive_changes_have_append_only_audit_history(db))
                db.execute("ROLLBACK TO invariant_17_disconnected")
                db.execute("RELEASE invariant_17_disconnected")

                db.execute("SAVEPOINT invariant_17_current")
                db.execute(
                    """
                    INSERT INTO audit_event(
                        event_id, entity_type, entity_id, field_name, changed_at,
                        reason, change_source, previous_value, new_value
                    ) VALUES('AE_NOT_APPLIED', 'FINDING', ?, 'proposition',
                             '9999-01-01T00:00:01Z', 'bad audit', 'reviewer-1',
                             ?, 'NOT_APPLIED_TO_CURRENT_ROW')
                    """,
                    (finding_id, current_proposition),
                )
                self.assertFalse(_substantive_changes_have_append_only_audit_history(db))
                db.execute("ROLLBACK TO invariant_17_current")
                db.execute("RELEASE invariant_17_current")

            date_fact_id = db.execute(
                "SELECT date_fact_id FROM date_fact ORDER BY date_fact_id LIMIT 1"
            ).fetchone()[0]
            with self.assertRaisesRegex(ValueError, "possession"):
                add_temporal_inference(
                    db,
                    "RECORD",
                    db.execute(
                        "SELECT entity_id FROM date_fact WHERE date_fact_id=?",
                        (date_fact_id,),
                    ).fetchone()[0],
                    "POSSESSED_AT_RESPONSE",
                    [date_fact_id],
                )
            self.assertTrue(_no_existence_inference_implies_possession_without_support(db))

            summary = reports / "summary.md"
            valid_summary = summary.read_text(encoding="utf-8")
            summary.write_text(valid_summary.replace("Packages (package unit; ledger scope): 2", "Packages (package unit; ledger scope): 999"), encoding="utf-8")
            self.assertFalse(_all_report_totals_reconcile(db, summary))

            package_row = dict(db.execute(
                "SELECT * FROM v_package_inventory WHERE package_id='P1'"
            ).fetchone())
            rendered_package = (
                "| {package_id} | {expected_level1_count} | {level1_source_file_count} | "
                "{package_completeness_state} |".format(**package_row)
            )
            summary.write_text(
                valid_summary.replace(rendered_package, rendered_package.replace("| P1 |", "| BROKEN |")),
                encoding="utf-8",
            )
            self.assertFalse(_all_report_totals_reconcile(db, summary))

            corpus_row = dict(db.execute(
                "SELECT * FROM v_corpus_summary_counts WHERE corpus_id='C1'"
            ).fetchone())
            rendered_corpus = (
                "| {corpus_id} | {level1_source_files} | {unique_level2_records} | "
                "{level2_occurrences} | {record_references} | {referenced_not_located_items} | "
                "{provisional_findings} | {verified_findings} | {open_review_tasks} | "
                "{unresolved_review_tasks} | {corpus_completeness_state} |".format(**corpus_row)
            )
            summary.write_text(
                valid_summary.replace(rendered_corpus, rendered_corpus.replace("| C1 |", "| BROKEN |")),
                encoding="utf-8",
            )
            self.assertFalse(_all_report_totals_reconcile(db, summary))

    def test_locked_level1_counts(self):
        """Fail if the controlled archive inventory differs from the fixed manifest."""
        with tempfile.TemporaryDirectory() as td:
            db = connect(Path(td) / "ledger.sqlite3")
            initialize(db)

            ingest_manifest(db, ROOT / "config/corpus.json", ROOT / "upload")

            rows = dict(db.execute(
                "SELECT package_id, count(source_file_id) FROM package "
                "LEFT JOIN source_file USING(package_id) GROUP BY package_id"
            ))
            self.assertEqual({"PKG_1": 24, "PKG_2": 72, "PKG_3": 0}, rows)
            status = db.execute(
                "SELECT package_status FROM package WHERE package_id='PKG_3'"
            ).fetchone()[0]
            self.assertEqual("NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED", status)

    def test_cli_intake_preserves_locked_uploads_and_remains_in_progress(self):
        """Fail if the CLI alters uploads, mis-inventories the corpus, or marks intake complete."""
        before_hashes = _upload_hashes()
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "analysis" / "ledger.sqlite3"
            self.assertEqual(0, main(["init", "--db", str(db_path)]))
            self.assertEqual(0, main([
                "ingest", "--db", str(db_path), "--manifest", str(ROOT / "config/corpus.json"),
                "--evidence-root", str(ROOT / "upload"),
            ]))

            db = connect(db_path)
            package_counts = dict(db.execute(
                "SELECT package_id, count(source_file_id) FROM package "
                "LEFT JOIN source_file USING(package_id) GROUP BY package_id"
            ))
            pkg3_status = db.execute(
                "SELECT package_status FROM package WHERE package_id='PKG_3'"
            ).fetchone()[0]
            self.assertEqual({"PKG_1": 24, "PKG_2": 72, "PKG_3": 0}, package_counts)
            self.assertEqual("NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED", pkg3_status)

            qc_output = io.StringIO()
            with contextlib.redirect_stdout(qc_output):
                self.assertEqual(0, main(["qc", "--db", str(db_path)]))
                self.assertEqual(1, main([
                    "qc", "--db", str(db_path), "--require-verified-complete",
                ]))
            self.assertIn("PKG_1 Level1=24", qc_output.getvalue())
            self.assertIn(
                "PKG_3 Level1=0 status=NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED",
                qc_output.getvalue(),
            )
            self.assertIn("Corpus completeness: IN_PROGRESS", qc_output.getvalue())
            self.assertNotIn("VERIFIED_COMPLETE", qc_output.getvalue())

        self.assertEqual(before_hashes, _upload_hashes())

    def test_cli_rejects_level1_count_mismatch(self):
        """Fail if the orchestration accepts an incomplete production archive as its manifest count."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            (evidence_root / "control.pdf").write_bytes(b"control")
            with zipfile.ZipFile(evidence_root / "production.zip", "w") as archive:
                archive.writestr("only-member.txt", "one")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"packages": [{
                "package_id": "P1", "control_record": "control.pdf",
                "production_archive": "production.zip", "expected_level1_count": 2,
                "package_status": None,
            }]}), encoding="utf-8")
            db_path = root / "ledger.sqlite3"
            self.assertEqual(0, main(["init", "--db", str(db_path)]))
            self.assertEqual(1, main([
                "ingest", "--db", str(db_path), "--manifest", str(manifest),
                "--evidence-root", str(evidence_root),
            ]))

    def test_cli_processes_only_synthetic_evidence_and_regenerates_reports(self):
        """Fail if CLI processing skips provenance/review exceptions or report regeneration."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            (evidence_root / "control.pdf").write_bytes(b"control")
            with zipfile.ZipFile(evidence_root / "production.zip", "w") as archive:
                archive.writestr("legacy.doc", b"legacy binary")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"packages": [{
                "package_id": "P1", "control_record": "control.pdf",
                "production_archive": "production.zip", "expected_level1_count": 1,
                "package_status": None,
            }]}), encoding="utf-8")
            db_path = root / "ledger.sqlite3"
            reports = root / "reports"
            self.assertEqual(0, main(["init", "--db", str(db_path)]))
            self.assertEqual(0, main([
                "ingest", "--db", str(db_path), "--manifest", str(manifest),
                "--evidence-root", str(evidence_root),
            ]))
            self.assertEqual(0, main([
                "process", "--db", str(db_path), "--evidence-root", str(evidence_root),
                "--derivative-root", str(root / "derivatives"),
            ]))
            self.assertEqual(0, main(["report", "--db", str(db_path), "--output", str(reports)]))

            db = connect(db_path)
            self.assertEqual(1, db.execute("SELECT count(*) FROM processing_run").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM review_task").fetchone()[0])
            self.assertEqual(0, db.execute(
                "SELECT count(*) FROM finding WHERE verification_state='VERIFIED'"
            ).fetchone()[0])
            self.assertTrue((reports / "summary.md").is_file())

    def test_module_entrypoint_does_not_preimport_cli(self):
        """Fail if package initialization makes `python -m` emit a runpy warning."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "ledger.sqlite3"
            self.assertEqual(0, main(["init", "--db", str(db_path)]))
            result = subprocess.run(
                [sys.executable, "-m", "metro_forensics.cli", "qc", "--db", str(db_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)

    def test_cli_rejects_all_process_output_overlaps_before_writing(self):
        """Fail if process can create derivatives in, around, or through a link to evidence."""
        for target in ("upload", "upload/derivatives", ".", "evidence-alias"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                evidence_root, db_path = _seed_synthetic_docx_ledger(root)
                if target == "evidence-alias":
                    (root / target).symlink_to(evidence_root, target_is_directory=True)
                before = _tree_hashes(evidence_root)
                with _working_directory(root):
                    result = main([
                        "process", "--db", str(db_path), "--evidence-root", "upload",
                        "--derivative-root", target,
                    ])
                db = connect(db_path)
                self.assertEqual(1, result)
                self.assertEqual(before, _tree_hashes(evidence_root))
                self.assertEqual(0, db.execute("SELECT count(*) FROM processing_run").fetchone()[0])

    def test_cli_rejects_all_report_output_overlaps_before_writing(self):
        """Fail if report generation can write into, around, or through a link to evidence."""
        for target in ("upload", "upload/reports", ".", "evidence-alias"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                evidence_root, db_path = _seed_synthetic_docx_ledger(root)
                if target == "evidence-alias":
                    (root / target).symlink_to(evidence_root, target_is_directory=True)
                before = _tree_hashes(evidence_root)
                with _working_directory(root):
                    result = main(["report", "--db", str(db_path), "--output", target])
                self.assertEqual(1, result)
                self.assertEqual(before, _tree_hashes(evidence_root))

    def test_cli_process_returns_nonzero_after_recorded_source_hash_failure(self):
        """Fail if a non-legacy processing failure is recorded but reported as CLI success."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root, db_path = _seed_synthetic_docx_ledger(root)
            source_file_id = connect(db_path).execute(
                "SELECT source_file_id FROM source_file"
            ).fetchone()[0]
            with zipfile.ZipFile(evidence_root / "production.zip", "w") as archive:
                archive.writestr("record.docx", b"tampered after intake")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main([
                    "process", "--db", str(db_path), "--evidence-root", str(evidence_root),
                    "--derivative-root", str(root / "derivatives"),
                ])
            db = connect(db_path)
            self.assertEqual(1, result)
            self.assertIn(source_file_id, stderr.getvalue())
            self.assertEqual("SOURCE_HASH_MISMATCH", db.execute(
                "SELECT errors FROM processing_run WHERE source_file_id=?",
                (source_file_id,),
            ).fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM review_task").fetchone()[0])

    def test_cli_isolates_one_unusable_sqlite_connection_and_continues_batch(self):
        """Fail if one per-source connection can strand its run or abort later sources."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root, db_path, source_ids = _seed_synthetic_batch_ledger(root)
            _insert_historical_incomplete_run(db_path, source_ids["02-fails.docx"])
            real_factory = cli_module._existing_ledger
            connection_number = 0

            def connection_factory(path):
                nonlocal connection_number
                connection_number += 1
                connection = real_factory(path)
                if connection_number == 3:
                    return _QueryOnlyOnProcessingFinish(connection)
                return connection

            stderr = io.StringIO()
            with _replace_connection_factory(connection_factory), contextlib.redirect_stderr(stderr):
                result = main([
                    "process",
                    "--db",
                    str(db_path),
                    "--evidence-root",
                    str(evidence_root),
                    "--derivative-root",
                    str(root / "derivatives"),
                ])

            db = connect(db_path)
            self.assertEqual(1, result)
            self.assertEqual(5, db.execute("SELECT count(*) FROM processing_run").fetchone()[0])
            self.assertEqual(0, db.execute(
                """
                SELECT count(*) FROM processing_run
                WHERE completed_at IS NULL
                  AND processing_run_id <> 'PR_HISTORICAL_INCOMPLETE'
                """
            ).fetchone()[0])
            self.assertEqual(
                (None, "", "2026-08-01T00:00:00Z"),
                tuple(db.execute(
                    """
                    SELECT completed_at, errors, started_at FROM processing_run
                    WHERE processing_run_id='PR_HISTORICAL_INCOMPLETE'
                    """
                ).fetchone()),
            )

            failed_run = db.execute(
                """
                SELECT errors FROM processing_run
                WHERE source_file_id=?
                  AND processing_run_id <> 'PR_HISTORICAL_INCOMPLETE'
                """,
                (source_ids["02-fails.docx"],),
            ).fetchone()
            self.assertIsNotNone(failed_run)
            self.assertTrue(failed_run["errors"].startswith("PROCESSING_CONNECTION_FAILURE:"))
            self.assertEqual(1, db.execute(
                """
                SELECT count(*) FROM review_task
                WHERE source_file_id=? AND task_state='OPEN'
                  AND reason_code='PROCESSING_CONNECTION_FAILURE'
                """,
                (source_ids["02-fails.docx"],),
            ).fetchone()[0])

            self.assertEqual(1, db.execute(
                "SELECT count(*) FROM derivative WHERE source_file_id=?",
                (source_ids["01-first.docx"],),
            ).fetchone()[0])
            self.assertEqual(0, db.execute(
                "SELECT count(*) FROM derivative WHERE source_file_id=?",
                (source_ids["02-fails.docx"],),
            ).fetchone()[0])
            self.assertFalse(
                (root / "derivatives" / source_ids["02-fails.docx"]).exists()
            )
            self.assertEqual(1, db.execute(
                "SELECT count(*) FROM derivative WHERE source_file_id=?",
                (source_ids["03-later.docx"],),
            ).fetchone()[0])
            self.assertEqual("", db.execute(
                "SELECT errors FROM processing_run WHERE source_file_id=?",
                (source_ids["03-later.docx"],),
            ).fetchone()[0])
            self.assertEqual("UNSUPPORTED_LEGACY_DOC", db.execute(
                "SELECT errors FROM processing_run WHERE source_file_id=?",
                (source_ids["04-legacy.doc"],),
            ).fetchone()[0])
            self.assertIn(source_ids["02-fails.docx"], stderr.getvalue())
            self.assertNotIn(source_ids["04-legacy.doc"], stderr.getvalue())

    def test_cli_start_failure_never_rewrites_historical_incomplete_run(self):
        """Fail if a readonly INSERT failure claims a pre-existing run as the current attempt."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root, db_path, source_ids = _seed_synthetic_batch_ledger(root)
            failed_source_id = source_ids["02-fails.docx"]
            _insert_historical_incomplete_run(db_path, failed_source_id)
            real_factory = cli_module._existing_ledger
            connection_number = 0

            def connection_factory(path):
                nonlocal connection_number
                connection_number += 1
                connection = real_factory(path)
                if connection_number == 3:
                    connection.execute("PRAGMA query_only=ON")
                return connection

            stderr = io.StringIO()
            with _replace_connection_factory(connection_factory), contextlib.redirect_stderr(stderr):
                result = main([
                    "process", "--db", str(db_path),
                    "--evidence-root", str(evidence_root),
                    "--derivative-root", str(root / "derivatives"),
                ])

            db = connect(db_path)
            self.assertEqual(1, result)
            self.assertEqual(
                (None, "", "", "2026-08-01T00:00:00Z"),
                tuple(db.execute(
                    """
                    SELECT completed_at, warnings, errors, started_at
                    FROM processing_run
                    WHERE processing_run_id='PR_HISTORICAL_INCOMPLETE'
                    """
                ).fetchone()),
            )
            self.assertEqual(1, db.execute(
                "SELECT count(*) FROM processing_run WHERE source_file_id=?",
                (failed_source_id,),
            ).fetchone()[0])
            self.assertEqual(0, db.execute(
                """
                SELECT count(*) FROM processing_run
                WHERE completed_at IS NULL
                  AND processing_run_id <> 'PR_HISTORICAL_INCOMPLETE'
                """
            ).fetchone()[0])
            self.assertEqual(1, db.execute(
                """
                SELECT count(*) FROM review_task
                WHERE source_file_id=? AND task_state='OPEN'
                  AND reason_code='PROCESSING_CONNECTION_FAILURE'
                """,
                (failed_source_id,),
            ).fetchone()[0])
            self.assertEqual(0, db.execute(
                "SELECT count(*) FROM derivative WHERE source_file_id=?",
                (failed_source_id,),
            ).fetchone()[0])
            self.assertFalse((root / "derivatives" / failed_source_id).exists())
            self.assertEqual(1, db.execute(
                "SELECT count(*) FROM derivative WHERE source_file_id=?",
                (source_ids["03-later.docx"],),
            ).fetchone()[0])
            self.assertIn(failed_source_id, stderr.getvalue())

    def test_cli_connection_open_failure_isolated_from_later_sources(self):
        """Fail if one CANTOPEN error escapes the per-source boundary and aborts the batch."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root, db_path, source_ids = _seed_synthetic_batch_ledger(root)
            real_factory = cli_module._existing_ledger
            connection_number = 0

            def connection_factory(path):
                nonlocal connection_number
                connection_number += 1
                if connection_number == 3:
                    return sqlite3.connect(root / "missing-parent" / "cannot-open.sqlite3")
                return real_factory(path)

            stderr = io.StringIO()
            with _replace_connection_factory(connection_factory), contextlib.redirect_stderr(stderr):
                result = main([
                    "process", "--db", str(db_path),
                    "--evidence-root", str(evidence_root),
                    "--derivative-root", str(root / "derivatives"),
                ])

            db = connect(db_path)
            failed_source_id = source_ids["02-fails.docx"]
            self.assertEqual(1, result)
            self.assertEqual(0, db.execute(
                "SELECT count(*) FROM processing_run WHERE source_file_id=?",
                (failed_source_id,),
            ).fetchone()[0])
            self.assertEqual(1, db.execute(
                """
                SELECT count(*) FROM review_task
                WHERE source_file_id=? AND task_state='OPEN'
                  AND reason_code='PROCESSING_CONNECTION_FAILURE'
                """,
                (failed_source_id,),
            ).fetchone()[0])
            self.assertEqual(1, db.execute(
                "SELECT count(*) FROM derivative WHERE source_file_id=?",
                (source_ids["03-later.docx"],),
            ).fetchone()[0])
            self.assertIn(failed_source_id, stderr.getvalue())

    def test_cli_recovery_open_failure_isolated_from_later_sources(self):
        """Fail if a second transient CANTOPEN during recovery aborts the remaining batch."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root, db_path, source_ids = _seed_synthetic_batch_ledger(root)
            real_factory = cli_module._existing_ledger
            connection_number = 0

            def connection_factory(path):
                nonlocal connection_number
                connection_number += 1
                if connection_number in {3, 4}:
                    return sqlite3.connect(root / "missing-parent" / "cannot-open.sqlite3")
                return real_factory(path)

            stderr = io.StringIO()
            with _replace_connection_factory(connection_factory), contextlib.redirect_stderr(stderr):
                result = main([
                    "process", "--db", str(db_path),
                    "--evidence-root", str(evidence_root),
                    "--derivative-root", str(root / "derivatives"),
                ])

            db = connect(db_path)
            failed_source_id = source_ids["02-fails.docx"]
            self.assertEqual(1, result)
            self.assertEqual(0, db.execute(
                "SELECT count(*) FROM processing_run WHERE source_file_id=?",
                (failed_source_id,),
            ).fetchone()[0])
            self.assertEqual(1, db.execute(
                "SELECT count(*) FROM derivative WHERE source_file_id=?",
                (source_ids["03-later.docx"],),
            ).fetchone()[0])
            self.assertEqual(1, db.execute(
                """
                SELECT count(*) FROM review_task
                WHERE source_file_id=? AND task_state='OPEN'
                  AND reason_code='PROCESSING_CONNECTION_FAILURE'
                """,
                (failed_source_id,),
            ).fetchone()[0])
            self.assertIn(failed_source_id, stderr.getvalue())

    def test_cli_defers_recovery_after_started_run_and_transient_cantopen(self):
        """Fail if exhausted immediate recovery is not retried after later DB availability."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root, db_path, source_ids = _seed_synthetic_batch_ledger(root)
            real_factory = cli_module._existing_ledger
            connection_number = 0

            def connection_factory(path):
                nonlocal connection_number
                connection_number += 1
                if connection_number == 3:
                    return _QueryOnlyOnProcessingFinish(real_factory(path))
                if connection_number in {4, 5}:
                    return sqlite3.connect(root / "missing-parent" / "cannot-open.sqlite3")
                return real_factory(path)

            stderr = io.StringIO()
            with _replace_connection_factory(connection_factory), contextlib.redirect_stderr(stderr):
                result = main([
                    "process", "--db", str(db_path),
                    "--evidence-root", str(evidence_root),
                    "--derivative-root", str(root / "derivatives"),
                ])

            db = connect(db_path)
            failed_source_id = source_ids["02-fails.docx"]
            self.assertEqual(1, result)
            self.assertEqual(0, db.execute(
                "SELECT count(*) FROM processing_run WHERE completed_at IS NULL"
            ).fetchone()[0])
            self.assertEqual(1, db.execute(
                """
                SELECT count(*) FROM processing_run
                WHERE source_file_id=?
                  AND errors LIKE 'PROCESSING_CONNECTION_FAILURE:%'
                """,
                (failed_source_id,),
            ).fetchone()[0])
            self.assertEqual(1, db.execute(
                """
                SELECT count(*) FROM review_task
                WHERE source_file_id=? AND task_state='OPEN'
                  AND reason_code='PROCESSING_CONNECTION_FAILURE'
                """,
                (failed_source_id,),
            ).fetchone()[0])
            self.assertEqual(0, db.execute(
                "SELECT count(*) FROM derivative WHERE source_file_id=?",
                (failed_source_id,),
            ).fetchone()[0])
            self.assertFalse((root / "derivatives" / failed_source_id).exists())
            self.assertEqual(1, db.execute(
                "SELECT count(*) FROM derivative WHERE source_file_id=?",
                (source_ids["03-later.docx"],),
            ).fetchone()[0])

    def test_cli_constraint_failure_is_not_mislabeled_as_connection_failure(self):
        """Fail if a real SQLite application constraint is reported as storage unavailability."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root, db_path, source_ids = _seed_synthetic_batch_ledger(root)
            real_factory = cli_module._existing_ledger
            connection_number = 0

            def connection_factory(path):
                nonlocal connection_number
                connection_number += 1
                connection = real_factory(path)
                if connection_number == 3:
                    connection.execute(
                        """
                        CREATE TEMP TRIGGER reject_processing_start
                        BEFORE INSERT ON main.processing_run
                        BEGIN
                            SELECT RAISE(ABORT, 'SYNTHETIC_APPLICATION_CONSTRAINT');
                        END
                        """
                    )
                return connection

            stderr = io.StringIO()
            with _replace_connection_factory(connection_factory), contextlib.redirect_stderr(stderr):
                result = main([
                    "process", "--db", str(db_path),
                    "--evidence-root", str(evidence_root),
                    "--derivative-root", str(root / "derivatives"),
                ])

            db = connect(db_path)
            failed_source_id = source_ids["02-fails.docx"]
            self.assertEqual(1, result)
            self.assertEqual(0, db.execute(
                """
                SELECT count(*) FROM review_task
                WHERE source_file_id=? AND reason_code='PROCESSING_CONNECTION_FAILURE'
                """,
                (failed_source_id,),
            ).fetchone()[0])
            self.assertEqual(1, db.execute(
                """
                SELECT count(*) FROM review_task
                WHERE source_file_id=? AND task_state='OPEN'
                  AND reason_code='PROCESSING_SQLITE_FAILURE'
                """,
                (failed_source_id,),
            ).fetchone()[0])
            self.assertIn("PROCESSING_SQLITE_FAILURE", stderr.getvalue())
            self.assertNotIn("PROCESSING_CONNECTION_FAILURE", stderr.getvalue())
            self.assertEqual(1, db.execute(
                "SELECT count(*) FROM derivative WHERE source_file_id=?",
                (source_ids["03-later.docx"],),
            ).fetchone()[0])

    def test_cli_report_uses_persisted_nondefault_intake_root(self):
        """Fail if report trusts its cwd/default instead of the evidence root recorded at intake."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root, db_path = _seed_synthetic_docx_ledger(root, "actual-evidence")
            before = _tree_hashes(evidence_root)

            self.assertEqual(1, main([
                "report", "--db", str(db_path), "--output", str(evidence_root / "reports"),
            ]))
            self.assertEqual(before, _tree_hashes(evidence_root))

    def test_cli_process_requires_persisted_intake_root_before_creating_runs(self):
        """Fail if a caller can redirect processing from the root that intake bound to the ledger."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, db_path = _seed_synthetic_docx_ledger(root, "actual-evidence")
            wrong_root = root / "different-evidence"
            wrong_root.mkdir()

            self.assertEqual(1, main([
                "process", "--db", str(db_path), "--evidence-root", str(wrong_root),
                "--derivative-root", str(root / "derivatives"),
            ]))
            self.assertEqual(0, connect(db_path).execute(
                "SELECT count(*) FROM processing_run"
            ).fetchone()[0])

    def test_cli_binds_one_intake_root_idempotently_and_rejects_rebinding(self):
        """Fail if re-ingest can silently replace the canonical evidence root for a ledger."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root, db_path = _seed_synthetic_docx_ledger(root, "actual-evidence")
            alternate_root = root / "alternate-evidence"
            shutil.copytree(evidence_root, alternate_root)
            manifest = root / "manifest.json"

            self.assertEqual(0, main([
                "ingest", "--db", str(db_path), "--manifest", str(manifest),
                "--evidence-root", str(evidence_root),
            ]))
            self.assertEqual(1, main([
                "ingest", "--db", str(db_path), "--manifest", str(manifest),
                "--evidence-root", str(alternate_root),
            ]))
            self.assertEqual(str(evidence_root.resolve()), connect(db_path).execute(
                "SELECT value FROM operational_metadata WHERE key='intake_evidence_root'"
            ).fetchone()[0])

    def test_cli_accepts_matching_persisted_root_with_disjoint_outputs(self):
        """Fail if binding the intake root blocks a matching process/report workflow outside evidence."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence_root, db_path = _seed_synthetic_docx_ledger(root, "actual-evidence")
            derivative_root = root / "derivatives"
            report_root = root / "reports"

            self.assertEqual(0, main([
                "process", "--db", str(db_path), "--evidence-root", str(evidence_root),
                "--derivative-root", str(derivative_root),
            ]))
            self.assertEqual(0, main(["report", "--db", str(db_path), "--output", str(report_root)]))
            self.assertTrue(any(derivative_root.rglob("extracted.json")))
            self.assertTrue((report_root / "summary.md").is_file())


def _upload_hashes() -> dict[str, str]:
    """Hash the bounded five-file evidence inventory without modifying it."""
    uploads = sorted((ROOT / "upload").iterdir())
    if len(uploads) != 5:
        raise AssertionError("expected exactly five upload inputs")
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in uploads
    }


def _no_rows(db, query: str, parameters: tuple[object, ...] = ()) -> bool:
    """Evaluate an invariant as a direct SQL query whose result is an empty violation set."""
    row = db.execute(query, parameters).fetchone()
    return row is None or row[0] == 0


def _has_rows(db, query: str, parameters: tuple[object, ...] = ()) -> bool:
    """Require a positive fixture witness so a violation query cannot pass vacuously."""
    return db.execute(query, parameters).fetchone() is not None


def _originals_unchanged(
    db, evidence_root: Path, expected_evidence_hashes: dict[str, str]
) -> bool:
    return (
        _tree_hashes(evidence_root) == expected_evidence_hashes
        and _no_rows(
            db,
            """
            SELECT count(*) FROM package p
            LEFT JOIN source_file sf USING(package_id)
            GROUP BY p.package_id
            HAVING count(sf.source_file_id) <> p.expected_level1_count
            """,
        )
    )


def _all_derivatives_have_source_and_run(db) -> bool:
    return _has_rows(db, "SELECT 1 FROM derivative LIMIT 1") and _no_rows(
        db,
        """
        SELECT count(*) FROM derivative d
        LEFT JOIN source_file sf ON sf.source_file_id=d.source_file_id
        LEFT JOIN processing_run pr ON pr.processing_run_id=d.processing_run_id
        WHERE sf.source_file_id IS NULL OR pr.processing_run_id IS NULL
           OR pr.source_file_id <> d.source_file_id OR pr.completed_at IS NULL OR pr.errors <> ''
        """,
    )


def _all_occurrences_have_exact_source_locator(db) -> bool:
    return _has_rows(db, "SELECT 1 FROM occurrence LIMIT 1") and _no_rows(
        db,
        """
        SELECT count(*) FROM occurrence o
        LEFT JOIN source_file sf ON sf.source_file_id=o.source_file_id
        WHERE sf.source_file_id IS NULL OR trim(o.source_locator) = ''
           OR o.source_locator NOT GLOB '*:*'
        """,
    )


def _control_records_do_not_inflate_level1_counts(db) -> bool:
    return _no_rows(
        db,
        """
        SELECT count(*) FROM package p
        LEFT JOIN source_file sf USING(package_id)
        GROUP BY p.package_id
        HAVING count(sf.source_file_id) <> p.expected_level1_count
        """,
    )


def _canonical_records_preserve_occurrences(db) -> bool:
    return _has_rows(
        db,
        """
        SELECT 1 FROM occurrence
        GROUP BY record_id
        HAVING count(*) >= 2 AND count(DISTINCT source_file_id) >= 2
        LIMIT 1
        """,
    ) and _no_rows(
        db,
        """
        SELECT count(*) FROM occurrence o
        LEFT JOIN record r ON r.record_id=o.record_id
        WHERE r.record_id IS NULL
        """,
    )


def _material_versions_are_not_deduplicated(db) -> bool:
    return _has_rows(db, "SELECT 1 FROM record_version_link LIMIT 1") and _no_rows(
        db,
        """
        SELECT count(*) FROM record_version_link rv
        LEFT JOIN record left_record ON left_record.record_id=rv.record_id
        LEFT JOIN record right_record ON right_record.record_id=rv.related_record_id
        WHERE left_record.record_id IS NULL OR right_record.record_id IS NULL
           OR rv.record_id = rv.related_record_id
           OR left_record.content_fingerprint = right_record.content_fingerprint
           OR rv.relationship_description <> 'MATERIALLY_DIFFERENT_VERSION'
        """,
    )


def _findings_are_cumulative_and_audited(db) -> bool:
    return _has_rows(
        db,
        """
        SELECT 1 FROM finding
        GROUP BY request_element_id
        HAVING count(DISTINCT finding_type) >= 2
        LIMIT 1
        """,
    ) and _no_rows(
        db,
        """
        SELECT count(*) FROM finding f
        WHERE NOT EXISTS (
            SELECT 1 FROM finding_identity fi WHERE fi.finding_id=f.finding_id
        ) OR NOT EXISTS (
            SELECT 1 FROM audit_event ae
            WHERE ae.entity_type='FINDING' AND ae.entity_id=f.finding_id
              AND ae.field_name='CREATE'
        )
        """,
    )


def _metro_statements_are_separate_from_findings(db) -> bool:
    return _has_rows(db, "SELECT 1 FROM metro_statement LIMIT 1") and _has_rows(
        db, "SELECT 1 FROM finding LIMIT 1"
    ) and _no_rows(
        db,
        """
        SELECT count(*) FROM metro_statement ms
        LEFT JOIN package p ON p.package_id=ms.package_id
        WHERE p.package_id IS NULL OR trim(ms.statement_text) = '' OR trim(ms.source_locator) = ''
        """,
    )


def _references_are_not_produced_until_located(db) -> bool:
    return _has_rows(db, "SELECT 1 FROM record_reference LIMIT 1") and _no_rows(
        db,
        """
        SELECT count(*) FROM record_reference rr
        WHERE rr.match_state <> 'CONFIRMED_MATCH'
          AND rr.resolved_record_id IS NOT NULL
        """,
    )


def _no_probable_match_closes_reference(db) -> bool:
    return _has_rows(
        db, "SELECT 1 FROM record_reference WHERE match_state='PROBABLE_MATCH' LIMIT 1"
    ) and _no_rows(
        db,
        """
        SELECT count(*) FROM record_reference
        WHERE match_state='PROBABLE_MATCH'
          AND (resolved_record_id IS NOT NULL OR absence_scope IS NOT NULL)
        """,
    )


def _no_cross_package_match_recredits_original_package(db) -> bool:
    return _has_rows(
        db,
        """
        SELECT 1 FROM request_element_evidence ree
        JOIN request_element re ON re.request_element_id=ree.request_element_id
        JOIN occurrence o ON o.occurrence_id=ree.occurrence_id
        JOIN source_file sf ON sf.source_file_id=o.source_file_id
        WHERE re.package_id <> sf.package_id
          AND ree.evidentiary_role <> 'RESPONSIVE'
        LIMIT 1
        """,
    ) and _no_rows(
        db,
        """
        SELECT count(*) FROM request_element_evidence ree
        JOIN request_element re ON re.request_element_id=ree.request_element_id
        JOIN occurrence o ON o.occurrence_id=ree.occurrence_id
        JOIN source_file sf ON sf.source_file_id=o.source_file_id
        WHERE ree.evidentiary_role='RESPONSIVE' AND re.package_id <> sf.package_id
        """,
    )


def _no_existence_inference_implies_possession_without_support(db) -> bool:
    return _has_rows(
        db,
        """
        SELECT 1 FROM temporal_inference ti
        JOIN temporal_inference_date_fact link
          ON link.temporal_inference_id=ti.temporal_inference_id
        JOIN date_fact df ON df.date_fact_id=link.date_fact_id
        JOIN evidence_citation ec ON ec.evidence_citation_id=df.evidence_citation_id
        WHERE ti.inference_type='EXISTED_BEFORE_RESPONSE'
          AND ec.source_file_id IS NOT NULL
        LIMIT 1
        """,
    ) and _no_rows(
        db,
        """
        SELECT count(*) FROM temporal_inference ti
        WHERE ti.inference_type='POSSESSED_AT_RESPONSE'
          AND (
              ti.verification_state<>'VERIFIED'
              OR ti.possession_supporting_finding_id IS NULL
              OR NOT EXISTS (
                  SELECT 1 FROM finding f
                  WHERE f.finding_id=ti.possession_supporting_finding_id
                    AND f.verification_state='VERIFIED'
                    AND EXISTS (
                        SELECT 1 FROM finding_citation fc
                        WHERE fc.finding_id=f.finding_id
                    )
              )
          )
        """,
    )


def _no_automated_material_finding_is_verified(db) -> bool:
    return _has_rows(
        db,
        "SELECT 1 FROM finding WHERE lower(created_by)='automation' "
        "AND verification_state='PROVISIONAL' LIMIT 1",
    ) and _no_rows(
        db,
        """
        SELECT count(*) FROM finding
        WHERE verification_state='VERIFIED' AND lower(created_by)='automation'
        """,
    )


def _all_processing_ambiguities_have_review_tasks(db) -> bool:
    return _has_rows(
        db,
        """
        SELECT 1 FROM processing_run pr
        JOIN review_task rt ON rt.source_file_id=pr.source_file_id
        WHERE pr.completed_at IS NULL
          AND rt.task_state='OPEN'
          AND rt.reason_code='PROCESSING_INCOMPLETE'
          AND rt.concern='PROCESSING_INCOMPLETE: ' || pr.processing_run_id
        LIMIT 1
        """,
    ) and _no_rows(
        db,
        """
        SELECT count(*) FROM processing_run pr
        WHERE (
            pr.completed_at IS NULL
            OR pr.errors <> ''
            OR pr.warnings LIKE '%LOW_CONFIDENCE_OCR%'
        ) AND NOT EXISTS (
            SELECT 1 FROM review_task rt
            WHERE rt.source_file_id=pr.source_file_id AND rt.task_state='OPEN'
              AND (
                  (
                      pr.completed_at IS NULL
                      AND rt.reason_code='PROCESSING_INCOMPLETE'
                      AND rt.concern='PROCESSING_INCOMPLETE: ' || pr.processing_run_id
                  )
                  OR (
                      pr.completed_at IS NOT NULL
                      AND (
                          (rt.reason_code=pr.errors)
                          OR (
                              rt.reason_code='EXTRACTION_FAILED'
                              AND pr.errors <> ''
                              AND instr(rt.concern, pr.errors) > 0
                          )
                          OR (
                              rt.reason_code='PROCESSING_CONNECTION_FAILURE'
                              AND pr.errors LIKE 'PROCESSING_CONNECTION_FAILURE:%'
                              AND instr(rt.concern, pr.errors) > 0
                          )
                          OR (
                              rt.reason_code='PROCESSING_SQLITE_FAILURE'
                              AND pr.errors LIKE 'PROCESSING_SQLITE_FAILURE:%'
                              AND instr(rt.concern, pr.errors) > 0
                          )
                          OR (
                              rt.reason_code='LOW_CONFIDENCE_OCR'
                              AND pr.warnings LIKE '%LOW_CONFIDENCE_OCR%'
                          )
                      )
                  )
              )
        )
        """,
    )


def _no_corpus_absence_precedes_completeness(db) -> bool:
    return _has_rows(
        db,
        """
        SELECT 1 FROM record_reference rr
        JOIN occurrence o ON o.occurrence_id=rr.occurrence_id
        JOIN source_file sf ON sf.source_file_id=o.source_file_id
        JOIN corpus c ON c.corpus_id=rr.search_corpus_id
        JOIN corpus_package cp
          ON cp.corpus_id=c.corpus_id AND cp.package_id=sf.package_id
        WHERE rr.absence_scope='NOT_LOCATED_CORPUS'
          AND c.completeness_state='VERIFIED_COMPLETE'
          AND NOT EXISTS (
              SELECT 1 FROM corpus_package scoped
              JOIN package p ON p.package_id=scoped.package_id
              WHERE scoped.corpus_id=c.corpus_id
                AND p.completeness_state <> 'VERIFIED_COMPLETE'
          )
        LIMIT 1
        """,
    ) and _no_rows(
        db,
        """
        SELECT count(*) FROM record_reference rr
        JOIN occurrence o ON o.occurrence_id=rr.occurrence_id
        JOIN source_file sf ON sf.source_file_id=o.source_file_id
        LEFT JOIN corpus c ON c.corpus_id=rr.search_corpus_id
        WHERE rr.absence_scope='NOT_LOCATED_CORPUS'
          AND (
              c.completeness_state <> 'VERIFIED_COMPLETE'
              OR c.corpus_id IS NULL
              OR NOT EXISTS (
                  SELECT 1 FROM corpus_package cp
                  WHERE cp.corpus_id=rr.search_corpus_id
                    AND cp.package_id=sf.package_id
              )
              OR EXISTS (
                  SELECT 1 FROM corpus_package cp
                  JOIN package p ON p.package_id=cp.package_id
                  WHERE cp.corpus_id=rr.search_corpus_id
                    AND p.completeness_state <> 'VERIFIED_COMPLETE'
              )
          )
        """,
    )


def _no_final_legal_assessment_uses_provisional_findings(db) -> bool:
    return _has_rows(
        db,
        """
        SELECT 1 FROM legal_assessment la
        JOIN legal_assessment_finding laf ON laf.legal_assessment_id=la.legal_assessment_id
        JOIN finding f ON f.finding_id=laf.finding_id
        WHERE la.assessment_status='FINAL' AND f.verification_state='VERIFIED'
          AND EXISTS (
              SELECT 1 FROM legal_authority auth
              WHERE auth.legal_assessment_id=la.legal_assessment_id
          )
        LIMIT 1
        """,
    ) and _no_rows(
        db,
        """
        SELECT count(*) FROM legal_assessment la
        JOIN legal_assessment_finding laf ON laf.legal_assessment_id=la.legal_assessment_id
        JOIN finding f ON f.finding_id=laf.finding_id
        WHERE la.assessment_status='FINAL' AND f.verification_state <> 'VERIFIED'
        """,
    )


def _substantive_changes_have_append_only_audit_history(db) -> bool:
    return _has_rows(
        db,
        """
        SELECT 1 FROM audit_event
        WHERE entity_type='FINDING' AND field_name='proposition'
        GROUP BY entity_id, field_name
        HAVING count(*) >= 2
        LIMIT 1
        """,
    ) and _no_rows(
        db,
        """
        SELECT count(*) FROM finding f
        WHERE NOT EXISTS (
            SELECT 1 FROM audit_event ae
            WHERE ae.entity_type='FINDING' AND ae.entity_id=f.finding_id
        )
        """,
    ) and _no_rows(
        db,
        """
        WITH substantive_audit AS (
            SELECT * FROM audit_event
            WHERE (
                entity_type='FINDING'
                AND field_name IN (
                    'proposition', 'finding_type', 'notes',
                    'verification_state', 'verified_by'
                )
            ) OR (
                entity_type='RECORD_REFERENCE'
                AND field_name IN (
                    'notes', 'match_state', 'matched_record_id',
                    'resolved_record_id', 'absence_scope'
                )
            )
        ),
        ordered AS (
            SELECT
                ae.*,
                row_number() OVER (
                    PARTITION BY entity_type, entity_id, field_name
                    ORDER BY changed_at, event_id
                ) AS sequence_number,
                row_number() OVER (
                    PARTITION BY entity_type, entity_id, field_name
                    ORDER BY changed_at DESC, event_id DESC
                ) AS reverse_sequence_number,
                lag(new_value) OVER (
                    PARTITION BY entity_type, entity_id, field_name
                    ORDER BY changed_at, event_id
                ) AS prior_new_value
            FROM substantive_audit ae
        ),
        current_values(entity_type, entity_id, field_name, current_value) AS (
            SELECT 'FINDING', finding_id, 'proposition', proposition FROM finding
            UNION ALL SELECT 'FINDING', finding_id, 'finding_type', finding_type FROM finding
            UNION ALL SELECT 'FINDING', finding_id, 'notes', notes FROM finding
            UNION ALL SELECT 'FINDING', finding_id, 'verification_state', verification_state FROM finding
            UNION ALL SELECT 'FINDING', finding_id, 'verified_by', verified_by FROM finding
            UNION ALL SELECT 'RECORD_REFERENCE', record_reference_id, 'notes', notes FROM record_reference
            UNION ALL SELECT 'RECORD_REFERENCE', record_reference_id, 'match_state', match_state FROM record_reference
            UNION ALL SELECT 'RECORD_REFERENCE', record_reference_id, 'matched_record_id', matched_record_id FROM record_reference
            UNION ALL SELECT 'RECORD_REFERENCE', record_reference_id, 'resolved_record_id', resolved_record_id FROM record_reference
            UNION ALL SELECT 'RECORD_REFERENCE', record_reference_id, 'absence_scope', absence_scope FROM record_reference
        )
        SELECT count(*) FROM ordered o
        LEFT JOIN current_values current
          ON current.entity_type=o.entity_type
         AND current.entity_id=o.entity_id
         AND current.field_name=o.field_name
        WHERE o.previous_value IS o.new_value
           OR (
               o.sequence_number > 1
               AND o.previous_value IS NOT o.prior_new_value
           )
           OR (
               o.reverse_sequence_number=1
               AND (
                   current.entity_id IS NULL
                   OR o.new_value IS NOT current.current_value
               )
           )
        """,
    )


def _all_report_totals_reconcile(db, summary_path: Path) -> bool:
    counts = dict(db.execute("SELECT * FROM v_summary_counts").fetchone())
    summary = summary_path.read_text(encoding="utf-8")
    rendered = {
        "package_count": "Packages (package unit; ledger scope)",
        "level1_source_files": "Level 1 source files (file unit; ledger scope)",
        "unique_level2_records": "Unique Level 2 records (record unit; ledger scope)",
        "level2_occurrences": "Level 2 occurrences (occurrence unit; ledger scope)",
        "record_references": "Record references (reference unit; ledger scope)",
        "referenced_not_located_items": "Referenced-but-not-located items (reference unit; ledger scope)",
        "provisional_findings": "Provisional findings (finding unit; ledger scope)",
        "verified_findings": "Verified findings (finding unit; ledger scope)",
        "open_review_tasks": "Open review tasks (review-task unit; ledger scope)",
        "unresolved_review_tasks": "Unresolved review tasks (review-task unit; ledger scope)",
    }
    populated_columns = (
        "package_count", "level1_source_files", "unique_level2_records",
        "level2_occurrences", "record_references", "referenced_not_located_items",
        "provisional_findings", "verified_findings", "open_review_tasks",
    )
    package_rows = [dict(row) for row in db.execute(
        "SELECT * FROM v_package_inventory ORDER BY package_id"
    )]
    corpus_rows = [dict(row) for row in db.execute(
        "SELECT * FROM v_corpus_summary_counts ORDER BY corpus_id"
    )]
    package_totals_match = all(
        "| {package_id} | {expected_level1_count} | {level1_source_file_count} | "
        "{package_completeness_state} |".format(**row) in summary
        for row in package_rows
    )
    corpus_totals_match = all(
        "| {corpus_id} | {level1_source_files} | {unique_level2_records} | "
        "{level2_occurrences} | {record_references} | {referenced_not_located_items} | "
        "{provisional_findings} | {verified_findings} | {open_review_tasks} | "
        "{unresolved_review_tasks} | {corpus_completeness_state} |".format(**row) in summary
        for row in corpus_rows
    )
    return (
        all(counts[column] > 0 for column in populated_columns)
        and bool(package_rows)
        and bool(corpus_rows)
        and all(f"- {label}: {counts[column]}" in summary for column, label in rendered.items())
        and package_totals_match
        and corpus_totals_match
    )


def _seed_acceptance_fixture(root: Path):
    """Populate every acceptance domain through the Task 3--8 service APIs."""
    evidence_root = root / "evidence"
    evidence_root.mkdir()
    (evidence_root / "p1-control.pdf").write_bytes(b"synthetic P1 control")
    (evidence_root / "p2-control.pdf").write_bytes(b"synthetic P2 control")
    repeated = _docx_bytes("Repeated canonical record")
    with zipfile.ZipFile(evidence_root / "p1-production.zip", "w") as archive:
        archive.writestr("01-repeated.docx", repeated)
        archive.writestr("02-repeated-copy.docx", repeated)
        archive.writestr("03-legacy.doc", b"legacy binary")
    with zipfile.ZipFile(evidence_root / "p2-production.zip", "w") as archive:
        archive.writestr("01-material-version.docx", _docx_bytes("Materially revised record"))
    evidence_hashes = _tree_hashes(evidence_root)

    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"packages": [
        {
            "package_id": "P1",
            "control_record": "p1-control.pdf",
            "production_archive": "p1-production.zip",
            "expected_level1_count": 3,
            "package_status": None,
        },
        {
            "package_id": "P2",
            "control_record": "p2-control.pdf",
            "production_archive": "p2-production.zip",
            "expected_level1_count": 1,
            "package_status": None,
        },
    ]}), encoding="utf-8")
    db = connect(root / "ledger.sqlite3")
    initialize(db)
    ingest_manifest(db, manifest, evidence_root)

    derivative_root = root / "derivatives"
    sources = {
        row["archive_member_path"]: row["source_file_id"]
        for row in db.execute("SELECT archive_member_path, source_file_id FROM source_file")
    }
    for source_file_id in sources.values():
        process_source(db, source_file_id, evidence_root, derivative_root)
    derivatives = {
        row["source_file_id"]: row["derivative_id"]
        for row in db.execute("SELECT source_file_id, derivative_id FROM derivative")
    }

    repeated_record_id = create_record(db, "Repeated record", "1" * 64, "REPORT")
    if create_record(db, "Duplicate copy", "1" * 64, "REPORT") != repeated_record_id:
        raise AssertionError("exact duplicate did not resolve to the canonical record")
    first_occurrence = create_occurrence(
        db,
        repeated_record_id,
        sources["01-repeated.docx"],
        derivatives[sources["01-repeated.docx"]],
        "paragraph:1",
        "PROVISIONAL",
    )
    create_occurrence(
        db,
        repeated_record_id,
        sources["02-repeated-copy.docx"],
        derivatives[sources["02-repeated-copy.docx"]],
        "paragraph:1",
        "PROVISIONAL",
    )
    version_record_id = create_record(db, "Materially revised record", "2" * 64, "REPORT")
    version_occurrence = create_occurrence(
        db,
        version_record_id,
        sources["01-material-version.docx"],
        derivatives[sources["01-material-version.docx"]],
        "paragraph:1",
        "PROVISIONAL",
    )
    link_version_family(db, repeated_record_id, version_record_id, "documented material revision")

    request_element_id = add_request_element(db, "P1", "Produce the requested report", 1)
    link_request_evidence(db, request_element_id, first_occurrence, "RESPONSIVE")
    link_request_evidence(db, request_element_id, version_occurrence, "EXISTENCE_EVIDENCE")
    citation_id = add_citation(
        db, sources["01-repeated.docx"], first_occurrence, "paragraph:1"
    )
    add_metro_statement(
        db,
        "Metro states that no additional report exists.",
        "NONEXISTENCE_ASSERTION",
        citation_id,
        "PROVISIONAL",
    )

    automated_finding_id = add_finding(
        db,
        "NONEXISTENCE_ASSERTED",
        request_element_id,
        "PROVISIONAL",
        "AUTOMATION",
        "The response contains a nonexistence assertion.",
    )
    human_finding_id = add_finding(
        db,
        "POSSIBLE_EXISTENCE_EVIDENCE",
        request_element_id,
        "PROVISIONAL",
        "HUMAN",
        "A related version may evidence the report's existence.",
    )
    register_reviewer_identity(db, "reviewer-1", "HUMAN")
    promote_finding_verified(
        db,
        human_finding_id,
        "reviewer-1",
        "2026-08-07T12:00:00Z",
        [citation_id],
    )
    change_with_audit(
        db,
        "FINDING",
        automated_finding_id,
        "finding",
        "proposition",
        "Human review is evaluating the extracted nonexistence assertion.",
        "begin human source review",
        "reviewer-1",
    )
    change_with_audit(
        db,
        "FINDING",
        automated_finding_id,
        "finding",
        "proposition",
        "Source review confirms that the response contains a nonexistence assertion.",
        "human source review",
        "reviewer-1",
    )

    probable_reference = add_record_reference(
        db, first_occurrence, "paragraph:2", "ATTACHMENT", "Probable attachment"
    )
    set_reference_match(
        db, probable_reference, "PROBABLE_MATCH", version_record_id, "reviewer-1"
    )
    cross_package_reference = add_record_reference(
        db, first_occurrence, "paragraph:3", "REPORT", "Revised report"
    )
    set_reference_match(
        db, cross_package_reference, "CONFIRMED_MATCH", version_record_id, "reviewer-1"
    )
    add_record_reference(
        db, first_occurrence, "paragraph:4", "EXHIBIT", "Unlocated exhibit"
    )

    date_fact_id = add_date_fact(
        db,
        "RECORD",
        repeated_record_id,
        "RECORD_DATE",
        "2026-07-01",
        "2026-07-01",
        "DAY",
        citation_id,
    )
    add_temporal_inference(
        db,
        "RECORD",
        repeated_record_id,
        "EXISTED_BEFORE_RESPONSE",
        [date_fact_id],
    )

    assessment_id = create_legal_assessment(
        db,
        "Does the verified evidence support the bounded conclusion?",
        "The verified finding supports only the stated bounded conclusion.",
        [human_finding_id],
        [("STATUTE", "Synthetic authority § 1")],
    )
    finalize_legal_assessment(db, assessment_id, "reviewer-1")
    incomplete_run_id = "PR_ACCEPTANCE_INCOMPLETE"
    incomplete_source_id = sources["01-repeated.docx"]
    db.execute(
        """
        INSERT INTO processing_run(
            processing_run_id, source_file_id, operation, tool_name, started_at
        ) VALUES(?, ?, 'EXTRACT_DOCX', 'synthetic-acceptance',
                 '2026-08-07T13:00:00Z')
        """,
        (incomplete_run_id, incomplete_source_id),
    )
    open_review_task(
        db,
        "SOURCE_FILE",
        incomplete_source_id,
        "PROCESSING_INCOMPLETE",
        f"PROCESSING_INCOMPLETE: {incomplete_run_id}",
        task_type="EXTRACTION_EXCEPTION",
    )
    set_package_completeness(db, "P1", "REVIEW_REQUIRED", "reviewer-1")
    promote_occurrence_verified(
        db, version_occurrence, "reviewer-1", "2026-08-07T12:30:00Z"
    )
    set_package_completeness(db, "P2", "VERIFIED_COMPLETE", "reviewer-1")
    add_corpus(db, "C1", "Synthetic acceptance corpus")
    add_corpus_package(db, "C1", "P1", "reviewer-1")
    add_corpus_package(db, "C1", "P2", "reviewer-1")
    set_corpus_completeness(db, "C1", "REVIEW_REQUIRED", "reviewer-1")
    db.commit()
    return db, evidence_root, evidence_hashes


def _seed_verified_absence_fixture(root: Path):
    """Create one real corpus-wide absence only after all completeness gates are verified."""
    root.mkdir()
    db = connect(root / "ledger.sqlite3")
    initialize(db)
    db.execute(
        """
        INSERT INTO package(package_id, control_record_path, expected_level1_count)
        VALUES('P1', 'control.pdf', 1)
        """
    )
    db.execute(
        """
        INSERT INTO source_file(
            source_file_id, package_id, archive_member_path, byte_size, sha256, media_type
        ) VALUES('S1', 'P1', 'source.pdf', 1, ?, 'application/pdf')
        """,
        ("a" * 64,),
    )
    record_id = create_record(db, "Reference-bearing record", "b" * 64, "REPORT")
    occurrence_id = create_occurrence(
        db, record_id, "S1", None, "page:1", "PROVISIONAL"
    )
    record_processing_result(db, "S1", "EXTRACT_PDF", b"verified terminal derivative")
    reference_id = add_record_reference(
        db,
        occurrence_id,
        "page:2",
        "ATTACHMENT",
        "Attachment not located in the verified corpus",
    )
    register_reviewer_identity(db, "absence-reviewer", "HUMAN")
    promote_occurrence_verified(
        db, occurrence_id, "absence-reviewer", "2026-08-07T12:00:00Z"
    )
    set_package_completeness(db, "P1", "VERIFIED_COMPLETE", "absence-reviewer")
    add_corpus(db, "C_VERIFIED", "Verified corpus")
    add_corpus_package(db, "C_VERIFIED", "P1", "absence-reviewer")
    set_corpus_completeness(
        db, "C_VERIFIED", "VERIFIED_COMPLETE", "absence-reviewer"
    )
    assign_reference_search_corpus(
        db, reference_id, "C_VERIFIED", "absence-reviewer"
    )
    set_reference_absence_scope(
        db, reference_id, "NOT_LOCATED_CORPUS", "absence-reviewer"
    )
    db.commit()
    return db


def _seed_synthetic_docx_ledger(root: Path, evidence_name: str = "upload") -> tuple[Path, Path]:
    """Create a one-source ledger whose valid DOCX would produce a derivative if run."""
    evidence_root = root / evidence_name
    evidence_root.mkdir()
    (evidence_root / "control.pdf").write_bytes(b"control")
    with zipfile.ZipFile(evidence_root / "production.zip", "w") as archive:
        archive.writestr("record.docx", _docx_bytes())
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"packages": [{
        "package_id": "P1", "control_record": "control.pdf",
        "production_archive": "production.zip", "expected_level1_count": 1,
        "package_status": None,
    }]}), encoding="utf-8")
    db_path = root / "ledger.sqlite3"
    if main(["init", "--db", str(db_path)]) != 0:
        raise AssertionError("synthetic ledger initialization failed")
    if main([
        "ingest", "--db", str(db_path), "--manifest", str(manifest),
        "--evidence-root", str(evidence_root),
    ]) != 0:
        raise AssertionError("synthetic ledger intake failed")
    return evidence_root, db_path


def _seed_synthetic_batch_ledger(root: Path) -> tuple[Path, Path, dict[str, str]]:
    """Create an ordered four-source batch for connection-isolation regression testing."""
    evidence_root = root / "evidence"
    evidence_root.mkdir()
    (evidence_root / "control.pdf").write_bytes(b"control")
    with zipfile.ZipFile(evidence_root / "production.zip", "w") as archive:
        archive.writestr("01-first.docx", _docx_bytes("first source"))
        archive.writestr("02-fails.docx", _docx_bytes("connection failure source"))
        archive.writestr("03-later.docx", _docx_bytes("later source"))
        archive.writestr("04-legacy.doc", b"legacy binary")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"packages": [{
        "package_id": "P1",
        "control_record": "control.pdf",
        "production_archive": "production.zip",
        "expected_level1_count": 4,
        "package_status": None,
    }]}), encoding="utf-8")
    db_path = root / "ledger.sqlite3"
    if main(["init", "--db", str(db_path)]) != 0:
        raise AssertionError("synthetic batch ledger initialization failed")
    if main([
        "ingest",
        "--db",
        str(db_path),
        "--manifest",
        str(manifest),
        "--evidence-root",
        str(evidence_root),
    ]) != 0:
        raise AssertionError("synthetic batch ledger intake failed")
    db = connect(db_path)
    source_ids = {
        row["archive_member_path"]: row["source_file_id"]
        for row in db.execute("SELECT archive_member_path, source_file_id FROM source_file")
    }
    db.close()
    return evidence_root, db_path, source_ids


def _insert_historical_incomplete_run(db_path: Path, source_file_id: str) -> None:
    db = connect(db_path)
    db.execute(
        """
        INSERT INTO processing_run(
            processing_run_id, source_file_id, operation, tool_name,
            started_at, warnings, errors
        ) VALUES('PR_HISTORICAL_INCOMPLETE', ?, 'EXTRACT_DOCX',
                 'historical-run', '2026-08-01T00:00:00Z', '', '')
        """,
        (source_file_id,),
    )
    db.commit()
    db.close()


class _QueryOnlyOnProcessingFinish:
    """Transition a real SQLite connection to query-only before its first run completion write."""

    def __init__(self, connection):
        self._connection = connection
        self._transitioned = False

    def execute(self, sql, parameters=()):
        if (
            not self._transitioned
            and " ".join(sql.split()).startswith("UPDATE processing_run SET completed_at=")
        ):
            self._connection.execute("PRAGMA query_only=ON")
            self._transitioned = True
        return self._connection.execute(sql, parameters)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exception_type, exception, traceback):
        return self._connection.__exit__(exception_type, exception, traceback)

    def __getattr__(self, name):
        return getattr(self._connection, name)


def _docx_bytes(text: str = "Synthetic ledger regression fixture") -> bytes:
    document = Document()
    document.add_paragraph(text)
    payload = io.BytesIO()
    document.save(payload)
    return payload.getvalue()


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@contextlib.contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextlib.contextmanager
def _replace_connection_factory(factory):
    """Inject only ledger connection creation; returned connections remain real SQLite objects."""
    original = cli_module._existing_ledger
    cli_module._existing_ledger = factory
    try:
        yield
    finally:
        cli_module._existing_ledger = original
