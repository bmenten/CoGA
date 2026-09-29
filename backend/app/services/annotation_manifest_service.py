"""Per-family annotation/reference version manifest (clinical traceability, Phase 0).

Consolidates which annotation modules/versions backed a family's data so a report
can state its provenance (see docs/clinical-traceability.md). It merges two layers:

* the per-family *pipeline* manifest — the upstream tools that produced this family's
  annotated VCF (VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, …). Recorded explicitly in
  ``family_annotation_manifest`` (manual/admin), or captured for free from
  ``family.metadata.annotation_manifest`` when the import manifest declares it.
* the platform *reference* layer — versions of what CoGA itself loaded (the assembly,
  the gene loci, the Monarch and HPO releases), read live from the reference tables.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .clinical_audit_service import record_clinical_event
from .family_metadata_context import build_family_metadata_context
from .hpo_service import get_loaded_hpo_release
from .metadata_service import get_family_record
from .access_control import CurrentUser, is_admin_user

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
    "deepsomatic": "DeepSomatic",
    "hificnv": "HiFiCNV",
    "glnexus": "GLnexus",
    "mutserve": "mutserve",
    # annotation engines
    "vep": "VEP",
    "snpeff": "SnpEff",
    "bcftools": "bcftools",
    "vcfanno": "vcfanno",
    "slivar": "slivar",
    "ensemblvep": "Ensembl VEP",
    "exomiser": "Exomiser",
    "exomiser_data": "Exomiser data",
    "remm": "ReMM",
    # alignment / file handling / QC
    "minimap2": "minimap2",
    "samtools": "samtools",
    "htslib": "HTSlib",
    "tabix": "tabix",
    "mosdepth": "mosdepth",
    "nanoplot": "NanoPlot",
    "longphase": "LongPhase",
    "gzip": "gzip",
    "xz": "xz",
    "perl-math-cdf": "perl-Math-CDF",
    # workflow + engine
    "nf-core/lrsvar": "nf-core/lrsvar",
    "nextflow": "Nextflow",
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
    "gene_loci": "Gene loci (CoGA reference)",
    "gencc": "GenCC",
    "panelapp": "PanelApp",
    "monarch": "Monarch",
    "hpo": "HPO",
}
def _fallback_module_label(key: str) -> str:
    """Display label for a module the catalogue does not know: the key, verbatim.

    Module keys are tool and database names, and their casing is part of their identity —
    `nf-core/lrsvar`, `minimap2`, `xz`, `bcftools`. Title-casing an unrecognised one is a
    guess, and for a name it is nearly always wrong. A record whose purpose is to say
    exactly what produced a result must not rename it; anything that genuinely wants a
    prettier label gets an explicit entry in `_MODULE_LABELS`.
    """
    return key


def module_label(key: str) -> str:
    """The label a module is shown under: its curated one, or the key verbatim."""
    return _MODULE_LABELS.get(key, _fallback_module_label(key))


_MODULE_ORDER = list(_MODULE_LABELS)

# The reference-layer modules `_platform_modules` looks up. A signed snapshot freezes this
# list next to its modules, so that one of these missing from the record reads as "not
# loaded when it was signed" rather than "not looked up by the software that signed it".
REFERENCE_MODULE_KEYS: tuple[str, ...] = ("assembly", "gene_loci", "monarch", "hpo")


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


# Stands in for a module version the provenance lookup could not read (#514).
UNAVAILABLE_MODULE_VERSION = "unavailable"


async def _platform_modules(session: AsyncSession, assembly_id: str | None) -> dict[str, dict[str, Any]]:
    """Versions of the reference layer CoGA loaded — best-effort, never fatal.

    This runs inside sign-out, so each lookup is a SAVEPOINT: a failed statement cannot
    poison the caller's transaction. A failure is logged and recorded as an explicit
    ``unavailable`` module, so the frozen record says the version could not be read
    instead of silently leaving the module out (#514). A bare ``except: pass`` here is
    the pattern that once hid a total failure to write the manifest.
    """
    modules: dict[str, dict[str, Any]] = {}
    if assembly_id:
        try:
            async with session.begin_nested():
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
        except Exception:  # noqa: BLE001 — provenance must never break sign-out
            logger.warning("Reference-assembly provenance lookup failed", exc_info=True)
            modules["assembly"] = {"version": UNAVAILABLE_MODULE_VERSION, "detail": "lookup failed"}
        # The gene loci CoGA itself loaded for the assembly (panel regions, gene tracks) and
        # where they came from: GENCODE, or the UCSC table used when GENCODE could not be
        # fetched. Distinct from the pipeline's own annotation version (#536).
        try:
            async with session.begin_nested():
                gene_import = (
                    await session.execute(
                        text(
                            "SELECT source, performed_at FROM reference_dataset_imports "
                            "WHERE assembly_id = CAST(:assembly_id AS uuid) AND dataset_type = 'genes' "
                            "ORDER BY performed_at DESC LIMIT 1"
                        ),
                        {"assembly_id": assembly_id},
                    )
                ).mappings().first()
            if gene_import and gene_import["source"]:
                performed_at = gene_import["performed_at"]
                modules["gene_loci"] = {
                    "version": str(gene_import["source"]),
                    "detail": f"imported {performed_at:%Y-%m-%d}" if performed_at else None,
                }
        except Exception:  # noqa: BLE001 — provenance must never break sign-out
            logger.warning("Gene-locus provenance lookup failed", exc_info=True)
            modules["gene_loci"] = {"version": UNAVAILABLE_MODULE_VERSION, "detail": "lookup failed"}
    try:
        async with session.begin_nested():
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
    except Exception:  # noqa: BLE001 — provenance must never break sign-out
        logger.warning("Monarch-release provenance lookup failed", exc_info=True)
        modules["monarch"] = {"version": UNAVAILABLE_MODULE_VERSION, "detail": "lookup failed"}
    # The HPO release phenotype matching, HPO-driven ranking and the phenotype features ran
    # on: the release of the latest ontology import (see get_loaded_hpo_release, which the
    # admin summary and the ranking-cache key read too).
    try:
        async with session.begin_nested():
            hpo_release = await get_loaded_hpo_release(session)
        if hpo_release:
            version = str(hpo_release["release_version"] or "").strip()
            if version:
                release_date = hpo_release["release_date"]
                modules["hpo"] = {"version": version, "detail": str(release_date) if release_date else None}
            else:
                # Loaded without a release: say so rather than leave HPO out as if no
                # ontology were loaded (#514).
                modules["hpo"] = {"version": UNAVAILABLE_MODULE_VERSION, "detail": "release not recorded"}
    except Exception:  # noqa: BLE001 — provenance must never break sign-out
        logger.warning("HPO-release provenance lookup failed", exc_info=True)
        modules["hpo"] = {"version": UNAVAILABLE_MODULE_VERSION, "detail": "lookup failed"}
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
                # Unknown keys are shown verbatim — see _fallback_module_label.
                "label": module_label(key),
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


async def _pipeline_manifest(
    session: AsyncSession, *, family_uuid: str, family_id: str, user: CurrentUser
) -> dict[str, Any]:
    """The family's pipeline layer as its report states it: the recorded row, else the
    manifest the import captured into ``family.metadata`` (source ``'manifest'``)."""
    row = await _family_manifest_row(session, family_uuid)
    if row and _as_dict(row.get("modules")):
        return {
            "modules": _as_dict(row.get("modules")),
            "source": row.get("source"),
            "recorded_at": row.get("recorded_at"),
            "recorded_by": row.get("recorded_by"),
        }
    # Fall back to the manifest the import captured into family.metadata.
    family = await get_family_record(session, family_id, user)
    captured = (getattr(family, "metadata", None) or {}).get("annotation_manifest")
    if isinstance(captured, dict):
        return {"modules": captured, "source": "manifest", "recorded_at": None, "recorded_by": None}
    return {"modules": {}, "source": None, "recorded_at": None, "recorded_by": None}


async def get_family_annotation_manifest(
    session: AsyncSession, *, family_id: str, user: CurrentUser, project_id: str | None = None
) -> dict[str, Any]:
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    pipeline = await _pipeline_manifest(
        session, family_uuid=context.family_uuid, family_id=family_id, user=user
    )
    platform_modules = await _platform_modules(session, context.assembly_id)
    return {
        "family_id": context.family_id,
        "assembly": context.assembly_name,
        "source": pipeline["source"],
        "recorded_at": pipeline["recorded_at"],
        "recorded_by": pipeline["recorded_by"],
        "modules": _module_list(pipeline["modules"], platform_modules),
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
                         -- NULLIF, not `CASE WHEN :assembly_id IS NULL`: asyncpg cannot
                         -- infer a parameter's type when one of its uses is a bare
                         -- `IS NULL`, and raises AmbiguousParameterError. The CASE form
                         -- meant this INSERT failed on every call, and because the whole
                         -- merge is best-effort and logs rather than raises, the family
                         -- annotation manifest was silently never written — for any
                         -- modality. Regression-guarded by
                         -- test_sql_parameter_typing.py.
                         CAST(NULLIF(:assembly_id, '') AS uuid),
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
                    # '' rather than None so NULLIF above resolves it to SQL NULL with a
                    # parameter type asyncpg can infer.
                    "assembly_id": assembly_id or "",
                    "modules": json.dumps(merged),
                    "recorded_by": recorded_by,
                    "source": recorded_source,
                },
            )
    except Exception:  # noqa: BLE001 — provenance capture must never break ingestion
        logger.warning("%s provenance capture failed for family %s", recorded_source, family_uuid, exc_info=True)


# The audit summary names at most this many module changes; before/after hold them all.
_SUMMARY_MAX_CHANGES = 6


def _module_version(value: Any) -> str:
    version = _as_module(value).get("version")
    return str(version) if version not in (None, "") else "no version"


def _manifest_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """One line per module the replacement adds, removes or changes, in display order."""
    keys = set(before) | set(after)
    ordered = [k for k in _MODULE_ORDER if k in keys] + sorted(k for k in keys if k not in _MODULE_ORDER)
    changes: list[str] = []
    for key in ordered:
        label = _MODULE_LABELS.get(key, _fallback_module_label(key))
        if key not in before:
            changes.append(f"{label} added")
        elif key not in after:
            changes.append(f"{label} removed")
        elif _as_module(before[key]) != _as_module(after[key]):
            old, new = _module_version(before[key]), _module_version(after[key])
            changes.append(f"{label} {old} → {new}" if old != new else f"{label} details changed")
    return changes


def _manifest_replacement_summary(
    *,
    prior_source: str | None,
    source: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> str:
    """The audit trail's one-line account of a manifest replacement."""
    changes = _manifest_changes(before, after)
    listed = "; ".join(changes[:_SUMMARY_MAX_CHANGES]) or "no module changed"
    if len(changes) > _SUMMARY_MAX_CHANGES:
        listed += f"; and {len(changes) - _SUMMARY_MAX_CHANGES} more"
    if prior_source is None:
        return f"Annotation manifest recorded ({source}): {listed}"
    return f"Annotation manifest replaced (was {prior_source}, now {source}): {listed}"


async def set_family_annotation_manifest(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    modules: dict[str, Any],
    source: str = "manual",
) -> dict[str, Any]:
    """Replace a family's pipeline manifest by hand (admin only), on the audit trail.

    The row is overwritten in place and every later sign-out freezes it into the signed
    report, so the replacement is recorded as a clinical audit event on the family's
    hash chain, with the manifest it replaced and the one it wrote, in the same
    transaction as the overwrite: neither persists without the other.
    """
    # Also enforced by the route (get_current_admin_user); checked here because this is
    # the one path that writes curated provenance, whoever calls it.
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user
    )
    # Serialise replacements of one family's manifest until this transaction ends, so the
    # "before" each event records is the manifest it really replaced.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"fam-manifest:{context.family_uuid}"},
    )
    prior = await _pipeline_manifest(
        session, family_uuid=context.family_uuid, family_id=family_id, user=user
    )
    new_modules = modules or {}
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
            "modules": json.dumps(new_modules),
            "source": source,
            "recorded_by": getattr(user, "email", None),
        },
    )
    prior_recorded_at = prior["recorded_at"]
    await record_clinical_event(
        session,
        family_uuid=context.family_uuid,
        family_identifier=context.family_id,
        variant_id=None,
        actor=getattr(user, "username", None) or getattr(user, "email", "") or "unknown",
        actor_id=getattr(user, "id", None),
        action="annotation_manifest",
        summary=_manifest_replacement_summary(
            prior_source=prior["source"], source=source, before=prior["modules"], after=new_modules
        ),
        before={
            "source": prior["source"],
            "recorded_by": prior["recorded_by"],
            "recorded_at": (
                prior_recorded_at.isoformat()
                if hasattr(prior_recorded_at, "isoformat")
                else prior_recorded_at
            ),
            "modules": prior["modules"],
        },
        after={"source": source, "modules": new_modules},
    )
    await session.commit()
    return await get_family_annotation_manifest(session, family_id=family_id, user=user)
