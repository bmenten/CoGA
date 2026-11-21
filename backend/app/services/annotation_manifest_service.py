"""Per-analysis annotation/reference version manifest (clinical traceability, Phase 0).

Consolidates which annotation modules/versions backed an analysis's data so its report
can state its provenance (see docs/clinical-traceability.md). It merges two layers:

* the *pipeline* manifest — the upstream tools that produced the analysis's annotated
  VCF (VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, …), recorded in
  ``family_annotation_manifest``: by an import, from the VCF headers (``vcf_header``) or
  the package's run manifest (``manifest``), or by an admin's replacement (``manual``).
* the platform *reference* layer — versions of what CoGA itself loaded (the assembly,
  the gene loci, the Monarch and HPO releases), read live from the reference tables.

A family may hold several analyses, each imported from a package of its own: short-read and
then long-read WGS of the same DNA, or the monogenic and the conventional NIPT of each of a
woman's pregnancies. So the pipeline manifest is kept per analysis: one row per family and
analysis, keyed as the family's package records are (``family_package_records.package_record_key``:
a NIPT's key names its pregnancy). An import writes
its own analysis's row and keeps the others; a single-file upload, which belongs to no analysis,
writes the row of no analysis (``UNSPECIFIED_ANALYSIS``), as a package that names none does.

What an analysis's manifest states (``analysis_manifest``), and so what a sign-out of it freezes:
its own row, laid over the rows of no analysis the family holds, which are every analysis's (the
scope rule of ``signout_scope``). A family that holds no analysis has one manifest, of those rows.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .analysis_scope import analysis_holding
from .clinical_audit_service import audit_actor, record_clinical_event
from .family_analysis_types import family_analyses
from .family_metadata_context import FamilyMetadataContext, build_family_metadata_context
from .family_package_records import UNSPECIFIED_ANALYSIS, analysis_label
from .hpo_service import get_loaded_hpo_release
from .access_control import CurrentUser, is_admin_user
from .stored_values import json_object as _as_dict

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
    "clinvar_vep_cache": "ClinVar (VEP cache)",
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


async def _lock_family_manifest(session: AsyncSession, family_uuid: str) -> None:
    """Hold this family's manifest lock until the transaction ends.

    Both writers of ``family_annotation_manifest`` take it before they read the family's rows:
    an import's merge and an admin's replacement. A replacement therefore never lands
    between an import's read and its write (the import would overwrite the manual
    manifest it never saw), and each replacement's audit event records the manifest it
    really replaced. A replacement waits for an import of the same family to commit.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"fam-manifest:{family_uuid}"},
    )


def manifest_key(analysis: str | None) -> str:
    """The row of the family's manifest that holds the versions of ``analysis``: its package
    record's key; for a family that holds no analysis (None), the row of no analysis."""
    return analysis or UNSPECIFIED_ANALYSIS


async def _family_manifest_row(
    session: AsyncSession, family_uuid: str, analysis: str
) -> dict[str, Any] | None:
    """The family's manifest row of one analysis (``manifest_key``), or None."""
    row = (
        await session.execute(
            text(
                """
                SELECT analysis_type, modules, source, recorded_by, recorded_at
                FROM family_annotation_manifest
                WHERE family_id = CAST(:family_uuid AS uuid) AND analysis_type = :analysis
                """
            ),
            {"family_uuid": family_uuid, "analysis": analysis},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _family_manifest_rows(session: AsyncSession, family_uuid: str) -> list[dict[str, Any]]:
    """Every manifest row of the family, one per analysis, by analysis."""
    rows = (
        await session.execute(
            text(
                """
                SELECT analysis_type, modules, source, recorded_by, recorded_at
                FROM family_annotation_manifest
                WHERE family_id = CAST(:family_uuid AS uuid)
                ORDER BY analysis_type
                """
            ),
            {"family_uuid": family_uuid},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


_FAMILY_METADATA = text("SELECT metadata FROM families WHERE id = CAST(:family_uuid AS uuid)")


async def _family_metadata(session: AsyncSession, family_uuid: str) -> dict[str, Any]:
    """The family's ``families.metadata``: its analyses and its package records."""
    metadata = (await session.execute(_FAMILY_METADATA, {"family_uuid": family_uuid})).scalar_one_or_none()
    return _as_dict(metadata)


# Stands in for a module version the provenance lookup could not read.
UNAVAILABLE_MODULE_VERSION = "unavailable"


async def _platform_modules(session: AsyncSession, assembly_id: str | None) -> dict[str, dict[str, Any]]:
    """Versions of the reference layer CoGA loaded — best-effort, never fatal.

    This runs inside sign-out, so each lookup is a SAVEPOINT: a failed statement cannot
    poison the caller's transaction. A failure is logged and recorded as an explicit
    ``unavailable`` module, so the frozen record says the version could not be read
    instead of silently leaving the module out.
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
        except Exception:  # provenance must never break sign-out
            logger.warning("Reference-assembly provenance lookup failed", exc_info=True)
            modules["assembly"] = {"version": UNAVAILABLE_MODULE_VERSION, "detail": "lookup failed"}
        # The gene loci CoGA itself loaded for the assembly (panel regions, gene tracks) and
        # where they came from: GENCODE, or the UCSC table used when GENCODE could not be
        # fetched. Distinct from the pipeline's own annotation version.
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
        except Exception:  # provenance must never break sign-out
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
    except Exception:  # provenance must never break sign-out
        logger.warning("Monarch-release provenance lookup failed", exc_info=True)
        modules["monarch"] = {"version": UNAVAILABLE_MODULE_VERSION, "detail": "lookup failed"}
    # The HPO release phenotype matching, HPO-driven ranking and the phenotype features ran
    # on: the release of the latest ontology import (see get_loaded_hpo_release, which the
    # admin summary and the ranking-cache key read too).
    try:
        async with session.begin_nested():
            hpo_release = await get_loaded_hpo_release(session)
        if hpo_release:
            # Every import records the release its file states (import_hpo_ontology), with the
            # release date and the SHA-256 of the file the terms came from.
            details = [str(hpo_release["release_date"] or ""), f"SHA-256 {hpo_release['release_sha256']}"]
            modules["hpo"] = {
                "version": str(hpo_release["release_version"]),
                "detail": "; ".join(d for d in details if d) or None,
            }
    except Exception:  # provenance must never break sign-out
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
                # Per-modality versions: which modality's pipeline
                # cited which release — so SNV vs SV divergence is preserved.
                "by_modality": (
                    {str(k): str(v) for k, v in by_modality.items() if v not in (None, "")}
                    if isinstance(by_modality, dict) and by_modality
                    else None
                ),
            }
        )
    return result


def _holds_modules(row: Mapping[str, Any]) -> bool:
    return bool(_as_dict(row.get("modules")))


def _stated_rows(
    rows: Sequence[Mapping[str, Any]], *, analysis: str | None
) -> list[Mapping[str, Any]]:
    """The manifest rows a manifest of ``analysis`` states, of the family's ``rows``, in the order they are laid over each other: the row of no
    analysis (``UNSPECIFIED_ANALYSIS``: a single-file upload's, a package's that named none), as
    that data is every analysis's; then the analysis's own, which wins. The row of an analysis
    the family does not hold (an import that was put back, whose data are gone) states nothing.
    An analysis's own row an administrator replaced (``manual``) is stated alone: the
    replacement is the whole manifest of that analysis. A row that records no module states
    nothing."""
    own_key = manifest_key(analysis)
    own = next((row for row in rows if row.get("analysis_type") == own_key), None)
    if own is not None and own.get("source") == _MANUAL_SOURCE:
        return [own]
    shared = [
        row
        for row in rows
        if row.get("analysis_type") == UNSPECIFIED_ANALYSIS
        and row.get("analysis_type") != own_key
        and _holds_modules(row)
    ]
    return [*shared, *([own] if own is not None and _holds_modules(own) else [])]


def _stated_modules(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each module as ``rows`` state it, a later row's over an earlier's: its fields replaced
    whole, its versions per modality joined, the later row's for a modality both name. So an
    upload's SV caller stays beside the analysis's own SNV annotation, and the analysis's own
    release of a database wins over an upload's."""
    stated: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key, value in _as_dict(row.get("modules")).items():
            module = dict(_as_module(value))
            earlier = stated.get(key, {}).get("by_modality")
            later = module.get("by_modality")
            joined = {
                **(earlier if isinstance(earlier, dict) else {}),
                **(later if isinstance(later, dict) else {}),
            }
            if joined:
                module["by_modality"] = joined
            stated[key] = module
    return stated


def analysis_manifest(
    rows: Sequence[Mapping[str, Any]],
    *,
    analysis: str | None,
    platform_modules: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """The manifest of one of the family's analyses (``analysis``; None for a family that holds
    none), from the family's manifest ``rows``: the modules its rows state (``_stated_rows``)
    over the platform's reference layer, with where the row stated last came from (the
    analysis's own, when it has one)."""
    stated = _stated_rows(rows, analysis=analysis)
    last: Mapping[str, Any] = stated[-1] if stated else {}
    return {
        "analysis_type": analysis,
        "source": last.get("source"),
        "recorded_at": last.get("recorded_at"),
        "recorded_by": last.get("recorded_by"),
        "modules": _module_list(_stated_modules(stated), platform_modules),
    }


async def get_family_annotation_manifest(
    session: AsyncSession, *, family_id: str, user: CurrentUser, project_id: str | None = None
) -> dict[str, Any]:
    """The family's annotation manifests, one per analysis it holds (by analysis), each as a
    sign-out of that analysis freezes it; one, of no analysis, for a family that holds none."""
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user, project_id=project_id
    )
    held = family_analyses(await _family_metadata(session, context.family_uuid))
    rows = await _family_manifest_rows(session, context.family_uuid)
    platform_modules = await _platform_modules(session, context.assembly_id)
    analyses: list[str | None] = [*sorted(held)] or [None]
    return {
        "family_id": context.family_id,
        "assembly": context.assembly_name,
        "analyses": [
            analysis_manifest(rows, analysis=analysis, platform_modules=platform_modules)
            for analysis in analyses
        ],
    }


async def analysis_annotation_manifest(
    session: AsyncSession, context: FamilyMetadataContext, *, analysis: str | None
) -> dict[str, Any]:
    """The annotation manifest a sign-out of the family's ``analysis`` freezes (None: a family
    that holds none), with the family's assembly: that analysis's versions, never another's."""
    rows = await _family_manifest_rows(session, context.family_uuid)
    platform_modules = await _platform_modules(session, context.assembly_id)
    return {
        "assembly": context.assembly_name,
        **analysis_manifest(rows, analysis=analysis, platform_modules=platform_modules),
    }


async def call_annotation_manifest(
    session: AsyncSession, context: FamilyMetadataContext, *, kind: str, stored_under: str | None
) -> dict[str, Any]:
    """The annotation manifest of the analysis a call of the family is (of the
    ``family_package_records.SOURCE_KINDS`` ``kind``, stored under the source ``stored_under``):
    that of the one analysis the family holds whose package record lists that source, else that
    of no analysis (a single-file upload's call, or one of a package that named none)."""
    metadata = await _family_metadata(session, context.family_uuid)
    rows = await _family_manifest_rows(session, context.family_uuid)
    platform_modules = await _platform_modules(session, context.assembly_id)
    return analysis_manifest(
        rows,
        analysis=analysis_holding(metadata, kind, str(stored_under or "")),
        platform_modules=platform_modules,
    )


# The ClinVar modules name where a classification comes from: the ClinVar file the pipeline
# annotated, or VEP's cache (``vcf_header_provenance.clinvar_file_module``). A new import's
# module replaces the stored version and detail together, and a version it does not state
# drops the modality's. Merged field by field, one import's cache release would stay beside a
# ClinVar file named without a date, and read as that file's release.
_SOURCE_BOUND_MODULES = frozenset({"clinvar", "clinvar_vep_cache"})


def _refresh_modules(
    current: Mapping[str, Any], incoming: Mapping[str, Any], modality: str | None = None
) -> dict[str, dict[str, Any]]:
    """Merge freshly parsed modules into the stored ones. Newly parsed versions
    win per-key (a re-import reflects the latest annotation), while modules the new
    input does not mention are preserved (so re-importing one modality does not
    wipe another's provenance).

    When ``modality`` is given, each module's version is also recorded under
    ``by_modality[modality]`` — so the same database cited at different
    releases by different modalities (e.g. GENCODE 49 for SNV, 45 for SV) keeps the
    full per-modality truth alongside the flat representative ``version``."""
    out: dict[str, dict[str, Any]] = {
        key: dict(_as_module(value)) for key, value in (current or {}).items()
    }
    for key, value in (incoming or {}).items():
        entry = out.setdefault(key, {})
        incoming_entry = _as_module(value)
        if key in _SOURCE_BOUND_MODULES:
            entry.pop("version", None)
            entry.pop("detail", None)
            stored_by = entry.get("by_modality")
            if modality and isinstance(stored_by, dict) and incoming_entry.get("version") in (None, ""):
                entry["by_modality"] = {
                    name: version for name, version in stored_by.items() if name != str(modality)
                }
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
    analysis: str,
    modality: str | None = None,
    recorded_by: str = "import (vcf_header)",
    source: str = "vcf_header",
) -> None:
    """Best-effort: fold harvested module versions into the annotation manifest of the
    family's ``analysis`` under ``source`` (``'vcf_header'`` by default).

    ``analysis`` is the analysis whose data the versions came with: the key of its package
    record (``family_package_records.package_record_key`` of the package's analysis type), or
    ``UNSPECIFIED_ANALYSIS`` for a single-file upload, which belongs to no analysis. Only that
    analysis's row is written: another analysis's versions stay as its own import recorded
    them.

    ``source`` records *where* the versions came from. Most arrive from a VCF header;
    the long-read pipeline instead ships a run manifest (``software_versions.yaml``),
    which is recorded as ``'manifest'`` so the provenance is not mislabelled as
    parsed-from-a-header. ``'manual'`` remains reserved for admin-curated entries and
    is never written here.

    * **Never overwrites a ``manual`` manifest** — an admin's curated provenance
      wins over anything parsed from a header.
    * **Takes turns with a replacement** — it holds the family's manifest lock from
      before its read until the import commits, so an admin's replacement cannot land
      in between and be overwritten.
    * **Refreshes on re-import** — newly parsed versions overwrite stale ones,
      while untouched modules are preserved.
    * **Never raises and never poisons the caller's transaction** — the write runs
      in a SAVEPOINT and joins the caller's commit, so provenance persists iff the
      ingestion that produced it does.
    """
    if not modules:
        return
    key = manifest_key(analysis)
    # 'manual' is written only by the admin-curation path; accepting it here would let
    # an import masquerade as curated provenance and then be immune to later refreshes.
    recorded_source = source if source != "manual" else "vcf_header"
    try:
        async with session.begin_nested():
            # Taken inside the SAVEPOINT, but a released savepoint hands its locks to
            # the enclosing transaction, so it is held until the import commits.
            await _lock_family_manifest(session, family_uuid)
            existing = await _family_manifest_row(session, family_uuid, key)
            if existing and existing.get("source") == "manual":
                return  # respect admin-curated provenance
            current = _as_dict(existing.get("modules")) if existing else {}
            merged = _refresh_modules(current, modules, modality=modality)
            await session.execute(
                text(
                    """
                    INSERT INTO family_annotation_manifest
                        (family_id, analysis_type, assembly_id, modules, source, recorded_by, recorded_at)
                    VALUES
                        (CAST(:family_uuid AS uuid), :analysis,
                         -- NULLIF, not `CASE WHEN :assembly_id IS NULL`: asyncpg cannot
                         -- infer a parameter's type when one of its uses is a bare
                         -- `IS NULL`, and raises AmbiguousParameterError. The merge is
                         -- best-effort and logs rather than raises, so that INSERT would
                         -- fail on every call and the manifest never be written, without a
                         -- word. test_sql_parameter_typing.py guards it.
                         CAST(NULLIF(:assembly_id, '') AS uuid),
                         CAST(:modules AS jsonb), :source, :recorded_by, now())
                    ON CONFLICT (family_id, analysis_type) DO UPDATE SET
                        modules = EXCLUDED.modules,
                        source = EXCLUDED.source,
                        recorded_by = EXCLUDED.recorded_by,
                        recorded_at = now(),
                        assembly_id = COALESCE(EXCLUDED.assembly_id, family_annotation_manifest.assembly_id)
                    """
                ),
                {
                    "family_uuid": family_uuid,
                    "analysis": key,
                    # '' rather than None so NULLIF above resolves it to SQL NULL with a
                    # parameter type asyncpg can infer.
                    "assembly_id": assembly_id or "",
                    "modules": json.dumps(merged),
                    "recorded_by": recorded_by,
                    "source": recorded_source,
                },
            )
    except Exception:  # provenance capture must never break ingestion
        logger.warning("%s provenance capture failed for family %s", recorded_source, family_uuid, exc_info=True)


# The audit summary names at most this many module changes; before/after hold them all.
_SUMMARY_MAX_CHANGES = 6
# The source of every replacement made through set_family_annotation_manifest.
_MANUAL_SOURCE = "manual"


def _module_version(value: Any) -> str:
    version = _as_module(value).get("version")
    return str(version) if version not in (None, "") else "no version"


def _manifest_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """One line per module the replacement adds, removes or changes, in display order."""
    keys = set(before) | set(after)
    ordered = [k for k in _MODULE_ORDER if k in keys] + sorted(k for k in keys if k not in _MODULE_ORDER)
    changes: list[str] = []
    for key in ordered:
        label = module_label(key)
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
    analysis: str | None = None,
) -> str:
    """The audit trail's one-line account of a manifest replacement, naming the analysis whose
    manifest it is (none for a family that holds no analysis)."""
    changes = _manifest_changes(before, after)
    listed = "; ".join(changes[:_SUMMARY_MAX_CHANGES]) or "no module changed"
    if len(changes) > _SUMMARY_MAX_CHANGES:
        listed += f"; and {len(changes) - _SUMMARY_MAX_CHANGES} more"
    of = f" of the {analysis_label(analysis) or analysis}" if analysis else ""
    if prior_source is None:
        return f"Annotation manifest{of} recorded ({source}): {listed}"
    return f"Annotation manifest{of} replaced (was {prior_source}, now {source}): {listed}"


def resolve_manifest_analysis(held: Collection[str], asked: str | None) -> str | None:
    """The analysis whose manifest a replacement is of: ``asked``, which the family must hold
    (``held``); without one, the family's only analysis, or None for a family that holds none.
    A family that holds two or more must be asked which: 422 otherwise, as for an analysis it
    does not hold."""
    ordered = sorted(held)
    if asked is not None:
        if asked not in held:
            raise HTTPException(
                status_code=422,
                detail="This family holds no such analysis"
                + (f"; it holds {', '.join(ordered)}." if ordered else "."),
            )
        return asked
    if len(ordered) > 1:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This family holds several analyses ({', '.join(ordered)}): name the one whose "
                "annotation manifest to replace."
            ),
        )
    return ordered[0] if ordered else None


async def set_family_annotation_manifest(
    session: AsyncSession,
    *,
    family_id: str,
    user: CurrentUser,
    modules: dict[str, Any],
    analysis_type: str | None = None,
) -> dict[str, Any]:
    """Replace the pipeline manifest of one of a family's analyses by hand (admin only), on the
    audit trail: ``analysis_type`` (``resolve_manifest_analysis``; for a family that holds none,
    the manifest of no analysis).

    The replacement is always recorded as ``'manual'``: that is what it is, and it is
    the source an import never overwrites. It is that analysis's whole manifest
    (``_stated_rows``). The row is overwritten in place and every later sign-out of the
    analysis freezes it into the signed report, so the replacement is recorded as a clinical
    audit event on the family's hash chain, naming the analysis, with the manifest it replaced
    and the one it wrote, in the same transaction as the overwrite: neither persists without
    the other.
    """
    # Also enforced by the route (get_current_admin_user); checked here because this is
    # the one path that writes curated provenance, whoever calls it.
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    context = await build_family_metadata_context(
        session, family_identifier=family_id, user=user
    )
    held = family_analyses(await _family_metadata(session, context.family_uuid))
    analysis = resolve_manifest_analysis(held, analysis_type)
    await _lock_family_manifest(session, context.family_uuid)
    # What the analysis's manifest stated until now: the modules a sign-out of it froze.
    prior_rows = _stated_rows(
        await _family_manifest_rows(session, context.family_uuid), analysis=analysis
    )
    prior_last: Mapping[str, Any] = prior_rows[-1] if prior_rows else {}
    prior_modules = _stated_modules(prior_rows)
    new_modules = modules or {}
    await session.execute(
        text(
            """
            INSERT INTO family_annotation_manifest
                (family_id, analysis_type, assembly_id, modules, source, recorded_by, recorded_at)
            VALUES
                (CAST(:family_uuid AS uuid), :analysis, CAST(:assembly_id AS uuid),
                 CAST(:modules AS jsonb), :source, :recorded_by, now())
            ON CONFLICT (family_id, analysis_type) DO UPDATE SET
                modules = EXCLUDED.modules,
                source = EXCLUDED.source,
                recorded_by = EXCLUDED.recorded_by,
                recorded_at = now(),
                assembly_id = EXCLUDED.assembly_id
            """
        ),
        {
            "family_uuid": context.family_uuid,
            "analysis": manifest_key(analysis),
            "assembly_id": context.assembly_id,
            "modules": json.dumps(new_modules),
            "source": _MANUAL_SOURCE,
            "recorded_by": getattr(user, "email", None),
        },
    )
    prior_source = prior_last.get("source")
    prior_recorded_at: Any = prior_last.get("recorded_at")
    await record_clinical_event(
        session,
        family_uuid=context.family_uuid,
        family_identifier=context.family_id,
        variant_id=None,
        actor=audit_actor(user),
        actor_id=getattr(user, "id", None),
        action="annotation_manifest",
        summary=_manifest_replacement_summary(
            prior_source=prior_source,
            source=_MANUAL_SOURCE,
            before=prior_modules,
            after=new_modules,
            analysis=analysis,
        ),
        before={
            "analysis_type": analysis,
            "source": prior_source,
            "recorded_by": prior_last.get("recorded_by"),
            "recorded_at": (
                prior_recorded_at.isoformat()
                if hasattr(prior_recorded_at, "isoformat")
                else prior_recorded_at
            ),
            "modules": prior_modules,
        },
        after={"analysis_type": analysis, "source": _MANUAL_SOURCE, "modules": new_modules},
    )
    await session.commit()
    return await get_family_annotation_manifest(session, family_id=family_id, user=user)
