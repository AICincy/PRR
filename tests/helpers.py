import sqlite3

from metro_forensics.db import initialize
from metro_forensics.evidence import (
    add_citation,
    add_finding,
    add_record_reference,
    add_request_element,
)
from metro_forensics.records import create_occurrence, create_record
from metro_forensics.review import register_reviewer_identity


def new_test_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    initialize(db)
    return db


def seed_package_source(db, package_id="P1", source_file_id="S1", member="a.pdf"):
    db.execute(
        "INSERT INTO package(package_id,control_record_path,expected_level1_count) VALUES(?,?,1)",
        (package_id, f"{package_id}.pdf"),
    )
    db.execute(
        "INSERT INTO source_file(source_file_id,package_id,archive_member_path,byte_size,sha256,media_type) "
        "VALUES(?,?,?,?,?,?)",
        (source_file_id, package_id, member, 1, "0" * 64, "application/octet-stream"),
    )
    return package_id, source_file_id


def seeded_reference_db():
    """Build one responsive reference and a separate candidate record."""
    db = new_test_db()
    register_reviewer_identity(db, "reviewer", "HUMAN")
    _, source_file_id = seed_package_source(db)
    source_record_id = create_record(db, "Responsive report", "1" * 64)
    source_occurrence_id = create_occurrence(
        db, source_record_id, source_file_id, None, "page:1", "PROVISIONAL"
    )
    reference_id = add_record_reference(
        db, source_occurrence_id, "page:2", "ATTACHMENT", "Referenced attachment"
    )
    candidate_record_id = create_record(db, "Candidate attachment", "2" * 64)
    return db, reference_id, candidate_record_id


def seeded_cross_package_reference_db():
    """Build a P1 reference whose candidate exists only in P2."""
    db = new_test_db()
    register_reviewer_identity(db, "reviewer", "HUMAN")
    _, p1_source_file_id = seed_package_source(db, "P1", "S1", "p1.pdf")
    source_record_id = create_record(db, "P1 report", "3" * 64)
    source_occurrence_id = create_occurrence(
        db, source_record_id, p1_source_file_id, None, "page:1", "PROVISIONAL"
    )
    reference_id = add_record_reference(
        db, source_occurrence_id, "page:2", "ATTACHMENT", "P2 attachment"
    )

    _, p2_source_file_id = seed_package_source(db, "P2", "S2", "p2.pdf")
    candidate_record_id = create_record(db, "P2 attachment", "4" * 64)
    create_occurrence(
        db, candidate_record_id, p2_source_file_id, None, "page:1", "PROVISIONAL"
    )
    return db, reference_id, candidate_record_id


def seeded_provisional_finding_db():
    """Build a source-backed, human-created finding ready for review."""
    db = new_test_db()
    _, source_file_id = seed_package_source(db)
    request_element_id = add_request_element(db, "P1", "requested item", 1)
    finding_id = add_finding(
        db,
        "UNPRODUCED",
        request_element_id,
        "PROVISIONAL",
        "HUMAN",
        "No responsive item was located.",
    )
    citation_id = add_citation(db, source_file_id, None, "page:1")
    return db, finding_id, citation_id


def seeded_citation_db():
    """Build a minimal source/record/occurrence/citation graph via public services."""
    db = new_test_db()
    _, source_file_id = seed_package_source(db)
    record_id = create_record(db, "Dated record", "6" * 64)
    occurrence_id = create_occurrence(
        db, record_id, source_file_id, None, "page:1", "PROVISIONAL"
    )
    citation_id = add_citation(db, source_file_id, occurrence_id, "page:1")
    return db, citation_id


def seeded_unlocated_reference_db():
    """Build one unresolved reference in an incomplete synthetic corpus."""
    db = new_test_db()
    _, source_file_id = seed_package_source(db)
    record_id = create_record(db, "Responsive report", "5" * 64)
    occurrence_id = create_occurrence(
        db, record_id, source_file_id, None, "page:1", "PROVISIONAL"
    )
    reference_id = add_record_reference(
        db, occurrence_id, "page:2", "ATTACHMENT", "Referenced attachment"
    )
    return db, reference_id


def seeded_duplicate_occurrence_db():
    """Build one Level 2 record preserved as two distinct source occurrences."""
    db = new_test_db()
    _, first_source_file_id = seed_package_source(db, "P1", "S1", "first.pdf")
    db.execute(
        "INSERT INTO source_file(source_file_id,package_id,archive_member_path,byte_size,sha256,media_type) "
        "VALUES(?,?,?,?,?,?)",
        ("S2", "P1", "second.pdf", 1, "a" * 64, "application/octet-stream"),
    )
    record_id = create_record(db, "Repeated record", "b" * 64)
    create_occurrence(db, record_id, first_source_file_id, None, "page:1", "PROVISIONAL")
    create_occurrence(db, record_id, "S2", None, "page:1", "PROVISIONAL")
    return db
