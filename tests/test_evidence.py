import unittest

from metro_forensics.evidence import (
    add_finding,
    add_request_element,
    link_request_evidence,
    set_reference_absence_scope,
    set_reference_match,
)
from metro_forensics.records import create_occurrence, create_record
from tests.helpers import (
    new_test_db,
    seed_package_source,
    seeded_cross_package_reference_db,
    seeded_reference_db,
)


class EvidenceTests(unittest.TestCase):
    def test_cumulative_findings_do_not_overwrite(self):
        db = new_test_db()
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','1.pdf',0)"
        )
        req = add_request_element(db, "P1", "requested item", 1)
        for code in ("UNPRODUCED", "NONEXISTENCE_ASSERTED", "SUBSTITUTE_PRODUCTION"):
            add_finding(db, code, req, "PROVISIONAL", "HUMAN", code.lower())
        found = {
            row[0]
            for row in db.execute(
                "SELECT finding_type FROM finding WHERE request_element_id=?", (req,)
            )
        }
        self.assertEqual(
            {"UNPRODUCED", "NONEXISTENCE_ASSERTED", "SUBSTITUTE_PRODUCTION"}, found
        )

    def test_probable_reference_match_does_not_close_absence(self):
        db, reference_id, candidate = seeded_reference_db()

        set_reference_match(db, reference_id, "PROBABLE_MATCH", candidate, "reviewer")

        row = db.execute(
            "SELECT resolved_record_id FROM record_reference WHERE reference_id=?",
            (reference_id,),
        ).fetchone()
        self.assertIsNone(row[0])

    def test_elsewhere_match_does_not_credit_original_package(self):
        db, reference_id, record_id = seeded_cross_package_reference_db()

        set_reference_match(db, reference_id, "CONFIRMED_MATCH", record_id, "reviewer")

        scope = db.execute(
            "SELECT absence_scope FROM record_reference WHERE reference_id=?",
            (reference_id,),
        ).fetchone()[0]
        package_one_count = db.execute(
            "SELECT count(*) FROM occurrence o JOIN source_file s USING(source_file_id) "
            "WHERE o.record_id=? AND s.package_id='P1'",
            (record_id,),
        ).fetchone()[0]
        self.assertEqual("LOCATED_ELSEWHERE_CORPUS", scope)
        self.assertEqual(0, package_one_count)

    def test_responsive_evidence_must_be_in_request_package(self):
        db = new_test_db()
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','P1.pdf',0)"
        )
        request_element_id = add_request_element(db, "P1", "requested item", 1)
        _, p2_source_file_id = seed_package_source(db, "P2", "S2", "p2.pdf")
        p2_record_id = create_record(db, "P2 material", "a" * 64)
        p2_occurrence_id = create_occurrence(
            db, p2_record_id, p2_source_file_id, None, "page:1", "PROVISIONAL"
        )

        with self.assertRaisesRegex(ValueError, "RESPONSIVE"):
            link_request_evidence(
                db, request_element_id, p2_occurrence_id, "RESPONSIVE"
            )

        self.assertEqual(
            0,
            db.execute(
                "SELECT count(*) FROM request_element_evidence "
                "WHERE request_element_id=? AND occurrence_id=?",
                (request_element_id, p2_occurrence_id),
            ).fetchone()[0],
        )

    def test_reference_transitions_append_audits_and_failed_or_repeated_calls_do_not(self):
        db, reference_id, candidate_record_id = seeded_cross_package_reference_db()

        set_reference_match(
            db, reference_id, "CONFIRMED_MATCH", candidate_record_id, "reviewer"
        )
        match_events = [
            tuple(event)
            for event in db.execute(
                "SELECT field_name, previous_value, new_value FROM audit_event "
                "WHERE entity_type='RECORD_REFERENCE' AND entity_id=? ORDER BY field_name",
                (reference_id,),
            )
        ]
        self.assertEqual(
            [
                "absence_scope", "match_state", "matched_record_id", "resolved_record_id",
                "verification_state", "verified_by",
            ],
            [event[0] for event in match_events],
        )
        self.assertIn(
            ("match_state", "NO_MATCH_LOCATED", "CONFIRMED_MATCH"), match_events
        )
        event_count = len(match_events)

        set_reference_match(
            db, reference_id, "CONFIRMED_MATCH", candidate_record_id, "reviewer"
        )
        with self.assertRaisesRegex(ValueError, "absence conclusions"):
            set_reference_absence_scope(
                db, reference_id, "NOT_LOCATED_RESPONSIVE_PACKAGE", "reviewer"
            )

        self.assertEqual(
            event_count,
            db.execute(
                "SELECT count(*) FROM audit_event "
                "WHERE entity_type='RECORD_REFERENCE' AND entity_id=?",
                (reference_id,),
            ).fetchone()[0],
        )

        absence_db, absence_reference_id, _ = seeded_reference_db()
        set_reference_absence_scope(
            absence_db, absence_reference_id, "NOT_LOCATED_RESPONSIVE_PACKAGE", "reviewer"
        )
        absence_events = {
            tuple(event)
            for event in absence_db.execute(
                "SELECT field_name, previous_value, new_value FROM audit_event "
                "WHERE entity_type='RECORD_REFERENCE' AND entity_id=?",
                (absence_reference_id,),
            )
        }
        self.assertEqual(
            {
                ("absence_scope", None, "NOT_LOCATED_RESPONSIVE_PACKAGE"),
                ("verification_state", "PROVISIONAL", "VERIFIED"),
                ("verified_by", None, "reviewer"),
            },
            absence_events,
        )

    def test_no_match_rejects_candidate_and_leaves_reference_unchanged(self):
        db, reference_id, candidate_record_id = seeded_reference_db()
        before = tuple(
            db.execute(
                "SELECT match_state, matched_record_id, resolved_record_id, absence_scope "
                "FROM record_reference WHERE reference_id=?",
                (reference_id,),
            ).fetchone()
        )

        with self.assertRaisesRegex(ValueError, "NO_MATCH_LOCATED"):
            set_reference_match(
                db, reference_id, "NO_MATCH_LOCATED", candidate_record_id, "reviewer"
            )

        after = tuple(
            db.execute(
                "SELECT match_state, matched_record_id, resolved_record_id, absence_scope "
                "FROM record_reference WHERE reference_id=?",
                (reference_id,),
            ).fetchone()
        )
        self.assertEqual(before, after)

    def test_invalid_finding_type_creates_no_orphan_audit_event(self):
        db = new_test_db()
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','P1.pdf',0)"
        )
        request_element_id = add_request_element(db, "P1", "requested item", 1)

        with self.assertRaisesRegex(ValueError, "finding_type"):
            add_finding(
                db,
                "NOT_A_FINDING",
                request_element_id,
                "PROVISIONAL",
                "HUMAN",
                "invalid finding",
            )

        self.assertEqual(0, db.execute("SELECT count(*) FROM finding").fetchone()[0])
        self.assertEqual(
            0,
            db.execute(
                "SELECT count(*) FROM audit_event WHERE entity_type='FINDING'"
            ).fetchone()[0],
        )
