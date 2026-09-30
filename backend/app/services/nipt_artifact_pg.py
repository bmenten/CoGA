"""Recurrent-artifact (panel-of-normals) list for monogenic NIPT.

Artifacts are scoped per ``(assembly_id, assay_key)`` -- recurrent artifacts are
capture/chemistry-specific, so a new panel starts with a clean list. The list is
curated through the admin API (no UI), where ``auto-seed`` proposes candidates from
recurrence in the assay's own cfDNA samples; ``load_nipt_artifact_ids`` provides the
fast membership set the analysis uses to exclude (and count) artifacts. Every change to
the list is recorded in the clinical audit trail (``NIPT_ARTIFACT_AUDIT_CHAIN``).

See docs/monogenic-nipt.md.
"""

from __future__ import annotations

from typing import Any, Sequence

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .clickhouse_family_variants import fetch_recurrent_small_variant_ids
from .clinical_audit_service import record_clinical_event
from .nipt import NIPT_CFDNA_ASSAY, SAMPLE_ASSAY_PANEL_KEY, assay_key_for_sample_metadata

# A change to the artifact list changes which variants every NIPT analysis of its scope
# filters out, so each add, update, removal and auto-seed is a hash-chained clinical audit
# event (#683). They form a chain of their own, not tied to a family; verify it with
# GET /admin/integrity/verify?table=clinical_audit_events&family_id=system:nipt-artifacts.
NIPT_ARTIFACT_AUDIT_CHAIN = "system:nipt-artifacts"

_ARTIFACT_COLUMNS = """
    id::text AS id,
    assembly_id::text AS assembly_id,
    assay_key,
    variant_id,
    recurrence_count,
    source,
    label,
    created_at,
    updated_at
"""


def _artifact_state(row: dict[str, Any]) -> dict[str, Any]:
    """What an audit event records of an artifact entry (JSON-safe)."""
    return {
        "assembly_id": row.get("assembly_id"),
        "assay_key": row.get("assay_key"),
        "variant_id": row.get("variant_id"),
        "source": row.get("source"),
        "label": row.get("label"),
        "recurrence_count": row.get("recurrence_count"),
    }


async def _record_artifact_event(
    session: AsyncSession,
    *,
    actor: str,
    actor_id: str | None,
    action: str,
    summary: str,
    variant_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await record_clinical_event(
        session,
        family_uuid=None,
        family_identifier=NIPT_ARTIFACT_AUDIT_CHAIN,
        variant_id=variant_id,
        actor=actor,
        actor_id=actor_id,
        action=action,
        summary=summary,
        before=before,
        after=after,
        metadata=metadata,
    )


async def load_nipt_artifact_ids(
    session: AsyncSession,
    *,
    assembly_id: str | None,
    assay_key: str,
) -> set[str]:
    """Return the set of artifact ``variant_id``s for a scope (for fast lookup)."""
    if not assembly_id:
        return set()
    result = await session.execute(
        text(
            """
            SELECT variant_id
            FROM nipt_artifact_variants
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND assay_key = :assay_key
            """
        ),
        {"assembly_id": assembly_id, "assay_key": assay_key},
    )
    return {row[0] for row in result.all()}


async def list_nipt_artifacts(
    session: AsyncSession,
    *,
    assembly_id: str,
    assay_key: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["assembly_id = CAST(:assembly_id AS uuid)"]
    params: dict[str, Any] = {"assembly_id": assembly_id}
    if assay_key:
        clauses.append("assay_key = :assay_key")
        params["assay_key"] = assay_key
    result = await session.execute(
        text(
            f"""
            SELECT {_ARTIFACT_COLUMNS}
            FROM nipt_artifact_variants
            WHERE {' AND '.join(clauses)}
            ORDER BY assay_key, variant_id
            """
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]


async def add_nipt_artifact(
    session: AsyncSession,
    *,
    assembly_id: str,
    assay_key: str,
    variant_id: str,
    label: str | None = None,
    source: str = "curated",
    recurrence_count: int = 0,
    created_by: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Insert (or update on conflict) an artifact within its scope, and audit it."""
    existing = (
        await session.execute(
            text(
                f"""
                SELECT {_ARTIFACT_COLUMNS}
                FROM nipt_artifact_variants
                WHERE assembly_id = CAST(:assembly_id AS uuid)
                  AND assay_key = :assay_key
                  AND variant_id = :variant_id
                """
            ),
            {"assembly_id": assembly_id, "assay_key": assay_key, "variant_id": variant_id},
        )
    ).mappings().first()
    result = await session.execute(
        text(
            f"""
            INSERT INTO nipt_artifact_variants (
                assembly_id, assay_key, variant_id, recurrence_count, source, label, created_by
            ) VALUES (
                CAST(:assembly_id AS uuid), :assay_key, :variant_id,
                :recurrence_count, :source, :label, CAST(:created_by AS uuid)
            )
            ON CONFLICT (assembly_id, assay_key, variant_id) DO UPDATE SET
                recurrence_count = EXCLUDED.recurrence_count,
                source = EXCLUDED.source,
                label = EXCLUDED.label,
                updated_at = timezone('utc', now())
            RETURNING {_ARTIFACT_COLUMNS}
            """
        ),
        {
            "assembly_id": assembly_id,
            "assay_key": assay_key,
            "variant_id": variant_id,
            "recurrence_count": recurrence_count,
            "source": source,
            "label": label,
            "created_by": created_by,
        },
    )
    row = dict(result.mappings().one())
    before = _artifact_state(dict(existing)) if existing is not None else None
    after = _artifact_state(row)
    if after != before:  # an unchanged re-save changes nothing, and writes no event
        await _record_artifact_event(
            session,
            actor=actor,
            actor_id=created_by,
            action="nipt_artifact_updated" if before is not None else "nipt_artifact_added",
            summary=(
                f"{'Updated' if before is not None else 'Added'} {variant_id} "
                f"{'on' if before is not None else 'to'} the NIPT artifact list ({assay_key})"
            ),
            variant_id=variant_id,
            before=before,
            after=after,
            metadata={"assembly_id": assembly_id, "assay_key": assay_key},
        )
    await session.commit()
    return row


async def delete_nipt_artifact(
    session: AsyncSession,
    *,
    artifact_id: str,
    actor: str = "system",
    actor_id: str | None = None,
) -> bool:
    """Remove an artifact entry, and audit it; False when there is none."""
    result = await session.execute(
        text(
            f"""
            DELETE FROM nipt_artifact_variants WHERE id = CAST(:id AS uuid)
            RETURNING {_ARTIFACT_COLUMNS}
            """
        ),
        {"id": artifact_id},
    )
    deleted = result.mappings().first()
    if deleted is None:
        await session.rollback()
        return False
    removed = _artifact_state(dict(deleted))
    await _record_artifact_event(
        session,
        actor=actor,
        actor_id=actor_id,
        action="nipt_artifact_removed",
        summary=f"Removed {removed['variant_id']} from the NIPT artifact list ({removed['assay_key']})",
        variant_id=removed["variant_id"],
        before=removed,
        metadata={"assembly_id": removed["assembly_id"], "assay_key": removed["assay_key"]},
    )
    await session.commit()
    return True


async def bulk_upsert_nipt_artifacts(
    session: AsyncSession,
    *,
    assembly_id: str,
    assay_key: str,
    items: Sequence[tuple[str, int]],
    source: str = "auto",
    created_by: str | None = None,
) -> int:
    """Upsert ``(variant_id, recurrence_count)`` pairs into a scope (the caller commits).

    On conflict only the recurrence count is refreshed, so a manually curated
    entry keeps its source and label when it also turns up as recurrent.
    """
    if not items:
        return 0
    rows = [
        {
            "assembly_id": assembly_id,
            "assay_key": assay_key,
            "variant_id": variant_id,
            "recurrence_count": int(count),
            "source": source,
            "label": "recurrent (auto)",
            "created_by": created_by,
        }
        for variant_id, count in items
    ]
    await session.execute(
        text(
            """
            INSERT INTO nipt_artifact_variants (
                assembly_id, assay_key, variant_id, recurrence_count, source, label, created_by
            ) VALUES (
                CAST(:assembly_id AS uuid), :assay_key, :variant_id,
                :recurrence_count, :source, :label, CAST(:created_by AS uuid)
            )
            ON CONFLICT (assembly_id, assay_key, variant_id) DO UPDATE SET
                recurrence_count = EXCLUDED.recurrence_count,
                updated_at = timezone('utc', now())
            """
        ),
        rows,
    )
    return len(rows)


async def _resolve_assembly_name(session: AsyncSession, assembly_id: str) -> str | None:
    result = await session.execute(
        text("SELECT assembly_name FROM assemblies WHERE id = CAST(:id AS uuid)"),
        {"id": assembly_id},
    )
    row = result.first()
    return str(row[0]) if row else None


async def _assay_cfdna_carrier_samples(session: AsyncSession, *, assay_key: str) -> dict[str, str]:
    """The cfDNA samples whose artifact scope is ``assay_key``, keyed by each identifier
    ClickHouse may store for them (name and UUID) -> the sample name.

    A cfDNA sample is one tagged ``assay: nipt_cfdna``; its scope is its
    ``assay_panel``, resolved as ``nipt_assay_key`` resolves it for the analysis.
    """
    result = await session.execute(
        text(
            """
            SELECT id::text AS sample_uuid,
                   sample_id,
                   CASE WHEN jsonb_typeof(metadata -> 'assay_panel') = 'string'
                        THEN metadata ->> 'assay_panel' END AS assay_panel
            FROM samples
            WHERE metadata ->> 'assay' = :assay
            ORDER BY sample_id
            """
        ),
        {"assay": NIPT_CFDNA_ASSAY},
    )
    carriers: dict[str, str] = {}
    for sample_uuid, sample_name, assay_panel in result.all():
        if assay_key_for_sample_metadata({SAMPLE_ASSAY_PANEL_KEY: assay_panel}) != assay_key:
            continue
        carriers[str(sample_name)] = str(sample_name)
        carriers[str(sample_uuid)] = str(sample_name)
    return carriers


async def auto_seed_nipt_artifacts(
    session: AsyncSession,
    *,
    assembly_id: str,
    assay_key: str,
    min_carrier_samples: int = 5,
    created_by: str | None = None,
    actor: str = "system",
) -> dict[str, int]:
    """Seed the artifact list from recurrence among the assay's own cfDNA samples.

    A variant is upserted as a ``source='auto'`` artifact for the scope when at least
    ``min_carrier_samples`` distinct cfDNA samples of this assay carry it, it is not
    common in the population, and ClinVar does not call it pathogenic. The first two
    limits protect the fetal fraction. FF is read off paternal sites that are mostly
    common SNPs, and every common variant is carried by many samples, so counting
    recurrence across the whole assembly -- as this did, every assay and application
    pooled -- listed the very sites FF relies on (and other assays' artifacts), and the
    analysis then excluded them.

    Protected -- never seeded, whatever its recurrence: a variant with a ClinVar
    pathogenic or likely pathogenic assertion in any of its annotations on the
    assembly (Pathogenic, Likely_pathogenic, their combinations and low-penetrance
    forms), and every "conflicting classifications/interpretations" record, which may
    hold a P/LP submission (``clinvar_may_assert_pathogenic``). A familial founder
    variant (CFTR F508del is ~1% in gnomAD) reaches five cfDNA samples of a
    disease-focused panel easily; seeded, it would be excluded from every analysis.

    Known limits; review the auto entries before relying on them. "Not common" is the
    import-time gnomAD/TopMed > 5% flag, and the ClinVar protection needs a ClinVar
    annotation, so a site with neither, or a real recurrent variant that ClinVar has
    not called pathogenic, can still be seeded, and is then excluded from this assay's
    NIPT analyses. A cfDNA sample counts only when tagged ``assay: nipt_cfdna``.
    """
    assembly_name = await _resolve_assembly_name(session, assembly_id)
    if assembly_name is None:
        raise HTTPException(status_code=404, detail="Assembly not found")
    recurrent = await fetch_recurrent_small_variant_ids(
        assembly_name,
        min_carrier_samples=min_carrier_samples,
        carrier_samples=await _assay_cfdna_carrier_samples(session, assay_key=assay_key),
        exclude_common=True,
        exclude_clinvar_pathogenic=True,
    )
    seeded = await bulk_upsert_nipt_artifacts(
        session,
        assembly_id=assembly_id,
        assay_key=assay_key,
        items=recurrent,
        source="auto",
        created_by=created_by,
    )
    if seeded:
        await _record_artifact_event(
            session,
            actor=actor,
            actor_id=created_by,
            action="nipt_artifacts_auto_seeded",
            summary=(
                f"Auto-seeded {seeded} recurrent variant(s) into the NIPT artifact list "
                f"({assay_key}; carried by at least {min_carrier_samples} cfDNA samples)"
            ),
            after={
                "variants": [
                    {"variant_id": variant_id, "recurrence_count": int(count)}
                    for variant_id, count in recurrent
                ]
            },
            metadata={
                "assembly_id": assembly_id,
                "assay_key": assay_key,
                "min_carrier_samples": min_carrier_samples,
            },
        )
        await session.commit()
    return {"seeded": seeded, "min_carrier_samples": min_carrier_samples}
