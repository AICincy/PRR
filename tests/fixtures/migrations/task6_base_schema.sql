PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS vocabulary (
    domain TEXT NOT NULL,
    code TEXT NOT NULL,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    deprecated INTEGER NOT NULL DEFAULT 0 CHECK (deprecated IN (0,1)),
    PRIMARY KEY (domain, code)
);

CREATE TABLE IF NOT EXISTS package (
    package_id TEXT PRIMARY KEY,
    control_record_path TEXT NOT NULL UNIQUE,
    production_archive_path TEXT,
    package_status TEXT CHECK (
        package_status IS NULL OR package_status IN ('NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED')
    ),
    expected_level1_count INTEGER NOT NULL CHECK (expected_level1_count >= 0),
    completeness_state TEXT NOT NULL DEFAULT 'IN_PROGRESS' CHECK (
        completeness_state IN (
            'IN_PROGRESS', 'REVIEW_REQUIRED', 'COMPLETE_WITH_EXCEPTIONS', 'VERIFIED_COMPLETE'
        )
    ),
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS corpus (
    corpus_id TEXT PRIMARY KEY,
    corpus_name TEXT NOT NULL,
    completeness_state TEXT NOT NULL DEFAULT 'IN_PROGRESS' CHECK (
        completeness_state IN (
            'IN_PROGRESS', 'REVIEW_REQUIRED', 'COMPLETE_WITH_EXCEPTIONS', 'VERIFIED_COMPLETE'
        )
    ),
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS corpus_package (
    corpus_id TEXT NOT NULL REFERENCES corpus(corpus_id),
    package_id TEXT NOT NULL REFERENCES package(package_id),
    PRIMARY KEY (corpus_id, package_id)
);

CREATE INDEX IF NOT EXISTS idx_corpus_package_package ON corpus_package(package_id);

CREATE TRIGGER IF NOT EXISTS corpus_no_empty_verified_complete_insert
BEFORE INSERT ON corpus
WHEN NEW.completeness_state = 'VERIFIED_COMPLETE'
BEGIN
    SELECT RAISE(ABORT, 'A verified complete corpus must include verified complete packages');
END;

CREATE TRIGGER IF NOT EXISTS corpus_no_incomplete_member_on_verified_complete
BEFORE UPDATE OF completeness_state ON corpus
WHEN NEW.completeness_state = 'VERIFIED_COMPLETE'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM corpus_package WHERE corpus_id = NEW.corpus_id
    ) OR EXISTS (
        SELECT 1
        FROM corpus_package AS membership
        JOIN package AS member_package ON member_package.package_id = membership.package_id
        WHERE membership.corpus_id = NEW.corpus_id
          AND member_package.completeness_state <> 'VERIFIED_COMPLETE'
    ) THEN RAISE(ABORT, 'A verified complete corpus must include only verified complete packages') END;
END;

CREATE TRIGGER IF NOT EXISTS corpus_membership_requires_verified_package
BEFORE INSERT ON corpus_package
WHEN EXISTS (
    SELECT 1 FROM corpus
    WHERE corpus_id = NEW.corpus_id AND completeness_state = 'VERIFIED_COMPLETE'
)
AND EXISTS (
    SELECT 1 FROM package
    WHERE package_id = NEW.package_id AND completeness_state <> 'VERIFIED_COMPLETE'
)
BEGIN
    SELECT RAISE(ABORT, 'A verified complete corpus cannot gain an incomplete package');
END;

CREATE TRIGGER IF NOT EXISTS corpus_verified_membership_is_immutable_update
BEFORE UPDATE ON corpus_package
WHEN EXISTS (
    SELECT 1 FROM corpus
    WHERE corpus_id = OLD.corpus_id AND completeness_state = 'VERIFIED_COMPLETE'
)
BEGIN
    SELECT RAISE(ABORT, 'Verified complete corpus membership is immutable');
END;

CREATE TRIGGER IF NOT EXISTS corpus_verified_membership_is_immutable_delete
BEFORE DELETE ON corpus_package
WHEN EXISTS (
    SELECT 1 FROM corpus
    WHERE corpus_id = OLD.corpus_id AND completeness_state = 'VERIFIED_COMPLETE'
)
BEGIN
    SELECT RAISE(ABORT, 'Verified complete corpus membership is immutable');
END;

CREATE TRIGGER IF NOT EXISTS package_cannot_invalidate_verified_complete_corpus
BEFORE UPDATE OF completeness_state ON package
WHEN NEW.completeness_state <> 'VERIFIED_COMPLETE'
AND EXISTS (
    SELECT 1
    FROM corpus_package
    JOIN corpus ON corpus.corpus_id = corpus_package.corpus_id
    WHERE corpus_package.package_id = OLD.package_id
      AND corpus.completeness_state = 'VERIFIED_COMPLETE'
)
BEGIN
    SELECT RAISE(ABORT, 'A package in a verified complete corpus cannot become incomplete');
END;

CREATE TABLE IF NOT EXISTS request_element (
    request_element_id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES package(package_id),
    parent_request_element_id TEXT REFERENCES request_element(request_element_id),
    requested_language TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    completeness_state TEXT NOT NULL DEFAULT 'IN_PROGRESS' CHECK (
        completeness_state IN (
            'IN_PROGRESS', 'REVIEW_REQUIRED', 'COMPLETE_WITH_EXCEPTIONS', 'VERIFIED_COMPLETE'
        )
    ),
    scope_notes TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_request_element_package ON request_element(package_id);
CREATE INDEX IF NOT EXISTS idx_request_element_parent ON request_element(parent_request_element_id);

CREATE TABLE IF NOT EXISTS source_file (
    source_file_id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES package(package_id),
    archive_member_path TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    sha256 TEXT NOT NULL CHECK (length(sha256)=64),
    media_type TEXT NOT NULL,
    structural_unit_count INTEGER CHECK (structural_unit_count IS NULL OR structural_unit_count >= 0),
    processing_condition TEXT NOT NULL DEFAULT '',
    UNIQUE(package_id, archive_member_path, sha256)
);

CREATE INDEX IF NOT EXISTS idx_source_file_package ON source_file(package_id);

CREATE TRIGGER IF NOT EXISTS source_file_provenance_is_immutable_after_processing
BEFORE UPDATE OF archive_member_path, byte_size, sha256 ON source_file
WHEN EXISTS (
    SELECT 1 FROM processing_run WHERE source_file_id = OLD.source_file_id
)
BEGIN
    SELECT RAISE(ABORT, 'Source-file provenance is immutable after processing begins');
END;

CREATE TABLE IF NOT EXISTS processing_run (
    processing_run_id TEXT PRIMARY KEY,
    source_file_id TEXT NOT NULL REFERENCES source_file(source_file_id),
    operation TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_version TEXT NOT NULL DEFAULT '',
    parameters TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    warnings TEXT NOT NULL DEFAULT '',
    errors TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_processing_run_source_file ON processing_run(source_file_id);

CREATE TRIGGER IF NOT EXISTS processing_run_provenance_is_immutable
BEFORE UPDATE ON processing_run
WHEN NEW.source_file_id IS NOT OLD.source_file_id
  OR NEW.operation IS NOT OLD.operation
  OR NEW.tool_name IS NOT OLD.tool_name
  OR NEW.tool_version IS NOT OLD.tool_version
  OR NEW.parameters IS NOT OLD.parameters
  OR NEW.started_at IS NOT OLD.started_at
  OR OLD.completed_at IS NOT NULL
  OR NEW.completed_at IS NULL
BEGIN
    SELECT RAISE(ABORT, 'Processing run provenance is immutable after completion');
END;

CREATE TRIGGER IF NOT EXISTS processing_run_no_delete
BEFORE DELETE ON processing_run
BEGIN
    SELECT RAISE(ABORT, 'PROCESSING_RUN is append-only');
END;

CREATE TABLE IF NOT EXISTS derivative (
    derivative_id TEXT PRIMARY KEY,
    source_file_id TEXT NOT NULL REFERENCES source_file(source_file_id),
    processing_run_id TEXT NOT NULL REFERENCES processing_run(processing_run_id),
    sha256 TEXT NOT NULL CHECK (length(sha256)=64),
    artifact_type TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    source_location_mapping TEXT NOT NULL,
    UNIQUE(source_file_id, processing_run_id, sha256)
);

CREATE INDEX IF NOT EXISTS idx_derivative_source_file ON derivative(source_file_id);
CREATE INDEX IF NOT EXISTS idx_derivative_processing_run ON derivative(processing_run_id);

CREATE TRIGGER IF NOT EXISTS derivative_source_matches_processing_run_insert
BEFORE INSERT ON derivative
WHEN NOT EXISTS (
    SELECT 1 FROM processing_run
    WHERE processing_run_id = NEW.processing_run_id
      AND source_file_id = NEW.source_file_id
)
BEGIN
    SELECT RAISE(ABORT, 'Derivative source must match its processing run source');
END;

CREATE TRIGGER IF NOT EXISTS derivative_requires_successful_processing_run
BEFORE INSERT ON derivative
WHEN NOT EXISTS (
    SELECT 1 FROM processing_run
    WHERE processing_run_id = NEW.processing_run_id
      AND source_file_id = NEW.source_file_id
      AND completed_at IS NOT NULL
      AND errors = ''
)
BEGIN
    SELECT RAISE(ABORT, 'Derivative requires a successfully completed processing run');
END;

CREATE TRIGGER IF NOT EXISTS derivative_source_matches_processing_run_update
BEFORE UPDATE OF source_file_id, processing_run_id ON derivative
WHEN NOT EXISTS (
    SELECT 1 FROM processing_run
    WHERE processing_run_id = NEW.processing_run_id
      AND source_file_id = NEW.source_file_id
)
BEGIN
    SELECT RAISE(ABORT, 'Derivative source must match its processing run source');
END;

CREATE TRIGGER IF NOT EXISTS derivative_no_update
BEFORE UPDATE ON derivative
BEGIN
    SELECT RAISE(ABORT, 'DERIVATIVE is immutable');
END;

CREATE TRIGGER IF NOT EXISTS derivative_no_delete
BEFORE DELETE ON derivative
BEGIN
    SELECT RAISE(ABORT, 'DERIVATIVE is append-only');
END;

CREATE TABLE IF NOT EXISTS record (
    record_id TEXT PRIMARY KEY,
    title_or_description TEXT NOT NULL,
    record_type TEXT,
    content_fingerprint TEXT UNIQUE CHECK (
        content_fingerprint IS NULL OR length(content_fingerprint) = 64
    ),
    canonical_identity_basis TEXT NOT NULL,
    version_family_key TEXT,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS occurrence (
    occurrence_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES record(record_id),
    source_file_id TEXT NOT NULL REFERENCES source_file(source_file_id),
    derivative_id TEXT REFERENCES derivative(derivative_id),
    source_locator TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'PROVISIONAL' CHECK (
        verification_state IN ('PROVISIONAL', 'VERIFIED')
    ),
    verified_by TEXT,
    notes TEXT NOT NULL DEFAULT '',
    CHECK (verification_state <> 'VERIFIED' OR verified_by IS NOT NULL),
    UNIQUE(record_id, source_file_id, source_locator)
);

CREATE INDEX IF NOT EXISTS idx_occurrence_record ON occurrence(record_id);
CREATE INDEX IF NOT EXISTS idx_occurrence_source_file ON occurrence(source_file_id);
CREATE INDEX IF NOT EXISTS idx_occurrence_derivative ON occurrence(derivative_id);

CREATE TABLE IF NOT EXISTS request_element_evidence (
    request_element_evidence_id TEXT PRIMARY KEY,
    request_element_id TEXT NOT NULL REFERENCES request_element(request_element_id),
    occurrence_id TEXT NOT NULL REFERENCES occurrence(occurrence_id),
    evidentiary_role TEXT NOT NULL CHECK (
        evidentiary_role IN (
            'RESPONSIVE', 'SUBSTITUTE', 'EXISTENCE_EVIDENCE', 'CONTRADICTION_EVIDENCE', 'CONTEXT'
        )
    ),
    notes TEXT NOT NULL DEFAULT '',
    UNIQUE(request_element_id, occurrence_id, evidentiary_role)
);

CREATE INDEX IF NOT EXISTS idx_request_element_evidence_request_element
    ON request_element_evidence(request_element_id);
CREATE INDEX IF NOT EXISTS idx_request_element_evidence_occurrence
    ON request_element_evidence(occurrence_id);

CREATE TABLE IF NOT EXISTS metro_statement (
    metro_statement_id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES package(package_id),
    source_file_id TEXT REFERENCES source_file(source_file_id),
    statement_text TEXT NOT NULL,
    statement_type TEXT NOT NULL CHECK (
        statement_type IN ('NONEXISTENCE_ASSERTION', 'DENIAL', 'WITHHOLDING_BASIS')
    ),
    source_locator TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'PROVISIONAL' CHECK (
        verification_state IN ('PROVISIONAL', 'VERIFIED')
    ),
    verified_by TEXT,
    CHECK (verification_state <> 'VERIFIED' OR verified_by IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_metro_statement_package ON metro_statement(package_id);
CREATE INDEX IF NOT EXISTS idx_metro_statement_source_file ON metro_statement(source_file_id);

CREATE TABLE IF NOT EXISTS statement_request_element (
    metro_statement_id TEXT NOT NULL REFERENCES metro_statement(metro_statement_id),
    request_element_id TEXT NOT NULL REFERENCES request_element(request_element_id),
    PRIMARY KEY (metro_statement_id, request_element_id)
);

CREATE INDEX IF NOT EXISTS idx_statement_request_element_request_element
    ON statement_request_element(request_element_id);

CREATE TABLE IF NOT EXISTS evidence_citation (
    evidence_citation_id TEXT PRIMARY KEY,
    source_file_id TEXT REFERENCES source_file(source_file_id),
    occurrence_id TEXT REFERENCES occurrence(occurrence_id),
    metro_statement_id TEXT REFERENCES metro_statement(metro_statement_id),
    locator TEXT NOT NULL,
    quoted_text TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    CHECK (
        source_file_id IS NOT NULL OR occurrence_id IS NOT NULL OR metro_statement_id IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_evidence_citation_source_file ON evidence_citation(source_file_id);
CREATE INDEX IF NOT EXISTS idx_evidence_citation_occurrence ON evidence_citation(occurrence_id);
CREATE INDEX IF NOT EXISTS idx_evidence_citation_metro_statement ON evidence_citation(metro_statement_id);

CREATE TABLE IF NOT EXISTS record_reference (
    record_reference_id TEXT PRIMARY KEY,
    reference_id TEXT UNIQUE,
    occurrence_id TEXT NOT NULL REFERENCES occurrence(occurrence_id),
    source_locator TEXT NOT NULL,
    relationship_type TEXT NOT NULL CHECK (
        relationship_type IN (
            'ATTACHMENT', 'EXHIBIT', 'CONTRACT', 'REPORT', 'STUDY', 'INVOICE', 'PROPOSAL', 'SPREADSHEET'
        )
    ),
    referenced_description TEXT NOT NULL,
    matched_record_id TEXT REFERENCES record(record_id),
    resolved_record_id TEXT REFERENCES record(record_id),
    match_state TEXT NOT NULL CHECK (
        match_state IN ('CONFIRMED_MATCH', 'PROBABLE_MATCH', 'NO_MATCH_LOCATED')
    ),
    search_corpus_id TEXT REFERENCES corpus(corpus_id),
    absence_scope TEXT CHECK (
        absence_scope IS NULL OR absence_scope IN (
            'NOT_LOCATED_RESPONSIVE_PACKAGE', 'LOCATED_ELSEWHERE_CORPUS',
            'NOT_LOCATED_CORPUS', 'CORPUS_SEARCH_INCOMPLETE'
        )
    ),
    verification_state TEXT NOT NULL DEFAULT 'PROVISIONAL' CHECK (
        verification_state IN ('PROVISIONAL', 'VERIFIED')
    ),
    verified_by TEXT,
    notes TEXT NOT NULL DEFAULT '',
    CHECK (verification_state <> 'VERIFIED' OR verified_by IS NOT NULL),
    CHECK (match_state <> 'CONFIRMED_MATCH' OR matched_record_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_record_reference_occurrence ON record_reference(occurrence_id);
CREATE INDEX IF NOT EXISTS idx_record_reference_matched_record ON record_reference(matched_record_id);
CREATE INDEX IF NOT EXISTS idx_record_reference_search_corpus ON record_reference(search_corpus_id);

CREATE TRIGGER IF NOT EXISTS record_reference_not_located_corpus_requires_verified_search_insert
BEFORE INSERT ON record_reference
WHEN NEW.absence_scope = 'NOT_LOCATED_CORPUS'
BEGIN
    SELECT CASE WHEN NEW.search_corpus_id IS NULL
        OR NOT EXISTS (
            SELECT 1 FROM corpus
            WHERE corpus_id = NEW.search_corpus_id AND completeness_state = 'VERIFIED_COMPLETE'
        )
        OR NOT EXISTS (
            SELECT 1
            FROM corpus_package AS membership
            JOIN occurrence AS source_occurrence ON source_occurrence.occurrence_id = NEW.occurrence_id
            JOIN source_file AS source_file ON source_file.source_file_id = source_occurrence.source_file_id
            WHERE membership.corpus_id = NEW.search_corpus_id
              AND membership.package_id = source_file.package_id
        )
        OR EXISTS (
            SELECT 1
            FROM corpus_package AS membership
            JOIN package AS member_package ON member_package.package_id = membership.package_id
            WHERE membership.corpus_id = NEW.search_corpus_id
              AND member_package.completeness_state <> 'VERIFIED_COMPLETE'
        )
        THEN RAISE(ABORT, 'NOT_LOCATED_CORPUS requires a verified complete search corpus') END;
END;

CREATE TRIGGER IF NOT EXISTS record_reference_not_located_corpus_requires_verified_search_update
BEFORE UPDATE OF absence_scope, search_corpus_id, occurrence_id ON record_reference
WHEN NEW.absence_scope = 'NOT_LOCATED_CORPUS'
BEGIN
    SELECT CASE WHEN NEW.search_corpus_id IS NULL
        OR NOT EXISTS (
            SELECT 1 FROM corpus
            WHERE corpus_id = NEW.search_corpus_id AND completeness_state = 'VERIFIED_COMPLETE'
        )
        OR NOT EXISTS (
            SELECT 1
            FROM corpus_package AS membership
            JOIN occurrence AS source_occurrence ON source_occurrence.occurrence_id = NEW.occurrence_id
            JOIN source_file AS source_file ON source_file.source_file_id = source_occurrence.source_file_id
            WHERE membership.corpus_id = NEW.search_corpus_id
              AND membership.package_id = source_file.package_id
        )
        OR EXISTS (
            SELECT 1
            FROM corpus_package AS membership
            JOIN package AS member_package ON member_package.package_id = membership.package_id
            WHERE membership.corpus_id = NEW.search_corpus_id
              AND member_package.completeness_state <> 'VERIFIED_COMPLETE'
        )
        THEN RAISE(ABORT, 'NOT_LOCATED_CORPUS requires a verified complete search corpus') END;
END;

CREATE TRIGGER IF NOT EXISTS absence_supporting_corpus_membership_is_immutable
BEFORE INSERT ON corpus_package
WHEN EXISTS (
    SELECT 1
    FROM record_reference
    WHERE absence_scope = 'NOT_LOCATED_CORPUS'
      AND search_corpus_id = NEW.corpus_id
)
BEGIN
    SELECT RAISE(ABORT, 'A corpus supporting a corpus-wide absence claim cannot change scope');
END;

CREATE TRIGGER IF NOT EXISTS absence_supporting_corpus_cannot_downgrade
BEFORE UPDATE OF completeness_state ON corpus
WHEN OLD.completeness_state = 'VERIFIED_COMPLETE'
 AND NEW.completeness_state <> 'VERIFIED_COMPLETE'
 AND EXISTS (
    SELECT 1
    FROM record_reference
    WHERE absence_scope = 'NOT_LOCATED_CORPUS'
      AND search_corpus_id = OLD.corpus_id
)
BEGIN
    SELECT RAISE(ABORT, 'A corpus supporting a corpus-wide absence claim cannot be downgraded');
END;

CREATE TABLE IF NOT EXISTS finding (
    finding_id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES package(package_id),
    request_element_id TEXT REFERENCES request_element(request_element_id),
    record_id TEXT REFERENCES record(record_id),
    record_reference_id TEXT REFERENCES record_reference(record_reference_id),
    finding_type TEXT NOT NULL CHECK (
        finding_type IN (
            'UNPRODUCED', 'NONEXISTENCE_ASSERTED', 'SUBSTITUTE_PRODUCTION',
            'DIRECT_CONTRADICTION', 'STRONG_EXISTENCE_EVIDENCE', 'POSSIBLE_EXISTENCE_EVIDENCE',
            'PRODUCED_FULL', 'PRODUCED_PARTIAL_REDACTED', 'WITHHELD_WHOLE_OR_PART',
            'WITHHOLDING_BASIS_STATED', 'NO_WITHHOLDING_BASIS_STATED'
        )
    ),
    proposition TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'PROVISIONAL' CHECK (
        verification_state IN ('PROVISIONAL', 'VERIFIED')
    ),
    created_by TEXT NOT NULL DEFAULT 'human' CHECK (created_by IN ('human', 'automation')),
    verified_by TEXT,
    created_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    CHECK (verification_state <> 'VERIFIED' OR (verified_by IS NOT NULL AND verified_by <> 'automation')),
    CHECK (created_by <> 'automation' OR verification_state = 'PROVISIONAL')
);

CREATE INDEX IF NOT EXISTS idx_finding_package ON finding(package_id);
CREATE INDEX IF NOT EXISTS idx_finding_request_element ON finding(request_element_id);
CREATE INDEX IF NOT EXISTS idx_finding_record ON finding(record_id);
CREATE INDEX IF NOT EXISTS idx_finding_record_reference ON finding(record_reference_id);

CREATE TABLE IF NOT EXISTS finding_citation (
    finding_id TEXT NOT NULL REFERENCES finding(finding_id),
    evidence_citation_id TEXT NOT NULL REFERENCES evidence_citation(evidence_citation_id),
    PRIMARY KEY (finding_id, evidence_citation_id)
);

CREATE INDEX IF NOT EXISTS idx_finding_citation_evidence_citation
    ON finding_citation(evidence_citation_id);

CREATE TABLE IF NOT EXISTS date_fact (
    date_fact_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    date_role TEXT NOT NULL CHECK (
        date_role IN ('RECORD_DATE', 'REFERENCE_DATE', 'REQUEST_DATE', 'RESPONSE_DATE', 'DISCOVERY_DATE')
    ),
    value_text TEXT NOT NULL,
    precision_qualifier TEXT NOT NULL,
    evidence_citation_id TEXT NOT NULL REFERENCES evidence_citation(evidence_citation_id),
    notes TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_date_fact_evidence_citation ON date_fact(evidence_citation_id);
CREATE INDEX IF NOT EXISTS idx_date_fact_entity ON date_fact(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS temporal_inference (
    temporal_inference_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    proposition TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'PROVISIONAL' CHECK (
        verification_state IN ('PROVISIONAL', 'VERIFIED')
    ),
    verified_by TEXT,
    supporting_citation_id TEXT REFERENCES evidence_citation(evidence_citation_id),
    notes TEXT NOT NULL DEFAULT '',
    CHECK (verification_state <> 'VERIFIED' OR verified_by IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_temporal_inference_supporting_citation
    ON temporal_inference(supporting_citation_id);
CREATE INDEX IF NOT EXISTS idx_temporal_inference_entity ON temporal_inference(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS temporal_inference_date_fact (
    temporal_inference_id TEXT NOT NULL REFERENCES temporal_inference(temporal_inference_id),
    date_fact_id TEXT NOT NULL REFERENCES date_fact(date_fact_id),
    PRIMARY KEY (temporal_inference_id, date_fact_id)
);

CREATE INDEX IF NOT EXISTS idx_temporal_inference_date_fact_date_fact
    ON temporal_inference_date_fact(date_fact_id);

CREATE TABLE IF NOT EXISTS review_task (
    review_task_id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES package(package_id),
    request_element_id TEXT REFERENCES request_element(request_element_id),
    source_file_id TEXT REFERENCES source_file(source_file_id),
    occurrence_id TEXT REFERENCES occurrence(occurrence_id),
    record_reference_id TEXT REFERENCES record_reference(record_reference_id),
    task_type TEXT NOT NULL,
    reason_code TEXT,
    task_state TEXT NOT NULL DEFAULT 'OPEN' CHECK (task_state IN ('OPEN', 'UNRESOLVED', 'RESOLVED')),
    concern TEXT NOT NULL,
    reviewer TEXT,
    resolved_at TEXT,
    resolution TEXT,
    supporting_citation_id TEXT REFERENCES evidence_citation(evidence_citation_id),
    CHECK (
        task_state = 'OPEN' OR (reviewer IS NOT NULL AND resolved_at IS NOT NULL AND resolution IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_review_task_package ON review_task(package_id);
CREATE INDEX IF NOT EXISTS idx_review_task_request_element ON review_task(request_element_id);
CREATE INDEX IF NOT EXISTS idx_review_task_source_file ON review_task(source_file_id);
CREATE INDEX IF NOT EXISTS idx_review_task_occurrence ON review_task(occurrence_id);
CREATE INDEX IF NOT EXISTS idx_review_task_record_reference ON review_task(record_reference_id);
CREATE INDEX IF NOT EXISTS idx_review_task_supporting_citation ON review_task(supporting_citation_id);

CREATE TABLE IF NOT EXISTS audit_event (
    event_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    field_name TEXT,
    previous_value TEXT,
    new_value TEXT,
    changed_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    change_source TEXT NOT NULL,
    supporting_citation_id TEXT REFERENCES evidence_citation(evidence_citation_id)
);

CREATE INDEX IF NOT EXISTS idx_audit_event_supporting_citation ON audit_event(supporting_citation_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_entity ON audit_event(entity_type, entity_id);

CREATE TRIGGER IF NOT EXISTS audit_event_no_update
BEFORE UPDATE ON audit_event
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_EVENT is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_event_no_delete
BEFORE DELETE ON audit_event
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_EVENT is append-only');
END;

CREATE TABLE IF NOT EXISTS finding_identity (
    finding_id TEXT PRIMARY KEY REFERENCES finding(finding_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TRIGGER IF NOT EXISTS finding_identity_no_update
BEFORE UPDATE ON finding_identity
BEGIN
    SELECT RAISE(ABORT, 'FINDING identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS finding_identity_no_delete
BEFORE DELETE ON finding_identity
BEGIN
    SELECT RAISE(ABORT, 'FINDING identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS finding_creation_requires_audit_event
BEFORE INSERT ON finding
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM audit_event
        WHERE entity_type = 'FINDING'
          AND entity_id = NEW.finding_id
          AND field_name = 'CREATE'
    ) THEN RAISE(ABORT, 'Finding creation requires an audit event') END;
END;

CREATE TRIGGER IF NOT EXISTS finding_identity_created_with_finding
AFTER INSERT ON finding
BEGIN
    INSERT INTO finding_identity(finding_id) VALUES (NEW.finding_id);
END;

CREATE TRIGGER IF NOT EXISTS finding_creator_is_immutable
BEFORE UPDATE OF created_by ON finding
WHEN NEW.created_by IS NOT OLD.created_by
BEGIN
    SELECT RAISE(ABORT, 'Finding creator provenance is immutable');
END;

CREATE TRIGGER IF NOT EXISTS finding_scope_matches_package_insert
BEFORE INSERT ON finding
BEGIN
    SELECT CASE WHEN NEW.request_element_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM request_element
        WHERE request_element_id = NEW.request_element_id AND package_id = NEW.package_id
    ) THEN RAISE(ABORT, 'Finding request element must belong to its package') END;

    SELECT CASE WHEN NEW.record_reference_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM record_reference
        JOIN occurrence ON occurrence.occurrence_id = record_reference.occurrence_id
        JOIN source_file ON source_file.source_file_id = occurrence.source_file_id
        WHERE record_reference.record_reference_id = NEW.record_reference_id
          AND source_file.package_id = NEW.package_id
    ) THEN RAISE(ABORT, 'Finding record reference must belong to its package') END;
END;

CREATE TRIGGER IF NOT EXISTS finding_scope_matches_package_update
BEFORE UPDATE OF package_id, request_element_id, record_reference_id ON finding
BEGIN
    SELECT CASE WHEN NEW.request_element_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM request_element
        WHERE request_element_id = NEW.request_element_id AND package_id = NEW.package_id
    ) THEN RAISE(ABORT, 'Finding request element must belong to its package') END;

    SELECT CASE WHEN NEW.record_reference_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM record_reference
        JOIN occurrence ON occurrence.occurrence_id = record_reference.occurrence_id
        JOIN source_file ON source_file.source_file_id = occurrence.source_file_id
        WHERE record_reference.record_reference_id = NEW.record_reference_id
          AND source_file.package_id = NEW.package_id
    ) THEN RAISE(ABORT, 'Finding record reference must belong to its package') END;
END;

CREATE TRIGGER IF NOT EXISTS request_element_package_move_preserves_finding_scope
BEFORE UPDATE OF package_id ON request_element
WHEN NEW.package_id IS NOT OLD.package_id
AND EXISTS (
    SELECT 1 FROM finding
    WHERE request_element_id = OLD.request_element_id AND package_id <> NEW.package_id
)
BEGIN
    SELECT RAISE(ABORT, 'Request element move would cross an existing finding package');
END;

CREATE TRIGGER IF NOT EXISTS source_file_package_move_preserves_finding_scope
BEFORE UPDATE OF package_id ON source_file
WHEN NEW.package_id IS NOT OLD.package_id
AND EXISTS (
    SELECT 1
    FROM finding
    JOIN record_reference ON record_reference.record_reference_id = finding.record_reference_id
    JOIN occurrence ON occurrence.occurrence_id = record_reference.occurrence_id
    WHERE occurrence.source_file_id = OLD.source_file_id
      AND finding.package_id <> NEW.package_id
)
BEGIN
    SELECT RAISE(ABORT, 'Source file move would cross an existing finding package');
END;

CREATE TRIGGER IF NOT EXISTS occurrence_source_move_preserves_finding_scope
BEFORE UPDATE OF source_file_id ON occurrence
WHEN NEW.source_file_id IS NOT OLD.source_file_id
AND EXISTS (
    SELECT 1
    FROM finding
    JOIN record_reference ON record_reference.record_reference_id = finding.record_reference_id
    JOIN source_file AS new_source_file ON new_source_file.source_file_id = NEW.source_file_id
    WHERE record_reference.occurrence_id = OLD.occurrence_id
      AND finding.package_id <> new_source_file.package_id
)
BEGIN
    SELECT RAISE(ABORT, 'Occurrence move would cross an existing finding package');
END;

CREATE TRIGGER IF NOT EXISTS record_reference_occurrence_move_preserves_finding_scope
BEFORE UPDATE OF occurrence_id ON record_reference
WHEN NEW.occurrence_id IS NOT OLD.occurrence_id
AND EXISTS (
    SELECT 1
    FROM finding
    JOIN source_file AS new_source_file
    JOIN occurrence AS new_occurrence ON new_occurrence.source_file_id = new_source_file.source_file_id
    WHERE finding.record_reference_id = OLD.record_reference_id
      AND new_occurrence.occurrence_id = NEW.occurrence_id
      AND finding.package_id <> new_source_file.package_id
)
BEGIN
    SELECT RAISE(ABORT, 'Record reference move would cross an existing finding package');
END;

CREATE TRIGGER IF NOT EXISTS finding_substantive_change_requires_audit_event
BEFORE UPDATE ON finding
BEGIN
    SELECT CASE WHEN NEW.proposition IS NOT OLD.proposition AND NOT EXISTS (
        SELECT 1 FROM audit_event
        WHERE entity_type = 'FINDING' AND entity_id = NEW.finding_id
          AND field_name = 'proposition'
          AND previous_value IS OLD.proposition AND new_value IS NEW.proposition
    ) THEN RAISE(ABORT, 'Finding proposition change requires an audit event') END;

    SELECT CASE WHEN NEW.finding_type IS NOT OLD.finding_type AND NOT EXISTS (
        SELECT 1 FROM audit_event
        WHERE entity_type = 'FINDING' AND entity_id = NEW.finding_id
          AND field_name = 'finding_type'
          AND previous_value IS OLD.finding_type AND new_value IS NEW.finding_type
    ) THEN RAISE(ABORT, 'Finding type change requires an audit event') END;

    SELECT CASE WHEN NEW.verification_state IS NOT OLD.verification_state AND NOT EXISTS (
        SELECT 1 FROM audit_event
        WHERE entity_type = 'FINDING' AND entity_id = NEW.finding_id
          AND field_name = 'verification_state'
          AND previous_value IS OLD.verification_state AND new_value IS NEW.verification_state
    ) THEN RAISE(ABORT, 'Finding verification change requires an audit event') END;

    SELECT CASE WHEN NEW.verified_by IS NOT OLD.verified_by AND NOT EXISTS (
        SELECT 1 FROM audit_event
        WHERE entity_type = 'FINDING' AND entity_id = NEW.finding_id
          AND field_name = 'verified_by'
          AND previous_value IS OLD.verified_by AND new_value IS NEW.verified_by
    ) THEN RAISE(ABORT, 'Finding verifier change requires an audit event') END;

    SELECT CASE WHEN NEW.created_by IS NOT OLD.created_by AND NOT EXISTS (
        SELECT 1 FROM audit_event
        WHERE entity_type = 'FINDING' AND entity_id = NEW.finding_id
          AND field_name = 'created_by'
          AND previous_value IS OLD.created_by AND new_value IS NEW.created_by
    ) THEN RAISE(ABORT, 'Finding creator change requires an audit event') END;

    SELECT CASE WHEN NEW.package_id IS NOT OLD.package_id AND NOT EXISTS (
        SELECT 1 FROM audit_event
        WHERE entity_type = 'FINDING' AND entity_id = NEW.finding_id
          AND field_name = 'package_id'
          AND previous_value IS OLD.package_id AND new_value IS NEW.package_id
    ) THEN RAISE(ABORT, 'Finding package scope change requires an audit event') END;

    SELECT CASE WHEN NEW.request_element_id IS NOT OLD.request_element_id AND NOT EXISTS (
        SELECT 1 FROM audit_event
        WHERE entity_type = 'FINDING' AND entity_id = NEW.finding_id
          AND field_name = 'request_element_id'
          AND previous_value IS OLD.request_element_id AND new_value IS NEW.request_element_id
    ) THEN RAISE(ABORT, 'Finding request-element scope change requires an audit event') END;

    SELECT CASE WHEN NEW.record_id IS NOT OLD.record_id AND NOT EXISTS (
        SELECT 1 FROM audit_event
        WHERE entity_type = 'FINDING' AND entity_id = NEW.finding_id
          AND field_name = 'record_id'
          AND previous_value IS OLD.record_id AND new_value IS NEW.record_id
    ) THEN RAISE(ABORT, 'Finding record scope change requires an audit event') END;

    SELECT CASE WHEN NEW.record_reference_id IS NOT OLD.record_reference_id AND NOT EXISTS (
        SELECT 1 FROM audit_event
        WHERE entity_type = 'FINDING' AND entity_id = NEW.finding_id
          AND field_name = 'record_reference_id'
          AND previous_value IS OLD.record_reference_id AND new_value IS NEW.record_reference_id
    ) THEN RAISE(ABORT, 'Finding reference scope change requires an audit event') END;

    SELECT CASE WHEN NEW.notes IS NOT OLD.notes AND NOT EXISTS (
        SELECT 1 FROM audit_event
        WHERE entity_type = 'FINDING' AND entity_id = NEW.finding_id
          AND field_name = 'notes'
          AND previous_value IS OLD.notes AND new_value IS NEW.notes
    ) THEN RAISE(ABORT, 'Finding notes change requires an audit event') END;
END;

CREATE TABLE IF NOT EXISTS legal_authority (
    legal_authority_id TEXT PRIMARY KEY,
    authority_type TEXT NOT NULL,
    citation TEXT NOT NULL,
    source_uri TEXT,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS legal_assessment (
    legal_assessment_id TEXT PRIMARY KEY,
    legal_question TEXT NOT NULL,
    conclusion TEXT NOT NULL DEFAULT '',
    primary_legal_authority_id TEXT REFERENCES legal_authority(legal_authority_id),
    assessment_status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (
        assessment_status IN ('DRAFT', 'QUALIFIED', 'FINAL')
    ),
    uncertainty TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_legal_assessment_primary_authority
    ON legal_assessment(primary_legal_authority_id);

CREATE TABLE IF NOT EXISTS legal_assessment_finding (
    legal_assessment_id TEXT NOT NULL REFERENCES legal_assessment(legal_assessment_id),
    finding_id TEXT NOT NULL REFERENCES finding(finding_id),
    PRIMARY KEY (legal_assessment_id, finding_id)
);

CREATE INDEX IF NOT EXISTS idx_legal_assessment_finding_finding
    ON legal_assessment_finding(finding_id);

CREATE TABLE IF NOT EXISTS record_version_link (
    record_version_link_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES record(record_id),
    related_record_id TEXT NOT NULL REFERENCES record(record_id),
    relationship_description TEXT NOT NULL,
    evidence_basis TEXT NOT NULL DEFAULT '',
    CHECK (record_id <> related_record_id),
    UNIQUE(record_id, related_record_id, relationship_description)
);

CREATE INDEX IF NOT EXISTS idx_record_version_link_record ON record_version_link(record_id);
CREATE INDEX IF NOT EXISTS idx_record_version_link_related_record ON record_version_link(related_record_id);

INSERT OR IGNORE INTO vocabulary(domain, code, label, description) VALUES
    ('FINDING_TYPE', 'UNPRODUCED', 'Unproduced', 'Requested material was not produced.'),
    ('FINDING_TYPE', 'NONEXISTENCE_ASSERTED', 'Nonexistence asserted', 'Metro asserted that the record does not exist.'),
    ('FINDING_TYPE', 'SUBSTITUTE_PRODUCTION', 'Substitute production', 'Produced material is relevant but not the requested record.'),
    ('FINDING_TYPE', 'DIRECT_CONTRADICTION', 'Direct contradiction', 'The allegedly nonexistent record itself was located.'),
    ('FINDING_TYPE', 'STRONG_EXISTENCE_EVIDENCE', 'Strong existence evidence', 'A Metro record specifically identifies the record.'),
    ('FINDING_TYPE', 'POSSIBLE_EXISTENCE_EVIDENCE', 'Possible existence evidence', 'A reference concerns the subject but identity is not precise.'),
    ('FINDING_TYPE', 'PRODUCED_FULL', 'Produced in full', 'The requested record was produced in full.'),
    ('FINDING_TYPE', 'PRODUCED_PARTIAL_REDACTED', 'Produced partial or redacted', 'The record was partially produced or redacted.'),
    ('FINDING_TYPE', 'WITHHELD_WHOLE_OR_PART', 'Withheld whole or part', 'The record was withheld in whole or part.'),
    ('FINDING_TYPE', 'WITHHOLDING_BASIS_STATED', 'Withholding basis stated', 'A withholding basis was stated.'),
    ('FINDING_TYPE', 'NO_WITHHOLDING_BASIS_STATED', 'No withholding basis stated', 'No withholding basis was stated.'),
    ('PACKAGE_STATUS', 'NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED', 'No output production response received', 'No associated production output was received.'),
    ('VERIFICATION_STATE', 'PROVISIONAL', 'Provisional', 'Machine or extraction-based identification suitable for discovery.'),
    ('VERIFICATION_STATE', 'VERIFIED', 'Verified', 'Checked against the actual source location.'),
    ('REFERENCE_MATCH_STATE', 'CONFIRMED_MATCH', 'Confirmed match', 'Identifiers or content establish identity.'),
    ('REFERENCE_MATCH_STATE', 'PROBABLE_MATCH', 'Probable match', 'Evidence strongly suggests identity but is not conclusive.'),
    ('REFERENCE_MATCH_STATE', 'NO_MATCH_LOCATED', 'No match located', 'No analyzed item is presently identified as the referenced record.'),
    ('ABSENCE_SCOPE', 'NOT_LOCATED_RESPONSIVE_PACKAGE', 'Not located in responsive package', 'Not located in the responsive package.'),
    ('ABSENCE_SCOPE', 'LOCATED_ELSEWHERE_CORPUS', 'Located elsewhere in corpus', 'Located in another analyzed package.'),
    ('ABSENCE_SCOPE', 'NOT_LOCATED_CORPUS', 'Not located in corpus', 'Not located after the applicable verified corpus search.'),
    ('ABSENCE_SCOPE', 'CORPUS_SEARCH_INCOMPLETE', 'Corpus search incomplete', 'The corpus search is not complete.'),
    ('REVIEW_TASK_STATE', 'OPEN', 'Open', 'Requires review or resolution.'),
    ('REVIEW_TASK_STATE', 'UNRESOLVED', 'Unresolved', 'Reviewed but not safely resolvable.'),
    ('REVIEW_TASK_STATE', 'RESOLVED', 'Resolved', 'Explicitly resolved by a reviewer.'),
    ('COMPLETENESS_STATE', 'IN_PROGRESS', 'In progress', 'Analysis for the defined scope is incomplete.'),
    ('COMPLETENESS_STATE', 'REVIEW_REQUIRED', 'Review required', 'Open review tasks can affect the result.'),
    ('COMPLETENESS_STATE', 'COMPLETE_WITH_EXCEPTIONS', 'Complete with exceptions', 'Processing is complete with unresolved issues.'),
    ('COMPLETENESS_STATE', 'VERIFIED_COMPLETE', 'Verified complete', 'Required verification is complete.'),
    ('STATEMENT_TYPE', 'NONEXISTENCE_ASSERTION', 'Nonexistence assertion', 'Metro statement asserting nonexistence.'),
    ('STATEMENT_TYPE', 'DENIAL', 'Denial', 'Metro denial statement.'),
    ('STATEMENT_TYPE', 'WITHHOLDING_BASIS', 'Withholding basis', 'Metro statement of withholding basis.'),
    ('REFERENCE_RELATIONSHIP_TYPE', 'ATTACHMENT', 'Attachment', 'Referenced attachment.'),
    ('REFERENCE_RELATIONSHIP_TYPE', 'EXHIBIT', 'Exhibit', 'Referenced exhibit.'),
    ('REFERENCE_RELATIONSHIP_TYPE', 'CONTRACT', 'Contract', 'Referenced contract.'),
    ('REFERENCE_RELATIONSHIP_TYPE', 'REPORT', 'Report', 'Referenced report.'),
    ('REFERENCE_RELATIONSHIP_TYPE', 'STUDY', 'Study', 'Referenced study.'),
    ('REFERENCE_RELATIONSHIP_TYPE', 'INVOICE', 'Invoice', 'Referenced invoice.'),
    ('REFERENCE_RELATIONSHIP_TYPE', 'PROPOSAL', 'Proposal', 'Referenced proposal.'),
    ('REFERENCE_RELATIONSHIP_TYPE', 'SPREADSHEET', 'Spreadsheet', 'Referenced spreadsheet.'),
    ('DATE_ROLE', 'RECORD_DATE', 'Record date', 'Documented date of a record.'),
    ('DATE_ROLE', 'REFERENCE_DATE', 'Reference date', 'Documented date of a reference.'),
    ('DATE_ROLE', 'REQUEST_DATE', 'Request date', 'Documented date of a request.'),
    ('DATE_ROLE', 'RESPONSE_DATE', 'Response date', 'Documented date of a response.'),
    ('DATE_ROLE', 'DISCOVERY_DATE', 'Discovery date', 'Documented date of discovery.'),
    ('EVIDENTIARY_ROLE', 'RESPONSIVE', 'Responsive', 'Actual material responsive to the request element.'),
    ('EVIDENTIARY_ROLE', 'SUBSTITUTE', 'Substitute', 'Related substitute material.'),
    ('EVIDENTIARY_ROLE', 'EXISTENCE_EVIDENCE', 'Existence evidence', 'Evidence bearing on existence.'),
    ('EVIDENTIARY_ROLE', 'CONTRADICTION_EVIDENCE', 'Contradiction evidence', 'Evidence bearing on contradiction.'),
    ('EVIDENTIARY_ROLE', 'CONTEXT', 'Context', 'Contextual evidence.');
