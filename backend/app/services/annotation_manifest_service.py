"""Per-family annotation/reference version manifest (clinical traceability, Phase 0).

Consolidates which annotation modules/versions backed a family's data so a report
can state its provenance (see docs/clinical-traceability.md). It merges two layers:

* the per-family *pipeline* manifest — the upstream tools that produced this family's
  annotated VCF (VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, …). Recorded explicitly in
  ``family_annotation_manifest`` (manual/admin), or captured for free from
  ``family.metadata.annotation_manifest`` when the import manifest declares it.
* the platform *reference* layer — versions of what CoGA itself loaded (the assembly,
  the Monarch release), read live from the reference tables.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .family_metadata_context import build_family_metadata_context
from .metadata_service import CurrentUser, get_family_record

logger = logging.getLogger(__name__)

# Canonical display order + labels; unknown keys fall through with a title-cased label.
_MODULE_LABELS: dict[str, str] = {
    "assembly": "Reference assembly",
    # variant callers (per modality)
    "deepvariant": "DeepVariant",
    "gatk": "GATK",
    "dragen": "DRAGEN",
    "clair3": "Clair3",
    "glimpse": "GLIMPSE",
    "sniffles": "Sniffles",
    "spectre": "Spectre",
    "needlr": "NeedlR",
    "cutesv": "cuteSV",
    "delly": "Delly",
    "manta": "Manta",
    "trgt": "TRGT",
    "paraphase": "Paraphase",
    # annotation engines
    "vep": "VEP",
    "snpeff": "SnpEff",
    "bcftools": "bcftools",
    "vcfanno": "vcfanno",
    "slivar": "slivar",
    # reference databases
    "clinvar": "ClinVar",
    "gnomad": "gnomAD",
    "dbnsfp": "dbNSFP",
    "spliceai": "SpliceAI",
    "alphamissense": "AlphaMissense",
    "revel": "REVEL",
    "cadd": "CADD",
    "dbsnp": "dbSNP",
    "cosmic": "COSMIC",
    "sift": "SIFT",
    "polyphen": "PolyPhen",
    "gencode": "GENCODE",
    "mane": "MANE",
    "hgmd": "HGMD",
    "gerp": "GERP++",
    "omim": "OMIM",
    "giab": "GIAB",
    # platform reference layer
    "gencc": "GenCC",
    "panelapp": "PanelApp",
    "monarch": "Monarch",
    "hpo": "HPO",
}
_MODULE_ORDER = list(_MODULE_LABELS)


def _as_module(value: Any) -> dict[str, Any]:
    """Accept either ``"110"`` or ``{"version": "110", "cache": "…"}``."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {"version": str(value)}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def _family_manifest_row(session: AsyncSession, family_uuid: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                """
                SELECT modules, source, recorded_by, recorded_at
                FROM family_annotation_manifest
                WHERE family_id = CAST(:family_uuid AS uuid)
                """
            ),
            {"family_uuid": family_uuid},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _platform_modules(session: AsyncSession, assembly_id: str | None) -> dict[str, dict[str, Any]]:
    """Versions of the reference layer CoGA loaded — best-effort, never fatal."""
    modules: dict[str, dict[str, Any]] = {}
    if assembly_id:
        try:
            row = (
                await session.execute(
                    text(
                        "SELECT assembly_name, version, release_date FROM assemblies "
                        "WHERE id = CAST(:assembly_id AS uuid)"
                    ),
                    {"assembly_id": assembly_id},
                )
            ).mappings().first()
            if row:
                detail = str(row["release_date"]) if row["release_date"] else (row["version"] or None)
                modules["assembly"] = {"version": row["assembly_name"], "detail": detail}
        except Exception:  # noqa: BLE001 — a missing column shouldn't break provenance
            pass
    try:
        release = (
            await session.execute(
                text(
                    "SELECT release_version FROM monarch_gene_disease "
                    "WHERE release_version IS NOT NULL AND release_version <> '' "
                    "ORDER BY updated_at DESC NULLS LAST LIMIT 1"
                )
            )
        ).scalar()
        if release:
            modules["monarch"] = {"version": str(release)}
    except Exception:  # noqa: BLE001
        pass
    return modules


def _module_list(
    pipeline_modules: dict[str, Any], platform_modules: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: dict[str, tuple[dict[str, Any], str]] = {}
    for key, value in platform_modules.items():
        merged[key] = (_as_module(value), "reference")
    # The family's own pipeline versions take precedence over platform defaults.
    for key, value in pipeline_modules.items():
        merged[key] = (_as_module(value), "pipeline")

    ordered = [k for k in _MODULE_ORDER if k in merged] + [k for k in merged if k not in _MODULE_ORDER]
    result: list[dict[str, Any]] = []
    for key in ordered:
        module, layer = merged[key]
        version = module.get("version")
        detail = (
            module.get("detail")
            or module.get("cache")
            or module.get("release")
            or module.get("release_date")
        )
        by_modality = module.get("by_modality")
        result.append(
            {
                "key": key,
                "label": _MODULE_LABELS.get(key, key.replace("_", " ").title()),
                "version": str(version) if version not in (None, "") else None,
                "detail": str(detail) if detail else None,
                "layer": layer,
                # Per-modality versions (issue #294): which modality's pipeline
                # cited which release — so SNV vs SV divergence is preserved.
                "by_modality": (
                    {str(k): str(v) for k, v in by_modality.items() if v not in (None, "")}
                    if isinstance(by_modality, dict) and by_modality
                    else None
                ),
            }
        )
    return result


async def get_family_annotation_manifest(
    session: AsyncSession, *, family_id: str, user: CurrentUser, project_id: str | None = None
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    row = await _family_manifest_row(session, context.family_uuid)

    pipeline_modules: dict[str, Any] = {}
    source: str | None = None
    recorded_at = None
    recorded_by: str | None = None
    if row and _as_dict(row.get("modules")):
        pipeline_modules = _as_dict(row.get("modules"))
        source = row.get("source")
        recorded_at = row.get("recorded_at")
        recorded_by = row.get("recorded_by")
    else:
        # Fall back to the manifest the import captured into family.metadata.
        family = await get_family_record(session, family_id, user)
        captured = (getattr(family, "metadata", None) or {}).get("annotation_manifest")
        if isinstance(captured, dict):
            pipeline_modules = captured
            source = "manifest"

    platform_modules = await _platform_modules(session, context.assembly_id)
    return {
        "family_id": context.family_id,
        "assembly": context.assembly_name,
        "source": source,
        "recorded_at": recorded_at,
        "recorded_by": recorded_by,
        "modules": _module_list(pipeline_modules, platform_modules),
    }


def _refresh_modules(
    current: Mapping[str, Any], incoming: Mapping[str, Any], modality: str | None = None
) -> dict[str, dict[str, Any]]:
    """Merge freshly parsed modules into the stored ones. Newly parsed versions
    win per-key (a re-import reflects the latest annotation), while modules the new
    input does not mention are preserved (so re-importing one modality does not
    wipe another's provenance).

    When ``modality`` is given, each module's version is also recorded under
    ``by_modality[modality]`` (issue #294) — so the same database cited at different
    releases by different modalities (e.g. GENCODE 49 for SNV, 45 for SV) keeps the
    full per-modality truth alongside the flat representative ``version``."""
    out: dict[str, dict[str, Any]] = {
        key: dict(_as_module(value)) for key, value in (current or {}).items()
    }
    for key, value in (incoming or {}).items():
        entry = out.setdefault(key, {})
        incoming_entry = _as_module(value)
        for field_name, field_value in incoming_entry.items():
            if field_name == "by_modality" or field_value in (None, ""):
                continue
            entry[field_name] = field_value
        if modality and incoming_entry.get("version") not in (None, ""):
            existing_by = entry.get("by_modality")
            by_modality = dict(existing_by) if isinstance(existing_by, dict) else {}
            by_modality[str(modality)] = incoming_entry["version"]
            entry["by_modality"] = by_modality
    return {k: v for k, v in out.items() if v}


async def merge_vcf_header_provenance(
    session: AsyncSession,
    *,
    family_uuid: str,
    assembly_id: str | None,
    modules: Mapping[str, Any],
    modality: str | None = None,
    recorded_by: str = "import (vcf_header)",
    source: str = "vcf_header",
) -> None:
    """Best-effort: fold harvested module versions into the family's annotation
    manifest under ``source`` (``'vcf_header'`` by default).

    ``source`` records *where* the versions came from. Most arrive from a VCF header;
    the long-read pipeline instead ships a run manifest (``software_versions.yaml``),
    which is recorded as ``'manifest'`` so the provenance is not mislabelled as
    parsed-from-a-header. ``'manual'`` remains reserved for admin-curated entries and
    is never written here.

    * **Never overwrites a ``manual`` manifest** — an admin's curated provenance
      wins over anything parsed from a header.
    * **Refreshes on re-import** — newly parsed versions overwrite stale ones,
      while untouched modules are preserved.
    * **Never raises and never poisons the caller's transaction** — the write runs
      in a SAVEPOINT and joins the caller's commit, so provenance persists iff the
      ingestion that produced it does.
    """
    if not modules:
        return
    # 'manual' is written only by the admin-curation path; accepting it here would let
    # an import masquerade as curated provenance and then be immune to later refreshes.
    recorded_source = source if source != "manual" else "vcf_header"
    try:
        async with session.begin_nested():
            existing = await _family_manifest_row(session, family_uuid)
            if existing and existing.get("source") == "manual":
                return  # respect admin-curated provenance
            current = _as_dict(existing.get("modules")) if existing else {}
            merged = _refresh_modules(current, modules, modality=modality)
            await session.execute(
                text(
                    """
                    INSERT INTO family_annotation_manifest
                        (family_id, assembly_id, modules, source, recorded_by, recorded_at)
                    VALUES
                        (CAST(:family_uuid AS uuid),
                         CASE WHEN :assembly_id IS NULL THEN NULL ELSE CAST(:assembly_id AS uuid) END,
                         CAST(:modules AS jsonb), :source, :recorded_by, now())
                    ON CONFLICT (family_id) DO UPDATE SET
                        modules = EXCLUDED.modules,
                        source = EXCLUDED.source,
                        recorded_by = EXCLUDED.recorded_by,
                        recorded_at = now(),
                        assembly_id = COALESCE(EXCLUDED.assembly_id, family_annotation_manifest.assembly_id)
                    """
                ),
                {
                    "family_uuid": family_uuid,
                    "assembly_id": assembly_id,
                    "modules": json.dumps(merged),
                    "recorded_by": recorded_by,
                    "source": recorded_source,
                },
            )
    except Exception:  # noqa: BLE001 — provenance capture must never break ingestion
        logger.warning("%s provenance capture failed for family %s", recorded_source, family_uuid, exc_info=True)


async def set_family_annotation_manifest(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    modules: dict[str, Any],
    source: str = "manual",
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user
    )
    await session.execute(
        text(
            """
            INSERT INTO family_annotation_manifest
                (family_id, assembly_id, modules, source, recorded_by, recorded_at)
            VALUES
                (CAST(:family_uuid AS uuid), CAST(:assembly_id AS uuid),
                 CAST(:modules AS jsonb), :source, :recorded_by, now())
            ON CONFLICT (family_id) DO UPDATE SET
                modules = EXCLUDED.modules,
                source = EXCLUDED.source,
                recorded_by = EXCLUDED.recorded_by,
                recorded_at = now(),
                assembly_id = EXCLUDED.assembly_id
            """
        ),
        {
            "family_uuid": context.family_uuid,
            "assembly_id": context.assembly_id,
            "modules": json.dumps(modules or {}),
            "source": source,
            "recorded_by": getattr(user, "email", None),
        },
    )
    await session.commit()
    return await get_family_annotation_manifest(session, family_id=family_id, user=user)
