"""Command-line orchestration for the read-only Metro forensic ledger workflow."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys
from typing import Callable

from metro_forensics.db import connect, initialize
from metro_forensics.extract import process_source
from metro_forensics.ingest import ingest_manifest, intake_evidence_root
from metro_forensics.report import generate_reports
from metro_forensics.review import corpus_completeness
from metro_forensics.review import open_review_task


Handler = Callable[[argparse.Namespace], int]

_SQLITE_STORAGE_FAILURE_CODES = frozenset({
    sqlite3.SQLITE_BUSY,
    sqlite3.SQLITE_CANTOPEN,
    sqlite3.SQLITE_FULL,
    sqlite3.SQLITE_IOERR,
    sqlite3.SQLITE_LOCKED,
    sqlite3.SQLITE_READONLY,
})


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit, staged command interface without running any stage."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create the SQLite ledger schema")
    init.add_argument("--db", type=Path, required=True)
    init.set_defaults(handler=_init)

    ingest = commands.add_parser("ingest", help="inventory read-only Level 1 evidence")
    ingest.add_argument("--db", type=Path, required=True)
    ingest.add_argument("--manifest", type=Path, required=True)
    ingest.add_argument("--evidence-root", type=Path, required=True)
    ingest.set_defaults(handler=_ingest)

    process = commands.add_parser("process", help="create source-mapped derivatives and review exceptions")
    process.add_argument("--db", type=Path, required=True)
    process.add_argument("--evidence-root", type=Path, required=True)
    process.add_argument("--derivative-root", type=Path, required=True)
    process.set_defaults(handler=_process)

    report = commands.add_parser("report", help="regenerate reports from the SQLite ledger")
    report.add_argument("--db", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    report.set_defaults(handler=_report)

    qc = commands.add_parser("qc", help="print package and corpus completeness")
    qc.add_argument("--db", type=Path, required=True)
    qc.add_argument("--require-verified-complete", action="store_true")
    qc.set_defaults(handler=_qc)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one stage and return a shell-compatible result code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _init(args: argparse.Namespace) -> int:
    args.db.parent.mkdir(parents=True, exist_ok=True)
    db = connect(args.db)
    try:
        initialize(db)
    finally:
        db.close()
    return 0


def _ingest(args: argparse.Namespace) -> int:
    db = _existing_ledger(args.db)
    try:
        ingest_manifest(db, args.manifest, args.evidence_root)
    finally:
        db.close()
    return 0


def _process(args: argparse.Namespace) -> int:
    db = _existing_ledger(args.db)
    failures: list[tuple[str, str]] = []
    pending_recoveries: list[
        tuple[str, sqlite3.Error, frozenset[str] | None]
    ] = []
    try:
        evidence_root = intake_evidence_root(db)
        _require_same_path(args.evidence_root, evidence_root, "evidence root")
        _require_disjoint_paths(evidence_root, args.derivative_root, "derivative root")
        source_file_ids = [
            row[0] for row in db.execute(
                "SELECT source_file_id FROM source_file ORDER BY package_id, archive_member_path, source_file_id"
            )
        ]
    finally:
        db.close()
    for source_file_id in source_file_ids:
        source_db: sqlite3.Connection | None = None
        preexisting_incomplete_run_ids: frozenset[str] | None = None
        try:
            source_db = _existing_ledger(args.db)
            preexisting_incomplete_run_ids = _incomplete_processing_run_ids(
                source_db, source_file_id
            )
            process_source(source_db, source_file_id, evidence_root, args.derivative_root)
            error = source_db.execute(
                "SELECT errors FROM processing_run WHERE source_file_id=? ORDER BY rowid DESC LIMIT 1",
                (source_file_id,),
            ).fetchone()[0]
        except sqlite3.Error as processing_error:
            if source_db is not None:
                source_db.close()
                source_db = None
            recovery_error: sqlite3.Error | None = None
            for _recovery_attempt in range(2):
                try:
                    error = _recover_processing_failure(
                        args.db,
                        source_file_id,
                        processing_error,
                        preexisting_incomplete_run_ids,
                    )
                    break
                except sqlite3.Error as caught_recovery_error:
                    recovery_error = caught_recovery_error
            else:
                processing_code = _sqlite_processing_failure_code(processing_error)
                assert recovery_error is not None
                recovery_code = _sqlite_processing_failure_code(recovery_error)
                error = (
                    f"{processing_code}: {processing_error}; "
                    f"PROCESSING_RECOVERY_FAILURE[{recovery_code}]: {recovery_error}"
                )
                pending_recoveries.append(
                    (
                        source_file_id,
                        processing_error,
                        preexisting_incomplete_run_ids,
                    )
                )
        finally:
            if source_db is not None:
                source_db.close()
        if error and error != "UNSUPPORTED_LEGACY_DOC":
            failures.append((source_file_id, error))
    for source_file_id, processing_error, preexisting_run_ids in pending_recoveries:
        try:
            recovered_error = _recover_processing_failure(
                args.db,
                source_file_id,
                processing_error,
                preexisting_run_ids,
            )
        except sqlite3.Error:
            continue
        failures = [
            (
                failed_source_file_id,
                recovered_error if failed_source_file_id == source_file_id else failed_error,
            )
            for failed_source_file_id, failed_error in failures
        ]
    for source_file_id, error in failures:
        print(f"error: processing failed for {source_file_id}: {error}", file=sys.stderr)
    return 1 if failures else 0


def _incomplete_processing_run_ids(
    db: sqlite3.Connection, source_file_id: str
) -> frozenset[str]:
    return frozenset(
        row[0]
        for row in db.execute(
            """SELECT processing_run_id FROM processing_run
               WHERE source_file_id=? AND completed_at IS NULL""",
            (source_file_id,),
        )
    )


def _sqlite_processing_failure_code(error: sqlite3.Error) -> str:
    """Separate storage/connection unavailability from schema or application failures."""
    sqlite_errorcode = getattr(error, "sqlite_errorcode", None)
    primary_code = None if sqlite_errorcode is None else sqlite_errorcode & 0xFF
    if primary_code in _SQLITE_STORAGE_FAILURE_CODES:
        return "PROCESSING_CONNECTION_FAILURE"
    return "PROCESSING_SQLITE_FAILURE"


def _recover_processing_failure(
    db_path: Path,
    source_file_id: str,
    error: sqlite3.Error,
    preexisting_incomplete_run_ids: frozenset[str] | None,
) -> str:
    """Review every SQLite failure, terminalizing only a run owned by this attempt."""
    reason_code = _sqlite_processing_failure_code(error)
    terminal_error = f"{reason_code}: {error}"
    db = _existing_ledger(db_path)
    try:
        owned_run_id = None
        if preexisting_incomplete_run_ids is not None:
            current_incomplete_run_ids = _incomplete_processing_run_ids(db, source_file_id)
            new_incomplete_run_ids = (
                current_incomplete_run_ids - preexisting_incomplete_run_ids
            )
            if len(new_incomplete_run_ids) == 1:
                owned_run_id = next(iter(new_incomplete_run_ids))
            elif len(new_incomplete_run_ids) > 1:
                terminal_error += "; RECOVERY_OWNERSHIP_AMBIGUOUS"
        with db:
            if owned_run_id is not None:
                db.execute(
                    """UPDATE processing_run
                       SET completed_at=CURRENT_TIMESTAMP, errors=?
                       WHERE processing_run_id=? AND completed_at IS NULL""",
                    (terminal_error, owned_run_id),
                )
            open_review_task(
                db,
                "SOURCE_FILE",
                source_file_id,
                reason_code,
                terminal_error,
                task_type="EXTRACTION_EXCEPTION",
            )
    finally:
        db.close()
    return terminal_error


def _report(args: argparse.Namespace) -> int:
    db = _existing_ledger(args.db)
    try:
        evidence_root = intake_evidence_root(db)
        _require_disjoint_paths(evidence_root, args.output, "report output")
        generate_reports(db, args.output)
    finally:
        db.close()
    return 0


def _qc(args: argparse.Namespace) -> int:
    db = _existing_ledger(args.db)
    try:
        for row in db.execute(
            """
            SELECT package_id, expected_level1_count, level1_source_file_count,
                   package_status, package_completeness_state
            FROM v_package_inventory
            ORDER BY package_id
            """
        ):
            status = (
                f" status={row['package_status']}"
                if row["package_status"] is not None
                else ""
            )
            print(
                f"{row['package_id']} Level1={row['level1_source_file_count']}"
                f"{status} expected={row['expected_level1_count']}"
                f" completeness={row['package_completeness_state']}"
            )
        completeness = corpus_completeness(db)
        print(f"Corpus completeness: {completeness}")
    finally:
        db.close()
    return 0 if not args.require_verified_complete or completeness == "VERIFIED_COMPLETE" else 1


def _existing_ledger(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"ledger not initialized: {path}; run init first")
    return connect(path)


def _require_disjoint_paths(evidence_root: Path, output_root: Path, output_name: str) -> None:
    evidence = evidence_root.resolve()
    output = output_root.resolve()
    if output == evidence or output.is_relative_to(evidence) or evidence.is_relative_to(output):
        raise ValueError(f"{output_name} must not overlap immutable evidence root")


def _require_same_path(supplied_root: Path, persisted_root: Path, root_name: str) -> None:
    if supplied_root.resolve() != persisted_root.resolve():
        raise ValueError(f"{root_name} does not match the ledger's immutable intake root")


if __name__ == "__main__":
    raise SystemExit(main())
