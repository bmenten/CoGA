"""The diagnostic gene panel of a family's analysis (REQ-NIPT-019).

A monogenic NIPT is read and reported within a gene panel the laboratory chooses for the
pregnancy: the indication's panel, or the clinical exome's. The analyst sets it per analysis
(``family_analysis_gene_panels``, one row per family and analysis key) and may change it; every
change is a clinical audit event (``diagnostic_panel_set``) on the family's chain, with the panel
before and after. The panels are CoGA's own gene panels, edited and versioned as they are for
every other analysis: the analysis names the panel, and reads the version it holds now. A
sign-out freezes that version, its gene list's digest and the target coverage over its genes
(``signed_record_nipt``), so a panel edited since shows as a change.

A panel that is an analysis's diagnostic panel cannot be deleted (the table's foreign key is
RESTRICT; ``panel_metadata_service.delete_panel_data`` refuses it with 409).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import canonical_uuid
from .access_control import CurrentUser
from .clinical_audit_service import audit_actor, record_clinical_event
from .hash_chain import canonical_hash
from .panel_use_audit import PANEL_SQL, describe_panel, panel_identity

DIAGNOSTIC_PANEL_ACTION = "diagnostic_panel_set"
_PANEL_NOT_FOUND = "Panel not found"

_ANALYSIS_PANEL_SQL = text(
    """
    SELECT panel_id::text AS panel_id, set_at
    FROM family_analysis_gene_panels
    WHERE family_id = CAST(:family_uuid AS uuid) AND analysis = :analysis
    """
)

_PANEL_GENES_SQL = text(
    """
    SELECT gene, symbol
    FROM gene_panel_genes
    WHERE panel_id = CAST(:panel_id AS uuid)
    ORDER BY gene
    """
)

_UPSERT_SQL = text(
    """
    INSERT INTO family_analysis_gene_panels (family_id, analysis, panel_id, set_at)
    VALUES (CAST(:family_uuid AS uuid), :analysis, CAST(:panel_id AS uuid), timezone('utc', now()))
    ON CONFLICT (family_id, analysis)
    DO UPDATE SET panel_id = EXCLUDED.panel_id, set_at = EXCLUDED.set_at
    """
)

_DELETE_SQL = text(
    """
    DELETE FROM family_analysis_gene_panels
    WHERE family_id = CAST(:family_uuid AS uuid) AND analysis = :analysis
    """
)

PANEL_IN_USE_SQL = text(
    "SELECT count(*) FROM family_analysis_gene_panels WHERE panel_id = CAST(:panel_id AS uuid)"
)


@dataclass(frozen=True, slots=True)
class AnalysisGenePanel:
    """An analysis's diagnostic panel as it is now: its identity (``panel_use_audit.panel_identity``:
    id, name, local version, source and its release), its genes as stored (an HGNC symbol or an
    Ensembl gene id), each gene's symbol, and when the analysis took it."""

    identity: dict[str, Any]
    genes: tuple[str, ...]
    gene_symbols: tuple[str, ...]
    set_at: datetime | None

    @property
    def panel_id(self) -> str:
        return str(self.identity["panel_id"])

    def frozen(self) -> dict[str, Any]:
        """What a signed record holds of it: its identity and the digest of its gene list."""
        return {**self.identity, "genes_digest": canonical_hash(list(self.genes))}


async def _panel(session: AsyncSession, panel_id: str) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]] | None:
    row = (await session.execute(PANEL_SQL, {"panel_id": panel_id})).mappings().first()
    if row is None:
        return None
    gene_rows = (await session.execute(_PANEL_GENES_SQL, {"panel_id": panel_id})).mappings().all()
    genes = tuple(str(row["gene"]) for row in gene_rows)
    # An Ensembl gene id's symbol is stored beside it; a symbol is its own.
    symbols = tuple(sorted({str(row["symbol"] or row["gene"]).strip() for row in gene_rows}))
    return panel_identity(dict(row)), genes, symbols


async def panel_gene_symbols(session: AsyncSession, panel_id: str) -> tuple[str, ...] | None:
    """A gene panel's genes by symbol, as its capture targets are named and a signed record
    reads them: an Ensembl gene id's symbol, a symbol itself (``panel_id`` canonical). None for
    no such panel."""
    panel = await _panel(session, panel_id)
    return None if panel is None else panel[2]


async def load_analysis_gene_panel(
    session: AsyncSession, *, family_uuid: str, analysis: str
) -> AnalysisGenePanel | None:
    """The analysis's diagnostic panel, None when it has none."""
    row = (
        await session.execute(_ANALYSIS_PANEL_SQL, {"family_uuid": family_uuid, "analysis": analysis})
    ).mappings().first()
    if row is None:
        return None
    panel = await _panel(session, str(row["panel_id"]))
    if panel is None:  # the foreign key keeps the panel; a race with its deletion reads as none
        return None
    identity, genes, symbols = panel
    return AnalysisGenePanel(identity=identity, genes=genes, gene_symbols=symbols, set_at=row["set_at"])


async def set_analysis_gene_panel(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    analysis: str,
    analysis_label: str,
    panel_id: str | None,
    user: CurrentUser,
) -> AnalysisGenePanel | None:
    """Set (``panel_id``) or clear (None) the analysis's diagnostic panel, record the change on
    the family's clinical audit trail in the same transaction, and commit. A panel that does not
    exist answers 404; setting the panel the analysis already has changes and records nothing."""
    panel_uuid: str | None = None
    if panel_id is not None:
        panel_uuid = canonical_uuid(panel_id)
        if panel_uuid is None or (await _panel(session, panel_uuid)) is None:
            raise HTTPException(status_code=404, detail=_PANEL_NOT_FOUND)
    # The family's audit-chain lock (record_clinical_event takes it too) before the read, so two
    # changes at once are recorded one after the other, each with the panel it replaced.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"cae:{family_identifier}"}
    )
    before = await load_analysis_gene_panel(session, family_uuid=family_uuid, analysis=analysis)
    if (before.panel_id if before else None) == panel_uuid:
        await session.commit()
        return before
    params = {"family_uuid": family_uuid, "analysis": analysis}
    if panel_uuid is None:
        await session.execute(_DELETE_SQL, params)
    else:
        await session.execute(_UPSERT_SQL, {**params, "panel_id": panel_uuid})
    after = await load_analysis_gene_panel(session, family_uuid=family_uuid, analysis=analysis)
    summary = (
        f"{describe_panel(after.identity)} set as the diagnostic panel of the {analysis_label}"
        if after is not None
        else f"Diagnostic panel of the {analysis_label} cleared"
    )
    await record_clinical_event(
        session,
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        variant_id=None,
        actor=audit_actor(user),
        actor_id=getattr(user, "id", None),
        action=DIAGNOSTIC_PANEL_ACTION,
        summary=summary,
        before=before.frozen() if before is not None else None,
        after=after.frozen() if after is not None else None,
        metadata={"analysis": analysis},
    )
    await session.commit()
    return after
