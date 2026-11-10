"""A server log line names no family and no sample by its ID (SEC-39; the owner, 2026-11-10).

AGENTS.md: logs carry no values. The family and sample IDs are the lab's identifiers of a
patient's samples, so a log line names a family by its UUID (``families.id``) or by the import
job that writes it, and a sample by its UUID or not at all. About thirty lines named the family
by its ID: the import's bookkeeping when a write failed, the upload and restore clean-ups, the
lineage and ranking warm-ups, the Sample QC warnings, the sign-out's tamper alarm; one named
the couples and children of an ambiguous pedigree, one the package path of a file named after
the family ID, one a managed copy by its path (the family's folder and the uploaded file's
name).

The guard reads every logging call in ``backend/app`` and refuses one whose arguments read a
value held under a family's or a sample's ID name. A line that must name the family passes its
UUID. The runtime tests drive the import's bookkeeping when its write fails, with a family
whose ID is a marker, and read each line it logs.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import pytest

from backend.app.services import family_package_import as fpi
from backend.app.services import family_package_registration as registration

_APP = Path(__file__).resolve().parents[1] / "app"
_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
# The names a family's or a sample's ID is held under.
_ID_NAMES = {
    "family_id",
    "family_ids",
    "family_identifier",
    "requested_family_id",
    "sample_id",
    "sample_ids",
    "sample_name",
    "sample_names",
    "vcf_sample",
}


def _logging_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _LEVELS
            and "log" in ast.unparse(node.func.value).lower()
        ):
            yield node


def _id_reads(call: ast.Call):
    """The ID names a logging call reads, in its message, its arguments or its ``extra``."""
    for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
        for node in ast.walk(argument):
            if isinstance(node, ast.Name) and node.id in _ID_NAMES:
                yield node.id
            elif isinstance(node, ast.Attribute) and node.attr in _ID_NAMES:
                yield node.attr


def test_no_log_line_reads_a_family_or_sample_id() -> None:
    lines: list[str] = []
    for path in sorted(_APP.rglob("*.py")):
        relative = path.relative_to(_APP).as_posix()
        for call in _logging_calls(ast.parse(path.read_text(encoding="utf-8"))):
            lines.extend(f"{relative}:{call.lineno} logs {name}" for name in _id_reads(call))
    # Before: some thirty lines in the import, the uploads, Sample QC, the lineage and ranking
    # warm-ups and the sign-out named the family by its ID.
    assert lines == []


def test_the_guard_finds_an_id_in_any_part_of_a_logging_call() -> None:
    source = (
        'logger.warning("Failed for family %s", context.family_id)\n'
        'logger.info(f"family {family_id}")\n'
        'logging.getLogger(__name__).error("x", extra={"family": sample_id})\n'
        'log.debug("x %s", scrub_log(vcf_sample))\n'
        'logger.warning("Failed for family %s", context.family_uuid)\n'
        'logs.append(f"Family {family_context.family_id} is registered")\n'
    )
    found = [name for call in _logging_calls(ast.parse(source)) for name in _id_reads(call)]
    # The job's own log (``logs``, which the import page shows) is no server log line.
    assert found == ["family_id", "family_id", "sample_id", "vcf_sample"]


# --- the import's bookkeeping, when its write fails ---------------------------------------

FAMILY_ID = "FAMILY-ID-MARKER"
FAMILY_UUID = "7b0e3c52-9a51-4c51-8f40-3f2a7c6d1e90"


class _FailingSession:
    """Every statement fails, with an error that names nothing of the family."""

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("the database is unreachable")

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _context() -> SimpleNamespace:
    return SimpleNamespace(family_uuid=FAMILY_UUID, family_id=FAMILY_ID, assembly_name="GRCh38")


def _mark() -> registration.ImportMark:
    return registration.ImportMark.begin(job_id="job-1", datasets=["snv"])


def _write_mark() -> registration.FamilyWriteMark:
    return registration.FamilyWriteMark.begin(registration.UPLOAD_MARK_PREFIX, [("snv", {"source": "clair3"})])


_WRITES: dict[str, Callable[[_FailingSession], Awaitable[Any]]] = {
    "the finished datasets": lambda session: registration._record_family_import_finished(
        session, _context(), _mark(), ["snv"]  # type: ignore[arg-type]
    ),
    "the failure of an import before its datasets": lambda session: registration._end_import_failed_before_datasets(
        session, family_id=FAMILY_ID, mark=_mark()  # type: ignore[arg-type]
    ),
    "the end of an import put back": lambda session: registration._end_family_import_unfinished(
        session, _context(), import_key="job-1"  # type: ignore[arg-type]
    ),
    "the import-incomplete flag": lambda session: registration._flag_family_import_incomplete(
        session, _context(), failed_datasets=["snv"], imported_datasets=[]  # type: ignore[arg-type]
    ),
    "the restore's whole-family entry": lambda session: registration._mark_family_import_restoring(
        session, _context(), _mark()  # type: ignore[arg-type]
    ),
    "the flag of a restore that failed": lambda session: registration._flag_family_restore_failed(
        session, _context(), failed_datasets=["snv"], job_id="job-1", mark=_mark()  # type: ignore[arg-type]
    ),
    "the clear of a completed import": lambda session: fpi._clear_family_import_incomplete(
        session, _context(), import_key="job-1"  # type: ignore[arg-type]
    ),
    "the end of an upload's mark": lambda session: registration.end_family_write_mark(
        session, family_uuid=FAMILY_UUID, mark=_write_mark()  # type: ignore[arg-type]
    ),
    "the flag of an upload that failed": lambda session: registration.record_family_upload_failure(
        session, family_uuid=FAMILY_UUID, dataset="snv", scope={"source": "clair3"}  # type: ignore[arg-type]
    ),
    "the narrowed mark of a deletion that failed": lambda session: registration.FamilyDeletion(
        family_uuid=FAMILY_UUID, family_id=FAMILY_ID, mark=_write_mark()
    ).failed(session),  # type: ignore[arg-type]
}


@pytest.mark.asyncio
@pytest.mark.parametrize("write", sorted(_WRITES))
async def test_a_bookkeeping_write_that_fails_logs_the_family_by_its_uuid(
    write: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=registration.logger.name)

    await _WRITES[write](_FailingSession())

    messages = [record.getMessage() for record in caplog.records if record.name == registration.logger.name]
    assert messages, "the failed write leaves its trace"
    # Before: each line named the family by its ID ("Failed to flag family FAMILY-ID-MARKER
    # as import-incomplete"). Now by its UUID, or by the import whose failure it records.
    assert not [message for message in messages if FAMILY_ID in message]
    assert all(FAMILY_UUID in message or "job-1" in message for message in messages), messages
