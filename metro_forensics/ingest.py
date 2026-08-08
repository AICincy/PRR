import hashlib
import json
import mimetypes
from pathlib import Path, PurePosixPath
import sqlite3
import zipfile

from metro_forensics.ids import stable_id


ZERO_PRODUCTION_STATUS = "NO_OUTPUT_PRODUCTION_RESPONSE_RECEIVED"
INTAKE_EVIDENCE_ROOT_KEY = "intake_evidence_root"


def ingest_manifest(
    conn: sqlite3.Connection, manifest_path: Path, evidence_root: Path
) -> None:
    """Intake a package manifest without modifying its source evidence."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    packages = manifest.get("packages")
    if not isinstance(packages, list):
        raise ValueError("manifest packages must be a list")

    resolved_root = evidence_root.resolve()
    with conn:
        _bind_intake_evidence_root(conn, resolved_root)
        for package in packages:
            _ingest_package(conn, package, resolved_root)


def intake_evidence_root(conn: sqlite3.Connection) -> Path:
    """Return the immutable canonical evidence root that successfully populated this ledger."""
    try:
        row = conn.execute(
            "SELECT value FROM operational_metadata WHERE key=?",
            (INTAKE_EVIDENCE_ROOT_KEY,),
        ).fetchone()
    except sqlite3.OperationalError as error:
        raise ValueError(
            "ledger lacks operational intake metadata; run init and re-ingest before processing or reporting"
        ) from error
    if row is None:
        raise ValueError(
            "ledger has no canonical intake evidence root; re-ingest before processing or reporting"
        )
    return Path(row[0])


def inventory_archive(conn: sqlite3.Connection, package_id: str, archive: Path) -> int:
    """Record read-only Level 1 metadata for non-directory ZIP members."""
    if not archive.is_file():
        raise FileNotFoundError(f"production archive not found: {archive}")

    count = 0
    member_paths: set[str] = set()
    with zipfile.ZipFile(archive) as zip_archive:
        for member in zip_archive.infolist():
            _validate_member_name(member.filename)
            if member.is_dir():
                continue
            if member.filename in member_paths:
                raise ValueError(f"duplicate ZIP member path: {member.filename}")
            member_paths.add(member.filename)

            sha256, byte_size, leading_bytes = _hash_member(zip_archive, member)
            media_type = _media_type(member.filename, leading_bytes)
            existing = conn.execute(
                """
                SELECT sha256, byte_size, media_type FROM source_file
                WHERE package_id=? AND archive_member_path=?
                """,
                (package_id, member.filename),
            ).fetchone()
            if existing is not None and tuple(existing) != (sha256, byte_size, media_type):
                raise ValueError(
                    f"archive member provenance drift for {package_id}:{member.filename}"
                )
            source_file_id = stable_id(
                "SF", package_id, member.filename, sha256
            )
            conn.execute(
                """
                INSERT INTO source_file(
                    source_file_id, package_id, archive_member_path,
                    byte_size, sha256, media_type
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(package_id, archive_member_path, sha256) DO NOTHING
                """,
                (
                    source_file_id,
                    package_id,
                    member.filename,
                    byte_size,
                    sha256,
                    media_type,
                ),
            )
            count += 1
    existing_paths = {
        row[0]
        for row in conn.execute(
            "SELECT archive_member_path FROM source_file WHERE package_id=?", (package_id,)
        )
    }
    if existing_paths != member_paths:
        raise ValueError(f"archive member path inventory drift for {package_id}")
    return count


def _ingest_package(
    conn: sqlite3.Connection, package: object, evidence_root: Path
) -> None:
    if not isinstance(package, dict):
        raise ValueError("each manifest package must be an object")

    package_id = package.get("package_id")
    control_record = package.get("control_record")
    archive_name = package.get("production_archive")
    expected_count = package.get("expected_level1_count")
    package_status = package.get("package_status")
    if not isinstance(package_id, str) or not package_id:
        raise ValueError("package_id must be a non-empty string")
    if not isinstance(control_record, str) or not control_record:
        raise ValueError("control_record must be a non-empty string")
    if not isinstance(expected_count, int) or expected_count < 0:
        raise ValueError("expected_level1_count must be a non-negative integer")

    control_path = _evidence_path(evidence_root, control_record)
    if not control_path.is_file():
        raise FileNotFoundError(f"control record not found: {control_path}")

    package_metadata = (control_record, archive_name, package_status, expected_count)
    existing_package = conn.execute(
        """
        SELECT control_record_path, production_archive_path, package_status,
               expected_level1_count
        FROM package WHERE package_id=?
        """,
        (package_id,),
    ).fetchone()
    if existing_package is not None and tuple(existing_package) != package_metadata:
        raise ValueError(f"package metadata drift for {package_id}")

    conn.execute(
        """
        INSERT INTO package(
            package_id, control_record_path, production_archive_path,
            package_status, expected_level1_count
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(package_id) DO NOTHING
        """,
        (package_id, *package_metadata),
    )

    if archive_name is None:
        if expected_count != 0 or package_status != ZERO_PRODUCTION_STATUS:
            raise ValueError(
                "a missing archive is allowed only for the zero-production package"
            )
        return
    if not isinstance(archive_name, str) or not archive_name:
        raise ValueError("production_archive must be a string or null")

    actual_count = inventory_archive(
        conn, package_id, _evidence_path(evidence_root, archive_name)
    )
    if actual_count != expected_count:
        raise ValueError(
            f"Level 1 count mismatch for {package_id}: "
            f"expected {expected_count}, found {actual_count}"
        )


def _bind_intake_evidence_root(conn: sqlite3.Connection, evidence_root: Path) -> None:
    """Bind a ledger to its first successfully inventoried canonical evidence root."""
    root = str(evidence_root)
    row = conn.execute(
        "SELECT value FROM operational_metadata WHERE key=?",
        (INTAKE_EVIDENCE_ROOT_KEY,),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO operational_metadata(key, value) VALUES (?, ?)",
            (INTAKE_EVIDENCE_ROOT_KEY, root),
        )
    elif row[0] != root:
        raise ValueError("ledger is already bound to a different immutable evidence root")


def _evidence_path(evidence_root: Path, relative_name: str) -> Path:
    root = evidence_root.resolve()
    candidate = (root / relative_name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"evidence path escapes root: {relative_name}") from error
    return candidate


def _validate_member_name(name: str) -> None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if normalized.startswith("/") or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe ZIP member path: {name}")
    if len(normalized) >= 3 and normalized[1] == ":" and normalized[0].isalpha():
        raise ValueError(f"unsafe ZIP member path: {name}")


def _hash_member(
    zip_archive: zipfile.ZipFile, member: zipfile.ZipInfo
) -> tuple[str, int, bytes]:
    digest = hashlib.sha256()
    byte_size = 0
    leading_bytes = b""
    with zip_archive.open(member) as member_stream:
        while chunk := member_stream.read(1024 * 1024):
            if len(leading_bytes) < 16:
                leading_bytes += chunk[: 16 - len(leading_bytes)]
            digest.update(chunk)
            byte_size += len(chunk)
    return digest.hexdigest(), byte_size, leading_bytes


def _media_type(member_name: str, leading_bytes: bytes) -> str:
    suffix = Path(member_name).suffix.lower()
    if suffix == ".pdf":
        return (
            "application/pdf"
            if leading_bytes.startswith(b"%PDF-")
            else "application/octet-stream"
        )
    if suffix == ".xlsx":
        return (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if leading_bytes.startswith(b"PK\x03\x04")
            else "application/octet-stream"
        )
    if suffix == ".xlsm":
        return (
            "application/vnd.ms-excel.sheet.macroEnabled.12"
            if leading_bytes.startswith(b"PK\x03\x04")
            else "application/octet-stream"
        )
    return mimetypes.guess_type(member_name)[0] or "application/octet-stream"
