# Metro Forensic Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved Metro/SORTA forensic-processing system as a reproducible SQLite ledger and traceable extraction/reporting pipeline, then run its intake and QC gates against the current 24/72/0 corpus without altering Metro's originals.

**Architecture:** A small Python package owns all mutations of the canonical SQLite ledger. Immutable evidence remains under `upload/`; ZIP members are streamed byte-for-byte into processing reads rather than rewritten in place, while generated derivatives and reports live under `analysis/`. Application services enforce the evidentiary rules that SQLite cannot express cleanly; database constraints/triggers enforce append-only audit history and stable controlled codes.

**Tech Stack:** Python 3 standard library (`sqlite3`, `zipfile`, `hashlib`, `csv`, `json`, `pathlib`, `uuid`, `unittest`), `pypdf`, `pdfplumber`, `openpyxl`, `python-docx`, Pillow, `pdftotext`, `pdfinfo`, and Tesseract when OCR is required. No network service is required.

## Global Constraints

- Design authority is `docs/superpowers/specs/2026-08-07-metro-public-records-forensic-processing-design.md`, user-approved 2026-08-07.
- Current package map is fixed: `1.pdf` -> `26-145_2026-08-07 11_33_06 -0400.zip` -> 24 Level 1 files; `2.pdf` -> `Metro PRR Now.zip` -> 72 Level 1 files; `3.pdf` -> no production -> 0 Level 1 files and `NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED`.
- Files under `upload/` are immutable evidence. Tests and implementation must never write to them.
- Package-control PDFs establish request/response context and never count as Level 1 produced files.
- SQLite is the sole analytical source of truth. CSV/Markdown are regenerated outputs only.
- Findings are cumulative. `UNPRODUCED`, `NONEXISTENCE_ASSERTED`, and `SUBSTITUTE_PRODUCTION` may coexist.
- Automation may create `PROVISIONAL` findings and `REVIEW_TASK`s; it may not create a `VERIFIED` material finding.
- References do not count as production. `PROBABLE_MATCH` does not close an absence condition.
- Package scope precedes corpus scope. `NOT_LOCATED_CORPUS` is prohibited until the relevant corpus is `VERIFIED_COMPLETE`.
- Existence and possession are separate propositions.
- Final legal assessments may depend only on `VERIFIED` findings and cited authority.
- Substantive analytical changes are append-only in `AUDIT_EVENT` history.
- The single legacy `.doc` in the current 26-145 ZIP has no installed safe text extractor in this environment; ingestion must preserve it, record the limitation, and create a review/extraction exception instead of guessing from lossy conversion.
- The current workspace is not a Git worktree. Commit steps below are for execution after the project is placed in a Git-backed checkout; do not fabricate commits in this workspace.

## File Structure

```text
config/
  corpus.json                    Exact three-package intake manifest
metro_forensics/
  __init__.py                    Package marker and version
  db.py                          SQLite connection, transactions, migrations
  schema.sql                     Canonical tables, FK/check constraints, triggers, indexes, vocab seed
  ids.py                         Stable deterministic IDs for immutable/provenance entities
  ingest.py                      Package-control/archive inventory and Level 1 hashing
  extract.py                     Processing-run/derivative creation and file-type adapters
  records.py                     Level 2 record, occurrence, dedup/version-family operations
  evidence.py                    Request elements, statements, findings, citations, references
  temporal_legal.py              Date facts, temporal inferences, legal-assessment guards
  review.py                      Review tasks, verification transitions, QC/completeness, audit events
  report.py                      Reproducible SQL-backed CSV/Markdown exports
  cli.py                         Thin command-line entry points for staged execution
tests/
  helpers.py                     Temporary DB/archive/document fixtures
  test_schema.py                 Schema/vocabulary/invariant tests
  test_ingest.py                 Package and Level 1 provenance/count tests
  test_extract.py                Derivative/provenance/adapter tests
  test_records.py                Level 2 identity, occurrence, duplicate/version tests
  test_evidence.py               Cumulative finding/reference/absence tests
  test_temporal_legal.py         Date/inference/legal separation tests
  test_review.py                 Verification, audit, review-task, completeness tests
  test_report.py                 Query/export reconciliation tests
  test_current_corpus.py         Locked 24/72/0 smoke/acceptance test
analysis/
  metro_forensics.sqlite3        Generated canonical ledger; never hand-edited
  derivatives/                   Generated working artifacts, source-mapped and hashed
  reports/                       Generated CSV/Markdown views
```

`analysis/` is runtime output. No implementation task copies, renames, normalizes, or rewrites files under `upload/`.

---

### Task 1: Canonical schema, controlled vocabularies, and database bootstrap

**Files:**
- Create: `metro_forensics/__init__.py`
- Create: `metro_forensics/schema.sql`
- Create: `metro_forensics/db.py`
- Create: `tests/helpers.py`
- Create: `tests/test_schema.py`

**Interfaces:**
- Produces: `connect(path: Path) -> sqlite3.Connection`
- Produces: `initialize(conn: sqlite3.Connection) -> None`
- Produces: `new_test_db() -> sqlite3.Connection`
- Consumes: nothing from later tasks.

- [ ] **Step 1: Write the schema/vocabulary acceptance test**

```python
# tests/test_schema.py
import sqlite3
import unittest
from tests.helpers import new_test_db

REQUIRED_TABLES = {
    "package", "request_element", "source_file", "record", "occurrence",
    "request_element_evidence", "metro_statement", "statement_request_element",
    "finding", "evidence_citation", "finding_citation", "record_reference",
    "date_fact", "temporal_inference", "temporal_inference_date_fact",
    "processing_run", "derivative", "review_task", "audit_event",
    "legal_assessment", "legal_assessment_finding", "legal_authority",
    "record_version_link", "vocabulary",
}

class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = new_test_db()

    def test_required_tables_and_codes_exist(self):
        tables = {r[0] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertTrue(REQUIRED_TABLES <= tables)
        codes = {r[0] for r in self.db.execute("SELECT code FROM vocabulary")}
        for code in {
            "UNPRODUCED", "NONEXISTENCE_ASSERTED", "SUBSTITUTE_PRODUCTION",
            "DIRECT_CONTRADICTION", "STRONG_EXISTENCE_EVIDENCE",
            "POSSIBLE_EXISTENCE_EVIDENCE", "PROVISIONAL", "VERIFIED",
            "CONFIRMED_MATCH", "PROBABLE_MATCH", "NO_MATCH_LOCATED",
            "NOT_LOCATED_RESPONSIVE_PACKAGE", "LOCATED_ELSEWHERE_CORPUS",
            "NOT_LOCATED_CORPUS", "CORPUS_SEARCH_INCOMPLETE",
            "OPEN", "UNRESOLVED", "RESOLVED", "IN_PROGRESS",
            "REVIEW_REQUIRED", "COMPLETE_WITH_EXCEPTIONS", "VERIFIED_COMPLETE",
            "NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED",
        }:
            self.assertIn(code, codes)

    def test_audit_events_are_append_only(self):
        self.db.execute(
            "INSERT INTO audit_event(event_id, entity_type, entity_id, changed_at, reason, change_source) "
            "VALUES('AE1','FINDING','F1','2026-08-07T12:00:00Z','test','human')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM audit_event WHERE event_id='AE1'")
```

- [ ] **Step 2: Run the tests and confirm they fail because the schema/bootstrap does not exist**

Run: `python3 -m unittest tests.test_schema -v`

Expected: import/schema failure before any application implementation exists.

- [ ] **Step 3: Implement the schema and bootstrap**

`schema.sql` must create the 22 tables named above with `PRAGMA foreign_keys=ON`, foreign keys for every entity relationship, `CHECK` constraints for required booleans/states, and indexes on every foreign key used in reporting joins. Use the vocabulary table as the stable code registry:

```sql
CREATE TABLE vocabulary (
    domain TEXT NOT NULL,
    code TEXT NOT NULL,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    deprecated INTEGER NOT NULL DEFAULT 0 CHECK (deprecated IN (0,1)),
    PRIMARY KEY (domain, code)
);

CREATE TABLE package (
    package_id TEXT PRIMARY KEY,
    control_record_path TEXT NOT NULL UNIQUE,
    production_archive_path TEXT,
    package_status TEXT,
    expected_level1_count INTEGER NOT NULL CHECK (expected_level1_count >= 0),
    completeness_state TEXT NOT NULL DEFAULT 'IN_PROGRESS',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE source_file (
    source_file_id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL REFERENCES package(package_id),
    archive_member_path TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    sha256 TEXT NOT NULL CHECK (length(sha256)=64),
    media_type TEXT NOT NULL,
    structural_unit_count INTEGER,
    processing_condition TEXT NOT NULL DEFAULT '',
    UNIQUE(package_id, archive_member_path, sha256)
);

CREATE TABLE audit_event (
    event_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    field_name TEXT,
    previous_value TEXT,
    new_value TEXT,
    changed_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    change_source TEXT NOT NULL,
    supporting_citation_id TEXT
);

CREATE TRIGGER audit_event_no_update
BEFORE UPDATE ON audit_event BEGIN SELECT RAISE(ABORT, 'AUDIT_EVENT is append-only'); END;
CREATE TRIGGER audit_event_no_delete
BEFORE DELETE ON audit_event BEGIN SELECT RAISE(ABORT, 'AUDIT_EVENT is append-only'); END;
```

Complete the remaining required entities from spec §4.3. `finding` must allow multiple rows per request element/type combination when the propositions differ; do not implement a single fulfillment-status column. `record_reference` stores match state and absence scope separately. `legal_assessment_finding` is the bridge that later guards final legal conclusions.

Seed every controlled code from spec §5 plus statement types (`NONEXISTENCE_ASSERTION`, `DENIAL`, `WITHHOLDING_BASIS`), relationship types (`ATTACHMENT`, `EXHIBIT`, `CONTRACT`, `REPORT`, `STUDY`, `INVOICE`, `PROPOSAL`, `SPREADSHEET`), date roles (`RECORD_DATE`, `REFERENCE_DATE`, `REQUEST_DATE`, `RESPONSE_DATE`, `DISCOVERY_DATE`), and evidentiary relationship roles (`RESPONSIVE`, `SUBSTITUTE`, `EXISTENCE_EVIDENCE`, `CONTRADICTION_EVIDENCE`, `CONTEXT`).

```python
# metro_forensics/db.py
from pathlib import Path
import sqlite3

SCHEMA = Path(__file__).with_name("schema.sql")

def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
```

```python
# tests/helpers.py
import sqlite3
from metro_forensics.db import initialize

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
```

- [ ] **Step 4: Run schema tests to green**

Run: `python3 -m unittest tests.test_schema -v`

Expected: all schema tests pass, including the append-only audit trigger test.

- [ ] **Step 5: Run an integrity smoke check**

Run: `python3 -m unittest discover -s tests -v`

Expected: all currently implemented tests pass with no evidence files modified.

- [ ] **Step 6: Commit when running in a Git-backed checkout**

```bash
git add metro_forensics tests
git commit -m "feat: add forensic ledger schema"
```

---

### Task 2: Stable IDs and idempotent package/Level 1 ingestion

**Files:**
- Create: `metro_forensics/ids.py`
- Create: `metro_forensics/ingest.py`
- Create: `config/corpus.json`
- Create: `tests/test_ingest.py`
- Create: `tests/test_current_corpus.py`

**Interfaces:**
- Consumes: `connect()`, `initialize()` from Task 1.
- Produces: `stable_id(prefix: str, *parts: str) -> str`
- Produces: `ingest_manifest(conn, manifest_path: Path, evidence_root: Path) -> None`
- Produces: `inventory_archive(conn, package_id: str, archive: Path) -> int`

- [ ] **Step 1: Write failing ingestion tests**

```python
# tests/test_ingest.py
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from tests.helpers import new_test_db
from metro_forensics.ingest import ingest_manifest

class IngestTests(unittest.TestCase):
    def test_zip_members_are_level1_and_control_pdf_is_not(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "1.pdf").write_bytes(b"control")
            with zipfile.ZipFile(root / "prod.zip", "w") as z:
                z.writestr("a.pdf", b"A")
                z.writestr("b.xlsx", b"B")
            manifest = {"packages": [{
                "package_id": "P1", "control_record": "1.pdf",
                "production_archive": "prod.zip", "expected_level1_count": 2,
                "package_status": None
            }]}
            (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")
            db = new_test_db()
            ingest_manifest(db, root / "corpus.json", root)
            self.assertEqual(2, db.execute("SELECT count(*) FROM source_file").fetchone()[0])
            self.assertEqual(hashlib.sha256(b"A").hexdigest(),
                db.execute("SELECT sha256 FROM source_file WHERE archive_member_path='a.pdf'").fetchone()[0])

    def test_ingestion_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "1.pdf").write_bytes(b"control")
            with zipfile.ZipFile(root / "prod.zip", "w") as z:
                z.writestr("only.pdf", b"same bytes")
            manifest = {"packages": [{
                "package_id": "P1", "control_record": "1.pdf",
                "production_archive": "prod.zip", "expected_level1_count": 1,
                "package_status": None
            }]}
            (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")
            db = new_test_db()
            ingest_manifest(db, root / "corpus.json", root)
            ingest_manifest(db, root / "corpus.json", root)
            self.assertEqual(1, db.execute("SELECT count(*) FROM source_file").fetchone()[0])
```

- [ ] **Step 2: Run the ingestion tests and confirm failure**

Run: `python3 -m unittest tests.test_ingest -v`

Expected: import failure because `ids.py`/`ingest.py` do not yet exist.

- [ ] **Step 3: Implement stable IDs and safe ZIP streaming**

```python
# metro_forensics/ids.py
import hashlib

def stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:24]}"
```

`inventory_archive` must iterate `ZipInfo` members, reject directories from Level 1 counts, reject absolute/traversal member names (`..` path components), stream member bytes through SHA-256 without writing to `upload/`, detect media type by extension plus `file`/reader validation where appropriate, and upsert only on the immutable uniqueness key. A rerun must produce identical IDs and no new rows.

`config/corpus.json` must contain exactly:

```json
{
  "packages": [
    {"package_id":"PKG_1","control_record":"1.pdf","production_archive":"26-145_2026-08-07 11_33_06 -0400.zip","expected_level1_count":24,"package_status":null},
    {"package_id":"PKG_2","control_record":"2.pdf","production_archive":"Metro PRR Now.zip","expected_level1_count":72,"package_status":null},
    {"package_id":"PKG_3","control_record":"3.pdf","production_archive":null,"expected_level1_count":0,"package_status":"NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED"}
  ]
}
```

The production archives are resolved relative to `upload/`; a missing archive for any package except the explicitly zero-production package is an error. Count mismatch is a hard intake failure.

- [ ] **Step 4: Add the real-corpus count test without parsing document content**

```python
# tests/test_current_corpus.py
import tempfile
import unittest
from pathlib import Path
from metro_forensics.db import connect, initialize
from metro_forensics.ingest import ingest_manifest

ROOT = Path(__file__).resolve().parents[1]

class CurrentCorpusTests(unittest.TestCase):
    def test_locked_level1_counts(self):
        with tempfile.TemporaryDirectory() as td:
            db = connect(Path(td) / "ledger.sqlite3")
            initialize(db)
            ingest_manifest(db, ROOT / "config/corpus.json", ROOT / "upload")
            rows = dict(db.execute(
                "SELECT package_id, count(source_file_id) FROM package "
                "LEFT JOIN source_file USING(package_id) GROUP BY package_id"
            ))
            self.assertEqual({"PKG_1": 24, "PKG_2": 72, "PKG_3": 0}, rows)
            status = db.execute(
                "SELECT package_status FROM package WHERE package_id='PKG_3'"
            ).fetchone()[0]
            self.assertEqual("NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED", status)
```

- [ ] **Step 5: Run ingestion and current-corpus tests to green**

Run: `python3 -m unittest tests.test_ingest tests.test_current_corpus -v`

Expected: all pass; current evidence files remain byte-for-byte untouched.

- [ ] **Step 6: Commit when running in a Git-backed checkout**

```bash
git add config metro_forensics/ids.py metro_forensics/ingest.py tests/test_ingest.py tests/test_current_corpus.py
git commit -m "feat: add immutable package intake"
```

---

### Task 3: Processing provenance and source-type extraction adapters

**Files:**
- Create: `metro_forensics/extract.py`
- Create: `tests/test_extract.py`
- Modify: `metro_forensics/schema.sql`

**Interfaces:**
- Consumes: `source_file` rows and `stable_id()`.
- Produces: `process_source(conn, source_file_id: str, evidence_root: Path, derivative_root: Path) -> list[str]`
- Produces: `extract_pdf(data: bytes)`, `extract_xlsx(data: bytes)`, `extract_docx(data: bytes)` adapter results containing text plus exact source-unit maps.
- Produces: `record_processing_result(conn, source_file_id: str, operation: str, derivative_bytes: bytes) -> ProcessingResult`.
- Produces: `record_unsupported_legacy_doc(conn, source_file_id: str) -> UnsupportedResult`.
- Produces: `PROCESSING_RUN` and `DERIVATIVE` rows for every attempted operation.

- [ ] **Step 1: Write failing provenance/adapter tests**

```python
# tests/test_extract.py
import unittest
from tests.helpers import new_test_db, seed_package_source
from metro_forensics.extract import record_processing_result, record_unsupported_legacy_doc

class ExtractionTests(unittest.TestCase):
    def test_reprocessing_creates_new_run_and_derivative_ids(self):
        db = new_test_db()
        _, source_id = seed_package_source(db, member="sheet.xlsx")
        first = record_processing_result(db, source_id, "EXTRACT_XLSX", b"first")
        second = record_processing_result(db, source_id, "EXTRACT_XLSX", b"first")
        self.assertNotEqual(first.processing_run_id, second.processing_run_id)
        self.assertNotEqual(first.derivative_id, second.derivative_id)
        self.assertEqual(2, db.execute(
            "SELECT count(*) FROM processing_run WHERE source_file_id=?", (source_id,)
        ).fetchone()[0])
        self.assertEqual("0" * 64, db.execute(
            "SELECT sha256 FROM source_file WHERE source_file_id=?", (source_id,)
        ).fetchone()[0])

    def test_legacy_doc_opens_review_exception(self):
        db = new_test_db()
        _, source_id = seed_package_source(db, member="legacy.doc")
        result = record_unsupported_legacy_doc(db, source_id)
        task = db.execute(
            "SELECT state,reason_code FROM review_task WHERE review_task_id=?",
            (result.review_task_id,),
        ).fetchone()
        self.assertEqual(("OPEN", "UNSUPPORTED_LEGACY_DOC"), tuple(task))
        self.assertEqual(0, db.execute(
            "SELECT count(*) FROM derivative WHERE source_file_id=?", (source_id,)
        ).fetchone()[0])
```

- [ ] **Step 2: Run the extraction tests and confirm failure**

Run: `python3 -m unittest tests.test_extract -v`

Expected: fail because extraction/provenance services are absent.

- [ ] **Step 3: Implement adapters and provenance-first processing**

Each adapter returns a structure like:

```python
@dataclass(frozen=True)
class ExtractedUnit:
    unit_kind: str       # PAGE, SHEET, PARAGRAPH
    unit_locator: str    # page:1, sheet:Data, paragraph:17
    text: str
    confidence: float | None
```

Rules:

- PDF: use embedded text first (`pypdf`/`pdftotext`), page-mapped; invoke Tesseract only when a page has no usable embedded text, and preserve OCR confidence/warnings.
- XLSX: open from bytes with `openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=False)` and preserve sheet name plus cell/range locators; formulas and displayed values must not be conflated.
- DOCX: read paragraphs/tables with `python-docx`, preserving paragraph/table indices.
- DOC: record the processing attempt and condition `UNSUPPORTED_LEGACY_DOC`; open an `OPEN` review task; do not generate a pseudo-verified text derivative.
- Every run records tool/version, parameters, time, and warnings/errors even when it fails.
- Every derivative is written only below `analysis/derivatives/<source_file_id>/<processing_run_id>/`, hashed, and mapped back to source units.

- [ ] **Step 4: Run extraction tests to green**

Run: `python3 -m unittest tests.test_extract -v`

Expected: adapter/provenance tests pass and no fixture writes escape their temp directory.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 6: Commit when running in a Git-backed checkout**

```bash
git add metro_forensics/extract.py metro_forensics/schema.sql tests/test_extract.py
git commit -m "feat: add traceable extraction pipeline"
```

---

### Task 4: Level 2 records, occurrences, exact deduplication, and version families

**Files:**
- Create: `metro_forensics/records.py`
- Create: `tests/test_records.py`
- Modify: `metro_forensics/schema.sql`

**Interfaces:**
- Consumes: source/derivative IDs from Tasks 2–3.
- Produces: `create_record(conn, title: str, content_fingerprint: str, record_type: str | None = None) -> str`
- Produces: `create_occurrence(conn, record_id: str, source_file_id: str, derivative_id: str | None, locator: str, verification_state: str) -> str`
- Produces: `resolve_exact_duplicate(conn, content_fingerprint: str) -> str | None`
- Produces: `link_version_family(conn, older_record_id: str, newer_record_id: str, basis: str) -> None`
- Produces: `open_boundary_review(conn, source_file_id: str, locator: str, candidate_description: str) -> str` for ambiguous segmentation.

- [ ] **Step 1: Write failing identity tests**

```python
# tests/test_records.py
import unittest
from tests.helpers import new_test_db, seed_package_source
from metro_forensics.records import create_record, create_occurrence, link_version_family, open_boundary_review

class RecordIdentityTests(unittest.TestCase):
    def test_duplicate_has_one_record_two_occurrences(self):
        db = new_test_db()
        _, s1 = seed_package_source(db, source_file_id="S1", member="a.pdf")
        db.execute(
            "INSERT INTO source_file(source_file_id,package_id,archive_member_path,byte_size,sha256,media_type) "
            "VALUES('S2','P1','b.pdf',1,?,'application/pdf')", ("1" * 64,)
        )
        r1 = create_record(db, "Same record", "f" * 64)
        r2 = create_record(db, "Same record", "f" * 64)
        self.assertEqual(r1, r2)
        create_occurrence(db, r1, s1, None, "page:1", "PROVISIONAL")
        create_occurrence(db, r1, "S2", None, "page:1", "PROVISIONAL")
        self.assertEqual(1, db.execute("SELECT count(*) FROM record").fetchone()[0])
        self.assertEqual(2, db.execute("SELECT count(*) FROM occurrence").fetchone()[0])

    def test_materially_different_version_stays_separate(self):
        db = new_test_db()
        r1 = create_record(db, "Contract", "a" * 64)
        r2 = create_record(db, "Contract revised", "b" * 64)
        link_version_family(db, r1, r2, "same contract identifier; materially revised bytes")
        self.assertNotEqual(r1, r2)
        self.assertEqual(2, db.execute("SELECT count(*) FROM record").fetchone()[0])
        self.assertEqual(1, db.execute("SELECT count(*) FROM record_version_link").fetchone()[0])

    def test_ambiguous_boundary_is_review_required(self):
        db = new_test_db()
        _, source_id = seed_package_source(db)
        task_id = open_boundary_review(db, source_id, "pages:10-12", "possible embedded exhibit")
        row = db.execute(
            "SELECT state,reason_code FROM review_task WHERE review_task_id=?", (task_id,)
        ).fetchone()
        self.assertEqual(("OPEN", "AMBIGUOUS_LEVEL2_BOUNDARY"), tuple(row))
```

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_records -v`

Expected: fail until record services exist.

- [ ] **Step 3: Implement record identity operations**

Use exact/content-identity fingerprints only for automatic deduplication. A title/date similarity may generate a candidate/review task but may not collapse records. `create_occurrence` must require an exact Level 1 locator (`page`, `sheet/cell-range`, paragraph/table range, or whole-file locator) and a `PROVISIONAL`/`VERIFIED` identification state. Materially different content always receives a separate `record_id`; `record_version_link(record_id_a, record_id_b, basis)` preserves the family.

```python
def resolve_exact_duplicate(conn, content_fingerprint: str) -> str | None:
    row = conn.execute(
        "SELECT record_id FROM record WHERE content_fingerprint=?",
        (content_fingerprint,),
    ).fetchone()
    return None if row is None else row[0]
```

- [ ] **Step 4: Run record tests to green**

Run: `python3 -m unittest tests.test_records -v`

Expected: one-record/two-occurrence and separate-version invariants pass.

- [ ] **Step 5: Commit when running in a Git-backed checkout**

```bash
git add metro_forensics/records.py metro_forensics/schema.sql tests/test_records.py
git commit -m "feat: model level two record identity"
```

---

### Task 5: Request elements, Metro statements, cumulative findings, citations, and evidence links

**Files:**
- Create: `metro_forensics/evidence.py`
- Create: `tests/test_evidence.py`
- Modify: `metro_forensics/schema.sql`
- Modify: `tests/helpers.py`

**Interfaces:**
- Consumes: package, record, occurrence IDs.
- Produces: `add_request_element(conn, package_id: str, text: str, ordinal: int, parent_id: str | None = None) -> str`
- Produces: `add_metro_statement(conn, text: str, statement_type: str, citation_id: str, verification_state: str) -> str`
- Produces: `add_finding(conn, finding_type: str, request_element_id: str | None, verification_state: str, creation_source: str, description: str) -> str`
- Produces: `add_citation(conn, source_file_id: str, occurrence_id: str | None, locator: str) -> str`
- Produces: `link_request_evidence(conn, request_element_id: str, occurrence_id: str, role: str) -> None`
- Produces: `add_record_reference(conn, source_occurrence_id: str, locator: str, relationship_type: str, referenced_description: str) -> str`
- Produces: `set_reference_match(conn, reference_id: str, match_state: str, record_id: str | None) -> None` and `set_reference_absence_scope(conn, reference_id: str, absence_scope: str) -> None` guarded transitions.

- [ ] **Step 1: Write failing cumulative/reference tests**

```python
# tests/test_evidence.py
import unittest
from tests.helpers import new_test_db, seeded_reference_db, seeded_cross_package_reference_db
from metro_forensics.evidence import add_request_element, add_finding, set_reference_match

class EvidenceTests(unittest.TestCase):
    def test_cumulative_findings_do_not_overwrite(self):
        db = new_test_db()
        db.execute("INSERT INTO package(package_id,control_record_path,expected_level1_count) VALUES('P1','1.pdf',0)")
        req = add_request_element(db, "P1", "requested item", 1)
        for code in ("UNPRODUCED", "NONEXISTENCE_ASSERTED", "SUBSTITUTE_PRODUCTION"):
            add_finding(db, code, req, "PROVISIONAL", "HUMAN", code.lower())
        found = {r[0] for r in db.execute(
            "SELECT finding_type FROM finding WHERE request_element_id=?", (req,)
        )}
        self.assertEqual({"UNPRODUCED", "NONEXISTENCE_ASSERTED", "SUBSTITUTE_PRODUCTION"}, found)

    def test_probable_reference_match_does_not_close_absence(self):
        db, ref, candidate = seeded_reference_db()
        set_reference_match(db, ref, "PROBABLE_MATCH", candidate)
        row = db.execute("SELECT resolved_record_id FROM record_reference WHERE reference_id=?", (ref,)).fetchone()
        self.assertIsNone(row[0])

    def test_elsewhere_match_does_not_credit_original_package(self):
        db, ref, record_id = seeded_cross_package_reference_db()
        set_reference_match(db, ref, "CONFIRMED_MATCH", record_id)
        scope = db.execute("SELECT absence_scope FROM record_reference WHERE reference_id=?", (ref,)).fetchone()[0]
        pkg1_count = db.execute(
            "SELECT count(*) FROM occurrence o JOIN source_file s USING(source_file_id) "
            "WHERE o.record_id=? AND s.package_id='P1'", (record_id,)
        ).fetchone()[0]
        self.assertEqual("LOCATED_ELSEWHERE_CORPUS", scope)
        self.assertEqual(0, pkg1_count)
```

In this task, extend `tests/helpers.py` with concrete `seeded_reference_db()` and `seeded_cross_package_reference_db()` builders. Each builder uses `seed_package_source`, `create_record`, `create_occurrence`, and `add_record_reference` to return the exact `(db, reference_id, candidate_record_id)` graph consumed above; it must not insert synthetic occurrences into the reference's responsive package.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_evidence -v`

Expected: fail until evidence services are implemented.

- [ ] **Step 3: Implement evidence operations with fail-closed verification**

`add_metro_statement` must preserve source text and exact citation separately from any finding. `add_finding` must reject `verification_state='VERIFIED'` when `creation_source='AUTOMATION'`. `add_record_reference` must not create a `record` or `occurrence`; it stores only the referenced description plus source occurrence/location.

```python
MATERIAL_FINDINGS = {
    "UNPRODUCED", "NONEXISTENCE_ASSERTED", "SUBSTITUTE_PRODUCTION",
    "DIRECT_CONTRADICTION", "STRONG_EXISTENCE_EVIDENCE",
    "POSSIBLE_EXISTENCE_EVIDENCE", "PRODUCED_FULL",
    "PRODUCED_PARTIAL_REDACTED", "WITHHELD_WHOLE_OR_PART",
    "WITHHOLDING_BASIS_STATED", "NO_WITHHOLDING_BASIS_STATED",
}

def assert_verification_allowed(finding_type, verification_state, creation_source):
    if (finding_type in MATERIAL_FINDINGS and verification_state == "VERIFIED"
            and creation_source == "AUTOMATION"):
        raise ValueError("automation cannot create VERIFIED material findings")
```

Only `CONFIRMED_MATCH` may link a reference to a canonical record as resolved. If that record has no occurrence in the reference's responsive package but does have one elsewhere, use `LOCATED_ELSEWHERE_CORPUS`; do not insert an occurrence into the original package.

- [ ] **Step 4: Run evidence tests to green**

Run: `python3 -m unittest tests.test_evidence -v`

Expected: cumulative and cross-package/reference invariants pass.

- [ ] **Step 5: Commit when running in a Git-backed checkout**

```bash
git add metro_forensics/evidence.py metro_forensics/schema.sql tests/test_evidence.py
git commit -m "feat: add cumulative evidence model"
```

---

### Task 6: Review tasks, audit transitions, and completeness gates

**Files:**
- Create: `metro_forensics/review.py`
- Create: `tests/test_review.py`
- Modify: `metro_forensics/schema.sql`
- Modify: `tests/helpers.py`

**Interfaces:**
- Consumes: findings, occurrences, references, packages, review tasks.
- Produces: `open_review_task(conn, entity_type: str, entity_id: str, reason_code: str, description: str, material: bool = True) -> str`
- Produces: `resolve_review_task(conn, task_id: str, state: str, reviewer: str, resolved_at: str, decision: str, source_locator: str | None) -> None`
- Produces: `promote_finding_verified(conn, finding_id: str, reviewer: str, verified_at: str, citation_ids: list[str]) -> None`
- Produces: `change_with_audit(conn, entity_type: str, entity_id: str, table: str, field: str, value: str, reason: str, source: str) -> None`
- Produces: `set_package_completeness(conn, package_id: str, state: str, reviewer: str) -> None`
- Produces: `corpus_completeness(conn) -> str`

- [ ] **Step 1: Write failing QC/audit tests**

```python
# tests/test_review.py
import unittest
from tests.helpers import new_test_db, seeded_provisional_finding_db, seeded_unlocated_reference_db
from metro_forensics.review import open_review_task, promote_finding_verified, set_package_completeness, corpus_completeness
from metro_forensics.evidence import set_reference_absence_scope

class ReviewTests(unittest.TestCase):
    def test_open_material_review_blocks_verified_complete(self):
        db = new_test_db()
        db.execute("INSERT INTO package(package_id,control_record_path,expected_level1_count) VALUES('P1','1.pdf',0)")
        open_review_task(db, "PACKAGE", "P1", "MATERIAL_AMBIGUITY", "needs source review", True)
        with self.assertRaises(ValueError):
            set_package_completeness(db, "P1", "VERIFIED_COMPLETE", "reviewer")

    def test_verification_transition_creates_audit_event(self):
        db, finding_id, citation_id = seeded_provisional_finding_db()
        promote_finding_verified(db, finding_id, "reviewer", "2026-08-07T12:00:00Z", [citation_id])
        self.assertEqual("VERIFIED", db.execute(
            "SELECT verification_state FROM finding WHERE finding_id=?", (finding_id,)
        ).fetchone()[0])
        self.assertEqual(1, db.execute(
            "SELECT count(*) FROM audit_event WHERE entity_type='FINDING' AND entity_id=?", (finding_id,)
        ).fetchone()[0])

    def test_not_located_corpus_rejected_while_corpus_incomplete(self):
        db, reference_id = seeded_unlocated_reference_db()
        self.assertEqual("IN_PROGRESS", corpus_completeness(db))
        with self.assertRaises(ValueError):
            set_reference_absence_scope(db, reference_id, "NOT_LOCATED_CORPUS")
```

In this task, extend `tests/helpers.py` with `seeded_provisional_finding_db()` and `seeded_unlocated_reference_db()`, built through Task 4/5 public operations so the returned graphs obey the same foreign keys as production.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_review -v`

Expected: fail until QC services exist.

- [ ] **Step 3: Implement atomic transitions**

Every substantive transition runs inside one SQLite transaction: read current value, validate gate, insert `AUDIT_EVENT`, then update the analytical row. `promote_finding_verified` requires reviewer ID/name, review timestamp, and one or more source-backed citation IDs. `resolve_review_task` requires resolution text and supporting source location unless state is `UNRESOLVED`.

```python
def corpus_completeness(conn) -> str:
    states = [r[0] for r in conn.execute("SELECT completeness_state FROM package")]
    if states and all(s == "VERIFIED_COMPLETE" for s in states):
        return "VERIFIED_COMPLETE"
    if any(s == "REVIEW_REQUIRED" for s in states):
        return "REVIEW_REQUIRED"
    if states and all(s in {"VERIFIED_COMPLETE", "COMPLETE_WITH_EXCEPTIONS"} for s in states):
        return "COMPLETE_WITH_EXCEPTIONS"
    return "IN_PROGRESS"
```

`set_reference_absence_scope(conn, reference_id, "NOT_LOCATED_CORPUS")` must call this gate and raise `ValueError` unless it returns `VERIFIED_COMPLETE`.

- [ ] **Step 4: Run QC tests to green**

Run: `python3 -m unittest tests.test_review -v`

Expected: open review tasks block verified completion; every substantive transition is auditable.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 6: Commit when running in a Git-backed checkout**

```bash
git add metro_forensics/review.py metro_forensics/schema.sql tests/test_review.py
git commit -m "feat: enforce review and completeness gates"
```

---

### Task 7: Date facts, temporal inferences, and legal-assessment separation

**Files:**
- Create: `metro_forensics/temporal_legal.py`
- Create: `tests/test_temporal_legal.py`
- Modify: `metro_forensics/schema.sql`
- Modify: `tests/helpers.py`

**Interfaces:**
- Consumes: citations and verified findings.
- Produces: `add_date_fact(conn, entity_type: str, entity_id: str, date_role: str, raw_value: str, normalized_value: str | None, precision: str, citation_id: str) -> str`
- Produces: `add_temporal_inference(conn, entity_type: str, entity_id: str, inference_type: str, supporting_date_fact_ids: list[str]) -> str`
- Produces: `create_legal_assessment(conn, legal_question: str, conclusion: str, finding_ids: list[str], authorities: list[tuple[str, str]]) -> str`
- Produces: `finalize_legal_assessment(conn, assessment_id: str) -> None`

- [ ] **Step 1: Write failing temporal/legal tests**

```python
# tests/test_temporal_legal.py
import unittest
from tests.helpers import seeded_citation_db, seeded_provisional_finding_db
from metro_forensics.temporal_legal import add_date_fact, add_temporal_inference, create_legal_assessment, finalize_legal_assessment

class TemporalLegalTests(unittest.TestCase):
    def test_existence_inference_does_not_create_possession_inference(self):
        db, citation_id = seeded_citation_db()
        date_id = add_date_fact(db, "RECORD", "R1", "RECORD_DATE", "2026-07-01", "2026-07-01", "DAY", citation_id)
        add_temporal_inference(db, "RECORD", "R1", "EXISTED_BEFORE_RESPONSE", [date_id])
        kinds = {r[0] for r in db.execute("SELECT inference_type FROM temporal_inference")}
        self.assertEqual({"EXISTED_BEFORE_RESPONSE"}, kinds)
        self.assertNotIn("POSSESSED_AT_RESPONSE", kinds)

    def test_partial_and_conflicting_dates_are_preserved(self):
        db, citation_id = seeded_citation_db()
        add_date_fact(db, "RECORD", "R1", "RECORD_DATE", "July 2026", "2026-07", "MONTH", citation_id)
        add_date_fact(db, "RECORD", "R1", "RECORD_DATE", "2026 or 2025", None, "CONFLICTING", citation_id)
        rows = list(db.execute("SELECT raw_value,normalized_value,precision FROM date_fact ORDER BY rowid"))
        self.assertEqual(("July 2026", "2026-07", "MONTH"), tuple(rows[0]))
        self.assertEqual(("2026 or 2025", None, "CONFLICTING"), tuple(rows[1]))

    def test_final_legal_assessment_rejects_provisional_finding(self):
        db, finding_id, _ = seeded_provisional_finding_db()
        assessment = create_legal_assessment(
            db, "Was the legal duty satisfied?", "draft conclusion",
            [finding_id], [("STATUTE", "Ohio Rev. Code § 149.43")],
        )
        with self.assertRaises(ValueError):
            finalize_legal_assessment(db, assessment)
```

In this task, extend `tests/helpers.py` with `seeded_citation_db()`, which creates a minimal source/record/occurrence/citation graph through the public Task 4/5 operations.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_temporal_legal -v`

Expected: fail until services exist.

- [ ] **Step 3: Implement sourced dates and guarded conclusions**

```python
def finalize_legal_assessment(conn, assessment_id: str) -> None:
    bad = conn.execute(
        "SELECT count(*) FROM legal_assessment_finding laf "
        "JOIN finding f ON f.finding_id=laf.finding_id "
        "WHERE laf.assessment_id=? AND f.verification_state <> 'VERIFIED'",
        (assessment_id,),
    ).fetchone()[0]
    if bad:
        raise ValueError("final legal assessment requires VERIFIED findings only")
    authority_count = conn.execute(
        "SELECT count(*) FROM legal_authority WHERE assessment_id=?", (assessment_id,)
    ).fetchone()[0]
    if authority_count == 0:
        raise ValueError("final legal assessment requires cited authority")
    conn.execute("UPDATE legal_assessment SET status='FINAL' WHERE assessment_id=?", (assessment_id,))
```

Task 1 already declares `legal_authority`; this service writes one row per cited authority linked to its assessment. Date values must retain `raw_value`, normalized sortable value only when justified, `precision` (`DAY`, `MONTH`, `YEAR`, `APPROXIMATE`, `CONFLICTING`), and citation. Never synthesize a missing exact day.

- [ ] **Step 4: Run temporal/legal tests to green**

Run: `python3 -m unittest tests.test_temporal_legal -v`

Expected: temporal and legal separation tests pass.

- [ ] **Step 5: Commit when running in a Git-backed checkout**

```bash
git add metro_forensics/temporal_legal.py metro_forensics/schema.sql tests/test_temporal_legal.py
git commit -m "feat: separate temporal and legal analysis"
```

---

### Task 8: Reproducible SQL-backed reports and counting-unit reconciliation

**Files:**
- Create: `metro_forensics/report.py`
- Create: `tests/test_report.py`
- Create: `metro_forensics/views.sql`
- Modify: `tests/helpers.py`

**Interfaces:**
- Consumes: canonical ledger only.
- Produces: `generate_reports(conn, output_dir: Path) -> list[Path]`
- Produces: named SQL views for package inventory, request crosswalk, record inventory, occurrences, unresolved references, existence conflicts, withholding/redaction, review queue, audit history, legal assessments.

- [ ] **Step 1: Write failing report reconciliation tests**

```python
# tests/test_report.py
import csv
import tempfile
import unittest
from pathlib import Path
from tests.helpers import seeded_duplicate_occurrence_db
from metro_forensics.report import generate_reports

class ReportTests(unittest.TestCase):
    def test_unique_records_and_occurrences_are_separate_counts(self):
        db = seeded_duplicate_occurrence_db()
        row = db.execute("SELECT unique_level2_records,level2_occurrences FROM v_summary_counts").fetchone()
        self.assertEqual((1, 2), tuple(row))

    def test_exports_reconcile_to_sqlite(self):
        db = seeded_duplicate_occurrence_db()
        with tempfile.TemporaryDirectory() as td:
            paths = generate_reports(db, Path(td))
            occurrence_csv = Path(td) / "occurrences.csv"
            with occurrence_csv.open(newline="", encoding="utf-8") as f:
                exported = list(csv.DictReader(f))
            expected = db.execute("SELECT count(*) FROM v_occurrences").fetchone()[0]
            self.assertEqual(expected, len(exported))
            self.assertIn(occurrence_csv, paths)
```

In this task, extend `tests/helpers.py` with `seeded_duplicate_occurrence_db()`, using Task 4 public operations to create one canonical record with two preserved occurrences.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_report -v`

Expected: fail until views/report generator exist.

- [ ] **Step 3: Implement views and deterministic exports**

`views.sql` must expose separate units for package Level 1 counts, unique Level 2 records, occurrences, referenced-but-unproduced items, finding verification state, review state, and package completeness. Include `v_summary_counts` with explicit `unique_level2_records` and `level2_occurrences` columns. Reports must never use a generic `document_count` column.

```python
REPORTS = {
    "package_inventory.csv": "v_package_inventory",
    "request_element_crosswalk.csv": "v_request_element_crosswalk",
    "level2_records.csv": "v_level2_records",
    "occurrences.csv": "v_occurrences",
    "referenced_not_located.csv": "v_referenced_not_located",
    "existence_conflicts.csv": "v_existence_conflicts",
    "withholding_redaction.csv": "v_withholding_redaction",
    "review_queue.csv": "v_review_queue",
    "audit_history.csv": "v_audit_history",
    "legal_assessments.csv": "v_legal_assessments",
}
```

Sort every export by stable IDs/declared keys so reruns are byte-stable when the ledger is unchanged. Generate `summary.md` from the same query results and label every total with unit and scope.

- [ ] **Step 4: Run report tests to green**

Run: `python3 -m unittest tests.test_report -v`

Expected: exported totals exactly match direct SQLite queries.

- [ ] **Step 5: Commit when running in a Git-backed checkout**

```bash
git add metro_forensics/report.py metro_forensics/views.sql tests/test_report.py
git commit -m "feat: add reproducible forensic reports"
```

---

### Task 9: CLI orchestration and safe current-corpus intake checkpoint

**Files:**
- Create: `metro_forensics/cli.py`
- Modify: `metro_forensics/__init__.py`
- Modify: `tests/test_current_corpus.py`
- Create: `README.md`

**Interfaces:**
- Consumes: all prior task services.
- Produces commands: `init`, `ingest`, `process`, `report`, `qc`.
- Produces runtime ledger at `analysis/metro_forensics.sqlite3` only when execution begins.

- [ ] **Step 1: Write failing CLI/current-corpus safety tests**

Add tests that invoke the CLI with a temporary analysis directory and assert:

```python
self.assertEqual({"PKG_1": 24, "PKG_2": 72, "PKG_3": 0}, package_counts)
self.assertEqual("NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED", pkg3_status)
self.assertEqual(before_hashes, after_hashes)  # hashes of all five upload/ inputs
```

Also assert `qc` reports `IN_PROGRESS` after intake alone; ingestion must not falsely claim `VERIFIED_COMPLETE`.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_current_corpus -v`

Expected: CLI-specific tests fail until orchestration exists.

- [ ] **Step 3: Implement the thin CLI**

```python
# metro_forensics/cli.py
def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)
```

Required commands and behavior:

- `python3 -m metro_forensics.cli init --db analysis/metro_forensics.sqlite3` creates only the ledger/schema.
- `python3 -m metro_forensics.cli ingest --db analysis/metro_forensics.sqlite3 --manifest config/corpus.json --evidence-root upload` inventories the packages and hard-fails on any count mismatch.
- `python3 -m metro_forensics.cli process --db analysis/metro_forensics.sqlite3 --evidence-root upload --derivative-root analysis/derivatives` creates processing runs/derivatives and review exceptions but no `VERIFIED` material findings.
- `python3 -m metro_forensics.cli report --db analysis/metro_forensics.sqlite3 --output analysis/reports` regenerates all reports from SQLite.
- `python3 -m metro_forensics.cli qc --db analysis/metro_forensics.sqlite3` prints per-package completeness plus corpus completeness and returns nonzero if a requested `--require-verified-complete` gate is not satisfied.

`README.md` must document that evidence under `upload/` is read-only, how to run each stage, how reviewers resolve tasks/promote findings with citations, how to regenerate reports, and why the `.doc` remains an explicit review exception in this environment.

- [ ] **Step 4: Run the entire automated suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass, including 24/72/0 and upload-hash immutability assertions.

- [ ] **Step 5: Run the intake-only checkpoint against the real current corpus**

Run:

```bash
python3 -m metro_forensics.cli init --db analysis/metro_forensics.sqlite3
python3 -m metro_forensics.cli ingest --db analysis/metro_forensics.sqlite3 --manifest config/corpus.json --evidence-root upload
python3 -m metro_forensics.cli qc --db analysis/metro_forensics.sqlite3
```

Expected:

- `PKG_1 Level1=24`
- `PKG_2 Level1=72`
- `PKG_3 Level1=0 status=NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED`
- corpus state remains `IN_PROGRESS` until extraction/review gates are actually satisfied.

This checkpoint inventories/hashes Level 1 evidence only. It does not begin substantive Level 2 classification.

- [ ] **Step 6: Commit when running in a Git-backed checkout**

```bash
git add metro_forensics/cli.py metro_forensics/__init__.py tests/test_current_corpus.py README.md
git commit -m "feat: add staged forensic CLI"
```

---

### Task 10: Acceptance matrix and first processing/review queue generation

**Files:**
- Modify: `tests/test_current_corpus.py`
- Modify: `README.md`
- Generate during execution: `analysis/metro_forensics.sqlite3`
- Generate during execution: `analysis/derivatives/`
- Generate during execution: `analysis/reports/`

**Interfaces:**
- Consumes: completed Tasks 1–9.
- Produces: a reproducible, review-gated analytical corpus state; no final legal conclusion is required by this task.

- [ ] **Step 1: Encode the remaining spec acceptance cases as integration tests**

Add explicit integration tests covering all 18 spec invariants, including:

```python
def test_acceptance_matrix(self):
    self.assertTrue(originals_unchanged())
    self.assertTrue(all_derivatives_have_source_and_run())
    self.assertTrue(all_occurrences_have_exact_source_locator())
    self.assertFalse(any_automated_material_finding_is_verified())
    self.assertFalse(any_probable_match_closes_reference())
    self.assertFalse(any_cross_package_match_recredits_original_package())
    self.assertFalse(any_existence_inference_implies_possession_without_support())
    self.assertFalse(any_final_legal_assessment_uses_provisional_findings())
    self.assertTrue(all_report_totals_reconcile())
```

Implement each helper as a direct SQL/query assertion over the fixture ledger; do not leave narrative-only checks.

- [ ] **Step 2: Run integration tests before real processing and fix only implementation defects**

Run: `python3 -m unittest tests.test_current_corpus -v`

Expected: all acceptance tests pass on fixtures/current intake state. If a test reveals an ambiguity not resolvable under Decisions 1–33, stop and return that single ambiguity to the user instead of inventing a new rule.

- [ ] **Step 3: Process the current 96 Level 1 files into traceable derivatives**

Run:

```bash
python3 -m metro_forensics.cli process --db analysis/metro_forensics.sqlite3 --evidence-root upload --derivative-root analysis/derivatives
```

Expected: each attempted source has a `PROCESSING_RUN`; successful outputs have hashed/source-mapped `DERIVATIVE`s; the unsupported `.doc`, low-confidence OCR, damaged content, and ambiguous Level 2 boundaries create `OPEN` review tasks instead of verified conclusions.

- [ ] **Step 4: Generate reports from the resulting ledger**

Run:

```bash
python3 -m metro_forensics.cli report --db analysis/metro_forensics.sqlite3 --output analysis/reports
python3 -m metro_forensics.cli qc --db analysis/metro_forensics.sqlite3
```

Expected: reports regenerate without manual edits; package and corpus completeness accurately reflect unresolved review work.

- [ ] **Step 5: Perform final automated verification before handing the review queue to the user**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m metro_forensics.cli qc --db analysis/metro_forensics.sqlite3
```

Expected: test suite passes. QC may legitimately be `REVIEW_REQUIRED` or `COMPLETE_WITH_EXCEPTIONS`; do not relabel it `VERIFIED_COMPLETE` merely to finish the task.

- [ ] **Step 6: Commit implementation changes when running in a Git-backed checkout**

```bash
git add tests/test_current_corpus.py README.md
git commit -m "test: verify forensic pipeline invariants"
```

Do not commit `analysis/` outputs as source code unless the user explicitly chooses to version those forensic artifacts in a Git-backed repository.

## Execution Order and Checkpoints

Tasks 1–9 are implementation work and must remain green task-by-task. Task 10 is the first point where substantive processing of the current 96 Level 1 files begins. The safe execution sequence is therefore:

1. Build and test schema/ingestion/provenance/services/reporting/CLI.
2. Verify the real corpus only at the metadata/count/hash intake level (24/72/0).
3. Run the full automated acceptance suite.
4. Begin derivative processing.
5. Stop at explicit human review tasks rather than auto-verifying ambiguity.
6. Regenerate reports and publish the true QC state.

No step performs final Ohio public-records legal analysis automatically. That remains a separate downstream use of `LEGAL_ASSESSMENT` after factual verification.
