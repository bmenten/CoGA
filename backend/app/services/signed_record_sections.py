"""The sections of the signed analysis record that describe what the analysis looked at.

A sign-out closes and freezes the family's analysis (owner decision of 2026-10-14: the
medical sign-off of the patient report happens in the EPD, which refers to this record by
its report code). Besides the reported reviews and the checks
(``report_signout_service.build_report_snapshot``), the record freezes, each as a section
of its own:

* ``variant_descriptions`` — each reported variant as the analyst reviewed it: gene,
  transcript, HGVS c. and p., consequence and each sample's genotype for a small variant;
  type, locus, length, genes and each sample's genotype and copy number for a structural
  variant or CNV, one entry per callset or caller that calls it (REQ-TRACE-007);
* ``gene_panels`` — each gene-panel version the family's searches applied, with the release
  of its source (REQ-CARR-003; ``panel_use_audit``);
* ``reference_data`` — beside the manifest's modules, the releases of the reference data
  the analysis read: the gene reference for the reported variants' genes, and the clinical
  CNV knowledgebase (TRACE-5). PanelApp states a release per panel, which ``gene_panels``
  holds;
* ``repeat_expansions`` — each TRGT call of the family's samples, with its status as CoGA
  classifies it at sign-out and the thresholds and catalogue behind it;
* ``paraphase`` — each Paraphase result of the family's samples, with its status as CoGA
  classifies it at sign-out and the catalogue rule behind it.

Every section is built inside a savepoint. A section that cannot be read is frozen as
``{"unavailable": <a fixed sentence>}``, never as an empty one that would read as "nothing
there" (#514), and ``report_signout_service.snapshot_gaps`` names it. A record signed before
CoGA recorded a section does not hold it: the sign-out check lists it as not compared, and
the signed view says the record does not hold it.

Adding a section (the NIPT and PGT results follow this pattern):

1. write its builder here: ``async (session, inputs) -> dict``, JSON-safe and deterministic
   (lists sorted, no clock, no random order), reading what the analysis showed the analyst;
2. add it to ``RECORD_SECTIONS`` and to ``build_record_sections``. It is then hashed with
   the record, compared by the sign-out check (``REPORT_CONTENT_SECTIONS`` includes every
   key here) and named in ``not_captured`` when it could not be read;
3. read it in ``frontend/src/pages/families/signedReportRecord.ts`` (``parseRecordSection``
   gives the absent / unavailable / recorded states) and render it in
   ``SignedFamilyReport.tsx`` (``RecordSectionBlock``), with its label in
   ``REPORT_SECTION_LABELS``: a key the view does not render is missing from the signed
   view without a trace;
4. document it in ``docs/clinical-traceability.md`` (*Sign-out*).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import VariantOut
from .clickhouse_family_variants import (
    MAX_VARIANT_PAGE_SIZE,
    get_family_small_variants_page,
    get_family_structural_variants_page,
)
from .family_metadata_context import FamilyMetadataContext
from .panel_use_audit import applied_gene_panels
from .paraphase_pg import (
    _paraphase_region_for_gene,
    get_family_paraphase_table_response,
    load_paraphase_medical_regions,
)
from .repeat_expansion_pg import get_family_repeat_expansion_table_response

logger = logging.getLogger(__name__)

# The review tag that puts a variant on the report.
REPORT_TAG = "report"


@dataclass(frozen=True, slots=True)
class RecordSection:
    """A section of the signed record: its snapshot key, the words that name it, and the
    fixed sentence frozen in its place when it cannot be read."""

    key: str
    label: str
    unavailable: str


VARIANT_DESCRIPTIONS = RecordSection(
    "variant_descriptions",
    "Variant descriptions",
    "The reported variants' descriptions could not be read.",
)
GENE_PANELS = RecordSection(
    "gene_panels", "Gene panels applied", "The gene panels the analysis applied could not be read."
)
REFERENCE_DATA = RecordSection(
    "reference_data",
    "Reference-data releases",
    "The reference-data releases could not be read.",
)
REPEAT_EXPANSIONS = RecordSection(
    "repeat_expansions",
    "Repeat expansions (TRGT)",
    "The repeat-expansion calls could not be read.",
)
PARAPHASE = RecordSection(
    "paraphase", "Paraphase results", "The Paraphase results could not be read."
)

RECORD_SECTIONS: tuple[RecordSection, ...] = (
    VARIANT_DESCRIPTIONS,
    GENE_PANELS,
    REFERENCE_DATA,
    REPEAT_EXPANSIONS,
    PARAPHASE,
)

# The parts of ``reference_data``, each frozen as unavailable on its own.
REFERENCE_DATA_PARTS: dict[str, tuple[str, str]] = {
    "gene_reference": ("Gene reference", "The gene reference's releases could not be read."),
    "clinical_cnvs": (
        "Clinical CNV knowledgebase",
        "The clinical CNV knowledgebase's release could not be read.",
    ),
}


def _text(value: Any) -> str | None:
    text_value = str(value).strip() if value is not None else ""
    return text_value or None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return _text(value)


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _digest(value: Any) -> str:
    """SHA-256 over a canonical JSON encoding, as the record's own content hash is made."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


async def _guarded(
    session: AsyncSession, unavailable: str, build: Callable[[], Awaitable[Any]]
) -> Any:
    """``build()``, inside a savepoint; the fixed marker when it fails.

    A failed statement inside the savepoint is rolled back to it, so it cannot poison the
    sign-out's transaction. Nothing of the failure is frozen but the fixed sentence: an
    exception's text may quote the values it was reading.
    """
    try:
        async with session.begin_nested():
            return await build()
    except Exception:  # a section that cannot be read must not break the sign-out (#514)
        logger.warning("A signed-record section could not be read: %s", unavailable, exc_info=True)
        return {"unavailable": unavailable}


# --- Variant descriptions ------------------------------------------------------


def _small_description(variant: VariantOut) -> dict[str, Any]:
    return {
        "variant_id": str(variant.id),
        "source": variant.source,
        "chrom": variant.chr,
        "start": variant.start,
        "end": variant.end,
        "ref": variant.ref,
        "alt": variant.alt,
        "gene": variant.gene,
        "gene_id": variant.gene_id,
        "transcript": variant.transcript_id,
        "hgvsc": variant.hgvsc,
        "hgvsp": variant.hgvsp,
        "consequence": variant.effect,
        "impact": variant.impact,
        "mane_select": bool(variant.mane_select),
        "canonical": bool(variant.canonical),
        "genotypes": sorted(
            (
                {
                    "sample": genotype.sample,
                    "gt": genotype.gt,
                    "dp": genotype.dp,
                    "gq": genotype.gq,
                    "ad": list(genotype.ad) if genotype.ad is not None else None,
                    "af": list(genotype.af) if genotype.af is not None else None,
                }
                for genotype in variant.genotypes
            ),
            key=lambda genotype: str(genotype["sample"]),
        ),
    }


def _structural_description(variant: VariantOut) -> dict[str, Any]:
    extra = variant.annotation_extra or {}
    return {
        "variant_id": str(variant.id),
        "source": variant.source,
        "sv_type": variant.type,
        # Whether it removes or adds a copy of what it spans, as the SV ranking reads it.
        "copy_change": variant.copy_change,
        "chrom": variant.chr,
        "start": variant.start,
        "end": variant.end,
        "length": variant.length,
        "remote_chrom": variant.remote_chr,
        "remote_start": variant.remote_start,
        "genes": list(variant.gene_symbols or ([variant.gene] if variant.gene else [])),
        "cytoband": _text(variant.cytoband or extra.get("cytoband")),
        "genotypes": sorted(
            (
                {
                    "sample": genotype.sample,
                    "gt": genotype.gt,
                    "cn": genotype.cn,
                    "read_support": genotype.read_support,
                    "qual": genotype.qual,
                    "filter": genotype.filter,
                }
                for genotype in variant.genotypes
            ),
            key=lambda genotype: str(genotype["sample"]),
        ),
    }


def _description_order(entry: Mapping[str, Any]) -> tuple[str, str]:
    return (str(entry.get("variant_id") or ""), str(entry.get("source") or ""))


async def variant_descriptions(
    session: AsyncSession,
    context: FamilyMetadataContext,
    *,
    small_ids: Iterable[str],
    structural_ids: Iterable[str],
) -> dict[str, Any]:
    """Each reported variant as the family's variant lists serve it, the report page's list
    included: one entry per callset or caller that calls it."""
    wanted_small = {str(variant_id) for variant_id in small_ids if variant_id}
    wanted_structural = {str(variant_id) for variant_id in structural_ids if variant_id}
    small: list[dict[str, Any]] = []
    structural: list[dict[str, Any]] = []
    if wanted_small:
        page = await get_family_small_variants_page(
            session,
            context=context,
            page=1,
            page_size=MAX_VARIANT_PAGE_SIZE,
            review_tags=[REPORT_TAG],
        )
        small = [
            _small_description(variant)
            for variant in page.variants
            if str(variant.id) in wanted_small
        ]
    if wanted_structural:
        page = await get_family_structural_variants_page(
            session,
            context=context,
            page=1,
            page_size=MAX_VARIANT_PAGE_SIZE,
            review_tags=[REPORT_TAG],
        )
        structural = [
            _structural_description(variant)
            for variant in page.variants
            if str(variant.id) in wanted_structural
        ]
    return {
        "small": sorted(small, key=_description_order),
        "structural": sorted(structural, key=_description_order),
    }


def described_genes(descriptions: Any) -> list[str]:
    """The genes the reported variants' descriptions name: a small variant's gene, and
    every gene a structural variant overlaps."""
    if not isinstance(descriptions, Mapping):
        return []
    genes: set[str] = set()
    for entry in descriptions.get("small") or []:
        if isinstance(entry, Mapping) and _text(entry.get("gene")):
            genes.add(str(entry["gene"]).strip())
    for entry in descriptions.get("structural") or []:
        if isinstance(entry, Mapping):
            genes.update(str(gene).strip() for gene in entry.get("genes") or [] if _text(gene))
    return sorted(genes)


# --- Reference data --------------------------------------------------------------

_GENE_REFERENCE_SQL = text(
    """
    SELECT gi.hgnc_symbol,
           source.key AS source,
           source.value ->> 'status' AS status,
           source.value ->> 'release' AS release,
           source.value -> 'release_detail' ->> 'checksum' AS checksum
    FROM gene_info gi
    CROSS JOIN LATERAL jsonb_each(COALESCE(gi.source_status, '{}'::jsonb)) AS source(key, value)
    WHERE gi.assembly_id = CAST(:assembly_id AS uuid)
      AND upper(gi.hgnc_symbol) = ANY(CAST(:symbols AS text[]))
    """
)

_CLINICAL_CNV_SQL = text(
    """
    SELECT source, source_version, source_release_date, source_url, performed_at, inserted
    FROM reference_dataset_imports
    WHERE assembly_id = CAST(:assembly_id AS uuid)
      AND dataset_type = 'clinical_cnvs'
    ORDER BY performed_at DESC
    LIMIT 1
    """
)


async def gene_reference_releases(
    session: AsyncSession, assembly_id: str | None, genes: list[str]
) -> dict[str, Any]:
    """Which release of each gene-reference source answered for each of ``genes``.

    The gene reference (``gene_info``) is cached per gene and refreshed source by source, and
    each gene's record names the release of each source that answered (or had nothing) and
    the checksum of the file it read. A partial refresh can leave genes on different
    releases, so the record lists them per gene, grouped.
    """
    looked_up = sorted({gene for gene in genes if gene})
    if not looked_up or not assembly_id:
        return {"genes": looked_up, "sources": [], "not_in_reference": looked_up}
    rows = (
        await session.execute(
            _GENE_REFERENCE_SQL,
            {"assembly_id": assembly_id, "symbols": sorted({gene.upper() for gene in looked_up})},
        )
    ).mappings().all()
    by_symbol = {gene.upper(): gene for gene in looked_up}
    groups: dict[tuple[str, str, str, str], set[str]] = {}
    found: set[str] = set()
    for row in rows:
        gene = by_symbol.get(str(row.get("hgnc_symbol") or "").upper())
        if gene is None:
            continue
        found.add(gene)
        key = (
            str(row.get("source") or ""),
            str(row.get("status") or ""),
            str(row.get("release") or ""),
            str(row.get("checksum") or ""),
        )
        groups.setdefault(key, set()).add(gene)
    return {
        "genes": looked_up,
        "sources": [
            {
                "source": source,
                "status": status or None,
                "release": release or None,
                "checksum": checksum or None,
                "genes": sorted(members),
            }
            for (source, status, release, checksum), members in sorted(groups.items())
        ],
        "not_in_reference": [gene for gene in looked_up if gene not in found],
    }


async def clinical_cnv_release(session: AsyncSession, assembly_id: str | None) -> dict[str, Any] | None:
    """The clinical CNV knowledgebase loaded for the assembly: where it came from, the release
    its source states (none for a rebuild, which reads the data current on the day) and when
    it was loaded. None when none is loaded."""
    if not assembly_id:
        return None
    row = (await session.execute(_CLINICAL_CNV_SQL, {"assembly_id": assembly_id})).mappings().first()
    if row is None:
        return None
    return {
        "source": _text(row.get("source")),
        "release": _text(row.get("source_version")),
        "release_date": _iso(row.get("source_release_date")),
        "source_url": _text(row.get("source_url")),
        "loaded_at": _iso(row.get("performed_at")),
        "records": _int_or_none(row.get("inserted")),
    }


async def reference_data(
    session: AsyncSession, context: FamilyMetadataContext, genes: list[str]
) -> dict[str, Any]:
    """The releases of the reference data the analysis read, each part frozen as unavailable
    on its own when it cannot be read."""
    assembly_id = getattr(context, "assembly_id", None)
    return {
        "gene_reference": await _guarded(
            session,
            REFERENCE_DATA_PARTS["gene_reference"][1],
            lambda: gene_reference_releases(session, assembly_id, genes),
        ),
        "clinical_cnvs": await _guarded(
            session,
            REFERENCE_DATA_PARTS["clinical_cnvs"][1],
            lambda: clinical_cnv_release(session, assembly_id),
        ),
    }


# --- Repeat expansions ------------------------------------------------------------

_REPEAT_CATALOGUE_SQL = text(
    """
    SELECT locus_id,
           gene,
           warning_min,
           pathogenic_min,
           metadata ->> 'benign_min' AS benign_min,
           metadata ->> 'benign_max' AS benign_max,
           metadata ->> 'pathogenic_max' AS pathogenic_max,
           metadata ->> 'premutation_min' AS premutation_min,
           metadata ->> 'source' AS source
    FROM repeat_loci
    ORDER BY locus_id
    """
)


async def repeat_catalogue_identity(session: AsyncSession) -> dict[str, Any]:
    """The TRGT catalogue the calls were classified against, as its content: the number of
    loci, the sources they came from, and a digest of every locus's thresholds. STRchive's
    file states no release, so the digest is what tells one catalogue from another."""
    rows = [dict(row) for row in (await session.execute(_REPEAT_CATALOGUE_SQL)).mappings().all()]
    entries = [
        {
            "locus_id": _text(row.get("locus_id")),
            "gene": _text(row.get("gene")),
            "intermediate_min": _int_or_none(row.get("warning_min")),
            "premutation_min": _int_or_none(row.get("premutation_min")),
            "pathogenic_min": _int_or_none(row.get("pathogenic_min")),
            "benign_min": _int_or_none(row.get("benign_min")),
            "benign_max": _int_or_none(row.get("benign_max")),
            "pathogenic_max": _int_or_none(row.get("pathogenic_max")),
            "source": _text(row.get("source")) or "CoGA built-in",
        }
        for row in rows
    ]
    return {
        "loci": len(entries),
        "sources": sorted({str(entry["source"]) for entry in entries}),
        "digest": _digest(entries),
    }


async def repeat_expansion_calls(
    session: AsyncSession, context: FamilyMetadataContext
) -> dict[str, Any]:
    """Every TRGT call of the family's samples as the repeat page classifies it now."""
    table = await get_family_repeat_expansion_table_response(session, context=context)
    loci: list[dict[str, Any]] = []
    for locus in table.loci:
        loci.append(
            {
                "locus_id": locus.locus_id,
                "gene": locus.gene,
                "disease": locus.disease,
                "inheritance": locus.inheritance,
                "chrom": locus.chr,
                "start": locus.start,
                "end": locus.end,
                "motif": locus.motif,
                "thresholds": {
                    "intermediate_min": locus.warning_min,
                    "premutation_min": locus.premutation_min,
                    "pathogenic_min": locus.pathogenic_min,
                    "benign_min": locus.benign_min,
                    "benign_max": locus.benign_max,
                    "pathogenic_max": locus.pathogenic_max,
                },
                "status": locus.status,
                "calls": [
                    {
                        "sample": call.sample,
                        "genotype": call.genotype,
                        "status": call.status,
                        "note": call.note,
                        "alleles": [
                            {
                                "repeat_count": allele.repeat_count,
                                "bp_length": allele.bp_length,
                                "support_reads": allele.support_reads,
                                "interruption_label": allele.interruption_label,
                                "status": allele.status,
                            }
                            for allele in call.alleles
                        ],
                    }
                    for _sample, call in sorted(locus.calls.items())
                ],
            }
        )
    loci.sort(key=lambda entry: (str(entry["chrom"]), int(entry["start"]), str(entry["locus_id"])))
    return {"catalogue": await repeat_catalogue_identity(session), "loci": loci}


# --- Paraphase --------------------------------------------------------------------


def paraphase_catalogue_identity() -> dict[str, Any]:
    """The catalogue of medically relevant Paraphase regions the statuses were read with: the
    number of regions and a digest of their rules and copy-number norms."""
    regions = load_paraphase_medical_regions()
    return {"regions": len(regions), "digest": _digest(regions)}


def _status_rule(region: Mapping[str, Any] | None) -> Any:
    rule = region.get("status_rule") if region else None
    return json.loads(json.dumps(rule, default=str)) if isinstance(rule, Mapping) else None


def _expected_copy_number(region: Mapping[str, Any] | None) -> dict[str, list[float]] | None:
    block = region.get("expected_copy_number") if region else None
    if not isinstance(block, Mapping) or not block:
        return None
    return {str(key): [float(low), float(high)] for key, (low, high) in sorted(block.items())}


async def paraphase_results(
    session: AsyncSession, context: FamilyMetadataContext
) -> dict[str, Any]:
    """Every Paraphase result of the family's samples as the Paraphase page reads it now."""
    table = await get_family_paraphase_table_response(session, context=context)
    genes: list[dict[str, Any]] = []
    for gene in table.genes:
        region = _paraphase_region_for_gene(gene.gene_symbol)
        genes.append(
            {
                "gene": gene.gene_symbol,
                "region_id": region.get("region_id") if region else None,
                "region": region.get("display_name") if region else None,
                "medically_relevant": gene.is_medically_relevant,
                "status_rule": _status_rule(region),
                "expected_copy_number": _expected_copy_number(region),
                "samples": [
                    {
                        "sample": result.sample,
                        "status": result.clinical_status,
                        "copy_number_signal": result.copy_number_signal,
                        "total_cn": result.total_cn,
                        "gene_cn": result.gene_cn,
                        "highest_total_cn": result.highest_total_cn,
                        "sample_sex": result.sample_sex,
                        "copy_number": [
                            {"key": metric.key, "label": metric.label, "value": metric.value}
                            for metric in result.copy_number_metrics
                        ],
                        "fusion_count": result.fusion_count,
                        "loaded_at": _iso(result.uploaded_at),
                    }
                    for _sample, result in sorted(gene.samples.items())
                ],
            }
        )
    genes.sort(key=lambda entry: str(entry["gene"]).lower())
    return {"catalogue": paraphase_catalogue_identity(), "genes": genes}


# --- The sections together ----------------------------------------------------------


async def build_record_sections(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    reported: Sequence[Mapping[str, Any]],
    reported_structural: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Every section of ``RECORD_SECTIONS``, keyed by its snapshot key."""
    descriptions = await _guarded(
        session,
        VARIANT_DESCRIPTIONS.unavailable,
        lambda: variant_descriptions(
            session,
            context,
            small_ids=[str(entry.get("variant_id") or "") for entry in reported],
            structural_ids=[str(entry.get("variant_id") or "") for entry in reported_structural],
        ),
    )
    return {
        VARIANT_DESCRIPTIONS.key: descriptions,
        GENE_PANELS.key: await _guarded(
            session,
            GENE_PANELS.unavailable,
            lambda: _gene_panels(session, context),
        ),
        REFERENCE_DATA.key: await reference_data(session, context, described_genes(descriptions)),
        REPEAT_EXPANSIONS.key: await _guarded(
            session,
            REPEAT_EXPANSIONS.unavailable,
            lambda: repeat_expansion_calls(session, context),
        ),
        PARAPHASE.key: await _guarded(
            session, PARAPHASE.unavailable, lambda: paraphase_results(session, context)
        ),
    }


async def _gene_panels(session: AsyncSession, context: FamilyMetadataContext) -> dict[str, Any]:
    return {"applied": await applied_gene_panels(session, context.family_uuid)}


def record_section_gaps(snapshot: Mapping[str, Any]) -> list[dict[str, str]]:
    """The sections of ``snapshot`` frozen as unavailable, with the sentence frozen for each."""
    gaps: list[dict[str, str]] = []
    for section in RECORD_SECTIONS:
        value = snapshot.get(section.key)
        if not isinstance(value, Mapping):
            continue
        if value.get("unavailable"):
            gaps.append(
                {"section": section.key, "item": section.label, "reason": str(value["unavailable"])}
            )
            continue
        if section is REFERENCE_DATA:
            for part, (label, _sentence) in REFERENCE_DATA_PARTS.items():
                part_value = value.get(part)
                if isinstance(part_value, Mapping) and part_value.get("unavailable"):
                    gaps.append(
                        {
                            "section": f"{section.key}.{part}",
                            "item": label,
                            "reason": str(part_value["unavailable"]),
                        }
                    )
    return gaps
