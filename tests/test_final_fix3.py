import sqlite3
import unittest

from metro_forensics.db import initialize
from metro_forensics.records import create_occurrence, create_record
from metro_forensics.review import (
    open_review_task,
    promote_occurrence_verified,
    register_reviewer_identity,
    resolve_review_task,
)
from tests.helpers import new_test_db, seed_package_source


class OccurrenceIdempotencyTests(unittest.TestCase):
    def test_repeat_create_after_legitimate_promotion_is_idempotent(self):
        db = new_test_db()
        _, source_id = seed_package_source(db)
        record_id = create_record(db, "Promoted record", "a" * 64)
        occurrence_id = create_occurrence(
            db, record_id, source_id, None, "page:1", "PROVISIONAL"
        )
        register_reviewer_identity(db, "reviewer", "HUMAN")
        promote_occurrence_verified(
            db, occurrence_id, "reviewer", "2026-08-07T12:00:00Z"
        )

        repeated_id = create_occurrence(
            db, record_id, source_id, None, "page:1", "PROVISIONAL"
        )

        self.assertEqual(occurrence_id, repeated_id)
        self.assertEqual(
            ("VERIFIED", "reviewer"),
            tuple(
                db.execute(
                    "SELECT verification_state, verified_by FROM occurrence "
                    "WHERE occurrence_id=?",
                    (occurrence_id,),
                ).fetchone()
            ),
        )


class OperationalMetadataAuthorityTests(unittest.TestCase):
    def test_intake_root_cannot_be_replaced_by_primary_key_conflict(self):
        db = new_test_db()
        db.execute(
            "INSERT INTO operational_metadata(key,value) VALUES('intake_evidence_root','/trusted')"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT OR REPLACE INTO operational_metadata(key,value) "
                "VALUES('intake_evidence_root','/shadow')"
            )

        self.assertEqual(
            "/trusted",
            db.execute(
                "SELECT value FROM operational_metadata WHERE key='intake_evidence_root'"
            ).fetchone()[0],
        )

    def test_intake_root_cannot_be_erased_by_rowid_replace(self):
        db = new_test_db()
        db.execute(
            "INSERT INTO operational_metadata(key,value) VALUES('intake_evidence_root','/trusted')"
        )
        root_rowid = db.execute(
            "SELECT rowid FROM operational_metadata WHERE key='intake_evidence_root'"
        ).fetchone()[0]

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT OR REPLACE INTO operational_metadata(rowid,key,value) VALUES(?,?,?)",
                (root_rowid, "decoy", "x"),
            )

        self.assertEqual(
            ("intake_evidence_root", "/trusted"),
            tuple(
                db.execute(
                    "SELECT key,value FROM operational_metadata WHERE rowid=?", (root_rowid,)
                ).fetchone()
            ),
        )

    def test_reinitialize_restores_intake_root_replace_guard(self):
        db = new_test_db()
        db.execute(
            "INSERT INTO operational_metadata(key,value) VALUES('intake_evidence_root','/trusted')"
        )
        db.execute("DROP TRIGGER IF EXISTS operational_metadata_intake_root_no_replace")
        initialize(db)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT OR REPLACE INTO operational_metadata(key,value) "
                "VALUES('intake_evidence_root','/shadow')"
            )

    def test_reinitialize_replaces_stale_same_name_intake_root_guard(self):
        db = new_test_db()
        db.execute(
            "INSERT INTO operational_metadata(key,value) VALUES('intake_evidence_root','/trusted')"
        )
        db.execute("DROP TRIGGER operational_metadata_intake_root_no_replace")
        db.execute(
            "CREATE TRIGGER operational_metadata_intake_root_no_replace "
            "BEFORE INSERT ON operational_metadata WHEN 0 "
            "BEGIN SELECT 1; END"
        )

        initialize(db)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT OR REPLACE INTO operational_metadata(key,value) "
                "VALUES('intake_evidence_root','/shadow')"
            )
        self.assertEqual(
            "/trusted",
            db.execute(
                "SELECT value FROM operational_metadata WHERE key='intake_evidence_root'"
            ).fetchone()[0],
        )

    def test_reinitialize_replaces_stale_same_name_operational_metadata_update_guard(self):
        db = new_test_db()
        db.execute(
            "INSERT INTO operational_metadata(key,value) VALUES('intake_evidence_root','/trusted')"
        )
        db.execute("DROP TRIGGER operational_metadata_is_immutable")
        db.execute(
            "CREATE TRIGGER operational_metadata_is_immutable "
            "BEFORE UPDATE ON operational_metadata WHEN 0 BEGIN SELECT 1; END"
        )

        initialize(db)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE operational_metadata SET value='/shadow' "
                "WHERE key='intake_evidence_root'"
            )
        self.assertEqual(
            "/trusted",
            db.execute(
                "SELECT value FROM operational_metadata WHERE key='intake_evidence_root'"
            ).fetchone()[0],
        )

    def test_reinitialize_replaces_stale_same_name_operational_metadata_delete_guard(self):
        db = new_test_db()
        db.execute(
            "INSERT INTO operational_metadata(key,value) VALUES('intake_evidence_root','/trusted')"
        )
        db.execute("DROP TRIGGER operational_metadata_cannot_be_deleted")
        db.execute(
            "CREATE TRIGGER operational_metadata_cannot_be_deleted "
            "BEFORE DELETE ON operational_metadata WHEN 0 BEGIN SELECT 1; END"
        )

        initialize(db)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("DELETE FROM operational_metadata WHERE key='intake_evidence_root'")
        self.assertEqual(
            "/trusted",
            db.execute(
                "SELECT value FROM operational_metadata WHERE key='intake_evidence_root'"
            ).fetchone()[0],
        )


class AuditAppendOnlyTests(unittest.TestCase):
    def _consumed_event(self):
        db = new_test_db()
        db.execute(
            "INSERT INTO audit_event(event_id,entity_type,entity_id,field_name,changed_at,reason,change_source) "
            "VALUES('AE1','TEST','E1','state','2026-08-07T12:00:00Z','test','system')"
        )
        db.execute(
            "INSERT INTO audit_event_use(event_id,entity_type,entity_id,field_name) "
            "VALUES('AE1','TEST','E1','state')"
        )
        return db

    def test_consumed_audit_event_cannot_be_replaced(self):
        db = self._consumed_event()
        before = tuple(db.execute("SELECT * FROM audit_event WHERE event_id='AE1'").fetchone())

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT OR REPLACE INTO audit_event(" 
                "event_id,entity_type,entity_id,field_name,changed_at,reason,change_source" 
                ") VALUES('AE1','TEST','FORGED','state','2026-08-07T13:00:00Z','forged','system')"
            )

        self.assertEqual(
            before, tuple(db.execute("SELECT * FROM audit_event WHERE event_id='AE1'").fetchone())
        )

    def test_audit_consumption_binding_cannot_be_replaced(self):
        db = self._consumed_event()
        before = tuple(
            db.execute("SELECT * FROM audit_event_use WHERE event_id='AE1'").fetchone()
        )

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT OR REPLACE INTO audit_event_use(event_id,entity_type,entity_id,field_name) "
                "VALUES('AE1','TEST','FORGED','other')"
            )

        self.assertEqual(
            before,
            tuple(db.execute("SELECT * FROM audit_event_use WHERE event_id='AE1'").fetchone()),
        )

    def test_migration_rejects_mismatched_existing_audit_consumption(self):
        db = new_test_db()
        db.execute("DROP TRIGGER IF EXISTS audit_event_use_matches_event")
        db.execute("DELETE FROM schema_migration WHERE version=8")
        db.execute(
            "INSERT INTO audit_event(event_id,entity_type,entity_id,field_name,changed_at,reason,change_source) "
            "VALUES('AE_BAD','TEST','E1','state','2026-08-07T12:00:00Z','test','system')"
        )
        db.execute(
            "INSERT INTO audit_event_use(event_id,entity_type,entity_id,field_name) "
            "VALUES('AE_BAD','TEST','FORGED','other')"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            initialize(db)
        self.assertIsNone(
            db.execute("SELECT 1 FROM schema_migration WHERE version=8").fetchone()
        )

    def test_reinitialize_replaces_stale_same_name_audit_replace_guard(self):
        db = self._consumed_event()
        db.execute("DROP TRIGGER audit_event_no_replace")
        db.execute(
            "CREATE TRIGGER audit_event_no_replace BEFORE INSERT ON audit_event WHEN 0 "
            "BEGIN SELECT 1; END"
        )

        initialize(db)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT OR REPLACE INTO audit_event("
                "event_id,entity_type,entity_id,field_name,changed_at,reason,change_source"
                ") VALUES('AE1','TEST','FORGED','state','2026-08-07T13:00:00Z',"
                "'forged','system')"
            )
        self.assertEqual(
            "E1", db.execute("SELECT entity_id FROM audit_event WHERE event_id='AE1'").fetchone()[0]
        )

    def test_reinitialize_rejects_mismatched_audit_use_with_recorded_migration_8(self):
        db = self._consumed_event()
        db.execute("DROP TRIGGER audit_event_use_no_update")
        db.execute(
            "UPDATE audit_event_use SET entity_id='FORGED',field_name='other' WHERE event_id='AE1'"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            initialize(db)

    def test_reinitialize_does_not_forget_deleted_audit_consumption(self):
        db = self._consumed_event()
        db.execute("DROP TRIGGER audit_event_use_no_delete")
        try:
            db.execute("DELETE FROM audit_event_use WHERE event_id='AE1'")
        except sqlite3.IntegrityError:
            # A durable relational anchor may block erasure even after the
            # trigger is removed. That is stronger than repairing it later.
            pass

        initialize(db)

        self.assertIsNotNone(
            db.execute("SELECT 1 FROM audit_event_use WHERE event_id='AE1'").fetchone()
        )
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO audit_event_use(event_id,entity_type,entity_id,field_name) "
                "VALUES('AE1','TEST','E1','state')"
            )


class ReviewTaskAuthorityTests(unittest.TestCase):
    @staticmethod
    def _second_package(db):
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P2','control-2.pdf',0)"
        )

    def test_review_task_insert_or_replace_cannot_move_existing_blocker(self):
        db = new_test_db()
        _, source_id = seed_package_source(db)
        self._second_package(db)
        task_id = open_review_task(db, "SOURCE_FILE", source_id, "CHECK", "needs review")

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT OR REPLACE INTO review_task(" 
                "review_task_id,package_id,task_type,reason_code,task_state,material,concern" 
                ") VALUES(?, 'P2','FORENSIC_REVIEW','CHECK','OPEN',1,'forged')",
                (task_id,),
            )

        self.assertEqual(
            ("P1", "OPEN", 1),
            tuple(
                db.execute(
                    "SELECT package_id,task_state,material FROM review_task WHERE review_task_id=?",
                    (task_id,),
                ).fetchone()
            ),
        )

    def test_review_task_materiality_cannot_be_downgraded_by_raw_update(self):
        db = new_test_db()
        _, source_id = seed_package_source(db)
        task_id = open_review_task(db, "SOURCE_FILE", source_id, "CHECK", "needs review")
        register_reviewer_identity(db, "reviewer", "HUMAN")
        resolve_review_task(
            db,
            task_id,
            "UNRESOLVED",
            "reviewer",
            "2026-08-07T12:00:00Z",
            "still unresolved",
            None,
        )

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE review_task SET material=0 WHERE review_task_id=?", (task_id,))
        self.assertEqual(
            ("UNRESOLVED", 1),
            tuple(
                db.execute(
                    "SELECT task_state,material FROM review_task WHERE review_task_id=?", (task_id,)
                ).fetchone()
            ),
        )

    def test_review_task_scope_cannot_be_rewritten(self):
        db = new_test_db()
        _, source_id = seed_package_source(db)
        self._second_package(db)
        task_id = open_review_task(db, "SOURCE_FILE", source_id, "CHECK", "needs review")

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE review_task SET package_id='P2' WHERE review_task_id=?", (task_id,))
        self.assertEqual(
            "P1",
            db.execute(
                "SELECT package_id FROM review_task WHERE review_task_id=?", (task_id,)
            ).fetchone()[0],
        )

    def test_reinitialize_replaces_stale_same_name_review_authority_guard(self):
        db = new_test_db()
        _, source_id = seed_package_source(db)
        self._second_package(db)
        task_id = open_review_task(db, "SOURCE_FILE", source_id, "CHECK", "needs review")
        db.execute("DROP TRIGGER review_task_authority_is_immutable")
        db.execute(
            "CREATE TRIGGER review_task_authority_is_immutable "
            "BEFORE UPDATE ON review_task WHEN 0 BEGIN SELECT 1; END"
        )

        initialize(db)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE review_task SET package_id='P2' WHERE review_task_id=?", (task_id,))
        self.assertEqual(
            "P1",
            db.execute(
                "SELECT package_id FROM review_task WHERE review_task_id=?", (task_id,)
            ).fetchone()[0],
        )

    def test_reinitialize_replaces_stale_same_name_review_transition_gate(self):
        db = new_test_db()
        _, source_id = seed_package_source(db)
        task_id = open_review_task(db, "SOURCE_FILE", source_id, "CHECK", "needs review")
        db.execute("DROP TRIGGER review_task_transition_requires_unused_human_audit")
        db.execute(
            "CREATE TRIGGER review_task_transition_requires_unused_human_audit "
            "BEFORE UPDATE ON review_task WHEN 0 BEGIN SELECT 1; END"
        )

        initialize(db)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE review_task SET task_state='RESOLVED',reviewer='ghost',"
                "resolved_at='x',resolution='forged' WHERE review_task_id=?",
                (task_id,),
            )
        self.assertEqual(
            "OPEN",
            db.execute(
                "SELECT task_state FROM review_task WHERE review_task_id=?", (task_id,)
            ).fetchone()[0],
        )

    def test_reinitialize_replaces_stale_same_name_review_transition_consumer(self):
        db = new_test_db()
        _, source_id = seed_package_source(db)
        task_id = open_review_task(db, "SOURCE_FILE", source_id, "CHECK", "needs review")
        register_reviewer_identity(db, "reviewer", "HUMAN")
        db.execute("DROP TRIGGER review_task_transition_consumes_audits")
        db.execute(
            "CREATE TRIGGER review_task_transition_consumes_audits "
            "AFTER UPDATE ON review_task WHEN 0 BEGIN SELECT 1; END"
        )

        initialize(db)
        resolve_review_task(
            db,
            task_id,
            "UNRESOLVED",
            "reviewer",
            "2026-08-07T15:00:00Z",
            "still unresolved",
            None,
        )

        unused_transition_audits = db.execute(
            """
            SELECT count(*)
            FROM audit_event AS ae
            LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
            WHERE ae.entity_type='REVIEW_TASK'
              AND ae.entity_id=?
              AND ae.field_name<>'CREATE'
              AND used.event_id IS NULL
            """,
            (task_id,),
        ).fetchone()[0]
        self.assertEqual(0, unused_transition_audits)

    def test_review_task_cannot_start_resolved_or_without_creation_audit(self):
        db = new_test_db()
        seed_package_source(db)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO review_task(" 
                "review_task_id,package_id,task_type,task_state,material,concern," 
                "reviewer,resolved_at,resolution" 
                ") VALUES('RT_FORGED','P1','FORENSIC_REVIEW','RESOLVED',1,'forged'," 
                "'nobody','2026-08-07T12:00:00Z','forged')"
            )

    def test_reinitialize_reanchors_existing_review_tasks_when_identity_table_is_missing(self):
        db = new_test_db()
        _, source_id = seed_package_source(db)
        self._second_package(db)
        task_id = open_review_task(db, "SOURCE_FILE", source_id, "CHECK", "needs review")

        # Reinitialization must restore both the guard schema and its load-bearing
        # data even when migration 8 has already been recorded.
        db.execute("DROP TABLE review_task_identity")
        initialize(db)
        self.assertIsNotNone(
            db.execute(
                "SELECT 1 FROM review_task_identity WHERE review_task_id=?", (task_id,)
            ).fetchone()
        )

        # A second creation-shaped audit must not reopen INSERT OR REPLACE as a
        # way to erase or move the existing review obligation.
        db.execute(
            "INSERT INTO audit_event("
            "event_id,entity_type,entity_id,field_name,previous_value,new_value,"
            "changed_at,reason,change_source"
            ") VALUES('AE_REPLAY','REVIEW_TASK',?,'CREATE',NULL,'OPEN',"
            "'2026-08-07T13:00:00Z','forged replay','system')",
            (task_id,),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT OR REPLACE INTO review_task("
                "review_task_id,package_id,task_type,reason_code,task_state,material,concern"
                ") VALUES(?, 'P2','FORENSIC_REVIEW','CHECK','OPEN',1,'forged')",
                (task_id,),
            )


if __name__ == "__main__":
    unittest.main()
