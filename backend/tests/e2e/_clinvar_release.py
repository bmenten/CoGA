"""CoGA's ClinVar release of an end-to-end test's own: the alleles it names, stored in a partition
of the assembly's ClinVar table and read as the assembly's current release while the test runs.

Every reader of a small variant's ClinVar (the family page and its export, the Variant Explorer,
the ranking, the NIPT analysis, the artifact list) reads the assembly's current release through
``clinvar_coga.clinvar_coga_read``; here that lookup answers the test's release for its assembly
(``clinvar_coga.current_clinvar_release``, and the assembly among those ClinVar publishes for),
and every other assembly as before. The record of the releases loaded in Postgres is not
touched, so no other test reads the release, and the partition is dropped at the end.
Synthetic alleles only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest


@asynccontextmanager
async def clinvar_release(assembly: str, release: date, classifications: Mapping[str, str]) -> AsyncIterator[None]:
    """While the block runs, ``assembly``'s current ClinVar release is ``release``, holding each
    ``chrom-pos-ref-alt`` allele of ``classifications`` with its classification and no other."""
    from backend.app.services import clinvar_coga
    from backend.app.services.clinvar_release_store import (
        drop_clinvar_release,
        ensure_clinvar_table,
        insert_clinvar_rows,
    )
    from backend.app.services.clinvar_releases import CLINVAR_ASSEMBLY_FOLDERS
    from backend.tests.integration._clinvar_source import release_rows

    await ensure_clinvar_table(assembly)
    await insert_clinvar_rows(assembly, release, release_rows(assembly, classifications))
    recorded = clinvar_coga.current_clinvar_release

    async def current(session: Any, assembly_name: str) -> Any:
        if assembly_name == assembly:
            return SimpleNamespace(release=release)
        return await recorded(session, assembly_name)

    try:
        with pytest.MonkeyPatch.context() as patch:
            if assembly not in CLINVAR_ASSEMBLY_FOLDERS:
                patch.setitem(CLINVAR_ASSEMBLY_FOLDERS, assembly, f"vcf_{assembly}")
            patch.setattr(clinvar_coga, "current_clinvar_release", current)
            yield
    finally:
        await drop_clinvar_release(assembly, release)
