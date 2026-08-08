import sqlite3

from metro_forensics.ids import stable_id
from metro_forensics.locators import require_exact_locator
from metro_forensics.review import open_review_task


_VERIFICATION_STATES = {"PROVISIONAL", "VERIFIED"}
def create_record(
    conn: sqlite3.Connection,
    title: str,
    content_fingerprint: str,
    record_type: str | None = None,
) -> str:
    """Create or return the record identified by its exact content fingerprint."""
    content_fingerprint = _normalize_content_fingerprint(content_fingerprint)
    existing_record_id = resolve_exact_duplicate(conn, content_fingerprint)
    if existing_record_id is not None:
        return existing_record_id

    record_id = stable_id("R", content_fingerprint)
    conn.execute(
        """
        INSERT INTO record(
            record_id, title_or_description, record_type,
            content_fingerprint, canonical_identity_basis
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            record_id,
            title,
            record_type,
            content_fingerprint,
            f"exact content fingerprint: {content_fingerprint}",
        ),
    )
    return record_id


def create_occurrence(
    conn: sqlite3.Connection,
    record_id: str,
    source_file_id: str,
    derivative_id: str | None,
    locator: str,
    verification_state: str,
    verified_by: str | None = None,
) -> str:
    """Record one traceable Level 2 occurrence of a record."""
    require_exact_locator(locator)
    if verification_state not in _VERIFICATION_STATES:
        raise ValueError("verification_state must be PROVISIONAL or VERIFIED")
    if verification_state != "PROVISIONAL":
        raise ValueError(
            "occurrences must be created PROVISIONAL and promoted after human review"
        )
    if derivative_id is not None:
        derivative = conn.execute(
            "SELECT source_file_id FROM derivative WHERE derivative_id=?", (derivative_id,)
        ).fetchone()
        if derivative is None:
            raise ValueError(f"unknown derivative_id: {derivative_id}")
        if derivative[0] != source_file_id:
            raise ValueError("occurrence derivative must belong to the same source")

    occurrence_id = stable_id("O", record_id, source_file_id, locator)
    existing = conn.execute(
        """
        SELECT occurrence_id, derivative_id, verification_state, verified_by
        FROM occurrence
        WHERE record_id=? AND source_file_id=? AND source_locator=?
        """,
        (record_id, source_file_id, locator),
    ).fetchone()
    if existing is not None:
        # Verification state and reviewer attribution are mutable only through the
        # audited promotion path.  A repeated creation call therefore compares the
        # immutable creation provenance, not the occurrence's later review state.
        if existing["derivative_id"] != derivative_id:
            raise ValueError(
                "occurrence request conflicts with existing evidentiary provenance"
            )
        return existing["occurrence_id"]

    conn.execute(
        """
        INSERT INTO occurrence(
            occurrence_id, record_id, source_file_id, derivative_id,
            source_locator, verification_state, verified_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            occurrence_id,
            record_id,
            source_file_id,
            derivative_id,
            locator,
            verification_state,
            verified_by,
        ),
    )
    return occurrence_id


def resolve_exact_duplicate(
    conn: sqlite3.Connection, content_fingerprint: str
) -> str | None:
    """Return the existing record only when the exact content matches."""
    content_fingerprint = _normalize_content_fingerprint(content_fingerprint)
    row = conn.execute(
        "SELECT record_id FROM record WHERE content_fingerprint=?",
        (content_fingerprint,),
    ).fetchone()
    return None if row is None else row[0]


def link_version_family(
    conn: sqlite3.Connection,
    older_record_id: str,
    newer_record_id: str,
    basis: str,
) -> None:
    """Preserve the relationship between materially distinct record versions."""
    link_id = stable_id(
        "RVL", older_record_id, newer_record_id, "MATERIALLY_DIFFERENT_VERSION", basis
    )
    conn.execute(
        """
        INSERT INTO record_version_link(
            record_version_link_id, record_id, related_record_id,
            relationship_description, evidence_basis
        ) VALUES (?, ?, ?, 'MATERIALLY_DIFFERENT_VERSION', ?)
        ON CONFLICT(record_id, related_record_id, relationship_description) DO NOTHING
        """,
        (link_id, older_record_id, newer_record_id, basis),
    )


def open_boundary_review(
    conn: sqlite3.Connection,
    source_file_id: str,
    locator: str,
    candidate_description: str,
) -> str:
    """Open a review task instead of automatically segmenting an ambiguous record."""
    return open_review_task(
        conn,
        "SOURCE_FILE",
        source_file_id,
        "AMBIGUOUS_LEVEL2_BOUNDARY",
        candidate_description,
        task_type="SEGMENTATION_REVIEW",
        task_key=locator,
    )


def _normalize_content_fingerprint(content_fingerprint: str) -> str:
    import re

    if not isinstance(content_fingerprint, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", content_fingerprint
    ):
        raise ValueError("content_fingerprint must be a 64-character hexadecimal digest")
    return content_fingerprint.lower()
