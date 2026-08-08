PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS vocabulary (
    domain TEXT NOT NULL,
    code TEXT NOT NULL,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    deprecated INTEGER NOT NULL DEFAULT 0 CHECK (deprecated IN (0,1)),
    PRIMARY KEY (domain, code)
);

CREATE TABLE IF NOT EXISTS operational_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS operational_metadata_is_immutable
BEFORE UPDATE ON operational_metadata
BEGIN
    SELECT RAISE(ABORT, 'OPERATIONAL_METADATA is immutable');
END;

CREATE TRIGGER IF NOT EXISTS operational_metadata_cannot_be_deleted
BEFORE DELETE ON operational_metadata
BEGIN
    SELECT RAISE(ABORT, 'OPERATIONAL_METADATA is immutable');
END;

CREATE TRIGGER IF NOT EXISTS operational_metadata_intake_root_no_replace
BEFORE INSERT ON operational_metadata
WHEN EXISTS (
    SELECT 1
    FROM operational_metadata AS bound
    WHERE bound.key='intake_evidence_root'
      AND (NEW.key=bound.key OR NEW.rowid IS bound.rowid)
)
BEGIN
    SELECT RAISE(ABORT, 'intake evidence root is immutable once bound');
END;

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
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_file_package_member_path
    ON source_file(package_id, archive_member_path);

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

CREATE TRIGGER IF NOT EXISTS occurrence_derivative_matches_source_insert
BEFORE INSERT ON occurrence
WHEN NEW.derivative_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM derivative
    WHERE derivative_id=NEW.derivative_id AND source_file_id=NEW.source_file_id
)
BEGIN
    SELECT RAISE(ABORT, 'Occurrence derivative must belong to the same source');
END;

CREATE TRIGGER IF NOT EXISTS occurrence_identity_is_immutable
BEFORE UPDATE OF record_id, source_file_id, derivative_id, source_locator ON occurrence
WHEN NEW.record_id IS NOT OLD.record_id
  OR NEW.source_file_id IS NOT OLD.source_file_id
  OR NEW.derivative_id IS NOT OLD.derivative_id
  OR NEW.source_locator IS NOT OLD.source_locator
BEGIN
    SELECT RAISE(ABORT, 'OCCURRENCE identity and provenance are immutable');
END;

CREATE TRIGGER IF NOT EXISTS occurrence_verified_reviewer_insert
BEFORE INSERT ON occurrence
WHEN NEW.verification_state = 'VERIFIED'
 AND NOT EXISTS (
    SELECT 1 FROM reviewer_identity
    WHERE reviewer_id=NEW.verified_by AND identity_type='HUMAN'
 )
BEGIN
    SELECT RAISE(ABORT, 'VERIFIED occurrence requires a registered HUMAN reviewer');
END;

CREATE TRIGGER IF NOT EXISTS occurrence_cannot_start_verified
BEFORE INSERT ON occurrence
WHEN NEW.verification_state = 'VERIFIED'
BEGIN
    SELECT RAISE(ABORT, 'Occurrences must be created PROVISIONAL');
END;

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

CREATE TRIGGER IF NOT EXISTS evidence_citation_exact_locator_insert
BEFORE INSERT ON evidence_citation
WHEN is_exact_locator(NEW.locator) <> 1
BEGIN
    SELECT RAISE(ABORT, 'Evidence citation requires an exact Level 1 locator');
END;

CREATE TRIGGER IF NOT EXISTS evidence_citation_exact_locator_update
BEFORE UPDATE OF locator ON evidence_citation
WHEN is_exact_locator(NEW.locator) <> 1
BEGIN
    SELECT RAISE(ABORT, 'Evidence citation requires an exact Level 1 locator');
END;

CREATE TRIGGER IF NOT EXISTS evidence_citation_source_identity_is_immutable
BEFORE UPDATE OF source_file_id, occurrence_id, locator ON evidence_citation
WHEN NEW.source_file_id IS NOT OLD.source_file_id
  OR NEW.occurrence_id IS NOT OLD.occurrence_id
  OR NEW.locator IS NOT OLD.locator
BEGIN
    SELECT RAISE(ABORT, 'EVIDENCE_CITATION source identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS evidence_citation_occurrence_matches_source_insert
BEFORE INSERT ON evidence_citation
WHEN NEW.occurrence_id IS NOT NULL AND NEW.source_file_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1 FROM occurrence
    WHERE occurrence_id=NEW.occurrence_id AND source_file_id=NEW.source_file_id
 )
BEGIN
    SELECT RAISE(ABORT, 'Citation occurrence must belong to its source file');
END;

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

CREATE TRIGGER IF NOT EXISTS finding_cannot_start_verified
BEFORE INSERT ON finding
WHEN NEW.verification_state = 'VERIFIED'
BEGIN
    SELECT RAISE(ABORT, 'Findings must be created PROVISIONAL');
END;

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
    raw_value TEXT NOT NULL,
    normalized_value TEXT,
    precision TEXT NOT NULL CHECK (
        precision IN ('DAY', 'MONTH', 'YEAR', 'APPROXIMATE', 'CONFLICTING')
    ),
    evidence_citation_id TEXT NOT NULL REFERENCES evidence_citation(evidence_citation_id),
    notes TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_date_fact_evidence_citation ON date_fact(evidence_citation_id);
CREATE INDEX IF NOT EXISTS idx_date_fact_entity ON date_fact(entity_type, entity_id);

CREATE TRIGGER IF NOT EXISTS date_fact_no_update
BEFORE UPDATE ON date_fact
BEGIN
    SELECT RAISE(ABORT, 'DATE_FACT is append-only');
END;

CREATE TRIGGER IF NOT EXISTS date_fact_no_delete
BEFORE DELETE ON date_fact
BEGIN
    SELECT RAISE(ABORT, 'DATE_FACT is append-only');
END;

CREATE TABLE IF NOT EXISTS temporal_inference (
    temporal_inference_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    proposition TEXT NOT NULL,
    inference_type TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'PROVISIONAL' CHECK (
        verification_state IN ('PROVISIONAL', 'VERIFIED')
    ),
    verified_by TEXT,
    supporting_citation_id TEXT REFERENCES evidence_citation(evidence_citation_id),
    possession_supporting_finding_id TEXT REFERENCES finding(finding_id),
    notes TEXT NOT NULL DEFAULT '',
    CHECK (verification_state <> 'VERIFIED' OR verified_by IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_temporal_inference_supporting_citation
    ON temporal_inference(supporting_citation_id);
CREATE INDEX IF NOT EXISTS idx_temporal_inference_entity ON temporal_inference(entity_type, entity_id);
CREATE TRIGGER IF NOT EXISTS temporal_inference_no_update
BEFORE UPDATE ON temporal_inference
BEGIN
    SELECT RAISE(ABORT, 'TEMPORAL_INFERENCE is append-only');
END;

CREATE TRIGGER IF NOT EXISTS temporal_inference_no_delete
BEFORE DELETE ON temporal_inference
BEGIN
    SELECT RAISE(ABORT, 'TEMPORAL_INFERENCE is append-only');
END;

CREATE TRIGGER IF NOT EXISTS temporal_possession_requires_controlled_support_type
BEFORE INSERT ON temporal_inference
WHEN NEW.inference_type='POSSESSED_AT_RESPONSE'
BEGIN
    SELECT RAISE(ABORT, 'No controlled possession-supporting finding type exists');
END;

CREATE TABLE IF NOT EXISTS temporal_inference_date_fact (
    temporal_inference_id TEXT NOT NULL REFERENCES temporal_inference(temporal_inference_id),
    date_fact_id TEXT NOT NULL REFERENCES date_fact(date_fact_id),
    PRIMARY KEY (temporal_inference_id, date_fact_id)
);

CREATE INDEX IF NOT EXISTS idx_temporal_inference_date_fact_date_fact
    ON temporal_inference_date_fact(date_fact_id);

CREATE TRIGGER IF NOT EXISTS temporal_inference_date_fact_no_update
BEFORE UPDATE ON temporal_inference_date_fact
BEGIN
    SELECT RAISE(ABORT, 'TEMPORAL_INFERENCE_DATE_FACT is append-only');
END;

CREATE TABLE IF NOT EXISTS temporal_inference_finding (
    temporal_inference_id TEXT NOT NULL REFERENCES temporal_inference(temporal_inference_id),
    finding_id TEXT NOT NULL REFERENCES finding(finding_id),
    PRIMARY KEY (temporal_inference_id, finding_id)
);

CREATE INDEX IF NOT EXISTS idx_temporal_inference_finding_finding
    ON temporal_inference_finding(finding_id);

CREATE TRIGGER IF NOT EXISTS temporal_inference_finding_no_update
BEFORE UPDATE ON temporal_inference_finding
BEGIN
    SELECT RAISE(ABORT, 'TEMPORAL_INFERENCE_FINDING is append-only');
END;

CREATE TRIGGER IF NOT EXISTS temporal_inference_finding_no_delete
BEFORE DELETE ON temporal_inference_finding
BEGIN
    SELECT RAISE(ABORT, 'TEMPORAL_INFERENCE_FINDING is append-only');
END;

CREATE TRIGGER IF NOT EXISTS temporal_inference_date_fact_no_delete
BEFORE DELETE ON temporal_inference_date_fact
BEGIN
    SELECT RAISE(ABORT, 'TEMPORAL_INFERENCE_DATE_FACT is append-only');
END;

CREATE TABLE IF NOT EXISTS review_task (
    review_task_id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES package(package_id),
    request_element_id TEXT REFERENCES request_element(request_element_id),
    source_file_id TEXT REFERENCES source_file(source_file_id),
    occurrence_id TEXT REFERENCES occurrence(occurrence_id),
    record_reference_id TEXT REFERENCES record_reference(record_reference_id),
    finding_id TEXT REFERENCES finding(finding_id),
    task_type TEXT NOT NULL,
    reason_code TEXT,
    task_state TEXT NOT NULL DEFAULT 'OPEN' CHECK (task_state IN ('OPEN', 'UNRESOLVED', 'RESOLVED')),
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
);

CREATE INDEX IF NOT EXISTS idx_review_task_package ON review_task(package_id);
CREATE INDEX IF NOT EXISTS idx_review_task_request_element ON review_task(request_element_id);
CREATE INDEX IF NOT EXISTS idx_review_task_source_file ON review_task(source_file_id);
CREATE INDEX IF NOT EXISTS idx_review_task_occurrence ON review_task(occurrence_id);
CREATE INDEX IF NOT EXISTS idx_review_task_record_reference ON review_task(record_reference_id);
CREATE INDEX IF NOT EXISTS idx_review_task_supporting_citation ON review_task(supporting_citation_id);

CREATE TRIGGER IF NOT EXISTS review_task_no_delete
BEFORE DELETE ON review_task
BEGIN
    SELECT RAISE(ABORT, 'REVIEW_TASK rows are non-deletable');
END;

CREATE TABLE IF NOT EXISTS review_task_identity (
    review_task_id TEXT PRIMARY KEY
        REFERENCES review_task(review_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TRIGGER IF NOT EXISTS review_task_identity_no_update
BEFORE UPDATE ON review_task_identity
BEGIN
    SELECT RAISE(ABORT, 'REVIEW_TASK identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS review_task_identity_no_delete
BEFORE DELETE ON review_task_identity
BEGIN
    SELECT RAISE(ABORT, 'REVIEW_TASK identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS review_task_identity_created_with_task
AFTER INSERT ON review_task
BEGIN
    INSERT INTO review_task_identity(review_task_id) VALUES (NEW.review_task_id);
END;

CREATE TRIGGER IF NOT EXISTS review_task_authority_is_immutable
BEFORE UPDATE OF review_task_id,package_id,request_element_id,source_file_id,
                 occurrence_id,record_reference_id,finding_id,task_type,reason_code,material
ON review_task
WHEN NEW.review_task_id IS NOT OLD.review_task_id
  OR NEW.package_id IS NOT OLD.package_id
  OR NEW.request_element_id IS NOT OLD.request_element_id
  OR NEW.source_file_id IS NOT OLD.source_file_id
  OR NEW.occurrence_id IS NOT OLD.occurrence_id
  OR NEW.record_reference_id IS NOT OLD.record_reference_id
  OR NEW.finding_id IS NOT OLD.finding_id
  OR NEW.task_type IS NOT OLD.task_type
  OR NEW.reason_code IS NOT OLD.reason_code
  OR NEW.material IS NOT OLD.material
BEGIN
    SELECT RAISE(ABORT, 'REVIEW_TASK authority fields are immutable');
END;

CREATE TABLE IF NOT EXISTS reviewer_identity (
    reviewer_id TEXT PRIMARY KEY,
    identity_type TEXT NOT NULL CHECK (identity_type IN ('HUMAN', 'AUTOMATION'))
);

CREATE TRIGGER IF NOT EXISTS reviewer_identity_no_type_replacement
BEFORE INSERT ON reviewer_identity
WHEN EXISTS (
    SELECT 1 FROM reviewer_identity
    WHERE reviewer_id=NEW.reviewer_id AND identity_type<>NEW.identity_type
)
BEGIN
    SELECT RAISE(ABORT, 'REVIEWER_IDENTITY type is immutable');
END;

CREATE TRIGGER IF NOT EXISTS reviewer_identity_no_update
BEFORE UPDATE ON reviewer_identity
BEGIN
    SELECT RAISE(ABORT, 'REVIEWER_IDENTITY is immutable');
END;

CREATE TRIGGER IF NOT EXISTS reviewer_identity_no_delete
BEFORE DELETE ON reviewer_identity
BEGIN
    SELECT RAISE(ABORT, 'REVIEWER_IDENTITY rows are non-deletable');
END;

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

CREATE TRIGGER IF NOT EXISTS finding_audit_requires_registered_human_insert
BEFORE INSERT ON audit_event
WHEN NEW.entity_type='FINDING'
 AND NEW.field_name IS NOT 'CREATE'
 AND NOT EXISTS (
    SELECT 1 FROM reviewer_identity
    WHERE reviewer_id=NEW.change_source AND identity_type='HUMAN'
 )
BEGIN
    SELECT RAISE(ABORT, 'Finding transition audit requires a registered HUMAN identity');
END;

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

CREATE TRIGGER IF NOT EXISTS audit_event_no_replace
BEFORE INSERT ON audit_event
WHEN EXISTS (
    SELECT 1 FROM audit_event AS existing
    WHERE existing.event_id=NEW.event_id OR existing.rowid IS NEW.rowid
)
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_EVENT identity is immutable');
END;

CREATE TABLE IF NOT EXISTS audit_event_use (
    event_id TEXT PRIMARY KEY REFERENCES audit_event(event_id),
    used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    field_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_event_use_identity (
    event_id TEXT PRIMARY KEY
        REFERENCES audit_event_use(event_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TRIGGER IF NOT EXISTS audit_event_use_identity_no_update
BEFORE UPDATE ON audit_event_use_identity
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_EVENT_USE identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS audit_event_use_identity_no_delete
BEFORE DELETE ON audit_event_use_identity
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_EVENT_USE identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS audit_event_use_identity_no_replace
BEFORE INSERT ON audit_event_use_identity
WHEN EXISTS (
    SELECT 1 FROM audit_event_use_identity AS existing
    WHERE existing.event_id=NEW.event_id OR existing.rowid IS NEW.rowid
)
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_EVENT_USE identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS audit_event_use_identity_created_with_use
AFTER INSERT ON audit_event_use
WHEN NOT EXISTS (
    SELECT 1 FROM audit_event_use_identity WHERE event_id=NEW.event_id
)
BEGIN
    INSERT INTO audit_event_use_identity(event_id) VALUES (NEW.event_id);
END;

CREATE TRIGGER IF NOT EXISTS audit_event_use_no_update
BEFORE UPDATE ON audit_event_use
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_EVENT_USE is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_event_use_no_delete
BEFORE DELETE ON audit_event_use
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_EVENT_USE is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_event_use_no_replace
BEFORE INSERT ON audit_event_use
WHEN EXISTS (
    SELECT 1 FROM audit_event_use AS existing
    WHERE existing.event_id=NEW.event_id OR existing.rowid IS NEW.rowid
)
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_EVENT_USE identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS audit_event_use_matches_event
BEFORE INSERT ON audit_event_use
WHEN NOT EXISTS (
    SELECT 1 FROM audit_event AS ae
    WHERE ae.event_id=NEW.event_id
      AND ae.entity_type=NEW.entity_type
      AND ae.entity_id=NEW.entity_id
      AND COALESCE(ae.field_name,'')=NEW.field_name
)
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_EVENT_USE must match its event');
END;

CREATE TRIGGER IF NOT EXISTS review_task_insert_requires_creation_audit
BEFORE INSERT ON review_task
WHEN NEW.task_state<>'OPEN'
  OR NEW.reviewer IS NOT NULL
  OR NEW.resolved_at IS NOT NULL
  OR NEW.resolution IS NOT NULL
  OR NEW.supporting_citation_id IS NOT NULL
  OR NOT EXISTS (
      SELECT 1 FROM audit_event AS ae
      LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
      WHERE used.event_id IS NULL
        AND ae.entity_type='REVIEW_TASK'
        AND ae.entity_id=NEW.review_task_id
        AND ae.field_name='CREATE'
        AND ae.previous_value IS NULL
        AND ae.new_value='OPEN'
  )
BEGIN
    SELECT RAISE(ABORT, 'REVIEW_TASK creation requires an unused creation audit');
END;

CREATE TRIGGER IF NOT EXISTS review_task_insert_consumes_creation_audit
AFTER INSERT ON review_task
BEGIN
    INSERT INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'REVIEW_TASK',NEW.review_task_id,'CREATE'
    FROM audit_event AS ae
    LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
    WHERE used.event_id IS NULL
      AND ae.entity_type='REVIEW_TASK'
      AND ae.entity_id=NEW.review_task_id
      AND ae.field_name='CREATE'
      AND ae.previous_value IS NULL
      AND ae.new_value='OPEN';
END;

CREATE TRIGGER IF NOT EXISTS corpus_package_insert_requires_unused_human_audit
BEFORE INSERT ON corpus_package
WHEN NOT EXISTS (
    SELECT 1
    FROM audit_event AS ae
    JOIN reviewer_identity AS reviewer ON reviewer.reviewer_id=ae.change_source
    LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
    WHERE ae.entity_type='CORPUS'
      AND ae.entity_id=NEW.corpus_id
      AND ae.field_name='package_membership'
      AND ae.previous_value IS NULL
      AND ae.new_value IS NEW.package_id
      AND reviewer.identity_type='HUMAN'
      AND used.event_id IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'Corpus membership requires an unused human audit event');
END;

CREATE TRIGGER IF NOT EXISTS corpus_package_insert_consumes_audit
AFTER INSERT ON corpus_package
BEGIN
    INSERT INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'CORPUS',NEW.corpus_id,'package_membership'
    FROM audit_event AS ae
    JOIN reviewer_identity AS reviewer ON reviewer.reviewer_id=ae.change_source
    LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
    WHERE ae.entity_type='CORPUS'
      AND ae.entity_id=NEW.corpus_id
      AND ae.field_name='package_membership'
      AND ae.previous_value IS NULL
      AND ae.new_value IS NEW.package_id
      AND reviewer.identity_type='HUMAN'
      AND used.event_id IS NULL;
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

CREATE TRIGGER IF NOT EXISTS finding_change_requires_unused_audit_event
BEFORE UPDATE ON finding
BEGIN
    SELECT CASE WHEN NEW.proposition IS NOT OLD.proposition AND NOT EXISTS (
        SELECT 1 FROM audit_event ae
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND ae.entity_type='FINDING'
          AND ae.entity_id=OLD.finding_id AND ae.field_name='proposition'
          AND ae.previous_value IS OLD.proposition AND ae.new_value IS NEW.proposition
    ) THEN RAISE(ABORT, 'Finding proposition change requires an unused audit event') END;

    SELECT CASE WHEN NEW.finding_type IS NOT OLD.finding_type AND NOT EXISTS (
        SELECT 1 FROM audit_event ae
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND ae.entity_type='FINDING'
          AND ae.entity_id=OLD.finding_id AND ae.field_name='finding_type'
          AND ae.previous_value IS OLD.finding_type AND ae.new_value IS NEW.finding_type
    ) THEN RAISE(ABORT, 'Finding type change requires an unused audit event') END;

    SELECT CASE WHEN NEW.verification_state IS NOT OLD.verification_state AND NOT EXISTS (
        SELECT 1 FROM audit_event ae
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND ae.entity_type='FINDING'
          AND ae.entity_id=OLD.finding_id AND ae.field_name='verification_state'
          AND ae.previous_value IS OLD.verification_state AND ae.new_value IS NEW.verification_state
    ) THEN RAISE(ABORT, 'Finding verification change requires an unused audit event') END;

    SELECT CASE WHEN NEW.verified_by IS NOT OLD.verified_by AND NOT EXISTS (
        SELECT 1 FROM audit_event ae
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND ae.entity_type='FINDING'
          AND ae.entity_id=OLD.finding_id AND ae.field_name='verified_by'
          AND ae.previous_value IS OLD.verified_by AND ae.new_value IS NEW.verified_by
    ) THEN RAISE(ABORT, 'Finding verifier change requires an unused audit event') END;

    SELECT CASE WHEN NEW.notes IS NOT OLD.notes AND NOT EXISTS (
        SELECT 1 FROM audit_event ae
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND ae.entity_type='FINDING'
          AND ae.entity_id=OLD.finding_id AND ae.field_name='notes'
          AND ae.previous_value IS OLD.notes AND ae.new_value IS NEW.notes
    ) THEN RAISE(ABORT, 'Finding notes change requires an unused audit event') END;
END;

CREATE TRIGGER IF NOT EXISTS finding_change_requires_unused_human_audit_event
BEFORE UPDATE ON finding
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM (
            SELECT 'proposition' AS field_name,OLD.proposition AS old_value,NEW.proposition AS new_value
            UNION ALL SELECT 'finding_type',OLD.finding_type,NEW.finding_type
            UNION ALL SELECT 'verification_state',OLD.verification_state,NEW.verification_state
            UNION ALL SELECT 'verified_by',OLD.verified_by,NEW.verified_by
            UNION ALL SELECT 'created_by',OLD.created_by,NEW.created_by
            UNION ALL SELECT 'package_id',OLD.package_id,NEW.package_id
            UNION ALL SELECT 'request_element_id',OLD.request_element_id,NEW.request_element_id
            UNION ALL SELECT 'record_id',OLD.record_id,NEW.record_id
            UNION ALL SELECT 'record_reference_id',OLD.record_reference_id,NEW.record_reference_id
            UNION ALL SELECT 'notes',OLD.notes,NEW.notes
        ) change
        WHERE change.old_value IS NOT change.new_value AND NOT EXISTS (
            SELECT 1 FROM audit_event ae
            JOIN reviewer_identity identity ON identity.reviewer_id=ae.change_source
            LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
            WHERE used.event_id IS NULL AND identity.identity_type='HUMAN'
              AND ae.entity_type='FINDING' AND ae.entity_id=OLD.finding_id
              AND ae.field_name=change.field_name
              AND ae.previous_value IS change.old_value AND ae.new_value IS change.new_value
        )
    ) THEN RAISE(ABORT, 'Finding change requires an unused human audit event') END;
END;

CREATE TRIGGER IF NOT EXISTS finding_change_consumes_human_audit_events
AFTER UPDATE ON finding
BEGIN
    INSERT OR IGNORE INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'FINDING',OLD.finding_id,ae.field_name
    FROM audit_event ae
    JOIN reviewer_identity identity ON identity.reviewer_id=ae.change_source
    LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
    WHERE used.event_id IS NULL AND identity.identity_type='HUMAN'
      AND ae.entity_type='FINDING' AND ae.entity_id=OLD.finding_id
      AND (
        (ae.field_name='proposition' AND NEW.proposition IS NOT OLD.proposition
         AND ae.previous_value IS OLD.proposition AND ae.new_value IS NEW.proposition)
        OR (ae.field_name='finding_type' AND NEW.finding_type IS NOT OLD.finding_type
         AND ae.previous_value IS OLD.finding_type AND ae.new_value IS NEW.finding_type)
        OR (ae.field_name='verification_state' AND NEW.verification_state IS NOT OLD.verification_state
         AND ae.previous_value IS OLD.verification_state AND ae.new_value IS NEW.verification_state)
        OR (ae.field_name='verified_by' AND NEW.verified_by IS NOT OLD.verified_by
         AND ae.previous_value IS OLD.verified_by AND ae.new_value IS NEW.verified_by)
        OR (ae.field_name='created_by' AND NEW.created_by IS NOT OLD.created_by
         AND ae.previous_value IS OLD.created_by AND ae.new_value IS NEW.created_by)
        OR (ae.field_name='package_id' AND NEW.package_id IS NOT OLD.package_id
         AND ae.previous_value IS OLD.package_id AND ae.new_value IS NEW.package_id)
        OR (ae.field_name='request_element_id' AND NEW.request_element_id IS NOT OLD.request_element_id
         AND ae.previous_value IS OLD.request_element_id AND ae.new_value IS NEW.request_element_id)
        OR (ae.field_name='record_id' AND NEW.record_id IS NOT OLD.record_id
         AND ae.previous_value IS OLD.record_id AND ae.new_value IS NEW.record_id)
        OR (ae.field_name='record_reference_id' AND NEW.record_reference_id IS NOT OLD.record_reference_id
         AND ae.previous_value IS OLD.record_reference_id AND ae.new_value IS NEW.record_reference_id)
        OR (ae.field_name='notes' AND NEW.notes IS NOT OLD.notes
         AND ae.previous_value IS OLD.notes AND ae.new_value IS NEW.notes)
      );
END;

CREATE TRIGGER IF NOT EXISTS finding_change_consumes_audit_events
AFTER UPDATE ON finding
BEGIN
    INSERT OR IGNORE INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'FINDING',OLD.finding_id,ae.field_name
    FROM audit_event ae LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
    WHERE used.event_id IS NULL AND ae.entity_type='FINDING' AND ae.entity_id=OLD.finding_id
      AND (
        (ae.field_name='proposition' AND NEW.proposition IS NOT OLD.proposition
         AND ae.previous_value IS OLD.proposition AND ae.new_value IS NEW.proposition)
        OR (ae.field_name='finding_type' AND NEW.finding_type IS NOT OLD.finding_type
         AND ae.previous_value IS OLD.finding_type AND ae.new_value IS NEW.finding_type)
        OR (ae.field_name='verification_state' AND NEW.verification_state IS NOT OLD.verification_state
         AND ae.previous_value IS OLD.verification_state AND ae.new_value IS NEW.verification_state)
        OR (ae.field_name='verified_by' AND NEW.verified_by IS NOT OLD.verified_by
         AND ae.previous_value IS OLD.verified_by AND ae.new_value IS NEW.verified_by)
        OR (ae.field_name='notes' AND NEW.notes IS NOT OLD.notes
         AND ae.previous_value IS OLD.notes AND ae.new_value IS NEW.notes)
      );
END;

CREATE TRIGGER IF NOT EXISTS record_canonical_identity_is_immutable
BEFORE UPDATE OF record_id, content_fingerprint, canonical_identity_basis ON record
WHEN NEW.record_id IS NOT OLD.record_id
  OR NEW.content_fingerprint IS NOT OLD.content_fingerprint
  OR NEW.canonical_identity_basis IS NOT OLD.canonical_identity_basis
BEGIN
    SELECT RAISE(ABORT, 'RECORD canonical identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS metro_statement_content_is_immutable
BEFORE UPDATE OF package_id, source_file_id, statement_text, statement_type, source_locator
ON metro_statement
WHEN NEW.package_id IS NOT OLD.package_id
  OR NEW.source_file_id IS NOT OLD.source_file_id
  OR NEW.statement_text IS NOT OLD.statement_text
  OR NEW.statement_type IS NOT OLD.statement_type
  OR NEW.source_locator IS NOT OLD.source_locator
BEGIN
    SELECT RAISE(ABORT, 'METRO_STATEMENT source identity and content are immutable');
END;

CREATE TABLE IF NOT EXISTS legal_authority (
    legal_authority_id TEXT PRIMARY KEY,
    legal_assessment_id TEXT REFERENCES legal_assessment(legal_assessment_id),
    authority_type TEXT NOT NULL,
    citation TEXT NOT NULL,
    source_uri TEXT,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS legal_authority_no_update
BEFORE UPDATE ON legal_authority
BEGIN
    SELECT RAISE(ABORT, 'LEGAL_AUTHORITY is append-only');
END;

CREATE TRIGGER IF NOT EXISTS legal_authority_no_delete
BEFORE DELETE ON legal_authority
BEGIN
    SELECT RAISE(ABORT, 'LEGAL_AUTHORITY is append-only');
END;

CREATE TABLE IF NOT EXISTS legal_assessment (
    legal_assessment_id TEXT PRIMARY KEY,
    legal_question TEXT NOT NULL,
    conclusion TEXT NOT NULL DEFAULT '',
    primary_legal_authority_id TEXT REFERENCES legal_authority(legal_authority_id),
    assessment_status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (
        assessment_status IN ('DRAFT', 'QUALIFIED', 'FINAL')
    ),
    uncertainty TEXT NOT NULL DEFAULT '',
    finalized_by TEXT REFERENCES reviewer_identity(reviewer_id),
    finalized_at TEXT,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_legal_assessment_primary_authority
    ON legal_assessment(primary_legal_authority_id);

CREATE TRIGGER IF NOT EXISTS legal_assessment_content_is_immutable
BEFORE UPDATE ON legal_assessment
WHEN NEW.legal_question IS NOT OLD.legal_question
  OR NEW.conclusion IS NOT OLD.conclusion
  OR NEW.primary_legal_authority_id IS NOT OLD.primary_legal_authority_id
  OR NEW.uncertainty IS NOT OLD.uncertainty
  OR NEW.notes IS NOT OLD.notes
BEGIN
    SELECT RAISE(ABORT, 'LEGAL_ASSESSMENT content is immutable');
END;

CREATE TRIGGER IF NOT EXISTS legal_assessment_status_requires_audit
BEFORE UPDATE OF assessment_status ON legal_assessment
WHEN NEW.assessment_status IS NOT OLD.assessment_status
 AND NOT EXISTS (
    SELECT 1
    FROM audit_event AS ae
    JOIN reviewer_identity AS reviewer ON reviewer.reviewer_id=ae.change_source
    LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
    WHERE ae.entity_type = 'LEGAL_ASSESSMENT'
      AND ae.entity_id = OLD.legal_assessment_id
      AND ae.field_name = 'assessment_status'
      AND ae.previous_value IS OLD.assessment_status
      AND ae.new_value IS NEW.assessment_status
      AND reviewer.identity_type='HUMAN'
      AND used.event_id IS NULL
 )
BEGIN
    SELECT RAISE(ABORT, 'Legal assessment status change requires an unused human audit event');
END;

CREATE TRIGGER IF NOT EXISTS legal_assessment_status_consumes_audit
AFTER UPDATE OF assessment_status ON legal_assessment
WHEN NEW.assessment_status IS NOT OLD.assessment_status
BEGIN
    INSERT INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'LEGAL_ASSESSMENT',OLD.legal_assessment_id,'assessment_status'
    FROM audit_event AS ae
    JOIN reviewer_identity AS reviewer ON reviewer.reviewer_id=ae.change_source
    LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
    WHERE ae.entity_type='LEGAL_ASSESSMENT'
      AND ae.entity_id=OLD.legal_assessment_id
      AND ae.field_name='assessment_status'
      AND ae.previous_value IS OLD.assessment_status
      AND ae.new_value IS NEW.assessment_status
      AND reviewer.identity_type='HUMAN'
      AND used.event_id IS NULL;
END;

CREATE TRIGGER IF NOT EXISTS legal_assessment_no_delete
BEFORE DELETE ON legal_assessment
BEGIN
    SELECT RAISE(ABORT, 'LEGAL_ASSESSMENT is append-only');
END;

CREATE TABLE IF NOT EXISTS legal_assessment_finding (
    legal_assessment_id TEXT NOT NULL REFERENCES legal_assessment(legal_assessment_id),
    finding_id TEXT NOT NULL REFERENCES finding(finding_id),
    PRIMARY KEY (legal_assessment_id, finding_id)
);

CREATE INDEX IF NOT EXISTS idx_legal_assessment_finding_finding
    ON legal_assessment_finding(finding_id);

CREATE TRIGGER IF NOT EXISTS legal_assessment_finding_no_update
BEFORE UPDATE ON legal_assessment_finding
BEGIN
    SELECT RAISE(ABORT, 'LEGAL_ASSESSMENT_FINDING is append-only');
END;

CREATE TABLE IF NOT EXISTS legal_assessment_authority (
    legal_assessment_id TEXT NOT NULL REFERENCES legal_assessment(legal_assessment_id),
    legal_authority_id TEXT NOT NULL REFERENCES legal_authority(legal_authority_id),
    association_basis TEXT NOT NULL DEFAULT 'EXPLICIT',
    PRIMARY KEY (legal_assessment_id, legal_authority_id)
);

CREATE INDEX IF NOT EXISTS idx_legal_assessment_authority_authority
    ON legal_assessment_authority(legal_authority_id);

CREATE TRIGGER IF NOT EXISTS legal_assessment_authority_no_update
BEFORE UPDATE ON legal_assessment_authority
BEGIN
    SELECT RAISE(ABORT, 'LEGAL_ASSESSMENT_AUTHORITY is append-only');
END;

CREATE TRIGGER IF NOT EXISTS legal_assessment_authority_no_delete
BEFORE DELETE ON legal_assessment_authority
BEGIN
    SELECT RAISE(ABORT, 'LEGAL_ASSESSMENT_AUTHORITY is append-only');
END;

CREATE TRIGGER IF NOT EXISTS legal_assessment_finding_no_delete
BEFORE DELETE ON legal_assessment_finding
BEGIN
    SELECT RAISE(ABORT, 'LEGAL_ASSESSMENT_FINDING is append-only');
END;

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

CREATE TRIGGER IF NOT EXISTS package_cannot_start_verified_complete
BEFORE INSERT ON package
WHEN NEW.completeness_state='VERIFIED_COMPLETE'
BEGIN
    SELECT RAISE(ABORT, 'Package completeness must pass an audited transition');
END;

CREATE TRIGGER IF NOT EXISTS package_verified_complete_transition_gate
BEFORE UPDATE OF completeness_state ON package
WHEN NEW.completeness_state='VERIFIED_COMPLETE'
 AND NEW.completeness_state IS NOT OLD.completeness_state
BEGIN
    SELECT CASE WHEN NEW.expected_level1_count <> (
        SELECT count(*) FROM source_file WHERE package_id=NEW.package_id
    ) THEN RAISE(ABORT, 'Declared Level 1 inventory must equal actual inventory') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM source_file AS sf
        WHERE sf.package_id=NEW.package_id AND NOT EXISTS (
            SELECT 1 FROM processing_run AS pr
            JOIN derivative AS d ON d.processing_run_id=pr.processing_run_id
                                AND d.source_file_id=pr.source_file_id
            WHERE pr.source_file_id=sf.source_file_id
              AND pr.completed_at IS NOT NULL AND pr.errors=''
        )
    ) THEN RAISE(ABORT, 'Every source requires acceptable terminal processing') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM processing_run AS pr
        JOIN source_file AS sf ON sf.source_file_id=pr.source_file_id
        WHERE sf.package_id=NEW.package_id AND pr.completed_at IS NULL
    ) THEN RAISE(ABORT, 'Incomplete processing attempts block VERIFIED_COMPLETE') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM review_task
        WHERE package_id=NEW.package_id
          AND (task_state='OPEN' OR (task_state='UNRESOLVED' AND material=1))
    ) THEN RAISE(ABORT, 'Open or material unresolved review blocks VERIFIED_COMPLETE') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM source_file AS sf
        WHERE sf.package_id=NEW.package_id AND (
            NOT EXISTS (
                SELECT 1 FROM occurrence AS o WHERE o.source_file_id=sf.source_file_id
            ) OR EXISTS (
                SELECT 1 FROM occurrence AS o
                WHERE o.source_file_id=sf.source_file_id
                  AND o.verification_state<>'VERIFIED'
            )
        )
    ) THEN RAISE(ABORT, 'Required source and occurrence verification is incomplete') END;

    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM audit_event AS ae
        JOIN reviewer_identity AS reviewer ON reviewer.reviewer_id=ae.change_source
        WHERE ae.entity_type='PACKAGE' AND ae.entity_id=OLD.package_id
          AND ae.field_name='completeness_state'
          AND ae.previous_value IS OLD.completeness_state
          AND ae.new_value IS NEW.completeness_state
          AND reviewer.identity_type='HUMAN'
    ) THEN RAISE(ABORT, 'Package completeness transition requires registered-human audit') END;
END;

CREATE TRIGGER IF NOT EXISTS finding_verified_promotion_gate
BEFORE UPDATE OF verification_state, verified_by ON finding
WHEN NEW.verification_state='VERIFIED' AND OLD.verification_state<>'VERIFIED'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM reviewer_identity
        WHERE reviewer_id=NEW.verified_by AND identity_type='HUMAN'
    ) OR NOT EXISTS (
        SELECT 1 FROM finding_citation AS fc
        JOIN evidence_citation AS ec ON ec.evidence_citation_id=fc.evidence_citation_id
        LEFT JOIN source_file AS direct_source ON direct_source.source_file_id=ec.source_file_id
        LEFT JOIN occurrence AS cited_occurrence ON cited_occurrence.occurrence_id=ec.occurrence_id
        LEFT JOIN source_file AS occurrence_source
          ON occurrence_source.source_file_id=cited_occurrence.source_file_id
        WHERE fc.finding_id=OLD.finding_id
          AND COALESCE(direct_source.source_file_id,occurrence_source.source_file_id) IS NOT NULL
          AND is_exact_locator(ec.locator)=1
    ) THEN RAISE(ABORT, 'VERIFIED finding requires registered human and exact source citation') END;
END;

CREATE TRIGGER IF NOT EXISTS package_completeness_requires_unused_human_audit
BEFORE UPDATE OF completeness_state ON package
WHEN NEW.completeness_state IS NOT OLD.completeness_state
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM audit_event AS ae
        JOIN reviewer_identity AS reviewer ON reviewer.reviewer_id=ae.change_source
        LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND reviewer.identity_type='HUMAN'
          AND ae.entity_type='PACKAGE' AND ae.entity_id=OLD.package_id
          AND ae.field_name='completeness_state'
          AND ae.previous_value IS OLD.completeness_state
          AND ae.new_value IS NEW.completeness_state
    ) THEN RAISE(ABORT, 'Package completeness requires an unused registered-human audit') END;
END;

CREATE TRIGGER IF NOT EXISTS package_completeness_consumes_audit
AFTER UPDATE OF completeness_state ON package
WHEN NEW.completeness_state IS NOT OLD.completeness_state
BEGIN
    INSERT INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'PACKAGE',OLD.package_id,'completeness_state'
    FROM audit_event AS ae LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
    WHERE used.event_id IS NULL AND ae.entity_type='PACKAGE' AND ae.entity_id=OLD.package_id
      AND ae.field_name='completeness_state'
      AND ae.previous_value IS OLD.completeness_state
      AND ae.new_value IS NEW.completeness_state
    ORDER BY ae.changed_at DESC,ae.event_id DESC LIMIT 1;
END;

CREATE TRIGGER IF NOT EXISTS corpus_completeness_requires_unused_human_audit
BEFORE UPDATE OF completeness_state ON corpus
WHEN NEW.completeness_state IS NOT OLD.completeness_state
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM audit_event AS ae
        JOIN reviewer_identity AS reviewer ON reviewer.reviewer_id=ae.change_source
        LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND reviewer.identity_type='HUMAN'
          AND ae.entity_type='CORPUS' AND ae.entity_id=OLD.corpus_id
          AND ae.field_name='completeness_state'
          AND ae.previous_value IS OLD.completeness_state
          AND ae.new_value IS NEW.completeness_state
    ) THEN RAISE(ABORT, 'Corpus completeness requires an unused registered-human audit') END;
END;

CREATE TRIGGER IF NOT EXISTS corpus_completeness_consumes_audit
AFTER UPDATE OF completeness_state ON corpus
WHEN NEW.completeness_state IS NOT OLD.completeness_state
BEGIN
    INSERT INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'CORPUS',OLD.corpus_id,'completeness_state'
    FROM audit_event AS ae LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
    WHERE used.event_id IS NULL AND ae.entity_type='CORPUS' AND ae.entity_id=OLD.corpus_id
      AND ae.field_name='completeness_state'
      AND ae.previous_value IS OLD.completeness_state
      AND ae.new_value IS NEW.completeness_state
    ORDER BY ae.changed_at DESC,ae.event_id DESC LIMIT 1;
END;

CREATE TRIGGER IF NOT EXISTS occurrence_verification_requires_unused_human_audit
BEFORE UPDATE OF verification_state, verified_by ON occurrence
WHEN NEW.verification_state IS NOT OLD.verification_state OR NEW.verified_by IS NOT OLD.verified_by
BEGIN
    SELECT CASE WHEN NEW.verification_state<>'VERIFIED' OR NOT EXISTS (
        SELECT 1 FROM reviewer_identity
        WHERE reviewer_id=NEW.verified_by AND identity_type='HUMAN'
    ) OR NOT EXISTS (
        SELECT 1 FROM audit_event AS ae LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND ae.entity_type='OCCURRENCE'
          AND ae.entity_id=OLD.occurrence_id AND ae.field_name='verification_state'
          AND ae.previous_value IS OLD.verification_state AND ae.new_value IS NEW.verification_state
    ) OR NOT EXISTS (
        SELECT 1 FROM audit_event AS ae LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND ae.entity_type='OCCURRENCE'
          AND ae.entity_id=OLD.occurrence_id AND ae.field_name='verified_by'
          AND ae.previous_value IS OLD.verified_by AND ae.new_value IS NEW.verified_by
    ) THEN RAISE(ABORT, 'Occurrence verification requires audited registered-human promotion') END;
END;

CREATE TRIGGER IF NOT EXISTS occurrence_verification_consumes_audits
AFTER UPDATE OF verification_state, verified_by ON occurrence
WHEN NEW.verification_state IS NOT OLD.verification_state OR NEW.verified_by IS NOT OLD.verified_by
BEGIN
    INSERT OR IGNORE INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'OCCURRENCE',OLD.occurrence_id,ae.field_name
    FROM audit_event AS ae LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
    WHERE used.event_id IS NULL AND ae.entity_type='OCCURRENCE' AND ae.entity_id=OLD.occurrence_id
      AND ((ae.field_name='verification_state' AND ae.previous_value IS OLD.verification_state
            AND ae.new_value IS NEW.verification_state)
        OR (ae.field_name='verified_by' AND ae.previous_value IS OLD.verified_by
            AND ae.new_value IS NEW.verified_by));
END;

CREATE TRIGGER IF NOT EXISTS metro_statement_verified_reviewer_insert
BEFORE INSERT ON metro_statement
WHEN NEW.verification_state='VERIFIED' AND NOT EXISTS (
    SELECT 1 FROM reviewer_identity
    WHERE reviewer_id=NEW.verified_by AND identity_type='HUMAN'
)
BEGIN
    SELECT RAISE(ABORT, 'VERIFIED statement requires a registered HUMAN reviewer');
END;

CREATE TRIGGER IF NOT EXISTS metro_statement_verification_requires_unused_human_audit
BEFORE UPDATE OF verification_state, verified_by ON metro_statement
WHEN NEW.verification_state IS NOT OLD.verification_state OR NEW.verified_by IS NOT OLD.verified_by
BEGIN
    SELECT CASE WHEN NEW.verification_state<>'VERIFIED' OR NOT EXISTS (
        SELECT 1 FROM reviewer_identity
        WHERE reviewer_id=NEW.verified_by AND identity_type='HUMAN'
    ) OR NOT EXISTS (
        SELECT 1 FROM audit_event AS ae LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND ae.entity_type='METRO_STATEMENT'
          AND ae.entity_id=OLD.metro_statement_id AND ae.field_name='verification_state'
          AND ae.previous_value IS OLD.verification_state AND ae.new_value IS NEW.verification_state
    ) OR NOT EXISTS (
        SELECT 1 FROM audit_event AS ae LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND ae.entity_type='METRO_STATEMENT'
          AND ae.entity_id=OLD.metro_statement_id AND ae.field_name='verified_by'
          AND ae.previous_value IS OLD.verified_by AND ae.new_value IS NEW.verified_by
    ) THEN RAISE(ABORT, 'Statement verification requires audited registered-human promotion') END;
END;

CREATE TRIGGER IF NOT EXISTS metro_statement_verification_consumes_audits
AFTER UPDATE OF verification_state, verified_by ON metro_statement
WHEN NEW.verification_state IS NOT OLD.verification_state OR NEW.verified_by IS NOT OLD.verified_by
BEGIN
    INSERT OR IGNORE INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'METRO_STATEMENT',OLD.metro_statement_id,ae.field_name
    FROM audit_event AS ae LEFT JOIN audit_event_use AS used ON used.event_id=ae.event_id
    WHERE used.event_id IS NULL AND ae.entity_type='METRO_STATEMENT'
      AND ae.entity_id=OLD.metro_statement_id
      AND ((ae.field_name='verification_state' AND ae.previous_value IS OLD.verification_state
            AND ae.new_value IS NEW.verification_state)
        OR (ae.field_name='verified_by' AND ae.previous_value IS OLD.verified_by
            AND ae.new_value IS NEW.verified_by));
END;

CREATE TRIGGER IF NOT EXISTS record_reference_transition_requires_unused_human_audit
BEFORE UPDATE OF match_state,matched_record_id,resolved_record_id,absence_scope,
                 search_corpus_id,verification_state,verified_by ON record_reference
BEGIN
    SELECT CASE WHEN NEW.match_state IS NOT OLD.match_state AND NOT EXISTS (
        SELECT 1 FROM audit_event ae JOIN reviewer_identity reviewer
          ON reviewer.reviewer_id=ae.change_source
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND reviewer.identity_type='HUMAN'
          AND ae.entity_type='RECORD_REFERENCE' AND ae.entity_id=OLD.record_reference_id
          AND ae.field_name='match_state' AND ae.previous_value IS OLD.match_state
          AND ae.new_value IS NEW.match_state
    ) THEN RAISE(ABORT,'Reference match transition requires unused human audit') END;
    SELECT CASE WHEN NEW.matched_record_id IS NOT OLD.matched_record_id AND NOT EXISTS (
        SELECT 1 FROM audit_event ae JOIN reviewer_identity reviewer
          ON reviewer.reviewer_id=ae.change_source
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND reviewer.identity_type='HUMAN'
          AND ae.entity_type='RECORD_REFERENCE' AND ae.entity_id=OLD.record_reference_id
          AND ae.field_name='matched_record_id' AND ae.previous_value IS OLD.matched_record_id
          AND ae.new_value IS NEW.matched_record_id
    ) THEN RAISE(ABORT,'Reference candidate transition requires unused human audit') END;
    SELECT CASE WHEN NEW.resolved_record_id IS NOT OLD.resolved_record_id AND NOT EXISTS (
        SELECT 1 FROM audit_event ae JOIN reviewer_identity reviewer
          ON reviewer.reviewer_id=ae.change_source
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND reviewer.identity_type='HUMAN'
          AND ae.entity_type='RECORD_REFERENCE' AND ae.entity_id=OLD.record_reference_id
          AND ae.field_name='resolved_record_id' AND ae.previous_value IS OLD.resolved_record_id
          AND ae.new_value IS NEW.resolved_record_id
    ) THEN RAISE(ABORT,'Reference resolution transition requires unused human audit') END;
    SELECT CASE WHEN NEW.absence_scope IS NOT OLD.absence_scope AND NOT EXISTS (
        SELECT 1 FROM audit_event ae JOIN reviewer_identity reviewer
          ON reviewer.reviewer_id=ae.change_source
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND reviewer.identity_type='HUMAN'
          AND ae.entity_type='RECORD_REFERENCE' AND ae.entity_id=OLD.record_reference_id
          AND ae.field_name='absence_scope' AND ae.previous_value IS OLD.absence_scope
          AND ae.new_value IS NEW.absence_scope
    ) THEN RAISE(ABORT,'Reference absence transition requires unused human audit') END;
    SELECT CASE WHEN NEW.search_corpus_id IS NOT OLD.search_corpus_id AND NOT EXISTS (
        SELECT 1 FROM audit_event ae JOIN reviewer_identity reviewer
          ON reviewer.reviewer_id=ae.change_source
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND reviewer.identity_type='HUMAN'
          AND ae.entity_type='RECORD_REFERENCE' AND ae.entity_id=OLD.record_reference_id
          AND ae.field_name='search_corpus_id' AND ae.previous_value IS OLD.search_corpus_id
          AND ae.new_value IS NEW.search_corpus_id
    ) THEN RAISE(ABORT,'Reference search scope transition requires unused human audit') END;
    SELECT CASE WHEN NEW.verification_state IS NOT OLD.verification_state AND NOT EXISTS (
        SELECT 1 FROM audit_event ae JOIN reviewer_identity reviewer
          ON reviewer.reviewer_id=ae.change_source
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND reviewer.identity_type='HUMAN'
          AND ae.entity_type='RECORD_REFERENCE' AND ae.entity_id=OLD.record_reference_id
          AND ae.field_name='verification_state' AND ae.previous_value IS OLD.verification_state
          AND ae.new_value IS NEW.verification_state
    ) THEN RAISE(ABORT,'Reference verification transition requires unused human audit') END;
    SELECT CASE WHEN NEW.verified_by IS NOT OLD.verified_by AND NOT EXISTS (
        SELECT 1 FROM audit_event ae JOIN reviewer_identity reviewer
          ON reviewer.reviewer_id=ae.change_source
        LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
        WHERE used.event_id IS NULL AND reviewer.identity_type='HUMAN'
          AND ae.entity_type='RECORD_REFERENCE' AND ae.entity_id=OLD.record_reference_id
          AND ae.field_name='verified_by' AND ae.previous_value IS OLD.verified_by
          AND ae.new_value IS NEW.verified_by
    ) THEN RAISE(ABORT,'Reference verifier transition requires unused human audit') END;
END;

CREATE TRIGGER IF NOT EXISTS record_reference_transition_consumes_audits
AFTER UPDATE OF match_state,matched_record_id,resolved_record_id,absence_scope,
                search_corpus_id,verification_state,verified_by ON record_reference
BEGIN
    INSERT OR IGNORE INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'RECORD_REFERENCE',OLD.record_reference_id,ae.field_name
    FROM audit_event ae LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
    WHERE used.event_id IS NULL AND ae.entity_type='RECORD_REFERENCE'
      AND ae.entity_id=OLD.record_reference_id AND (
        (ae.field_name='match_state' AND ae.previous_value IS OLD.match_state AND ae.new_value IS NEW.match_state)
        OR (ae.field_name='matched_record_id' AND ae.previous_value IS OLD.matched_record_id AND ae.new_value IS NEW.matched_record_id)
        OR (ae.field_name='resolved_record_id' AND ae.previous_value IS OLD.resolved_record_id AND ae.new_value IS NEW.resolved_record_id)
        OR (ae.field_name='absence_scope' AND ae.previous_value IS OLD.absence_scope AND ae.new_value IS NEW.absence_scope)
        OR (ae.field_name='search_corpus_id' AND ae.previous_value IS OLD.search_corpus_id AND ae.new_value IS NEW.search_corpus_id)
        OR (ae.field_name='verification_state' AND ae.previous_value IS OLD.verification_state AND ae.new_value IS NEW.verification_state)
        OR (ae.field_name='verified_by' AND ae.previous_value IS OLD.verified_by AND ae.new_value IS NEW.verified_by)
      );
END;

CREATE TRIGGER IF NOT EXISTS review_task_transition_requires_unused_human_audit
BEFORE UPDATE OF task_state,reviewer,resolved_at,resolution,supporting_citation_id,concern ON review_task
BEGIN
    SELECT CASE WHEN NEW.task_state<>'OPEN' AND NOT EXISTS (
        SELECT 1 FROM reviewer_identity
        WHERE reviewer_id=NEW.reviewer AND identity_type='HUMAN'
    ) THEN RAISE(ABORT,'Review transition requires registered human') END;
    SELECT CASE WHEN NEW.task_state='RESOLVED' AND (
        NEW.supporting_citation_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM evidence_citation ec
            WHERE ec.evidence_citation_id=NEW.supporting_citation_id
              AND is_exact_locator(ec.locator)=1
        )
    ) THEN RAISE(ABORT,'Resolved review requires exact source citation') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM (
            SELECT 'task_state' AS field_name,OLD.task_state AS old_value,NEW.task_state AS new_value
            UNION ALL SELECT 'reviewer',OLD.reviewer,NEW.reviewer
            UNION ALL SELECT 'resolved_at',OLD.resolved_at,NEW.resolved_at
            UNION ALL SELECT 'resolution',OLD.resolution,NEW.resolution
            UNION ALL SELECT 'supporting_citation_id',OLD.supporting_citation_id,NEW.supporting_citation_id
            UNION ALL SELECT 'concern',OLD.concern,NEW.concern
        ) change
        WHERE change.old_value IS NOT change.new_value AND NOT EXISTS (
            SELECT 1 FROM audit_event ae JOIN reviewer_identity identity
              ON identity.reviewer_id=ae.change_source
            LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
            WHERE used.event_id IS NULL AND identity.identity_type='HUMAN'
              AND ae.entity_type='REVIEW_TASK' AND ae.entity_id=OLD.review_task_id
              AND ae.field_name=change.field_name
              AND ae.previous_value IS change.old_value AND ae.new_value IS change.new_value
        )
    ) THEN RAISE(ABORT,'Review transition requires unused human audits') END;
END;

CREATE TRIGGER IF NOT EXISTS review_task_transition_consumes_audits
AFTER UPDATE OF task_state,reviewer,resolved_at,resolution,supporting_citation_id,concern ON review_task
BEGIN
    INSERT OR IGNORE INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'REVIEW_TASK',OLD.review_task_id,ae.field_name
    FROM audit_event ae LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
    WHERE used.event_id IS NULL AND ae.entity_type='REVIEW_TASK' AND ae.entity_id=OLD.review_task_id
      AND ((ae.field_name='task_state' AND ae.previous_value IS OLD.task_state AND ae.new_value IS NEW.task_state)
        OR (ae.field_name='reviewer' AND ae.previous_value IS OLD.reviewer AND ae.new_value IS NEW.reviewer)
        OR (ae.field_name='resolved_at' AND ae.previous_value IS OLD.resolved_at AND ae.new_value IS NEW.resolved_at)
        OR (ae.field_name='resolution' AND ae.previous_value IS OLD.resolution AND ae.new_value IS NEW.resolution)
        OR (ae.field_name='supporting_citation_id' AND ae.previous_value IS OLD.supporting_citation_id AND ae.new_value IS NEW.supporting_citation_id)
        OR (ae.field_name='concern' AND ae.previous_value IS OLD.concern AND ae.new_value IS NEW.concern));
END;

CREATE TRIGGER IF NOT EXISTS record_descriptive_change_requires_unused_human_audit
BEFORE UPDATE OF title_or_description,record_type,version_family_key,notes ON record
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM (
            SELECT 'title_or_description' AS field_name,OLD.title_or_description AS old_value,NEW.title_or_description AS new_value
            UNION ALL SELECT 'record_type',OLD.record_type,NEW.record_type
            UNION ALL SELECT 'version_family_key',OLD.version_family_key,NEW.version_family_key
            UNION ALL SELECT 'notes',OLD.notes,NEW.notes
        ) change
        WHERE change.old_value IS NOT change.new_value AND NOT EXISTS (
            SELECT 1 FROM audit_event ae JOIN reviewer_identity identity
              ON identity.reviewer_id=ae.change_source
            LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
            WHERE used.event_id IS NULL AND identity.identity_type='HUMAN'
              AND ae.entity_type='RECORD' AND ae.entity_id=OLD.record_id
              AND ae.field_name=change.field_name
              AND ae.previous_value IS change.old_value AND ae.new_value IS change.new_value
        )
    ) THEN RAISE(ABORT,'Record description change requires unused human audit') END;
END;

CREATE TRIGGER IF NOT EXISTS record_descriptive_change_consumes_audit
AFTER UPDATE OF title_or_description,record_type,version_family_key,notes ON record
BEGIN
    INSERT OR IGNORE INTO audit_event_use(event_id,entity_type,entity_id,field_name)
    SELECT ae.event_id,'RECORD',OLD.record_id,ae.field_name
    FROM audit_event ae LEFT JOIN audit_event_use used ON used.event_id=ae.event_id
    WHERE used.event_id IS NULL AND ae.entity_type='RECORD' AND ae.entity_id=OLD.record_id
      AND ((ae.field_name='title_or_description' AND ae.previous_value IS OLD.title_or_description AND ae.new_value IS NEW.title_or_description)
        OR (ae.field_name='record_type' AND ae.previous_value IS OLD.record_type AND ae.new_value IS NEW.record_type)
        OR (ae.field_name='version_family_key' AND ae.previous_value IS OLD.version_family_key AND ae.new_value IS NEW.version_family_key)
        OR (ae.field_name='notes' AND ae.previous_value IS OLD.notes AND ae.new_value IS NEW.notes));
END;

CREATE TRIGGER IF NOT EXISTS legal_assessment_cannot_start_final
BEFORE INSERT ON legal_assessment
WHEN NEW.assessment_status='FINAL'
BEGIN
    SELECT RAISE(ABORT, 'Legal assessments must be finalized by audited transition');
END;

CREATE TRIGGER IF NOT EXISTS legal_assessment_final_gate
BEFORE UPDATE OF assessment_status ON legal_assessment
WHEN NEW.assessment_status='FINAL' AND NEW.assessment_status IS NOT OLD.assessment_status
BEGIN
    SELECT CASE WHEN trim(NEW.conclusion)=''
        OR NEW.finalized_by IS NULL
        OR NEW.finalized_at IS NULL
        OR NOT EXISTS (
            SELECT 1 FROM reviewer_identity
            WHERE reviewer_id=NEW.finalized_by AND identity_type='HUMAN'
        )
        OR NOT EXISTS (
            SELECT 1 FROM legal_assessment_finding AS laf
            JOIN finding AS f ON f.finding_id=laf.finding_id
            WHERE laf.legal_assessment_id=OLD.legal_assessment_id
              AND f.verification_state='VERIFIED'
              AND EXISTS (
                  SELECT 1 FROM finding_citation AS fc
                  JOIN evidence_citation AS ec
                    ON ec.evidence_citation_id=fc.evidence_citation_id
                  WHERE fc.finding_id=f.finding_id AND is_exact_locator(ec.locator)=1
              )
        )
        OR EXISTS (
            SELECT 1 FROM legal_assessment_finding AS laf
            JOIN finding AS f ON f.finding_id=laf.finding_id
            WHERE laf.legal_assessment_id=OLD.legal_assessment_id
              AND f.verification_state<>'VERIFIED'
        )
        OR NOT EXISTS (
            SELECT 1 FROM legal_assessment_authority
            WHERE legal_assessment_id=OLD.legal_assessment_id
        )
        THEN RAISE(ABORT, 'FINAL legal assessment requires human, cited verified finding, and authority') END;
END;

CREATE TRIGGER IF NOT EXISTS legal_assessment_finalizer_is_immutable
BEFORE UPDATE OF finalized_by, finalized_at ON legal_assessment
WHEN OLD.assessment_status='FINAL'
 AND (NEW.finalized_by IS NOT OLD.finalized_by OR NEW.finalized_at IS NOT OLD.finalized_at)
BEGIN
    SELECT RAISE(ABORT, 'Legal finalizer provenance is immutable');
END;

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
    ('REVIEWER_IDENTITY_TYPE', 'HUMAN', 'Human', 'A registered person authorized to review evidentiary transitions.'),
    ('REVIEWER_IDENTITY_TYPE', 'AUTOMATION', 'Automation', 'A non-human process that cannot perform human review or verification.'),
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
