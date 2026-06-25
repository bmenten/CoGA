from __future__ import annotations

import json
import re
from typing import Any, Iterable, Sequence
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    FamilyMemberOut,
    FamilyMitoDNAAnalysisOut,
    MitoDNACoverageOut,
    MitoDNAQcOut,
    MitoDNASampleOut,
    MitoDNAVariantAnnotationOut,
    MitoDNAVariantOut,
    MitoDNAVariantSampleCallOut,
)
from .clickhouse_family_variants import (
    PanelFilterConstraints,
    SmallVariantCall,
    SmallVariantRecord,
    _fetch_small_variant_rows,
)
from .clickhouse_interval_tracks import fetch_interval_track_rows
from .family_metadata_context import FamilyMetadataContext
from .family_variant_filters import SmallVariantQueryFilters
from .small_variant_review_pg import get_small_variant_review_map


MT_GENOME_LENGTH = 16_569
HETEROPLASMY_THRESHOLD = 0.02
HOMOPLASMY_THRESHOLD = 0.95
MITOMAP_ALLELE_SEARCH_URL = "https://www.mitomap.org/cgi-bin/search_allele"
MAX_MTDNA_VARIANTS = 5_000

MT_LOCI: tuple[dict[str, Any], ...] = (
    {"gene": "MT-CR", "region": "control region", "category": "control", "start": 1, "end": 576},
    {"gene": "MT-TF", "region": "MT-TF", "category": "tRNA", "start": 577, "end": 647},
    {"gene": "MT-RNR1", "region": "MT-RNR1", "category": "rRNA", "start": 648, "end": 1601},
    {"gene": "MT-TV", "region": "MT-TV", "category": "tRNA", "start": 1602, "end": 1670},
    {"gene": "MT-RNR2", "region": "MT-RNR2", "category": "rRNA", "start": 1671, "end": 3229},
    {"gene": "MT-TL1", "region": "MT-TL1", "category": "tRNA", "start": 3230, "end": 3304},
    {"gene": "MT-ND1", "region": "MT-ND1", "category": "protein coding", "start": 3307, "end": 4262},
    {"gene": "MT-TI", "region": "MT-TI", "category": "tRNA", "start": 4263, "end": 4331},
    {"gene": "MT-TQ", "region": "MT-TQ", "category": "tRNA", "start": 4329, "end": 4400},
    {"gene": "MT-TM", "region": "MT-TM", "category": "tRNA", "start": 4402, "end": 4469},
    {"gene": "MT-ND2", "region": "MT-ND2", "category": "protein coding", "start": 4470, "end": 5511},
    {"gene": "MT-TW", "region": "MT-TW", "category": "tRNA", "start": 5512, "end": 5579},
    {"gene": "MT-TA", "region": "MT-TA", "category": "tRNA", "start": 5587, "end": 5655},
    {"gene": "MT-TN", "region": "MT-TN", "category": "tRNA", "start": 5657, "end": 5729},
    {"gene": "MT-TC", "region": "MT-TC", "category": "tRNA", "start": 5761, "end": 5826},
    {"gene": "MT-TY", "region": "MT-TY", "category": "tRNA", "start": 5826, "end": 5891},
    {"gene": "MT-CO1", "region": "MT-CO1", "category": "protein coding", "start": 5904, "end": 7445},
    {"gene": "MT-TS1", "region": "MT-TS1", "category": "tRNA", "start": 7446, "end": 7514},
    {"gene": "MT-TD", "region": "MT-TD", "category": "tRNA", "start": 7518, "end": 7585},
    {"gene": "MT-CO2", "region": "MT-CO2", "category": "protein coding", "start": 7586, "end": 8269},
    {"gene": "MT-TK", "region": "MT-TK", "category": "tRNA", "start": 8295, "end": 8364},
    {"gene": "MT-ATP8", "region": "MT-ATP8", "category": "protein coding", "start": 8366, "end": 8572},
    {"gene": "MT-ATP6", "region": "MT-ATP6", "category": "protein coding", "start": 8527, "end": 9207},
    {"gene": "MT-CO3", "region": "MT-CO3", "category": "protein coding", "start": 9207, "end": 9990},
    {"gene": "MT-TG", "region": "MT-TG", "category": "tRNA", "start": 9991, "end": 10058},
    {"gene": "MT-ND3", "region": "MT-ND3", "category": "protein coding", "start": 10059, "end": 10404},
    {"gene": "MT-TR", "region": "MT-TR", "category": "tRNA", "start": 10405, "end": 10469},
    {"gene": "MT-ND4L", "region": "MT-ND4L", "category": "protein coding", "start": 10470, "end": 10766},
    {"gene": "MT-ND4", "region": "MT-ND4", "category": "protein coding", "start": 10760, "end": 12137},
    {"gene": "MT-TH", "region": "MT-TH", "category": "tRNA", "start": 12138, "end": 12206},
    {"gene": "MT-TS2", "region": "MT-TS2", "category": "tRNA", "start": 12207, "end": 12265},
    {"gene": "MT-TL2", "region": "MT-TL2", "category": "tRNA", "start": 12266, "end": 12336},
    {"gene": "MT-ND5", "region": "MT-ND5", "category": "protein coding", "start": 12337, "end": 14148},
    {"gene": "MT-ND6", "region": "MT-ND6", "category": "protein coding", "start": 14149, "end": 14673},
    {"gene": "MT-TE", "region": "MT-TE", "category": "tRNA", "start": 14674, "end": 14742},
    {"gene": "MT-CYB", "region": "MT-CYB", "category": "protein coding", "start": 14747, "end": 15887},
    {"gene": "MT-TT", "region": "MT-TT", "category": "tRNA", "start": 15888, "end": 15953},
    {"gene": "MT-TP", "region": "MT-TP", "category": "tRNA", "start": 15956, "end": 16023},
    {"gene": "MT-CR", "region": "control region", "category": "control", "start": 16024, "end": 16569},
)


def _metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, "", b""):
        return {}
    if isinstance(value, bytes):
        value = value.decode()
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, list):
        values = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(values) if values else None
    text_value = str(value).strip()
    return text_value or None


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _list_texts(*values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        items = value if isinstance(value, list) else [value]
        for item in items:
            text_value = _text(item)
            if not text_value:
                continue
            for part in text_value.split(";"):
                cleaned = part.strip()
                key = cleaned.lower()
                if cleaned and key not in seen:
                    seen.add(key)
                    result.append(cleaned)
    return result


def _nested(metadata: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = metadata
        for key in path:
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if value not in (None, ""):
            return value
    return None


def _first_annotation(record: SmallVariantRecord) -> dict[str, Any]:
    return next((annotation for annotation in record.annotations if isinstance(annotation, dict)), {})


def _annotation_value(annotation: dict[str, Any], *keys: str) -> Any:
    normalized = {str(key).lower(): value for key, value in annotation.items()}
    for key in keys:
        if key in annotation and annotation[key] not in (None, ""):
            return annotation[key]
        value = normalized.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _variant_type(ref: str, alt: str) -> str:
    if len(ref) == 1 and len(alt) == 1:
        return "SNV"
    if len(ref) == len(alt):
        return "MNV"
    return "INDEL"


def _mitomap_query(record: SmallVariantRecord) -> str:
    return f"m.{record.start}{record.ref}>{record.alt}"


def _mitomap_url(record: SmallVariantRecord) -> str:
    query = f"{record.start}{record.ref}>{record.alt}"
    return f"{MITOMAP_ALLELE_SEARCH_URL}?variant={quote(query, safe='')}"


def _position_locus(position: int) -> dict[str, Any]:
    for locus in MT_LOCI:
        if int(locus["start"]) <= position <= int(locus["end"]):
            return locus
    return {"gene": None, "region": "intergenic", "category": "intergenic"}


def _clinical_significance(annotation: dict[str, Any]) -> str:
    status_text = " ".join(
        _list_texts(
            _annotation_value(annotation, "clinical_significance", "clin_sig", "clinvar", "ClinVar_CLNSIG"),
            _annotation_value(annotation, "mitomap_status", "mitomapClinicalSignificance", "status"),
            _annotation_value(annotation, "classification"),
            _annotation_value(annotation, "variant_status", "variantStatus"),
        )
    ).lower()
    if not status_text:
        return "unknown"
    if "pathogenic" in status_text and "likely" in status_text:
        return "likely_pathogenic"
    if "pathogenic" in status_text:
        return "pathogenic"
    if "benign" in status_text and "likely" in status_text:
        return "likely_benign"
    if "benign" in status_text:
        return "benign"
    if "polymorphism" in status_text or "common" in status_text:
        return "polymorphism"
    if "uncertain" in status_text or "vus" in status_text or "conflict" in status_text:
        return "uncertain"
    if "reported" in status_text or "confirmed" in status_text or "cfrm" in status_text:
        return "reported"
    return "unknown"


def _consequence_terms(annotation: dict[str, Any]) -> list[str]:
    """Normalised consequence/effect terms used for client-side filtering.

    VEP-style effects can be joined with ``&``/``,``/``;`` (e.g.
    ``missense_variant&splice_region_variant``); split them into individual
    lower-cased tokens so the UI can match ``synonymous_variant`` reliably.
    """

    raw = _annotation_value(
        annotation,
        "consequence",
        "effect",
        "variant_effect",
        "major_consequence",
        "majorConsequence",
        "most_severe_consequence",
    )
    terms: list[str] = []
    seen: set[str] = set()
    for value in raw if isinstance(raw, list) else [raw]:
        text_value = _text(value)
        if not text_value:
            continue
        for part in re.split(r"[&,;]", text_value):
            token = part.strip().lower().replace(" ", "_")
            if token and token not in seen:
                seen.add(token)
                terms.append(token)
    return terms


def _annotation_source_keys(annotation: dict[str, Any]) -> list[str]:
    interesting = (
        "mitomap",
        "clinvar",
        "disease",
        "phenotype",
        "haplogroup",
        "gnomad",
        "helix",
        "frequency",
        "consequence",
        "impact",
    )
    return sorted(
        str(key)
        for key in annotation
        if any(token in str(key).lower() for token in interesting)
    )


def _annotation_for_record(record: SmallVariantRecord) -> MitoDNAVariantAnnotationOut:
    annotation = _first_annotation(record)
    locus = _position_locus(record.start)
    gene = _text(
        _annotation_value(
            annotation,
            "gene",
            "gene_symbol",
            "symbol",
            "Gene",
            "SYMBOL",
        )
    ) or _text(locus.get("gene"))
    consequence = _text(
        _annotation_value(
            annotation,
            "consequence",
            "effect",
            "variant_effect",
            "major_consequence",
            "majorConsequence",
            "most_severe_consequence",
            "HGVSp",
            "hgvsp",
            "HGVSc",
            "hgvsc",
        )
    )
    consequence_terms = _consequence_terms(annotation)
    gnomad_af = _float(
        _annotation_value(
            annotation,
            "gnomad_af",
            "gnomadAf",
            "gnomad_genomes_af",
            "gnomadGenomesAf",
        )
    )
    disorders = _list_texts(
        _annotation_value(annotation, "disease", "diseases", "associated_disease"),
        _annotation_value(annotation, "mitomap_disease", "mitomapDisorder", "disorder"),
        _annotation_value(annotation, "phenotype", "phenotypes", "clinical_phenotype"),
    )
    polymorphism_notes = _list_texts(
        _annotation_value(annotation, "polymorphism", "polymorphism_notes"),
        _annotation_value(annotation, "population_frequency", "frequency", "helix_af", "gnomad_af"),
    )
    return MitoDNAVariantAnnotationOut(
        region=str(locus.get("region") or gene or "mitochondrial genome"),
        gene=gene,
        consequence=consequence,
        consequence_terms=consequence_terms,
        gnomad_af=gnomad_af,
        category=_text(locus.get("category")),
        clinical_significance=_clinical_significance(annotation),  # type: ignore[arg-type]
        disorders=disorders,
        polymorphism_notes=polymorphism_notes,
        mitomap_query=_mitomap_query(record),
        mitomap_url=_mitomap_url(record),
        source_keys=_annotation_source_keys(annotation),
    )


def _alt_depth(call: SmallVariantCall) -> int | None:
    if len(call.ad) > 1:
        return sum(value for value in call.ad[1:] if value is not None)
    return None


def _read_depth(call: SmallVariantCall) -> int | None:
    if call.dp is not None:
        return int(call.dp)
    if call.ad:
        return sum(call.ad)
    return None


def _allele_fraction(call: SmallVariantCall) -> float | None:
    af_values = [float(value) for value in call.af if value is not None]
    if af_values:
        return max(af_values)
    depth = _read_depth(call)
    alt_depth = _alt_depth(call)
    if depth and alt_depth is not None:
        return alt_depth / depth
    return None


def _gt_has_alt(gt: str) -> bool:
    normalized = str(gt or "").replace("|", "/")
    return any(part not in {"", ".", "0"} for part in normalized.split("/"))


def _zygosity(call: SmallVariantCall) -> str:
    af = _allele_fraction(call)
    if af is not None:
        if af >= HOMOPLASMY_THRESHOLD:
            return "homoplasmic"
        if af >= HETEROPLASMY_THRESHOLD:
            return "heteroplasmic"
        if af > 0:
            return "low_level"
        return "reference"
    gt = str(call.gt or "")
    if gt in {"", ".", "./.", ".|."}:
        return "no_call"
    if gt.replace("|", "/") in {"1/1", "1"}:
        return "homoplasmic"
    if _gt_has_alt(gt):
        return "heteroplasmic"
    return "reference"


def _call_display(call: SmallVariantCall) -> str:
    zygosity = _zygosity(call)
    af = _allele_fraction(call)
    if af is not None:
        return f"{af * 100:.1f}%"
    if zygosity == "reference":
        return "ref"
    if zygosity == "no_call":
        return "no-call"
    return call.gt or zygosity.replace("_", " ")


def _member_meta(context: FamilyMetadataContext) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("sample_id")): {
            "role": row.get("role"),
            "affected": bool(row.get("affected", False)),
            "sex": row.get("sex"),
        }
        for row in context.sample_rows
        if str(row.get("sample_id") or "").strip()
    }


def _call_out(
    call: SmallVariantCall,
    *,
    member_by_sample: dict[str, dict[str, Any]],
) -> MitoDNAVariantSampleCallOut:
    meta = member_by_sample.get(call.sample, {})
    return MitoDNAVariantSampleCallOut(
        sample=call.sample,
        role=meta.get("role"),
        affected=meta.get("affected"),
        sex=meta.get("sex"),
        genotype=call.gt,
        allele_fraction=_allele_fraction(call),
        depth=_read_depth(call),
        alt_depth=_alt_depth(call),
        zygosity=_zygosity(call),  # type: ignore[arg-type]
        display=_call_display(call),
    )


def _has_alt_call(call: MitoDNAVariantSampleCallOut | None) -> bool:
    return call is not None and call.zygosity in {"homoplasmic", "heteroplasmic", "low_level"}


def _maternal_transmission(
    calls: dict[str, MitoDNAVariantSampleCallOut],
    samples: Sequence[FamilyMemberOut],
) -> str:
    mothers = {sample.sample_id for sample in samples if sample.role == "mother"}
    fathers = {sample.sample_id for sample in samples if sample.role == "father"}
    children = {
        sample.sample_id
        for sample in samples
        if sample.role not in {"mother", "father"}
    }
    mother_alt = any(_has_alt_call(calls.get(sample_id)) for sample_id in mothers)
    father_alt = any(_has_alt_call(calls.get(sample_id)) for sample_id in fathers)
    child_alt = any(_has_alt_call(calls.get(sample_id)) for sample_id in children)
    any_alt = any(_has_alt_call(call) for call in calls.values())
    if not any_alt:
        return "no_alt_calls"
    if child_alt and mother_alt:
        return "maternal_shared"
    if child_alt and not mother_alt:
        return "maternal_not_observed"
    if mother_alt and not child_alt:
        return "maternal_only"
    if father_alt and not mother_alt and not child_alt:
        return "father_only"
    return "family_private"


def _variant_out(
    record: SmallVariantRecord,
    *,
    member_by_sample: dict[str, dict[str, Any]],
    samples: Sequence[FamilyMemberOut],
) -> MitoDNAVariantOut:
    calls = {
        call.sample: _call_out(call, member_by_sample=member_by_sample)
        for call in record.calls
        if call.sample in member_by_sample
    }
    label = f"m.{record.start}{record.ref}>{record.alt}"
    return MitoDNAVariantOut(
        variant_id=record.variant_id,
        position=record.start,
        ref=record.ref,
        alt=record.alt,
        label=label,
        type=_variant_type(record.ref, record.alt),
        rsid=record.rsid,
        annotation=_annotation_for_record(record),
        calls=calls,
        maternal_transmission=_maternal_transmission(calls, samples),  # type: ignore[arg-type]
    )


def _dedupe_records(records: Iterable[SmallVariantRecord]) -> list[SmallVariantRecord]:
    deduped: dict[str, SmallVariantRecord] = {}
    for record in records:
        deduped.setdefault(record.variant_id, record)
    return sorted(deduped.values(), key=lambda record: (record.start, record.ref, record.alt, record.variant_id))


async def _fetch_mt_records(context: FamilyMetadataContext) -> list[SmallVariantRecord]:
    if not context.assembly_name:
        return []
    fetched: list[SmallVariantRecord] = []
    filters = SmallVariantQueryFilters(
        page=1,
        page_size=MAX_MTDNA_VARIANTS,
        chromosome="MT",
    )
    fetched.extend(
        await _fetch_small_variant_rows(
            context,
            filters,
            panel_constraints=PanelFilterConstraints(),
            limit=MAX_MTDNA_VARIANTS,
        )
    )
    return _dedupe_records(fetched)


def _coverage_from_rows(rows: Sequence[dict[str, Any]]) -> MitoDNACoverageOut:
    if not rows:
        return MitoDNACoverageOut()
    weighted_total = 0.0
    covered_bases = 0
    values: list[float] = []
    sources: list[str] = []
    for row in rows:
        value = _float(row.get("value"))
        if value is None:
            continue
        start = max(_int(row.get("start")) or 0, 1)
        end = min(_int(row.get("end")) or start, MT_GENOME_LENGTH)
        width = max(end - start + 1, 1)
        covered_bases += width
        weighted_total += value * width
        values.append(value)
        source = _text(row.get("origin")) or _text(row.get("record_id"))
        if source and source not in sources:
            sources.append(source)
    if not values:
        return MitoDNACoverageOut(regions=len(rows))
    return MitoDNACoverageOut(
        mean_depth=weighted_total / max(covered_bases, 1),
        min_depth=min(values),
        max_depth=max(values),
        breadth=min(covered_bases / MT_GENOME_LENGTH, 1.0),
        source=", ".join(sources[:3]) if sources else "coverage",
        regions=len(rows),
    )


async def _has_mt_coverage(context: FamilyMetadataContext) -> bool:
    """Cheap presence check: is any MT/chrM coverage interval recorded?

    Used by the count-only path so the family dashboard can decide whether to show
    the mtDNA workspace without streaming the full coverage track (up to tens of
    thousands of interval rows). `LIMIT 1` lets ClickHouse stop at the first hit.
    """
    if not context.assembly_name or not context.sample_uuid_to_name:
        return False
    rows = await fetch_interval_track_rows(
        context.assembly_name,
        family_uuid=context.family_uuid,
        sample_uuids=list(context.sample_uuid_to_name),
        track_type="coverage",
        chromosomes=["MT", "M", "chrMT", "chrM"],
        limit=1,
    )
    return bool(rows)


async def _coverage_by_sample(context: FamilyMetadataContext) -> dict[str, MitoDNACoverageOut]:
    if not context.assembly_name or not context.sample_uuid_to_name:
        return {}
    rows = await fetch_interval_track_rows(
        context.assembly_name,
        family_uuid=context.family_uuid,
        sample_uuids=list(context.sample_uuid_to_name),
        track_type="coverage",
        chromosomes=["MT", "M", "chrMT", "chrM"],
        limit=50_000,
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sample_name = context.sample_uuid_to_name.get(str(row.get("sample_uuid")))
        if not sample_name:
            continue
        grouped.setdefault(sample_name, []).append(row)
    return {sample_name: _coverage_from_rows(sample_rows) for sample_name, sample_rows in grouped.items()}


def _sample_variant_depth_coverage(records: Sequence[SmallVariantRecord]) -> dict[str, MitoDNACoverageOut]:
    depths: dict[str, list[int]] = {}
    for record in records:
        for call in record.calls:
            depth = _read_depth(call)
            if depth is not None:
                depths.setdefault(call.sample, []).append(depth)
    return {
        sample: MitoDNACoverageOut(
            mean_depth=sum(values) / len(values),
            min_depth=min(values),
            max_depth=max(values),
            source="variant calls",
            regions=len(values),
        )
        for sample, values in depths.items()
        if values
    }


def _sample_haplogroup(metadata: dict[str, Any]) -> str | None:
    return _text(
        _nested(
            metadata,
            ("mtdna", "haplogroup"),
            ("mtDNA", "haplogroup"),
            ("mtdna_server", "haplogroup"),
            ("haplogrep", "haplogroup"),
            ("haplocheck", "haplogroup"),
            ("package_sample_metadata", "mtdna", "haplogroup"),
            ("package_sample_metadata", "haplogroup"),
            ("haplogroup",),
        )
    )


def _sample_contamination(metadata: dict[str, Any]) -> float | None:
    return _float(
        _nested(
            metadata,
            ("mtdna", "contamination"),
            ("mtDNA", "contamination"),
            ("mtdna_server", "contamination"),
            ("haplocheck", "contamination"),
            ("haplocheck", "contamination_level"),
            ("package_sample_metadata", "mtdna", "contamination"),
        )
    )


def _explicit_qc_status(metadata: dict[str, Any]) -> str | None:
    status = _text(
        _nested(
            metadata,
            ("mtdna", "qc_status"),
            ("mtdna", "status"),
            ("mtDNA", "qc_status"),
            ("mtdna_server", "qc_status"),
            ("haplocheck", "status"),
            ("package_sample_metadata", "mtdna", "qc_status"),
        )
    )
    if not status:
        return None
    normalized = status.strip().lower()
    if normalized in {"pass", "passed", "ok"}:
        return "pass"
    if normalized in {"fail", "failed", "error"}:
        return "fail"
    if normalized in {"warn", "warning", "review"}:
        return "warning"
    return None


def _qc_notes(metadata: dict[str, Any]) -> list[str]:
    return _list_texts(
        _nested(metadata, ("mtdna", "qc_notes"), ("mtdna", "notes"), ("haplocheck", "notes")),
        _nested(metadata, ("mtdna_server", "warnings"), ("package_sample_metadata", "mtdna", "qc_notes")),
    )


def _sample_qc(metadata: dict[str, Any], coverage: MitoDNACoverageOut) -> MitoDNAQcOut:
    notes = _qc_notes(metadata)
    contamination = _sample_contamination(metadata)
    explicit_status = _explicit_qc_status(metadata)
    mean_depth = coverage.mean_depth if coverage.mean_depth is not None else _float(
        _nested(
            metadata,
            ("mtdna", "mean_depth"),
            ("mtDNA", "mean_depth"),
            ("mtdna_server", "mean_depth"),
            ("package_sample_metadata", "mtdna", "mean_depth"),
        )
    )
    min_mean_depth = coverage.min_depth if coverage.min_depth is not None else _float(
        _nested(
            metadata,
            ("mtdna", "min_mean_depth"),
            ("mtDNA", "min_mean_depth"),
            ("mtdna_server", "min_mean_depth"),
            ("package_sample_metadata", "mtdna", "min_mean_depth"),
        )
    )
    status = explicit_status or "unknown"
    if contamination is not None and contamination >= 0.03:
        status = "fail"
        notes.append("Contamination estimate is at or above 3%.")
    elif contamination is not None and contamination >= 0.01 and status not in {"fail"}:
        status = "warning"
        notes.append("Contamination estimate is elevated.")
    if mean_depth is not None and mean_depth < 50:
        status = "warning" if status != "fail" else status
        notes.append("Mean mtDNA depth is below 50x.")
    if status == "unknown" and (mean_depth is not None or contamination is not None):
        status = "pass"
    return MitoDNAQcOut(
        status=status,  # type: ignore[arg-type]
        notes=list(dict.fromkeys(notes)),
        contamination=contamination,
        mean_depth=mean_depth,
        min_mean_depth=min_mean_depth,
    )


def _sample_outs(
    context: FamilyMetadataContext,
    *,
    records: Sequence[SmallVariantRecord],
    coverage_map: dict[str, MitoDNACoverageOut],
) -> list[MitoDNASampleOut]:
    variant_depth_map = _sample_variant_depth_coverage(records)
    samples: list[MitoDNASampleOut] = []
    for row in context.sample_rows:
        sample_id = str(row.get("sample_id") or "").strip()
        if not sample_id:
            continue
        metadata = _metadata_dict(row.get("sample_metadata"))
        coverage = coverage_map.get(sample_id) or variant_depth_map.get(sample_id) or MitoDNACoverageOut()
        samples.append(
            MitoDNASampleOut(
                sample_id=sample_id,
                role=row.get("role"),
                affected=bool(row.get("affected", False)),
                sex=row.get("sex"),
                haplogroup=_sample_haplogroup(metadata),
                coverage=coverage,
                qc=_sample_qc(metadata, coverage),
            )
        )
    return samples


def _family_qc_notes(samples: Sequence[MitoDNASampleOut], variants: Sequence[MitoDNAVariantOut]) -> list[str]:
    notes: list[str] = []
    if not variants:
        notes.append("No MT/chrM small variants are loaded for this family.")
    if not any(sample.haplogroup for sample in samples):
        notes.append("No per-sample mtDNA haplogroup metadata is available.")
    if not any(sample.coverage.mean_depth is not None for sample in samples):
        notes.append("No MT coverage track was found; variant-call depth is used when available.")
    if any(sample.qc.status in {"warning", "fail"} for sample in samples):
        notes.append("One or more samples have mtDNA QC warnings.")
    return notes


async def _attach_reviews(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variants: Sequence[MitoDNAVariantOut],
) -> None:
    """Attach the shared small-variant review (ACMG/tags/notes) to MT variants.

    MT variants live in the small-variant store, so their reviews are keyed by
    the same ``variant_id`` and read through the same Postgres review map.
    """

    if not variants:
        return
    review_map = await get_small_variant_review_map(
        session,
        family_uuid=context.family_uuid,
        variant_ids=[variant.variant_id for variant in variants],
    )
    if not review_map:
        return
    for variant in variants:
        variant.review = review_map.get(variant.variant_id)


async def get_family_mitochondrial_analysis_response(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    count_only: bool = False,
) -> FamilyMitoDNAAnalysisOut:
    records = await _fetch_mt_records(context)
    if count_only:
        # Presence check only: don't stream the full coverage track or build the
        # per-sample / per-variant payloads. Coverage is "present" when a sample
        # carries variant-call depth or any MT coverage interval exists — the same
        # two sources `_sample_outs` merges, but probed with a cheap `LIMIT 1`.
        has_coverage = bool(_sample_variant_depth_coverage(records)) or await _has_mt_coverage(context)
        return FamilyMitoDNAAnalysisOut(
            variant_count=len(records),
            has_coverage=has_coverage,
            heteroplasmy_threshold=HETEROPLASMY_THRESHOLD,
            homoplasmy_threshold=HOMOPLASMY_THRESHOLD,
        )
    member_by_sample = _member_meta(context)
    coverage_map = await _coverage_by_sample(context)
    samples = _sample_outs(context, records=records, coverage_map=coverage_map)
    # Coverage can derive from a coverage track or from variant-call depth, so it
    # is read off the resolved sample outs (same logic the full response uses).
    has_coverage = any(
        sample.coverage.source is not None or sample.coverage.mean_depth is not None
        for sample in samples
    )
    variant_rows = [
        _variant_out(record, member_by_sample=member_by_sample, samples=samples)
        for record in records
    ]
    await _attach_reviews(session, context=context, variants=variant_rows)
    return FamilyMitoDNAAnalysisOut(
        samples=samples,
        variants=variant_rows,
        variant_count=len(variant_rows),
        has_coverage=has_coverage,
        qc_notes=_family_qc_notes(samples, variant_rows),
        heteroplasmy_threshold=HETEROPLASMY_THRESHOLD,
        homoplasmy_threshold=HOMOPLASMY_THRESHOLD,
    )
