import sqlite3
import tempfile
import unittest
from pathlib import Path

from metro_forensics.db import connect
from metro_forensics.evidence import (
    add_record_reference,
    assign_reference_search_corpus,
    set_reference_absence_scope,
)
from metro_forensics.extract import record_processing_result
from metro_forensics.records import create_occurrence, create_record
from metro_forensics.review import (
    add_corpus,
    add_corpus_package,
    promote_occurrence_verified,
    register_reviewer_identity,
    set_corpus_completeness,
    set_package_completeness,
)
from tests.helpers import new_test_db, seed_package_source


REQUIRED_TABLES = {
    "package", "request_element", "source_file", "record", "occurrence",
    "request_element_evidence", "metro_statement", "statement_request_element",
    "finding", "evidence_citation", "finding_citation", "record_reference",
    "date_fact", "temporal_inference", "temporal_inference_date_fact",
    "processing_run", "derivative", "review_task", "audit_event",
    "reviewer_identity",
    "legal_assessment", "legal_assessment_finding", "legal_authority",
    "record_version_link", "vocabulary",
}


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = new_test_db()

    def test_required_tables_and_codes_exist(self):
        tables = {r[0] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertTrue(REQUIRED_TABLES <= tables)
        codes = {r[0] for r in self.db.execute("SELECT code FROM vocabulary")}
        for code in {
            "UNPRODUCED", "NONEXISTENCE_ASSERTED", "SUBSTITUTE_PRODUCTION",
            "DIRECT_CONTRADICTION", "STRONG_EXISTENCE_EVIDENCE",
            "POSSIBLE_EXISTENCE_EVIDENCE", "PROVISIONAL", "VERIFIED",
            "CONFIRMED_MATCH", "PROBABLE_MATCH", "NO_MATCH_LOCATED",
            "NOT_LOCATED_RESPONSIVE_PACKAGE", "LOCATED_ELSEWHERE_CORPUS",
            "NOT_LOCATED_CORPUS", "CORPUS_SEARCH_INCOMPLETE",
            "OPEN", "UNRESOLVED", "RESOLVED", "IN_PROGRESS",
            "REVIEW_REQUIRED", "COMPLETE_WITH_EXCEPTIONS", "VERIFIED_COMPLETE",
            "NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED",
        }:
            self.assertIn(code, codes)

    def test_audit_events_are_append_only(self):
        self.db.execute(
            "INSERT INTO audit_event(event_id, entity_type, entity_id, changed_at, reason, change_source) "
            "VALUES('AE1','TEST','F1','2026-08-07T12:00:00Z','test','human')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM audit_event WHERE event_id='AE1'")

    def test_connect_enables_foreign_keys_and_returns_row_objects(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db = connect(Path(temporary_directory) / "ledger.sqlite")
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            db.execute("CREATE TABLE check_rows (value TEXT NOT NULL)")
            db.execute("INSERT INTO check_rows(value) VALUES ('expected')")
            row = db.execute("SELECT value FROM check_rows").fetchone()
            self.assertIsInstance(row, sqlite3.Row)
            self.assertEqual(row["value"], "expected")
            db.close()

    def test_automation_cannot_create_a_verified_finding(self):
        self.db.execute(
            "INSERT INTO package(package_id, control_record_path, expected_level1_count) "
            "VALUES ('P1', 'P1.pdf', 0)"
        )
        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, changed_at, reason, change_source"
            ") VALUES ('AE1', 'FINDING', 'F1', 'CREATE', '2026-08-07T12:00:00Z', "
            "'attempt automated finding', 'automation')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO finding("
                "finding_id, package_id, finding_type, proposition, verification_state, "
                "created_by, verified_by, created_at"
                ") VALUES ('F1', 'P1', 'UNPRODUCED', 'automated result', 'VERIFIED', "
                "'automation', 'human-reviewer', '2026-08-07T12:00:00Z')"
            )

    def test_not_located_corpus_requires_a_verified_complete_search_corpus(self):
        _, source_id = seed_package_source(self.db, member="production.pdf")
        record_id = create_record(self.db, "Source record", "1" * 64)
        occurrence_id = create_occurrence(
            self.db, record_id, source_id, None, "page:1", "PROVISIONAL"
        )
        reference_id = add_record_reference(
            self.db, occurrence_id, "page:2", "ATTACHMENT", "Referenced item"
        )
        register_reviewer_identity(self.db, "reviewer", "HUMAN")
        with self.assertRaises(ValueError):
            set_reference_absence_scope(
                self.db, reference_id, "NOT_LOCATED_CORPUS", "reviewer"
            )

        record_processing_result(self.db, source_id, "EXTRACT_PDF", b"terminal")
        promote_occurrence_verified(
            self.db, occurrence_id, "reviewer", "2026-08-07T12:00:00Z"
        )
        set_package_completeness(self.db, "P1", "VERIFIED_COMPLETE", "reviewer")
        add_corpus(self.db, "C1", "Current corpus")
        add_corpus_package(self.db, "C1", "P1", "reviewer")
        set_corpus_completeness(self.db, "C1", "VERIFIED_COMPLETE", "reviewer")
        assign_reference_search_corpus(self.db, reference_id, "C1", "reviewer")
        set_reference_absence_scope(
            self.db, reference_id, "NOT_LOCATED_CORPUS", "reviewer"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM corpus_package WHERE corpus_id='C1' AND package_id='P1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE package SET completeness_state='IN_PROGRESS' WHERE package_id='P1'")

        self.db.execute(
            "INSERT INTO package(package_id, control_record_path, expected_level1_count) "
            "VALUES ('P2', 'P2.pdf', 0)"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            add_corpus_package(self.db, "C1", "P2", "reviewer")

    def test_substantive_finding_changes_require_audit_events(self):
        self.db.execute(
            "INSERT INTO package(package_id, control_record_path, expected_level1_count) "
            "VALUES ('P1', 'P1.pdf', 0)"
        )
        finding_values = (
            "'F1', 'P1', 'UNPRODUCED', 'original proposition', 'PROVISIONAL', "
            "'human', '2026-08-07T12:00:00Z'"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO finding("
                "finding_id, package_id, finding_type, proposition, verification_state, created_by, created_at"
                f") VALUES ({finding_values})"
            )

        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, changed_at, reason, change_source"
            ") VALUES ('AE1', 'FINDING', 'F1', 'CREATE', '2026-08-07T12:00:00Z', "
            "'create finding', 'human')"
        )
        self.db.execute(
            "INSERT INTO finding("
            "finding_id, package_id, finding_type, proposition, verification_state, created_by, created_at"
            f") VALUES ({finding_values})"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE finding SET proposition='rewritten proposition' WHERE finding_id='F1'")

        self.db.execute(
            "INSERT INTO reviewer_identity(reviewer_id,identity_type) VALUES('human','HUMAN')"
        )
        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, previous_value, new_value, "
            "changed_at, reason, change_source"
            ") VALUES ('AE2', 'FINDING', 'F1', 'proposition', 'original proposition', "
            "'rewritten proposition', '2026-08-07T12:01:00Z', 'correct wording', 'human')"
        )
        self.db.execute("UPDATE finding SET proposition='rewritten proposition' WHERE finding_id='F1'")
        self.assertEqual(
            self.db.execute("SELECT proposition FROM finding WHERE finding_id='F1'").fetchone()[0],
            "rewritten proposition",
        )

    def test_finding_scope_must_belong_to_its_package(self):
        self.db.execute(
            "INSERT INTO package(package_id, control_record_path, expected_level1_count) "
            "VALUES ('P1', 'P1.pdf', 0), ('P2', 'P2.pdf', 1)"
        )
        self.db.execute(
            "INSERT INTO reviewer_identity(reviewer_id,identity_type) VALUES('human','HUMAN')"
        )
        self.db.execute(
            "INSERT INTO request_element(request_element_id, package_id, requested_language) "
            "VALUES ('RE2', 'P2', 'The second package request')"
        )
        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, changed_at, reason, change_source"
            ") VALUES ('AE1', 'FINDING', 'F1', 'CREATE', '2026-08-07T12:00:00Z', "
            "'test creation', 'human')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO finding("
                "finding_id, package_id, request_element_id, finding_type, proposition, created_at"
                ") VALUES ('F1', 'P1', 'RE2', 'UNPRODUCED', 'cross-package request element', "
                "'2026-08-07T12:00:00Z')"
            )

        self.db.execute(
            "INSERT INTO source_file(source_file_id, package_id, archive_member_path, byte_size, sha256, media_type) "
            "VALUES ('S2', 'P2', 'production.pdf', 1, ?, 'application/pdf')",
            ("0" * 64,),
        )
        self.db.execute(
            "INSERT INTO record(record_id, title_or_description, canonical_identity_basis) "
            "VALUES ('R2', 'Source record', 'source identity')"
        )
        self.db.execute(
            "INSERT INTO occurrence(occurrence_id, record_id, source_file_id, source_locator) "
            "VALUES ('O2', 'R2', 'S2', 'p. 1')"
        )
        self.db.execute(
            "INSERT INTO record_reference("
            "record_reference_id, occurrence_id, source_locator, relationship_type, "
            "referenced_description, match_state"
            ") VALUES ('RR2', 'O2', 'p. 1', 'ATTACHMENT', 'Referenced item', 'NO_MATCH_LOCATED')"
        )
        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, changed_at, reason, change_source"
            ") VALUES ('AE2', 'FINDING', 'F2', 'CREATE', '2026-08-07T12:01:00Z', "
            "'test creation', 'human')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO finding("
                "finding_id, package_id, record_reference_id, finding_type, proposition, created_at"
                ") VALUES ('F2', 'P1', 'RR2', 'POSSIBLE_EXISTENCE_EVIDENCE', "
                "'cross-package reference', '2026-08-07T12:01:00Z')"
            )

        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, changed_at, reason, change_source"
            ") VALUES ('AE3', 'FINDING', 'F3', 'CREATE', '2026-08-07T12:02:00Z', "
            "'test creation', 'human')"
        )
        self.db.execute(
            "INSERT INTO finding("
            "finding_id, package_id, request_element_id, finding_type, proposition, created_at"
            ") VALUES ('F3', 'P2', 'RE2', 'UNPRODUCED', 'valid package scope', "
            "'2026-08-07T12:02:00Z')"
        )
        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, previous_value, new_value, "
            "changed_at, reason, change_source"
            ") VALUES ('AE4', 'FINDING', 'F3', 'package_id', 'P2', 'P1', "
            "'2026-08-07T12:03:00Z', 'test invalid move', 'human')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE finding SET package_id='P1' WHERE finding_id='F3'")

        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, changed_at, reason, change_source"
            ") VALUES ('AE5', 'FINDING', 'F4', 'CREATE', '2026-08-07T12:04:00Z', "
            "'test creation', 'human')"
        )
        self.db.execute(
            "INSERT INTO finding("
            "finding_id, package_id, record_reference_id, finding_type, proposition, created_at"
            ") VALUES ('F4', 'P2', 'RR2', 'POSSIBLE_EXISTENCE_EVIDENCE', "
            "'valid reference scope', '2026-08-07T12:04:00Z')"
        )
        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, previous_value, new_value, "
            "changed_at, reason, change_source"
            ") VALUES ('AE6', 'FINDING', 'F4', 'package_id', 'P2', 'P1', "
            "'2026-08-07T12:05:00Z', 'test invalid move', 'human')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE finding SET package_id='P1' WHERE finding_id='F4'")

    def test_finding_creator_and_identity_are_immutable(self):
        self.db.execute(
            "INSERT INTO package(package_id, control_record_path, expected_level1_count) "
            "VALUES ('P1', 'P1.pdf', 0)"
        )
        self.db.execute(
            "INSERT INTO reviewer_identity(reviewer_id,identity_type) VALUES('human','HUMAN')"
        )
        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, changed_at, reason, change_source"
            ") VALUES ('AE1', 'FINDING', 'F1', 'CREATE', '2026-08-07T12:00:00Z', "
            "'automated discovery', 'automation')"
        )
        self.db.execute(
            "INSERT INTO finding("
            "finding_id, package_id, finding_type, proposition, created_by, created_at"
            ") VALUES ('F1', 'P1', 'UNPRODUCED', 'automated discovery', 'automation', "
            "'2026-08-07T12:00:00Z')"
        )
        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, previous_value, new_value, "
            "changed_at, reason, change_source"
            ") VALUES ('AE2', 'FINDING', 'F1', 'created_by', 'automation', 'human', "
            "'2026-08-07T12:01:00Z', 'attempt provenance rewrite', 'human')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE finding SET created_by='human' WHERE finding_id='F1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE finding SET finding_id='F2' WHERE finding_id='F1'")

    def test_finding_replacement_cannot_reuse_its_creation_audit_event(self):
        self.db.execute(
            "INSERT INTO package(package_id, control_record_path, expected_level1_count) "
            "VALUES ('P1', 'P1.pdf', 0)"
        )
        self.db.execute(
            "INSERT INTO audit_event("
            "event_id, entity_type, entity_id, field_name, changed_at, reason, change_source"
            ") VALUES ('AE1', 'FINDING', 'F1', 'CREATE', '2026-08-07T12:00:00Z', "
            "'create finding', 'human')"
        )
        self.db.execute(
            "INSERT INTO finding("
            "finding_id, package_id, finding_type, proposition, created_at"
            ") VALUES ('F1', 'P1', 'UNPRODUCED', 'original proposition', '2026-08-07T12:00:00Z')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT OR REPLACE INTO finding("
                "finding_id, package_id, finding_type, proposition, created_at"
                ") VALUES ('F1', 'P1', 'UNPRODUCED', 'replacement proposition', "
                "'2026-08-07T12:01:00Z')"
            )

    def test_parent_scope_moves_cannot_break_existing_finding_packages(self):
        self.db.execute(
            "INSERT INTO package(package_id, control_record_path, expected_level1_count) "
            "VALUES ('P1', 'P1.pdf', 1), ('P2', 'P2.pdf', 1)"
        )
        self.db.execute(
            "INSERT INTO request_element(request_element_id, package_id, requested_language) "
            "VALUES ('RE2', 'P2', 'Second package request')"
        )
        self.db.execute(
            "INSERT INTO source_file(source_file_id, package_id, archive_member_path, byte_size, sha256, media_type) "
            "VALUES ('S1', 'P1', 'one.pdf', 1, ?, 'application/pdf'), "
            "('S2', 'P2', 'two.pdf', 1, ?, 'application/pdf')",
            ("0" * 64, "1" * 64),
        )
        self.db.execute(
            "INSERT INTO record(record_id, title_or_description, canonical_identity_basis) "
            "VALUES ('R1', 'Source record', 'source identity')"
        )
        self.db.execute(
            "INSERT INTO occurrence(occurrence_id, record_id, source_file_id, source_locator) "
            "VALUES ('O1', 'R1', 'S1', 'p. 1'), ('O2', 'R1', 'S2', 'p. 1')"
        )
        self.db.execute(
            "INSERT INTO record_reference("
            "record_reference_id, occurrence_id, source_locator, relationship_type, "
            "referenced_description, match_state"
            ") VALUES ('RR2', 'O2', 'p. 1', 'ATTACHMENT', 'Referenced item', 'NO_MATCH_LOCATED')"
        )
        for event_id, finding_id in (("AE1", "F1"), ("AE2", "F2")):
            self.db.execute(
                "INSERT INTO audit_event("
                "event_id, entity_type, entity_id, field_name, changed_at, reason, change_source"
                ") VALUES (?, 'FINDING', ?, 'CREATE', '2026-08-07T12:00:00Z', "
                "'test creation', 'human')",
                (event_id, finding_id),
            )
        self.db.execute(
            "INSERT INTO finding("
            "finding_id, package_id, request_element_id, finding_type, proposition, created_at"
            ") VALUES ('F1', 'P2', 'RE2', 'UNPRODUCED', 'request finding', '2026-08-07T12:00:00Z')"
        )
        self.db.execute(
            "INSERT INTO finding("
            "finding_id, package_id, record_reference_id, finding_type, proposition, created_at"
            ") VALUES ('F2', 'P2', 'RR2', 'POSSIBLE_EXISTENCE_EVIDENCE', "
            "'reference finding', '2026-08-07T12:00:00Z')"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE request_element SET package_id='P1' WHERE request_element_id='RE2'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE source_file SET package_id='P1' WHERE source_file_id='S2'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE occurrence SET source_file_id='S1' WHERE occurrence_id='O2'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE record_reference SET occurrence_id='O1' WHERE record_reference_id='RR2'")

    def test_absence_supporting_corpus_scope_and_state_are_locked(self):
        _, source_id = seed_package_source(self.db, member="production.pdf")
        record_id = create_record(self.db, "Source record", "2" * 64)
        occurrence_id = create_occurrence(
            self.db, record_id, source_id, None, "page:1", "PROVISIONAL"
        )
        reference_id = add_record_reference(
            self.db, occurrence_id, "page:2", "ATTACHMENT", "Referenced item"
        )
        register_reviewer_identity(self.db, "reviewer", "HUMAN")
        record_processing_result(self.db, source_id, "EXTRACT_PDF", b"terminal")
        promote_occurrence_verified(
            self.db, occurrence_id, "reviewer", "2026-08-07T12:00:00Z"
        )
        set_package_completeness(self.db, "P1", "VERIFIED_COMPLETE", "reviewer")
        add_corpus(self.db, "C1", "Current corpus")
        add_corpus_package(self.db, "C1", "P1", "reviewer")
        set_corpus_completeness(self.db, "C1", "VERIFIED_COMPLETE", "reviewer")
        assign_reference_search_corpus(self.db, reference_id, "C1", "reviewer")
        set_reference_absence_scope(
            self.db, reference_id, "NOT_LOCATED_CORPUS", "reviewer"
        )
        self.db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES ('P2', 'P2.pdf', 0)"
        )
        set_package_completeness(self.db, "P2", "VERIFIED_COMPLETE", "reviewer")
        with self.assertRaises(sqlite3.IntegrityError):
            add_corpus_package(self.db, "C1", "P2", "reviewer")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE corpus SET completeness_state='IN_PROGRESS' WHERE corpus_id='C1'")
