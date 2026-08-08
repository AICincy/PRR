import sqlite3
import unittest
from pathlib import Path

from metro_forensics.db import initialize
from metro_forensics.extract import record_processing_result

from metro_forensics.records import (
    create_occurrence,
    create_record,
    link_version_family,
    open_boundary_review,
    resolve_exact_duplicate,
)
from metro_forensics.review import promote_occurrence_verified, register_reviewer_identity
from tests.helpers import new_test_db, seed_package_source


TASK_4_BASE_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/migrations/task4_base_schema.sql"
)


class RecordIdentityTests(unittest.TestCase):
    def test_initialize_migrates_task4_base_record_table_without_losing_rows(self):
        """Fail if an existing ledger lacks content_fingerprint after reinitialization."""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(TASK_4_BASE_SCHEMA.read_text(encoding="utf-8"))
        db.execute(
            "INSERT INTO record(record_id, title_or_description, canonical_identity_basis) "
            "VALUES ('R_LEGACY', 'Pre-migration record', 'legacy source identity')"
        )

        initialize(db)
        record_id = create_record(db, "New record", "a" * 64)

        columns = {row[1] for row in db.execute("PRAGMA table_info(record)")}
        self.assertIn("content_fingerprint", columns)
        self.assertEqual(
            "Pre-migration record",
            db.execute(
                "SELECT title_or_description FROM record WHERE record_id='R_LEGACY'"
            ).fetchone()[0],
        )
        self.assertEqual(
            "a" * 64,
            db.execute(
                "SELECT content_fingerprint FROM record WHERE record_id=?", (record_id,)
            ).fetchone()[0],
        )

    def test_initialize_backfills_derivable_legacy_content_fingerprint(self):
        """Fail if a legacy exact-fingerprint basis remains unsearchable after migration."""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(TASK_4_BASE_SCHEMA.read_text(encoding="utf-8"))
        db.execute(
            "INSERT INTO record(record_id, title_or_description, canonical_identity_basis) "
            "VALUES (?, 'Backfilled record', ?)",
            ("R_BACKFILLED", f"exact content fingerprint: {'B' * 64}"),
        )

        initialize(db)

        self.assertEqual(
            "R_BACKFILLED", resolve_exact_duplicate(db, "b" * 64)
        )
        self.assertEqual(
            "b" * 64,
            db.execute(
                "SELECT content_fingerprint FROM record WHERE record_id='R_BACKFILLED'"
            ).fetchone()[0],
        )

    def test_duplicate_has_one_record_two_occurrences(self):
        """Fail if equal content fingerprints create separate record identities."""
        db = new_test_db()
        _, source_id = seed_package_source(db, source_file_id="S1", member="a.pdf")
        db.execute(
            "INSERT INTO source_file(source_file_id,package_id,archive_member_path,byte_size,sha256,media_type) "
            "VALUES('S2','P1','b.pdf',1,?,'application/pdf')",
            ("1" * 64,),
        )

        record_id = create_record(db, "Same record", "f" * 64)
        duplicate_id = create_record(db, "Different title for same record", "f" * 64)
        create_occurrence(db, record_id, source_id, None, "page:1", "PROVISIONAL")
        create_occurrence(db, record_id, "S2", None, "page:1", "PROVISIONAL")

        self.assertEqual(record_id, duplicate_id)
        self.assertEqual(record_id, resolve_exact_duplicate(db, "f" * 64))
        self.assertEqual(1, db.execute("SELECT count(*) FROM record").fetchone()[0])
        self.assertEqual(2, db.execute("SELECT count(*) FROM occurrence").fetchone()[0])

    def test_case_variants_of_a_digest_resolve_to_one_record(self):
        """Fail if uppercase and lowercase spellings create different identities."""
        db = new_test_db()

        uppercase_id = create_record(db, "Same record", "A" * 64)
        lowercase_id = create_record(db, "Same record", "a" * 64)

        self.assertEqual(uppercase_id, lowercase_id)
        self.assertEqual(1, db.execute("SELECT count(*) FROM record").fetchone()[0])
        self.assertEqual(
            "a" * 64,
            db.execute("SELECT content_fingerprint FROM record").fetchone()[0],
        )

    def test_materially_different_version_stays_separate(self):
        """Fail if nonmatching content fingerprints are automatically collapsed."""
        db = new_test_db()

        older_record_id = create_record(db, "Contract", "a" * 64)
        newer_record_id = create_record(db, "Contract revised", "b" * 64)
        link_version_family(
            db,
            older_record_id,
            newer_record_id,
            "same contract identifier; materially revised bytes",
        )

        self.assertNotEqual(older_record_id, newer_record_id)
        self.assertEqual(2, db.execute("SELECT count(*) FROM record").fetchone()[0])
        self.assertEqual(1, db.execute("SELECT count(*) FROM record_version_link").fetchone()[0])

    def test_ambiguous_boundary_is_review_required(self):
        """Fail if an ambiguous Level 2 boundary is treated as a record automatically."""
        db = new_test_db()
        _, source_id = seed_package_source(db)

        task_id = open_boundary_review(
            db, source_id, "pages:10-12", "possible embedded exhibit"
        )

        row = db.execute(
            "SELECT task_state, reason_code FROM review_task WHERE review_task_id=?",
            (task_id,),
        ).fetchone()
        self.assertEqual(("OPEN", "AMBIGUOUS_LEVEL2_BOUNDARY"), tuple(row))

    def test_verified_occurrence_must_start_provisional(self):
        """Fail if the service can create a VERIFIED occurrence without promotion."""
        db = new_test_db()
        _, source_id = seed_package_source(db)
        record_id = create_record(db, "Verified record", "c" * 64)

        with self.assertRaisesRegex(ValueError, "PROVISIONAL"):
            create_occurrence(
                db, record_id, source_id, None, "page:1", "VERIFIED"
            )

    def test_promoted_occurrence_records_verifier(self):
        """Fail if audited promotion does not retain the human verifier."""
        db = new_test_db()
        _, source_id = seed_package_source(db)
        record_id = create_record(db, "Verified record", "d" * 64)
        register_reviewer_identity(db, "reviewer@example.test", "HUMAN")

        occurrence_id = create_occurrence(
            db,
            record_id,
            source_id,
            None,
            "page:1",
            "PROVISIONAL",
        )
        promote_occurrence_verified(
            db, occurrence_id, "reviewer@example.test", "2026-08-07T12:00:00Z"
        )

        self.assertEqual(
            ("VERIFIED", "reviewer@example.test"),
            tuple(
                db.execute(
                    "SELECT verification_state, verified_by FROM occurrence WHERE occurrence_id=?",
                    (occurrence_id,),
                ).fetchone()
            ),
        )

    def test_occurrence_rejects_unaudited_provisional_to_verified_conflict(self):
        """Fail if a repeated occurrence call silently promotes its verification state."""
        db = new_test_db()
        _, source_id = seed_package_source(db)
        record_id = create_record(db, "Conflicted record", "6" * 64)
        occurrence_id = create_occurrence(
            db, record_id, source_id, None, "page:1", "PROVISIONAL"
        )
        register_reviewer_identity(db, "reviewer@example.test", "HUMAN")

        with self.assertRaisesRegex(ValueError, "PROVISIONAL"):
            create_occurrence(
                db,
                record_id,
                source_id,
                None,
                "page:1",
                "VERIFIED",
                verified_by="reviewer@example.test",
            )

        self.assertEqual(
            ("PROVISIONAL", None),
            tuple(
                db.execute(
                    "SELECT verification_state, verified_by FROM occurrence WHERE occurrence_id=?",
                    (occurrence_id,),
                ).fetchone()
            ),
        )

    def test_occurrence_rejects_conflicting_derivative_provenance(self):
        """Fail if a repeated occurrence call silently drops a different derivative."""
        db = new_test_db()
        _, source_id = seed_package_source(db)
        record_id = create_record(db, "Conflicted record", "7" * 64)
        first_derivative_id = record_processing_result(
            db, source_id, "EXTRACT_PDF", b"first derivative"
        ).derivative_id
        second_derivative_id = record_processing_result(
            db, source_id, "EXTRACT_PDF", b"second derivative"
        ).derivative_id
        occurrence_id = create_occurrence(
            db,
            record_id,
            source_id,
            first_derivative_id,
            "page:1",
            "PROVISIONAL",
        )

        with self.assertRaisesRegex(ValueError, "conflicts"):
            create_occurrence(
                db,
                record_id,
                source_id,
                second_derivative_id,
                "page:1",
                "PROVISIONAL",
            )

        self.assertEqual(
            first_derivative_id,
            db.execute(
                "SELECT derivative_id FROM occurrence WHERE occurrence_id=?",
                (occurrence_id,),
            ).fetchone()[0],
        )

    def test_occurrence_rejects_imprecise_locator(self):
        """Fail if an occurrence can be stored without a traceable Level 1 location."""
        db = new_test_db()
        _, source_id = seed_package_source(db)
        record_id = create_record(db, "Located record", "e" * 64)

        with self.assertRaisesRegex(ValueError, "locator"):
            create_occurrence(
                db, record_id, source_id, None, "somewhere in the file", "PROVISIONAL"
            )

    def test_occurrence_accepts_exact_page_locator(self):
        """Fail if a valid page locator is rejected as untraceable."""
        db = new_test_db()
        _, source_id = seed_package_source(db)
        record_id = create_record(db, "Located record", "9" * 64)

        occurrence_id = create_occurrence(
            db, record_id, source_id, None, "page:1", "PROVISIONAL"
        )

        self.assertEqual(
            "page:1",
            db.execute(
                "SELECT source_locator FROM occurrence WHERE occurrence_id=?",
                (occurrence_id,),
            ).fetchone()[0],
        )
