DROP VIEW IF EXISTS v_package_inventory;
CREATE VIEW v_package_inventory AS
SELECT
    p.package_id,
    p.control_record_path,
    p.production_archive_path,
    p.package_status,
    p.expected_level1_count,
    COUNT(DISTINCT sf.source_file_id) AS level1_source_file_count,
    p.completeness_state AS package_completeness_state,
    p.notes AS package_notes
FROM package AS p
LEFT JOIN source_file AS sf ON sf.package_id = p.package_id
GROUP BY p.package_id;

DROP VIEW IF EXISTS v_request_element_crosswalk;
CREATE VIEW v_request_element_crosswalk AS
SELECT
    re.request_element_id,
    re.package_id,
    re.parent_request_element_id,
    re.sort_order,
    re.requested_language,
    re.completeness_state AS request_element_completeness_state,
    (SELECT COUNT(*) FROM statement_request_element AS sre
     WHERE sre.request_element_id = re.request_element_id) AS metro_statement_count,
    (SELECT COUNT(*) FROM request_element_evidence AS ree
     WHERE ree.request_element_id = re.request_element_id
       AND ree.evidentiary_role = 'RESPONSIVE') AS responsive_occurrence_count,
    (SELECT COUNT(DISTINCT o.record_id)
     FROM request_element_evidence AS ree
     JOIN occurrence AS o ON o.occurrence_id = ree.occurrence_id
     WHERE ree.request_element_id = re.request_element_id
       AND ree.evidentiary_role = 'RESPONSIVE') AS responsive_record_count,
    (SELECT COUNT(*) FROM request_element_evidence AS ree
     WHERE ree.request_element_id = re.request_element_id
       AND ree.evidentiary_role = 'SUBSTITUTE') AS substitute_occurrence_count,
    (SELECT COUNT(DISTINCT o.record_id)
     FROM request_element_evidence AS ree
     JOIN occurrence AS o ON o.occurrence_id = ree.occurrence_id
     WHERE ree.request_element_id = re.request_element_id
       AND ree.evidentiary_role = 'SUBSTITUTE') AS substitute_record_count,
    (SELECT COUNT(*) FROM finding AS f
     WHERE f.request_element_id = re.request_element_id) AS finding_count
    ,(SELECT group_concat(finding_type, '|')
      FROM (
          SELECT DISTINCT f.finding_type
          FROM finding AS f
          WHERE f.request_element_id = re.request_element_id
          ORDER BY f.finding_type
      )) AS cumulative_finding_types
FROM request_element AS re;

DROP VIEW IF EXISTS v_level2_records;
CREATE VIEW v_level2_records AS
SELECT
    r.record_id,
    r.title_or_description,
    r.record_type,
    r.content_fingerprint,
    r.canonical_identity_basis,
    r.version_family_key,
    r.notes AS record_notes,
    COUNT(o.occurrence_id) AS occurrence_count
FROM record AS r
LEFT JOIN occurrence AS o ON o.record_id = r.record_id
GROUP BY r.record_id;

DROP VIEW IF EXISTS v_occurrences;
CREATE VIEW v_occurrences AS
SELECT
    o.occurrence_id,
    o.record_id,
    r.title_or_description AS record_title_or_description,
    o.source_file_id,
    sf.package_id,
    sf.archive_member_path,
    o.derivative_id,
    o.source_locator,
    o.verification_state AS occurrence_verification_state,
    o.verified_by,
    o.notes AS occurrence_notes
FROM occurrence AS o
JOIN record AS r ON r.record_id = o.record_id
JOIN source_file AS sf ON sf.source_file_id = o.source_file_id;

DROP VIEW IF EXISTS v_referenced_not_located;
CREATE VIEW v_referenced_not_located AS
SELECT
    rr.record_reference_id,
    rr.reference_id,
    sf.package_id,
    rr.occurrence_id AS source_occurrence_id,
    rr.source_locator,
    rr.relationship_type,
    rr.referenced_description,
    rr.match_state,
    rr.absence_scope,
    rr.search_corpus_id,
    rr.verification_state AS reference_verification_state,
    rr.verified_by,
    rr.notes AS reference_notes
FROM record_reference AS rr
JOIN occurrence AS o ON o.occurrence_id = rr.occurrence_id
JOIN source_file AS sf ON sf.source_file_id = o.source_file_id
WHERE rr.match_state <> 'CONFIRMED_MATCH';

DROP VIEW IF EXISTS v_existence_conflicts;
CREATE VIEW v_existence_conflicts AS
SELECT
    f.finding_id,
    f.package_id,
    f.request_element_id,
    f.record_id,
    f.record_reference_id,
    f.finding_type,
    f.proposition,
    f.verification_state AS finding_verification_state,
    f.verified_by,
    f.created_at
FROM finding AS f
WHERE f.finding_type IN (
    'DIRECT_CONTRADICTION',
    'STRONG_EXISTENCE_EVIDENCE',
    'POSSIBLE_EXISTENCE_EVIDENCE'
);

DROP VIEW IF EXISTS v_withholding_redaction;
CREATE VIEW v_withholding_redaction AS
SELECT
    f.finding_id AS withholding_item_id,
    'FINDING' AS withholding_item_type,
    f.package_id,
    f.request_element_id,
    f.finding_type AS classification,
    f.proposition AS description,
    f.verification_state,
    f.verified_by,
    f.created_at
FROM finding AS f
WHERE f.finding_type IN (
    'PRODUCED_PARTIAL_REDACTED',
    'WITHHELD_WHOLE_OR_PART',
    'WITHHOLDING_BASIS_STATED',
    'NO_WITHHOLDING_BASIS_STATED'
)
UNION ALL
SELECT
    ms.metro_statement_id AS withholding_item_id,
    'METRO_STATEMENT' AS withholding_item_type,
    ms.package_id,
    NULL AS request_element_id,
    ms.statement_type AS classification,
    ms.statement_text AS description,
    ms.verification_state,
    ms.verified_by,
    NULL AS created_at
FROM metro_statement AS ms
WHERE ms.statement_type = 'WITHHOLDING_BASIS';

DROP VIEW IF EXISTS v_review_queue;
CREATE VIEW v_review_queue AS
SELECT
    rt.review_task_id,
    rt.package_id,
    rt.request_element_id,
    rt.source_file_id,
    rt.occurrence_id,
    rt.record_reference_id,
    rt.finding_id,
    rt.task_type,
    rt.reason_code,
    rt.task_state,
    rt.material,
    rt.concern,
    rt.reviewer,
    rt.resolved_at,
    rt.resolution,
    rt.supporting_citation_id
FROM review_task AS rt;

DROP VIEW IF EXISTS v_audit_history;
CREATE VIEW v_audit_history AS
SELECT
    ae.event_id,
    ae.entity_type,
    ae.entity_id,
    ae.field_name,
    ae.previous_value,
    ae.new_value,
    ae.changed_at,
    ae.reason,
    ae.change_source,
    ae.supporting_citation_id
FROM audit_event AS ae;

DROP VIEW IF EXISTS v_legal_assessments;
CREATE VIEW v_legal_assessments AS
SELECT
    la.legal_assessment_id,
    la.legal_question,
    la.conclusion,
    la.assessment_status,
    la.uncertainty,
    la.notes AS assessment_notes,
    (SELECT COUNT(*) FROM legal_assessment_finding AS laf
     WHERE laf.legal_assessment_id = la.legal_assessment_id) AS linked_finding_count,
    (SELECT COUNT(*)
     FROM legal_assessment_finding AS laf
     JOIN finding AS f ON f.finding_id = laf.finding_id
     WHERE laf.legal_assessment_id = la.legal_assessment_id
       AND f.verification_state = 'VERIFIED') AS verified_linked_finding_count,
    (SELECT COUNT(*) FROM legal_assessment_authority AS laa
     WHERE laa.legal_assessment_id = la.legal_assessment_id) AS cited_authority_count,
    COALESCE((
        SELECT group_concat(
            legal_authority_id || ' | ' || authority_type || ' | ' || citation,
            ' || '
        )
        FROM (
            SELECT legal_authority_id, authority_type, citation
            FROM legal_assessment_authority AS laa
            JOIN legal_authority USING(legal_authority_id)
            WHERE laa.legal_assessment_id = la.legal_assessment_id
            ORDER BY legal_authority_id
        )
    ), '') AS cited_authorities
FROM legal_assessment AS la
WHERE EXISTS (
    SELECT 1
    FROM legal_assessment_finding AS laf
    WHERE laf.legal_assessment_id = la.legal_assessment_id
)
AND NOT EXISTS (
    SELECT 1
    FROM legal_assessment_finding AS laf
    JOIN finding AS f ON f.finding_id = laf.finding_id
    WHERE laf.legal_assessment_id = la.legal_assessment_id
      AND f.verification_state <> 'VERIFIED'
);

DROP VIEW IF EXISTS v_corpus_summary_counts;
CREATE VIEW v_corpus_summary_counts AS
SELECT
    c.corpus_id,
    (SELECT COUNT(DISTINCT sf.source_file_id)
     FROM corpus_package AS cp
     JOIN source_file AS sf ON sf.package_id = cp.package_id
     WHERE cp.corpus_id = c.corpus_id) AS level1_source_files,
    (SELECT COUNT(DISTINCT o.record_id)
     FROM corpus_package AS cp
     JOIN source_file AS sf ON sf.package_id = cp.package_id
     JOIN occurrence AS o ON o.source_file_id = sf.source_file_id
     WHERE cp.corpus_id = c.corpus_id) AS unique_level2_records,
    (SELECT COUNT(*)
     FROM corpus_package AS cp
     JOIN source_file AS sf ON sf.package_id = cp.package_id
     JOIN occurrence AS o ON o.source_file_id = sf.source_file_id
     WHERE cp.corpus_id = c.corpus_id) AS level2_occurrences,
    (SELECT COUNT(*)
     FROM corpus_package AS cp
     JOIN source_file AS sf ON sf.package_id = cp.package_id
     JOIN occurrence AS o ON o.source_file_id = sf.source_file_id
     JOIN record_reference AS rr ON rr.occurrence_id = o.occurrence_id
     WHERE cp.corpus_id = c.corpus_id) AS record_references,
    (SELECT COUNT(*)
     FROM corpus_package AS cp
     JOIN source_file AS sf ON sf.package_id = cp.package_id
     JOIN occurrence AS o ON o.source_file_id = sf.source_file_id
     JOIN record_reference AS rr ON rr.occurrence_id = o.occurrence_id
     WHERE cp.corpus_id = c.corpus_id
       AND rr.match_state <> 'CONFIRMED_MATCH') AS referenced_not_located_items,
    (SELECT COUNT(*)
     FROM corpus_package AS cp
     JOIN finding AS f ON f.package_id = cp.package_id
     WHERE cp.corpus_id = c.corpus_id
       AND f.verification_state = 'PROVISIONAL') AS provisional_findings,
    (SELECT COUNT(*)
     FROM corpus_package AS cp
     JOIN finding AS f ON f.package_id = cp.package_id
     WHERE cp.corpus_id = c.corpus_id
       AND f.verification_state = 'VERIFIED') AS verified_findings,
    (SELECT COUNT(*)
     FROM corpus_package AS cp
     JOIN review_task AS rt ON rt.package_id = cp.package_id
     WHERE cp.corpus_id = c.corpus_id
       AND rt.task_state = 'OPEN') AS open_review_tasks,
    (SELECT COUNT(*)
     FROM corpus_package AS cp
     JOIN review_task AS rt ON rt.package_id = cp.package_id
     WHERE cp.corpus_id = c.corpus_id
       AND rt.task_state = 'UNRESOLVED') AS unresolved_review_tasks,
    c.completeness_state AS corpus_completeness_state
FROM corpus AS c;

DROP VIEW IF EXISTS v_summary_counts;
CREATE VIEW v_summary_counts AS
SELECT
    (SELECT COUNT(*) FROM package) AS package_count,
    (SELECT COUNT(*) FROM source_file) AS level1_source_files,
    (SELECT COUNT(*) FROM record) AS unique_level2_records,
    (SELECT COUNT(*) FROM occurrence) AS level2_occurrences,
    (SELECT COUNT(*) FROM record_reference) AS record_references,
    (SELECT COUNT(*) FROM v_referenced_not_located) AS referenced_not_located_items,
    (SELECT COUNT(*) FROM finding WHERE verification_state = 'PROVISIONAL') AS provisional_findings,
    (SELECT COUNT(*) FROM finding WHERE verification_state = 'VERIFIED') AS verified_findings,
    (SELECT COUNT(*) FROM review_task WHERE task_state = 'OPEN') AS open_review_tasks,
    (SELECT COUNT(*) FROM review_task WHERE task_state = 'UNRESOLVED') AS unresolved_review_tasks,
    (SELECT COUNT(*) FROM package WHERE completeness_state = 'IN_PROGRESS') AS in_progress_packages,
    (SELECT COUNT(*) FROM package WHERE completeness_state = 'REVIEW_REQUIRED') AS review_required_packages,
    (SELECT COUNT(*) FROM package WHERE completeness_state = 'COMPLETE_WITH_EXCEPTIONS') AS complete_with_exceptions_packages,
    (SELECT COUNT(*) FROM package WHERE completeness_state = 'VERIFIED_COMPLETE') AS verified_complete_packages;
