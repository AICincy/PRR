"""Sourced date facts, bounded temporal inferences, and legal-assessment gates."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
import re
import sqlite3

from metro_forensics.ids import stable_id


_DATE_ROLES = {
    "RECORD_DATE",
    "REFERENCE_DATE",
    "REQUEST_DATE",
    "RESPONSE_DATE",
    "DISCOVERY_DATE",
}
_PRECISIONS = {"DAY", "MONTH", "YEAR", "APPROXIMATE", "CONFLICTING"}
_NORMALIZED_PATTERNS = {
    "DAY": re.compile(r"\d{4}-\d{2}-\d{2}"),
    "MONTH": re.compile(r"\d{4}-\d{2}"),
    "YEAR": re.compile(r"\d{4}"),
}
_POSSESSION_SUPPORTING_FINDING_TYPES: frozenset[str] = frozenset()
_MONTH_NUMBERS = {
    month: number
    for number, month in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}


def add_date_fact(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    date_role: str,
    raw_value: str,
    normalized_value: str | None,
    precision: str,
    citation_id: str,
) -> str:
    """Append a cited date fact without turning uncertainty into an exact date."""
    _require_nonempty("entity_type", entity_type)
    _require_nonempty("entity_id", entity_id)
    if date_role not in _DATE_ROLES:
        raise ValueError("date_role must be a controlled date role")
    _require_nonempty("raw_value", raw_value)
    _validate_normalized_value(raw_value, normalized_value, precision)
    _require_source_citation(conn, citation_id)

    date_fact_id = stable_id(
        "DF",
        entity_type,
        entity_id,
        date_role,
        raw_value,
        normalized_value or "",
        precision,
        citation_id,
    )
    with _atomic(conn):
        conn.execute(
            """
            INSERT INTO date_fact(
                date_fact_id, entity_type, entity_id, date_role, value_text,
                precision_qualifier, raw_value, normalized_value, precision,
                evidence_citation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date_fact_id) DO NOTHING
            """,
            (
                date_fact_id,
                entity_type,
                entity_id,
                date_role,
                raw_value,
                precision,
                raw_value,
                normalized_value,
                precision,
                citation_id,
            ),
        )
    return date_fact_id


def add_temporal_inference(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    inference_type: str,
    supporting_date_fact_ids: list[str],
    *,
    supporting_finding_ids: list[str] | None = None,
    reviewer: str | None = None,
) -> str:
    """Append a date-based inference, never treating existence as possession."""
    _require_nonempty("entity_type", entity_type)
    _require_nonempty("entity_id", entity_id)
    _require_nonempty("inference_type", inference_type)
    possession = inference_type == "POSSESSED_AT_RESPONSE"
    supporting_finding_ids = supporting_finding_ids or []
    if "POSSESS" in inference_type.upper() and not possession:
        raise ValueError("unsupported possession inference type")
    if possession and not supporting_finding_ids:
        raise ValueError("possession requires independently VERIFIED non-date evidence")
    if not isinstance(supporting_date_fact_ids, list) or not supporting_date_fact_ids:
        raise ValueError("supporting_date_fact_ids are required")
    if len(set(supporting_date_fact_ids)) != len(supporting_date_fact_ids):
        raise ValueError("supporting_date_fact_ids must not contain duplicates")

    with _atomic(conn):
        for date_fact_id in supporting_date_fact_ids:
            fact = conn.execute(
                "SELECT entity_type, entity_id FROM date_fact WHERE date_fact_id=?",
                (date_fact_id,),
            ).fetchone()
            if fact is None:
                raise ValueError(f"unknown date_fact_id: {date_fact_id}")
            if (fact[0], fact[1]) != (entity_type, entity_id):
                raise ValueError("supporting date facts must concern the inferred entity")

        for finding_id in supporting_finding_ids:
            finding = conn.execute(
                "SELECT verification_state,finding_type FROM finding WHERE finding_id=?",
                (finding_id,),
            ).fetchone()
            if finding is None or finding[0] != "VERIFIED":
                raise ValueError("possession support must be a VERIFIED finding")
            if possession and finding[1] not in _POSSESSION_SUPPORTING_FINDING_TYPES:
                raise ValueError(
                    "the controlled vocabulary has no possession-supporting finding type"
                )
            if conn.execute(
                "SELECT 1 FROM finding_citation WHERE finding_id=?", (finding_id,)
            ).fetchone() is None:
                raise ValueError("possession support must be a cited VERIFIED finding")
        if possession:
            _require_human_reviewer(conn, reviewer)

        date_ids = sorted(supporting_date_fact_ids)
        finding_ids = sorted(supporting_finding_ids)
        temporal_inference_id = stable_id(
            "TI", entity_type, entity_id, inference_type, *date_ids, *finding_ids
        )
        conn.execute(
            """
            INSERT INTO temporal_inference(
                temporal_inference_id, entity_type, entity_id, proposition, inference_type,
                verification_state, verified_by, possession_supporting_finding_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(temporal_inference_id) DO NOTHING
            """,
            (
                temporal_inference_id,
                entity_type,
                entity_id,
                inference_type,
                inference_type,
                "VERIFIED" if possession else "PROVISIONAL",
                reviewer if possession else None,
                finding_ids[0] if possession else None,
            ),
        )
        for date_fact_id in date_ids:
            conn.execute(
                """
                INSERT INTO temporal_inference_date_fact(temporal_inference_id, date_fact_id)
                VALUES (?, ?)
                ON CONFLICT(temporal_inference_id, date_fact_id) DO NOTHING
                """,
                (temporal_inference_id, date_fact_id),
            )
        for finding_id in finding_ids:
            conn.execute(
                """
                INSERT INTO temporal_inference_finding(temporal_inference_id,finding_id)
                VALUES(?,?) ON CONFLICT(temporal_inference_id,finding_id) DO NOTHING
                """,
                (temporal_inference_id, finding_id),
            )
    return temporal_inference_id


def create_legal_assessment(
    conn: sqlite3.Connection,
    legal_question: str,
    conclusion: str,
    finding_ids: list[str],
    authorities: list[tuple[str, str]],
) -> str:
    """Create a draft legal assessment linked to factual findings and authorities."""
    _require_nonempty("legal_question", legal_question)
    _require_nonempty("conclusion", conclusion)
    _validate_identifiers("finding_ids", finding_ids)
    _validate_authorities(authorities)

    ordered_findings = sorted(finding_ids)
    ordered_authorities = sorted(authorities)
    assessment_id = stable_id(
        "LA",
        legal_question,
        conclusion,
        *ordered_findings,
        *(f"{authority_type}\x1e{citation}" for authority_type, citation in ordered_authorities),
    )
    with _atomic(conn):
        for finding_id in ordered_findings:
            if conn.execute(
                "SELECT 1 FROM finding WHERE finding_id=?", (finding_id,)
            ).fetchone() is None:
                raise ValueError(f"unknown finding_id: {finding_id}")
        if conn.execute(
            "SELECT 1 FROM legal_assessment WHERE legal_assessment_id=?", (assessment_id,)
        ).fetchone():
            return assessment_id

        _append_audit(
            conn,
            assessment_id,
            "CREATE",
            None,
            "DRAFT",
            "create legal assessment",
        )
        conn.execute(
            """
            INSERT INTO legal_assessment(legal_assessment_id, legal_question, conclusion)
            VALUES (?, ?, ?)
            """,
            (assessment_id, legal_question, conclusion),
        )
        for finding_id in ordered_findings:
            conn.execute(
                """
                INSERT INTO legal_assessment_finding(legal_assessment_id, finding_id)
                VALUES (?, ?)
                """,
                (assessment_id, finding_id),
            )
        for authority_type, citation in ordered_authorities:
            authority_id = stable_id("LAR", assessment_id, authority_type, citation)
            conn.execute(
                """
                INSERT INTO legal_authority(
                    legal_authority_id, legal_assessment_id, authority_type, citation
                ) VALUES (?, ?, ?, ?)
                """,
                (authority_id, assessment_id, authority_type, citation),
            )
            conn.execute(
                """
                INSERT INTO legal_assessment_authority(
                    legal_assessment_id,legal_authority_id,association_basis
                ) VALUES(?,?,'EXPLICIT')
                """,
                (assessment_id, authority_id),
            )
    return assessment_id


def finalize_legal_assessment(
    conn: sqlite3.Connection, assessment_id: str, finalizer: str | None = None
) -> None:
    """Finalize only an authority-cited assessment based solely on verified findings."""
    with _atomic(conn):
        assessment = conn.execute(
            "SELECT assessment_status FROM legal_assessment WHERE legal_assessment_id=?",
            (assessment_id,),
        ).fetchone()
        if assessment is None:
            raise ValueError(f"unknown assessment_id: {assessment_id}")
        bad = conn.execute(
            """
            SELECT count(*)
            FROM legal_assessment_finding AS laf
            JOIN finding AS f ON f.finding_id=laf.finding_id
            WHERE laf.legal_assessment_id=? AND f.verification_state <> 'VERIFIED'
            """,
            (assessment_id,),
        ).fetchone()[0]
        if bad:
            raise ValueError("final legal assessment requires VERIFIED findings only")
        support = conn.execute(
            """
            SELECT count(*)
            FROM legal_assessment_finding AS laf
            JOIN finding AS f ON f.finding_id=laf.finding_id
            WHERE laf.legal_assessment_id=?
              AND f.verification_state='VERIFIED'
              AND EXISTS (
                  SELECT 1 FROM finding_citation AS fc
                  JOIN evidence_citation AS ec
                    ON ec.evidence_citation_id=fc.evidence_citation_id
                  WHERE fc.finding_id=f.finding_id
                    AND is_exact_locator(ec.locator)=1
              )
            """,
            (assessment_id,),
        ).fetchone()[0]
        if support == 0:
            raise ValueError("final legal assessment requires at least one VERIFIED cited finding")
        authority_count = conn.execute(
            "SELECT count(*) FROM legal_assessment_authority WHERE legal_assessment_id=?",
            (assessment_id,),
        ).fetchone()[0]
        if authority_count == 0:
            raise ValueError("final legal assessment requires cited authority")
        if assessment[0] == "FINAL":
            return
        _require_human_reviewer(conn, finalizer)
        finalized_at = datetime.now(timezone.utc).isoformat()
        _append_audit(
            conn,
            assessment_id,
            "assessment_status",
            assessment[0],
            "FINAL",
            "finalize legal assessment",
            finalizer,
        )
        updated = conn.execute(
            """
            UPDATE legal_assessment
            SET assessment_status='FINAL', finalized_by=?, finalized_at=?
            WHERE legal_assessment_id=? AND assessment_status IS ?
            """,
            (finalizer, finalized_at, assessment_id, assessment[0]),
        )
        if updated.rowcount != 1:
            raise ValueError("legal assessment status changed during finalization")


def set_legal_assessment_status(
    conn: sqlite3.Connection,
    assessment_id: str,
    status: str,
    reviewer: str,
) -> None:
    """Move a draft assessment between non-final review states with human attribution."""
    if status not in {"DRAFT", "QUALIFIED"}:
        raise ValueError("status must be DRAFT or QUALIFIED; use finalization for FINAL")
    with _atomic(conn):
        _require_human_reviewer(conn, reviewer)
        row = conn.execute(
            "SELECT assessment_status FROM legal_assessment WHERE legal_assessment_id=?",
            (assessment_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown assessment_id: {assessment_id}")
        previous = row[0]
        if previous == "FINAL":
            raise ValueError("a FINAL legal assessment cannot be reopened")
        if previous == status:
            return
        _append_audit(
            conn,
            assessment_id,
            "assessment_status",
            previous,
            status,
            "human legal review transition",
            reviewer,
        )
        updated = conn.execute(
            "UPDATE legal_assessment SET assessment_status=? "
            "WHERE legal_assessment_id=? AND assessment_status IS ?",
            (status, assessment_id, previous),
        )
        if updated.rowcount != 1:
            raise ValueError("legal assessment status changed during transition")


def _validate_normalized_value(
    raw_value: str, normalized_value: str | None, precision: str
) -> None:
    if precision not in _PRECISIONS:
        raise ValueError("precision must be DAY, MONTH, YEAR, APPROXIMATE, or CONFLICTING")
    if normalized_value is not None and not isinstance(normalized_value, str):
        raise ValueError("normalized_value must be a string or None")
    if precision in {"APPROXIMATE", "CONFLICTING"}:
        if normalized_value is not None:
            raise ValueError(f"{precision} dates must not be normalized as exact values")
        return
    if normalized_value is None:
        return
    if _NORMALIZED_PATTERNS[precision].fullmatch(normalized_value) is None:
        raise ValueError(f"normalized_value does not match {precision} precision")
    _require_valid_calendar_date(normalized_value, precision)
    source_normalized = _parse_deterministic_raw_value(raw_value, precision)
    if source_normalized is None:
        raise ValueError(
            "normalized_value requires a raw_value with deterministic matching precision"
        )
    if source_normalized != normalized_value:
        raise ValueError("normalized_value must exactly match the deterministic raw_value date")


def _require_valid_calendar_date(normalized_value: str, precision: str) -> None:
    values = [int(part) for part in normalized_value.split("-")]
    try:
        if precision == "DAY":
            date(values[0], values[1], values[2])
        elif precision == "MONTH":
            date(values[0], values[1], 1)
        else:
            date(values[0], 1, 1)
    except ValueError as error:
        raise ValueError("normalized_value must be a valid calendar date") from error


def _parse_deterministic_raw_value(raw_value: str, precision: str) -> str | None:
    raw = raw_value.strip()
    if precision == "YEAR":
        if re.fullmatch(r"\d{4}", raw) is None:
            return None
        _require_valid_calendar_date(raw, precision)
        return raw

    if precision == "MONTH":
        iso_match = re.fullmatch(r"(\d{4})-(\d{2})", raw)
        named_match = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", raw)
        if iso_match is not None:
            normalized = raw
        elif named_match is not None:
            month = _MONTH_NUMBERS.get(named_match.group(1).lower())
            if month is None:
                return None
            normalized = f"{named_match.group(2)}-{month:02d}"
        else:
            return None
        _require_valid_calendar_date(normalized, precision)
        return normalized

    iso_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    named_match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", raw)
    if iso_match is not None:
        normalized = raw
    elif named_match is not None:
        month = _MONTH_NUMBERS.get(named_match.group(1).lower())
        if month is None:
            return None
        normalized = f"{named_match.group(3)}-{month:02d}-{int(named_match.group(2)):02d}"
    else:
        return None
    _require_valid_calendar_date(normalized, precision)
    return normalized


def _require_source_citation(conn: sqlite3.Connection, citation_id: str) -> None:
    _require_nonempty("citation_id", citation_id)
    row = conn.execute(
        """
        SELECT 1
        FROM evidence_citation
        LEFT JOIN source_file AS direct_source
            ON direct_source.source_file_id=evidence_citation.source_file_id
        LEFT JOIN occurrence
            ON occurrence.occurrence_id=evidence_citation.occurrence_id
        LEFT JOIN source_file AS occurrence_source
            ON occurrence_source.source_file_id=occurrence.source_file_id
        WHERE evidence_citation.evidence_citation_id=?
          AND COALESCE(direct_source.source_file_id, occurrence_source.source_file_id) IS NOT NULL
        """,
        (citation_id,),
    ).fetchone()
    if row is None:
        raise ValueError("date facts require a source-backed citation")


def _validate_identifiers(name: str, values: list[str]) -> None:
    if not isinstance(values, list):
        raise ValueError(f"{name} must be a list")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"{name} must contain non-empty identifiers")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must not contain duplicates")


def _validate_authorities(authorities: list[tuple[str, str]]) -> None:
    if not isinstance(authorities, list):
        raise ValueError("authorities must be a list")
    for authority in authorities:
        if (
            not isinstance(authority, tuple)
            or len(authority) != 2
            or not all(isinstance(value, str) and value for value in authority)
        ):
            raise ValueError("authorities must contain (authority_type, citation) pairs")
    if len(set(authorities)) != len(authorities):
        raise ValueError("authorities must not contain duplicates")


def _append_audit(
    conn: sqlite3.Connection,
    assessment_id: str,
    field_name: str,
    previous_value: str | None,
    new_value: str | None,
    reason: str,
    source: str = "system",
) -> None:
    changed_at = datetime.now(timezone.utc).isoformat()
    event_id = stable_id(
        "AE",
        "LEGAL_ASSESSMENT",
        assessment_id,
        field_name,
        previous_value or "",
        new_value or "",
        changed_at,
    )
    conn.execute(
        """
        INSERT INTO audit_event(
            event_id, entity_type, entity_id, field_name, previous_value, new_value,
            changed_at, reason, change_source
        ) VALUES (?, 'LEGAL_ASSESSMENT', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, assessment_id, field_name, previous_value, new_value,
            changed_at, reason, source,
        ),
    )


def _require_human_reviewer(
    conn: sqlite3.Connection, reviewer: str | None
) -> None:
    if not isinstance(reviewer, str) or not reviewer:
        raise ValueError("finalizer/reviewer must be a registered HUMAN identity")
    row = conn.execute(
        "SELECT identity_type FROM reviewer_identity WHERE reviewer_id=?", (reviewer,)
    ).fetchone()
    if row is None or row[0] != "HUMAN":
        raise ValueError("finalizer/reviewer must be a registered HUMAN identity")


def _require_nonempty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")


@contextmanager
def _atomic(conn: sqlite3.Connection):
    conn.execute("SAVEPOINT temporal_legal_service")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT temporal_legal_service")
        conn.execute("RELEASE SAVEPOINT temporal_legal_service")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT temporal_legal_service")
