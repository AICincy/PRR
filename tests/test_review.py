import sqlite3
import unittest
from pathlib import Path

from metro_forensics.db import initialize
from metro_forensics.evidence import assign_reference_search_corpus, set_reference_absence_scope
from metro_forensics.extract import record_processing_result, record_unsupported_legacy_doc
from metro_forensics.records import open_boundary_review
from metro_forensics.review import (
    add_corpus,
    add_corpus_package,
    change_with_audit,
    corpus_completeness,
    open_review_task,
    promote_finding_verified,
    promote_occurrence_verified,
    register_reviewer_identity,
    resolve_review_task,
    set_corpus_completeness,
    set_package_completeness,
)
from tests.helpers import (
    new_test_db,
    seed_package_source,
    seeded_provisional_finding_db,
    seeded_unlocated_reference_db,
)


TASK_6_BASE_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/migrations/task6_base_schema.sql"
)


class ReviewTests(unittest.TestCase):
    def _register_human(self, db, identity="reviewer"):
        db.execute(
            "INSERT INTO reviewer_identity(reviewer_id, identity_type) VALUES (?, 'HUMAN')",
            (identity,),
        )

    def test_open_material_review_blocks_verified_complete(self):
        """Fail if material questions can be bypassed by package verification."""
        db = new_test_db()
        self._register_human(db)
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','1.pdf',0)"
        )

        open_review_task(
            db, "PACKAGE", "P1", "MATERIAL_AMBIGUITY", "needs source review", True
        )

        with self.assertRaises(ValueError):
            set_package_completeness(db, "P1", "VERIFIED_COMPLETE", "reviewer")

    def test_verification_transition_creates_audit_event(self):
        """Fail if verified status is changed without traceable reviewer evidence."""
        db, finding_id, citation_id = seeded_provisional_finding_db()
        self._register_human(db)

        promote_finding_verified(
            db, finding_id, "reviewer", "2026-08-07T12:00:00Z", [citation_id]
        )

        self.assertEqual(
            "VERIFIED",
            db.execute(
                "SELECT verification_state FROM finding WHERE finding_id=?", (finding_id,)
            ).fetchone()[0],
        )
        self.assertEqual(
            1,
            db.execute(
                "SELECT count(*) FROM audit_event WHERE entity_type='FINDING' "
                "AND entity_id=? AND field_name='verification_state'",
                (finding_id,),
            ).fetchone()[0],
        )
        self.assertEqual(
            1,
            db.execute(
                "SELECT count(*) FROM finding_citation WHERE finding_id=?", (finding_id,)
            ).fetchone()[0],
        )

    def test_not_located_corpus_rejected_while_corpus_incomplete(self):
        """Fail if a corpus-wide absence conclusion ignores corpus completeness."""
        db, reference_id = seeded_unlocated_reference_db()

        self.assertEqual("IN_PROGRESS", corpus_completeness(db))
        with self.assertRaises(ValueError):
            set_reference_absence_scope(db, reference_id, "NOT_LOCATED_CORPUS")

    def test_resolved_task_requires_matching_source_location_and_audit(self):
        """Fail if a material resolution lacks a traceable source citation."""
        db, finding_id, _ = seeded_provisional_finding_db()
        self._register_human(db)
        task_id = open_review_task(
            db, "FINDING", finding_id, "MATERIAL_AMBIGUITY", "needs source review"
        )

        with self.assertRaises(ValueError):
            resolve_review_task(
                db,
                task_id,
                "RESOLVED",
                "reviewer",
                "2026-08-07T12:00:00Z",
                "confirmed conclusion",
                "page:99",
            )

        resolve_review_task(
            db,
            task_id,
            "RESOLVED",
            "reviewer",
            "2026-08-07T12:00:00Z",
            "confirmed conclusion",
            "page:1",
        )
        self.assertEqual(
            ("RESOLVED", "reviewer", "confirmed conclusion"),
            tuple(
                db.execute(
                    "SELECT task_state, reviewer, resolution FROM review_task "
                    "WHERE review_task_id=?",
                    (task_id,),
                ).fetchone()
            ),
        )
        self.assertEqual(
            1,
            db.execute(
                "SELECT count(*) FROM audit_event WHERE entity_type='REVIEW_TASK' "
                "AND entity_id=? AND field_name='task_state'",
                (task_id,),
            ).fetchone()[0],
        )

    def test_unresolved_task_does_not_require_a_resolution_or_source(self):
        """Fail if an honest unresolved review is forced to invent support."""
        db = new_test_db()
        self._register_human(db)
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','1.pdf',0)"
        )
        task_id = open_review_task(
            db, "PACKAGE", "P1", "MATERIAL_AMBIGUITY", "cannot decide"
        )

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
            "UNRESOLVED",
            db.execute(
                "SELECT task_state FROM review_task WHERE review_task_id=?", (task_id,)
            ).fetchone()[0],
        )

    def test_change_with_audit_records_previous_and_new_values(self):
        """Fail if a substantive finding change can evade the append-only ledger."""
        db, finding_id, _ = seeded_provisional_finding_db()
        self._register_human(db)

        change_with_audit(
            db,
            "FINDING",
            finding_id,
            "finding",
            "proposition",
            "Source review changed the conclusion.",
            "corrected after review",
            "reviewer",
        )

        self.assertEqual(
            "Source review changed the conclusion.",
            db.execute("SELECT proposition FROM finding WHERE finding_id=?", (finding_id,)).fetchone()[0],
        )
        self.assertEqual(
            ("No responsive item was located.", "Source review changed the conclusion."),
            tuple(
                db.execute(
                    "SELECT previous_value, new_value FROM audit_event "
                    "WHERE entity_type='FINDING' AND entity_id=? AND field_name='proposition'",
                    (finding_id,),
                ).fetchone()
            ),
        )

    def test_all_verified_packages_make_corpus_verified_complete(self):
        """Fail if one incomplete included package is ignored by the corpus gate."""
        db = new_test_db()
        self._register_human(db)
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','1.pdf',0),('P2','2.pdf',0)"
        )

        set_package_completeness(db, "P1", "VERIFIED_COMPLETE", "reviewer")
        self.assertEqual("IN_PROGRESS", corpus_completeness(db))
        set_package_completeness(db, "P2", "VERIFIED_COMPLETE", "reviewer")

        self.assertEqual("VERIFIED_COMPLETE", corpus_completeness(db))

    def test_initialize_migrates_pre_task_six_review_tasks(self):
        """Fail if existing ledgers cannot add review material and finding scope columns."""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(TASK_6_BASE_SCHEMA.read_text(encoding="utf-8"))

        initialize(db)

        columns = {row[1] for row in db.execute("PRAGMA table_info(review_task)")}
        self.assertTrue({"material", "finding_id"} <= columns)

    def test_migrated_legacy_review_task_can_remain_unresolved(self):
        """Fail if the old resolution CHECK survives the Task 6 migration."""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(TASK_6_BASE_SCHEMA.read_text(encoding="utf-8"))
        initialize(db)
        self._register_human(db)
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','1.pdf',0)"
        )
        task_id = open_review_task(
            db, "PACKAGE", "P1", "MATERIAL_AMBIGUITY", "cannot decide"
        )

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
            "UNRESOLVED",
            db.execute(
                "SELECT task_state FROM review_task WHERE review_task_id=?", (task_id,)
            ).fetchone()[0],
        )

    def test_corpus_absence_ignores_unrelated_incomplete_package(self):
        """Fail if package-scoped search is controlled by an unrelated package."""
        db, reference_id = seeded_unlocated_reference_db()
        self._register_human(db)
        source_id = db.execute(
            "SELECT source_file_id FROM source_file WHERE package_id='P1'"
        ).fetchone()[0]
        occurrence_id = db.execute(
            "SELECT occurrence_id FROM occurrence WHERE source_file_id=?", (source_id,)
        ).fetchone()[0]
        record_processing_result(db, source_id, "EXTRACT_PDF", b"terminal")
        promote_occurrence_verified(
            db, occurrence_id, "reviewer", "2026-08-07T12:00:00Z"
        )
        set_package_completeness(db, "P1", "VERIFIED_COMPLETE", "reviewer")
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P2','2.pdf',0)"
        )
        add_corpus(db, "C1", "narrow corpus")
        add_corpus_package(db, "C1", "P1", "reviewer")
        set_corpus_completeness(db, "C1", "VERIFIED_COMPLETE", "reviewer")
        assign_reference_search_corpus(db, reference_id, "C1", "reviewer")
        set_reference_absence_scope(
            db, reference_id, "NOT_LOCATED_CORPUS", "reviewer"
        )

        self.assertEqual(
            "NOT_LOCATED_CORPUS",
            db.execute(
                "SELECT absence_scope FROM record_reference WHERE reference_id=?", (reference_id,)
            ).fetchone()[0]
        )

    def test_interleaved_package_change_rolls_back_audit_and_state(self):
        """Fail if a state change after the read can be overwritten with a stale audit."""
        db = new_test_db()
        self._register_human(db)
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','1.pdf',0)"
        )
        interleaving_conn = _InterleavingConnection(db, "P1")

        with self.assertRaisesRegex(ValueError, "state changed"):
            set_package_completeness(
                interleaving_conn, "P1", "VERIFIED_COMPLETE", "reviewer"
            )

        self.assertEqual(
            "IN_PROGRESS",
            db.execute(
                "SELECT completeness_state FROM package WHERE package_id='P1'"
            ).fetchone()[0],
        )
        self.assertEqual(
            0,
            db.execute(
                "SELECT count(*) FROM audit_event WHERE entity_type='PACKAGE' AND entity_id='P1'"
            ).fetchone()[0],
        )

    def test_existing_review_task_creators_append_creation_audits(self):
        """Fail if legacy production review creators bypass audit history."""
        boundary_db = new_test_db()
        _, boundary_source_id = seed_package_source(boundary_db)
        boundary_task_id = open_boundary_review(
            boundary_db, boundary_source_id, "pages:10-12", "possible embedded exhibit"
        )
        self.assertEqual(
            1,
            boundary_db.execute(
                "SELECT count(*) FROM audit_event WHERE entity_type='REVIEW_TASK' "
                "AND entity_id=? AND field_name='CREATE'",
                (boundary_task_id,),
            ).fetchone()[0],
        )

        extraction_db = new_test_db()
        _, extraction_source_id = seed_package_source(extraction_db, member="legacy.doc")
        extraction_result = record_unsupported_legacy_doc(extraction_db, extraction_source_id)
        self.assertEqual(
            1,
            extraction_db.execute(
                "SELECT count(*) FROM audit_event WHERE entity_type='REVIEW_TASK' "
                "AND entity_id=? AND field_name='CREATE'",
                (extraction_result.review_task_id,),
            ).fetchone()[0],
        )

    def test_review_operations_require_registered_human_identity(self):
        """Fail if arbitrary reviewer strings can verify an evidentiary finding."""
        db, finding_id, citation_id = seeded_provisional_finding_db()

        with self.assertRaises(ValueError):
            promote_finding_verified(
                db, finding_id, "unregistered", "2026-08-07T12:00:00Z", [citation_id]
            )
        db.execute(
            "INSERT INTO reviewer_identity(reviewer_id, identity_type) "
            "VALUES('automation-worker','AUTOMATION')"
        )
        with self.assertRaises(ValueError):
            promote_finding_verified(
                db, finding_id, "automation-worker", "2026-08-07T12:00:00Z", [citation_id]
            )
        self._register_human(db)

        promote_finding_verified(
            db, finding_id, "reviewer", "2026-08-07T12:00:00Z", [citation_id]
        )
        self.assertEqual(
            ("VERIFIED", "reviewer"),
            tuple(
                db.execute(
                    "SELECT verification_state, verified_by FROM finding WHERE finding_id=?",
                    (finding_id,),
                ).fetchone()
            ),
        )


class _InterleavingConnection:
    """Inject one competing update immediately before the service's package write."""

    def __init__(self, conn, package_id):
        self._conn = conn
        self._package_id = package_id
        self._interleaved = False

    def execute(self, sql, parameters=()):
        normalized = " ".join(sql.split())
        if (
            not self._interleaved
            and normalized.startswith("UPDATE package SET completeness_state=")
        ):
            self._interleaved = True
            self._conn.execute(
                "INSERT INTO audit_event(event_id,entity_type,entity_id,field_name,"
                "previous_value,new_value,changed_at,reason,change_source) VALUES(" 
                "'AE_INTERLEAVED','PACKAGE',?,'completeness_state','IN_PROGRESS',"
                "'REVIEW_REQUIRED','2026-08-07T12:00:00Z','simulated competing review',"
                "'reviewer')",
                (self._package_id,),
            )
            self._conn.execute(
                "UPDATE package SET completeness_state='REVIEW_REQUIRED' WHERE package_id=?",
                (self._package_id,),
            )
        return self._conn.execute(sql, parameters)

    def __getattr__(self, name):
        return getattr(self._conn, name)


if __name__ == "__main__":
    unittest.main()
