import unittest
import sqlite3
from pathlib import Path

from metro_forensics.db import initialize
from metro_forensics.temporal_legal import (
    add_date_fact,
    add_temporal_inference,
    create_legal_assessment,
    finalize_legal_assessment,
)
from tests.helpers import seeded_citation_db, seeded_provisional_finding_db


TASK_6_BASE_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / ".superpowers/sdd/2026-08-07-metro-forensic-ledger-implementation/task-6-base/metro_forensics/schema.sql"
)
TASK_7_BASE_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / ".superpowers/sdd/2026-08-07-metro-forensic-ledger-implementation/task-7-base/metro_forensics/schema.sql"
)


class TemporalLegalTests(unittest.TestCase):
    def test_existence_inference_does_not_create_possession_inference(self):
        db, citation_id = seeded_citation_db()
        date_id = add_date_fact(
            db, "RECORD", "R1", "RECORD_DATE", "2026-07-01", "2026-07-01", "DAY", citation_id
        )
        add_temporal_inference(db, "RECORD", "R1", "EXISTED_BEFORE_RESPONSE", [date_id])

        kinds = {r[0] for r in db.execute("SELECT inference_type FROM temporal_inference")}
        self.assertEqual({"EXISTED_BEFORE_RESPONSE"}, kinds)
        self.assertNotIn("POSSESSED_AT_RESPONSE", kinds)

    def test_partial_and_conflicting_dates_are_preserved(self):
        db, citation_id = seeded_citation_db()
        add_date_fact(db, "RECORD", "R1", "RECORD_DATE", "July 2026", "2026-07", "MONTH", citation_id)
        add_date_fact(db, "RECORD", "R1", "RECORD_DATE", "2026 or 2025", None, "CONFLICTING", citation_id)

        rows = list(db.execute("SELECT raw_value, normalized_value, precision FROM date_fact ORDER BY rowid"))
        self.assertEqual(("July 2026", "2026-07", "MONTH"), tuple(rows[0]))
        self.assertEqual(("2026 or 2025", None, "CONFLICTING"), tuple(rows[1]))

    def test_conflicting_date_rejects_a_normalized_exact_day(self):
        db, citation_id = seeded_citation_db()

        with self.assertRaisesRegex(ValueError, "CONFLICTING"):
            add_date_fact(
                db,
                "RECORD",
                "R1",
                "RECORD_DATE",
                "2026 or 2025",
                "2026-01-01",
                "CONFLICTING",
                citation_id,
            )

    def test_month_normalization_must_match_source_text(self):
        db, citation_id = seeded_citation_db()

        with self.assertRaisesRegex(ValueError, "raw_value"):
            add_date_fact(
                db,
                "RECORD",
                "R1",
                "RECORD_DATE",
                "July 2026",
                "2025-01",
                "MONTH",
                citation_id,
            )

        self.assertEqual(0, db.execute("SELECT count(*) FROM date_fact").fetchone()[0])

    def test_invalid_calendar_normalization_is_rejected(self):
        db, citation_id = seeded_citation_db()

        with self.assertRaisesRegex(ValueError, "normalized_value"):
            add_date_fact(
                db,
                "RECORD",
                "R1",
                "RECORD_DATE",
                "2026-02-30",
                "2026-02-30",
                "DAY",
                citation_id,
            )

        self.assertEqual(0, db.execute("SELECT count(*) FROM date_fact").fetchone()[0])

    def test_temporal_dates_cannot_alone_support_possession_inference(self):
        db, citation_id = seeded_citation_db()
        date_id = add_date_fact(
            db, "RECORD", "R1", "RECORD_DATE", "2026", "2026", "YEAR", citation_id
        )

        with self.assertRaisesRegex(ValueError, "possession"):
            add_temporal_inference(db, "RECORD", "R1", "POSSESSED_AT_RESPONSE", [date_id])

    def test_final_legal_assessment_rejects_provisional_finding(self):
        db, finding_id, _ = seeded_provisional_finding_db()
        assessment = create_legal_assessment(
            db,
            "Was the legal duty satisfied?",
            "draft conclusion",
            [finding_id],
            [("STATUTE", "Ohio Rev. Code § 149.43")],
        )

        with self.assertRaises(ValueError):
            finalize_legal_assessment(db, assessment)

    def test_initialize_backfills_legacy_primary_authority_link(self):
        db = sqlite3.connect(":memory:")
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(TASK_6_BASE_SCHEMA.read_text(encoding="utf-8"))
        db.execute(
            "INSERT INTO legal_authority(legal_authority_id, authority_type, citation) "
            "VALUES ('A1', 'STATUTE', 'Example statute')"
        )
        db.execute(
            "INSERT INTO legal_assessment(legal_assessment_id, legal_question, conclusion, "
            "primary_legal_authority_id) VALUES ('LA1', 'Question?', 'Draft', 'A1')"
        )

        initialize(db)

        self.assertEqual(
            "LA1",
            db.execute(
                "SELECT legal_assessment_id FROM legal_authority WHERE legal_authority_id='A1'"
            ).fetchone()[0],
        )

    def test_initialize_backfills_legacy_dates_and_inferences_then_restores_immutability(self):
        db = _seed_task_7_legacy_temporal_ledger()

        initialize(db)

        self.assertEqual(
            ("July 2026", None, "MONTH"),
            tuple(
                db.execute(
                    "SELECT raw_value, normalized_value, precision FROM date_fact WHERE date_fact_id='DF1'"
                ).fetchone()
            ),
        )
        self.assertEqual(
            "EXISTED_BEFORE_RESPONSE",
            db.execute(
                "SELECT inference_type FROM temporal_inference WHERE temporal_inference_id='TI1'"
            ).fetchone()[0],
        )
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE date_fact SET raw_value='August 2026' WHERE date_fact_id='DF1'")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE temporal_inference SET inference_type='CHANGED' WHERE temporal_inference_id='TI1'"
            )


def _seed_task_7_legacy_temporal_ledger() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(TASK_7_BASE_SCHEMA.read_text(encoding="utf-8"))
    db.execute(
        "INSERT INTO package(package_id, control_record_path, expected_level1_count) "
        "VALUES ('P1', 'P1.pdf', 0)"
    )
    db.execute(
        "INSERT INTO source_file(source_file_id, package_id, archive_member_path, byte_size, sha256, media_type) "
        "VALUES ('S1', 'P1', 'record.pdf', 1, ?, 'application/pdf')",
        ("0" * 64,),
    )
    db.execute(
        "INSERT INTO record(record_id, title_or_description, canonical_identity_basis) "
        "VALUES ('R1', 'Record', 'legacy identity')"
    )
    db.execute(
        "INSERT INTO occurrence(occurrence_id, record_id, source_file_id, source_locator) "
        "VALUES ('O1', 'R1', 'S1', 'page:1')"
    )
    db.execute(
        "INSERT INTO evidence_citation(evidence_citation_id, source_file_id, occurrence_id, locator) "
        "VALUES ('EC1', 'S1', 'O1', 'page:1')"
    )
    db.execute(
        "INSERT INTO date_fact(date_fact_id, entity_type, entity_id, date_role, value_text, "
        "precision_qualifier, evidence_citation_id) "
        "VALUES ('DF1', 'RECORD', 'R1', 'RECORD_DATE', 'July 2026', 'MONTH', 'EC1')"
    )
    db.execute(
        "INSERT INTO temporal_inference(temporal_inference_id, entity_type, entity_id, proposition, "
        "supporting_citation_id) "
        "VALUES ('TI1', 'RECORD', 'R1', 'EXISTED_BEFORE_RESPONSE', 'EC1')"
    )
    db.execute(
        "INSERT INTO temporal_inference_date_fact(temporal_inference_id, date_fact_id) "
        "VALUES ('TI1', 'DF1')"
    )
    return db
