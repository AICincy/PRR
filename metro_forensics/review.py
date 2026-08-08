"""Human review gates and append-only transitions for the forensic ledger."""

from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3

from metro_forensics.ids import stable_id


_REVIEW_STATES = {"RESOLVED", "UNRESOLVED"}
_COMPLETENESS_STATES = {
    "IN_PROGRESS",
    "REVIEW_REQUIRED",
    "COMPLETE_WITH_EXCEPTIONS",
    "VERIFIED_COMPLETE",
}
_ENTITY_QUERIES = {
    "PACKAGE": ("SELECT package_id FROM package WHERE package_id=?", ("package_id",)),
    "REQUEST_ELEMENT": (
        "SELECT package_id, request_element_id FROM request_element WHERE request_element_id=?",
        ("request_element_id",),
    ),
    "SOURCE_FILE": (
        "SELECT package_id, source_file_id FROM source_file WHERE source_file_id=?",
        ("source_file_id",),
    ),
    "OCCURRENCE": (
        """
        SELECT source_file.package_id, occurrence.occurrence_id
        FROM occurrence JOIN source_file USING(source_file_id)
        WHERE occurrence.occurrence_id=?
        """,
        ("occurrence_id",),
    ),
    "RECORD_REFERENCE": (
        """
        SELECT source_file.package_id, record_reference.record_reference_id
        FROM record_reference
        JOIN occurrence ON occurrence.occurrence_id = record_reference.occurrence_id
        JOIN source_file ON source_file.source_file_id = occurrence.source_file_id
        WHERE record_reference.reference_id=? OR record_reference.record_reference_id=?
        """,
        ("record_reference_id",),
    ),
    "FINDING": (
        "SELECT package_id, finding_id FROM finding WHERE finding_id=?",
        ("finding_id",),
    ),
}
_EDITABLE_FIELDS = {
    ("RECORD", "record"): {
        "title_or_description", "record_type", "version_family_key", "notes"
    },
    ("FINDING", "finding"): {"proposition", "finding_type", "notes"},
    ("PACKAGE", "package"): {"package_status", "notes"},
    ("REVIEW_TASK", "review_task"): {"concern"},
    ("RECORD_REFERENCE", "record_reference"): {"notes"},
    ("OCCURRENCE", "occurrence"): {"notes"},
}
_TABLE_IDENTITIES = {
    "record": "record_id",
    "finding": "finding_id",
    "package": "package_id",
    "review_task": "review_task_id",
    "record_reference": "record_reference_id",
    "occurrence": "occurrence_id",
}


def register_reviewer_identity(
    conn: sqlite3.Connection, reviewer_id: str, identity_type: str = "HUMAN"
) -> None:
    """Register an attributable identity without silently changing an existing type."""
    if not isinstance(reviewer_id, str) or not reviewer_id:
        raise ValueError("reviewer_id is required")
    if identity_type not in {"HUMAN", "AUTOMATION"}:
        raise ValueError("identity_type must be HUMAN or AUTOMATION")
    existing = conn.execute(
        "SELECT identity_type FROM reviewer_identity WHERE reviewer_id=?", (reviewer_id,)
    ).fetchone()
    if existing is not None and existing[0] != identity_type:
        raise ValueError("reviewer identity type is immutable")
    conn.execute(
        "INSERT INTO reviewer_identity(reviewer_id,identity_type) VALUES(?,?) "
        "ON CONFLICT(reviewer_id) DO NOTHING",
        (reviewer_id, identity_type),
    )


def add_corpus(
    conn: sqlite3.Connection, corpus_id: str, corpus_name: str, notes: str = ""
) -> None:
    """Create an explicit corpus scope and audit its analytical existence."""
    if not isinstance(corpus_id, str) or not corpus_id:
        raise ValueError("corpus_id is required")
    if not isinstance(corpus_name, str) or not corpus_name:
        raise ValueError("corpus_name is required")
    existing = conn.execute(
        "SELECT corpus_name,notes FROM corpus WHERE corpus_id=?", (corpus_id,)
    ).fetchone()
    if existing is not None:
        if tuple(existing) != (corpus_name, notes):
            raise ValueError("corpus metadata drift")
        return
    with _atomic(conn):
        _insert_audit(
            conn, "CORPUS", corpus_id, "CREATE", None, "IN_PROGRESS", _utc_now(),
            "define corpus scope", "system",
        )
        conn.execute(
            "INSERT INTO corpus(corpus_id,corpus_name,notes) VALUES(?,?,?)",
            (corpus_id, corpus_name, notes),
        )


def add_corpus_package(
    conn: sqlite3.Connection, corpus_id: str, package_id: str, reviewer: str
) -> None:
    """Add one audited package membership to a mutable corpus scope."""
    with _atomic(conn):
        _require_human_reviewer(conn, reviewer)
        if conn.execute("SELECT 1 FROM corpus WHERE corpus_id=?", (corpus_id,)).fetchone() is None:
            raise ValueError(f"unknown corpus_id: {corpus_id}")
        if conn.execute("SELECT 1 FROM package WHERE package_id=?", (package_id,)).fetchone() is None:
            raise ValueError(f"unknown package_id: {package_id}")
        if conn.execute(
            "SELECT 1 FROM corpus_package WHERE corpus_id=? AND package_id=?",
            (corpus_id, package_id),
        ).fetchone():
            return
        _insert_audit(
            conn, "CORPUS", corpus_id, "package_membership", None, package_id,
            _utc_now(), "add corpus package", reviewer,
        )
        conn.execute(
            "INSERT INTO corpus_package(corpus_id,package_id) VALUES(?,?)",
            (corpus_id, package_id),
        )


def open_review_task(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    reason_code: str,
    description: str,
    material: bool = True,
    *,
    task_type: str = "FORENSIC_REVIEW",
    task_key: str = "",
    creation_source: str = "automation",
) -> str:
    """Open an attributable human-review task for one in-scope analytical entity."""
    if entity_type not in _ENTITY_QUERIES:
        raise ValueError("unknown review entity type")
    if not isinstance(reason_code, str) or not reason_code:
        raise ValueError("reason_code is required")
    if not isinstance(description, str) or not description:
        raise ValueError("description is required")
    if not isinstance(material, bool):
        raise ValueError("material must be a boolean")
    with _atomic(conn):
        package_id, scope = _review_scope(conn, entity_type, entity_id)
        review_task_id = stable_id(
            "RT", entity_type, entity_id, reason_code, description, str(material), task_key
        )
        existing = conn.execute(
            """
            SELECT package_id,request_element_id,source_file_id,occurrence_id,
                   record_reference_id,finding_id,task_type,reason_code,material
            FROM review_task WHERE review_task_id=?
            """,
            (review_task_id,),
        ).fetchone()
        if existing is not None:
            expected = (
                package_id,
                scope.get("request_element_id"),
                scope.get("source_file_id"),
                scope.get("occurrence_id"),
                scope.get("record_reference_id"),
                scope.get("finding_id"),
                task_type,
                reason_code,
                int(material),
            )
            if tuple(existing) != expected:
                raise ValueError("review task metadata drift")
            return review_task_id
        changed_at = _utc_now()
        _insert_audit(
            conn,
            "REVIEW_TASK",
            review_task_id,
            "CREATE",
            None,
            "OPEN",
            changed_at,
            f"open review task for {task_key}" if task_key else "open review task",
            creation_source,
        )
        conn.execute(
            """
            INSERT INTO review_task(
                review_task_id, package_id, request_element_id, source_file_id,
                occurrence_id, record_reference_id, finding_id, task_type, reason_code,
                material, concern
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_task_id,
                package_id,
                scope.get("request_element_id"),
                scope.get("source_file_id"),
                scope.get("occurrence_id"),
                scope.get("record_reference_id"),
                scope.get("finding_id"),
                task_type,
                reason_code,
                int(material),
                description,
            ),
        )
    return review_task_id


def resolve_review_task(
    conn: sqlite3.Connection,
    task_id: str,
    state: str,
    reviewer: str,
    resolved_at: str,
    decision: str,
    source_locator: str | None,
) -> None:
    """Resolve or explicitly retain a review question with an auditable decision."""
    if state not in _REVIEW_STATES:
        raise ValueError("state must be RESOLVED or UNRESOLVED")
    if not isinstance(resolved_at, str) or not resolved_at:
        raise ValueError("resolved_at is required")
    with _atomic(conn):
        _require_human_reviewer(conn, reviewer)
        task = conn.execute(
            "SELECT * FROM review_task WHERE review_task_id=?", (task_id,)
        ).fetchone()
        if task is None:
            raise ValueError(f"unknown review task: {task_id}")
        if task["task_state"] != "OPEN":
            raise ValueError("only OPEN review tasks can be resolved")
        citation_id = None
        resolution = None
        if state == "RESOLVED":
            if not isinstance(decision, str) or not decision:
                raise ValueError("decision is required for RESOLVED review tasks")
            if not isinstance(source_locator, str) or not source_locator:
                raise ValueError("source_locator is required for RESOLVED review tasks")
            citation_id = _source_backed_citation(conn, task["package_id"], source_locator)
            if citation_id is None:
                raise ValueError("source_locator must match a source-backed citation in the task package")
            resolution = decision
        target_values = {
            "task_state": state,
            "reviewer": reviewer,
            "resolved_at": resolved_at,
            "resolution": resolution,
            "supporting_citation_id": citation_id,
        }
        for field, value in target_values.items():
            _insert_audit(
                conn,
                "REVIEW_TASK",
                task_id,
                field,
                task[field],
                value,
                resolved_at,
                "resolve review task",
                reviewer,
                citation_id,
            )
        assignments = ", ".join(f"{field}=?" for field in target_values)
        updated = conn.execute(
            f"UPDATE review_task SET {assignments} WHERE review_task_id=? AND task_state='OPEN'",
            (*target_values.values(), task_id),
        )
        if updated.rowcount != 1:
            raise ValueError("review task state changed during resolution")


def promote_finding_verified(
    conn: sqlite3.Connection,
    finding_id: str,
    reviewer: str,
    verified_at: str,
    citation_ids: list[str],
) -> None:
    """Promote a provisional finding only with human attribution and source evidence."""
    if not isinstance(verified_at, str) or not verified_at:
        raise ValueError("verified_at is required")
    if not isinstance(citation_ids, list) or not citation_ids:
        raise ValueError("one or more citation_ids are required")
    if len(set(citation_ids)) != len(citation_ids):
        raise ValueError("citation_ids must not contain duplicates")

    with _atomic(conn):
        _require_human_reviewer(conn, reviewer)
        finding = conn.execute(
            "SELECT * FROM finding WHERE finding_id=?", (finding_id,)
        ).fetchone()
        if finding is None:
            raise ValueError(f"unknown finding_id: {finding_id}")
        if finding["verification_state"] == "VERIFIED":
            if finding["verified_by"] == reviewer:
                return
            raise ValueError("a verified finding cannot change verifier provenance")
        if finding["verification_state"] != "PROVISIONAL":
            raise ValueError("only PROVISIONAL findings can be verified")
        _validate_finding_citations(conn, finding, citation_ids)
        for citation_id in citation_ids:
            conn.execute(
                "INSERT INTO finding_citation(finding_id, evidence_citation_id) VALUES (?, ?)",
                (finding_id, citation_id),
            )
        _insert_audit(
            conn,
            "FINDING",
            finding_id,
            "verification_state",
            "PROVISIONAL",
            "VERIFIED",
            verified_at,
            "human verification",
            reviewer,
        )
        _insert_audit(
            conn,
            "FINDING",
            finding_id,
            "verified_by",
            finding["verified_by"],
            reviewer,
            verified_at,
            "human verification",
            reviewer,
        )
        updated = conn.execute(
            """
            UPDATE finding SET verification_state='VERIFIED', verified_by=?
            WHERE finding_id=? AND verification_state='PROVISIONAL' AND verified_by IS ?
            """,
            (reviewer, finding_id, finding["verified_by"]),
        )
        if updated.rowcount != 1:
            raise ValueError("finding state changed during verification")


def change_with_audit(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    table: str,
    field: str,
    value: str,
    reason: str,
    source: str,
) -> None:
    """Apply one whitelisted substantive edit after appending its audit event."""
    key = (entity_type, table)
    if key not in _EDITABLE_FIELDS or field not in _EDITABLE_FIELDS[key]:
        raise ValueError("table and field are not an auditable editable analytical value")
    if not isinstance(reason, str) or not reason:
        raise ValueError("reason is required")
    if not isinstance(source, str) or not source:
        raise ValueError("source is required")
    with _atomic(conn):
        _require_human_reviewer(conn, source)
        identity = _TABLE_IDENTITIES[table]
        row = conn.execute(
            f"SELECT {field} FROM {table} WHERE {identity}=?", (entity_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown {entity_type} entity: {entity_id}")
        previous_value = row[field]
        if previous_value == value:
            return
        changed_at = _utc_now()
        _insert_audit(
            conn,
            entity_type,
            entity_id,
            field,
            previous_value,
            value,
            changed_at,
            reason,
            source,
        )
        updated = conn.execute(
            f"UPDATE {table} SET {field}=? WHERE {identity}=? AND {field} IS ?",
            (value, entity_id, previous_value),
        )
        if updated.rowcount != 1:
            raise ValueError(f"{entity_type} state changed during audited update")


def set_package_completeness(
    conn: sqlite3.Connection, package_id: str, state: str, reviewer: str
) -> None:
    """Set a package's reviewed completeness state without bypassing material tasks."""
    if state not in _COMPLETENESS_STATES:
        raise ValueError("unknown package completeness state")
    with _atomic(conn):
        _require_human_reviewer(conn, reviewer)
        package = conn.execute(
            "SELECT completeness_state FROM package WHERE package_id=?", (package_id,)
        ).fetchone()
        if package is None:
            raise ValueError(f"unknown package_id: {package_id}")
        if state == "VERIFIED_COMPLETE":
            _validate_package_verified_complete(conn, package_id)
        if package["completeness_state"] == state:
            return
        changed_at = _utc_now()
        _insert_audit(
            conn,
            "PACKAGE",
            package_id,
            "completeness_state",
            package["completeness_state"],
            state,
            changed_at,
            "set package completeness",
            reviewer,
        )
        updated = conn.execute(
            "UPDATE package SET completeness_state=? WHERE package_id=? AND completeness_state IS ?",
            (state, package_id, package["completeness_state"]),
        )
        if updated.rowcount != 1:
            raise ValueError("package state changed during completeness transition")


def set_corpus_completeness(
    conn: sqlite3.Connection, corpus_id: str, state: str, reviewer: str
) -> None:
    """Apply one audited corpus state transition over its declared package membership."""
    if state not in _COMPLETENESS_STATES:
        raise ValueError("unknown corpus completeness state")
    with _atomic(conn):
        _require_human_reviewer(conn, reviewer)
        corpus = conn.execute(
            "SELECT completeness_state FROM corpus WHERE corpus_id=?", (corpus_id,)
        ).fetchone()
        if corpus is None:
            raise ValueError(f"unknown corpus_id: {corpus_id}")
        if state == "VERIFIED_COMPLETE":
            if conn.execute(
                "SELECT 1 FROM corpus_package WHERE corpus_id=?", (corpus_id,)
            ).fetchone() is None:
                raise ValueError("VERIFIED_COMPLETE corpus requires package membership")
            if conn.execute(
                """
                SELECT 1 FROM corpus_package AS cp
                JOIN package AS p ON p.package_id=cp.package_id
                WHERE cp.corpus_id=? AND p.completeness_state<>'VERIFIED_COMPLETE'
                LIMIT 1
                """,
                (corpus_id,),
            ).fetchone():
                raise ValueError("all corpus packages must be VERIFIED_COMPLETE")
        if corpus[0] == state:
            return
        changed_at = _utc_now()
        _insert_audit(
            conn, "CORPUS", corpus_id, "completeness_state", corpus[0], state,
            changed_at, "set corpus completeness", reviewer,
        )
        updated = conn.execute(
            "UPDATE corpus SET completeness_state=? WHERE corpus_id=? AND completeness_state IS ?",
            (state, corpus_id, corpus[0]),
        )
        if updated.rowcount != 1:
            raise ValueError("corpus state changed during completeness transition")


def promote_occurrence_verified(
    conn: sqlite3.Connection, occurrence_id: str, reviewer: str, verified_at: str
) -> None:
    """Promote an exact-located occurrence through an audited human transition."""
    with _atomic(conn):
        _require_human_reviewer(conn, reviewer)
        occurrence = conn.execute(
            "SELECT verification_state,verified_by FROM occurrence WHERE occurrence_id=?",
            (occurrence_id,),
        ).fetchone()
        if occurrence is None:
            raise ValueError(f"unknown occurrence_id: {occurrence_id}")
        if occurrence[0] == "VERIFIED":
            if occurrence[1] == reviewer:
                return
            raise ValueError("verified occurrence provenance is immutable")
        for field, old, new in (
            ("verification_state", occurrence[0], "VERIFIED"),
            ("verified_by", occurrence[1], reviewer),
        ):
            _insert_audit(
                conn, "OCCURRENCE", occurrence_id, field, old, new, verified_at,
                "human occurrence verification", reviewer,
            )
        conn.execute(
            "UPDATE occurrence SET verification_state='VERIFIED',verified_by=? "
            "WHERE occurrence_id=? AND verification_state='PROVISIONAL'",
            (reviewer, occurrence_id),
        )


def promote_metro_statement_verified(
    conn: sqlite3.Connection, statement_id: str, reviewer: str, verified_at: str
) -> None:
    """Promote Metro's exact cited text through an audited human source check."""
    with _atomic(conn):
        _require_human_reviewer(conn, reviewer)
        statement = conn.execute(
            "SELECT verification_state,verified_by FROM metro_statement WHERE metro_statement_id=?",
            (statement_id,),
        ).fetchone()
        if statement is None:
            raise ValueError(f"unknown statement_id: {statement_id}")
        if statement[0] == "VERIFIED":
            if statement[1] == reviewer:
                return
            raise ValueError("verified statement provenance is immutable")
        for field, old, new in (
            ("verification_state", statement[0], "VERIFIED"),
            ("verified_by", statement[1], reviewer),
        ):
            _insert_audit(
                conn, "METRO_STATEMENT", statement_id, field, old, new, verified_at,
                "human statement verification", reviewer,
            )
        conn.execute(
            "UPDATE metro_statement SET verification_state='VERIFIED',verified_by=? "
            "WHERE metro_statement_id=? AND verification_state='PROVISIONAL'",
            (reviewer, statement_id),
        )


def corpus_completeness(conn: sqlite3.Connection) -> str:
    """Return the conservative aggregate state across all included packages."""
    states = [row[0] for row in conn.execute("SELECT completeness_state FROM package")]
    if states and all(state == "VERIFIED_COMPLETE" for state in states):
        return "VERIFIED_COMPLETE"
    if any(state == "REVIEW_REQUIRED" for state in states):
        return "REVIEW_REQUIRED"
    if states and all(state in {"VERIFIED_COMPLETE", "COMPLETE_WITH_EXCEPTIONS"} for state in states):
        return "COMPLETE_WITH_EXCEPTIONS"
    return "IN_PROGRESS"


def _validate_package_verified_complete(
    conn: sqlite3.Connection, package_id: str
) -> None:
    inventory = conn.execute(
        """
        SELECT expected_level1_count,
               (SELECT count(*) FROM source_file WHERE package_id=package.package_id)
        FROM package WHERE package_id=?
        """,
        (package_id,),
    ).fetchone()
    if inventory is None or inventory[0] != inventory[1]:
        raise ValueError("declared Level 1 inventory must equal actual inventory")

    missing_terminal = conn.execute(
        """
        SELECT 1 FROM source_file AS sf
        WHERE sf.package_id=? AND NOT EXISTS (
            SELECT 1 FROM processing_run AS pr
            JOIN derivative AS d ON d.processing_run_id=pr.processing_run_id
                                AND d.source_file_id=pr.source_file_id
            WHERE pr.source_file_id=sf.source_file_id
              AND pr.completed_at IS NOT NULL AND pr.errors=''
        ) LIMIT 1
        """,
        (package_id,),
    ).fetchone()
    if missing_terminal:
        raise ValueError("every source requires an acceptable terminal processing outcome")

    incomplete = conn.execute(
        """
        SELECT 1 FROM processing_run AS pr
        JOIN source_file AS sf ON sf.source_file_id=pr.source_file_id
        WHERE sf.package_id=? AND pr.completed_at IS NULL LIMIT 1
        """,
        (package_id,),
    ).fetchone()
    if incomplete:
        raise ValueError("incomplete processing attempts block VERIFIED_COMPLETE")

    open_or_material_unresolved = conn.execute(
        """
        SELECT task_state FROM review_task
        WHERE package_id=?
          AND (task_state='OPEN' OR (task_state='UNRESOLVED' AND material=1))
        ORDER BY CASE task_state WHEN 'OPEN' THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (package_id,),
    ).fetchone()
    if open_or_material_unresolved:
        if open_or_material_unresolved[0] == "UNRESOLVED":
            raise ValueError("material UNRESOLVED review tasks block VERIFIED_COMPLETE")
        raise ValueError("OPEN review tasks block VERIFIED_COMPLETE")

    incomplete_occurrence = conn.execute(
        """
        SELECT 1 FROM source_file AS sf
        WHERE sf.package_id=? AND (
            NOT EXISTS (
                SELECT 1 FROM occurrence AS o
                WHERE o.source_file_id=sf.source_file_id
            ) OR EXISTS (
                SELECT 1 FROM occurrence AS o
                WHERE o.source_file_id=sf.source_file_id
                  AND o.verification_state<>'VERIFIED'
            )
        ) LIMIT 1
        """,
        (package_id,),
    ).fetchone()
    if incomplete_occurrence:
        raise ValueError("required source and occurrence verification is incomplete")


def _review_scope(
    conn: sqlite3.Connection, entity_type: str, entity_id: str
) -> tuple[str, dict[str, str]]:
    query, fields = _ENTITY_QUERIES[entity_type]
    parameters = (entity_id, entity_id) if entity_type == "RECORD_REFERENCE" else (entity_id,)
    row = conn.execute(query, parameters).fetchone()
    if row is None:
        raise ValueError(f"unknown {entity_type} entity: {entity_id}")
    return row["package_id"], {field: row[field] for field in fields}


def _source_backed_citation(
    conn: sqlite3.Connection, package_id: str, locator: str
) -> str | None:
    row = conn.execute(
        """
        SELECT evidence_citation.evidence_citation_id
        FROM evidence_citation
        LEFT JOIN source_file AS direct_source
            ON direct_source.source_file_id = evidence_citation.source_file_id
        LEFT JOIN occurrence
            ON occurrence.occurrence_id = evidence_citation.occurrence_id
        LEFT JOIN source_file AS occurrence_source
            ON occurrence_source.source_file_id = occurrence.source_file_id
        WHERE evidence_citation.locator=?
          AND COALESCE(direct_source.package_id, occurrence_source.package_id)=?
        ORDER BY evidence_citation.evidence_citation_id
        LIMIT 1
        """,
        (locator, package_id),
    ).fetchone()
    return None if row is None else row[0]


def _validate_finding_citations(
    conn: sqlite3.Connection, finding: sqlite3.Row, citation_ids: list[str]
) -> None:
    for citation_id in citation_ids:
        row = conn.execute(
            """
            SELECT COALESCE(direct_source.package_id, occurrence_source.package_id) AS package_id,
                   evidence_citation.occurrence_id
            FROM evidence_citation
            LEFT JOIN source_file AS direct_source
                ON direct_source.source_file_id = evidence_citation.source_file_id
            LEFT JOIN occurrence
                ON occurrence.occurrence_id = evidence_citation.occurrence_id
            LEFT JOIN source_file AS occurrence_source
                ON occurrence_source.source_file_id = occurrence.source_file_id
            WHERE evidence_citation.evidence_citation_id=?
            """,
            (citation_id,),
        ).fetchone()
        if row is None or row["package_id"] is None:
            raise ValueError("citation_ids must be source-backed")
        if row["package_id"] == finding["package_id"]:
            continue
        if finding["request_element_id"] is None or row["occurrence_id"] is None:
            raise ValueError("cross-package citations require an explicit evidence link")
        linked = conn.execute(
            """
            SELECT 1 FROM request_element_evidence
            WHERE request_element_id=? AND occurrence_id=?
              AND evidentiary_role IN (
                  'SUBSTITUTE','EXISTENCE_EVIDENCE','CONTRADICTION_EVIDENCE','CONTEXT'
              )
            """,
            (finding["request_element_id"], row["occurrence_id"]),
        ).fetchone()
        if linked is None:
            raise ValueError("cross-package citations require an explicit evidence link")


def _require_human_reviewer(conn: sqlite3.Connection, reviewer: str) -> None:
    if not isinstance(reviewer, str) or not reviewer:
        raise ValueError("reviewer is required")
    row = conn.execute(
        "SELECT identity_type FROM reviewer_identity WHERE reviewer_id=?", (reviewer,)
    ).fetchone()
    if row is None or row["identity_type"] != "HUMAN":
        raise ValueError("reviewer must be a registered HUMAN identity")


def _insert_audit(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    field: str,
    previous_value: str | None,
    new_value: str | None,
    changed_at: str,
    reason: str,
    source: str,
    citation_id: str | None = None,
) -> None:
    event_id = stable_id(
        "AE", entity_type, entity_id, field, previous_value or "", new_value or "", changed_at
    )
    conn.execute(
        """
        INSERT INTO audit_event(
            event_id, entity_type, entity_id, field_name, previous_value, new_value,
            changed_at, reason, change_source, supporting_citation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            entity_type,
            entity_id,
            field,
            previous_value,
            new_value,
            changed_at,
            reason,
            source,
            citation_id,
        ),
    )


@contextmanager
def _atomic(conn: sqlite3.Connection):
    conn.execute("SAVEPOINT review_service")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT review_service")
        conn.execute("RELEASE SAVEPOINT review_service")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT review_service")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
