"""Recurrent-artifact (panel-of-normals) list for monogenic NIPT.

Artifacts are scoped per ``(assembly_id, assay_key)`` -- recurrent artifacts are
capture/chemistry-specific, so a new panel starts with a clean list. The list is
curated on the admin page *NIPT artefact list* through the admin API, where ``auto-seed``
lists the alleles that recur as an unexplained low-VAF call in the assay's own cfDNA samples
(its preview reads them without writing); ``load_nipt_artifacts`` gives the analysis each
listed allele with its recurrence, to exclude (and count) artifacts and to keep a listed
allele the family's father carries. Every change to the list is recorded in the clinical
audit trail (``NIPT_ARTIFACT_AUDIT_CHAIN``).

See docs/monogenic-nipt.md.
"""

from __future__ import annotations

import json

import csv
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import canonical_uuid, require_uuid
from .clickhouse_family_variants import (
    CfdnaRecurrenceRules,
    fetch_artifact_protection_flags,
    fetch_recurrent_artifact_candidates,
)
from .clickhouse_variant_ids import build_small_variant_id
from .clinical_audit_service import record_clinical_event
from .clinvar_coga import clinvar_coga_read
from .family_analysis_types import PREGNANCY_SEPARATOR, SAMPLE_ASSAY_PANELS_KEY
from .nipt import (
    DEFAULT_NIPT_ASSAY_KEY,
    MONOGENIC_NIPT_ANALYSIS_TYPE,
    NIPT_CFDNA_ASSAY,
    assay_key_for_panel,
)
from .nipt_analysis import MATERNAL_ALLELE_VAF_BOUNDARY, NiptQualityThresholds

# A change to the artifact list changes which variants every NIPT analysis of its scope
# filters out, so each add, update, removal and auto-seed is a hash-chained clinical audit
# event. They form a chain of their own, not tied to a family; verify it with
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


async def _resolve_assembly_name(session: AsyncSession, assembly_id: str) -> str | None:
    result = await session.execute(
        text("SELECT assembly_name FROM assemblies WHERE id = CAST(:id AS uuid)"),
        {"id": assembly_id},
    )
    row = result.first()
    return str(row[0]) if row else None


_ASSEMBLY_NOT_FOUND = "Assembly not found"


async def _require_assembly(session: AsyncSession, assembly_id: str) -> tuple[str, str]:
    """The canonical id and the name of the assembly a request scopes the list by.

    A value that spells no UUID, or a UUID of no assembly, is refused with a 404 before
    anything is written: there is no entry and no audit event for an unknown assembly
    (asyncpg's uuid codec refused the first, and an add's foreign key the second, with a
    500). Each statement binds the canonical id and each audit event records it, so every
    spelling of an assembly's id names one scope, in the list and in its audit trail.
    """
    assembly_uuid = require_uuid(assembly_id, _ASSEMBLY_NOT_FOUND, status_code=404)
    assembly_name = await _resolve_assembly_name(session, assembly_uuid)
    if assembly_name is None:
        raise HTTPException(status_code=404, detail=_ASSEMBLY_NOT_FOUND)
    return assembly_uuid, assembly_name


async def load_nipt_artifacts(
    session: AsyncSession,
    *,
    assembly_id: str | None,
    assay_key: str,
) -> dict[str, int]:
    """A scope's listed alleles, each ``variant_id`` with its ``recurrence_count`` (the
    cfDNA samples the list records it in), for the analysis to look up."""
    if not assembly_id:
        return {}
    result = await session.execute(
        text(
            """
            SELECT variant_id, recurrence_count
            FROM nipt_artifact_variants
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND assay_key = :assay_key
            """
        ),
        {"assembly_id": assembly_id, "assay_key": assay_key},
    )
    return {str(row[0]): int(row[1]) for row in result.all()}


async def list_nipt_artifacts(
    session: AsyncSession,
    *,
    assembly_id: str,
    assay_key: str | None = None,
) -> list[dict[str, Any]]:
    assembly_uuid = canonical_uuid(assembly_id)
    if assembly_uuid is None:
        return []
    clauses = ["assembly_id = CAST(:assembly_id AS uuid)"]
    params: dict[str, Any] = {"assembly_id": assembly_uuid}
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
    assembly_uuid, _ = await _require_assembly(session, assembly_id)
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
            {"assembly_id": assembly_uuid, "assay_key": assay_key, "variant_id": variant_id},
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
            "assembly_id": assembly_uuid,
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
            metadata={"assembly_id": assembly_uuid, "assay_key": assay_key},
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
    artifact_uuid = canonical_uuid(artifact_id)
    if artifact_uuid is None:
        return False
    result = await session.execute(
        text(
            f"""
            DELETE FROM nipt_artifact_variants WHERE id = CAST(:id AS uuid)
            RETURNING {_ARTIFACT_COLUMNS}
            """
        ),
        {"id": artifact_uuid},
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


# Where the SQL of ``_cfdna_sample_scopes`` reads a cfDNA sample's capture panel: the one the
# package of its monogenic NIPT named, Sample.metadata[SAMPLE_ASSAY_PANELS_KEY] under that
# analysis's key, whose analysis type is MONOGENIC_NIPT_ANALYSIS_TYPE (``monogenic_nipt:ZW1``: a
# plasma is the sample of one pregnancy). test_nipt_artifact_pg pins the two together.
CFDNA_PANEL_PATH = (SAMPLE_ASSAY_PANELS_KEY, MONOGENIC_NIPT_ANALYSIS_TYPE)


async def _cfdna_sample_scopes(session: AsyncSession) -> list[tuple[str, str]]:
    """Each cfDNA sample's ID with its artifact scope, in every project.

    A cfDNA sample is one whose assays hold ``nipt_cfdna``; its scope is the capture panel the
    package of its monogenic NIPT named for it (``assay_panels``), resolved as ``nipt_assay_key``
    resolves it for the analysis.
    """
    result = await session.execute(
        text(
            # The panel at CFDNA_PANEL_PATH: under the key of the sample's monogenic NIPT, the
            # analysis type before the pregnancy (family_analysis_types.PREGNANCY_SEPARATOR).
            """
            SELECT sample_id,
                   (
                       SELECT panel.value #>> '{}'
                       FROM jsonb_each(
                           CASE WHEN jsonb_typeof(metadata -> 'assay_panels') = 'object'
                                THEN metadata -> 'assay_panels' ELSE '{}'::jsonb END
                       ) AS panel(analysis, value)
                       WHERE split_part(panel.analysis, :separator, 1) = :analysis_type
                         AND jsonb_typeof(panel.value) = 'string'
                       ORDER BY panel.analysis
                       LIMIT 1
                   ) AS assay_panel
            FROM samples
            WHERE jsonb_typeof(metadata -> 'assays') = 'array'
              AND (metadata -> 'assays') @> CAST(:assays AS jsonb)
            ORDER BY sample_id
            """
        ),
        {
            "assays": json.dumps([NIPT_CFDNA_ASSAY]),
            "separator": PREGNANCY_SEPARATOR,
            "analysis_type": CFDNA_PANEL_PATH[1],
        },
    )
    return [(str(sample_id), assay_key_for_panel(assay_panel)) for sample_id, assay_panel in result.all()]


async def assay_cfdna_carrier_samples(session: AsyncSession, *, assay_key: str) -> list[str]:
    """The IDs of the cfDNA samples whose artifact scope is ``assay_key``: whose calls
    count as recurrence."""
    return [sample_id for sample_id, scope in await _cfdna_sample_scopes(session) if scope == assay_key]


async def list_nipt_artifact_scopes(session: AsyncSession, *, assembly_id: str) -> list[dict[str, Any]]:
    """The scopes an artifact list of the assembly can have, for the admin page to choose
    from: the default scope, each scope a cfDNA sample's ``assay_panel`` resolves to and
    each scope the assembly's list holds entries for. Each with its cfDNA samples, in every
    project, and its entries on the list; the default scope first, then by name.

    A value that spells no UUID names no assembly: its scopes hold no entry, as the list
    reads it.
    """
    samples = Counter(scope for _sample_id, scope in await _cfdna_sample_scopes(session))
    listed: dict[str, int] = {}
    assembly_uuid = canonical_uuid(assembly_id)
    if assembly_uuid is not None:
        result = await session.execute(
            text(
                """
                SELECT assay_key, count(*) AS listed
                FROM nipt_artifact_variants
                WHERE assembly_id = CAST(:assembly_id AS uuid)
                GROUP BY assay_key
                """
            ),
            {"assembly_id": assembly_uuid},
        )
        listed = {str(assay_key): int(count) for assay_key, count in result.all()}
    scopes = {DEFAULT_NIPT_ASSAY_KEY, *samples, *listed}
    return [
        {"assay_key": scope, "cfdna_samples": samples.get(scope, 0), "listed": listed.get(scope, 0)}
        for scope in sorted(scopes, key=lambda scope: (scope != DEFAULT_NIPT_ASSAY_KEY, scope))
    ]


def recurrence_rules(qc: NiptQualityThresholds) -> CfdnaRecurrenceRules:
    """How a call of another cfDNA sample of the assay is read, by the recurrence count on
    the NIPT list and by the auto-seed: the quality filter's floors, the maternal boundary
    (``MATERNAL_ALLELE_VAF_BOUNDARY``) and the father's het or hom-alt class
    (``nipt_service.derive_father_state``)."""
    return CfdnaRecurrenceRules(
        min_alt_reads=qc.min_cf_alt_reads,
        min_alt_fraction=qc.min_vaf,
        maternal_vaf_boundary=MATERNAL_ALLELE_VAF_BOUNDARY,
        father_min_depth=qc.min_father_dp,
        father_min_alt_reads=qc.min_father_alt_reads,
        father_min_quality=qc.min_father_qual,
        father_min_vaf=qc.father_het_min_vaf,
        father_min_genotyped_quality=qc.min_father_genotyped_qual,
        father_min_gq=qc.min_father_gq,
    )


async def auto_seed_nipt_artifacts(
    session: AsyncSession,
    *,
    assembly_id: str,
    assay_key: str,
    min_unexplained_samples: int,
    created_by: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Seed the artifact list from the alleles that recur as an artefact does in the
    assay's own cfDNA samples.

    An allele is upserted as a ``source='auto'`` artifact for the scope, with that count as
    its ``recurrence_count``, when it is an unexplained low-VAF call in at least
    ``min_unexplained_samples`` cfDNA samples of this assay
    (``fetch_recurrent_artifact_candidates``): a call below the maternal boundary that its
    family's father does not explain, read by ``recurrence_rules`` with the default quality
    thresholds, as the recurrence count on the NIPT list reads it. Every family's calls
    count. A maternal het or the fetus's paternal allele does not count, however many
    plasmas hold it: those are the sites the fetal fraction is read from, and a count of
    every carrier would list each polymorphism of 1 to 5% once the database holds about a
    hundred plasmas.

    Protected, never seeded whatever its count: a variant more than 5% frequent in gnomAD
    or TopMed (the import-time ``is_gnomad_gt_5_percent`` flag), one with a ClinVar
    pathogenic or likely pathogenic assertion in any of its annotations on the assembly
    (Pathogenic, Likely_pathogenic, their combinations and low-penetrance forms), and every
    "conflicting classifications/interpretations" record, which may hold a P/LP submission
    (``clinvar_may_assert_pathogenic``). A familial founder variant (CFTR F508del is ~1% in
    gnomAD) is a maternal or paternal call in a disease-focused panel; seeded, it would be
    excluded from every analysis.

    Known limits; review the auto entries before relying on them. A family without a
    father's sample, or whose father's call fails the quality floors, cannot explain its
    paternal alleles, so a polymorphism can still reach the threshold in a large cohort of
    such families. "Not common" is the import-time flag, and the ClinVar protection needs a
    ClinVar annotation. A cfDNA sample counts only when tagged ``assay: nipt_cfdna``.

    Returns what it wrote: the number of cfDNA samples it read and each seeded allele, with
    the source of its entry already on the list (``_auto_seed_candidates``, the reading
    ``preview_auto_seed_nipt_artifacts`` shows without writing).
    """
    assembly_uuid, assembly_name = await _require_assembly(session, assembly_id)
    rules = recurrence_rules(NiptQualityThresholds())
    candidates = await _auto_seed_candidates(
        session,
        assembly_uuid=assembly_uuid,
        assembly_name=assembly_name,
        assay_key=assay_key,
        min_unexplained_samples=min_unexplained_samples,
        rules=rules,
    )
    recurrent = [(variant["variant_id"], variant["recurrence_count"]) for variant in candidates["variants"]]
    seeded = await bulk_upsert_nipt_artifacts(
        session,
        assembly_id=assembly_uuid,
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
                f"({assay_key}; an unexplained low-VAF call in at least "
                f"{min_unexplained_samples} cfDNA samples)"
            ),
            after={
                "variants": [
                    {"variant_id": variant_id, "recurrence_count": int(count)}
                    for variant_id, count in recurrent
                ]
            },
            metadata={
                "assembly_id": assembly_uuid,
                "assay_key": assay_key,
                "min_unexplained_samples": min_unexplained_samples,
                "rules": asdict(rules),
            },
        )
        await session.commit()
    return {**candidates, "seeded": seeded}


async def preview_auto_seed_nipt_artifacts(
    session: AsyncSession,
    *,
    assembly_id: str,
    assay_key: str,
    min_unexplained_samples: int,
) -> dict[str, Any]:
    """What ``auto_seed_nipt_artifacts`` would write now, read exactly as it reads it (the
    same samples, rules, threshold and protections), without writing: no entry and no
    audit event. Each allele says whether it is on the list already (``listed_source``),
    where the seed would only refresh its recurrence count. An unknown assembly is a 404,
    as for the seed."""
    assembly_uuid, assembly_name = await _require_assembly(session, assembly_id)
    return await _auto_seed_candidates(
        session,
        assembly_uuid=assembly_uuid,
        assembly_name=assembly_name,
        assay_key=assay_key,
        min_unexplained_samples=min_unexplained_samples,
        rules=recurrence_rules(NiptQualityThresholds()),
    )


async def _auto_seed_candidates(
    session: AsyncSession,
    *,
    assembly_uuid: str,
    assembly_name: str,
    assay_key: str,
    min_unexplained_samples: int,
    rules: CfdnaRecurrenceRules,
) -> dict[str, Any]:
    """The alleles the auto-seed lists for a scope, most recurrent first, with the cfDNA
    samples it reads them in and the source of each one's entry already on the list: the
    one reading the seed writes from and its preview shows."""
    carrier_samples = await assay_cfdna_carrier_samples(session, assay_key=assay_key)
    recurrent = await fetch_recurrent_artifact_candidates(
        assembly_name,
        clinvar_coga=await clinvar_coga_read(session, assembly_name),
        carrier_samples=carrier_samples,
        rules=rules,
        min_unexplained_samples=min_unexplained_samples,
    )
    listed: dict[str, str] = {}
    if recurrent:
        result = await session.execute(
            text(
                """
                SELECT variant_id, source
                FROM nipt_artifact_variants
                WHERE assembly_id = CAST(:assembly_id AS uuid)
                  AND assay_key = :assay_key
                """
            ),
            {"assembly_id": assembly_uuid, "assay_key": assay_key},
        )
        listed = {str(variant_id): str(source) for variant_id, source in result.all()}
    return {
        "assay_key": assay_key,
        "min_unexplained_samples": min_unexplained_samples,
        "cfdna_samples": len(carrier_samples),
        "variants": [
            {"variant_id": variant_id, "recurrence_count": int(count), "listed_source": listed.get(variant_id)}
            for variant_id, count in recurrent
        ],
    }


# The R NIPT-M pipeline's recurrent-artefact table
# (``recurrent_cfdna_artifact_filter_<suffix>.tsv``): one row per allele seen in the
# cohort's plasma, ``filter_as_recurrent_artifact`` TRUE for those its tiered profiles
# remove. A plain list of alleles (``variant_key`` or CHROM/POS/REF/ALT) also reads.
_TRUE_VALUES = {"true", "t", "1", "yes"}


@dataclass(slots=True)
class ArtifactTableItem:
    variant_id: str
    recurrence_count: int
    label: str


@dataclass(slots=True)
class ArtifactTable:
    items: list[ArtifactTableItem] = field(default_factory=list)
    rows_read: int = 0
    not_flagged: int = 0
    invalid: int = 0


def _artifact_allele(row: dict[str, str]) -> tuple[str, int, str, str] | None:
    key = (row.get("variant_key") or "").strip()
    if key:
        parts = key.split(":")
        if len(parts) != 4:
            return None
        chrom, position, ref, alt = parts
    else:
        chrom = (row.get("CHROM") or row.get("chrom") or "").strip()
        position = (row.get("POS") or row.get("pos") or "").strip()
        ref = (row.get("REF") or row.get("ref") or "").strip()
        alt = (row.get("ALT") or row.get("alt") or "").strip()
    if not chrom or not ref or not alt or "," in alt or alt in {".", "*"}:
        return None
    try:
        start = int(position)
    except ValueError:
        return None
    if start < 1:
        return None
    return chrom, start, ref.upper(), alt.upper()


def parse_artifact_table(text_value: str) -> ArtifactTable:
    """The alleles a tab-separated artefact table lists: with a
    ``filter_as_recurrent_artifact`` column, only the rows it marks TRUE. The recurrence
    count is ``n_cfdna_families`` (0 without it); the label names the R profile
    (``recurrent_filter_profile``) when there is one."""
    reader = csv.DictReader(text_value.splitlines(), delimiter="\t")
    fields = set(reader.fieldnames or [])
    if not ({"variant_key"} <= fields or {"CHROM", "POS", "REF", "ALT"} <= fields):
        raise HTTPException(
            status_code=400,
            detail="The artifact table needs a variant_key column or CHROM, POS, REF and ALT columns",
        )
    flagged_column = "filter_as_recurrent_artifact" in fields
    table = ArtifactTable()
    seen: set[str] = set()
    for row in reader:
        table.rows_read += 1
        flag = str(row.get("filter_as_recurrent_artifact") or "").strip().lower()
        if flagged_column and flag not in _TRUE_VALUES:
            table.not_flagged += 1
            continue
        allele = _artifact_allele(row)
        if allele is None:
            table.invalid += 1
            continue
        variant_id = build_small_variant_id(*allele)
        if variant_id in seen:
            continue
        seen.add(variant_id)
        try:
            count = int(float(row.get("n_cfdna_families") or 0))
        except ValueError:
            count = 0
        profile = (row.get("recurrent_filter_profile") or "").strip()
        table.items.append(
            ArtifactTableItem(
                variant_id=variant_id,
                recurrence_count=max(0, count),
                label=f"recurrent ({profile})" if profile else "recurrent (imported)",
            )
        )
    return table


async def import_nipt_artifact_table(
    session: AsyncSession,
    *,
    assembly_id: str,
    assay_key: str,
    text_value: str,
    filename: str,
    created_by: str | None = None,
    actor: str = "system",
) -> dict[str, int]:
    """Add the alleles of an artefact table (the R NIPT-M pipeline's recurrent list) to the
    artifact list of a scope, as curated entries, and audit the import as one event.

    The auto-seed's protections hold: an allele whose annotation on the assembly is
    common (gnomAD or TopMed above 5%) or may assert pathogenic in ClinVar is never
    added. An allele no family on the assembly carries has no annotation to check; it is
    added, and each family's analysis checks it again with the family's own annotation
    (``nipt_service.family_artifact_ids``: a listed allele the family's annotation marks
    common or ClinVar-pathogenic, or that the family's father carries, stays in that
    family's analysis, flagged). An entry already on the list keeps its source and label and
    takes the table's recurrence count.
    """
    assembly_uuid, assembly_name = await _require_assembly(session, assembly_id)
    table = parse_artifact_table(text_value)
    protected = await fetch_artifact_protection_flags(
        assembly_name,
        [item.variant_id for item in table.items],
        clinvar_coga=await clinvar_coga_read(session, assembly_name),
    )
    accepted = [item for item in table.items if item.variant_id not in protected]
    if accepted:
        await session.execute(
            text(
                """
                INSERT INTO nipt_artifact_variants (
                    assembly_id, assay_key, variant_id, recurrence_count, source, label, created_by
                ) VALUES (
                    CAST(:assembly_id AS uuid), :assay_key, :variant_id,
                    :recurrence_count, 'curated', :label, CAST(:created_by AS uuid)
                )
                ON CONFLICT (assembly_id, assay_key, variant_id) DO UPDATE SET
                    recurrence_count = EXCLUDED.recurrence_count,
                    updated_at = timezone('utc', now())
                """
            ),
            [
                {
                    "assembly_id": assembly_uuid,
                    "assay_key": assay_key,
                    "variant_id": item.variant_id,
                    "recurrence_count": item.recurrence_count,
                    "label": item.label,
                    "created_by": created_by,
                }
                for item in accepted
            ],
        )
    summary = {
        "rows_read": table.rows_read,
        "not_flagged": table.not_flagged,
        "invalid": table.invalid,
        "imported": len(accepted),
        "protected_common": sum(1 for reason in protected.values() if reason == "common"),
        "protected_clinvar": sum(1 for reason in protected.values() if reason == "clinvar"),
    }
    if accepted:
        await _record_artifact_event(
            session,
            actor=actor,
            actor_id=created_by,
            action="nipt_artifacts_imported",
            summary=(
                f"Imported {len(accepted)} recurrent variant(s) from {filename} into the NIPT "
                f"artifact list ({assay_key})"
            ),
            after={
                "variants": [
                    {"variant_id": item.variant_id, "recurrence_count": item.recurrence_count, "label": item.label}
                    for item in accepted
                ]
            },
            metadata={
                "assembly_id": assembly_uuid,
                "assay_key": assay_key,
                "filename": filename,
                "protected": sorted(protected),
                **summary,
            },
        )
        await session.commit()
    return summary
