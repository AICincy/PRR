import sqlite3
import json
import inspect
import tempfile
import unittest
from pathlib import Path
import zipfile

from metro_forensics import evidence as evidence_module
from metro_forensics import review as review_module
from metro_forensics.db import initialize
from metro_forensics.evidence import (
    add_citation,
    add_finding,
    add_request_element,
    link_request_evidence,
    set_reference_match,
)
from metro_forensics.records import create_occurrence, create_record
from metro_forensics.extract import process_source, record_processing_result
from metro_forensics.ingest import ingest_manifest
from metro_forensics.review import change_with_audit, set_package_completeness
from metro_forensics.temporal_legal import (
    add_date_fact,
    add_temporal_inference,
    create_legal_assessment,
    finalize_legal_assessment,
)
from tests.helpers import new_test_db, seed_package_source


class VerificationAndLegalExploitTests(unittest.TestCase):
    def test_finding_creation_always_starts_provisional(self):
        """Fail if add_finding remains a second path into VERIFIED material state."""
        db = new_test_db()
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','P1.pdf',0)"
        )
        request_element_id = add_request_element(db, "P1", "requested item", 1)

        with self.assertRaisesRegex(ValueError, "PROVISIONAL"):
            add_finding(
                db,
                "UNPRODUCED",
                request_element_id,
                "VERIFIED",
                "HUMAN",
                "Unsupported direct verification",
                verified_by="reviewer",
            )

        self.assertEqual(0, db.execute("SELECT count(*) FROM finding").fetchone()[0])

    def test_verified_occurrence_creation_is_rejected_before_reviewer_attribution(self):
        """Fail if any verifier label can bypass promotion-only occurrence verification."""
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        record_id = create_record(db, "Record", "a" * 64)

        with self.assertRaisesRegex(ValueError, "PROVISIONAL"):
            create_occurrence(
                db,
                record_id,
                source_file_id,
                None,
                "page:1",
                "VERIFIED",
                verified_by="unregistered",
            )

    def test_citation_rejects_imprecise_locator(self):
        """Fail if a citation can claim source support without an exact reproducible locator."""
        db = new_test_db()
        _, source_file_id = seed_package_source(db)

        with self.assertRaisesRegex(ValueError, "exact"):
            add_citation(db, source_file_id, None, "somewhere near the end")

        self.assertEqual(0, db.execute("SELECT count(*) FROM evidence_citation").fetchone()[0])

    def test_direct_sql_cannot_insert_unsupported_verified_finding(self):
        """Fail if a fabricated human label and old-style audit can manufacture VERIFIED state."""
        db = new_test_db()
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','P1.pdf',0)"
        )
        db.execute(
            "INSERT INTO reviewer_identity(reviewer_id,identity_type) VALUES('reviewer','HUMAN')"
        )
        db.execute(
            "INSERT INTO audit_event(event_id,entity_type,entity_id,field_name,changed_at,reason,change_source) "
            "VALUES('AE1','FINDING','F1','CREATE','2026-08-07T12:00:00Z','fabricate','reviewer')"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO finding(finding_id,package_id,finding_type,proposition,"
                "verification_state,created_by,verified_by,created_at) "
                "VALUES('F1','P1','UNPRODUCED','unsupported','VERIFIED','human','reviewer',"
                "'2026-08-07T12:00:00Z')"
            )

    def test_direct_sql_verified_occurrence_requires_registered_human(self):
        """Fail if SQLite accepts an arbitrary verifier string for a VERIFIED occurrence."""
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        record_id = create_record(db, "Record", "b" * 64)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO occurrence(occurrence_id,record_id,source_file_id,source_locator,"
                "verification_state,verified_by) VALUES('O1',?,?, 'page:1','VERIFIED','nobody')",
                (record_id, source_file_id),
            )

    def test_direct_sql_rejects_imprecise_citation(self):
        """Fail if the SQLite boundary accepts a locator the service must reject."""
        db = new_test_db()
        _, source_file_id = seed_package_source(db)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO evidence_citation(evidence_citation_id,source_file_id,locator) "
                "VALUES('EC1',?,'near the end')",
                (source_file_id,),
            )

    def test_final_legal_assessment_requires_nonempty_verified_cited_support(self):
        """Fail if an authority alone can turn an unsupported conclusion into FINAL state."""
        db = new_test_db()
        assessment_id = create_legal_assessment(
            db,
            "Was the duty satisfied?",
            "Categorical conclusion",
            [],
            [("STATUTE", "Synthetic statute section 1")],
        )

        with self.assertRaisesRegex(ValueError, "VERIFIED cited finding"):
            finalize_legal_assessment(db, assessment_id)

        self.assertEqual(
            "DRAFT",
            db.execute(
                "SELECT assessment_status FROM legal_assessment WHERE legal_assessment_id=?",
                (assessment_id,),
            ).fetchone()[0],
        )


class CompletenessExploitTests(unittest.TestCase):
    def _register_human(self, db):
        db.execute(
            "INSERT INTO reviewer_identity(reviewer_id,identity_type) VALUES('reviewer','HUMAN')"
        )

    def test_verified_complete_rejects_declared_inventory_mismatch(self):
        """Fail if declared Level-1 inventory can differ from the ledger's actual inventory."""
        db = new_test_db()
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','P1.pdf',2)"
        )
        db.execute(
            "INSERT INTO source_file(source_file_id,package_id,archive_member_path,byte_size,sha256,media_type) "
            "VALUES('S1','P1','one.pdf',1,?,'application/pdf')",
            ("a" * 64,),
        )
        self._register_human(db)

        with self.assertRaisesRegex(ValueError, "inventory"):
            set_package_completeness(db, "P1", "VERIFIED_COMPLETE", "reviewer")

    def test_verified_complete_rejects_source_without_terminal_processing(self):
        """Fail if an inventoried source can be declared complete without a terminal run."""
        db = new_test_db()
        seed_package_source(db)
        self._register_human(db)

        with self.assertRaisesRegex(ValueError, "terminal processing"):
            set_package_completeness(db, "P1", "VERIFIED_COMPLETE", "reviewer")

    def test_verified_complete_rejects_any_incomplete_attempt_in_scope(self):
        """Fail if a later successful run hides an earlier incomplete processing attempt."""
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        record_processing_result(db, source_file_id, "EXTRACT_PDF", b"terminal")
        db.execute(
            "INSERT INTO processing_run(processing_run_id,source_file_id,operation,tool_name,started_at) "
            "VALUES('PR_INCOMPLETE',?,'EXTRACT_PDF','test','2026-08-07T12:00:00Z')",
            (source_file_id,),
        )
        record_id = create_record(db, "Record", "c" * 64)
        self._register_human(db)
        occurrence_id = create_occurrence(
            db, record_id, source_file_id, None, "page:1", "PROVISIONAL"
        )
        review_module.promote_occurrence_verified(
            db, occurrence_id, "reviewer", "2026-08-07T12:00:00Z"
        )

        with self.assertRaisesRegex(ValueError, "incomplete processing"):
            set_package_completeness(db, "P1", "VERIFIED_COMPLETE", "reviewer")

    def test_verified_complete_rejects_material_unresolved_review(self):
        """Fail if UNRESOLVED material ambiguity is treated as verified completeness."""
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        record_processing_result(db, source_file_id, "EXTRACT_PDF", b"terminal")
        self._register_human(db)
        record_id = create_record(db, "Record", "d" * 64)
        occurrence_id = create_occurrence(
            db, record_id, source_file_id, None, "page:1", "PROVISIONAL"
        )
        review_module.promote_occurrence_verified(
            db, occurrence_id, "reviewer", "2026-08-07T12:00:00Z"
        )
        task_id = review_module.open_review_task(
            db,
            "SOURCE_FILE",
            source_file_id,
            "MATERIAL_AMBIGUITY",
            "material ambiguity",
            material=True,
        )
        review_module.resolve_review_task(
            db,
            task_id,
            "UNRESOLVED",
            "reviewer",
            "2026-08-07T12:00:00Z",
            "material ambiguity remains unresolved",
            None,
        )

        with self.assertRaisesRegex(ValueError, "UNRESOLVED"):
            set_package_completeness(db, "P1", "VERIFIED_COMPLETE", "reviewer")

    def test_verified_complete_rejects_provisional_occurrence(self):
        """Fail if provisional Level-2 identification can satisfy verification completeness."""
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        record_processing_result(db, source_file_id, "EXTRACT_PDF", b"terminal")
        record_id = create_record(db, "Record", "e" * 64)
        create_occurrence(db, record_id, source_file_id, None, "page:1", "PROVISIONAL")
        self._register_human(db)

        with self.assertRaisesRegex(ValueError, "occurrence verification"):
            set_package_completeness(db, "P1", "VERIFIED_COMPLETE", "reviewer")

    def test_direct_sql_cannot_bypass_verified_complete_gates(self):
        """Fail if a raw UPDATE can skip inventory, processing, verification, and audit gates."""
        db = new_test_db()
        seed_package_source(db)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE package SET completeness_state='VERIFIED_COMPLETE' WHERE package_id='P1'"
            )


class ProvenanceAndAuditExploitTests(unittest.TestCase):
    def _manifest(self, root: Path, *, control: str = "control.pdf") -> Path:
        path = root / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "packages": [
                        {
                            "package_id": "P1",
                            "control_record": control,
                            "production_archive": "production.zip",
                            "expected_level1_count": 1,
                            "package_status": None,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_reingestion_rejects_changed_package_metadata(self):
        """Fail if an existing package identity silently keeps stale manifest metadata."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "control.pdf").write_bytes(b"control one")
            (root / "changed-control.pdf").write_bytes(b"control two")
            with zipfile.ZipFile(root / "production.zip", "w") as archive:
                archive.writestr("one.pdf", b"one")
            db = new_test_db()
            ingest_manifest(db, self._manifest(root), root)

            with self.assertRaisesRegex(ValueError, "package metadata drift"):
                ingest_manifest(db, self._manifest(root, control="changed-control.pdf"), root)

            self.assertEqual(
                "control.pdf",
                db.execute(
                    "SELECT control_record_path FROM package WHERE package_id='P1'"
                ).fetchone()[0],
            )

    def test_reingestion_rejects_changed_member_hash_and_size(self):
        """Fail if one archive path can acquire a second stale source identity after byte drift."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "control.pdf").write_bytes(b"control")
            with zipfile.ZipFile(root / "production.zip", "w") as archive:
                archive.writestr("one.pdf", b"one")
            db = new_test_db()
            manifest = self._manifest(root)
            ingest_manifest(db, manifest, root)
            with zipfile.ZipFile(root / "production.zip", "w") as archive:
                archive.writestr("one.pdf", b"changed and larger")

            with self.assertRaisesRegex(ValueError, "member provenance drift"):
                ingest_manifest(db, manifest, root)

            self.assertEqual(1, db.execute("SELECT count(*) FROM source_file").fetchone()[0])

    def test_member_path_is_unique_within_package_at_sqlite_boundary(self):
        """Fail if raw SQL can create two identities for the same archive member path."""
        db = new_test_db()
        seed_package_source(db)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO source_file(source_file_id,package_id,archive_member_path,byte_size,sha256,media_type) "
                "VALUES('S2','P1','a.pdf',2,?,'application/pdf')",
                ("f" * 64,),
            )

    def test_occurrence_rejects_derivative_from_different_source(self):
        """Fail if an occurrence can attach a derivative whose provenance belongs elsewhere."""
        db = new_test_db()
        _, source_one = seed_package_source(db, "P1", "S1", "one.pdf")
        db.execute(
            "INSERT INTO source_file(source_file_id,package_id,archive_member_path,byte_size,sha256,media_type) "
            "VALUES('S2','P1','two.pdf',1,?,'application/pdf')",
            ("2" * 64,),
        )
        derivative_id = record_processing_result(
            db, "S2", "EXTRACT_PDF", b"other source derivative"
        ).derivative_id
        record_id = create_record(db, "Record", "f" * 64)

        with self.assertRaisesRegex(ValueError, "same source"):
            create_occurrence(
                db, record_id, source_one, derivative_id, "page:1", "PROVISIONAL"
            )

    def test_direct_sql_occurrence_rejects_cross_source_derivative(self):
        """Fail if SQLite permits a forged source-to-derivative provenance chain."""
        db = new_test_db()
        _, source_one = seed_package_source(db, "P1", "S1", "one.pdf")
        db.execute(
            "INSERT INTO source_file(source_file_id,package_id,archive_member_path,byte_size,sha256,media_type) "
            "VALUES('S2','P1','two.pdf',1,?,'application/pdf')",
            ("2" * 64,),
        )
        derivative_id = record_processing_result(
            db, "S2", "EXTRACT_PDF", b"other source derivative"
        ).derivative_id
        record_id = create_record(db, "Record", "1" * 64)

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO occurrence(occurrence_id,record_id,source_file_id,derivative_id,"
                "source_locator) VALUES('O1',?,?,?,'page:1')",
                (record_id, source_one, derivative_id),
            )

    def test_process_source_rejects_evidence_derivative_overlap_in_service(self):
        """Fail if direct service use can write derivatives below immutable evidence."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "control.pdf").write_bytes(b"control")
            with zipfile.ZipFile(root / "production.zip", "w") as archive:
                archive.writestr("one.pdf", b"not a valid pdf")
            db = new_test_db()
            ingest_manifest(db, self._manifest(root), root)
            source_file_id = db.execute("SELECT source_file_id FROM source_file").fetchone()[0]

            with self.assertRaisesRegex(ValueError, "overlap"):
                process_source(db, source_file_id, root, root / "derivatives")

            self.assertEqual(0, db.execute("SELECT count(*) FROM processing_run").fetchone()[0])

    def test_old_audit_event_cannot_authorize_repeated_a_to_b_update(self):
        """Fail if a consumed audit can be reused after A→B, B→A, then raw A→B."""
        db = new_test_db()
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','P1.pdf',0)"
        )
        request_element_id = add_request_element(db, "P1", "requested item", 1)
        finding_id = add_finding(
            db, "UNPRODUCED", request_element_id, "PROVISIONAL", "HUMAN", "A"
        )
        review_module.register_reviewer_identity(db, "reviewer", "HUMAN")
        change_with_audit(
            db, "FINDING", finding_id, "finding", "proposition", "B", "first", "reviewer"
        )
        change_with_audit(
            db, "FINDING", finding_id, "finding", "proposition", "A", "second", "reviewer"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE finding SET proposition='B' WHERE finding_id=?", (finding_id,))

    def test_canonical_identities_reject_raw_rewrites(self):
        """Fail if stable record, occurrence, citation, or Metro-statement identity can drift."""
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        record_id = create_record(db, "Record", "3" * 64)
        occurrence_id = create_occurrence(
            db, record_id, source_file_id, None, "page:1", "PROVISIONAL"
        )
        citation_id = add_citation(db, source_file_id, occurrence_id, "page:1")
        from metro_forensics.evidence import add_metro_statement

        statement_id = add_metro_statement(
            db, "Metro text", "DENIAL", citation_id, "PROVISIONAL"
        )

        for sql, parameters in (
            ("UPDATE record SET content_fingerprint=? WHERE record_id=?", ("4" * 64, record_id)),
            ("UPDATE occurrence SET source_locator='page:2' WHERE occurrence_id=?", (occurrence_id,)),
            ("UPDATE evidence_citation SET locator='page:2' WHERE evidence_citation_id=?", (citation_id,)),
            ("UPDATE metro_statement SET statement_text='rewritten' WHERE metro_statement_id=?", (statement_id,)),
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                db.execute(sql, parameters)


class RelationshipsAndCrossPackageTests(unittest.TestCase):
    def _require_operation(self, module, name):
        self.assertTrue(hasattr(module, name), f"missing public operation: {name}")
        return getattr(module, name)

    def test_statement_to_request_element_link_has_validated_public_operation(self):
        """Fail if representative statement crosswalks still require direct bridge SQL."""
        link_statement = self._require_operation(
            evidence_module, "link_statement_request_element"
        )
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        request_element_id = add_request_element(db, "P1", "requested item", 1)
        citation_id = add_citation(db, source_file_id, None, "page:1")
        statement_id = evidence_module.add_metro_statement(
            db, "Metro response", "DENIAL", citation_id, "PROVISIONAL"
        )

        link_statement(db, statement_id, request_element_id)

        self.assertEqual(
            (statement_id, request_element_id),
            tuple(db.execute("SELECT * FROM statement_request_element").fetchone()),
        )

    def test_findings_support_package_record_and_reference_scope_without_request(self):
        """Fail if nullable request_element_id is unusable for declared finding scopes."""
        self.assertTrue(
            {"package_id", "record_id", "record_reference_id"}
            <= set(inspect.signature(add_finding).parameters),
            "add_finding is missing declared nullable scope parameters",
        )
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        record_id = create_record(db, "Record", "5" * 64)
        occurrence_id = create_occurrence(
            db, record_id, source_file_id, None, "page:1", "PROVISIONAL"
        )
        reference_id = evidence_module.add_record_reference(
            db, occurrence_id, "page:2", "ATTACHMENT", "Referenced item"
        )

        package_finding = add_finding(
            db,
            "UNPRODUCED",
            None,
            "PROVISIONAL",
            "HUMAN",
            "Package-scoped finding",
            package_id="P1",
        )
        record_finding = add_finding(
            db,
            "POSSIBLE_EXISTENCE_EVIDENCE",
            None,
            "PROVISIONAL",
            "HUMAN",
            "Record-scoped finding",
            package_id="P1",
            record_id=record_id,
        )
        reference_finding = add_finding(
            db,
            "POSSIBLE_EXISTENCE_EVIDENCE",
            None,
            "PROVISIONAL",
            "HUMAN",
            "Reference-scoped finding",
            package_id="P1",
            record_reference_id=reference_id,
        )

        scopes = [
            tuple(row)
            for row in db.execute(
                "SELECT package_id,request_element_id,record_id,record_reference_id "
                "FROM finding WHERE finding_id IN (?,?,?) ORDER BY proposition",
                (package_finding, record_finding, reference_finding),
            )
        ]
        self.assertEqual(3, len(scopes))
        self.assertTrue(all(scope[0] == "P1" and scope[1] is None for scope in scopes))

    def test_corpus_membership_completeness_and_search_assignment_use_public_operations(self):
        """Fail if corpus scope and reference search scope still depend on raw UPDATE/bridge SQL."""
        add_corpus = self._require_operation(review_module, "add_corpus")
        add_member = self._require_operation(review_module, "add_corpus_package")
        assign_search = self._require_operation(
            evidence_module, "assign_reference_search_corpus"
        )
        register = self._require_operation(review_module, "register_reviewer_identity")
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        record_id = create_record(db, "Record", "6" * 64)
        occurrence_id = create_occurrence(
            db, record_id, source_file_id, None, "page:1", "PROVISIONAL"
        )
        reference_id = evidence_module.add_record_reference(
            db, occurrence_id, "page:2", "ATTACHMENT", "Referenced item"
        )
        register(db, "reviewer", "HUMAN")
        add_corpus(db, "C1", "Defined search corpus")
        add_member(db, "C1", "P1", "reviewer")
        assign_search(db, reference_id, "C1", "reviewer")

        self.assertEqual(
            "C1",
            db.execute(
                "SELECT search_corpus_id FROM record_reference WHERE reference_id=?",
                (reference_id,),
            ).fetchone()[0],
        )
        self.assertEqual(1, db.execute("SELECT count(*) FROM corpus_package").fetchone()[0])
        self.assertGreaterEqual(
            db.execute(
                "SELECT count(*) FROM audit_event WHERE entity_type IN ('CORPUS','RECORD_REFERENCE')"
            ).fetchone()[0],
            2,
        )

    def test_reference_transition_requires_registered_human_identity(self):
        """Fail if reference identity conclusions remain attributed to a hard-coded 'human'."""
        db, reference_id, candidate_record_id = __import__(
            "tests.helpers", fromlist=["seeded_cross_package_reference_db"]
        ).seeded_cross_package_reference_db()

        with self.assertRaisesRegex(ValueError, "registered HUMAN"):
            set_reference_match(
                db,
                reference_id,
                "CONFIRMED_MATCH",
                candidate_record_id,
            )

    def test_explicit_cross_package_evidence_can_verify_without_recrediting(self):
        """Fail if linked elsewhere evidence cannot support verification or becomes responsive."""
        register = self._require_operation(review_module, "register_reviewer_identity")
        db = new_test_db()
        _, p1_source = seed_package_source(db, "P1", "S1", "p1.pdf")
        request_element_id = add_request_element(db, "P1", "requested record", 1)
        finding_id = add_finding(
            db,
            "STRONG_EXISTENCE_EVIDENCE",
            request_element_id,
            "PROVISIONAL",
            "HUMAN",
            "A record in another production specifically identifies the item.",
        )
        _, p2_source = seed_package_source(db, "P2", "S2", "p2.pdf")
        record_id = create_record(db, "Elsewhere evidence", "7" * 64)
        occurrence_id = create_occurrence(
            db, record_id, p2_source, None, "page:1", "PROVISIONAL"
        )
        link_request_evidence(
            db, request_element_id, occurrence_id, "EXISTENCE_EVIDENCE"
        )
        citation_id = add_citation(db, p2_source, occurrence_id, "page:1")
        register(db, "reviewer", "HUMAN")

        review_module.promote_finding_verified(
            db, finding_id, "reviewer", "2026-08-07T12:00:00Z", [citation_id]
        )

        self.assertEqual(
            ("P1", "VERIFIED"),
            tuple(
                db.execute(
                    "SELECT package_id,verification_state FROM finding WHERE finding_id=?",
                    (finding_id,),
                ).fetchone()
            ),
        )
        self.assertEqual(
            0,
            db.execute(
                "SELECT count(*) FROM request_element_evidence "
                "WHERE request_element_id=? AND evidentiary_role='RESPONSIVE'",
                (request_element_id,),
            ).fetchone()[0],
        )
        self.assertEqual(
            0,
            db.execute(
                "SELECT count(*) FROM occurrence JOIN source_file USING(source_file_id) "
                "WHERE record_id=? AND package_id='P1'",
                (record_id,),
            ).fetchone()[0],
        )

    def test_probable_match_remains_in_unresolved_view_and_totals(self):
        """Fail if PROBABLE_MATCH disappears from referenced-not-located reporting."""
        register = self._require_operation(review_module, "register_reviewer_identity")
        helpers = __import__("tests.helpers", fromlist=["seeded_reference_db"])
        db, reference_id, candidate_record_id = helpers.seeded_reference_db()
        register(db, "reviewer", "HUMAN")
        set_reference_match(
            db,
            reference_id,
            "PROBABLE_MATCH",
            candidate_record_id,
            reviewer="reviewer",
        )

        self.assertEqual(
            "PROBABLE_MATCH",
            db.execute(
                "SELECT match_state FROM v_referenced_not_located WHERE reference_id=?",
                (reference_id,),
            ).fetchone()[0],
        )
        self.assertEqual(
            1,
            db.execute(
                "SELECT referenced_not_located_items FROM v_summary_counts"
            ).fetchone()[0],
        )


class TemporalLegalAndMigrationTests(unittest.TestCase):
    def test_finalization_requires_and_records_registered_human_finalizer(self):
        """Fail if FINAL legal state lacks an identified registered human and transition audit."""
        self.assertIn(
            "finalizer",
            inspect.signature(finalize_legal_assessment).parameters,
            "finalize_legal_assessment must identify its human finalizer",
        )
        helpers = __import__("tests.helpers", fromlist=["seeded_provisional_finding_db"])
        db, finding_id, citation_id = helpers.seeded_provisional_finding_db()
        review_module.register_reviewer_identity(db, "reviewer", "HUMAN")
        review_module.promote_finding_verified(
            db, finding_id, "reviewer", "2026-08-07T12:00:00Z", [citation_id]
        )
        assessment_id = create_legal_assessment(
            db,
            "Was the duty satisfied?",
            "The cited verified finding supports this bounded conclusion.",
            [finding_id],
            [("STATUTE", "Synthetic statute section 1")],
        )

        with self.assertRaisesRegex(ValueError, "registered HUMAN"):
            finalize_legal_assessment(db, assessment_id, finalizer="unregistered")
        finalize_legal_assessment(db, assessment_id, finalizer="reviewer")

        self.assertEqual(
            ("FINAL", "reviewer"),
            tuple(
                db.execute(
                    "SELECT assessment_status,finalized_by FROM legal_assessment "
                    "WHERE legal_assessment_id=?",
                    (assessment_id,),
                ).fetchone()
            ),
        )
        self.assertEqual(
            "reviewer",
            db.execute(
                "SELECT change_source FROM audit_event WHERE entity_type='LEGAL_ASSESSMENT' "
                "AND entity_id=? AND field_name='assessment_status'",
                (assessment_id,),
            ).fetchone()[0],
        )

    def test_uncontrolled_finding_cannot_support_explicit_possession(self):
        """Fail if a non-possession finding bypasses the controlled support-type gate."""
        parameters = set(inspect.signature(add_temporal_inference).parameters)
        self.assertTrue(
            {"supporting_finding_ids", "reviewer"} <= parameters,
            "possession inference needs independently verified non-date support",
        )
        helpers = __import__("tests.helpers", fromlist=["seeded_provisional_finding_db"])
        db, finding_id, citation_id = helpers.seeded_provisional_finding_db()
        review_module.register_reviewer_identity(db, "reviewer", "HUMAN")
        review_module.promote_finding_verified(
            db, finding_id, "reviewer", "2026-08-07T12:00:00Z", [citation_id]
        )
        response_date_id = add_date_fact(
            db,
            "RECORD",
            "R_POSSESSION",
            "RESPONSE_DATE",
            "2026-08-07",
            "2026-08-07",
            "DAY",
            citation_id,
        )

        with self.assertRaisesRegex(ValueError, "possession-supporting"):
            add_temporal_inference(
                db,
                "RECORD",
                "R_POSSESSION",
                "POSSESSED_AT_RESPONSE",
                [response_date_id],
                supporting_finding_ids=[finding_id],
                reviewer="reviewer",
            )

        self.assertEqual(0, db.execute("SELECT count(*) FROM temporal_inference").fetchone()[0])

    def test_migration_preserves_shared_legacy_authority_for_all_assessments(self):
        """Fail if legacy backfill assigns one shared authority to only one assessment."""
        legacy_schema = (
            Path(__file__).resolve().parents[1]
            / ".superpowers/sdd/2026-08-07-metro-forensic-ledger-implementation/"
            "task-7-base/metro_forensics/schema.sql"
        )
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(legacy_schema.read_text(encoding="utf-8"))
        db.execute(
            "INSERT INTO legal_authority(legal_authority_id,authority_type,citation) "
            "VALUES('A_SHARED','STATUTE','Shared authority')"
        )
        db.execute(
            "INSERT INTO legal_assessment(legal_assessment_id,legal_question,conclusion,"
            "primary_legal_authority_id) VALUES"
            "('LA1','Question one?','Draft one','A_SHARED'),"
            "('LA2','Question two?','Draft two','A_SHARED')"
        )

        initialize(db)

        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("legal_assessment_authority", tables)
        self.assertEqual(
            {("LA1", "A_SHARED"), ("LA2", "A_SHARED")},
            {
                tuple(row)
                for row in db.execute(
                    "SELECT legal_assessment_id,legal_authority_id "
                    "FROM legal_assessment_authority"
                )
            },
        )


class SQLiteBoundaryHardeningTests(unittest.TestCase):
    def test_direct_finding_promotion_requires_cited_source_support(self):
        """Fail if raw UPDATE plus fabricated audits can verify a citationless finding."""
        db = new_test_db()
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','P1.pdf',0)"
        )
        request_element_id = add_request_element(db, "P1", "requested item", 1)
        finding_id = add_finding(
            db, "UNPRODUCED", request_element_id, "PROVISIONAL", "HUMAN", "citationless"
        )
        review_module.register_reviewer_identity(db, "reviewer", "HUMAN")
        for event_id, field, old, new in (
            ("AE_STATE", "verification_state", "PROVISIONAL", "VERIFIED"),
            ("AE_REVIEWER", "verified_by", None, "reviewer"),
        ):
            db.execute(
                "INSERT INTO audit_event(event_id,entity_type,entity_id,field_name,previous_value,"
                "new_value,changed_at,reason,change_source) VALUES(?, 'FINDING', ?, ?, ?, ?, "
                "'2026-08-07T12:00:00Z','fabricated direct promotion','reviewer')",
                (event_id, finding_id, field, old, new),
            )

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE finding SET verification_state='VERIFIED',verified_by='reviewer' "
                "WHERE finding_id=?",
                (finding_id,),
            )

    def test_all_package_completeness_transitions_require_single_use_human_audit(self):
        """Fail if non-FINAL completeness state changes bypass or reuse an old audit."""
        db = new_test_db()
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','P1.pdf',0)"
        )
        review_module.register_reviewer_identity(db, "reviewer", "HUMAN")

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE package SET completeness_state='REVIEW_REQUIRED' WHERE package_id='P1'"
            )

        set_package_completeness(db, "P1", "REVIEW_REQUIRED", "reviewer")
        set_package_completeness(db, "P1", "IN_PROGRESS", "reviewer")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE package SET completeness_state='REVIEW_REQUIRED' WHERE package_id='P1'"
            )

    def test_corpus_completeness_has_audited_public_transition(self):
        """Fail if corpus completeness can be rewritten without a registered human service."""
        self.assertTrue(
            hasattr(review_module, "set_corpus_completeness"),
            "missing public operation: set_corpus_completeness",
        )
        db = new_test_db()
        review_module.add_corpus(db, "C1", "Defined corpus")

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE corpus SET completeness_state='REVIEW_REQUIRED' WHERE corpus_id='C1'")

    def test_occurrence_and_statement_verification_require_audited_human_promotion(self):
        """Fail if evidentiary state can be changed directly or created by an unregistered label."""
        self.assertTrue(
            hasattr(review_module, "promote_occurrence_verified"),
            "missing public operation: promote_occurrence_verified",
        )
        self.assertTrue(
            hasattr(review_module, "promote_metro_statement_verified"),
            "missing public operation: promote_metro_statement_verified",
        )
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        record_id = create_record(db, "Record", "8" * 64)
        occurrence_id = create_occurrence(
            db, record_id, source_file_id, None, "page:1", "PROVISIONAL"
        )
        citation_id = add_citation(db, source_file_id, occurrence_id, "page:1")
        statement_id = evidence_module.add_metro_statement(
            db, "Metro statement", "DENIAL", citation_id, "PROVISIONAL"
        )
        review_module.register_reviewer_identity(db, "reviewer", "HUMAN")

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE occurrence SET verification_state='VERIFIED',verified_by='reviewer' "
                "WHERE occurrence_id=?",
                (occurrence_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE metro_statement SET verification_state='VERIFIED',verified_by='reviewer' "
                "WHERE metro_statement_id=?",
                (statement_id,),
            )
        review_module.promote_occurrence_verified(
            db, occurrence_id, "reviewer", "2026-08-07T12:00:00Z"
        )
        review_module.promote_metro_statement_verified(
            db, statement_id, "reviewer", "2026-08-07T12:00:01Z"
        )
        self.assertEqual(
            [("VERIFIED", "reviewer"), ("VERIFIED", "reviewer")],
            [
                tuple(
                    db.execute(
                        "SELECT verification_state,verified_by FROM occurrence "
                        "WHERE occurrence_id=?",
                        (occurrence_id,),
                    ).fetchone()
                ),
                tuple(
                    db.execute(
                        "SELECT verification_state,verified_by FROM metro_statement "
                        "WHERE metro_statement_id=?",
                        (statement_id,),
                    ).fetchone()
                ),
            ],
        )

    def test_service_rejects_unregistered_verified_metro_statement(self):
        """Fail if statement verification accepts any nonempty verifier string."""
        db = new_test_db()
        _, source_file_id = seed_package_source(db)
        citation_id = add_citation(db, source_file_id, None, "page:1")

        with self.assertRaisesRegex(ValueError, "registered HUMAN"):
            evidence_module.add_metro_statement(
                db, "Metro statement", "DENIAL", citation_id, "VERIFIED", "unregistered"
            )

    def test_direct_reference_and_review_state_transitions_require_audit(self):
        """Fail if raw SQL can change reference identity or review resolution state."""
        db, reference_id, candidate_id = __import__(
            "tests.helpers", fromlist=["seeded_reference_db"]
        ).seeded_reference_db()
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE record_reference SET match_state='PROBABLE_MATCH',matched_record_id=? "
                "WHERE reference_id=?",
                (candidate_id, reference_id),
            )
        task_id = review_module.open_review_task(
            db, "PACKAGE", "P1", "MATERIAL", "needs review"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE review_task SET task_state='UNRESOLVED',reviewer='made-up',"
                "resolved_at='2026-08-07T12:00:00Z' WHERE review_task_id=?",
                (task_id,),
            )

    def test_direct_citation_occurrence_must_match_source(self):
        """Fail if SQLite permits a citation to splice an occurrence onto another source."""
        db = new_test_db()
        _, source_one = seed_package_source(db, "P1", "S1", "one.pdf")
        db.execute(
            "INSERT INTO source_file(source_file_id,package_id,archive_member_path,byte_size,sha256,media_type) "
            "VALUES('S2','P1','two.pdf',1,?,'application/pdf')",
            ("9" * 64,),
        )
        record_id = create_record(db, "Record", "9" * 64)
        occurrence_id = create_occurrence(
            db, record_id, "S2", None, "page:1", "PROVISIONAL"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO evidence_citation(evidence_citation_id,source_file_id,occurrence_id,locator) "
                "VALUES('EC_BAD',?,?,'page:1')",
                (source_one, occurrence_id),
            )

    def test_record_description_correction_requires_audited_service(self):
        """Fail if canonical record description changes are raw or lack a correction path."""
        db = new_test_db()
        record_id = create_record(db, "Original title", "a1" * 32)
        review_module.register_reviewer_identity(db, "reviewer", "HUMAN")

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE record SET title_or_description='Raw rewrite' WHERE record_id=?",
                (record_id,),
            )
        change_with_audit(
            db,
            "RECORD",
            record_id,
            "record",
            "title_or_description",
            "Audited correction",
            "correct source-checked description",
            "reviewer",
        )
        self.assertEqual(
            "Audited correction",
            db.execute(
                "SELECT title_or_description FROM record WHERE record_id=?", (record_id,)
            ).fetchone()[0],
        )

    def test_corpus_absence_uses_declared_package_scope_not_unrelated_packages(self):
        """Fail if an unrelated package outside the search corpus controls its absence gate."""
        db = new_test_db()
        _, p1_source = seed_package_source(db, "P1", "S1", "p1.pdf")
        record_processing_result(db, p1_source, "EXTRACT_PDF", b"terminal")
        record_id = create_record(db, "Reference-bearing record", "b1" * 32)
        occurrence_id = create_occurrence(
            db, record_id, p1_source, None, "page:1", "PROVISIONAL"
        )
        reference_id = evidence_module.add_record_reference(
            db, occurrence_id, "page:2", "ATTACHMENT", "Absent attachment"
        )
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P2','P2.pdf',0)"
        )
        review_module.register_reviewer_identity(db, "reviewer", "HUMAN")
        review_module.promote_occurrence_verified(
            db, occurrence_id, "reviewer", "2026-08-07T12:00:00Z"
        )
        set_package_completeness(db, "P1", "VERIFIED_COMPLETE", "reviewer")
        review_module.add_corpus(db, "C1", "P1-only search corpus")
        review_module.add_corpus_package(db, "C1", "P1", "reviewer")
        review_module.set_corpus_completeness(
            db, "C1", "VERIFIED_COMPLETE", "reviewer"
        )
        evidence_module.assign_reference_search_corpus(
            db, reference_id, "C1", "reviewer"
        )

        try:
            evidence_module.set_reference_absence_scope(
                db, reference_id, "NOT_LOCATED_CORPUS", reviewer="reviewer"
            )
        except (ValueError, sqlite3.IntegrityError) as error:
            self.fail(f"unrelated P2 incorrectly blocked P1-only verified search: {error}")

        self.assertEqual(
            "NOT_LOCATED_CORPUS",
            db.execute(
                "SELECT absence_scope FROM record_reference WHERE reference_id=?",
                (reference_id,),
            ).fetchone()[0],
        )

    def test_corpus_membership_requires_single_use_human_audit(self):
        """Fail if raw membership insertion can silently redefine a search corpus."""
        db = new_test_db()
        db.execute(
            "INSERT INTO package(package_id,control_record_path,expected_level1_count) "
            "VALUES('P1','P1.pdf',0),('P2','P2.pdf',0),('P3','P3.pdf',0)"
        )
        review_module.add_corpus(db, "C1", "Defined corpus")
        review_module.register_reviewer_identity(db, "reviewer", "HUMAN")

        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO corpus_package(corpus_id,package_id) VALUES('C1','P1')")

        review_module.add_corpus_package(db, "C1", "P1", "reviewer")
        db.execute(
            "INSERT INTO audit_event(event_id,entity_type,entity_id,field_name,previous_value,"
            "new_value,changed_at,reason,change_source) VALUES(" 
            "'AE_MEMBER','CORPUS','C1','package_membership',NULL,'P2',"
            "'2026-08-07T12:00:00Z','add member','reviewer')"
        )
        db.execute("INSERT INTO corpus_package(corpus_id,package_id) VALUES('C1','P2')")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO corpus_package(corpus_id,package_id) VALUES('C1','P3')")

    def test_legal_status_transition_requires_single_use_registered_human_audit(self):
        """Fail if an old or automated audit can authorize later legal-state rewrites."""
        self.assertTrue(
            hasattr(__import__("metro_forensics.temporal_legal", fromlist=["x"]),
                    "set_legal_assessment_status"),
            "missing public human-attributed legal status transition",
        )
        db = new_test_db()
        review_module.register_reviewer_identity(db, "reviewer", "HUMAN")
        review_module.register_reviewer_identity(db, "bot", "AUTOMATION")
        assessment_id = create_legal_assessment(
            db, "Question?", "Qualified draft conclusion", [], []
        )

        db.execute(
            "INSERT INTO audit_event(event_id,entity_type,entity_id,field_name,previous_value,"
            "new_value,changed_at,reason,change_source) VALUES(" 
            "'AE_BOT','LEGAL_ASSESSMENT',?,'assessment_status','DRAFT','QUALIFIED',"
            "'2026-08-07T12:00:00Z','automated legal rewrite','bot')",
            (assessment_id,),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE legal_assessment SET assessment_status='QUALIFIED' "
                "WHERE legal_assessment_id=?",
                (assessment_id,),
            )

        from metro_forensics.temporal_legal import set_legal_assessment_status

        set_legal_assessment_status(db, assessment_id, "QUALIFIED", "reviewer")
        set_legal_assessment_status(db, assessment_id, "DRAFT", "reviewer")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE legal_assessment SET assessment_status='QUALIFIED' "
                "WHERE legal_assessment_id=?",
                (assessment_id,),
            )

    def test_incomplete_run_review_audit_names_exact_owned_run(self):
        """Fail if retained-run repair leaves an ambiguous source-only audit annotation."""
        db = new_test_db()
        _, source_id = seed_package_source(db)
        run_id = "PR_EXACT_INCOMPLETE"
        db.execute(
            "INSERT INTO processing_run(processing_run_id,source_file_id,operation,tool_name,"
            "started_at) VALUES(?,?,'EXTRACT_PDF','test','2026-08-07T12:00:00Z')",
            (run_id, source_id),
        )
        task_id = review_module.open_review_task(
            db,
            "SOURCE_FILE",
            source_id,
            "PROCESSING_INCOMPLETE",
            f"PROCESSING_INCOMPLETE: {run_id}",
            task_type="EXTRACTION_EXCEPTION",
            task_key=run_id,
            creation_source="repair-automation",
        )

        audit_reason = db.execute(
            "SELECT reason FROM audit_event WHERE entity_type='REVIEW_TASK' "
            "AND entity_id=? AND field_name='CREATE'",
            (task_id,),
        ).fetchone()[0]
        self.assertIn(run_id, audit_reason)


if __name__ == "__main__":
    unittest.main()
