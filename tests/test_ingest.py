import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from metro_forensics.ids import stable_id
from metro_forensics.ingest import ingest_manifest
from tests.helpers import new_test_db


class IngestTests(unittest.TestCase):
    def _media_type_for(self, member_name, member_bytes):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "1.pdf").write_bytes(b"control")
            with zipfile.ZipFile(root / "prod.zip", "w") as archive:
                archive.writestr(member_name, member_bytes)
            manifest = {"packages": [{
                "package_id": "P1", "control_record": "1.pdf",
                "production_archive": "prod.zip", "expected_level1_count": 1,
                "package_status": None,
            }]}
            (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")
            db = new_test_db()
            ingest_manifest(db, root / "corpus.json", root)
            return db.execute("SELECT media_type FROM source_file").fetchone()[0]

    def test_invalid_pdf_is_recorded_as_generic_bytes(self):
        """Fail if a PDF filename alone is treated as PDF validation."""
        self.assertEqual(
            "application/octet-stream",
            self._media_type_for("invalid.pdf", b"not a PDF"),
        )

    def test_invalid_xlsx_is_recorded_as_generic_bytes(self):
        """Fail if an XLSX filename alone is treated as OOXML validation."""
        self.assertEqual(
            "application/octet-stream",
            self._media_type_for("invalid.xlsx", b"not a ZIP container"),
        )

    def test_valid_xlsm_retains_the_macro_enabled_media_type(self):
        """Fail if a validated XLSM member is recorded as XLSX or generic bytes."""
        self.assertEqual(
            "application/vnd.ms-excel.sheet.macroEnabled.12",
            self._media_type_for("valid.xlsm", b"PK\x03\x04minimal ZIP header"),
        )

    def test_zip_members_are_level1_and_control_pdf_is_not(self):
        """Fail if the control PDF is counted or member bytes are not hashed."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "1.pdf").write_bytes(b"control")
            with zipfile.ZipFile(root / "prod.zip", "w") as archive:
                archive.writestr("a.pdf", b"A")
                archive.writestr("b.xlsx", b"B")
            manifest = {"packages": [{
                "package_id": "P1", "control_record": "1.pdf",
                "production_archive": "prod.zip", "expected_level1_count": 2,
                "package_status": None,
            }]}
            (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")
            db = new_test_db()

            ingest_manifest(db, root / "corpus.json", root)

            self.assertEqual(2, db.execute("SELECT count(*) FROM source_file").fetchone()[0])
            self.assertEqual(
                hashlib.sha256(b"A").hexdigest(),
                db.execute(
                    "SELECT sha256 FROM source_file WHERE archive_member_path='a.pdf'"
                ).fetchone()[0],
            )
            self.assertEqual(0, db.execute(
                "SELECT count(*) FROM source_file WHERE archive_member_path='1.pdf'"
            ).fetchone()[0])

    def test_ingestion_is_idempotent(self):
        """Fail if a second intake creates another Level 1 row."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "1.pdf").write_bytes(b"control")
            with zipfile.ZipFile(root / "prod.zip", "w") as archive:
                archive.writestr("only.pdf", b"same bytes")
            manifest = {"packages": [{
                "package_id": "P1", "control_record": "1.pdf",
                "production_archive": "prod.zip", "expected_level1_count": 1,
                "package_status": None,
            }]}
            (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")
            db = new_test_db()

            ingest_manifest(db, root / "corpus.json", root)
            source_id = db.execute("SELECT source_file_id FROM source_file").fetchone()[0]
            ingest_manifest(db, root / "corpus.json", root)

            self.assertEqual(1, db.execute("SELECT count(*) FROM source_file").fetchone()[0])
            self.assertEqual(source_id, db.execute(
                "SELECT source_file_id FROM source_file"
            ).fetchone()[0])

    def test_count_mismatch_rolls_back_intake(self):
        """Fail if a declared package count does not make intake abort atomically."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "1.pdf").write_bytes(b"control")
            with zipfile.ZipFile(root / "prod.zip", "w") as archive:
                archive.writestr("only.pdf", b"same bytes")
            manifest = {"packages": [{
                "package_id": "P1", "control_record": "1.pdf",
                "production_archive": "prod.zip", "expected_level1_count": 2,
                "package_status": None,
            }]}
            (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")
            db = new_test_db()

            with self.assertRaisesRegex(ValueError, "count"):
                ingest_manifest(db, root / "corpus.json", root)

            self.assertEqual(0, db.execute("SELECT count(*) FROM package").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM source_file").fetchone()[0])

    def test_traversal_member_is_rejected_without_intake(self):
        """Fail if an unsafe ZIP member path is accepted as evidence metadata."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "1.pdf").write_bytes(b"control")
            with zipfile.ZipFile(root / "prod.zip", "w") as archive:
                archive.writestr("../outside.pdf", b"unsafe")
            manifest = {"packages": [{
                "package_id": "P1", "control_record": "1.pdf",
                "production_archive": "prod.zip", "expected_level1_count": 1,
                "package_status": None,
            }]}
            (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")
            db = new_test_db()

            with self.assertRaisesRegex(ValueError, "unsafe"):
                ingest_manifest(db, root / "corpus.json", root)

            self.assertEqual(0, db.execute("SELECT count(*) FROM source_file").fetchone()[0])

    def test_stable_id_is_reproducible_and_scoped(self):
        """Fail if identical evidence coordinates do not retain their stable identity."""
        self.assertEqual(
            "SF_937ab14c4b7a24568088fa6f",
            stable_id("SF", "P1", "a.pdf", "a" * 64),
        )
        self.assertNotEqual(
            stable_id("SF", "P1", "a.pdf", "a" * 64),
            stable_id("SF", "P2", "a.pdf", "a" * 64),
        )
