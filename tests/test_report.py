import csv
import tempfile
import unittest
from pathlib import Path

from metro_forensics.evidence import (
    add_citation,
    add_finding,
    add_request_element,
    link_request_evidence,
)
from metro_forensics.report import REPORTS, generate_reports
from metro_forensics.records import create_occurrence, create_record
from metro_forensics.review import (
    add_corpus,
    add_corpus_package,
    promote_finding_verified,
    register_reviewer_identity,
)
from metro_forensics.temporal_legal import (
    create_legal_assessment,
    finalize_legal_assessment,
)
from tests.helpers import new_test_db, seed_package_source, seeded_duplicate_occurrence_db


class ReportTests(unittest.TestCase):
    def test_unique_records_and_occurrences_are_separate_counts(self):
        """Fail if the summary collapses repeated appearances into record count."""
        db = seeded_duplicate_occurrence_db()

        row = db.execute(
            "SELECT unique_level2_records, level2_occurrences FROM v_summary_counts"
        ).fetchone()

        self.assertEqual((1, 2), tuple(row))

    def test_exports_reconcile_to_sqlite(self):
        """Fail if a CSV is not a complete export of its canonical SQLite view."""
        db = seeded_duplicate_occurrence_db()
        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td)
            paths = generate_reports(db, output_dir)
            occurrence_csv = output_dir / "occurrences.csv"
            with occurrence_csv.open(newline="", encoding="utf-8") as handle:
                exported = list(csv.DictReader(handle))

            expected = db.execute("SELECT count(*) FROM v_occurrences").fetchone()[0]
            self.assertEqual(expected, len(exported))
            self.assertIn(occurrence_csv, paths)

    def test_named_views_and_exports_are_complete_and_deterministic(self):
        """Fail if a required ledger view is absent, mutable, or non-reproducible."""
        db = seeded_duplicate_occurrence_db()
        required_views = set(REPORTS.values()) | {
            "v_summary_counts",
            "v_corpus_summary_counts",
        }
        views = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='view'")
        }
        self.assertTrue(required_views <= views)

        before = tuple(db.iterdump())
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_paths = generate_reports(db, Path(first))
            second_paths = generate_reports(db, Path(second))
            self.assertEqual(
                [path.name for path in first_paths], [path.name for path in second_paths]
            )
            for first_path, second_path in zip(first_paths, second_paths):
                self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
                if first_path.suffix == ".csv":
                    with first_path.open(newline="", encoding="utf-8") as handle:
                        self.assertNotIn("document_count", csv.DictReader(handle).fieldnames)

            summary = (Path(first) / "summary.md").read_text(encoding="utf-8")
            self.assertIn("Package scope", summary)
            self.assertIn("Level 1 source files", summary)
            self.assertIn("Unique Level 2 records", summary)
            self.assertIn("Level 2 occurrences", summary)
        self.assertEqual(before, tuple(db.iterdump()))

    def test_legal_report_excludes_provisional_support_and_includes_authorities(self):
        """Fail if a legal report treats provisional facts or authority counts as auditable support."""
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        request_element_id = add_request_element(db, "P1", "requested item", 1)
        citation_id = add_citation(db, source_file_id, None, "page:1")
        provisional_finding_id = add_finding(
            db,
            "UNPRODUCED",
            request_element_id,
            "PROVISIONAL",
            "HUMAN",
            "Provisional factual support.",
        )
        verified_finding_id = add_finding(
            db,
            "DIRECT_CONTRADICTION",
            request_element_id,
            "PROVISIONAL",
            "HUMAN",
            "Verified factual support.",
        )
        register_reviewer_identity(db, "reviewer", "HUMAN")
        promote_finding_verified(
            db, verified_finding_id, "reviewer", "2026-08-07T12:00:00Z", [citation_id]
        )

        excluded_assessment_id = create_legal_assessment(
            db,
            "Excluded question",
            "Excluded conclusion",
            [provisional_finding_id],
            [("STATUTE", "R.C. 149.43")],
        )
        draft_assessment_id = create_legal_assessment(
            db,
            "Draft question",
            "Draft conclusion",
            [verified_finding_id],
            [("STATUTE", "R.C. 149.43")],
        )
        final_assessment_id = create_legal_assessment(
            db,
            "Final question",
            "Final conclusion",
            [verified_finding_id],
            [("CASE", "State ex rel. v. Metro")],
        )
        finalize_legal_assessment(db, final_assessment_id, "reviewer")

        rows = list(db.execute("SELECT * FROM v_legal_assessments ORDER BY legal_assessment_id"))
        exported_ids = {row["legal_assessment_id"] for row in rows}
        self.assertEqual({draft_assessment_id, final_assessment_id}, exported_ids)
        self.assertNotIn(excluded_assessment_id, exported_ids)
        self.assertEqual(
            {"DRAFT", "FINAL"}, {row["assessment_status"] for row in rows}
        )
        authorities = "\n".join(row["cited_authorities"] for row in rows)
        self.assertIn("STATUTE", authorities)
        self.assertIn("R.C. 149.43", authorities)
        self.assertIn("CASE", authorities)
        self.assertIn("State ex rel. v. Metro", authorities)

        with tempfile.TemporaryDirectory() as td:
            generate_reports(db, Path(td))
            with (Path(td) / "legal_assessments.csv").open(newline="", encoding="utf-8") as handle:
                exported = list(csv.DictReader(handle))
        self.assertEqual(exported_ids, {row["legal_assessment_id"] for row in exported})
        self.assertTrue(all(row["cited_authorities"] for row in exported))

    def test_crosswalk_separates_unique_records_from_occurrences_and_keeps_findings(self):
        """Fail if duplicate evidence inflates record responsiveness or hides cumulative findings."""
        db = new_test_db()
        _, first_source_file_id = seed_package_source(db, "P1", "S1", "first.pdf")
        db.execute(
            "INSERT INTO source_file(source_file_id,package_id,archive_member_path,byte_size,sha256,media_type) "
            "VALUES('S2','P1','second.pdf',1,?, 'application/octet-stream')",
            ("1" * 64,),
        )
        request_element_id = add_request_element(db, "P1", "requested item", 1)
        responsive_record_id = create_record(db, "Responsive item", "2" * 64)
        responsive_occurrences = [
            create_occurrence(db, responsive_record_id, source_file_id, None, "page:1", "PROVISIONAL")
            for source_file_id in (first_source_file_id, "S2")
        ]
        substitute_record_id = create_record(db, "Substitute item", "3" * 64)
        substitute_occurrences = [
            create_occurrence(db, substitute_record_id, source_file_id, None, "page:2", "PROVISIONAL")
            for source_file_id in (first_source_file_id, "S2")
        ]
        for occurrence_id in responsive_occurrences:
            link_request_evidence(db, request_element_id, occurrence_id, "RESPONSIVE")
        for occurrence_id in substitute_occurrences:
            link_request_evidence(db, request_element_id, occurrence_id, "SUBSTITUTE")
        add_finding(
            db, "UNPRODUCED", request_element_id, "PROVISIONAL", "HUMAN", "No item produced."
        )
        add_finding(
            db, "SUBSTITUTE_PRODUCTION", request_element_id, "PROVISIONAL", "HUMAN", "Substitute produced."
        )

        row = db.execute(
            "SELECT responsive_record_count, responsive_occurrence_count, "
            "substitute_record_count, substitute_occurrence_count, cumulative_finding_types "
            "FROM v_request_element_crosswalk WHERE request_element_id=?",
            (request_element_id,),
        ).fetchone()
        self.assertEqual((1, 2, 1, 2), tuple(row[:4]))
        self.assertEqual("SUBSTITUTE_PRODUCTION|UNPRODUCED", row["cumulative_finding_types"])

    def test_corpus_totals_are_membership_scoped_and_deduplicate_records(self):
        """Fail if corpus totals masquerade as package totals or duplicate shared records."""
        db = new_test_db()
        _, p1_source_file_id = seed_package_source(db, "P1", "S1", "p1.pdf")
        _, p2_source_file_id = seed_package_source(db, "P2", "S2", "p2.pdf")
        shared_record_id = create_record(db, "Shared", "4" * 64)
        create_occurrence(db, shared_record_id, p1_source_file_id, None, "page:1", "PROVISIONAL")
        create_occurrence(db, shared_record_id, p2_source_file_id, None, "page:1", "PROVISIONAL")
        p1_record_id = create_record(db, "P1 only", "5" * 64)
        create_occurrence(db, p1_record_id, p1_source_file_id, None, "page:2", "PROVISIONAL")
        p2_record_id = create_record(db, "P2 only", "6" * 64)
        create_occurrence(db, p2_record_id, p2_source_file_id, None, "page:2", "PROVISIONAL")
        register_reviewer_identity(db, "reviewer", "HUMAN")
        add_corpus(db, "C1", "P1 scope")
        add_corpus(db, "C2", "Combined scope")
        add_corpus_package(db, "C1", "P1", "reviewer")
        add_corpus_package(db, "C2", "P1", "reviewer")
        add_corpus_package(db, "C2", "P2", "reviewer")

        rows = {
            row["corpus_id"]: tuple(row)
            for row in db.execute(
                "SELECT corpus_id, level1_source_files, unique_level2_records, level2_occurrences "
                "FROM v_corpus_summary_counts ORDER BY corpus_id"
            )
        }
        self.assertEqual(("C1", 1, 2, 2), rows["C1"])
        self.assertEqual(("C2", 2, 3, 4), rows["C2"])

        with tempfile.TemporaryDirectory() as td:
            generate_reports(db, Path(td))
            summary = (Path(td) / "summary.md").read_text(encoding="utf-8")
        self.assertIn("## Corpus scope", summary)
        self.assertIn("| C1 | 1 | 2 | 2 |", summary)
        self.assertIn("| C2 | 2 | 3 | 4 |", summary)
