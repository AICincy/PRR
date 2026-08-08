import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from metro_forensics.db import initialize
from metro_forensics.evidence import add_citation, add_finding, add_request_element
from metro_forensics.extract import process_source
from metro_forensics.ingest import ingest_manifest
from metro_forensics.records import create_occurrence, create_record
from metro_forensics.review import (
    open_review_task,
    promote_finding_verified,
    promote_occurrence_verified,
    register_reviewer_identity,
    resolve_review_task,
)
from metro_forensics.temporal_legal import add_date_fact, add_temporal_inference
from tests.helpers import new_test_db, seed_package_source


class InitialOccurrenceVerificationTests(unittest.TestCase):
    def test_service_rejects_initial_verified_occurrence_for_registered_human(self):
        """Fail if creation can skip the audited PROVISIONAL-to-VERIFIED transition."""
        db = new_test_db()
        _, source_id = seed_package_source(db)
        record_id = create_record(db, "Record", "a" * 64)
        register_reviewer_identity(db, "reviewer", "HUMAN")

        with self.assertRaisesRegex(ValueError, "PROVISIONAL"):
            create_occurrence(
                db,
                record_id,
                source_id,
                None,
                "page:1",
                "VERIFIED",
                verified_by="reviewer",
            )

        self.assertEqual(0, db.execute("SELECT count(*) FROM occurrence").fetchone()[0])

    def test_raw_sql_rejects_initial_verified_occurrence_for_registered_human(self):
        """Fail if SQLite accepts initial verification without a distinct transition."""
        db = new_test_db()
        _, source_id = seed_package_source(db)
        record_id = create_record(db, "Record", "b" * 64)
        register_reviewer_identity(db, "reviewer", "HUMAN")

        with self.assertRaisesRegex(sqlite3.IntegrityError, "PROVISIONAL"):
            db.execute(
                "INSERT INTO occurrence(occurrence_id,record_id,source_file_id,source_locator,"
                "verification_state,verified_by) VALUES('O_DIRECT',?,?,'page:1','VERIFIED','reviewer')",
                (record_id, source_id),
            )

    def test_audited_human_promotion_remains_available(self):
        """Fail if promotion-only creation prevents the legitimate verified path."""
        db = new_test_db()
        _, source_id = seed_package_source(db)
        record_id = create_record(db, "Record", "c" * 64)
        occurrence_id = create_occurrence(
            db, record_id, source_id, None, "page:1", "PROVISIONAL", verified_by=None
        )
        register_reviewer_identity(db, "reviewer", "HUMAN")

        promote_occurrence_verified(
            db, occurrence_id, "reviewer", "2026-08-07T12:00:00Z"
        )

        self.assertEqual(
            ("VERIFIED", "reviewer"),
            tuple(
                db.execute(
                    "SELECT verification_state,verified_by FROM occurrence WHERE occurrence_id=?",
                    (occurrence_id,),
                ).fetchone()
            ),
        )
        self.assertEqual(
            2,
            db.execute(
                "SELECT count(*) FROM audit_event_use WHERE entity_type='OCCURRENCE' AND entity_id=?",
                (occurrence_id,),
            ).fetchone()[0],
        )


class ReviewTaskDeletionTests(unittest.TestCase):
    def test_raw_sql_cannot_delete_open_material_review_task(self):
        """Fail if deleting the blocking row can bypass completeness authority."""
        db = new_test_db()
        seed_package_source(db)
        task_id = open_review_task(
            db, "PACKAGE", "P1", "MATERIAL_QUESTION", "substantive review remains", True
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "non-deletable"):
            db.execute("DELETE FROM review_task WHERE review_task_id=?", (task_id,))

        self.assertEqual(
            ("OPEN", 1),
            tuple(
                db.execute(
                    "SELECT task_state,material FROM review_task WHERE review_task_id=?",
                    (task_id,),
                ).fetchone()
            ),
        )

    def test_audited_review_resolution_remains_available(self):
        """Fail if the deletion guard prevents legitimate review-state transitions."""
        db = new_test_db()
        seed_package_source(db)
        task_id = open_review_task(
            db, "PACKAGE", "P1", "MATERIAL_QUESTION", "substantive review remains", True
        )
        register_reviewer_identity(db, "reviewer", "HUMAN")

        resolve_review_task(
            db,
            task_id,
            "UNRESOLVED",
            "reviewer",
            "2026-08-07T12:00:00Z",
            "",
            None,
        )

        self.assertEqual(
            ("UNRESOLVED", "reviewer"),
            tuple(
                db.execute(
                    "SELECT task_state,reviewer FROM review_task WHERE review_task_id=?",
                    (task_id,),
                ).fetchone()
            ),
        )


class CanonicalProcessingRootTests(unittest.TestCase):
    def test_process_source_rejects_shadow_evidence_root_before_processing(self):
        """Fail if direct service use can move the immutable-evidence boundary."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            evidence_a = root / "evidence-a"
            evidence_b = root / "evidence-b"
            derivative_root = root / "derivatives"
            for evidence_root in (evidence_a, evidence_b):
                evidence_root.mkdir()
                (evidence_root / "control.pdf").write_bytes(b"same control")
                with zipfile.ZipFile(evidence_root / "production.zip", "w") as archive:
                    archive.writestr("same.txt", b"same member bytes")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "packages": [
                            {
                                "package_id": "P1",
                                "control_record": "control.pdf",
                                "production_archive": "production.zip",
                                "expected_level1_count": 1,
                                "package_status": None,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            db = new_test_db()
            ingest_manifest(db, manifest, evidence_a)
            source_id = db.execute("SELECT source_file_id FROM source_file").fetchone()[0]

            with self.assertRaisesRegex(ValueError, "immutable intake root"):
                process_source(db, source_id, evidence_b, derivative_root)

            self.assertEqual(
                0, db.execute("SELECT count(*) FROM processing_run").fetchone()[0]
            )
            self.assertFalse(derivative_root.exists())


class FindingScopeAuditTests(unittest.TestCase):
    def _scoped_finding(self):
        db = new_test_db()
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','control.pdf',0)"
        )
        request_id = add_request_element(db, "P1", "requested item", 1)
        first_record_id = create_record(db, "First record", "d" * 64)
        second_record_id = create_record(db, "Second record", "e" * 64)
        finding_id = add_finding(
            db,
            "UNPRODUCED",
            request_id,
            "PROVISIONAL",
            "HUMAN",
            "Scoped finding",
            record_id=first_record_id,
        )
        return db, finding_id, first_record_id, second_record_id

    @staticmethod
    def _scope_audit(db, event_id, finding_id, old_record_id, new_record_id, source):
        db.execute(
            "INSERT INTO audit_event(event_id,entity_type,entity_id,field_name,previous_value,"
            "new_value,changed_at,reason,change_source) VALUES(?, 'FINDING', ?, 'record_id', ?, ?, "
            "'2026-08-07T12:00:00Z','change finding record scope',?)",
            (event_id, finding_id, old_record_id, new_record_id, source),
        )

    def test_finding_record_scope_audit_cannot_be_replayed(self):
        """Fail if an old A-to-B scope audit can authorize a later repeated change."""
        db, finding_id, first_record_id, second_record_id = self._scoped_finding()
        register_reviewer_identity(db, "reviewer", "HUMAN")
        self._scope_audit(
            db, "AE_A_B", finding_id, first_record_id, second_record_id, "reviewer"
        )
        db.execute(
            "UPDATE finding SET record_id=? WHERE finding_id=?",
            (second_record_id, finding_id),
        )
        self._scope_audit(
            db, "AE_B_A", finding_id, second_record_id, first_record_id, "reviewer"
        )
        db.execute(
            "UPDATE finding SET record_id=? WHERE finding_id=?",
            (first_record_id, finding_id),
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "unused human"):
            db.execute(
                "UPDATE finding SET record_id=? WHERE finding_id=?",
                (second_record_id, finding_id),
            )

        self.assertEqual(
            2,
            db.execute(
                "SELECT count(*) FROM audit_event_use WHERE event_id IN ('AE_A_B','AE_B_A')"
            ).fetchone()[0],
        )

    def test_automation_audit_cannot_authorize_finding_record_scope(self):
        """Fail if an AUTOMATION identity can authorize a human-only scope decision."""
        db, finding_id, first_record_id, second_record_id = self._scoped_finding()
        register_reviewer_identity(db, "bot", "AUTOMATION")

        with self.assertRaisesRegex(sqlite3.IntegrityError, "registered HUMAN"):
            self._scope_audit(
                db, "AE_BOT", finding_id, first_record_id, second_record_id, "bot"
            )

    def test_unregistered_audit_cannot_authorize_finding_record_scope(self):
        """Fail if an unregistered label can authorize a human-only scope decision."""
        db, finding_id, first_record_id, second_record_id = self._scoped_finding()

        with self.assertRaisesRegex(sqlite3.IntegrityError, "registered HUMAN"):
            self._scope_audit(
                db,
                "AE_UNKNOWN",
                finding_id,
                first_record_id,
                second_record_id,
                "unknown",
            )

    def test_reviewer_identity_type_cannot_be_relabelled(self):
        """Fail if an AUTOMATION identity can later become retroactively HUMAN."""
        db = new_test_db()
        register_reviewer_identity(db, "bot", "AUTOMATION")

        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            db.execute(
                "UPDATE reviewer_identity SET identity_type='HUMAN' WHERE reviewer_id='bot'"
            )

    def test_reviewer_identity_cannot_be_deleted_and_reinserted(self):
        """Fail if deleting an identity permits later type replacement under the same ID."""
        db = new_test_db()
        register_reviewer_identity(db, "bot", "AUTOMATION")

        with self.assertRaisesRegex(sqlite3.IntegrityError, "non-deletable"):
            db.execute("DELETE FROM reviewer_identity WHERE reviewer_id='bot'")

    def test_reviewer_identity_cannot_be_relabelled_by_replace(self):
        """Fail if SQLite replacement semantics bypass immutable identity type."""
        db = new_test_db()
        register_reviewer_identity(db, "bot", "AUTOMATION")

        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            db.execute(
                "INSERT OR REPLACE INTO reviewer_identity(reviewer_id,identity_type) "
                "VALUES('bot','HUMAN')"
            )

        self.assertEqual(
            "AUTOMATION",
            db.execute(
                "SELECT identity_type FROM reviewer_identity WHERE reviewer_id='bot'"
            ).fetchone()[0],
        )

    def test_preexisting_unregistered_audit_cannot_gain_authority_after_migration(self):
        """Fail if a pre-wave unknown audit becomes human-authorized after registration."""
        db, finding_id, first_record_id, second_record_id = self._scoped_finding()
        db.execute("DROP TRIGGER finding_audit_requires_registered_human_insert")
        db.execute("DELETE FROM schema_migration WHERE version=7")
        self._scope_audit(
            db,
            "AE_PRE_WAVE_UNKNOWN",
            finding_id,
            first_record_id,
            second_record_id,
            "later-reviewer",
        )

        initialize(db)
        register_reviewer_identity(db, "later-reviewer", "HUMAN")

        with self.assertRaisesRegex(sqlite3.IntegrityError, "unused human"):
            db.execute(
                "UPDATE finding SET record_id=? WHERE finding_id=?",
                (second_record_id, finding_id),
            )


class PossessionSupportTests(unittest.TestCase):
    def _verified_existence_fixture(self, finding_type):
        db = new_test_db()
        _, source_id = seed_package_source(db)
        request_id = add_request_element(db, "P1", "requested item", 1)
        finding_id = add_finding(
            db,
            finding_type,
            request_id,
            "PROVISIONAL",
            "HUMAN",
            "The record is identified as existing.",
        )
        citation_id = add_citation(db, source_id, None, "page:1")
        register_reviewer_identity(db, "reviewer", "HUMAN")
        promote_finding_verified(
            db, finding_id, "reviewer", "2026-08-07T12:00:00Z", [citation_id]
        )
        date_fact_id = add_date_fact(
            db,
            "RECORD",
            "R_TARGET",
            "RESPONSE_DATE",
            "2026-08-07",
            "2026-08-07",
            "DAY",
            citation_id,
        )
        return db, finding_id, date_fact_id

    def test_existence_only_finding_cannot_support_possession_service(self):
        """Fail if verified existence is collapsed into possession at response."""
        for finding_type in (
            "STRONG_EXISTENCE_EVIDENCE",
            "POSSIBLE_EXISTENCE_EVIDENCE",
        ):
            with self.subTest(finding_type=finding_type):
                db, finding_id, date_fact_id = self._verified_existence_fixture(
                    finding_type
                )

                with self.assertRaisesRegex(ValueError, "possession-supporting"):
                    add_temporal_inference(
                        db,
                        "RECORD",
                        "R_TARGET",
                        "POSSESSED_AT_RESPONSE",
                        [date_fact_id],
                        supporting_finding_ids=[finding_id],
                        reviewer="reviewer",
                    )

                self.assertEqual(
                    0, db.execute("SELECT count(*) FROM temporal_inference").fetchone()[0]
                )

    def test_existence_only_finding_cannot_support_possession_raw_sql(self):
        """Fail if SQLite permits existence-only support for verified possession."""
        for finding_type in (
            "STRONG_EXISTENCE_EVIDENCE",
            "POSSIBLE_EXISTENCE_EVIDENCE",
        ):
            with self.subTest(finding_type=finding_type):
                db, finding_id, _ = self._verified_existence_fixture(finding_type)

                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "possession-supporting"
                ):
                    db.execute(
                        "INSERT INTO temporal_inference(temporal_inference_id,entity_type,"
                        "entity_id,proposition,inference_type,verification_state,verified_by,"
                        "possession_supporting_finding_id) VALUES(?, 'RECORD','R_TARGET',"
                        "'POSSESSED_AT_RESPONSE','POSSESSED_AT_RESPONSE','VERIFIED','reviewer',?)",
                        (f"TI_{finding_type}", finding_id),
                    )

    def test_reinitialize_restores_possession_boundary_trigger(self):
        """Fail if a recorded migration leaves a missing SQLite possession guard missing."""
        db, finding_id, _ = self._verified_existence_fixture(
            "STRONG_EXISTENCE_EVIDENCE"
        )
        db.execute("DROP TRIGGER temporal_possession_requires_controlled_support_type")

        initialize(db)

        with self.assertRaisesRegex(sqlite3.IntegrityError, "possession-supporting"):
            db.execute(
                "INSERT INTO temporal_inference(temporal_inference_id,entity_type,entity_id,"
                "proposition,inference_type,verification_state,verified_by,"
                "possession_supporting_finding_id) VALUES('TI_RESTORED','RECORD','R_TARGET',"
                "'POSSESSED_AT_RESPONSE','POSSESSED_AT_RESPONSE','VERIFIED','reviewer',?)",
                (finding_id,),
            )


if __name__ == "__main__":
    unittest.main()
