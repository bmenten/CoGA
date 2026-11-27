"""Which ClinVar releases CoGA holds for an assembly, and which one is current.

A load (``clinvar_load_jobs``) records its release in ``reference_dataset_imports``
(``dataset_type = 'clinvar'``) only once the whole release is stored and checked, so the
releases recorded there are the releases that were complete. The newest of them is the current
release, which every screen reads; ClickHouse keeps it and the one before it (``KEPT_RELEASES``)
and drops the others' partitions.

ClinVar publishes a VCF for GRCh37 and GRCh38 only (``CLINVAR_ASSEMBLY_FOLDERS``): another
assembly has no ClinVar release.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The assemblies ClinVar publishes a weekly VCF for, by the folder NCBI keeps it in.
CLINVAR_ASSEMBLY_FOLDERS: dict[str, str] = {"GRCh38": "vcf_GRCh38", "GRCh37": "vcf_GRCh37"}

# How many releases ClickHouse keeps per assembly: the current one and the one before it.
KEPT_RELEASES = 2

CLINVAR_DATASET_TYPE = "clinvar"

# A ClinVar file's release as the manifest's ``clinvar`` module states it: ``20261101``, the
# date in the file's name, or a version line's ``2026-11-01``.
_RELEASE = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})")


@dataclass(frozen=True, slots=True)
class ClinvarRelease:
    """A release recorded for an assembly: its date, the rows stored, and where it came from."""

    release: date
    records: int
    source_url: str | None
    # The release, the file and its SHA-256 (``clinvar_load_jobs.recorded_release``).
    source_version: str
    loaded_at: datetime
    loaded_by: str | None


async def recorded_clinvar_releases(
    session: AsyncSession, assembly_name: str, *, limit: int = KEPT_RELEASES
) -> list[ClinvarRelease]:
    """The assembly's recorded releases, newest first: the first is the current one. At most
    ``limit``; none for an assembly without a ClinVar release."""
    rows = (
        await session.execute(
            text(
                """
                SELECT r.source_release_date AS release, r.inserted AS records, r.source_url,
                       r.source_version, r.performed_at AS loaded_at, r.performed_by AS loaded_by
                FROM reference_dataset_imports r
                JOIN assemblies a ON a.id = r.assembly_id
                WHERE a.assembly_name = :assembly AND r.dataset_type = :dataset_type
                ORDER BY r.source_release_date DESC, r.performed_at DESC
                LIMIT :limit
                """
            ),
            {"assembly": assembly_name, "dataset_type": CLINVAR_DATASET_TYPE, "limit": int(limit)},
        )
    ).mappings().all()
    return [
        ClinvarRelease(
            release=row["release"],
            records=int(row["records"]),
            source_url=row["source_url"],
            source_version=str(row["source_version"]),
            loaded_at=row["loaded_at"],
            loaded_by=row["loaded_by"],
        )
        for row in rows
    ]


# The lock a load makes its release current under (exclusive) and a signed record is built under
# (shared): a release made current while a record reads the current release waits for the
# record's transaction to end, so every read of one record sees one release.
_CURRENT_RELEASE_LOCK = "clinvar-current-release"


async def hold_current_clinvar_release(session: AsyncSession, *, exclusive: bool = False) -> None:
    """Hold the current-release lock to the end of the session's transaction: shared while a
    record reads the current release, exclusive while a load makes its release current."""
    lock = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    await session.execute(text(f"SELECT {lock}(hashtext(:k))"), {"k": _CURRENT_RELEASE_LOCK})


async def current_clinvar_release(session: AsyncSession, assembly_name: str) -> ClinvarRelease | None:
    """The assembly's current release: the newest recorded. None when it has none."""
    releases = await recorded_clinvar_releases(session, assembly_name, limit=1)
    return releases[0] if releases else None


def annotated_clinvar_release(modules: Mapping[str, Mapping[str, Any]]) -> date | None:
    """The release of the ClinVar file a callset was annotated with: its manifest's ``clinvar``
    module's version as a date. None for a callset annotated without one, or whose release is
    not a date."""
    version = str((modules.get("clinvar") or {}).get("version") or "").strip()
    match = _RELEASE.fullmatch(version)
    if match is None:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None
