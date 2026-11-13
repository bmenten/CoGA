"""The reference data an assembly gets without being asked: its clinical CNV knowledgebase and its
DGV variants, for a GRCh37 or GRCh38 assembly that has neither yet.

Adding an assembly from UCSC loads its cytobands and genes (``reference_source_service``). For
an assembly the clinical CNV knowledgebase build knows, and one with a pinned DGV release, the
import then queues the knowledgebase build (``clinical_cnv_kb_jobs``) and the DGV load
(``dgv_load_jobs``) as the importing administrator; a start does the same for such an assembly
already there, as no account (``REFERENCE_BOOTSTRAP_CLINICAL_CNVS``, ``REFERENCE_BOOTSTRAP_DGV``).
Each runs in the background as the Reference page's rebuild and load buttons run it, and an
assembly that already has the data gets nothing new: a later release is the administrator's
step, on that page.
"""

from __future__ import annotations

import logging
from contextlib import suppress

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import describe_error
from ..core.config import DGV_PINNED_RELEASES, settings
from .access_control import CurrentUser
from .clinical_cnv_kb_jobs import KNOWLEDGEBASE_ASSEMBLIES, queue_clinical_cnv_kb_rebuild_if_missing
from .dgv_load_jobs import queue_dgv_load_if_missing

logger = logging.getLogger(__name__)


async def queue_missing_reference_loads(
    session: AsyncSession,
    *,
    assembly_name: str,
    requested_by: CurrentUser | None,
    clinical_cnvs: bool = True,
    dgv: bool = True,
) -> list[str]:
    """Queue the clinical CNV knowledgebase build and the DGV load for the assembly, each only if
    it has none of that data yet, and return what was started (or why not) as sentences."""
    notes: list[str] = []
    if clinical_cnvs:
        _job, note = await queue_clinical_cnv_kb_rebuild_if_missing(
            session, assembly_name=assembly_name, requested_by=requested_by
        )
        if note:
            notes.append(note)
    if dgv:
        _dgv_job, note = await queue_dgv_load_if_missing(
            session, assembly_name=assembly_name, requested_by=requested_by
        )
        if note:
            notes.append(note)
    return notes


async def _rollback_quietly(session: AsyncSession) -> None:
    """End the failed transaction, so the start's later steps get a usable session; a rollback
    that fails too is left to them."""
    with suppress(Exception):
        await session.rollback()


async def queue_startup_reference_loads(session: AsyncSession) -> None:
    """At a start, queue what a GRCh37 or GRCh38 assembly lacks (the bootstrap assembly first),
    as no account. Never stops the start: a failure is logged, and the Reference page offers the
    rebuild and the load."""
    if not settings.reference_bootstrap_enabled:
        return
    if not (settings.reference_bootstrap_clinical_cnvs or settings.reference_bootstrap_dgv):
        return
    names = sorted(
        set(KNOWLEDGEBASE_ASSEMBLIES) | set(DGV_PINNED_RELEASES),
        key=lambda name: (name != settings.reference_bootstrap_assembly_name, name),
    )
    try:
        present = set((await session.execute(text("SELECT assembly_name FROM assemblies"))).scalars())
    except Exception as exc:  # noqa: BLE001 - a start never stops on it; logged by type
        logger.error("Could not read the assemblies to complete their reference data (%s)", describe_error(exc))
        await _rollback_quietly(session)
        return
    for name in names:
        if name not in present:
            continue
        try:
            notes = await queue_missing_reference_loads(
                session,
                assembly_name=name,
                requested_by=None,
                clinical_cnvs=settings.reference_bootstrap_clinical_cnvs,
                dgv=settings.reference_bootstrap_dgv,
            )
        except Exception as exc:  # noqa: BLE001 - a start never stops on it; logged by type
            logger.error("Could not queue the reference data %s lacks (%s)", name, describe_error(exc))
            await _rollback_quietly(session)
            continue
        for note in notes:
            logger.info("%s", note)
