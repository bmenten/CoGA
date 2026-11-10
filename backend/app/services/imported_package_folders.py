"""The families imported from each package folder the import page lists.

A package folder stays on the share it was delivered to: CoGA never moves it, and serves the
alignments and the QC report from it after the import. The import page's list of the folders in
the import roots therefore keeps every folder ever imported, and a folder that was imported reads
like one that waits. Each family a package import registered names the folder it was imported
from (``families.metadata.package_import.folder_path``: a local folder's real path, or a bucket
folder's URI), so the list is told, per folder, which families were imported from it, when their
last import registered them, and whether that import did not complete or is still running
(``import_incomplete``, ``import_unfinished``). Nothing in the folders is read: the record is the
family's.

A folder whose last import was undone because the package changed while it was read
(``package_changed_during_import``, the owner's decision of 2026-11-05) is offered again: the
list says so (``folders_changed_during_import``), by the folder its latest import job names.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import ImportedPackageFamily

_IMPORTED_FROM = text(
    """
    SELECT family_id,
           metadata -> 'package_import' ->> 'folder_path' AS folder_path,
           metadata -> 'package_import' ->> 'registered_at' AS registered_at,
           metadata -> 'import_incomplete' AS import_incomplete,
           metadata -> 'import_unfinished' AS import_unfinished
    FROM families
    WHERE rtrim(metadata -> 'package_import' ->> 'folder_path', '/') = ANY(CAST(:folders AS text[]))
    ORDER BY family_id
    """
)


def folder_key(folder_path: str) -> str:
    """A folder as the list and a package record are compared by: a bucket folder's URI without
    the ``/`` it may end with (a local folder's real path ends with none)."""
    return folder_path.rstrip("/") or folder_path


def _registered_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _import_incomplete(flag: Any, unfinished: Any) -> bool:
    """Whether the family's last import did not complete or is still running, as the family page
    and the ancestry PCA tell it: it is flagged import-incomplete, or carries an import's mark."""
    return flag not in (None, False) or bool(unfinished)


async def imported_package_folders(
    session: AsyncSession, folder_paths: Iterable[str]
) -> dict[str, list[ImportedPackageFamily]]:
    """The families imported from each of ``folder_paths``, by the folder as given, in family-ID
    order; a folder no family was imported from is left out."""
    asked: dict[str, list[str]] = {}
    for folder_path in folder_paths:
        asked.setdefault(folder_key(folder_path), []).append(folder_path)
    if not asked:
        return {}
    rows = (await session.execute(_IMPORTED_FROM, {"folders": list(asked)})).mappings().all()
    found: dict[str, list[ImportedPackageFamily]] = {}
    for row in rows:
        family = ImportedPackageFamily(
            family_id=str(row["family_id"]),
            registered_at=_registered_at(row["registered_at"]),
            import_incomplete=_import_incomplete(row["import_incomplete"], row["import_unfinished"]),
        )
        for folder_path in asked.get(folder_key(str(row["folder_path"])), []):
            found.setdefault(folder_path, []).append(family)
    return found


_LATEST_IMPORT_JOBS = text(
    """
    SELECT DISTINCT ON (rtrim(submitted_path, '/'))
           rtrim(submitted_path, '/') AS folder,
           validation_errors @> CAST(:changed AS jsonb) AS changed
    FROM family_import_jobs
    WHERE NOT dry_run
      AND rtrim(submitted_path, '/') = ANY(CAST(:folders AS text[]))
    ORDER BY rtrim(submitted_path, '/'), requested_at DESC
    """
)

# The validation error an import job ends with when its import was undone because the package
# changed while it was read (family_package_import.PACKAGE_CHANGED_DURING_IMPORT).
_CHANGED_DURING_IMPORT = json.dumps([{"code": "package_changed_during_import"}])


async def folders_changed_during_import(session: AsyncSession, folder_paths: Iterable[str]) -> set[str]:
    """The folders of ``folder_paths``, as given, whose latest import (no dry run) was undone
    because the package changed while it was read: the list offers them again. A job is found by
    the folder it was asked for, which is the listed one when it was chosen from the list."""
    asked: dict[str, list[str]] = {}
    for folder_path in folder_paths:
        asked.setdefault(folder_key(folder_path), []).append(folder_path)
    if not asked:
        return set()
    rows = (
        await session.execute(_LATEST_IMPORT_JOBS, {"folders": list(asked), "changed": _CHANGED_DURING_IMPORT})
    ).mappings().all()
    return {folder_path for row in rows if row["changed"] for folder_path in asked.get(str(row["folder"]), [])}
