from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence
from uuid import UUID

from fastapi import HTTPException
from clickhouse_driver.errors import ServerException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.clickhouse import execute_clickhouse
from ..core.config import settings
from ..schemas import GenotypeOut, SmallVariantGroupOut, SmallVariantReviewOut, VariantOut, VariantPage
from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext
from .family_variant_filters import (
    SmallVariantQueryFilters,
    StructuralVariantQueryFilters,
    parse_small_variant_sample_filter,
    parse_structural_sample_filter,
)
from .small_variant_review_pg import (
    get_small_variant_review_map,
    list_matching_small_variant_review_ids,
)
from .structural_variant_review_pg import (
    get_structural_variant_review_map,
    list_matching_structural_variant_review_ids,
)

_VALID_CLICKHOUSE_SEGMENT = re.compile(r"^[A-Za-z0-9._/-]+$")
_INTERVAL_PATTERN = re.compile(
    r"^\s*(?P<chr>[^:\s]+)\s*:\s*(?P<start>\d[\d,]*)\s*-\s*(?P<end>\d[\d,]*)\s*$"
)
_GENE_QUERY_SPLIT = re.compile(r"[\s,;]+")
_HET_GT_VALUES = {"0/1", "1/0", "0|1", "1|0"}
_HOM_ALT_GT_VALUES = {"1/1", "1|1"}
_X_CHROMOSOME_TOKENS = {"X", "23"}
_COMPOUND_HET_INHERITANCE = "compound_het"
_RECESSIVE_INHERITANCE = "recessive"
_RECESSIVE_HOMOZYGOUS_INHERITANCE = "recessive_homozygous"
_DE_NOVO_DOMINANT_INHERITANCE = "de_novo_dominant"
_X_LINKED_INHERITANCE = "x_linked"
_SMALL_INHERITANCE_ALIASES = {
    "compound_heterozygous": _COMPOUND_HET_INHERITANCE,
    "recessive_hom": _RECESSIVE_HOMOZYGOUS_INHERITANCE,
    "homozygous_recessive": _RECESSIVE_HOMOZYGOUS_INHERITANCE,
    "de_novo": _DE_NOVO_DOMINANT_INHERITANCE,
    "dominant": _DE_NOVO_DOMINANT_INHERITANCE,
    "xlinked": _X_LINKED_INHERITANCE,
    "x_linked_recessive": _X_LINKED_INHERITANCE,
}
_SUPPORTED_SMALL_INHERITANCE = {
    _COMPOUND_HET_INHERITANCE,
    _RECESSIVE_INHERITANCE,
    _RECESSIVE_HOMOZYGOUS_INHERITANCE,
    _DE_NOVO_DOMINANT_INHERITANCE,
    _X_LINKED_INHERITANCE,
}
_KNOWN_ANNOTATION_KEYS = {
    "gene",
    "gene_id",
    "geneId",
    "geneID",
    "gene_symbol",
    "geneSymbol",
    "hgnc_symbol",
    "hgncSymbol",
    "impact",
    "effect",
    "majorConsequence",
    "consequence",
    "most_severe_consequence",
    "clinvar",
    "clinvarClinicalSignificance",
    "transcript_id",
    "transcriptId",
    "feature_type",
    "featureType",
    "transcript_biotype",
    "transcriptBiotype",
    "hgvsc",
    "hgvsp",
    "canonical",
    "mane_select",
    "maneSelect",
    "mane_plus_clinical",
    "manePlusClinical",
    "exon",
    "intron",
    "lof",
    "lof_filter",
    "lofFilter",
    "lof_flags",
    "lofFlags",
    "gnomad_af",
    "gnomadAf",
    "gnomad_hom_count",
    "gnomadHomCount",
    "cadd_raw",
    "caddRaw",
    "cadd_phred",
    "caddPhred",
    "revel",
    "sift",
    "siftPrediction",
    "polyphen",
    "polyphenPrediction",
    "spliceai_ds_ag",
    "spliceaiDsAg",
    "spliceai_ds_al",
    "spliceaiDsAl",
    "spliceai_ds_dg",
    "spliceaiDsDg",
    "spliceai_ds_dl",
    "spliceaiDsDl",
    "spliceai_max",
    "spliceaiMax",
    "population_frequencies",
    "populationFrequencies",
    "extra",
    "rsid",
}


@dataclass(slots=True)
class Region:
    chr: str
    start: int
    end: int


@dataclass(slots=True)
class SmallVariantCall:
    sample: str
    gt: str
    gq: float | None
    dp: int | None
    af: list[float]
    ad: list[int]
    ps: int | None


@dataclass(slots=True)
class SmallVariantRecord:
    variant_key: int | None
    variant_id: str
    chr: str
    start: int
    end: int
    ref: str
    alt: str
    source: str | None
    rsid: str | None
    filters: list[str]
    gene_symbols: list[str]
    annotations: list[dict[str, Any]]
    calls: list[SmallVariantCall]


@dataclass(slots=True)
class SmallVariantCompoundHetPair:
    pair_key: str
    gene: str | None
    gene_id: str | None
    left: SmallVariantRecord
    right: SmallVariantRecord


@dataclass(slots=True)
class StructuralVariantCall:
    sample: str
    gt: str
    qual: float | None
    read_support: int | None
    filter: str | None


@dataclass(slots=True)
class StructuralVariantRecord:
    variant_key: int | None
    variant_id: str
    chr: str
    start: int
    end: int
    sv_type: str
    source: str | None
    remote_chr: str | None
    remote_start: int | None
    remote_end: int | None
    sv_len: int | None
    filters: list[str]
    gene_symbols: list[str]
    annotations: list[dict[str, Any]]
    calls: list[StructuralVariantCall]


def _require_clickhouse_identifier(value: str) -> str:
    if not _VALID_CLICKHOUSE_SEGMENT.fullmatch(value):
        raise HTTPException(status_code=400, detail="Assembly name is invalid")
    return value


def _small_table_name(assembly_name: str, suffix: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SNV_INDEL/{suffix}`"


def _structural_table_name(assembly_name: str, suffix: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SV/{suffix}`"


def _chromosome_options(chromosome: str) -> tuple[str, ...]:
    normalized = normalize_chromosome(chromosome)
    prefixed = f"chr{normalized}"
    return (normalized, prefixed) if prefixed != chromosome else (chromosome, normalized)


def _casefold(value: Any) -> str:
    return str(value or "").strip().casefold()


def _contains_casefold(value: Any, needle: str | None) -> bool:
    if not needle:
        return True
    return _casefold(needle) in _casefold(value)


def _flexible_status_match(value: Any, candidates: Sequence[str]) -> bool:
    normalized_value = " ".join(_casefold(value).replace("_", " ").split())
    return any(
        normalized_value == " ".join(_casefold(candidate).replace("_", " ").split())
        for candidate in candidates
        if str(candidate).strip()
    )


def _coerce_int(value: Any) -> int | None:
    if value in (None, "", "."):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _coerce_float(value: Any) -> float | None:
    if value in (None, "", "."):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _casefold(value) in {"1", "true", "yes", "y"}


def _string_list(values: Any) -> list[str]:
    if values in (None, ""):
        return []
    if isinstance(values, (list, tuple)):
        source = values
    else:
        source = [values]
    result: list[str] = []
    seen: set[str] = set()
    for value in source:
        text_value = str(value or "").strip()
        if not text_value or text_value in seen:
            continue
        seen.add(text_value)
        result.append(text_value)
    return result


def _listify(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _indexed(values: Any, index: int) -> Any:
    sequence = _listify(values)
    if index < len(sequence):
        return sequence[index]
    return None


def _int_list(value: Any) -> list[int]:
    result: list[int] = []
    for item in _listify(value):
        parsed = _coerce_int(item)
        if parsed is not None:
            result.append(parsed)
    return result


def _float_list(value: Any) -> list[float]:
    result: list[float] = []
    for item in _listify(value):
        parsed = _coerce_float(item)
        if parsed is not None:
            result.append(parsed)
    return result


def _decode_json_payload(raw_value: Any) -> Any:
    if raw_value in (None, "", b""):
        return None
    if isinstance(raw_value, (dict, list)):
        return raw_value
    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode()
    try:
        return json.loads(str(raw_value))
    except json.JSONDecodeError:
        return None


def _collect_annotations(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (
        "annotations",
        "sortedTranscriptConsequences",
        "transcriptConsequences",
        "transcripts",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    for value in payload.values():
        annotations = _collect_annotations(value)
        if annotations:
            return annotations
    return []


def _annotation_value(annotation: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in annotation:
            return annotation[key]
    return None


def _annotation_text(annotation: dict[str, Any], *keys: str) -> str | None:
    value = _annotation_value(annotation, *keys)
    text_value = str(value or "").strip()
    return text_value or None


def _annotation_float(annotation: dict[str, Any], *keys: str) -> float | None:
    return _coerce_float(_annotation_value(annotation, *keys))


def _annotation_int(annotation: dict[str, Any], *keys: str) -> int | None:
    return _coerce_int(_annotation_value(annotation, *keys))


def _annotation_bool(annotation: dict[str, Any], *keys: str) -> bool:
    return _coerce_bool(_annotation_value(annotation, *keys))


def _annotation_rank(annotation: dict[str, Any]) -> tuple[int, int, int]:
    impact_order = {"HIGH": 4, "MODERATE": 3, "MEDIUM": 3, "LOW": 2, "MODIFIER": 1}
    return (
        1 if _annotation_bool(annotation, "mane_select", "maneSelect") else 0,
        1 if _annotation_bool(annotation, "canonical") else 0,
        impact_order.get(
            _casefold(_annotation_text(annotation, "impact") or "").upper(),
            0,
        ),
    )


def _select_primary_annotation(annotations: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return max(annotations, key=_annotation_rank, default={})


def _normalize_gt(value: Any) -> str:
    raw = str(value or "").strip()
    normalized = raw.upper()
    if normalized in {"REF", "REFERENCE", "WT", "WILDTYPE"}:
        return "0/0"
    if normalized in {"HET", "HETEROZYGOUS"}:
        return "0/1"
    if normalized in {"HOM", "HOM_ALT", "HOMOZYGOUS", "ALT"}:
        return "1/1"
    if normalized in {"MISSING", "NO_CALL"}:
        return "./."
    return raw or "./."


def _small_type(ref: str, alt: str) -> str:
    if len(ref) == 1 and len(alt) == 1:
        return "SNV"
    return "INDEL"


def _split_gene_terms(raw_value: str | None) -> list[str]:
    return [term for term in _GENE_QUERY_SPLIT.split(str(raw_value or "").strip()) if term]


def _parse_interval_regions(raw_value: str | None) -> list[Region]:
    regions: list[Region] = []
    for entry in re.split(r"[\n;]+", str(raw_value or "")):
        match = _INTERVAL_PATTERN.match(entry.strip())
        if not match:
            continue
        regions.append(
            Region(
                chr=normalize_chromosome(match.group("chr")),
                start=int(match.group("start").replace(",", "")),
                end=int(match.group("end").replace(",", "")),
            )
        )
    return regions


def _variant_overlaps_regions(chr_value: str, start: int, end: int, regions: Sequence[Region]) -> bool:
    normalized_chr = normalize_chromosome(chr_value)
    return any(
        normalized_chr == normalize_chromosome(region.chr)
        and start <= region.end
        and end >= region.start
        for region in regions
    )


def _variant_hits_gene_symbols(gene_symbols: Sequence[str], query: str | None) -> bool:
    terms = {_casefold(term) for term in _split_gene_terms(query)}
    if not terms:
        return True
    return bool({_casefold(symbol) for symbol in gene_symbols}.intersection(terms))


def _annotation_population_frequencies(annotation: dict[str, Any]) -> dict[str, float]:
    payload = _annotation_value(annotation, "population_frequencies", "populationFrequencies")
    result: dict[str, float] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            parsed = _coerce_float(value)
            if parsed is not None:
                result[str(key)] = parsed
    for key in (
        ("gnomad_exomes_af", "gnomadExomesAf"),
        ("gnomad_genomes_af", "gnomadGenomesAf"),
        ("gnomad_popmax_af", "gnomadPopmaxAf"),
        ("topmed_af", "topmedAf"),
    ):
        parsed = _annotation_float(annotation, *key)
        if parsed is not None:
            result[key[0]] = parsed
    return result


def _annotation_extra(annotation: dict[str, Any]) -> dict[str, Any]:
    payload = _annotation_value(annotation, "extra")
    result: dict[str, Any] = payload if isinstance(payload, dict) else {}
    for key, value in annotation.items():
        if key in _KNOWN_ANNOTATION_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result.setdefault(key, value)
    return result


def _annotation_gene(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(
        annotation,
        "gene",
        "gene_symbol",
        "geneSymbol",
        "hgnc_symbol",
        "hgncSymbol",
    )


def _annotation_gene_id(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(annotation, "gene_id", "geneId", "geneID")


def _annotation_effect(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(
        annotation,
        "effect",
        "majorConsequence",
        "consequence",
        "most_severe_consequence",
    )


def _annotation_clinvar(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(annotation, "clinvar", "clinvarClinicalSignificance")


def _annotation_sift(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(annotation, "sift", "siftPrediction")


def _annotation_polyphen(annotation: dict[str, Any]) -> str | None:
    return _annotation_text(annotation, "polyphen", "polyphenPrediction")


def _annotation_spliceai_max(annotation: dict[str, Any]) -> float | None:
    explicit = _annotation_float(annotation, "spliceai_max", "spliceaiMax")
    if explicit is not None:
        return explicit
    values = [
        _annotation_float(annotation, "spliceai_ds_ag", "spliceaiDsAg"),
        _annotation_float(annotation, "spliceai_ds_al", "spliceaiDsAl"),
        _annotation_float(annotation, "spliceai_ds_dg", "spliceaiDsDg"),
        _annotation_float(annotation, "spliceai_ds_dl", "spliceaiDsDl"),
    ]
    present = [value for value in values if value is not None]
    return max(present) if present else None


_STRUCTURAL_REGION_FLAG_KEYS = (
    "UTR",
    "CDS",
    "ORegAnno",
    "TRE",
    "Centromeric",
    "Pericentromeric",
    "Telomeric",
    "Segdup",
    "Repeat",
    "Gap",
    "Homopolymer",
    "HiConf",
)


def _split_info_terms(value: Any) -> list[str]:
    if value in (None, "", "."):
        return []
    if isinstance(value, (list, tuple)):
        terms: list[str] = []
        for item in value:
            terms.extend(_split_info_terms(item))
        return terms
    return [
        item.strip()
        for item in re.split(r"[,|]+", str(value))
        if item.strip() and item.strip() != "."
    ]


def _first_float_from_info(value: Any) -> float | None:
    parsed = _coerce_float(value)
    if parsed is not None:
        return parsed
    for term in _split_info_terms(value):
        parsed = _coerce_float(term)
        if parsed is not None:
            return parsed
    return None


def _structural_info_payloads(record: StructuralVariantRecord) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for annotation in record.annotations:
        info = annotation.get("info")
        if isinstance(info, dict):
            payloads.append(info)
        else:
            payloads.append(annotation)
    return payloads


def _structural_info_value(record: StructuralVariantRecord, *keys: str) -> Any:
    for info in _structural_info_payloads(record):
        for key in keys:
            if key in info and info[key] not in (None, "", "."):
                return info[key]
    return None


def _structural_info_terms(record: StructuralVariantRecord, *keys: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for info in _structural_info_payloads(record):
        for key in keys:
            for term in _split_info_terms(info.get(key)):
                folded = _casefold(term)
                if folded in seen:
                    continue
                seen.add(folded)
                terms.append(term)
    return terms


def _structural_info_text(record: StructuralVariantRecord, *keys: str) -> str | None:
    terms = _structural_info_terms(record, *keys)
    if terms:
        return ", ".join(terms)
    value = _structural_info_value(record, *keys)
    text_value = str(value or "").strip()
    return text_value or None


def _structural_info_float(record: StructuralVariantRecord, *keys: str) -> float | None:
    for info in _structural_info_payloads(record):
        for key in keys:
            parsed = _first_float_from_info(info.get(key))
            if parsed is not None:
                return parsed
    return None


def _structural_pli(record: StructuralVariantRecord) -> float | None:
    values = [
        parsed
        for parsed in (
            _first_float_from_info(term)
            for term in _structural_info_terms(record, "pLI", "pli", "gene_pli")
        )
        if parsed is not None
    ]
    return max(values) if values else None


def _structural_region_flags(record: StructuralVariantRecord) -> list[str]:
    flags: list[str] = []
    for key in _STRUCTURAL_REGION_FLAG_KEYS:
        value = _structural_info_value(record, key)
        if value in (None, "", ".", "0"):
            continue
        if isinstance(value, str) and _casefold(value) in {"false", "no", "n"}:
            continue
        flags.append(key)
    return flags


def _structural_population_frequencies(record: StructuralVariantRecord) -> dict[str, float]:
    result: dict[str, float] = {}
    for key in (
        "Allele_Freq_ALL_Control",
        "Allele_Freq_ALL",
        "Pop_Freq_ALL",
        "q",
        "Allele_Freq_AFR",
        "Allele_Freq_AMR",
        "Allele_Freq_EAS",
        "Allele_Freq_EUR",
        "Allele_Freq_SAS",
        "Pop_Freq_AFR",
        "Pop_Freq_AMR",
        "Pop_Freq_EAS",
        "Pop_Freq_EUR",
        "Pop_Freq_SAS",
    ):
        parsed = _structural_info_float(record, key)
        if parsed is not None:
            result[key] = parsed
    return result


def _band_position_contains(band: dict[str, Any], position: int) -> bool:
    start = _coerce_int(band.get("start"))
    end = _coerce_int(band.get("end"))
    if start is None or end is None:
        return False
    zero_based_position = max(0, position - 1)
    return start <= zero_based_position <= end


def _band_name_for_position(bands: Sequence[dict[str, Any]], position: int) -> str | None:
    for band in bands:
        name = str(band.get("name") or "").strip()
        if name and _band_position_contains(band, position):
            return name
    return None


def _format_cytoband_label(chromosome: str, start_band: str | None, end_band: str | None) -> str | None:
    if not start_band and not end_band:
        return None
    chrom = normalize_chromosome(chromosome)
    if start_band and end_band and start_band != end_band:
        return f"{chrom}{start_band}-{chrom}{end_band}"
    return f"{chrom}{start_band or end_band}"


async def _fetch_structural_cytoband_map(
    session: AsyncSession,
    *,
    assembly_id: str | None,
    records: Sequence[StructuralVariantRecord],
) -> dict[str, str]:
    if not assembly_id or not records:
        return {}
    chromosomes = list(
        dict.fromkeys(
            alias
            for record in records
            for alias in (normalize_chromosome(record.chr), f"chr{normalize_chromosome(record.chr)}")
        )
    )
    result = await session.execute(
        text(
            """
            SELECT chr, bands
            FROM chromosomes
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND chr IN :chromosomes
            """
        ).bindparams(bindparam("chromosomes", expanding=True)),
        {
            "assembly_id": assembly_id,
            "chromosomes": chromosomes or [""],
        },
    )
    band_map: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        bands = row.get("bands") or []
        if isinstance(bands, str):
            try:
                bands = json.loads(bands)
            except json.JSONDecodeError:
                bands = []
        if not isinstance(bands, list):
            continue
        normalized = normalize_chromosome(str(row["chr"]))
        band_map[normalized] = [band for band in bands if isinstance(band, dict)]

    cytobands: dict[str, str] = {}
    for record in records:
        bands = band_map.get(normalize_chromosome(record.chr))
        if not bands:
            continue
        start_band = _band_name_for_position(bands, record.start)
        end_band = _band_name_for_position(bands, record.end)
        label = _format_cytoband_label(record.chr, start_band, end_band)
        if label:
            cytobands[record.variant_id] = label
    return cytobands


def _structural_annotation_extra(record: StructuralVariantRecord) -> dict[str, Any]:
    population_frequencies = _structural_population_frequencies(record)
    region_flags = _structural_region_flags(record)
    read_depths = {
        key: value
        for key, value in {
            "query_ref_reads": _coerce_int(_structural_info_value(record, "Ref_Reads")),
            "query_total_reads": _coerce_int(_structural_info_value(record, "Total_Reads")),
            "maternal_ref_reads": _coerce_int(_structural_info_value(record, "Maternal_Ref_Reads")),
            "maternal_total_reads": _coerce_int(_structural_info_value(record, "Maternal_Total_Reads")),
            "paternal_ref_reads": _coerce_int(_structural_info_value(record, "Paternal_Ref_Reads")),
            "paternal_total_reads": _coerce_int(_structural_info_value(record, "Paternal_Total_Reads")),
        }.items()
        if value is not None
    }
    genotype_counts = {
        key: value
        for key, value in {
            "hom_ref": _coerce_int(_structural_info_value(record, "GT_homWT")),
            "het": _coerce_int(_structural_info_value(record, "GT_het")),
            "hom_alt": _coerce_int(_structural_info_value(record, "GT_homVAR")),
        }.items()
        if value is not None
    }
    return {
        key: value
        for key, value in {
            "inheritance": _structural_info_text(record, "Inheritance"),
            "query_id": _structural_info_text(record, "Query_ID"),
            "control_support": _structural_info_text(record, "Control_support"),
            "omim_phenotype": _structural_info_text(record, "OMIM_phenotype"),
            "omim_moi": _structural_info_text(record, "OMIM_MOI"),
            "gencc_phenotype": _structural_info_text(record, "GENCC_phenotype"),
            "gencc_support": _structural_info_text(record, "GENCC_support"),
            "gencc_moi": _structural_info_text(record, "GENCC_MOI"),
            "hpo_terms": _structural_info_text(record, "HPO_terms"),
            "pli": _structural_pli(record),
            "region_flags": region_flags,
            "control_af": population_frequencies.get("Allele_Freq_ALL_Control"),
            "population_af": population_frequencies.get("Allele_Freq_ALL")
            or population_frequencies.get("Pop_Freq_ALL")
            or population_frequencies.get("q"),
            "population_frequencies": population_frequencies,
            "genotype_counts": genotype_counts,
            "read_depths": read_depths,
            "hwe": _structural_info_text(record, "HWE"),
        }.items()
        if value not in (None, "", [], {})
    }


def _nullable_lte(value: float | int | None, maximum: float | int | None) -> bool:
    if maximum is None:
        return True
    if value is None:
        return True
    return value <= maximum


def _annotation_matches_normal(annotation: dict[str, Any], filters: SmallVariantQueryFilters) -> bool:
    if filters.transcript and not _contains_casefold(
        _annotation_text(annotation, "transcript_id", "transcriptId"),
        filters.transcript,
    ):
        return False
    if filters.hgvsc and not _contains_casefold(_annotation_text(annotation, "hgvsc"), filters.hgvsc):
        return False
    if filters.hgvsp and not _contains_casefold(_annotation_text(annotation, "hgvsp"), filters.hgvsp):
        return False
    if filters.canonical_only and not _annotation_bool(annotation, "canonical"):
        return False
    if filters.mane_only and not (
        _annotation_bool(annotation, "mane_select", "maneSelect")
        or _annotation_bool(annotation, "mane_plus_clinical", "manePlusClinical")
    ):
        return False
    if filters.lof_only and _casefold(_annotation_text(annotation, "lof") or "") in {"", ".", "na", "n/a"}:
        return False
    if not _nullable_lte(_annotation_float(annotation, "gnomad_af", "gnomadAf"), filters.max_gnomad_af):
        return False
    population_frequencies = _annotation_population_frequencies(annotation)
    if not _nullable_lte(population_frequencies.get("gnomad_exomes_af"), filters.max_gnomad_exomes_af):
        return False
    if not _nullable_lte(population_frequencies.get("gnomad_genomes_af"), filters.max_gnomad_genomes_af):
        return False
    if not _nullable_lte(population_frequencies.get("gnomad_popmax_af"), filters.max_gnomad_popmax_af):
        return False
    if not _nullable_lte(population_frequencies.get("topmed_af"), filters.max_topmed_af):
        return False
    if not _nullable_lte(_annotation_int(annotation, "gnomad_ac"), filters.max_gnomad_ac):
        return False
    if not _nullable_lte(_annotation_int(annotation, "gnomad_hom_count", "gnomadHomCount"), filters.max_gnomad_hom_count):
        return False
    if not _nullable_lte(_annotation_int(annotation, "gnomad_hemi_count"), filters.max_gnomad_hemi_count):
        return False

    impact_terms = {_casefold(value) for value in filters.impact if str(value).strip()}
    effect_terms = {_casefold(value) for value in filters.effect if str(value).strip()}
    any_impact_effect = bool(impact_terms or effect_terms or filters.min_spliceai is not None)
    if any_impact_effect:
        impact_match = bool(impact_terms) and _casefold(_annotation_text(annotation, "impact")) in impact_terms
        effect_match = bool(effect_terms) and _casefold(_annotation_effect(annotation)) in effect_terms
        splice_match = (
            filters.min_spliceai is not None
            and (_annotation_spliceai_max(annotation) or -1.0) >= filters.min_spliceai
        )
        if not (impact_match or effect_match or splice_match):
            return False

    clinvar_terms = [value for value in filters.clinvar if str(value).strip()]
    if clinvar_terms and not _flexible_status_match(_annotation_clinvar(annotation), clinvar_terms):
        return False

    in_silico_requested = any(
        value is not None and value != ""
        for value in (
            filters.min_cadd,
            filters.min_revel,
            filters.min_spliceai,
            filters.sift,
            filters.polyphen,
        )
    )
    if in_silico_requested:
        cadd_match = filters.min_cadd is not None and (
            (_annotation_float(annotation, "cadd_phred", "caddPhred") or -1.0) >= filters.min_cadd
        )
        revel_match = filters.min_revel is not None and (
            (_annotation_float(annotation, "revel") or -1.0) >= filters.min_revel
        )
        splice_match = filters.min_spliceai is not None and (
            (_annotation_spliceai_max(annotation) or -1.0) >= filters.min_spliceai
        )
        sift_match = bool(filters.sift) and _flexible_status_match(_annotation_sift(annotation), [filters.sift])
        polyphen_match = bool(filters.polyphen) and _flexible_status_match(
            _annotation_polyphen(annotation),
            [filters.polyphen],
        )
        if not (cadd_match or revel_match or splice_match or sift_match or polyphen_match):
            return False
    return True


def _annotation_matches_override(annotation: dict[str, Any], filters: SmallVariantQueryFilters) -> bool:
    clinvar_terms = [value for value in filters.clinvar if str(value).strip()]
    if not clinvar_terms:
        return False
    if filters.transcript and not _contains_casefold(
        _annotation_text(annotation, "transcript_id", "transcriptId"),
        filters.transcript,
    ):
        return False
    if filters.hgvsc and not _contains_casefold(_annotation_text(annotation, "hgvsc"), filters.hgvsc):
        return False
    if filters.hgvsp and not _contains_casefold(_annotation_text(annotation, "hgvsp"), filters.hgvsp):
        return False
    if not _flexible_status_match(_annotation_clinvar(annotation), clinvar_terms):
        return False
    return _nullable_lte(_annotation_float(annotation, "gnomad_af", "gnomadAf"), 0.05)


def _matches_small_annotations(record: SmallVariantRecord, filters: SmallVariantQueryFilters) -> bool:
    annotations = record.annotations or [{}]
    normal_match = any(_annotation_matches_normal(annotation, filters) for annotation in annotations)
    if normal_match:
        return True
    if filters.clinvar:
        return any(_annotation_matches_override(annotation, filters) for annotation in annotations)
    annotation_specific_requested = any(
        (
            filters.transcript,
            filters.impact,
            filters.effect,
            filters.clinvar,
            filters.hgvsc,
            filters.hgvsp,
            filters.canonical_only,
            filters.mane_only,
            filters.lof_only,
            filters.max_gnomad_af is not None,
            filters.max_gnomad_exomes_af is not None,
            filters.max_gnomad_genomes_af is not None,
            filters.max_gnomad_popmax_af is not None,
            filters.max_topmed_af is not None,
            filters.max_gnomad_ac is not None,
            filters.max_gnomad_hom_count is not None,
            filters.max_gnomad_hemi_count is not None,
            filters.min_cadd is not None,
            filters.min_revel is not None,
            filters.min_spliceai is not None,
            filters.sift,
            filters.polyphen,
        )
    )
    return not annotation_specific_requested


def _small_record_matches_sample_filters(
    record: SmallVariantRecord,
    filters: SmallVariantQueryFilters,
) -> bool:
    call_map = {call.sample: call for call in record.calls}
    for entry in filters.sample_filters:
        sample_filter = parse_small_variant_sample_filter(entry)
        if sample_filter is None:
            continue
        call = call_map.get(sample_filter.sample_name)
        if call is None:
            if sample_filter.include_absent:
                continue
            return False
        if sample_filter.genotype_values and call.gt not in set(sample_filter.genotype_values):
            return False
        if sample_filter.minimum_genotype_quality is not None:
            if call.gq is None or call.gq < sample_filter.minimum_genotype_quality:
                return False
        if sample_filter.minimum_depth is not None:
            if call.dp is None or call.dp < sample_filter.minimum_depth:
                return False
        if sample_filter.minimum_allele_frequency is not None:
            if not call.af or max(call.af) < sample_filter.minimum_allele_frequency:
                return False
        if sample_filter.minimum_alt_depth is not None:
            alt_depth = call.ad[1] if len(call.ad) > 1 else None
            if alt_depth is None or alt_depth < sample_filter.minimum_alt_depth:
                return False
    return True


def _structural_record_matches_sample_filters(
    record: StructuralVariantRecord,
    filters: StructuralVariantQueryFilters,
) -> bool:
    call_map = {call.sample: call for call in record.calls}
    for entry in filters.sample_filters:
        sample_filter = parse_structural_sample_filter(entry)
        if sample_filter is None:
            continue
        call = call_map.get(sample_filter.sample_name)
        if call is None:
            if sample_filter.include_absent:
                continue
            return False
        if sample_filter.genotype_values and call.gt not in set(sample_filter.genotype_values):
            return False
        if sample_filter.minimum_quality is not None:
            if call.qual is None or call.qual < sample_filter.minimum_quality:
                return False
        if sample_filter.read_support is not None:
            if call.read_support is None or str(call.read_support) != str(sample_filter.read_support):
                return False
        if sample_filter.filter_text and not _contains_casefold(call.filter, sample_filter.filter_text):
            return False
    return True


def _structural_annotation_contains(
    record: StructuralVariantRecord,
    query: str | None,
    *keys: str,
) -> bool:
    if not query:
        return True
    terms = _split_gene_terms(query)
    if not terms:
        return True
    haystack = " ".join(
        str(value)
        for key in keys
        for value in _structural_info_terms(record, key)
    )
    return all(_contains_casefold(haystack, term) for term in terms)


def _structural_record_matches_annotations(
    record: StructuralVariantRecord,
    filters: StructuralVariantQueryFilters,
) -> bool:
    if filters.inheritance and not _contains_casefold(
        _structural_info_text(record, "Inheritance"),
        filters.inheritance,
    ):
        return False
    if filters.phenotype and not _structural_annotation_contains(
        record,
        filters.phenotype,
        "OMIM_phenotype",
        "GENCC_phenotype",
    ):
        return False
    if filters.hpo and not _structural_annotation_contains(record, filters.hpo, "HPO_terms"):
        return False
    if filters.moi and not _structural_annotation_contains(record, filters.moi, "OMIM_MOI", "GENCC_MOI"):
        return False
    if filters.gencc_support and not _contains_casefold(
        _structural_info_text(record, "GENCC_support"),
        filters.gencc_support,
    ):
        return False
    if filters.region_flags:
        present_flags = {_casefold(flag) for flag in _structural_region_flags(record)}
        requested_flags = {_casefold(flag) for flag in filters.region_flags if str(flag).strip()}
        if requested_flags and not requested_flags.intersection(present_flags):
            return False
    if filters.max_control_af is not None:
        control_af = _structural_info_float(record, "Allele_Freq_ALL_Control")
        if control_af is not None and control_af > filters.max_control_af:
            return False
    if filters.max_population_af is not None:
        population_values = [
            value
            for value in (
                _structural_info_float(record, "Allele_Freq_ALL"),
                _structural_info_float(record, "Pop_Freq_ALL"),
                _structural_info_float(record, "q"),
                _structural_info_float(record, "Allele_Freq_ALL_Control"),
            )
            if value is not None
        ]
        if population_values and max(population_values) > filters.max_population_af:
            return False
    if filters.min_pli is not None:
        pli = _structural_pli(record)
        if pli is None or pli < filters.min_pli:
            return False
    return True


def _small_record_matches(
    record: SmallVariantRecord,
    filters: SmallVariantQueryFilters,
    include_regions: Sequence[Region],
    exclude_regions: Sequence[Region],
    exclude_gene_regions: Sequence[Region],
) -> bool:
    if filters.chromosome and normalize_chromosome(record.chr) != normalize_chromosome(filters.chromosome):
        return False
    if filters.overlap:
        if filters.start is not None and record.end < filters.start:
            return False
        if filters.end is not None and record.start > filters.end:
            return False
    else:
        if filters.start is not None and record.start < filters.start:
            return False
        if filters.end is not None and record.end > filters.end:
            return False
    if filters.variant_type and not _contains_casefold(_small_type(record.ref, record.alt), filters.variant_type):
        return False
    if filters.source and not _contains_casefold(record.source, filters.source):
        return False
    if filters.phase_set is not None and not any(call.ps == filters.phase_set for call in record.calls):
        return False
    if include_regions and not _variant_overlaps_regions(record.chr, record.start, record.end, include_regions):
        return False
    if exclude_regions and _variant_overlaps_regions(record.chr, record.start, record.end, exclude_regions):
        return False
    if exclude_gene_regions and _variant_overlaps_regions(record.chr, record.start, record.end, exclude_gene_regions):
        return False
    if filters.gene and not _variant_hits_gene_symbols(record.gene_symbols, filters.gene):
        return False
    if filters.rsid and not (
        _contains_casefold(record.rsid, filters.rsid)
        or any(_contains_casefold(_annotation_text(annotation, "rsid"), filters.rsid) for annotation in record.annotations)
    ):
        return False
    if filters.exclude_clinvar and any(
        _flexible_status_match(_annotation_clinvar(annotation), filters.exclude_clinvar)
        for annotation in record.annotations
    ):
        return False
    if not _matches_small_annotations(record, filters):
        return False
    return _small_record_matches_sample_filters(record, filters)


def _structural_record_matches(
    record: StructuralVariantRecord,
    filters: StructuralVariantQueryFilters,
    include_regions: Sequence[Region],
    selected_samples: Sequence[str],
) -> bool:
    if filters.chromosome and normalize_chromosome(record.chr) != normalize_chromosome(filters.chromosome):
        return False
    if filters.overlap:
        if filters.start is not None and record.end < filters.start:
            return False
        if filters.end is not None and record.start > filters.end:
            return False
    else:
        if filters.start is not None and record.start < filters.start:
            return False
        if filters.end is not None and record.end > filters.end:
            return False
    if filters.variant_type and not _contains_casefold(record.sv_type, filters.variant_type):
        return False
    if filters.source and not _contains_casefold(record.source, filters.source):
        return False
    if filters.length is not None and abs(record.end - record.start) != filters.length:
        return False
    if filters.min_length is not None and abs(record.end - record.start) < filters.min_length:
        return False
    if filters.remote_chr and normalize_chromosome(record.remote_chr or "") != normalize_chromosome(filters.remote_chr):
        return False
    if filters.remote_start is not None and (record.remote_start is None or record.remote_start < filters.remote_start):
        return False
    if include_regions and not _variant_overlaps_regions(record.chr, record.start, record.end, include_regions):
        return False
    if filters.gene and not _variant_hits_gene_symbols(record.gene_symbols, filters.gene):
        return False
    if selected_samples and not any(call.sample in set(selected_samples) for call in record.calls):
        return False
    if not _structural_record_matches_annotations(record, filters):
        return False
    return _structural_record_matches_sample_filters(record, filters)


def _primary_gene_keys(record: SmallVariantRecord) -> tuple[str | None, str | None]:
    annotation = _select_primary_annotation(record.annotations)
    gene = _annotation_gene(annotation) or (record.gene_symbols[0] if record.gene_symbols else None)
    gene_id = _annotation_gene_id(annotation)
    return gene, gene_id


def _chromosome_sort_key(chromosome: str) -> tuple[int, int, str]:
    cleaned = normalize_chromosome(chromosome).upper()
    numeric = _coerce_int(cleaned)
    if numeric is not None:
        return 0, numeric, cleaned
    if cleaned == "X":
        return 1, 23, cleaned
    if cleaned == "Y":
        return 1, 24, cleaned
    if cleaned in {"MT", "M"}:
        return 1, 25, cleaned
    return 9, 0, cleaned


def _small_record_sort_key(record: SmallVariantRecord) -> tuple[int, int, str, int, int, str, str]:
    gene, gene_id = _primary_gene_keys(record)
    return (
        *_chromosome_sort_key(record.chr),
        record.start,
        record.end,
        _casefold(gene_id or gene),
        record.variant_id,
    )


def _sample_small_track_records(
    records: Sequence[SmallVariantRecord],
    limit: int,
) -> list[SmallVariantRecord]:
    if limit <= 0 or len(records) <= limit:
        return list(records)
    if limit == 1:
        return [records[0]]
    last_index = len(records) - 1
    return [
        records[(index * last_index) // (limit - 1)]
        for index in range(limit)
    ]


def _resolve_compound_het_pair_gene_labels(
    left: SmallVariantRecord,
    right: SmallVariantRecord,
) -> tuple[str | None, str | None]:
    left_gene, left_gene_id = _primary_gene_keys(left)
    right_gene, right_gene_id = _primary_gene_keys(right)
    gene_id = (
        left_gene_id
        if left_gene_id and right_gene_id and left_gene_id == right_gene_id
        else left_gene_id or right_gene_id
    )
    gene = (
        left_gene
        if left_gene and right_gene and _casefold(left_gene) == _casefold(right_gene)
        else left_gene or right_gene
    )
    return gene, gene_id


def _compound_het_gene_keys(record: SmallVariantRecord) -> list[tuple[str, str]]:
    gene, gene_id = _primary_gene_keys(record)
    keys: list[tuple[str, str]] = []
    if gene_id:
        keys.append(("gene_id", gene_id))
    if gene:
        keys.append(("gene", _casefold(gene)))
    return keys


def _small_call_map(record: SmallVariantRecord) -> dict[str, SmallVariantCall]:
    return {call.sample: call for call in record.calls}


def _call_is_het(call: SmallVariantCall | None) -> bool:
    return call is not None and call.gt in _HET_GT_VALUES


def _call_is_hom_alt(call: SmallVariantCall | None) -> bool:
    return call is not None and call.gt in _HOM_ALT_GT_VALUES


def _call_has_alt(call: SmallVariantCall | None) -> bool:
    return call is not None and _has_alt_allele(call.gt)


def _is_x_chromosome(chromosome: str | None) -> bool:
    normalized = normalize_chromosome(str(chromosome or ""))
    return normalized.upper() in _X_CHROMOSOME_TOKENS


def _record_matches_de_novo_dominant(
    record: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
) -> bool:
    if not affected_samples:
        return False
    call_map = _small_call_map(record)
    if not all(_call_is_het(call_map.get(sample)) for sample in affected_samples):
        return False
    return not any(_call_has_alt(call_map.get(sample)) for sample in unaffected_samples)


def _record_matches_homozygous_recessive(
    record: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
) -> bool:
    if not affected_samples:
        return False
    if _is_x_chromosome(record.chr):
        return False
    call_map = _small_call_map(record)
    if not all(_call_is_hom_alt(call_map.get(sample)) for sample in affected_samples):
        return False
    return not any(_call_is_hom_alt(call_map.get(sample)) for sample in unaffected_samples)


def _sample_sex_map(sample_rows: Sequence[dict[str, Any]]) -> dict[str, str]:
    return {
        str(row.get("sample_id") or "").strip(): str(row.get("sex") or "").strip().lower()
        for row in sample_rows
        if str(row.get("sample_id") or "").strip()
    }


def _is_male_sex(value: str) -> bool:
    return value in {"m", "male", "1"}


def _record_matches_x_linked_recessive(
    record: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    sample_sex: dict[str, str],
) -> bool:
    if not affected_samples or not _is_x_chromosome(record.chr):
        return False
    call_map = _small_call_map(record)
    if not all(_call_has_alt(call_map.get(sample)) for sample in affected_samples):
        return False

    for sample in unaffected_samples:
        sex = sample_sex.get(sample, "")
        call = call_map.get(sample)
        if _is_male_sex(sex):
            if _call_has_alt(call):
                return False
            continue
        if _call_is_hom_alt(call):
            return False
    return True


def _records_form_compound_het_pair(
    left: SmallVariantRecord,
    right: SmallVariantRecord,
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
) -> bool:
    if left.variant_id == right.variant_id:
        return False
    if not affected_samples:
        return False

    left_calls = _small_call_map(left)
    right_calls = _small_call_map(right)

    if not all(
        _call_is_het(left_calls.get(sample)) and _call_is_het(right_calls.get(sample))
        for sample in affected_samples
    ):
        return False

    return not any(
        _call_has_alt(left_calls.get(sample)) and _call_has_alt(right_calls.get(sample))
        for sample in unaffected_samples
    )


def _compound_het_pairs(
    records: Sequence[SmallVariantRecord],
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
) -> list[SmallVariantCompoundHetPair]:
    if not affected_samples:
        return []

    grouped: dict[tuple[str, str], list[SmallVariantRecord]] = {}
    for record in records:
        gene_keys = _compound_het_gene_keys(record)
        if not gene_keys:
            continue
        for gene_key in gene_keys:
            grouped.setdefault(gene_key, []).append(record)

    pair_map: dict[tuple[str, str], SmallVariantCompoundHetPair] = {}
    for group_records in grouped.values():
        if len(group_records) < 2:
            continue
        for index, left in enumerate(group_records[:-1]):
            for right in group_records[index + 1 :]:
                pair_ids = tuple(sorted((left.variant_id, right.variant_id)))
                if pair_ids in pair_map:
                    continue
                if not _records_form_compound_het_pair(
                    left,
                    right,
                    affected_samples=affected_samples,
                    unaffected_samples=unaffected_samples,
                ):
                    continue
                ordered_left, ordered_right = sorted((left, right), key=_small_record_sort_key)
                gene, gene_id = _resolve_compound_het_pair_gene_labels(ordered_left, ordered_right)
                pair_map[pair_ids] = SmallVariantCompoundHetPair(
                    pair_key="::".join(pair_ids),
                    gene=gene,
                    gene_id=gene_id,
                    left=ordered_left,
                    right=ordered_right,
                )

    return sorted(
        pair_map.values(),
        key=lambda pair: (
            *_small_record_sort_key(pair.left),
            *_small_record_sort_key(pair.right),
        ),
    )


def _compound_het_partner_map(
    records: Sequence[SmallVariantRecord],
    *,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
) -> dict[str, set[str]]:
    partner_map: dict[str, set[str]] = {}
    for pair in _compound_het_pairs(
        records,
        affected_samples=affected_samples,
        unaffected_samples=unaffected_samples,
    ):
        partner_map.setdefault(pair.left.variant_id, set()).add(pair.right.variant_id)
        partner_map.setdefault(pair.right.variant_id, set()).add(pair.left.variant_id)
    return partner_map


def _apply_small_inheritance_filter(
    records: Sequence[SmallVariantRecord],
    *,
    inheritance: str | None,
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    sample_rows: Sequence[dict[str, Any]] | None = None,
) -> list[SmallVariantRecord]:
    if not inheritance:
        return list(records)

    compound_het_ids = set(
        _compound_het_partner_map(
            records,
            affected_samples=affected_samples,
            unaffected_samples=unaffected_samples,
        )
    )

    if inheritance == _COMPOUND_HET_INHERITANCE:
        return [record for record in records if record.variant_id in compound_het_ids]

    if inheritance == _DE_NOVO_DOMINANT_INHERITANCE:
        return [
            record
            for record in records
            if _record_matches_de_novo_dominant(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
            )
        ]

    sample_sex = _sample_sex_map(sample_rows or [])

    if inheritance == _RECESSIVE_HOMOZYGOUS_INHERITANCE:
        return [
            record
            for record in records
            if _record_matches_homozygous_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
            )
        ]

    if inheritance == _X_LINKED_INHERITANCE:
        return [
            record
            for record in records
            if _record_matches_x_linked_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
                sample_sex=sample_sex,
            )
        ]

    if inheritance == _RECESSIVE_INHERITANCE:
        homozygous_ids = {
            record.variant_id
            for record in records
            if _record_matches_homozygous_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
            )
        }
        x_linked_ids = {
            record.variant_id
            for record in records
            if _record_matches_x_linked_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
                sample_sex=sample_sex,
            )
        }
        qualifying_ids = compound_het_ids.union(homozygous_ids).union(x_linked_ids)
        return [record for record in records if record.variant_id in qualifying_ids]

    return list(records)


def _normalize_small_variant_inheritance(value: str | None) -> str | None:
    normalized = _casefold(value).replace("-", "_").replace(" ", "_").replace("/", "_")
    if not normalized:
        return None
    normalized = _SMALL_INHERITANCE_ALIASES.get(normalized, normalized)
    if normalized not in _SUPPORTED_SMALL_INHERITANCE:
        raise HTTPException(status_code=400, detail="Unsupported small-variant inheritance filter")
    return normalized


def _carrier_partner_names(sample_rows: Sequence[dict[str, Any]]) -> tuple[str, str] | None:
    mother = next((row.get("sample_id") for row in sample_rows if row.get("role") == "mother"), None)
    father = next((row.get("sample_id") for row in sample_rows if row.get("role") == "father"), None)
    if mother and father:
        return str(mother), str(father)
    if len(sample_rows) == 2:
        left = str(sample_rows[0].get("sample_id") or "").strip()
        right = str(sample_rows[1].get("sample_id") or "").strip()
        if left and right and left != right:
            return left, right
    return None


def _has_alt_allele(gt: str) -> bool:
    alleles = gt.replace("|", "/").split("/")
    return any(allele not in {"", ".", "0"} for allele in alleles)


def _filter_expanded_carrier_screening(
    records: Sequence[SmallVariantRecord],
    sample_rows: Sequence[dict[str, Any]],
) -> list[SmallVariantRecord]:
    partners = _carrier_partner_names(sample_rows)
    if partners is None:
        return []
    carrier_variants: dict[tuple[str, str], list[SmallVariantRecord]] = {}
    carrier_sets: dict[tuple[str, str], set[str]] = {}
    for record in records:
        gene, gene_id = _primary_gene_keys(record)
        keys: list[tuple[str, str]] = []
        if gene_id:
            keys.append(("gene_id", gene_id))
        if gene:
            keys.append(("gene", gene))
        if not keys:
            continue
        call_map = {call.sample: call.gt for call in record.calls}
        carriers = {partner for partner in partners if _has_alt_allele(call_map.get(partner, "./."))}
        if not carriers:
            continue
        for key in keys:
            carrier_sets.setdefault(key, set()).update(carriers)
            carrier_variants.setdefault(key, []).append(record)
    qualifying_ids: set[str] = set()
    for key, carriers in carrier_sets.items():
        if all(partner in carriers for partner in partners):
            qualifying_ids.update(record.variant_id for record in carrier_variants.get(key, []))
    return [record for record in records if record.variant_id in qualifying_ids]


def _coerce_numeric_metric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _extract_nested_metric(payload: Any, candidate_keys: tuple[str, ...]) -> float | None:
    if isinstance(payload, dict):
        for key in candidate_keys:
            if key in payload:
                value = _coerce_numeric_metric(payload.get(key))
                if value is not None:
                    return value
        for value in payload.values():
            metric = _extract_nested_metric(value, candidate_keys)
            if metric is not None:
                return metric
        return None
    if isinstance(payload, list):
        for item in payload:
            metric = _extract_nested_metric(item, candidate_keys)
            if metric is not None:
                return metric
    return None


def _extract_gene_constraint_metrics(doc: dict[str, Any]) -> dict[str, float | None]:
    extra = doc.get("extra") or {}
    source_status = doc.get("source_status") or {}
    payloads = [extra] + [
        status.get("payload")
        for status in source_status.values()
        if isinstance(status, dict)
    ]
    pli_keys = ("pLI", "pli", "gene_pli", "lof_pLI", "LOF_PLI")
    missense_keys = ("missense_z", "MISSENSE_Z", "missenseZ", "mis_z", "MIS_Z", "gene_missense_z")
    pli = next(
        (metric for payload in payloads if (metric := _extract_nested_metric(payload, pli_keys)) is not None),
        None,
    )
    missense_z = next(
        (
            metric
            for payload in payloads
            if (metric := _extract_nested_metric(payload, missense_keys)) is not None
        ),
        None,
    )
    return {"gene_pli": pli, "gene_missense_z": missense_z}


async def _fetch_gene_regions(
    session: AsyncSession,
    *,
    gene_query: str,
    assembly_id: str | None,
) -> list[Region]:
    terms = _split_gene_terms(gene_query)
    if not terms:
        return []
    clauses = ["(upper(hgnc_symbol) IN :terms OR upper(gene_id) IN :terms)"]
    params: dict[str, Any] = {"terms": [term.upper() for term in terms]}
    bind_params = [bindparam("terms", expanding=True)]
    if assembly_id:
        clauses.append("assembly_id = CAST(:assembly_id AS uuid)")
        params["assembly_id"] = assembly_id
    result = await session.execute(
        text(
            f"""
            SELECT chr, start, "end"
            FROM genes
            WHERE {' AND '.join(clauses)}
            """
        ).bindparams(*bind_params),
        params,
    )
    return [
        Region(chr=row["chr"], start=int(row["start"]), end=int(row["end"]))
        for row in result.mappings().all()
    ]


async def _fetch_panel_regions(session: AsyncSession, panel_id: str) -> list[Region]:
    try:
        UUID(panel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid panel id") from exc
    result = await session.execute(
        text(
            """
            SELECT chr, start, "end"
            FROM gene_panel_regions
            WHERE panel_id = CAST(:panel_id AS uuid)
            ORDER BY gene, chr, start, "end"
            """
        ),
        {"panel_id": panel_id},
    )
    rows = [dict(row) for row in result.mappings().all()]
    if rows:
        return [Region(chr=row["chr"], start=int(row["start"]), end=int(row["end"])) for row in rows]
    exists = await session.execute(
        text("SELECT 1 FROM gene_panels WHERE id = CAST(:panel_id AS uuid)"),
        {"panel_id": panel_id},
    )
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    return []


async def _fetch_gene_constraint_metric_map(
    session: AsyncSession,
    variants: Sequence[VariantOut],
) -> dict[str, dict[str, float | None]]:
    if not variants:
        return {}
    gene_symbols = sorted({variant.gene for variant in variants if variant.gene})
    gene_ids = sorted({variant.gene_id for variant in variants if variant.gene_id})
    clauses: list[str] = []
    params: dict[str, Any] = {}
    bind_params = []
    if gene_symbols:
        clauses.append("upper(hgnc_symbol) IN :gene_symbols")
        params["gene_symbols"] = [symbol.upper() for symbol in gene_symbols]
        bind_params.append(bindparam("gene_symbols", expanding=True))
    if gene_ids:
        clauses.append("gene_id IN :gene_ids")
        params["gene_ids"] = gene_ids
        bind_params.append(bindparam("gene_ids", expanding=True))
    if not clauses:
        return {}
    result = await session.execute(
        text(
            f"""
            SELECT hgnc_symbol, gene_id, extra, source_status, updated_at
            FROM gene_info
            WHERE {' OR '.join(clauses)}
            ORDER BY updated_at DESC
            """
        ).bindparams(*bind_params),
        params,
    )
    by_symbol: dict[str, dict[str, float | None]] = {}
    by_gene_id: dict[str, dict[str, float | None]] = {}
    for row in result.mappings().all():
        doc = dict(row)
        metrics = _extract_gene_constraint_metrics(doc)
        symbol = str(doc.get("hgnc_symbol") or "").strip()
        gene_id = str(doc.get("gene_id") or "").strip()
        if symbol and symbol not in by_symbol:
            by_symbol[symbol] = metrics
        if gene_id and gene_id not in by_gene_id:
            by_gene_id[gene_id] = metrics
    result_map: dict[str, dict[str, float | None]] = {}
    for variant in variants:
        if variant.gene_id and variant.gene_id in by_gene_id:
            result_map[str(variant.id)] = by_gene_id[variant.gene_id]
        elif variant.gene and variant.gene in by_symbol:
            result_map[str(variant.id)] = by_symbol[variant.gene]
    return result_map


def _small_variant_out(record: SmallVariantRecord) -> VariantOut:
    annotation = _select_primary_annotation(record.annotations)
    population_frequencies = _annotation_population_frequencies(annotation)
    return VariantOut(
        _id=record.variant_id,
        chr=record.chr,
        start=record.start,
        end=record.end,
        length=record.end - record.start,
        type=_small_type(record.ref, record.alt),
        source=record.source,
        ref=record.ref,
        alt=record.alt,
        ps=next((call.ps for call in record.calls if call.ps is not None), None),
        gene=_annotation_gene(annotation) or (record.gene_symbols[0] if record.gene_symbols else None),
        gene_id=_annotation_gene_id(annotation),
        impact=_annotation_text(annotation, "impact"),
        effect=_annotation_effect(annotation),
        clinvar=_annotation_clinvar(annotation),
        rsid=record.rsid or _annotation_text(annotation, "rsid"),
        transcript_id=_annotation_text(annotation, "transcript_id", "transcriptId"),
        feature_type=_annotation_text(annotation, "feature_type", "featureType"),
        transcript_biotype=_annotation_text(annotation, "transcript_biotype", "transcriptBiotype"),
        hgvsc=_annotation_text(annotation, "hgvsc"),
        hgvsp=_annotation_text(annotation, "hgvsp"),
        canonical=_annotation_bool(annotation, "canonical"),
        mane_select=_annotation_bool(annotation, "mane_select", "maneSelect"),
        mane_plus_clinical=_annotation_bool(annotation, "mane_plus_clinical", "manePlusClinical"),
        exon=_annotation_text(annotation, "exon"),
        intron=_annotation_text(annotation, "intron"),
        lof=_annotation_text(annotation, "lof"),
        lof_filter=_annotation_text(annotation, "lof_filter", "lofFilter"),
        lof_flags=_annotation_text(annotation, "lof_flags", "lofFlags"),
        gnomad_af=_annotation_float(annotation, "gnomad_af", "gnomadAf"),
        gnomad_hom_count=_annotation_int(annotation, "gnomad_hom_count", "gnomadHomCount"),
        population_frequencies=population_frequencies,
        cadd_raw=_annotation_float(annotation, "cadd_raw", "caddRaw"),
        cadd_phred=_annotation_float(annotation, "cadd_phred", "caddPhred"),
        revel=_annotation_float(annotation, "revel"),
        sift=_annotation_sift(annotation),
        polyphen=_annotation_polyphen(annotation),
        spliceai_ds_ag=_annotation_float(annotation, "spliceai_ds_ag", "spliceaiDsAg"),
        spliceai_ds_al=_annotation_float(annotation, "spliceai_ds_al", "spliceaiDsAl"),
        spliceai_ds_dg=_annotation_float(annotation, "spliceai_ds_dg", "spliceaiDsDg"),
        spliceai_ds_dl=_annotation_float(annotation, "spliceai_ds_dl", "spliceaiDsDl"),
        spliceai_max=_annotation_spliceai_max(annotation),
        annotation_extra=_annotation_extra(annotation),
        genotypes=[
            GenotypeOut(
                sample=call.sample,
                gt=call.gt,
                dp=call.dp,
                ad=call.ad or None,
                af=call.af or None,
                ps=call.ps,
            )
            for call in record.calls
        ],
    )


def _group_review_for_pair(
    left: VariantOut,
    right: VariantOut,
):
    for variant, partner in ((left, right), (right, left)):
        review = variant.review
        compound_het_review = review.compound_het if review else None
        if compound_het_review and str(partner.id) in compound_het_review.partner_variant_ids:
            return compound_het_review
    return None


async def _hydrate_small_variant_outs(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variants: Sequence[VariantOut],
) -> None:
    if not variants:
        return

    review_map = await get_small_variant_review_map(
        session,
        family_uuid=context.family_uuid,
        variant_ids=[str(variant.id) for variant in variants],
    )
    for variant in variants:
        variant.review = review_map.get(str(variant.id))

    metric_map = await _fetch_gene_constraint_metric_map(session, variants)
    for variant in variants:
        metrics = metric_map.get(str(variant.id))
        if not metrics:
            continue
        if variant.gene_pli is None:
            variant.gene_pli = metrics.get("gene_pli")
        if variant.gene_missense_z is None:
            variant.gene_missense_z = metrics.get("gene_missense_z")


def _structural_variant_out(
    record: StructuralVariantRecord,
    selected_samples: Sequence[str],
    review: SmallVariantReviewOut | None = None,
    cytoband: str | None = None,
) -> VariantOut:
    allowed_samples = set(selected_samples)
    calls = [call for call in record.calls if not allowed_samples or call.sample in allowed_samples]
    length = record.sv_len if record.sv_len is not None else record.end - record.start
    annotation_extra = _structural_annotation_extra(record)
    if cytoband:
        annotation_extra["cytoband"] = cytoband
    population_frequencies = annotation_extra.get("population_frequencies")
    return VariantOut(
        _id=record.variant_id,
        chr=record.chr,
        start=record.start,
        end=record.end,
        length=length,
        type=record.sv_type,
        source=record.source,
        qual=calls[0].qual if calls else None,
        read_support=calls[0].read_support if calls else None,
        filter=calls[0].filter if calls else None,
        remote_chr=record.remote_chr,
        remote_start=record.remote_start,
        gene=record.gene_symbols[0] if record.gene_symbols else None,
        gene_pli=annotation_extra.get("pli") if isinstance(annotation_extra.get("pli"), (int, float)) else None,
        population_frequencies=population_frequencies if isinstance(population_frequencies, dict) else {},
        annotation_extra=annotation_extra,
        review=review,
        genotypes=[
            GenotypeOut(
                sample=call.sample,
                gt=call.gt,
                qual=call.qual,
                read_support=call.read_support,
                filter=call.filter,
            )
            for call in calls
        ],
    )


async def _execute_clickhouse(query: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    try:
        return await execute_clickhouse(query, params)
    except ServerException as exc:
        message = str(exc)
        if "UNKNOWN_TABLE" in message or "doesn't exist" in message:
            return []
        raise


async def _fetch_small_variant_rows(
    context: FamilyMetadataContext,
    filters: SmallVariantQueryFilters,
) -> list[SmallVariantRecord]:
    if not context.assembly_name:
        return []
    entries_table = _small_table_name(context.assembly_name, "entries")
    details_table = _small_table_name(context.assembly_name, "variants/details")
    where_clauses = ["e.family_guid = %(family_guid)s", "e.sign = 1"]
    params: dict[str, Any] = {"family_guid": context.family_uuid}
    if context.project_ids:
        where_clauses.append("e.project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(context.project_ids)
    if filters.chromosome:
        where_clauses.append("e.chrom IN %(chromosomes)s")
        params["chromosomes"] = _chromosome_options(filters.chromosome)
    if filters.start is not None:
        if filters.overlap and filters.end is not None:
            where_clauses.append("(e.pos <= %(end)s AND (e.pos + length(e.ref) - 1) >= %(start)s)")
            params["start"] = filters.start
            params["end"] = filters.end
        else:
            where_clauses.append("e.pos >= %(start)s")
            params["start"] = filters.start
    if filters.end is not None and not (filters.overlap and filters.start is not None):
        where_clauses.append("e.pos <= %(end)s")
        params["end"] = filters.end
    query = f"""
        SELECT
            any(e.key) AS key,
            any(e.variantId) AS variant_id,
            any(e.chrom) AS chrom,
            any(e.pos) AS pos,
            any(e.ref) AS ref,
            any(e.alt) AS alt,
            any(d.source) AS source,
            any(e.filters) AS entry_filters,
            any(d.filters) AS detail_filters,
            any(d.rsid) AS rsid,
            any(d.annotationsJson) AS annotations_json,
            any(e.gene_symbols) AS gene_symbols,
            any(e.calls.sampleId) AS sample_ids,
            any(e.calls.gt) AS sample_gts,
            any(e.calls.gq) AS sample_gqs,
            any(e.calls.dp) AS sample_dps,
            any(e.calls.ab) AS sample_abs,
            any(e.calls.af) AS sample_afs,
            any(e.calls.ad) AS sample_ads,
            any(e.calls.ps) AS sample_phase_sets
        FROM {entries_table} AS e
        LEFT JOIN {details_table} AS d ON d.key = e.key
        WHERE {' AND '.join(where_clauses)}
        GROUP BY e.key, e.variantId
        ORDER BY chrom, pos, key
    """
    rows = await _execute_clickhouse(query, params)
    allowed_samples = set(context.sample_name_to_uuid)
    records: list[SmallVariantRecord] = []
    for row in rows:
        (
            variant_key,
            variant_id,
            chrom,
            pos,
            ref,
            alt,
            source,
            entry_filters,
            detail_filters,
            rsid,
            annotations_json,
            gene_symbols,
            sample_ids,
            sample_gts,
            sample_gqs,
            sample_dps,
            sample_abs,
            sample_afs,
            sample_ads,
            sample_phase_sets,
        ) = row
        calls: list[SmallVariantCall] = []
        sample_id_list = _listify(sample_ids)
        for index, sample_id in enumerate(sample_id_list):
            sample_name = str(sample_id or "").strip()
            if not sample_name or sample_name not in allowed_samples:
                continue
            af_values = _float_list(_indexed(sample_afs, index))
            ab_value = _coerce_float(_indexed(sample_abs, index))
            if not af_values and ab_value is not None:
                af_values = [ab_value]
            calls.append(
                SmallVariantCall(
                    sample=sample_name,
                    gt=_normalize_gt(_indexed(sample_gts, index)),
                    gq=_coerce_float(_indexed(sample_gqs, index)),
                    dp=_coerce_int(_indexed(sample_dps, index)),
                    af=af_values,
                    ad=_int_list(_indexed(sample_ads, index)),
                    ps=_coerce_int(_indexed(sample_phase_sets, index)),
                )
            )
        if not calls:
            continue
        start = int(pos)
        ref_text = str(ref or "")
        records.append(
            SmallVariantRecord(
                variant_key=_coerce_int(variant_key),
                variant_id=str(variant_id),
                chr=normalize_chromosome(str(chrom)),
                start=start,
                end=start + max(len(ref_text), 1) - 1,
                ref=ref_text,
                alt=str(alt or ""),
                source=str(source) if source is not None else None,
                rsid=str(rsid) if rsid not in (None, "") else None,
                filters=_string_list(entry_filters) + [
                    item for item in _string_list(detail_filters) if item not in set(_string_list(entry_filters))
                ],
                gene_symbols=_string_list(gene_symbols),
                annotations=_collect_annotations(_decode_json_payload(annotations_json)),
                calls=calls,
            )
        )
    return records


async def _fetch_structural_variant_rows(
    context: FamilyMetadataContext,
    filters: StructuralVariantQueryFilters,
) -> list[StructuralVariantRecord]:
    if not context.assembly_name:
        return []
    entries_table = _structural_table_name(context.assembly_name, "entries")
    details_table = _structural_table_name(context.assembly_name, "variants/details")
    where_clauses = ["e.family_guid = %(family_guid)s", "e.sign = 1"]
    params: dict[str, Any] = {"family_guid": context.family_uuid}
    if context.project_ids:
        where_clauses.append("e.project_guid IN %(project_ids)s")
        params["project_ids"] = tuple(context.project_ids)
    if filters.chromosome:
        where_clauses.append("e.chrom IN %(chromosomes)s")
        params["chromosomes"] = _chromosome_options(filters.chromosome)
    if filters.start is not None:
        if filters.overlap and filters.end is not None:
            where_clauses.append("(e.start <= %(end)s AND e.end >= %(start)s)")
            params["start"] = filters.start
            params["end"] = filters.end
        else:
            where_clauses.append("e.start >= %(start)s")
            params["start"] = filters.start
    if filters.end is not None and not (filters.overlap and filters.start is not None):
        where_clauses.append("e.end <= %(end)s")
        params["end"] = filters.end
    query = f"""
        SELECT
            any(e.key) AS key,
            any(e.variantId) AS variant_id,
            any(e.chrom) AS chrom,
            any(e.start) AS start,
            any(e.end) AS "end",
            any(e.svType) AS sv_type,
            any(e.source) AS source,
            any(d.remoteChrom) AS remote_chr,
            any(d.remoteStart) AS remote_start,
            any(d.remoteEnd) AS remote_end,
            any(d.svLen) AS sv_len,
            any(d.filters) AS filters,
            any(d.annotationsJson) AS annotations_json,
            any(e.gene_symbols) AS gene_symbols,
            any(e.calls.sampleId) AS sample_ids,
            any(e.calls.gt) AS sample_gts,
            any(e.calls.qual) AS sample_quals,
            any(e.calls.readSupport) AS sample_read_supports,
            any(e.calls.filter) AS sample_filters
        FROM {entries_table} AS e
        LEFT JOIN {details_table} AS d ON d.key = e.key
        WHERE {' AND '.join(where_clauses)}
        GROUP BY e.key, e.variantId
        ORDER BY chrom, start, key
    """
    rows = await _execute_clickhouse(query, params)
    allowed_samples = set(context.sample_name_to_uuid)
    records: list[StructuralVariantRecord] = []
    for row in rows:
        (
            variant_key,
            variant_id,
            chrom,
            start,
            end,
            sv_type,
            source,
            remote_chr,
            remote_start,
            remote_end,
            sv_len,
            filters_raw,
            annotations_json,
            gene_symbols,
            sample_ids,
            sample_gts,
            sample_quals,
            sample_read_supports,
            sample_filters_raw,
        ) = row
        calls: list[StructuralVariantCall] = []
        sample_id_list = _listify(sample_ids)
        for index, sample_id in enumerate(sample_id_list):
            sample_name = str(sample_id or "").strip()
            if not sample_name or sample_name not in allowed_samples:
                continue
            calls.append(
                StructuralVariantCall(
                    sample=sample_name,
                    gt=_normalize_gt(_indexed(sample_gts, index)),
                    qual=_coerce_float(_indexed(sample_quals, index)),
                    read_support=_coerce_int(_indexed(sample_read_supports, index)),
                    filter=str(_indexed(sample_filters_raw, index) or "").strip() or None,
                )
            )
        if not calls:
            continue
        records.append(
            StructuralVariantRecord(
                variant_key=_coerce_int(variant_key),
                variant_id=str(variant_id),
                chr=normalize_chromosome(str(chrom)),
                start=int(start),
                end=int(end),
                sv_type=str(sv_type or ""),
                source=str(source) if source is not None else None,
                remote_chr=normalize_chromosome(str(remote_chr)) if remote_chr not in (None, "") else None,
                remote_start=_coerce_int(remote_start),
                remote_end=_coerce_int(remote_end),
                sv_len=_coerce_int(sv_len),
                filters=_string_list(filters_raw),
                gene_symbols=_string_list(gene_symbols),
                annotations=_collect_annotations(_decode_json_payload(annotations_json)),
                calls=calls,
            )
        )
    return records


def _selected_structural_samples(
    context: FamilyMetadataContext,
    sample_names: Sequence[str],
) -> list[str] | None:
    if not sample_names:
        return [row["sample_id"] for row in context.sample_rows]
    selected = [
        sample_name
        for sample_name in sample_names
        if sample_name in context.sample_name_to_uuid
    ]
    return selected or None


def _inheritance_item_sort_key(
    item: tuple[str, SmallVariantCompoundHetPair | SmallVariantRecord],
) -> tuple[int, int, int, str, int, int, str, str]:
    item_type, item_value = item
    if item_type == "group":
        return (0, *_small_record_sort_key(item_value.left))
    return (1, *_small_record_sort_key(item_value))


def _inheritance_result_items(
    *,
    inheritance: str,
    records: Sequence[SmallVariantRecord],
    affected_samples: Sequence[str],
    unaffected_samples: Sequence[str],
    sample_rows: Sequence[dict[str, Any]],
) -> list[tuple[str, SmallVariantCompoundHetPair | SmallVariantRecord]]:
    pair_items = [
        ("group", pair)
        for pair in _compound_het_pairs(
            records,
            affected_samples=affected_samples,
            unaffected_samples=unaffected_samples,
        )
    ]
    if inheritance == _COMPOUND_HET_INHERITANCE:
        return pair_items

    if inheritance == _DE_NOVO_DOMINANT_INHERITANCE:
        return [
            ("variant", record)
            for record in records
            if _record_matches_de_novo_dominant(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
            )
        ]

    if inheritance == _RECESSIVE_HOMOZYGOUS_INHERITANCE:
        return [
            ("variant", record)
            for record in records
            if _record_matches_homozygous_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
            )
        ]

    sample_sex = _sample_sex_map(sample_rows)

    if inheritance == _X_LINKED_INHERITANCE:
        return [
            ("variant", record)
            for record in records
            if _record_matches_x_linked_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
                sample_sex=sample_sex,
            )
        ]

    if inheritance == _RECESSIVE_INHERITANCE:
        homozygous_items = [
            ("variant", record)
            for record in records
            if _record_matches_homozygous_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
            )
        ]
        x_linked_items = [
            ("variant", record)
            for record in records
            if _record_matches_x_linked_recessive(
                record,
                affected_samples=affected_samples,
                unaffected_samples=unaffected_samples,
                sample_sex=sample_sex,
            )
        ]
        deduped_variant_items: dict[str, tuple[str, SmallVariantRecord]] = {}
        for item_type, record in [*homozygous_items, *x_linked_items]:
            deduped_variant_items[record.variant_id] = (item_type, record)
        return sorted(
            [*pair_items, *deduped_variant_items.values()],
            key=_inheritance_item_sort_key,
        )

    return []


async def get_family_small_variants_page(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    page: int,
    page_size: int,
    chr: str | None = None,
    start: int | None = None,
    end: int | None = None,
    intervals: str | None = None,
    inheritance: str | None = None,
    expanded_carrier_screening: bool = False,
    ps: int | None = None,
    type: str | None = None,
    source: str | None = None,
    gene: str | None = None,
    transcript: str | None = None,
    impact: list[str] | None = None,
    effect: list[str] | None = None,
    clinvar: list[str] | None = None,
    exclude_clinvar: list[str] | None = None,
    exclude_gene: str | None = None,
    exclude_intervals: str | None = None,
    rsid: str | None = None,
    hgvsc: str | None = None,
    hgvsp: str | None = None,
    canonical_only: bool = False,
    mane_only: bool = False,
    lof_only: bool = False,
    max_gnomad_af: float | None = None,
    max_gnomad_exomes_af: float | None = None,
    max_gnomad_genomes_af: float | None = None,
    max_gnomad_popmax_af: float | None = None,
    max_topmed_af: float | None = None,
    max_gnomad_ac: int | None = None,
    max_gnomad_hom_count: int | None = None,
    max_gnomad_hemi_count: int | None = None,
    min_cadd: float | None = None,
    min_revel: float | None = None,
    min_spliceai: float | None = None,
    sift: str | None = None,
    polyphen: str | None = None,
    panel_id: str | None = None,
    sample_filters: list[str] | None = None,
    review_classifications: list[str] | None = None,
    review_tags: list[str] | None = None,
    exclude_review_tags: list[str] | None = None,
    has_notes: bool = False,
    overlap: bool = False,
    track_mode: bool = False,
) -> VariantPage:
    normalized_inheritance = _normalize_small_variant_inheritance(inheritance)
    filters = SmallVariantQueryFilters(
        page=page,
        page_size=page_size,
        chromosome=chr,
        start=start,
        end=end,
        intervals=intervals,
        inheritance=normalized_inheritance,
        expanded_carrier_screening=expanded_carrier_screening,
        phase_set=ps,
        variant_type=type,
        source=source,
        gene=gene,
        transcript=transcript,
        impact=impact or [],
        effect=effect or [],
        clinvar=clinvar or [],
        exclude_clinvar=exclude_clinvar or [],
        exclude_gene=exclude_gene,
        exclude_intervals=exclude_intervals,
        rsid=rsid,
        hgvsc=hgvsc,
        hgvsp=hgvsp,
        canonical_only=canonical_only,
        mane_only=mane_only,
        lof_only=lof_only,
        max_gnomad_af=max_gnomad_af,
        max_gnomad_exomes_af=max_gnomad_exomes_af,
        max_gnomad_genomes_af=max_gnomad_genomes_af,
        max_gnomad_popmax_af=max_gnomad_popmax_af,
        max_topmed_af=max_topmed_af,
        max_gnomad_ac=max_gnomad_ac,
        max_gnomad_hom_count=max_gnomad_hom_count,
        max_gnomad_hemi_count=max_gnomad_hemi_count,
        min_cadd=min_cadd,
        min_revel=min_revel,
        min_spliceai=min_spliceai,
        sift=sift,
        polyphen=polyphen,
        panel_id=panel_id,
        sample_filters=sample_filters or [],
        overlap=overlap,
    )
    include_regions: list[Region] = []
    if filters.panel_id:
        include_regions.extend(await _fetch_panel_regions(session, filters.panel_id))
        if not include_regions:
            return VariantPage(total=0, variants=[])
    if filters.gene:
        gene_regions = await _fetch_gene_regions(
            session,
            gene_query=filters.gene,
            assembly_id=context.assembly_id,
        )
        if not gene_regions:
            return VariantPage(total=0, variants=[])
        include_regions.extend(gene_regions)
    if filters.intervals:
        interval_regions = _parse_interval_regions(filters.intervals)
        if not interval_regions:
            return VariantPage(total=0, variants=[])
        include_regions.extend(interval_regions)
    exclude_regions = _parse_interval_regions(filters.exclude_intervals)
    exclude_gene_regions = (
        await _fetch_gene_regions(session, gene_query=filters.exclude_gene, assembly_id=context.assembly_id)
        if filters.exclude_gene
        else []
    )
    review_variant_ids = await list_matching_small_variant_review_ids(
        session,
        family_uuid=context.family_uuid,
        classifications=review_classifications,
        tags=review_tags,
        has_notes=has_notes,
    )
    include_review_filter_active = bool(review_classifications or review_tags or has_notes)
    if include_review_filter_active and not review_variant_ids:
        return VariantPage(total=0, variants=[])
    excluded_review_variant_ids = (
        await list_matching_small_variant_review_ids(
            session,
            family_uuid=context.family_uuid,
            tags=exclude_review_tags,
        )
        if exclude_review_tags
        else set()
    )
    records = await _fetch_small_variant_rows(context, filters)
    filtered = [
        record
        for record in records
        if record.variant_id not in excluded_review_variant_ids
        and ((not include_review_filter_active) or record.variant_id in review_variant_ids)
        and _small_record_matches(record, filters, include_regions, exclude_regions, exclude_gene_regions)
    ]
    affected_sample_names = [
        sample_name
        for sample_name in context.affected_sample_names
        if sample_name in context.sample_name_to_uuid
    ]
    affected_sample_set = set(affected_sample_names)
    unaffected_sample_names = [
        str(row.get("sample_id") or "").strip()
        for row in context.sample_rows
        if str(row.get("sample_id") or "").strip()
        and str(row.get("sample_id") or "").strip() not in affected_sample_set
    ]
    if filters.expanded_carrier_screening:
        filtered = _filter_expanded_carrier_screening(filtered, context.sample_rows)
    if filters.inheritance:
        inheritance_items = _inheritance_result_items(
            inheritance=filters.inheritance,
            records=filtered,
            affected_samples=affected_sample_names,
            unaffected_samples=unaffected_sample_names,
            sample_rows=context.sample_rows,
        )
        total = len(inheritance_items)
        skip = max(page - 1, 0) * page_size if page_size else 0
        page_items = inheritance_items[skip: skip + page_size] if page_size else inheritance_items[skip:]
        page_variant_groups: list[SmallVariantGroupOut] = []
        page_single_variants: list[VariantOut] = []
        group_variant_outs: list[VariantOut] = []
        for item_type, item_value in page_items:
            if item_type == "group":
                pair = item_value
                left_variant = _small_variant_out(pair.left)
                right_variant = _small_variant_out(pair.right)
                group_variant_outs.extend([left_variant, right_variant])
                page_variant_groups.append(
                    SmallVariantGroupOut(
                        group_key=pair.pair_key,
                        gene=pair.gene,
                        gene_id=pair.gene_id,
                        variants=[left_variant, right_variant],
                    )
                )
            else:
                page_single_variants.append(_small_variant_out(item_value))
        await _hydrate_small_variant_outs(
            session,
            context=context,
            variants=[*group_variant_outs, *page_single_variants],
        )
        for group in page_variant_groups:
            if len(group.variants) >= 2:
                group.review = _group_review_for_pair(group.variants[0], group.variants[1])
        return VariantPage(
            total=0 if track_mode else total,
            variants=page_single_variants,
            variant_groups=page_variant_groups,
        )
    total = len(filtered)
    if track_mode:
        page_records = _sample_small_track_records(filtered, page_size)
    else:
        skip = max(page - 1, 0) * page_size if page_size else 0
        page_records = filtered[skip: skip + page_size] if page_size else filtered[skip:]
    variants = [_small_variant_out(record) for record in page_records]
    await _hydrate_small_variant_outs(
        session,
        context=context,
        variants=variants,
    )
    return VariantPage(total=0 if track_mode else total, variants=variants)


async def get_family_structural_variants_page(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    page: int,
    page_size: int,
    chr: str | None = None,
    start: int | None = None,
    end: int | None = None,
    length: int | None = None,
    min_length: int | None = None,
    type: str | None = None,
    source: str | None = None,
    sample_filters: list[str] | None = None,
    samples: list[str] | None = None,
    remote_chr: str | None = None,
    remote_start: int | None = None,
    gene: str | None = None,
    panel_id: str | None = None,
    inheritance: str | None = None,
    phenotype: str | None = None,
    hpo: str | None = None,
    moi: str | None = None,
    gencc_support: str | None = None,
    region_flags: list[str] | None = None,
    max_control_af: float | None = None,
    max_population_af: float | None = None,
    min_pli: float | None = None,
    review_classifications: list[str] | None = None,
    review_tags: list[str] | None = None,
    exclude_review_tags: list[str] | None = None,
    has_notes: bool = False,
    overlap: bool = False,
    track_mode: bool = False,
) -> VariantPage:
    filters = StructuralVariantQueryFilters(
        page=page,
        page_size=page_size,
        chromosome=chr,
        start=start,
        end=end,
        length=length,
        min_length=min_length,
        variant_type=type,
        source=source,
        sample_filters=sample_filters or [],
        selected_samples=samples or [],
        remote_chr=remote_chr,
        remote_start=remote_start,
        gene=gene,
        panel_id=panel_id,
        inheritance=inheritance,
        phenotype=phenotype,
        hpo=hpo,
        moi=moi,
        gencc_support=gencc_support,
        region_flags=region_flags or [],
        max_control_af=max_control_af,
        max_population_af=max_population_af,
        min_pli=min_pli,
        review_classifications=review_classifications or [],
        review_tags=review_tags or [],
        exclude_review_tags=exclude_review_tags or [],
        has_notes=has_notes,
        overlap=overlap,
    )
    selected_samples = _selected_structural_samples(context, filters.selected_samples)
    if selected_samples is None:
        return VariantPage(total=0, variants=[], summary={})
    include_regions: list[Region] = []
    if filters.panel_id:
        include_regions.extend(await _fetch_panel_regions(session, filters.panel_id))
        if not include_regions:
            return VariantPage(total=0, variants=[], summary={})
    if filters.gene:
        gene_regions = await _fetch_gene_regions(
            session,
            gene_query=filters.gene,
            assembly_id=context.assembly_id,
        )
        if not gene_regions:
            return VariantPage(total=0, variants=[], summary={})
        include_regions.extend(gene_regions)
    review_variant_ids = await list_matching_structural_variant_review_ids(
        session,
        family_uuid=context.family_uuid,
        classifications=filters.review_classifications,
        tags=filters.review_tags,
        has_notes=filters.has_notes,
    )
    include_review_filter_active = bool(
        filters.review_classifications or filters.review_tags or filters.has_notes
    )
    if include_review_filter_active and not review_variant_ids:
        return VariantPage(total=0, variants=[], summary={})
    excluded_review_variant_ids = (
        await list_matching_structural_variant_review_ids(
            session,
            family_uuid=context.family_uuid,
            tags=filters.exclude_review_tags,
        )
        if filters.exclude_review_tags
        else set()
    )
    records = await _fetch_structural_variant_rows(context, filters)
    filtered = [
        record
        for record in records
        if record.variant_id not in excluded_review_variant_ids
        and ((not include_review_filter_active) or record.variant_id in review_variant_ids)
        and _structural_record_matches(record, filters, include_regions, selected_samples)
    ]
    summary: dict[str, dict[str, int]] = {}
    for record in filtered:
        summary.setdefault(record.sv_type or "", {})
        source_key = record.source or ""
        summary[record.sv_type or ""][source_key] = summary[record.sv_type or ""].get(source_key, 0) + 1
    total = len(filtered)
    skip = max(page - 1, 0) * page_size if page_size else 0
    page_records = filtered[skip: skip + page_size] if page_size else filtered[skip:]
    review_map = await get_structural_variant_review_map(
        session,
        family_uuid=context.family_uuid,
        variant_ids=[record.variant_id for record in page_records],
    )
    cytoband_map = await _fetch_structural_cytoband_map(
        session,
        assembly_id=context.assembly_id,
        records=page_records,
    )
    variants = [
        _structural_variant_out(
            record,
            selected_samples,
            review_map.get(record.variant_id),
            cytoband_map.get(record.variant_id),
        )
        for record in page_records
    ]
    return VariantPage(
        total=0 if track_mode else total,
        variants=variants,
        summary=None if track_mode else summary,
    )


async def get_family_compound_het_candidates(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    variant_id: str,
    limit: int = 50,
) -> VariantPage:
    filters = SmallVariantQueryFilters(page=1, page_size=max(limit, 1))
    records = await _fetch_small_variant_rows(context, filters)
    source_record = next((record for record in records if record.variant_id == variant_id), None)
    if source_record is None:
        return VariantPage(total=0, variants=[])
    affected_sample_names = [
        sample_name
        for sample_name in context.affected_sample_names
        if sample_name in context.sample_name_to_uuid
    ]
    affected_sample_set = set(affected_sample_names)
    unaffected_sample_names = [
        str(row.get("sample_id") or "").strip()
        for row in context.sample_rows
        if str(row.get("sample_id") or "").strip()
        and str(row.get("sample_id") or "").strip() not in affected_sample_set
    ]
    partner_ids = _compound_het_partner_map(
        records,
        affected_samples=affected_sample_names,
        unaffected_samples=unaffected_sample_names,
    ).get(source_record.variant_id, set())
    if not partner_ids:
        return VariantPage(total=0, variants=[])
    candidates = [
        record
        for record in records
        if record.variant_id in partner_ids and record.variant_id != source_record.variant_id
    ][:limit]
    variants = [_small_variant_out(record) for record in candidates]
    await _hydrate_small_variant_outs(
        session,
        context=context,
        variants=variants,
    )
    return VariantPage(total=len(variants), variants=variants)
