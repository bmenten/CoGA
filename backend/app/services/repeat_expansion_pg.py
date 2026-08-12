from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from fastapi import HTTPException, UploadFile
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.sql import uuid_list_bindparam, uuid_values
from ..schemas import (
    FamilyRepeatExpansionTableOut,
    RepeatExpansionRowOut,
    RepeatExpansionSampleCallOut,
    RepeatExpansionTrackItemOut,
    RepeatExpansionTrackResponse,
)
from .data_scope import chromosome_aliases, normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from .upload_safety import decode_upload_text
from .family_package_common import resolve_vcf_sample_id
from .repeat_expansion_catalog import BUILTIN_REPEAT_LOCI
from .vcf_header_provenance import extract_header_provenance


REPO_STRCHIVE_LOCI_PATH = Path(__file__).resolve().parents[3] / "data" / "ref-data" / "STRchive-loci.json"


def _json_payload(value: Any) -> str:
    return json.dumps(value if value is not None else {})


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, ""))
    text_value = str(value).strip()
    return text_value or None


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _text_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _first_text(value: Any) -> str | None:
    values = _text_list(value)
    return values[0] if values else None


def _unique_texts(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        for item in _text_list(value):
            key = item.lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)
    return unique


def _normalize_strchive_repeat_locus(entry: dict[str, Any]) -> dict[str, Any] | None:
    locus_id = _as_text(entry.get("id") or entry.get("locus_id") or entry.get("trid"))
    gene = _as_text(entry.get("gene") or entry.get("gene_symbol"))
    if not locus_id or not gene:
        return None

    pathogenic_motifs = _text_list(
        entry.get("pathogenic_motif_reference_orientation") or entry.get("pathogenic_motifs")
    )
    reference_motifs = _text_list(
        entry.get("reference_motif_reference_orientation") or entry.get("reference_motifs")
    )
    interruption_motifs = _unique_texts(
        [
            entry.get("interruption_reference_orientation"),
            entry.get("interruption_gene_orientation"),
        ]
    )
    motif = _first_text(pathogenic_motifs) or _first_text(reference_motifs) or _as_text(entry.get("motif"))
    inheritance = _as_text(entry.get("inheritance"))
    intermediate_min = _as_int(entry.get("intermediate_min"))
    pathogenic_min = _as_int(entry.get("pathogenic_min"))
    disease_id = _as_text(entry.get("disease_id"))
    aliases = _unique_texts(
        [
            locus_id,
            gene,
            disease_id,
            entry.get("stripy"),
            entry.get("tr_atlas"),
            entry.get("webstr_hg38"),
            entry.get("webstr_hg19"),
            entry.get("locus_tags"),
            entry.get("disease_tags"),
        ]
    )

    return {
        "locus_id": locus_id,
        "gene": gene,
        "display_name": gene,
        "disease": _as_text(entry.get("disease")) or disease_id or locus_id,
        "inheritance": inheritance,
        "motif": motif,
        "motif_index": 0,
        "warning_min": intermediate_min,
        "pathogenic_min": pathogenic_min,
        "x_linked": "X" in (inheritance or "").upper(),
        "aliases": aliases,
        "notes": _as_text(entry.get("details") or entry.get("disease_description")),
        "metadata": {
            "source": "STRchive",
            "reference_motifs": reference_motifs,
            "pathogenic_motifs": pathogenic_motifs,
            "interruption_motifs": interruption_motifs,
            "benign_min": _as_int(entry.get("benign_min")),
            "benign_max": _as_int(entry.get("benign_max")),
            "intermediate_max": _as_int(entry.get("intermediate_max")),
            "pathogenic_max": _as_int(entry.get("pathogenic_max")),
            "motif_len": _as_int(entry.get("motif_len")),
            "chrom": _as_text(entry.get("chrom")),
            "start_hg38": _as_int(entry.get("start_hg38")),
            "stop_hg38": _as_int(entry.get("stop_hg38")),
            "hpo_terms": _text_list(entry.get("hpo_terms")),
            "evidence": _text_list(entry.get("evidence")),
            "references": _text_list(entry.get("references")),
            "raw": entry,
        },
    }


def load_strchive_repeat_loci(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open() as handle:
        payload = json.load(handle)
    entries: list[dict[str, Any]]
    if isinstance(payload, list):
        entries = [entry for entry in payload if isinstance(entry, dict)]
    elif isinstance(payload, dict):
        entries = [entry for entry in payload.values() if isinstance(entry, dict)]
    else:
        entries = []
    return [
        normalized
        for entry in entries
        if (normalized := _normalize_strchive_repeat_locus(entry)) is not None
    ]


def _configured_strchive_loci_paths() -> list[Path]:
    raw_paths = [settings.trgt_strchive_loci_path, REPO_STRCHIVE_LOCI_PATH]
    paths: list[Path] = []
    seen: set[Path] = set()
    for raw_path in raw_paths:
        if not raw_path:
            continue
        path = Path(raw_path)
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _load_configured_strchive_repeat_loci() -> list[dict[str, Any]]:
    for path in _configured_strchive_loci_paths():
        entries = load_strchive_repeat_loci(path)
        if entries:
            return entries
    return []


async def _seed_repeat_catalog_entries(
    session: AsyncSession,
    entries: Iterable[dict[str, Any]],
) -> None:
    now = datetime.now(timezone.utc)
    for entry in entries:
        entry_metadata = entry.get("metadata", {})
        metadata = {
            "seeded": True,
            "research_use_only": True,
            **(entry_metadata if isinstance(entry_metadata, dict) else {}),
        }
        await session.execute(
            text(
                """
                INSERT INTO repeat_loci (
                    locus_id,
                    gene,
                    display_name,
                    disease,
                    inheritance,
                    motif,
                    motif_index,
                    warning_min,
                    pathogenic_min,
                    x_linked,
                    aliases,
                    notes,
                    metadata,
                    created_at,
                    updated_at
                )
                VALUES (
                    :locus_id,
                    :gene,
                    :display_name,
                    :disease,
                    :inheritance,
                    :motif,
                    :motif_index,
                    :warning_min,
                    :pathogenic_min,
                    :x_linked,
                    CAST(:aliases_json AS jsonb),
                    :notes,
                    CAST(:metadata_json AS jsonb),
                    :created_at,
                    :updated_at
                )
                ON CONFLICT (locus_id) DO UPDATE
                SET
                    gene = EXCLUDED.gene,
                    display_name = EXCLUDED.display_name,
                    disease = EXCLUDED.disease,
                    inheritance = EXCLUDED.inheritance,
                    motif = EXCLUDED.motif,
                    motif_index = EXCLUDED.motif_index,
                    warning_min = EXCLUDED.warning_min,
                    pathogenic_min = EXCLUDED.pathogenic_min,
                    x_linked = EXCLUDED.x_linked,
                    aliases = EXCLUDED.aliases,
                    notes = EXCLUDED.notes,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
                **entry,
                "motif_index": int(entry.get("motif_index", 0)),
                "x_linked": bool(entry.get("x_linked", False)),
                "aliases_json": _json_payload(entry.get("aliases", [])),
                "metadata_json": _json_payload(metadata),
                "notes": entry.get("notes"),
                "created_at": now,
                "updated_at": now,
            },
        )


async def seed_builtin_repeat_catalog(session: AsyncSession) -> None:
    await _seed_repeat_catalog_entries(session, BUILTIN_REPEAT_LOCI)
    await _seed_repeat_catalog_entries(session, _load_configured_strchive_repeat_loci())
    await session.commit()


async def decode_repeat_upload_text(file: UploadFile) -> str:
    return await decode_upload_text(file, kind="TRGT")


def parse_info(info_field: str) -> dict[str, str]:
    info: dict[str, str] = {}
    if info_field and info_field != ".":
        for item in info_field.split(";"):
            if "=" in item:
                key, value = item.split("=", 1)
                info[key] = value
    return info


def parse_format(format_field: str, sample_field: str) -> dict[str, str]:
    keys = format_field.split(":")
    values = sample_field.split(":")
    return {k: v for k, v in zip(keys, values)}


def parse_int_list(value: str | None) -> list[int]:
    if value in (None, "", "."):
        return []
    return [int(item) for item in value.split(",") if item and item != "."]


def parse_float_list(value: str | None) -> list[float | None]:
    if value in (None, "", "."):
        return []
    items: list[float | None] = []
    for item in value.split(","):
        if item in ("", "."):
            items.append(None)
        else:
            items.append(float(item))
    return items


def parse_mc_field(value: str | None) -> list[list[int]]:
    if value in (None, "", "."):
        return []
    parsed: list[list[int]] = []
    for allele in value.split(","):
        if allele in ("", "."):
            parsed.append([])
            continue
        parsed.append([int(item) for item in allele.split("_") if item and item != "."])
    return parsed


def parse_ms_field(value: str | None) -> list[str | None]:
    if value in (None, "", "."):
        return []
    return [item if item not in ("", ".") else None for item in value.split(",")]


def _known_interruption_motifs(locus_document: dict[str, Any]) -> set[str]:
    metadata = locus_document.get("metadata")
    if not isinstance(metadata, dict):
        return set()
    motifs = metadata.get("interruption_motifs")
    return {motif.upper() for motif in _text_list(motifs)}


def _motif_count_summary(motifs: list[str], counts: list[int]) -> list[dict[str, int | str]]:
    summary: list[dict[str, int | str]] = []
    for index, count in enumerate(counts):
        motif = motifs[index] if index < len(motifs) else f"motif_{index + 1}"
        summary.append({"motif": motif, "count": int(count)})
    return summary


def _motif_count_label(motif_counts: list[dict[str, int | str]]) -> str | None:
    nonzero = [
        f"{item['motif']} {item['count']}"
        for item in motif_counts
        if int(item.get("count") or 0) > 0
    ]
    return " + ".join(nonzero) if len(nonzero) > 1 else None


def _has_triplet_interruption(
    *,
    motif_counts: list[dict[str, int | str]],
    canonical_motif: str | None,
    interruption_motifs: set[str],
) -> bool:
    if not canonical_motif or len(canonical_motif) != 3 or not interruption_motifs:
        return False
    return any(
        str(item.get("motif", "")).upper() in interruption_motifs
        and int(item.get("count") or 0) > 0
        for item in motif_counts
    )


def classify_repeat_count(
    repeat_count: int | None,
    warning_min: int | None,
    pathogenic_min: int | None,
    *,
    benign_min: int | None = None,
    benign_max: int | None = None,
    pathogenic_max: int | None = None,
) -> str:
    """Status for one allele's repeat count.

    Almost every locus is pathogenic by *expansion*: bigger is worse, and "at or above
    the threshold" is the whole rule. Two catalogued loci are not. VWA1 (normal exactly
    2, pathogenic 1 or 3) and MIR7-2 (normal 4, pathogenic 3) are pathogenic by
    *contraction*, so their normal count sits at or above ``pathogenic_min`` and a bare
    ``>=`` test calls every healthy call pathogenic.

    The two shapes are told apart by the catalog itself — a benign count at or above
    ``pathogenic_min`` means repeat count and risk do not rise together — rather than by
    naming genes. ``pathogenic_max`` cannot be that test: 73 of 75 loci record one, as
    the largest count observed rather than a ceiling on harm, so treating it as a bound
    would downgrade a 300-repeat HTT allele.

    On a non-monotonic locus a count outside every stated range is ``unknown``, not
    ``normal``: "further from the threshold is safer" is exactly the assumption that
    fails there, and the catalog says nothing about those counts.
    """
    if repeat_count is None:
        return "unknown"

    has_benign_range = benign_min is not None and benign_max is not None
    in_benign_range = has_benign_range and benign_min <= repeat_count <= benign_max
    if in_benign_range:
        return "normal"

    non_monotonic = (
        has_benign_range and pathogenic_min is not None and benign_max >= pathogenic_min
    )
    if non_monotonic:
        upper = pathogenic_max if pathogenic_max is not None else repeat_count
        if pathogenic_min <= repeat_count <= upper:
            return "pathogenic"
        return "unknown"

    if pathogenic_min is not None and repeat_count >= pathogenic_min:
        return "pathogenic"
    if warning_min is not None and repeat_count >= warning_min:
        return "intermediate"
    return "normal"


def summarize_repeat_status(statuses: Iterable[str]) -> str:
    ranking = {"unknown": 0, "normal": 1, "intermediate": 2, "pathogenic": 3}
    best = "unknown"
    for status in statuses:
        if ranking.get(status, 0) > ranking[best]:
            best = status
    return best


def _reclassify_repeat_alleles(
    alleles: Any,
    *,
    warning_min: int | None,
    pathogenic_min: int | None,
    benign_min: int | None = None,
    benign_max: int | None = None,
    pathogenic_max: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(alleles, list):
        return []
    has_bounds = any(
        value is not None
        for value in (warning_min, pathogenic_min, benign_min, benign_max, pathogenic_max)
    )
    reclassified: list[dict[str, Any]] = []
    for allele in alleles:
        if not isinstance(allele, dict):
            continue
        next_allele = dict(allele)
        repeat_count = next_allele.get("repeat_count")
        if repeat_count is not None and has_bounds:
            next_allele["status"] = classify_repeat_count(
                int(repeat_count),
                warning_min,
                pathogenic_min,
                benign_min=benign_min,
                benign_max=benign_max,
                pathogenic_max=pathogenic_max,
            )
        reclassified.append(next_allele)
    return reclassified


def _normalize_x_male_alleles(
    alleles: list[dict[str, Any]],
    *,
    sex: str,
    chrom: str,
) -> list[dict[str, Any]]:
    if sex != "male" or normalize_chromosome(chrom).upper() != "X":
        return alleles
    if not alleles:
        return alleles
    first = alleles[0]
    if len(alleles) == 1:
        return alleles
    return [first]


_REPEAT_LOCI_LOOKUP_SQL = text(
    """
    SELECT
        locus_id,
        gene,
        display_name,
        disease,
        inheritance,
        motif,
        motif_index,
        warning_min,
        pathogenic_min,
        aliases,
        notes,
        metadata
    FROM repeat_loci
    ORDER BY locus_id
    """
)


def _coerce_locus_aliases(value: Any) -> list[str]:
    """Decode a ``repeat_loci.aliases`` JSONB value (a list, or a JSON string
    depending on the driver) into a list of strings for in-memory matching."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


async def _build_repeat_locus_lookup(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Load the (small, static) repeat catalog once and index it by lowercased
    locus_id / gene / display_name / alias so TRGT ingest can resolve a locus in
    memory instead of issuing a sequential-scan SELECT per VCF line.

    On a key collision an exact field match wins in the order
    locus_id > gene > display_name > alias; within a field the first locus in
    catalog order keeps the key. This is at least as precise as the previous
    per-row ``OR ... LIMIT 1`` query, which returned an arbitrary matching row.
    """
    result = await session.execute(_REPEAT_LOCI_LOOKUP_SQL)
    lookup: dict[str, dict[str, Any]] = {}
    best_priority: dict[str, int] = {}

    def _register(value: Any, priority: int, row: dict[str, Any]) -> None:
        if not isinstance(value, str) or not value:
            return
        key = value.lower()
        if key not in best_priority or priority < best_priority[key]:
            best_priority[key] = priority
            lookup[key] = row

    for row in result.mappings().all():
        row_dict = dict(row)
        _register(row_dict.get("locus_id"), 0, row_dict)
        _register(row_dict.get("gene"), 1, row_dict)
        _register(row_dict.get("display_name"), 2, row_dict)
        for alias in _coerce_locus_aliases(row_dict.get("aliases")):
            _register(alias, 3, row_dict)
    return lookup


def _lookup_repeat_locus(
    locus_lookup: dict[str, dict[str, Any]], trid: str | None
) -> dict[str, Any] | None:
    if not trid:
        return None
    return locus_lookup.get(trid.lower())


def _fallback_locus_document(trid: str, motifs: list[str]) -> dict[str, Any]:
    motif = motifs[0] if motifs else None
    return {
        "locus_id": trid or "UNKNOWN",
        "gene": trid or "UNKNOWN",
        "display_name": trid or "UNKNOWN",
        "disease": trid or "Uncatalogued repeat locus",
        "inheritance": None,
        "motif": motif,
        "motif_index": 0,
        "warning_min": None,
        "pathogenic_min": None,
        "aliases": [],
        "notes": "No catalog thresholds available",
    }


async def clear_sample_repeat_expansions(
    session: AsyncSession,
    *,
    sample_uuid: str,
) -> None:
    await session.execute(
        text(
            """
            DELETE FROM repeat_expansions
            WHERE sample_id = CAST(:sample_id AS uuid)
              AND source = 'trgt'
            """
        ),
        {"sample_id": sample_uuid},
    )


_INSERT_REPEAT_EXPANSION_SQL = text(
    """
    INSERT INTO repeat_expansions (
        sample_id,
        family_id,
        assembly_id,
        source,
        locus_id,
        gene,
        display_name,
        disease,
        inheritance,
        chr,
        start,
        "end",
        motif,
        motifs,
        motif_index,
        genotype,
        allele_count,
        alleles,
        warning_min,
        pathogenic_min,
        status,
        metadata,
        uploaded_at
    )
    VALUES (
        CAST(:sample_id AS uuid),
        CAST(:family_id AS uuid),
        CAST(:assembly_id AS uuid),
        :source,
        :locus_id,
        :gene,
        :display_name,
        :disease,
        :inheritance,
        :chr,
        :start,
        :end,
        :motif,
        CAST(:motifs_json AS jsonb),
        :motif_index,
        :genotype,
        :allele_count,
        CAST(:alleles_json AS jsonb),
        :warning_min,
        :pathogenic_min,
        :status,
        CAST(:metadata_json AS jsonb),
        :uploaded_at
    )
    """
)

# Bound to keep a single executemany statement reasonable for genome-wide catalogs.
_REPEAT_EXPANSION_INSERT_CHUNK = 500


async def _insert_repeat_expansion_batch(
    session: AsyncSession, records: list[dict[str, Any]]
) -> None:
    """Insert TRGT records with one batched (executemany) statement per chunk,
    instead of one round-trip per VCF line."""
    for start in range(0, len(records), _REPEAT_EXPANSION_INSERT_CHUNK):
        chunk = records[start : start + _REPEAT_EXPANSION_INSERT_CHUNK]
        if chunk:
            await session.execute(_INSERT_REPEAT_EXPANSION_SQL, chunk)


def _build_trgt_insert_params(
    *,
    sample_context: SampleMetadataContext,
    locus_lookup: dict[str, dict[str, Any]],
    chrom: str,
    pos: str,
    ref: str,
    info_field: str,
    format_field: str,
    sample_field: str,
    header_sample: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    info = parse_info(info_field)
    sample_values = parse_format(format_field, sample_field)

    trid = info.get("TRID") or header_sample
    end = int(info.get("END", int(pos) + len(ref) - 1))
    motifs = [item for item in info.get("MOTIFS", "").split(",") if item]
    locus_document = _lookup_repeat_locus(locus_lookup, trid)
    if locus_document is None:
        locus_document = _fallback_locus_document(trid, motifs)

    allele_bp_lengths = parse_int_list(sample_values.get("AL"))
    allele_confidence = (
        []
        if sample_values.get("ALLR") in (None, "", ".")
        else sample_values.get("ALLR", "").split(",")
    )
    allele_support = parse_int_list(sample_values.get("SD"))
    allele_purity = parse_float_list(sample_values.get("AP"))
    allele_methylation = parse_float_list(sample_values.get("AM"))
    motif_copy_counts = parse_mc_field(sample_values.get("MC"))
    motif_spans = parse_ms_field(sample_values.get("MS"))
    motif_index = int(locus_document.get("motif_index") or 0)
    catalog_motif = _as_text(locus_document.get("motif"))
    if catalog_motif and motifs:
        motif_lookup = {motif.upper(): index for index, motif in enumerate(motifs)}
        motif_index = motif_lookup.get(catalog_motif.upper(), motif_index)
    warning_min = locus_document.get("warning_min")
    pathogenic_min = locus_document.get("pathogenic_min")
    locus_metadata = locus_document.get("metadata")
    if not isinstance(locus_metadata, dict):
        locus_metadata = {}
    interruption_motifs = _known_interruption_motifs(locus_document)

    alleles: list[dict[str, Any]] = []
    allele_total = max(
        len(allele_bp_lengths),
        len(allele_confidence),
        len(allele_support),
        len(allele_purity),
        len(allele_methylation),
        len(motif_copy_counts),
    )
    for index in range(allele_total):
        repeat_count = None
        if index < len(motif_copy_counts) and motif_index < len(motif_copy_counts[index]):
            repeat_count = motif_copy_counts[index][motif_index]
        elif index < len(allele_bp_lengths) and locus_document.get("motif"):
            motif = str(locus_document["motif"])
            if motif and set(motif) != {"N"}:
                repeat_count = max(
                    int(round(allele_bp_lengths[index] / max(len(motif), 1))),
                    0,
                )
        status = classify_repeat_count(
            repeat_count,
            warning_min,
            pathogenic_min,
            benign_min=_as_int(locus_metadata.get("benign_min")),
            benign_max=_as_int(locus_metadata.get("benign_max")),
            pathogenic_max=_as_int(locus_metadata.get("pathogenic_max")),
        )
        motif_counts = (
            _motif_count_summary(motifs, motif_copy_counts[index])
            if index < len(motif_copy_counts)
            else []
        )
        interrupted = _has_triplet_interruption(
            motif_counts=motif_counts,
            canonical_motif=catalog_motif or (motifs[motif_index] if motif_index < len(motifs) else None),
            interruption_motifs=interruption_motifs,
        )
        interruption_label = _motif_count_label(motif_counts)
        if interrupted and not interruption_label:
            interruption_parts = [
                f"{item['motif']} {item['count']}"
                for item in motif_counts
                if str(item.get("motif", "")).upper() in interruption_motifs
                and int(item.get("count") or 0) > 0
            ]
            interruption_label = " + ".join(interruption_parts) if interruption_parts else None
        alleles.append(
            {
                "repeat_count": repeat_count,
                "bp_length": allele_bp_lengths[index] if index < len(allele_bp_lengths) else None,
                "confidence_interval": allele_confidence[index] if index < len(allele_confidence) else None,
                "support_reads": allele_support[index] if index < len(allele_support) else None,
                "purity": allele_purity[index] if index < len(allele_purity) else None,
                "methylation": allele_methylation[index] if index < len(allele_methylation) else None,
                "motif_counts": motif_counts,
                "motif_spans": motif_spans[index] if index < len(motif_spans) else None,
                "interrupted": interrupted,
                "interruption_label": interruption_label,
                "status": status,
            }
        )

    alleles = _normalize_x_male_alleles(
        alleles,
        sex=sample_context.sex,
        chrom=chrom,
    )
    if not alleles:
        alleles = [{"status": "unknown"}]

    status = summarize_repeat_status(allele["status"] for allele in alleles)
    genotype = sample_values.get("GT", "./.")
    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id,
        "source": "trgt",
        "locus_id": locus_document.get("locus_id") or trid,
        "gene": locus_document.get("gene") or trid,
        "display_name": locus_document.get("display_name") or trid,
        "disease": locus_document.get("disease") or trid,
        "inheritance": locus_document.get("inheritance"),
        "chr": normalize_chromosome(chrom),
        "start": int(pos),
        "end": end,
        "motif": locus_document.get("motif") or (motifs[0] if motifs else None),
        "motifs_json": _json_payload(motifs),
        "motif_index": motif_index,
        "genotype": genotype,
        "allele_count": len(alleles),
        "alleles_json": _json_payload(alleles),
        "warning_min": warning_min,
        "pathogenic_min": pathogenic_min,
        "status": status,
        "metadata_json": _json_payload(
            {
                **metadata,
                "trid": trid,
                "motifs": motifs,
                "raw_format": sample_values,
            }
        ),
        "uploaded_at": datetime.now(timezone.utc),
    }


async def _update_sample_repeat_file(
    session: AsyncSession,
    *,
    sample_uuid: str,
    filename: str,
) -> None:
    await session.execute(
        text(
            """
            UPDATE samples
            SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{repeat_files,trgt}',
                to_jsonb(CAST(:filename AS text)),
                true
            )
            WHERE id = CAST(:sample_id AS uuid)
            """
        ),
        {
            "sample_id": sample_uuid,
            "filename": filename,
        },
    )


async def ingest_trgt_text(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    text_value: str,
    metadata: dict[str, Any],
) -> dict[str, int | str]:
    lines = text_value.splitlines()
    header_samples: list[str] = []
    inserted = 0
    processed = 0
    locus_lookup = await _build_repeat_locus_lookup(session)
    insert_records: list[dict[str, Any]] = []

    for line in lines:
        if line.startswith("#CHROM"):
            header_samples = line.strip().split("\t")[9:]
            continue
        if not line or line.startswith("#"):
            continue

        fields = line.rstrip().split("\t")
        if len(fields) < 10:
            continue
        if not header_samples:
            raise HTTPException(status_code=400, detail="TRGT VCF header is missing a sample column")

        processed += 1
        chrom, pos, _vid, ref, _alt, _qual, _filter, info_field, format_field = fields[:9]
        sample_fields = fields[9:]
        insert_records.append(
            _build_trgt_insert_params(
                sample_context=sample_context,
                locus_lookup=locus_lookup,
                chrom=chrom,
                pos=pos,
                ref=ref,
                info_field=info_field,
                format_field=format_field,
                sample_field=sample_fields[0],
                header_sample=header_samples[0],
                metadata=metadata,
            )
        )
        inserted += 1

    await _insert_repeat_expansion_batch(session, insert_records)
    await _update_sample_repeat_file(
        session,
        sample_uuid=sample_context.sample_uuid,
        filename=metadata.get("filename") or "uploaded.vcf",
    )
    # Capture the TRGT caller version into the family's annotation manifest, as the
    # family-level path does. Long-read packages take this per-sample route (the
    # pipeline writes no joint TRGT VCF), so without it the repeat caller's version
    # would be missing from every long-read family's provenance record.
    from .annotation_manifest_service import merge_vcf_header_provenance

    await merge_vcf_header_provenance(
        session,
        family_uuid=sample_context.family_uuid,
        assembly_id=getattr(sample_context, "assembly_id", None),
        modules=extract_header_provenance(lines, modality="repeats").as_modules(),
        modality="repeats",
    )
    await session.commit()
    return {
        "processed": processed,
        "inserted": inserted,
        "source_format": "trgt",
    }


async def ingest_family_trgt_text(
    session: AsyncSession,
    *,
    sample_contexts: dict[str, SampleMetadataContext],
    text_value: str,
    metadata: dict[str, Any],
) -> dict[str, int | str]:
    lines = text_value.splitlines()
    header_samples: list[str] = []
    matched_samples: dict[str, SampleMetadataContext] = {}
    inserted = 0
    processed = 0
    locus_lookup = await _build_repeat_locus_lookup(session)
    insert_records: list[dict[str, Any]] = []

    for line in lines:
        if line.startswith("#CHROM"):
            header_samples = line.strip().split("\t")[9:]
            matched_samples = {}
            for sample_name in header_samples:
                # TRGT names the column after its input alignment ("HG002_sort"), so an
                # exact match is not enough; resolve through the shared alias rules.
                resolved = resolve_vcf_sample_id(sample_name, set(sample_contexts))
                if resolved is not None:
                    matched_samples[sample_name] = sample_contexts[resolved]
            if not matched_samples:
                # Silently inserting zero rows here used to report "imported" while the
                # repeat callset was dropped entirely.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "TRGT VCF sample column(s) "
                        f"{header_samples} match no sample in this family"
                    ),
                )
            for sample_context in matched_samples.values():
                await clear_sample_repeat_expansions(
                    session,
                    sample_uuid=sample_context.sample_uuid,
                )
            continue
        if not line or line.startswith("#"):
            continue

        fields = line.rstrip().split("\t")
        if len(fields) < 10:
            continue
        if not header_samples:
            raise HTTPException(status_code=400, detail="TRGT VCF header is missing sample columns")

        processed += 1
        chrom, pos, _vid, ref, _alt, _qual, _filter, info_field, format_field = fields[:9]
        sample_fields = fields[9:]
        for header_sample, sample_field in zip(header_samples, sample_fields):
            sample_context = matched_samples.get(header_sample)
            if sample_context is None:
                continue
            insert_records.append(
                _build_trgt_insert_params(
                    sample_context=sample_context,
                    locus_lookup=locus_lookup,
                    chrom=chrom,
                    pos=pos,
                    ref=ref,
                    info_field=info_field,
                    format_field=format_field,
                    sample_field=sample_field,
                    header_sample=header_sample,
                    metadata=metadata,
                )
            )
            inserted += 1

    await _insert_repeat_expansion_batch(session, insert_records)
    filename = metadata.get("filename") or "family.trgt.vcf"
    for sample_context in matched_samples.values():
        await _update_sample_repeat_file(
            session,
            sample_uuid=sample_context.sample_uuid,
            filename=filename,
        )
    # Capture the TRGT caller version (and any tool versions in the header) into
    # the family's annotation manifest (best-effort; joins this transaction).
    provenance_ctx = next(iter(matched_samples.values()), None)
    if provenance_ctx is not None:
        from .annotation_manifest_service import merge_vcf_header_provenance

        await merge_vcf_header_provenance(
            session,
            family_uuid=provenance_ctx.family_uuid,
            assembly_id=getattr(provenance_ctx, "assembly_id", None),
            modules=extract_header_provenance(lines, modality="repeats").as_modules(),
            modality="repeats",
        )
    await session.commit()
    return {
        "processed": processed,
        "inserted": inserted,
        "samples": len(matched_samples),
        "source_format": "trgt_family",
    }


async def get_family_repeat_expansion_table_response(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    count_only: bool = False,
) -> FamilyRepeatExpansionTableOut:
    sample_ids = list(context.sample_uuid_to_name)
    if not sample_ids:
        return FamilyRepeatExpansionTableOut(samples=[], loci=[])
    if count_only:
        # Presence check only: count distinct loci without the catalog LATERAL
        # join, grouping, or row serialization the full table response does.
        count_result = await session.execute(
            text(
                """
                SELECT count(DISTINCT locus_id) AS loci_count
                FROM repeat_expansions
                WHERE family_id = CAST(:family_id AS uuid)
                  AND sample_id IN :sample_ids
                """
            ).bindparams(uuid_list_bindparam("sample_ids")),
            {"family_id": context.family_uuid, "sample_ids": uuid_values(sample_ids)},
        )
        return FamilyRepeatExpansionTableOut(loci_count=int(count_result.scalar() or 0))
    result = await session.execute(
        text(
            """
            SELECT
                repeat_expansions.sample_id::text AS sample_uuid,
                COALESCE(catalog.locus_id, repeat_expansions.locus_id) AS locus_id,
                COALESCE(catalog.gene, repeat_expansions.gene) AS gene,
                COALESCE(catalog.display_name, repeat_expansions.display_name) AS display_name,
                COALESCE(catalog.disease, repeat_expansions.disease) AS disease,
                COALESCE(catalog.inheritance, repeat_expansions.inheritance) AS inheritance,
                repeat_expansions.chr,
                repeat_expansions.start,
                repeat_expansions."end",
                COALESCE(catalog.motif, repeat_expansions.motif) AS motif,
                COALESCE(catalog.warning_min, repeat_expansions.warning_min) AS warning_min,
                COALESCE(catalog.pathogenic_min, repeat_expansions.pathogenic_min) AS pathogenic_min,
                catalog.benign_min AS benign_min,
                catalog.benign_max AS benign_max,
                catalog.pathogenic_max AS pathogenic_max,
                repeat_expansions.status,
                repeat_expansions.genotype,
                repeat_expansions.allele_count,
                repeat_expansions.alleles
            FROM repeat_expansions
            LEFT JOIN LATERAL (
                SELECT
                    repeat_loci.locus_id,
                    repeat_loci.gene,
                    repeat_loci.display_name,
                    repeat_loci.disease,
                    repeat_loci.inheritance,
                    repeat_loci.motif,
                    repeat_loci.warning_min,
                    repeat_loci.pathogenic_min,
                    -- Not columns: the ranges ride in the catalog metadata.
                    (repeat_loci.metadata ->> 'benign_min')::int AS benign_min,
                    (repeat_loci.metadata ->> 'benign_max')::int AS benign_max,
                    (repeat_loci.metadata ->> 'pathogenic_max')::int AS pathogenic_max
                FROM repeat_loci
                WHERE lower(repeat_loci.locus_id) = lower(repeat_expansions.locus_id)
                   OR lower(repeat_loci.gene) = lower(repeat_expansions.gene)
                   OR lower(repeat_loci.display_name) = lower(repeat_expansions.display_name)
                   OR EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements_text(repeat_loci.aliases) AS alias(value)
                        WHERE lower(alias.value) IN (
                            lower(repeat_expansions.locus_id),
                            lower(repeat_expansions.gene),
                            lower(repeat_expansions.display_name),
                            lower(repeat_expansions.metadata ->> 'trid')
                        )
                   )
                ORDER BY
                    CASE
                        WHEN lower(repeat_loci.locus_id) = lower(repeat_expansions.locus_id) THEN 0
                        WHEN lower(repeat_loci.gene) = lower(repeat_expansions.gene) THEN 1
                        ELSE 2
                    END
                LIMIT 1
            ) AS catalog ON TRUE
            WHERE repeat_expansions.family_id = CAST(:family_id AS uuid)
              AND repeat_expansions.sample_id IN :sample_ids
            ORDER BY repeat_expansions.chr, repeat_expansions.start, gene
            """
        ).bindparams(uuid_list_bindparam("sample_ids")),
        {
            "family_id": context.family_uuid,
            "sample_ids": uuid_values(sample_ids),
        },
    )
    rows = [dict(row) for row in result.mappings().all()]
    member_meta = {
        row["sample_uuid"]: {
            "role": row.get("role"),
            "affected": row.get("affected"),
            "sex": row.get("sex"),
        }
        for row in context.sample_rows
    }
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        warning_min = row.get("warning_min")
        pathogenic_min = row.get("pathogenic_min")
        alleles = _reclassify_repeat_alleles(
            row.get("alleles", []),
            warning_min=warning_min,
            pathogenic_min=pathogenic_min,
            benign_min=row.get("benign_min"),
            benign_max=row.get("benign_max"),
            pathogenic_max=row.get("pathogenic_max"),
        )
        row_status = summarize_repeat_status(
            allele.get("status", "unknown") for allele in alleles
        )
        if row_status == "unknown":
            row_status = row.get("status", "unknown")
        key = str(row.get("locus_id") or f'{row.get("chr")}:{row.get("start")}')
        locus = grouped.setdefault(
            key,
            {
                "locus_id": row.get("locus_id"),
                "gene": row.get("gene"),
                "display_name": row.get("display_name"),
                "disease": row.get("disease"),
                "inheritance": row.get("inheritance"),
                "chr": row.get("chr"),
                "start": int(row.get("start", 0)),
                "end": int(row.get("end", 0)),
                "motif": row.get("motif"),
                "warning_min": warning_min,
                "pathogenic_min": pathogenic_min,
                "benign_min": row.get("benign_min"),
                "benign_max": row.get("benign_max"),
                "pathogenic_max": row.get("pathogenic_max"),
                "status": row_status,
                "calls": {},
            },
        )
        if row_status == "pathogenic":
            locus["status"] = "pathogenic"
        elif row_status == "intermediate" and locus["status"] != "pathogenic":
            locus["status"] = "intermediate"
        elif locus["status"] == "unknown":
            locus["status"] = row_status
        sample_name = context.sample_uuid_to_name.get(row["sample_uuid"], row["sample_uuid"])
        meta = member_meta.get(row["sample_uuid"], {})
        locus["calls"][sample_name] = RepeatExpansionSampleCallOut(
            sample=sample_name,
            role=meta.get("role"),
            affected=meta.get("affected"),
            sex=meta.get("sex"),
            genotype=row.get("genotype", "./."),
            allele_count=int(row.get("allele_count", 0)),
            alleles=alleles,
            status=row_status,
        )

    ordered_rows = [RepeatExpansionRowOut(**row) for row in grouped.values()]
    ordered_rows.sort(key=lambda row: (row.chr, row.start, row.gene, row.locus_id))
    samples = [
        {
            "sample_id": row["sample_id"],
            "role": row.get("role", "sibling"),
            "affected": bool(row.get("affected", False)),
            "sex": row.get("sex", "und"),
        }
        for row in context.sample_rows
    ]
    return FamilyRepeatExpansionTableOut(
        samples=samples, loci=ordered_rows, loci_count=len(ordered_rows)
    )


async def get_sample_repeat_expansion_track_response(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    sample_name: str,
    chromosomes: Iterable[str] | None = None,
    start: int | None = None,
    end: int | None = None,
) -> RepeatExpansionTrackResponse:
    sample_uuid = context.sample_name_to_uuid.get(sample_name)
    if sample_uuid is None:
        return RepeatExpansionTrackResponse()

    chrom_values: list[str] = []
    for chromosome in chromosomes or []:
        chrom_values.extend(chromosome_aliases(chromosome))
    params: dict[str, Any] = {"sample_id": sample_uuid}
    where_clauses = ["sample_id = CAST(:sample_id AS uuid)"]
    if chrom_values:
        where_clauses.append("chr IN :chromosomes")
        params["chromosomes"] = chrom_values
    if start is not None and end is not None and chrom_values:
        where_clauses.append("start <= :end")
        where_clauses.append("\"end\" >= :start")
        params["start"] = start
        params["end"] = end
    query = text(
        f"""
        SELECT
            locus_id,
            gene,
            display_name,
            disease,
            chr,
            start,
            "end",
            motif,
            warning_min,
            pathogenic_min,
            status,
            alleles
        FROM repeat_expansions
        WHERE {' AND '.join(where_clauses)}
        ORDER BY chr, start
        """
    )
    if chrom_values:
        query = query.bindparams(bindparam("chromosomes", expanding=True))
    result = await session.execute(query, params)
    items = [
        RepeatExpansionTrackItemOut(
            sample=sample_name,
            locus_id=row["locus_id"],
            gene=row["gene"],
            display_name=row["display_name"],
            disease=row["disease"],
            chr=row["chr"],
            start=int(row["start"]),
            end=int(row["end"]),
            motif=row.get("motif"),
            warning_min=row.get("warning_min"),
            pathogenic_min=row.get("pathogenic_min"),
            status=row.get("status", "unknown"),
            allele_repeat_counts=[
                int(allele["repeat_count"])
                for allele in row.get("alleles", [])
                if allele.get("repeat_count") is not None
            ],
            allele_bp_lengths=[
                int(allele["bp_length"])
                for allele in row.get("alleles", [])
                if allele.get("bp_length") is not None
            ],
        )
        for row in result.mappings().all()
    ]
    return RepeatExpansionTrackResponse(items=items)
