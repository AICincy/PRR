from pathlib import Path
import re
import sqlite3

from metro_forensics.locators import is_exact_locator


SCHEMA = Path(__file__).with_name("schema.sql")
VIEWS = Path(__file__).with_name("views.sql")


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _register_functions(conn)
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    _register_functions(conn)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    _apply_migrations(conn)
    # Table-rebuild migrations temporarily drop schema triggers that reference the
    # rebuilt table from another table.  Re-applying the idempotent schema restores
    # those boundary protections against the final table definitions.
    _drop_refreshable_integrity_triggers(conn)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    with conn:
        _reconcile_reinitialization_invariants(conn)
    conn.executescript(VIEWS.read_text(encoding="utf-8"))


def _register_functions(conn: sqlite3.Connection) -> None:
    conn.create_function(
        "is_exact_locator", 1, lambda value: int(is_exact_locator(value)), deterministic=True
    )


def _apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied_versions = {
        row[0] for row in conn.execute("SELECT version FROM schema_migration")
    }
    temporal_inference_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(temporal_inference)")
    }
    if "inference_type" not in temporal_inference_columns:
        conn.execute(
            "DROP TRIGGER IF EXISTS temporal_possession_requires_controlled_support_type"
        )
    for version, migration in (
        (1, _migrate_record_content_fingerprint),
        (2, _migrate_record_reference_aliases),
        (3, _migrate_review_task_scope),
        (4, _migrate_temporal_legal_fields),
        (5, _migrate_final_fix_schema),
        (6, _migrate_final_fix2_schema),
        (7, _migrate_final_fix2_finding_audits),
        (8, _migrate_final_fix3_authority),
        (9, _migrate_reinitialization_integrity_anchors),
    ):
        if version not in applied_versions:
            with conn:
                migration(conn)
                conn.execute(
                    "INSERT INTO schema_migration(version) VALUES (?)", (version,)
                )


def _migrate_record_content_fingerprint(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER IF EXISTS record_canonical_identity_is_immutable")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(record)")}
    if "content_fingerprint" not in columns:
        conn.execute("ALTER TABLE record ADD COLUMN content_fingerprint TEXT")

    intended_fingerprints: dict[str, str] = {}
    for row in conn.execute(
        "SELECT record_id, content_fingerprint, canonical_identity_basis FROM record"
    ):
        fingerprint = row[1]
        if fingerprint is None:
            fingerprint = _fingerprint_from_identity_basis(row[2])
        if fingerprint is not None:
            intended_fingerprints[row[0]] = fingerprint.lower()

    owners: dict[str, str] = {}
    for record_id, fingerprint in intended_fingerprints.items():
        if fingerprint in owners and owners[fingerprint] != record_id:
            raise sqlite3.IntegrityError(
                "record content fingerprint migration conflict; resolve duplicate identities first"
            )
        owners[fingerprint] = record_id

    for record_id, fingerprint in intended_fingerprints.items():
        conn.execute(
            "UPDATE record SET content_fingerprint=? WHERE record_id=?",
            (fingerprint, record_id),
        )

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_record_content_fingerprint_normalized
        ON record(lower(content_fingerprint))
        WHERE content_fingerprint IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS record_canonical_identity_is_immutable
        BEFORE UPDATE OF record_id,content_fingerprint,canonical_identity_basis ON record
        WHEN NEW.record_id IS NOT OLD.record_id
          OR NEW.content_fingerprint IS NOT OLD.content_fingerprint
          OR NEW.canonical_identity_basis IS NOT OLD.canonical_identity_basis
        BEGIN SELECT RAISE(ABORT,'RECORD canonical identity is immutable'); END
        """
    )


def _migrate_record_reference_aliases(conn: sqlite3.Connection) -> None:
    """Expose the Task 5 reference names without replacing historical columns."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(record_reference)")}
    if "reference_id" not in columns:
        conn.execute("ALTER TABLE record_reference ADD COLUMN reference_id TEXT")
    if "resolved_record_id" not in columns:
        conn.execute("ALTER TABLE record_reference ADD COLUMN resolved_record_id TEXT")

    conn.execute(
        """
        UPDATE record_reference
        SET reference_id = record_reference_id
        WHERE reference_id IS NULL
        """
    )
    conn.execute(
        """
        UPDATE record_reference
        SET resolved_record_id = matched_record_id
        WHERE match_state = 'CONFIRMED_MATCH' AND resolved_record_id IS NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_record_reference_reference_id
        ON record_reference(reference_id) WHERE reference_id IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_record_reference_resolved_record
        ON record_reference(resolved_record_id)
        """
    )


def _migrate_review_task_scope(conn: sqlite3.Connection) -> None:
    """Add Task 6 review metadata without invalidating historical ledgers."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(review_task)")}
    if {"finding_id", "material"} - columns:
        conn.execute("DROP TABLE IF EXISTS review_task_identity")
        conn.execute("DROP TRIGGER IF EXISTS package_verified_complete_transition_gate")
        # The current idempotent schema can install these definitions over a
        # legacy LEGAL_ASSESSMENT table before migration 5 adds finalizer fields.
        # SQLite reparses every trigger during a table rename, so remove the
        # temporarily invalid definitions and restore them after all migrations.
        conn.execute("DROP TRIGGER IF EXISTS legal_assessment_final_gate")
        conn.execute("DROP TRIGGER IF EXISTS legal_assessment_finalizer_is_immutable")
        conn.execute(
            """
            CREATE TABLE review_task_task6_migration (
                review_task_id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL REFERENCES package(package_id),
                request_element_id TEXT REFERENCES request_element(request_element_id),
                source_file_id TEXT REFERENCES source_file(source_file_id),
                occurrence_id TEXT REFERENCES occurrence(occurrence_id),
                record_reference_id TEXT REFERENCES record_reference(record_reference_id),
                finding_id TEXT REFERENCES finding(finding_id),
                task_type TEXT NOT NULL,
                reason_code TEXT,
                task_state TEXT NOT NULL DEFAULT 'OPEN'
                    CHECK (task_state IN ('OPEN', 'UNRESOLVED', 'RESOLVED')),
                material INTEGER NOT NULL DEFAULT 1 CHECK (material IN (0, 1)),
                concern TEXT NOT NULL,
                reviewer TEXT,
                resolved_at TEXT,
                resolution TEXT,
                supporting_citation_id TEXT REFERENCES evidence_citation(evidence_citation_id),
                CHECK (
                    task_state = 'OPEN' OR (
                        reviewer IS NOT NULL AND resolved_at IS NOT NULL AND
                        (task_state = 'UNRESOLVED' OR resolution IS NOT NULL)
                    )
                )
            )
            """
        )
        conn.execute(
            """
            INSERT INTO review_task_task6_migration(
                review_task_id, package_id, request_element_id, source_file_id,
                occurrence_id, record_reference_id, task_type, reason_code,
                task_state, concern, reviewer, resolved_at, resolution,
                supporting_citation_id
            )
            SELECT review_task_id, package_id, request_element_id, source_file_id,
                   occurrence_id, record_reference_id, task_type, reason_code,
                   task_state, concern, reviewer, resolved_at, resolution,
                   supporting_citation_id
            FROM review_task
            """
        )
        conn.execute("DROP TABLE review_task")
        conn.execute("ALTER TABLE review_task_task6_migration RENAME TO review_task")
    for column, index in (
        ("package_id", "idx_review_task_package"),
        ("request_element_id", "idx_review_task_request_element"),
        ("source_file_id", "idx_review_task_source_file"),
        ("occurrence_id", "idx_review_task_occurrence"),
        ("record_reference_id", "idx_review_task_record_reference"),
        ("supporting_citation_id", "idx_review_task_supporting_citation"),
        ("finding_id", "idx_review_task_finding"),
    ):
        conn.execute(f"CREATE INDEX IF NOT EXISTS {index} ON review_task({column})")


def _migrate_temporal_legal_fields(conn: sqlite3.Connection) -> None:
    """Add Task 7 fields without altering pre-existing analytical history."""
    date_columns = {row[1] for row in conn.execute("PRAGMA table_info(date_fact)")}
    if "raw_value" not in date_columns:
        conn.execute("ALTER TABLE date_fact ADD COLUMN raw_value TEXT")
    if "normalized_value" not in date_columns:
        conn.execute("ALTER TABLE date_fact ADD COLUMN normalized_value TEXT")
    if "precision" not in date_columns:
        conn.execute("ALTER TABLE date_fact ADD COLUMN precision TEXT")
    conn.execute("DROP TRIGGER IF EXISTS date_fact_no_update")
    conn.execute(
        "UPDATE date_fact SET raw_value=value_text WHERE raw_value IS NULL"
    )
    conn.execute(
        "UPDATE date_fact SET precision=precision_qualifier WHERE precision IS NULL"
    )
    conn.execute(
        """
        CREATE TRIGGER date_fact_no_update
        BEFORE UPDATE ON date_fact
        BEGIN
            SELECT RAISE(ABORT, 'DATE_FACT is append-only');
        END
        """
    )

    inference_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(temporal_inference)")
    }
    if "inference_type" not in inference_columns:
        conn.execute("ALTER TABLE temporal_inference ADD COLUMN inference_type TEXT")
    conn.execute("DROP TRIGGER IF EXISTS temporal_inference_no_update")
    conn.execute(
        "UPDATE temporal_inference SET inference_type=proposition WHERE inference_type IS NULL"
    )
    conn.execute(
        """
        CREATE TRIGGER temporal_inference_no_update
        BEFORE UPDATE ON temporal_inference
        BEGIN
            SELECT RAISE(ABORT, 'TEMPORAL_INFERENCE is append-only');
        END
        """
    )

    authority_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(legal_authority)")
    }
    if "legal_assessment_id" not in authority_columns:
        conn.execute(
            "ALTER TABLE legal_authority ADD COLUMN legal_assessment_id TEXT "
            "REFERENCES legal_assessment(legal_assessment_id)"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_legal_authority_assessment "
        "ON legal_authority(legal_assessment_id)"
    )
    conn.execute("DROP TRIGGER IF EXISTS legal_authority_no_update")
    conn.execute(
        """
        UPDATE legal_authority
        SET legal_assessment_id=(
            SELECT legal_assessment_id
            FROM legal_assessment
            WHERE primary_legal_authority_id=legal_authority.legal_authority_id
            LIMIT 1
        )
        WHERE legal_assessment_id IS NULL
        """
    )
    conn.execute(
        """
        CREATE TRIGGER legal_authority_no_update
        BEFORE UPDATE ON legal_authority
        BEGIN
            SELECT RAISE(ABORT, 'LEGAL_AUTHORITY is append-only');
        END
        """
    )


def _migrate_final_fix_schema(conn: sqlite3.Connection) -> None:
    """Backfill shared authorities and rebuild the bounded possession-support model."""
    assessment_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(legal_assessment)")
    }
    if "finalized_by" not in assessment_columns:
        conn.execute(
            "ALTER TABLE legal_assessment ADD COLUMN finalized_by TEXT "
            "REFERENCES reviewer_identity(reviewer_id)"
        )
    if "finalized_at" not in assessment_columns:
        conn.execute("ALTER TABLE legal_assessment ADD COLUMN finalized_at TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS legal_assessment_authority (
            legal_assessment_id TEXT NOT NULL REFERENCES legal_assessment(legal_assessment_id),
            legal_authority_id TEXT NOT NULL REFERENCES legal_authority(legal_authority_id),
            association_basis TEXT NOT NULL DEFAULT 'EXPLICIT',
            PRIMARY KEY (legal_assessment_id, legal_authority_id)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO legal_assessment_authority(
            legal_assessment_id,legal_authority_id,association_basis
        )
        SELECT legal_assessment_id,primary_legal_authority_id,'LEGACY_PRIMARY'
        FROM legal_assessment WHERE primary_legal_authority_id IS NOT NULL
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO legal_assessment_authority(
            legal_assessment_id,legal_authority_id,association_basis
        )
        SELECT legal_assessment_id,legal_authority_id,'LEGACY_OWNER_COLUMN'
        FROM legal_authority WHERE legal_assessment_id IS NOT NULL
        """
    )

    inference_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(temporal_inference)")
    }
    table_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='temporal_inference'"
    ).fetchone()
    table_sql = "" if table_sql_row is None else (table_sql_row[0] or "")
    if (
        "possession_supporting_finding_id" not in inference_columns
        or "inference_type <> 'POSSESSED_AT_RESPONSE'" in table_sql
    ):
        _rebuild_temporal_inference(conn)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS temporal_inference_finding (
            temporal_inference_id TEXT NOT NULL REFERENCES temporal_inference(temporal_inference_id),
            finding_id TEXT NOT NULL REFERENCES finding(finding_id),
            PRIMARY KEY (temporal_inference_id, finding_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_temporal_inference_possession_finding "
        "ON temporal_inference(possession_supporting_finding_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_temporal_inference_finding_finding "
        "ON temporal_inference_finding(finding_id)"
    )
    conn.execute("DROP TRIGGER IF EXISTS temporal_possession_requires_verified_non_date_evidence")
    conn.execute(
        """
        CREATE TRIGGER temporal_possession_requires_verified_non_date_evidence
        BEFORE INSERT ON temporal_inference
        WHEN NEW.inference_type='POSSESSED_AT_RESPONSE'
        BEGIN
            SELECT CASE WHEN NEW.verification_state<>'VERIFIED'
              OR NOT EXISTS (
                SELECT 1 FROM reviewer_identity
                WHERE reviewer_id=NEW.verified_by AND identity_type='HUMAN'
              )
              OR NEW.possession_supporting_finding_id IS NULL
              OR NOT EXISTS (
                SELECT 1 FROM finding AS f
                WHERE f.finding_id=NEW.possession_supporting_finding_id
                  AND f.verification_state='VERIFIED'
                  AND EXISTS (
                    SELECT 1 FROM finding_citation WHERE finding_id=f.finding_id
                  )
              ) THEN RAISE(ABORT,'Possession requires verified cited non-date evidence') END;
        END
        """
    )
    conn.execute(
        """
        INSERT INTO audit_event_use(event_id,entity_type,entity_id,field_name)
        SELECT ae.event_id,ae.entity_type,ae.entity_id,COALESCE(ae.field_name,'')
        FROM audit_event AS ae
        LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL
        """
    )


def _migrate_final_fix2_schema(conn: sqlite3.Connection) -> None:
    """Fail closed until a controlled possession-supporting finding type is approved."""
    conn.execute("DROP TRIGGER IF EXISTS temporal_possession_requires_controlled_support_type")
    conn.execute(
        """
        CREATE TRIGGER temporal_possession_requires_controlled_support_type
        BEFORE INSERT ON temporal_inference
        WHEN NEW.inference_type='POSSESSED_AT_RESPONSE'
        BEGIN
            SELECT RAISE(ABORT, 'No controlled possession-supporting finding type exists');
        END
        """
    )


def _migrate_final_fix2_finding_audits(conn: sqlite3.Connection) -> None:
    """Retire pre-wave finding authorizations before enforcing identity immutability."""
    conn.execute(
        """
        INSERT OR IGNORE INTO audit_event_use(event_id,entity_type,entity_id,field_name)
        SELECT ae.event_id,ae.entity_type,ae.entity_id,COALESCE(ae.field_name,'')
        FROM audit_event AS ae
        LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL
          AND ae.entity_type='FINDING'
          AND ae.field_name IS NOT 'CREATE'
        """
    )


def _migrate_final_fix3_authority(conn: sqlite3.Connection) -> None:
    """Validate audit consumption and anchor existing review-task identities."""
    mismatch = conn.execute(
        """
        SELECT used.event_id
        FROM audit_event_use AS used
        LEFT JOIN audit_event AS ae ON ae.event_id=used.event_id
        WHERE ae.event_id IS NULL
           OR ae.entity_type<>used.entity_type
           OR ae.entity_id<>used.entity_id
           OR COALESCE(ae.field_name,'')<>used.field_name
        LIMIT 1
        """
    ).fetchone()
    if mismatch is not None:
        raise sqlite3.IntegrityError(
            f"audit consumption does not match immutable event: {mismatch[0]}"
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_task_identity (
            review_task_id TEXT PRIMARY KEY
                REFERENCES review_task(review_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO review_task_identity(review_task_id)
        SELECT task.review_task_id
        FROM review_task AS task
        LEFT JOIN review_task_identity AS identity
          ON identity.review_task_id=task.review_task_id
        WHERE identity.review_task_id IS NULL
        """
    )
    conn.execute(
        """
        INSERT INTO audit_event_use(event_id,entity_type,entity_id,field_name)
        SELECT ae.event_id,'REVIEW_TASK',ae.entity_id,'CREATE'
        FROM audit_event AS ae
        JOIN review_task AS task ON task.review_task_id=ae.entity_id
        LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL
          AND ae.entity_type='REVIEW_TASK'
          AND ae.field_name='CREATE'
          AND ae.previous_value IS NULL
          AND ae.new_value='OPEN'
        """
    )


def _migrate_reinitialization_integrity_anchors(conn: sqlite3.Connection) -> None:
    """Anchor existing audit consumption independently of mutable trigger state."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_event_use_identity (
            event_id TEXT PRIMARY KEY
                REFERENCES audit_event_use(event_id) ON DELETE RESTRICT ON UPDATE RESTRICT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO audit_event_use_identity(event_id)
        SELECT used.event_id
        FROM audit_event_use AS used
        LEFT JOIN audit_event_use_identity AS identity
          ON identity.event_id=used.event_id
        WHERE identity.event_id IS NULL
        """
    )


_REFRESHABLE_INTEGRITY_TRIGGERS = (
    "operational_metadata_is_immutable",
    "operational_metadata_cannot_be_deleted",
    "operational_metadata_intake_root_no_replace",
    "review_task_no_delete",
    "review_task_identity_no_update",
    "review_task_identity_no_delete",
    "review_task_identity_created_with_task",
    "review_task_authority_is_immutable",
    "review_task_transition_requires_unused_human_audit",
    "review_task_transition_consumes_audits",
    "audit_event_no_update",
    "audit_event_no_delete",
    "audit_event_no_replace",
    "audit_event_use_no_update",
    "audit_event_use_no_delete",
    "audit_event_use_no_replace",
    "audit_event_use_matches_event",
    "audit_event_use_identity_no_update",
    "audit_event_use_identity_no_delete",
    "audit_event_use_identity_no_replace",
    "audit_event_use_identity_created_with_use",
    "review_task_insert_requires_creation_audit",
    "review_task_insert_consumes_creation_audit",
)


def _drop_refreshable_integrity_triggers(conn: sqlite3.Connection) -> None:
    """Force critical guards to be recreated from their current schema definitions."""
    for trigger_name in _REFRESHABLE_INTEGRITY_TRIGGERS:
        conn.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')


def _reconcile_reinitialization_invariants(conn: sqlite3.Connection) -> None:
    """Validate or restore durable integrity data even when migrations are recorded."""
    mismatch = conn.execute(
        """
        SELECT used.event_id
        FROM audit_event_use AS used
        LEFT JOIN audit_event AS ae ON ae.event_id=used.event_id
        WHERE ae.event_id IS NULL
           OR ae.entity_type<>used.entity_type
           OR ae.entity_id<>used.entity_id
           OR COALESCE(ae.field_name,'')<>used.field_name
        LIMIT 1
        """
    ).fetchone()
    if mismatch is not None:
        raise sqlite3.IntegrityError(
            f"audit consumption does not match immutable event: {mismatch[0]}"
        )

    orphaned_identity = conn.execute(
        """
        SELECT identity.event_id
        FROM audit_event_use_identity AS identity
        LEFT JOIN audit_event AS ae ON ae.event_id=identity.event_id
        WHERE ae.event_id IS NULL
        LIMIT 1
        """
    ).fetchone()
    if orphaned_identity is not None:
        raise sqlite3.IntegrityError(
            f"audit consumption identity lacks immutable event: {orphaned_identity[0]}"
        )

    conn.execute(
        """
        INSERT INTO audit_event_use(event_id,entity_type,entity_id,field_name)
        SELECT identity.event_id,ae.entity_type,ae.entity_id,COALESCE(ae.field_name,'')
        FROM audit_event_use_identity AS identity
        JOIN audit_event AS ae ON ae.event_id=identity.event_id
        LEFT JOIN audit_event_use AS used ON used.event_id=identity.event_id
        WHERE used.event_id IS NULL
        """
    )
    conn.execute(
        """
        INSERT INTO audit_event_use_identity(event_id)
        SELECT used.event_id
        FROM audit_event_use AS used
        LEFT JOIN audit_event_use_identity AS identity
          ON identity.event_id=used.event_id
        WHERE identity.event_id IS NULL
        """
    )
    conn.execute(
        """
        INSERT INTO review_task_identity(review_task_id)
        SELECT task.review_task_id
        FROM review_task AS task
        LEFT JOIN review_task_identity AS identity
          ON identity.review_task_id=task.review_task_id
        WHERE identity.review_task_id IS NULL
        """
    )


def _rebuild_temporal_inference(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER IF EXISTS temporal_inference_no_update")
    conn.execute("DROP TRIGGER IF EXISTS temporal_inference_no_delete")
    conn.execute("DROP TABLE IF EXISTS temporal_inference_finding")
    conn.execute(
        """
        CREATE TABLE temporal_inference_final_fix (
            temporal_inference_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            proposition TEXT NOT NULL,
            inference_type TEXT NOT NULL,
            verification_state TEXT NOT NULL DEFAULT 'PROVISIONAL'
                CHECK (verification_state IN ('PROVISIONAL','VERIFIED')),
            verified_by TEXT,
            supporting_citation_id TEXT REFERENCES evidence_citation(evidence_citation_id),
            possession_supporting_finding_id TEXT REFERENCES finding(finding_id),
            notes TEXT NOT NULL DEFAULT '',
            CHECK (verification_state <> 'VERIFIED' OR verified_by IS NOT NULL)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO temporal_inference_final_fix(
            temporal_inference_id,entity_type,entity_id,proposition,inference_type,
            verification_state,verified_by,supporting_citation_id,notes
        )
        SELECT temporal_inference_id,entity_type,entity_id,proposition,inference_type,
               verification_state,verified_by,supporting_citation_id,notes
        FROM temporal_inference
        """
    )
    conn.execute(
        """
        CREATE TABLE temporal_inference_date_fact_final_fix (
            temporal_inference_id TEXT NOT NULL
                REFERENCES temporal_inference_final_fix(temporal_inference_id),
            date_fact_id TEXT NOT NULL REFERENCES date_fact(date_fact_id),
            PRIMARY KEY (temporal_inference_id,date_fact_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO temporal_inference_date_fact_final_fix SELECT * FROM temporal_inference_date_fact"
    )
    conn.execute("DROP TABLE temporal_inference_date_fact")
    conn.execute("DROP TABLE temporal_inference")
    conn.execute("ALTER TABLE temporal_inference_final_fix RENAME TO temporal_inference")
    conn.execute(
        "ALTER TABLE temporal_inference_date_fact_final_fix RENAME TO temporal_inference_date_fact"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_temporal_inference_supporting_citation "
        "ON temporal_inference(supporting_citation_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_temporal_inference_entity "
        "ON temporal_inference(entity_type,entity_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_temporal_inference_possession_finding "
        "ON temporal_inference(possession_supporting_finding_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_temporal_inference_date_fact_date_fact "
        "ON temporal_inference_date_fact(date_fact_id)"
    )
    conn.execute(
        "CREATE TRIGGER temporal_inference_no_update BEFORE UPDATE ON temporal_inference "
        "BEGIN SELECT RAISE(ABORT,'TEMPORAL_INFERENCE is append-only'); END"
    )
    conn.execute(
        "CREATE TRIGGER temporal_inference_no_delete BEFORE DELETE ON temporal_inference "
        "BEGIN SELECT RAISE(ABORT,'TEMPORAL_INFERENCE is append-only'); END"
    )


def _fingerprint_from_identity_basis(identity_basis: object) -> str | None:
    prefix = "exact content fingerprint: "
    if not isinstance(identity_basis, str) or not identity_basis.startswith(prefix):
        return None
    fingerprint = identity_basis.removeprefix(prefix)
    return fingerprint if re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint) else None
