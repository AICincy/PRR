# Metro Public-Records Forensic Processing SOP and Design Specification

**Status:** Approved design  
**Date:** 2026-08-07  
**Scope:** Metro/SORTA public-records request productions in the current forensic corpus  
**Design authority:** Decisions 1–33 recorded in `handoff.md`; user-approved 2026-08-07

## 1. Purpose

This specification defines how Metro public-records requests, Metro's responses, produced files, embedded records, referenced-but-unproduced records, and later analytical findings are to be preserved, normalized, cross-referenced, verified, and reported.

The system must make it possible to answer, reproducibly and with source-level citations:

1. What exactly was requested?
2. What did Metro actually say about each request element?
3. What files did Metro actually produce in response to that request?
4. What independently identifiable records occur inside those files?
5. Did Metro substitute different material for a requested record?
6. Do produced records identify or reference records Metro asserted did not exist?
7. Was a referenced record absent from the responsive package, found elsewhere in the corpus, or not found after a complete corpus search?
8. What facts are verified, what remains provisional, and what still requires review?
9. What legal conclusions, if any, follow from the verified facts and cited legal authority?

The design prioritizes evidentiary integrity over apparent document volume. Metro's original files remain authoritative evidence. The SQLite ledger is the canonical analytical system of record. Human-readable exports are derived views.

## 2. Current package map

The current corpus begins with three package-control records:

| Package control record | Associated production | Level 1 files received | Package status |
|---|---|---:|---|
| `1.pdf` | `26-145_2026-08-07 11_33_06 -0400.zip` | 24 | Production received; pairing user-confirmed |
| `2.pdf` | `Metro PRR Now.zip` | 72 | Production received; pairing user-confirmed |
| `3.pdf` | none | 0 | `NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED` |

Package-control records establish request/response context but do **not** increase Level 1 production counts.

The status of `3.pdf` means only that no associated output/production response was received. It must not be silently restated as an express denial, a nonexistence assertion, or an omission within an otherwise responsive production unless separate evidence establishes that fact.

## 3. Non-negotiable evidentiary principles

### 3.1 Two levels of documentary identity

- **Level 1 source file:** one file exactly as Metro produced it.
- **Level 2 record:** one independently identifiable documentary record, whether it occupies all of a Level 1 file or only part of one.

A Level 2 record exists when the content has an independently recognizable documentary identity, such as its own title, resolution number, date, author, subject, or evident purpose. Pagination, covers, blanks, dividers, or tables of contents do not alone create separate Level 2 records.

Every appearance of a Level 2 record is an `OCCURRENCE`. Byte/content-identical instances may resolve to one canonical `RECORD`, while every occurrence remains preserved. Materially different versions remain separate, linked records.

### 3.2 Source integrity and immutability

Metro originals are immutable evidence. For each Level 1 source, preserve at minimum:

- original filename;
- package association;
- byte size;
- SHA-256;
- media/file type;
- page, sheet, or equivalent structural count; and
- any unreadable, corrupt, or processing-relevant condition.

OCR, splitting, normalization, renaming, deduplication, and analysis occur only on traceable derivatives. A derivative never replaces or becomes more authoritative than the original.

### 3.3 Stable identity chain

The persistent provenance chain is:

`PACKAGE_ID -> SOURCE_FILE_ID -> RECORD_ID -> OCCURRENCE_ID`

IDs and original-source provenance do not change when descriptive metadata is corrected. Corrections to substantive analytical state are recorded through append-only audit history.

### 3.4 Package scope before corpus scope

All production facts and counts are package-scoped first. Corpus-wide views are secondary aggregations. A record found in another Metro production does not retroactively count as having been produced in response to the original request.

## 4. Canonical architecture

### 4.1 System of record

Use a hybrid forensic architecture:

- immutable original Metro files remain the documentary evidence;
- one SQLite database is the canonical structured analytical ledger;
- OCR/extraction/normalization artifacts are traceable derivatives;
- CSV and Markdown outputs are regenerated views/reports from SQLite, never independently edited competing ledgers.

### 4.2 Core entity model

The canonical relational spine is:

`PACKAGE -> REQUEST_ELEMENT / SOURCE_FILE -> RECORD -> OCCURRENCE`

with a many-to-many `REQUEST_ELEMENT_EVIDENCE` bridge.

The same record may be responsive to one request element, substitute material for another, and contradiction/existence evidence for a third. The schema must permit all of those roles simultaneously.

### 4.3 Required first-class entities

The implementation must provide the following logical entities. Exact SQL column types and indexes may be chosen during implementation, but the listed information and relationships are required.

#### `PACKAGE`

Represents one request/response production unit.

Minimum information:

- stable package ID;
- package-control record reference;
- production/archive association, if any;
- package status;
- request and response date facts when documented;
- package-level completeness state; and
- notes that do not substitute for controlled status fields.

#### `REQUEST_ELEMENT`

Represents one independently evaluated request item or sub-item.

Minimum information:

- stable request-element ID;
- parent package ID;
- exact or faithfully normalized requested language;
- ordering/parent-child information where a request contains nested sub-items; and
- current scope/completeness metadata.

Each request element is evaluated independently through:

`REQUEST_ELEMENT -> METRO_STATEMENT -> ACTUAL_PRODUCTION -> CONTRADICTION/EXISTENCE_EVIDENCE -> CUMULATIVE_FULFILLMENT_FINDINGS`

The final term is a derived summary of cumulative findings, not a single mutually exclusive status that can overwrite another applicable classification.

#### `SOURCE_FILE`

Represents one immutable Level 1 file exactly as Metro produced it.

Minimum information is the provenance set in §3.2, permanently linked to one package.

#### `RECORD`

Represents one canonical unique Level 2 documentary identity.

Minimum information:

- stable record ID;
- title/description;
- controlled record type when known;
- documented date facts when known;
- canonical identity/deduplication basis; and
- version-family relationship when materially different versions exist.

#### `OCCURRENCE`

Represents one appearance of a Level 2 record in a Level 1 source file.

Minimum information:

- stable occurrence ID;
- record ID;
- source-file ID;
- derivative ID when a derivative was used to identify/extract it;
- exact page, sheet, cell/range, or equivalent location;
- extraction/identification verification state; and
- occurrence-specific notes.

#### `REQUEST_ELEMENT_EVIDENCE`

Many-to-many bridge between request elements and relevant record occurrences/evidence.

It must preserve the evidentiary role of the relationship rather than merely asserting that two entities are related.

#### `METRO_STATEMENT`

Preserves Metro's actual response language separately from analyst findings.

Minimum information:

- stable statement ID;
- exact or verbatim-preserved statement text subject to normal citation-length handling in reports;
- controlled statement type;
- exact source location/citation;
- linked request element(s); and
- verification state.

Typical statement types include `NONEXISTENCE_ASSERTION`, `DENIAL`, and `WITHHOLDING_BASIS`. Analyst paraphrase must never replace the underlying Metro language.

#### `FINDING`

Represents one cumulative factual classification. Findings are not mutually exclusive.

Minimum information:

- stable finding ID;
- controlled finding type;
- affected package/request element/record/reference as applicable;
- `PROVISIONAL` or `VERIFIED` state;
- concise factual description; and
- audit metadata.

Examples include `UNPRODUCED`, `NONEXISTENCE_ASSERTED`, `SUBSTITUTE_PRODUCTION`, `DIRECT_CONTRADICTION`, `STRONG_EXISTENCE_EVIDENCE`, and `POSSIBLE_EXISTENCE_EVIDENCE`.

Adding a finding never erases another applicable finding.

#### `EVIDENCE_CITATION`

Represents one exact documentary support location.

Minimum information:

- stable citation ID;
- source file and occurrence when applicable;
- page, sheet, cell/range, or equivalent locator;
- optional Metro-statement reference; and
- enough location data to reproduce the source check.

`FINDING` and `EVIDENCE_CITATION` are many-to-many: one finding may have several citations and one citation may support several findings.

#### `RECORD_REFERENCE`

Represents one Metro record identifying another record.

Minimum information:

- stable reference ID;
- exact source occurrence and source location;
- controlled relationship type such as `ATTACHMENT`, `EXHIBIT`, `CONTRACT`, `REPORT`, `STUDY`, `INVOICE`, `PROPOSAL`, or `SPREADSHEET`;
- referenced title/name/description as stated by the source;
- linked canonical record when identity is established;
- strict reference-match state;
- absence/search scope; and
- verification state.

A reference does not create a produced Level 2 occurrence by itself.

#### `DATE_FACT`

Represents one sourced, documented date.

Minimum information:

- stable date-fact ID;
- controlled date role such as `RECORD_DATE`, `REFERENCE_DATE`, `REQUEST_DATE`, `RESPONSE_DATE`, or `DISCOVERY_DATE`;
- stored date/value;
- precision/uncertainty qualifier;
- source citation; and
- entity to which the date applies.

Partial, approximate, conflicting, or uncertain dates remain qualified rather than being normalized into guessed exact dates.

#### `TEMPORAL_INFERENCE`

Represents a derived temporal proposition such as `EXISTED_BEFORE_RESPONSE`.

It must link to the `DATE_FACT` records and other verified evidence supporting it. Existence and possession are distinct propositions; neither may silently stand in for the other.

#### `PROCESSING_RUN`

Records each OCR, extraction, normalization, virtual split, or comparable operation.

Minimum information:

- stable run ID;
- operation;
- tool and version;
- material parameters;
- timestamp; and
- errors/warnings relevant to reproducibility.

Reprocessing creates a new run rather than overwriting history.

#### `DERIVATIVE`

Represents a working artifact produced by a processing run.

Minimum information:

- stable derivative ID;
- source-file ID;
- processing-run ID;
- derivative hash;
- artifact type/location; and
- source-page/sheet mapping needed to trace extracted content back to the immutable original.

#### `REVIEW_TASK`

Represents ambiguity automation cannot safely resolve.

Review tasks are required for, at minimum:

- ambiguous Level 2 boundaries;
- unreadable/corrupt content;
- low-confidence OCR that could affect a finding;
- uncertain duplicate or reference matches;
- conflicting metadata; and
- any question requiring human/source verification.

A task remains `OPEN` until explicitly resolved or marked `UNRESOLVED`. Resolution records reviewer, date, decision, and supporting source location. Automation may create review tasks but may never silently resolve evidentiary ambiguity.

#### `AUDIT_EVENT`

Append-only history for substantive analytical changes.

Minimum information:

- stable event ID;
- affected entity and field/classification/mapping;
- previous value;
- new value;
- timestamp;
- concise reason;
- change source/reviewer; and
- supporting evidence where applicable.

Routine regenerable extraction need not create audit history unless it changes evidentiary state.

#### `LEGAL_ASSESSMENT`

Represents legal analysis separately from factual findings.

Minimum information:

- stable assessment ID;
- legal question;
- conclusion or qualified conclusion;
- cited statute, case, rule, or other authority;
- linked supporting `VERIFIED` findings; and
- assessment status/uncertainty.

A final legal conclusion may rely only on `VERIFIED` findings. Provisional findings may inform investigation or a draft legal question but cannot support a final legal conclusion.

## 5. Controlled classifications

Classification fields use stable machine codes. Human-readable labels/descriptions are separate. Retired codes are deprecated rather than silently renamed, deleted, or repurposed.

At minimum, the controlled vocabulary must cover the following concepts.

### 5.1 Finding/response classifications

- `UNPRODUCED`
- `NONEXISTENCE_ASSERTED`
- `SUBSTITUTE_PRODUCTION`
- `DIRECT_CONTRADICTION`
- `STRONG_EXISTENCE_EVIDENCE`
- `POSSIBLE_EXISTENCE_EVIDENCE`
- `PRODUCED_FULL`
- `PRODUCED_PARTIAL_REDACTED`
- `WITHHELD_WHOLE_OR_PART`
- `WITHHOLDING_BASIS_STATED`
- `NO_WITHHOLDING_BASIS_STATED`

These codes are cumulative factual classifications, not a single-choice fulfillment enum.

### 5.2 Package status

The current required package-level exception code is:

- `NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED` — no associated production output was received for the request/control record.

This code must remain distinct from denial, nonexistence, withholding, or an omission within a received production.

### 5.3 Verification state

- `PROVISIONAL` — machine/extraction-based identification suitable for discovery and triage.
- `VERIFIED` — checked against the actual source location and safe to cite as a factual finding.

No contradiction, Metro nonexistence assertion, page/sheet range, reference match, or fulfillment conclusion becomes `VERIFIED` solely through automation.

### 5.4 Reference-match state

- `CONFIRMED_MATCH` — identifiers/content establish identity.
- `PROBABLE_MATCH` — evidence strongly suggests identity but is not conclusive.
- `NO_MATCH_LOCATED` — no analyzed item can presently be identified as the referenced record.

Only `CONFIRMED_MATCH` closes a referenced-but-not-located condition.

### 5.5 Absence/search scope

- `NOT_LOCATED_RESPONSIVE_PACKAGE`
- `LOCATED_ELSEWHERE_CORPUS`
- `NOT_LOCATED_CORPUS`
- `CORPUS_SEARCH_INCOMPLETE`

`LOCATED_ELSEWHERE_CORPUS` establishes that the record was located elsewhere; it does not rewrite the historical response to the original request.

### 5.6 Review-task state

- `OPEN` — requires review/resolution.
- `UNRESOLVED` — reviewed but not safely resolvable from the available evidence.
- `RESOLVED` — explicitly resolved by a reviewer with recorded decision, date, and supporting source location.

### 5.7 Completeness/QC state

- `IN_PROGRESS` — analysis for the defined scope is incomplete.
- `REVIEW_REQUIRED` — one or more open review tasks can affect the scope's result.
- `COMPLETE_WITH_EXCEPTIONS` — processing is complete but identified unresolved issues remain.
- `VERIFIED_COMPLETE` — every source in the defined scope is processed and required verification is complete, with no unresolved issue capable of affecting the reported result.

Package-level completeness never implies corpus completeness. Corpus-wide `VERIFIED_COMPLETE` requires every included package to independently satisfy the applicable verification gate.

## 6. Forensic processing SOP

### Stage 0 — Intake and freeze originals

1. Identify each package-control record and associated production without inferring pairings not established by evidence or user-confirmed provenance.
2. Inventory every Level 1 file exactly as produced.
3. Record original filename, size, SHA-256, type, and page/sheet/equivalent count.
4. Preserve originals unchanged.
5. Record any missing production at the package level. For the current corpus, `3.pdf` is `NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED` with zero received Level 1 files.

**Gate:** Level 1 counts must reproduce the package map before document-level analysis begins.

### Stage 1 — Parse package-control records

1. Divide each request into independent `REQUEST_ELEMENT`s.
2. Preserve Metro's response language as `METRO_STATEMENT`s with exact citations.
3. Link statements to every request element they actually address.
4. Record explicit nonexistence assertions, denials, withholding bases, and other relevant statement types without turning analyst interpretation into Metro's words.

### Stage 2 — Generate working derivatives

1. Create OCR/text/sheet/other derivatives only as needed.
2. Record every operation as a `PROCESSING_RUN` and every output as a `DERIVATIVE`.
3. Preserve mapping from derivative locations back to original Level 1 source locations.
4. Log errors/warnings and open `REVIEW_TASK`s when processing uncertainty could affect identity or findings.

### Stage 3 — Identify Level 2 records and occurrences

1. Apply the independent-identity rule to locate Level 2 records.
2. Create an `OCCURRENCE` for every appearance.
3. Deduplicate exact/identity-equivalent records into a canonical `RECORD` while preserving all occurrences.
4. Keep materially different versions separate and link them as a version family.
5. When a boundary, duplicate match, or identity is uncertain, keep the automated result provisional and open a review task rather than guessing.

### Stage 4 — Crosswalk actual production to request elements

For every request element:

1. identify actual produced record occurrences relevant to it;
2. use `REQUEST_ELEMENT_EVIDENCE` to record each relationship and evidentiary role;
3. record `UNPRODUCED`, `SUBSTITUTE_PRODUCTION`, partial/redacted production, withholding, and other applicable findings cumulatively;
4. never let substitute material count as the specifically requested record unless identity is independently established; and
5. preserve exact citations for all verified conclusions.

### Stage 5 — Extract and resolve record references

For every produced record that identifies another record:

1. create a `RECORD_REFERENCE` at the exact source location;
2. preserve the referenced item's name/identifier/relationship as stated;
3. search the responsive package first;
4. classify identity only as `CONFIRMED_MATCH`, `PROBABLE_MATCH`, or `NO_MATCH_LOCATED`;
5. if not confirmed in the responsive package, search the analyzed corpus as permitted by current completeness;
6. record package/corpus absence scope separately from identity confidence; and
7. do not create a produced occurrence for the referenced item unless the underlying record is actually located.

### Stage 6 — Analyze existence conflicts without overclaiming

When Metro asserted that a requested record did not exist, preserve that assertion as its own sourced statement/finding. Then classify conflicting evidence separately:

- `DIRECT_CONTRADICTION` only when the allegedly nonexistent record itself is located;
- `STRONG_EXISTENCE_EVIDENCE` when another Metro record specifically identifies it by sufficiently distinguishing detail; or
- `POSSIBLE_EXISTENCE_EVIDENCE` when the reference concerns the subject/category but identity is not precise enough.

Do not promote a reference into the missing record itself. Do not infer possession at the response date merely from evidence of existence.

### Stage 7 — Record temporal evidence

1. Preserve observed dates as sourced `DATE_FACT`s.
2. Keep request date, response date, record date, reference date, and discovery date distinct.
3. Preserve uncertainty/conflict in the date fact itself.
4. Create `TEMPORAL_INFERENCE`s only as separately derived propositions tied to supporting evidence.
5. Treat existence and possession as separate propositions requiring separate evidentiary support.

### Stage 8 — Human/source verification

1. Verify material automated findings directly against the original source page/file or mapped source location.
2. Resolve or explicitly leave unresolved each review task affecting the analytical scope.
3. Promote a finding from `PROVISIONAL` to `VERIFIED` only after the source check supports the exact proposition and locator.
4. Record substantive corrections through `AUDIT_EVENT`s rather than silently overwriting them.

### Stage 9 — Completeness and absence gating

1. Assign completeness separately for each package.
2. Do not infer corpus completeness from a complete individual package.
3. Corpus-wide `VERIFIED_COMPLETE` requires every included package to independently pass its applicable verification gate.
4. `NOT_LOCATED_CORPUS` may be assigned only after the relevant corpus search reaches sufficient verified completeness.
5. Until that gate is satisfied, use `CORPUS_SEARCH_INCOMPLETE` rather than presenting an unfinished search as proof of absence.

### Stage 10 — Reporting and legal assessment

1. Generate analytical reports from SQLite rather than hand-maintaining parallel totals.
2. Separate factual findings from legal assessments.
3. A final `LEGAL_ASSESSMENT` may rely only on `VERIFIED` findings and must cite the specific legal authority applied.
4. Preserve qualified/uncertain legal conclusions where facts or law do not justify categorical language.

## 7. Required reporting outputs

Final reporting must never collapse unlike units into a single generic "document count." Report at least:

1. Level 1 source files received, by package;
2. unique Level 2 records;
3. total Level 2 record occurrences;
4. referenced records not produced;
5. request elements and their cumulative fulfillment classifications;
6. existence-conflict findings by tier;
7. `VERIFIED` versus `PROVISIONAL` findings;
8. open/unresolved review tasks capable of affecting results;
9. completeness/QC state by package; and
10. corpus-wide totals separately from package-specific totals.

Every reported number must be reproducible by a query against the canonical ledger. Duplicate occurrences and substitute material must not inflate apparent responsiveness.

Recommended generated views/reports include:

- package inventory;
- request-element crosswalk;
- Level 2 unique-record inventory;
- occurrence ledger;
- referenced-but-not-located register;
- Metro nonexistence assertion vs. existence-evidence matrix;
- withholding/redaction register;
- verification/review queue;
- audit-history report; and
- legal-assessment report generated only from verified factual support.

## 8. Required invariants

An implementation is conformant only if all of the following remain true:

1. Level 1 originals are never altered by processing.
2. Every derivative traces to an immutable Level 1 source and processing run.
3. Every Level 2 occurrence traces to an exact Level 1 location.
4. Package-control PDFs never inflate Level 1 production counts.
5. One canonical Level 2 record may have many preserved occurrences.
6. Materially different versions are not deduplicated into one record.
7. Findings are cumulative and never mutually overwrite applicable classifications.
8. Metro's own statements remain separately preserved from analyst findings.
9. A referenced record is not counted as produced until the underlying record is actually located.
10. A `PROBABLE_MATCH` does not close a referenced-but-not-located condition.
11. A record found in another package never retroactively becomes part of the original responsive production.
12. Existence is never silently upgraded to possession.
13. Automated analysis alone never creates a `VERIFIED` material finding.
14. Ambiguity capable of affecting evidence is surfaced as a review task.
15. `NOT_LOCATED_CORPUS` is impossible before the applicable completeness gate is satisfied.
16. Final legal conclusions depend only on `VERIFIED` factual findings plus identified legal authority.
17. Substantive analytical changes remain reconstructable through append-only audit events.
18. Every published total is reproducible from SQLite and declares its counting unit/scope.

## 9. Verification and acceptance tests

The implementation plan must include tests proving at least the following behaviors.

### Provenance/counting tests

- The current package map produces Level 1 counts of 24, 72, and 0 for `1.pdf`, `2.pdf`, and `3.pdf` respectively.
- Package-control records are not included in those Level 1 production totals.
- A duplicate Level 2 record appearing twice yields one unique record and two occurrences.
- A materially different version yields a second record linked to the first rather than being deduplicated away.

### Classification tests

- `UNPRODUCED`, `NONEXISTENCE_ASSERTED`, and `SUBSTITUTE_PRODUCTION` can coexist for one request element.
- Adding existence evidence does not erase the underlying Metro statement or prior finding.
- A probable reference match remains unresolved.
- Locating a referenced record in a different package sets corpus-location evidence without crediting the original responsive package.

### Verification/QC tests

- Automated extraction can create provisional findings/review tasks but cannot directly create source-verified material findings.
- A verification-state change creates an auditable substantive transition.
- An unresolved review task capable of affecting the scope prevents `VERIFIED_COMPLETE`.
- `NOT_LOCATED_CORPUS` cannot be assigned while the relevant corpus search is incomplete.

### Temporal/legal tests

- Evidence of record existence does not automatically set possession-at-response.
- Conflicting/partial date facts remain qualified.
- A final legal assessment cannot depend on a provisional finding.

### Reproducibility tests

- Reprocessing creates new processing-run and derivative identities without modifying the original file or old provenance.
- Generated CSV/Markdown totals reconcile exactly to SQLite queries.
- Correcting a substantive mapping or classification preserves the prior state through `AUDIT_EVENT` history.

## 10. Error and ambiguity handling

- **Unreadable/corrupt source:** preserve the source/hash, record the condition, attempt only traceable derivative processing, and open a review task if the condition can affect findings.
- **Low-confidence OCR:** use for discovery only; do not verify a factual proposition from OCR alone.
- **Ambiguous record boundary:** keep candidate segmentation provisional and open review.
- **Conflicting metadata:** retain the conflict rather than choosing a convenient value; verify against source evidence.
- **Uncertain duplicate/version relationship:** do not collapse identities until the evidence supports the match.
- **Uncertain reference match:** retain `PROBABLE_MATCH` or `NO_MATCH_LOCATED`; never force a confirmed identity.
- **Incomplete corpus search:** use `CORPUS_SEARCH_INCOMPLETE`, not a corpus-wide absence claim.
- **Processing failure:** log the run error without modifying the original or pretending the source was successfully analyzed.

## 11. Scope boundaries

This specification defines the forensic evidence model and processing SOP. It does **not** itself:

- perform OCR, extraction, classification, or source verification on the Metro corpus;
- decide the merits of any Ohio public-records-law claim;
- infer facts not supported by the evidence;
- authorize modification of Metro's original files; or
- treat an automated result as a verified factual finding.

Legal research and final legal conclusions occur only in the separate `LEGAL_ASSESSMENT` layer after factual verification.

## 12. Implementation handoff

After user approval of this written specification, the next step is a detailed implementation plan. That plan should translate this design into:

- SQLite DDL and controlled-vocabulary tables;
- ingestion and hashing workflow;
- derivative/OCR/extraction pipeline;
- record-boundary and deduplication workflow;
- review/verification workflow;
- audit-event mechanics;
- reproducible reporting queries/views; and
- automated tests for the invariants and acceptance cases above.

Implementation must not reopen Decisions 1–33 unless execution exposes a genuinely new ambiguity that cannot be resolved by this specification.
