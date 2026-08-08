"""Evidence-ledger operations that preserve package scope and audit history."""

from datetime import datetime, timezone
from contextlib import contextmanager
import sqlite3

from metro_forensics.ids import stable_id
from metro_forensics.locators import require_exact_locator


MATERIAL_FINDINGS = {
    "UNPRODUCED",
    "NONEXISTENCE_ASSERTED",
    "SUBSTITUTE_PRODUCTION",
    "DIRECT_CONTRADICTION",
    "STRONG_EXISTENCE_EVIDENCE",
    "POSSIBLE_EXISTENCE_EVIDENCE",
    "PRODUCED_FULL",
    "PRODUCED_PARTIAL_REDACTED",
    "WITHHELD_WHOLE_OR_PART",
    "WITHHOLDING_BASIS_STATED",
    "NO_WITHHOLDING_BASIS_STATED",
}

_VERIFICATION_STATES = {"PROVISIONAL", "VERIFIED"}
_CREATION_SOURCES = {"HUMAN", "AUTOMATION"}
_REFERENCE_MATCH_STATES = {
    "CONFIRMED_MATCH",
    "PROBABLE_MATCH",
    "NO_MATCH_LOCATED",
}
_ABSENCE_SCOPES = {
    "NOT_LOCATED_RESPONSIVE_PACKAGE",
    "LOCATED_ELSEWHERE_CORPUS",
    "NOT_LOCATED_CORPUS",
    "CORPUS_SEARCH_INCOMPLETE",
}
_EVIDENTIARY_ROLES = {
    "RESPONSIVE",
    "SUBSTITUTE",
    "EXISTENCE_EVIDENCE",
    "CONTRADICTION_EVIDENCE",
    "CONTEXT",
}


def add_request_element(
    conn: sqlite3.Connection,
    package_id: str,
    text: str,
    ordinal: int,
    parent_id: str | None = None,
) -> str:
    """Add an ordered requested item without changing any existing request element."""
    if not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("ordinal must be a non-negative integer")
    if parent_id is not None:
        parent = conn.execute(
            "SELECT package_id FROM request_element WHERE request_element_id=?", (parent_id,)
        ).fetchone()
        if parent is None:
            raise ValueError(f"unknown parent request element: {parent_id}")
        if parent[0] != package_id:
            raise ValueError("parent request element must belong to the same package")

    request_element_id = stable_id("RE", package_id, parent_id or "", str(ordinal), text)
    conn.execute(
        """
        INSERT INTO request_element(
            request_element_id, package_id, parent_request_element_id,
            requested_language, sort_order
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(request_element_id) DO NOTHING
        """,
        (request_element_id, package_id, parent_id, text, ordinal),
    )
    return request_element_id


def add_citation(
    conn: sqlite3.Connection,
    source_file_id: str,
    occurrence_id: str | None,
    locator: str,
) -> str:
    """Store an exact source location independently from statements and findings."""
    require_exact_locator(locator)
    if occurrence_id is not None:
        occurrence = conn.execute(
            "SELECT source_file_id FROM occurrence WHERE occurrence_id=?", (occurrence_id,)
        ).fetchone()
        if occurrence is None:
            raise ValueError(f"unknown occurrence_id: {occurrence_id}")
        if occurrence[0] != source_file_id:
            raise ValueError("citation occurrence must belong to its source file")
    citation_id = stable_id("EC", source_file_id, occurrence_id or "", locator)
    conn.execute(
        """
        INSERT INTO evidence_citation(evidence_citation_id, source_file_id, occurrence_id, locator)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(evidence_citation_id) DO NOTHING
        """,
        (citation_id, source_file_id, occurrence_id, locator),
    )
    return citation_id


def add_metro_statement(
    conn: sqlite3.Connection,
    text: str,
    statement_type: str,
    citation_id: str,
    verification_state: str,
    verified_by: str | None = None,
) -> str:
    """Store Metro's text and point it to, rather than replace it with, a citation."""
    _require_verification_state(verification_state)
    if verification_state == "VERIFIED" and not verified_by:
        raise ValueError("verified_by is required for VERIFIED statements")
    if verification_state == "VERIFIED":
        _require_registered_human(conn, verified_by)
    citation = conn.execute(
        """
        SELECT source_file_id, locator, metro_statement_id
        FROM evidence_citation WHERE evidence_citation_id=?
        """,
        (citation_id,),
    ).fetchone()
    if citation is None or citation["source_file_id"] is None:
        raise ValueError("Metro statements require a citation with a source file")
    if citation["metro_statement_id"] is not None:
        raise ValueError("citation is already linked to a Metro statement")
    source = conn.execute(
        "SELECT package_id FROM source_file WHERE source_file_id=?", (citation["source_file_id"],)
    ).fetchone()
    if source is None:
        raise ValueError("citation source file is missing")

    statement_id = stable_id("MS", citation_id, statement_type, verification_state, text)
    conn.execute(
        """
        INSERT INTO metro_statement(
            metro_statement_id, package_id, source_file_id, statement_text,
            statement_type, source_locator, verification_state, verified_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            statement_id,
            source["package_id"],
            citation["source_file_id"],
            text,
            statement_type,
            citation["locator"],
            verification_state,
            verified_by,
        ),
    )
    conn.execute(
        "UPDATE evidence_citation SET metro_statement_id=? WHERE evidence_citation_id=?",
        (statement_id, citation_id),
    )
    return statement_id


def link_statement_request_element(
    conn: sqlite3.Connection, statement_id: str, request_element_id: str
) -> None:
    """Crosswalk Metro's preserved statement only to an in-package request element."""
    scope = conn.execute(
        """
        SELECT ms.package_id AS statement_package, re.package_id AS request_package
        FROM metro_statement AS ms
        JOIN request_element AS re ON re.request_element_id=?
        WHERE ms.metro_statement_id=?
        """,
        (request_element_id, statement_id),
    ).fetchone()
    if scope is None:
        raise ValueError("statement and request element must exist")
    if scope[0] != scope[1]:
        raise ValueError("statement and request element must belong to the same package")
    conn.execute(
        """
        INSERT INTO statement_request_element(metro_statement_id,request_element_id)
        VALUES(?,?) ON CONFLICT(metro_statement_id,request_element_id) DO NOTHING
        """,
        (statement_id, request_element_id),
    )


def assert_verification_allowed(
    finding_type: str, verification_state: str, creation_source: str
) -> None:
    if (
        finding_type in MATERIAL_FINDINGS
        and verification_state == "VERIFIED"
        and creation_source == "AUTOMATION"
    ):
        raise ValueError("automation cannot create VERIFIED material findings")


def add_finding(
    conn: sqlite3.Connection,
    finding_type: str,
    request_element_id: str | None,
    verification_state: str,
    creation_source: str,
    description: str,
    verified_by: str | None = None,
    *,
    package_id: str | None = None,
    record_id: str | None = None,
    record_reference_id: str | None = None,
) -> str:
    """Append one finding and its creation audit event; never overwrite conclusions."""
    if finding_type not in MATERIAL_FINDINGS:
        raise ValueError("finding_type must be a controlled material finding")
    _require_verification_state(verification_state)
    if verification_state != "PROVISIONAL":
        raise ValueError("findings must be created PROVISIONAL and promoted after source review")
    creation_source = creation_source.upper()
    if creation_source not in _CREATION_SOURCES:
        raise ValueError("creation_source must be HUMAN or AUTOMATION")
    assert_verification_allowed(finding_type, verification_state, creation_source)
    if verification_state == "VERIFIED" and not verified_by:
        raise ValueError("verified_by is required for VERIFIED findings")
    request_package_id = None
    if request_element_id is not None:
        request_element = conn.execute(
            "SELECT package_id FROM request_element WHERE request_element_id=?",
            (request_element_id,),
        ).fetchone()
        if request_element is None:
            raise ValueError(f"unknown request_element_id: {request_element_id}")
        request_package_id = request_element[0]
    if package_id is None:
        package_id = request_package_id
    if package_id is None:
        raise ValueError("package_id is required when request_element_id is null")
    if conn.execute("SELECT 1 FROM package WHERE package_id=?", (package_id,)).fetchone() is None:
        raise ValueError(f"unknown package_id: {package_id}")
    if request_package_id is not None and request_package_id != package_id:
        raise ValueError("finding request element must belong to its package")
    if record_id is not None and conn.execute(
        "SELECT 1 FROM record WHERE record_id=?", (record_id,)
    ).fetchone() is None:
        raise ValueError(f"unknown record_id: {record_id}")
    if record_reference_id is not None:
        reference_package_id = _reference_package_id(conn, record_reference_id)
        if reference_package_id != package_id:
            raise ValueError("finding record reference must belong to its package")

    created_by = creation_source.lower()
    finding_id = stable_id(
        "F", package_id, request_element_id or "", record_id or "",
        record_reference_id or "", finding_type, verification_state, created_by, description,
    )
    if conn.execute("SELECT 1 FROM finding WHERE finding_id=?", (finding_id,)).fetchone():
        return finding_id
    created_at = _utc_now()
    audit_event_id = stable_id("AE", "FINDING", finding_id, "CREATE")
    with _atomic(conn):
        conn.execute(
            """
            INSERT INTO audit_event(
                event_id, entity_type, entity_id, field_name, changed_at, reason, change_source
            ) VALUES (?, 'FINDING', ?, 'CREATE', ?, 'create finding', ?)
            """,
            (audit_event_id, finding_id, created_at, created_by),
        )
        conn.execute(
            """
            INSERT INTO finding(
                finding_id, package_id, request_element_id, record_id,
                record_reference_id, finding_type, proposition, verification_state,
                created_by, verified_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding_id,
                package_id,
                request_element_id,
                record_id,
                record_reference_id,
                finding_type,
                description,
                verification_state,
                created_by,
                verified_by,
                created_at,
            ),
        )
    return finding_id


def link_request_evidence(
    conn: sqlite3.Connection, request_element_id: str, occurrence_id: str, role: str
) -> None:
    """Attach an occurrence in a declared evidentiary role without altering either item."""
    if role not in _EVIDENTIARY_ROLES:
        raise ValueError("unknown evidentiary role")
    scope = conn.execute(
        """
        SELECT request_element.package_id AS request_package_id,
               source_file.package_id AS occurrence_package_id
        FROM request_element
        JOIN occurrence ON occurrence.occurrence_id=?
        JOIN source_file ON source_file.source_file_id = occurrence.source_file_id
        WHERE request_element.request_element_id=?
        """,
        (occurrence_id, request_element_id),
    ).fetchone()
    if scope is None:
        raise ValueError("request element and occurrence must exist")
    if role == "RESPONSIVE" and scope["request_package_id"] != scope["occurrence_package_id"]:
        raise ValueError("RESPONSIVE evidence must be in the request element's package")
    link_id = stable_id("REE", request_element_id, occurrence_id, role)
    conn.execute(
        """
        INSERT INTO request_element_evidence(
            request_element_evidence_id, request_element_id, occurrence_id, evidentiary_role
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(request_element_id, occurrence_id, evidentiary_role) DO NOTHING
        """,
        (link_id, request_element_id, occurrence_id, role),
    )


def add_record_reference(
    conn: sqlite3.Connection,
    source_occurrence_id: str,
    locator: str,
    relationship_type: str,
    referenced_description: str,
) -> str:
    """Record a reference as a reference only; do not infer production or existence."""
    require_exact_locator(locator)
    reference_id = stable_id(
        "RR", source_occurrence_id, locator, relationship_type, referenced_description
    )
    conn.execute(
        """
        INSERT INTO record_reference(
            record_reference_id, reference_id, occurrence_id, source_locator,
            relationship_type, referenced_description, match_state
        ) VALUES (?, ?, ?, ?, ?, ?, 'NO_MATCH_LOCATED')
        ON CONFLICT(record_reference_id) DO NOTHING
        """,
        (
            reference_id,
            reference_id,
            source_occurrence_id,
            locator,
            relationship_type,
            referenced_description,
        ),
    )
    return reference_id


def set_reference_match(
    conn: sqlite3.Connection,
    reference_id: str,
    match_state: str,
    record_id: str | None,
    reviewer: str | None = None,
) -> None:
    """Set a match conservatively; only confirmed matches resolve a canonical record."""
    if match_state not in _REFERENCE_MATCH_STATES:
        raise ValueError("unknown reference match state")
    _require_registered_human(conn, reviewer)
    reference = _reference_row(conn, reference_id)
    if reference is None:
        raise ValueError(f"unknown reference_id: {reference_id}")

    if match_state == "CONFIRMED_MATCH":
        if record_id is None:
            raise ValueError("CONFIRMED_MATCH requires a record_id")
        if conn.execute("SELECT 1 FROM record WHERE record_id=?", (record_id,)).fetchone() is None:
            raise ValueError(f"unknown record_id: {record_id}")
        package_id = _reference_package_id(conn, reference["record_reference_id"])
        present_in_responsive_package = conn.execute(
            """
            SELECT 1
            FROM occurrence
            JOIN source_file USING(source_file_id)
            WHERE occurrence.record_id=? AND source_file.package_id=?
            """,
            (record_id, package_id),
        ).fetchone()
        present_elsewhere = conn.execute(
            """
            SELECT 1
            FROM occurrence
            JOIN source_file USING(source_file_id)
            WHERE occurrence.record_id=? AND source_file.package_id<>?
            """,
            (record_id, package_id),
        ).fetchone()
        if present_in_responsive_package:
            absence_scope = None
        elif present_elsewhere:
            absence_scope = "LOCATED_ELSEWHERE_CORPUS"
        else:
            raise ValueError("a confirmed reference match requires a located occurrence")
        _apply_reference_transition(
            conn,
            reference,
            {
                "match_state": "CONFIRMED_MATCH",
                "matched_record_id": record_id,
                "resolved_record_id": record_id,
                "absence_scope": absence_scope,
                "verification_state": "VERIFIED",
                "verified_by": reviewer,
            },
            "set confirmed reference match",
            reviewer,
        )
        return

    if match_state == "NO_MATCH_LOCATED" and record_id is not None:
        raise ValueError("NO_MATCH_LOCATED requires record_id to be None")
    if record_id is not None and conn.execute(
        "SELECT 1 FROM record WHERE record_id=?", (record_id,)
    ).fetchone() is None:
        raise ValueError(f"unknown record_id: {record_id}")
    _apply_reference_transition(
        conn,
        reference,
        {
            "match_state": match_state,
            "matched_record_id": record_id,
            "resolved_record_id": None,
            "absence_scope": None,
            "verification_state": "VERIFIED",
            "verified_by": reviewer,
        },
        "set reference match",
        reviewer,
    )


def set_reference_absence_scope(
    conn: sqlite3.Connection,
    reference_id: str,
    absence_scope: str,
    reviewer: str | None = None,
) -> None:
    """Apply only absence conclusions consistent with the current match state and scope."""
    if absence_scope not in _ABSENCE_SCOPES:
        raise ValueError("unknown absence scope")
    with _atomic(conn):
        _require_registered_human(conn, reviewer)
        reference = _reference_row(conn, reference_id)
        if reference is None:
            raise ValueError(f"unknown reference_id: {reference_id}")
        if absence_scope == "NOT_LOCATED_CORPUS":
            _require_verified_search_corpus(conn, reference)
        if reference["match_state"] == "PROBABLE_MATCH":
            raise ValueError("PROBABLE_MATCH cannot resolve absence")
        if absence_scope == "LOCATED_ELSEWHERE_CORPUS":
            if reference["match_state"] != "CONFIRMED_MATCH":
                raise ValueError("LOCATED_ELSEWHERE_CORPUS requires CONFIRMED_MATCH")
            if reference["resolved_record_id"] is None:
                raise ValueError("LOCATED_ELSEWHERE_CORPUS requires a resolved record")
            package_id = _reference_package_id(conn, reference["record_reference_id"])
            present_here = conn.execute(
                """
                SELECT 1 FROM occurrence JOIN source_file USING(source_file_id)
                WHERE record_id=? AND package_id=?
                """,
                (reference["resolved_record_id"], package_id),
            ).fetchone()
            present_elsewhere = conn.execute(
                """
                SELECT 1 FROM occurrence JOIN source_file USING(source_file_id)
                WHERE record_id=? AND package_id<>?
                """,
                (reference["resolved_record_id"], package_id),
            ).fetchone()
            if present_here or not present_elsewhere:
                raise ValueError("record is not located exclusively elsewhere in the corpus")
        elif reference["match_state"] != "NO_MATCH_LOCATED":
            raise ValueError("absence conclusions require NO_MATCH_LOCATED")

        _apply_reference_transition(
            conn,
            reference,
            {
                "absence_scope": absence_scope,
                "verification_state": "VERIFIED",
                "verified_by": reviewer,
            },
            "set reference absence scope",
            reviewer,
        )


def assign_reference_search_corpus(
    conn: sqlite3.Connection,
    reference_id: str,
    corpus_id: str,
    reviewer: str,
) -> None:
    """Assign an explicit search corpus that contains the reference's affected package."""
    _require_registered_human(conn, reviewer)
    reference = _reference_row(conn, reference_id)
    if reference is None:
        raise ValueError(f"unknown reference_id: {reference_id}")
    package_id = _reference_package_id(conn, reference["record_reference_id"])
    membership = conn.execute(
        "SELECT 1 FROM corpus_package WHERE corpus_id=? AND package_id=?",
        (corpus_id, package_id),
    ).fetchone()
    if membership is None:
        raise ValueError("search corpus must include the reference's affected package")
    _apply_reference_transition(
        conn,
        reference,
        {"search_corpus_id": corpus_id},
        "assign reference search corpus",
        reviewer,
    )


def _reference_row(conn: sqlite3.Connection, reference_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT record_reference_id, reference_id, match_state, matched_record_id,
               resolved_record_id, absence_scope, search_corpus_id
               , verification_state, verified_by
        FROM record_reference
        WHERE reference_id=? OR record_reference_id=?
        """,
        (reference_id, reference_id),
    ).fetchone()


def _reference_package_id(conn: sqlite3.Connection, record_reference_id: str) -> str:
    row = conn.execute(
        """
        SELECT source_file.package_id
        FROM record_reference
        JOIN occurrence ON occurrence.occurrence_id = record_reference.occurrence_id
        JOIN source_file ON source_file.source_file_id = occurrence.source_file_id
        WHERE record_reference.record_reference_id=?
        """,
        (record_reference_id,),
    ).fetchone()
    if row is None:
        raise ValueError("reference source occurrence is missing")
    return row[0]


def _require_verified_search_corpus(
    conn: sqlite3.Connection, reference: sqlite3.Row
) -> None:
    corpus_id = reference["search_corpus_id"]
    if corpus_id is None:
        raise ValueError("NOT_LOCATED_CORPUS requires an assigned search corpus")
    source_package_id = _reference_package_id(conn, reference["record_reference_id"])
    valid = conn.execute(
        """
        SELECT 1 FROM corpus
        WHERE corpus_id=? AND completeness_state='VERIFIED_COMPLETE'
          AND EXISTS (
              SELECT 1 FROM corpus_package
              WHERE corpus_id=corpus.corpus_id AND package_id=?
          )
          AND NOT EXISTS (
              SELECT 1 FROM corpus_package AS cp
              JOIN package AS p ON p.package_id=cp.package_id
              WHERE cp.corpus_id=corpus.corpus_id
                AND p.completeness_state<>'VERIFIED_COMPLETE'
          )
        """,
        (corpus_id, source_package_id),
    ).fetchone()
    if valid is None:
        raise ValueError("NOT_LOCATED_CORPUS requires a verified complete search corpus")


def _apply_reference_transition(
    conn: sqlite3.Connection,
    reference: sqlite3.Row,
    target_values: dict[str, str | None],
    reason: str,
    reviewer: str,
) -> None:
    changes = {
        field_name: (reference[field_name], new_value)
        for field_name, new_value in target_values.items()
        if reference[field_name] != new_value
    }
    if not changes:
        return

    changed_at = _utc_now()
    entity_id = reference["record_reference_id"]
    with _atomic(conn):
        for field_name, (previous_value, new_value) in changes.items():
            event_id = stable_id(
                "AE",
                "RECORD_REFERENCE",
                entity_id,
                field_name,
                previous_value or "",
                new_value or "",
                changed_at,
            )
            conn.execute(
                """
                INSERT INTO audit_event(
                    event_id, entity_type, entity_id, field_name, previous_value,
                    new_value, changed_at, reason, change_source
                ) VALUES (?, 'RECORD_REFERENCE', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    entity_id,
                    field_name,
                    previous_value,
                    new_value,
                    changed_at,
                    reason,
                    reviewer,
                ),
            )
        assignments = ", ".join(f"{field_name}=?" for field_name in target_values)
        predicates = " AND ".join(f"{field_name} IS ?" for field_name in reference.keys())
        updated = conn.execute(
            f"UPDATE record_reference SET {assignments} WHERE record_reference_id=? AND {predicates}",
            (*target_values.values(), entity_id, *tuple(reference)),
        )
        if updated.rowcount != 1:
            raise ValueError("reference state changed during transition")


@contextmanager
def _atomic(conn: sqlite3.Connection):
    """Make a service write all-or-nothing without committing caller-owned work."""
    conn.execute("SAVEPOINT evidence_service")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT evidence_service")
        conn.execute("RELEASE SAVEPOINT evidence_service")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT evidence_service")


def _require_verification_state(verification_state: str) -> None:
    if verification_state not in _VERIFICATION_STATES:
        raise ValueError("verification_state must be PROVISIONAL or VERIFIED")


def _require_registered_human(
    conn: sqlite3.Connection, reviewer: str | None
) -> None:
    if not isinstance(reviewer, str) or not reviewer:
        raise ValueError("reviewer must be a registered HUMAN identity")
    row = conn.execute(
        "SELECT identity_type FROM reviewer_identity WHERE reviewer_id=?", (reviewer,)
    ).fetchone()
    if row is None or row[0] != "HUMAN":
        raise ValueError("reviewer must be a registered HUMAN identity")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
