from __future__ import annotations

import csv
import gzip
import io
import json
import logging
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.html_sanitize import sanitize_reference_html
from ..schemas import (
    AssemblyReferenceStatusOut,
    BlacklistRegionOut,
    ClinicalCnvOut,
    ChromosomeOut,
    ChromosomeSizeOut,
    DgvDensityBin,
    DgvTrackOut,
    DgvVariantOut,
    GeneOut,
    ReferenceDatasetImportOut,
    ReferenceImportActivityOut,
    ReferenceUploadResult,
    SegmentalDuplicationOut,
)
from .data_scope import chromosome_aliases, normalize_chromosome
from .upload_safety import decode_upload_text

ReferenceDatasetType = Literal[
    "cytobands", "genes", "blacklist", "clinical_cnvs", "segmental_duplications", "dgv"
]
_TRUE_TEXT_VALUES = {"1", "true", "yes", "y", "mane", "mane_select", "select", "canonical"}
logger = logging.getLogger(__name__)

# Named-column aliases for header-aware clinical CNV ingestion. Lets the loader
# accept the knowledgebase builder's TSV (chromosome/syndrome_name/clinical_description/…)
# directly, in addition to the positional bedDetail/simplified formats.
_CLINICAL_CNV_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "chr": ("chr", "chrom", "chromosome"),
    "start": ("start", "begin"),
    "end": ("end", "stop"),
    "label": ("label", "name", "syndrome_name", "region_name", "syndrome"),
    "type": ("type", "cnv_type", "variant_type"),
    "omim_id": ("omim_id", "omim"),
    "omim_title": ("omim_title",),
    "decipher_id": ("decipher_id", "decipher"),
    "description": ("description", "clinical_description"),
    "cytoband": ("cytoband", "cyto_band", "cytogenetic_location"),
    "source_id": ("source_id", "isca_id", "isca"),
    "orpha_id": ("orpha_id", "orphanet_id", "orpha"),
    "orpha_name": ("orpha_name", "orphanet_name"),
    "details_html": ("details_html", "details"),
    "source": ("source",),
    "source_detail": ("source_url", "clingen_url", "source_detail"),
}


def _clinical_cnv_header_index(row: list[str]) -> dict[str, int] | None:
    """Return field->column index if `row` looks like a named CNV header, else None."""
    normalized: dict[str, int] = {}
    for idx, cell in enumerate(row):
        key = str(cell or "").strip().lstrip("#").strip().lower()
        if key and key not in normalized:
            normalized[key] = idx
    field_index: dict[str, int] = {}
    for field, aliases in _CLINICAL_CNV_FIELD_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                field_index[field] = normalized[alias]
                break
    if all(field in field_index for field in ("chr", "start", "end")):
        return field_index
    return None

REPO_CLINICAL_CNVS_PATH = Path(__file__).resolve().parents[3] / "data" / "ref-data" / "clinical_cnv_syndromes_hg38_combined.tsv"
REPO_SEGMENTAL_DUPLICATIONS_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "ref-data"
    / "clinical_cnv_syndromes_hg38_bundle"
    / "ClinGen_recurrent_CNV_V2.1-hg38.bed"
)


def _json_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _text_value(value: object) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _normalized_transcript(value: object) -> str | None:
    text_value = _text_value(value)
    if not text_value:
        return None
    return text_value.split(".", 1)[0].upper()


def _transcript_id_from_gene_row(row: dict[str, object]) -> str:
    extra = _json_dict(row.get("extra"))
    return str(extra.get("transcript_id") or row.get("gene_id") or row.get("hgnc_symbol") or "")


def _first_extra_value(*payloads: dict[str, object], keys: tuple[str, ...]) -> object | None:
    for payload in payloads:
        for key in keys:
            value = payload.get(key)
            if value is not None and value != "":
                return value
    return None


def _field_marks_transcript(value: object, transcript_id: str) -> bool:
    if isinstance(value, bool):
        return value
    text_value = _text_value(value)
    if not text_value:
        return False
    if text_value.lower() in _TRUE_TEXT_VALUES:
        return True
    normalized_value = _normalized_transcript(text_value)
    normalized_transcript = _normalized_transcript(transcript_id)
    return bool(normalized_value and normalized_transcript and normalized_value == normalized_transcript)


def _transcript_matches_reference(value: object, transcript_id: str) -> bool:
    normalized_value = _normalized_transcript(value)
    normalized_transcript = _normalized_transcript(transcript_id)
    return bool(normalized_value and normalized_transcript and normalized_value == normalized_transcript)


def _gene_transcript_priority(row: dict[str, object]) -> tuple[int, int, int, str]:
    extra = _json_dict(row.get("extra"))
    gene_info_extra = _json_dict(row.get("gene_info_extra"))
    clingen_facts = _json_dict(gene_info_extra.get("clingen_gene_facts"))
    transcript_id = _transcript_id_from_gene_row(row)
    mane_reference = _first_extra_value(
        extra,
        gene_info_extra,
        clingen_facts,
        keys=(
            "mane_select_transcript",
            "maneSelectTranscript",
            "MANE_SELECT",
            "MANE Select Transcript",
        ),
    )
    canonical_reference = _first_extra_value(
        extra,
        gene_info_extra,
        keys=(
            "ensembl_canonical_transcript",
            "canonical_transcript",
            "canonicalTranscript",
        ),
    )
    is_mane = (
        _field_marks_transcript(extra.get("mane_select"), transcript_id)
        or _field_marks_transcript(extra.get("maneSelect"), transcript_id)
        or _field_marks_transcript(extra.get("MANE_SELECT"), transcript_id)
        or _transcript_matches_reference(mane_reference, transcript_id)
    )
    is_canonical = (
        _field_marks_transcript(extra.get("canonical"), transcript_id)
        or _field_marks_transcript(extra.get("is_canonical"), transcript_id)
        or _field_marks_transcript(extra.get("CANONICAL"), transcript_id)
        or _transcript_matches_reference(canonical_reference, transcript_id)
    )
    rank = 0 if is_mane else 1 if is_canonical else 2
    length = int(row.get("end") or 0) - int(row.get("start") or 0)
    exon_count = len(row.get("exons") or [])
    return (rank, -length, -exon_count, transcript_id)


def _select_preferred_gene_rows(rows: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    # Cache each gene's winning priority tuple so the stored winner is not
    # re-scored against every later candidate (priority is computed once per row).
    preferred_by_gene: dict[tuple[str, str], tuple[tuple, dict[str, object]]] = {}
    for row in rows:
        gene_key = (str(row.get("chr") or ""), str(row.get("hgnc_symbol") or "").upper())
        priority = _gene_transcript_priority(row)
        current = preferred_by_gene.get(gene_key)
        if current is None or priority < current[0]:
            preferred_by_gene[gene_key] = (priority, row)
    return sorted(
        (row for _priority, row in preferred_by_gene.values()),
        key=lambda row: (int(row.get("start") or 0), int(row.get("end") or 0), str(row.get("hgnc_symbol") or "")),
    )


def _require_uuid(value: str, detail: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


async def _get_assembly_by_id(
    session: AsyncSession,
    assembly_id: str,
) -> dict[str, str]:
    _require_uuid(assembly_id, "Invalid assembly id")
    result = await session.execute(
        text(
            """
            SELECT id::text AS id, assembly_name, version
            FROM assemblies
            WHERE id = CAST(:assembly_id AS uuid)
            """
        ),
        {"assembly_id": assembly_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Assembly not found")
    return dict(row)


async def _get_assembly_by_name(
    session: AsyncSession,
    assembly_name: str,
) -> dict[str, str]:
    result = await session.execute(
        text(
            """
            SELECT id::text AS id, assembly_name, version
            FROM assemblies
            WHERE assembly_name = :assembly_name
            ORDER BY release_date DESC, version DESC
            LIMIT 1
            """
        ),
        {"assembly_name": assembly_name},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Assembly not found")
    return dict(row)


async def decode_reference_upload(file: UploadFile) -> str:
    return await decode_upload_text(file, kind="Reference")


def _reader_from_text(text_value: str) -> Iterable[list[str]]:
    return csv.reader(io.StringIO(text_value), delimiter="\t")


def _is_interval_header_row(row: list[str]) -> bool:
    return len(row) >= 3 and row[0].strip().lower() in {"chrom", "chr"} and row[1].strip().lower() == "start"


def _is_black_rgb(value: str | None) -> bool:
    if value is None:
        return False
    rgb_text = value.strip().replace(" ", "")
    return rgb_text in {"0", "0,0,0"}


def _configured_reference_path(primary: str | None, fallback: Path) -> Path | None:
    candidates: list[Path] = []
    if primary:
        candidates.append(Path(primary))
    candidates.append(fallback)
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            return candidate
    return None


def _read_reference_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode()
    except UnicodeDecodeError:
        return gzip.decompress(raw).decode()


# Per-dataset row-count queries by assembly — single source of truth shared by
# _assembly_dataset_count, apply_reference_dataset_text, and (imported) by
# reference_source_service.
_DATASET_COUNT_QUERY: dict[str, str] = {
    "cytobands": "SELECT COUNT(*) FROM chromosomes WHERE assembly_id = CAST(:assembly_id AS uuid)",
    "genes": "SELECT COUNT(*) FROM genes WHERE assembly_id = CAST(:assembly_id AS uuid)",
    "blacklist": "SELECT COUNT(*) FROM blacklist WHERE assembly_id = CAST(:assembly_id AS uuid)",
    "clinical_cnvs": "SELECT COUNT(*) FROM clinical_cnvs WHERE assembly_id = CAST(:assembly_id AS uuid)",
    "segmental_duplications": "SELECT COUNT(*) FROM segmental_duplications WHERE assembly_id = CAST(:assembly_id AS uuid)",
    "dgv": "SELECT COUNT(*) FROM dgv_variants WHERE assembly_id = CAST(:assembly_id AS uuid)",
}


async def _assembly_dataset_count(
    session: AsyncSession,
    *,
    assembly_id: str,
    dataset_type: ReferenceDatasetType,
) -> int:
    result = await session.execute(
        text(_DATASET_COUNT_QUERY[dataset_type]), {"assembly_id": assembly_id}
    )
    return int(result.scalar_one() or 0)


async def list_reference_statuses(
    session: AsyncSession,
) -> list[AssemblyReferenceStatusOut]:
    result = await session.execute(
        text(
            """
            SELECT
                a.id::text AS assembly_id,
                a.assembly_name,
                COALESCE(chr_counts.count, 0) AS chromosomes,
                COALESCE(gene_counts.count, 0) AS genes,
                COALESCE(blacklist_counts.count, 0) AS blacklist_regions,
                COALESCE(cnv_counts.count, 0) AS clinical_cnvs,
                COALESCE(segdup_counts.count, 0) AS segmental_duplications,
                COALESCE(dgv_counts.count, 0) AS dgv
            FROM assemblies a
            LEFT JOIN (
                SELECT assembly_id, COUNT(*) AS count
                FROM chromosomes
                GROUP BY assembly_id
            ) AS chr_counts ON chr_counts.assembly_id = a.id
            LEFT JOIN (
                SELECT assembly_id, COUNT(*) AS count
                FROM genes
                GROUP BY assembly_id
            ) AS gene_counts ON gene_counts.assembly_id = a.id
            LEFT JOIN (
                SELECT assembly_id, COUNT(*) AS count
                FROM blacklist
                GROUP BY assembly_id
            ) AS blacklist_counts ON blacklist_counts.assembly_id = a.id
            LEFT JOIN (
                SELECT assembly_id, COUNT(*) AS count
                FROM clinical_cnvs
                GROUP BY assembly_id
            ) AS cnv_counts ON cnv_counts.assembly_id = a.id
            LEFT JOIN (
                SELECT assembly_id, COUNT(*) AS count
                FROM segmental_duplications
                GROUP BY assembly_id
            ) AS segdup_counts ON segdup_counts.assembly_id = a.id
            LEFT JOIN (
                SELECT assembly_id, COUNT(*) AS count
                FROM dgv_variants
                GROUP BY assembly_id
            ) AS dgv_counts ON dgv_counts.assembly_id = a.id
            ORDER BY a.assembly_name, a.version
            """
        )
    )
    # Latest import per (assembly, dataset_type) for "last updated / by / count".
    imports_result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (assembly_id, dataset_type)
                assembly_id::text AS assembly_id,
                dataset_type,
                inserted,
                replaced,
                source,
                performed_by,
                performed_at
            FROM reference_dataset_imports
            ORDER BY assembly_id, dataset_type, performed_at DESC
            """
        )
    )
    imports_by_assembly: dict[str, list[ReferenceDatasetImportOut]] = {}
    for row in imports_result.mappings().all():
        imports_by_assembly.setdefault(row["assembly_id"], []).append(
            ReferenceDatasetImportOut(
                dataset_type=row["dataset_type"],
                inserted=int(row["inserted"] or 0),
                replaced=bool(row["replaced"]),
                source=row.get("source"),
                performed_by=row.get("performed_by"),
                performed_at=row["performed_at"],
            )
        )
    return [
        AssemblyReferenceStatusOut(
            assembly_id=row["assembly_id"],
            assembly_name=row["assembly_name"],
            chromosomes=int(row["chromosomes"]),
            genes=int(row["genes"]),
            blacklist_regions=int(row["blacklist_regions"]),
            clinical_cnvs=int(row["clinical_cnvs"]),
            segmental_duplications=int(row["segmental_duplications"]),
            dgv=int(row["dgv"]),
            last_imports=imports_by_assembly.get(row["assembly_id"], []),
        )
        for row in result.mappings().all()
    ]


async def list_recent_reference_imports(
    session: AsyncSession,
    *,
    limit: int = 20,
) -> list[ReferenceImportActivityOut]:
    """Chronological feed of dataset imports across all assemblies for the
    "Recent reference activity" panel — every upload, UCSC import, and CNV-KB
    rebuild is recorded uniformly via reference_dataset_imports."""
    result = await session.execute(
        text(
            """
            SELECT
                i.assembly_id::text AS assembly_id,
                a.assembly_name,
                s.name AS species_name,
                i.dataset_type,
                i.inserted,
                i.replaced,
                i.source,
                i.performed_by,
                i.performed_at
            FROM reference_dataset_imports i
            JOIN assemblies a ON a.id = i.assembly_id
            JOIN species s ON s.id = a.species_id
            ORDER BY i.performed_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [
        ReferenceImportActivityOut(
            assembly_id=row["assembly_id"],
            assembly_name=row["assembly_name"],
            species_name=row["species_name"],
            dataset_type=row["dataset_type"],
            inserted=int(row["inserted"] or 0),
            replaced=bool(row["replaced"]),
            source=row.get("source"),
            performed_by=row.get("performed_by"),
            performed_at=row["performed_at"],
        )
        for row in result.mappings().all()
    ]


async def seed_builtin_reference_tracks(session: AsyncSession) -> None:
    if not settings.reference_bootstrap_enabled:
        return

    assembly_name = settings.reference_bootstrap_assembly_name.strip()
    if not assembly_name:
        return

    try:
        assembly = await _get_assembly_by_name(session, assembly_name)
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.info("Skipping reference track bootstrap: assembly '%s' not found", assembly_name)
            return
        raise

    assembly_id = str(assembly["id"])
    bootstrap_jobs: list[tuple[ReferenceDatasetType, Path]] = []

    clinical_cnvs_path = _configured_reference_path(
        settings.reference_clinical_cnvs_path,
        REPO_CLINICAL_CNVS_PATH,
    )
    if clinical_cnvs_path is not None:
        bootstrap_jobs.append(("clinical_cnvs", clinical_cnvs_path))

    segdup_path = _configured_reference_path(
        settings.reference_segmental_duplications_path,
        REPO_SEGMENTAL_DUPLICATIONS_PATH,
    )
    if segdup_path is not None:
        bootstrap_jobs.append(("segmental_duplications", segdup_path))

    if not bootstrap_jobs:
        logger.info("Skipping reference track bootstrap: no source files found")
        return

    for dataset_type, path in bootstrap_jobs:
        existing = await _assembly_dataset_count(
            session,
            assembly_id=assembly_id,
            dataset_type=dataset_type,
        )
        if existing > 0:
            continue
        try:
            text_value = _read_reference_text(path)
            result = await apply_reference_dataset_text(
                session,
                assembly_id=assembly_id,
                dataset_type=dataset_type,
                text_value=text_value,
                overwrite=False,
                commit=False,
            )
            logger.info(
                "Bootstrapped %s for %s from %s (%d rows)",
                dataset_type,
                assembly_name,
                path,
                result.inserted,
            )
        except Exception:
            logger.exception(
                "Failed to bootstrap %s for assembly %s from %s",
                dataset_type,
                assembly_name,
                path,
            )
    await session.commit()


async def upload_reference_dataset(
    session: AsyncSession,
    *,
    assembly_id: str,
    dataset_type: ReferenceDatasetType,
    file: UploadFile,
    overwrite: bool,
    performed_by: str | None = None,
) -> ReferenceUploadResult:
    text_value = await decode_reference_upload(file)
    return await apply_reference_dataset_text(
        session,
        assembly_id=assembly_id,
        dataset_type=dataset_type,
        text_value=text_value,
        overwrite=overwrite,
        performed_by=performed_by,
        source="upload",
    )


# DGV is huge (~2M variants for hg38), so rows are inserted in bounded batches
# rather than one giant statement to keep memory and statement size in check.
_DGV_INSERT_CHUNK = 20000
_DGV_GAIN_TOKENS = ("gain", "duplication", "insertion", "tandem")
_DGV_LOSS_TOKENS = ("loss", "deletion")


def dgv_variant_class(subtype: str | None, variant_type: str | None = None) -> str:
    """Normalize a DGV variantsubtype into a colour bucket: gain / loss / mixed / other."""
    haystack = f"{subtype or ''} {variant_type or ''}".lower()
    has_gain = any(token in haystack for token in _DGV_GAIN_TOKENS)
    has_loss = any(token in haystack for token in _DGV_LOSS_TOKENS)
    if (has_gain and has_loss) or "complex" in haystack:
        return "mixed"
    if has_loss:
        return "loss"
    if has_gain:
        return "gain"
    return "other"


def _opt_int(value: str | None) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _opt_float(value: str | None) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_dgv_row(
    row: list[str],
    *,
    header_index: dict[str, int] | None,
    assembly_id: str,
) -> dict[str, object] | None:
    """Build a dgv_variants insert dict from one TSV row, or None to skip it.

    Columns are resolved by header name when a header is present, otherwise by
    the fixed DGV column order (variantaccession, chr, start, end, varianttype,
    variantsubtype, reference, …, frequency, samplesize, observedgains,
    observedlosses)."""

    def col(name: str, pos: int) -> str:
        idx = header_index.get(name) if header_index is not None else pos
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    chrom = col("chr", 1)
    start_raw = col("start", 2)
    end_raw = col("end", 3)
    if not chrom or not start_raw or not end_raw:
        return None
    try:
        start_i = int(start_raw.replace(",", ""))
        end_i = int(end_raw.replace(",", ""))
    except ValueError:
        return None
    if end_i < start_i:
        start_i, end_i = end_i, start_i

    subtype = col("variantsubtype", 5) or None
    variant_type = col("varianttype", 4) or None
    return {
        "assembly_id": assembly_id,
        "chr": normalize_chromosome(chrom),
        "start": start_i,
        "end": end_i,
        "accession": col("variantaccession", 0) or None,
        "variant_type": variant_type,
        "variant_subtype": subtype,
        "variant_class": dgv_variant_class(subtype, variant_type),
        "frequency": _opt_float(col("frequency", 13)),
        "observed_gains": _opt_int(col("observedgains", 15)),
        "observed_losses": _opt_int(col("observedlosses", 16)),
        "sample_size": _opt_int(col("samplesize", 14)),
        "source": col("reference", 6) or None,
    }


async def insert_dgv_batch(session: AsyncSession, rows: list[dict[str, object]]) -> None:
    await session.execute(
        text(
            """
            INSERT INTO dgv_variants (
                assembly_id, chr, start, "end", accession, variant_type,
                variant_subtype, variant_class, frequency, observed_gains,
                observed_losses, sample_size, source
            )
            VALUES (
                CAST(:assembly_id AS uuid), :chr, :start, :end, :accession, :variant_type,
                :variant_subtype, :variant_class, :frequency, :observed_gains,
                :observed_losses, :sample_size, :source
            )
            """
        ),
        rows,
    )


async def apply_reference_dataset_text(
    session: AsyncSession,
    *,
    assembly_id: str,
    dataset_type: ReferenceDatasetType,
    text_value: str,
    overwrite: bool,
    commit: bool = True,
    performed_by: str | None = None,
    source: str | None = None,
) -> ReferenceUploadResult:
    assembly = await _get_assembly_by_id(session, assembly_id)

    existing_count = await _assembly_dataset_count(
        session, assembly_id=assembly_id, dataset_type=dataset_type
    )
    replaced = existing_count > 0
    if replaced and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"{dataset_type.replace('_', ' ')} already exist for this assembly",
        )

    delete_query = {
        "cytobands": "DELETE FROM chromosomes WHERE assembly_id = CAST(:assembly_id AS uuid)",
        "genes": "DELETE FROM genes WHERE assembly_id = CAST(:assembly_id AS uuid)",
        "blacklist": "DELETE FROM blacklist WHERE assembly_id = CAST(:assembly_id AS uuid)",
        "clinical_cnvs": "DELETE FROM clinical_cnvs WHERE assembly_id = CAST(:assembly_id AS uuid)",
        "segmental_duplications": "DELETE FROM segmental_duplications WHERE assembly_id = CAST(:assembly_id AS uuid)",
        "dgv": "DELETE FROM dgv_variants WHERE assembly_id = CAST(:assembly_id AS uuid)",
    }[dataset_type]
    if replaced:
        await session.execute(text(delete_query), {"assembly_id": assembly_id})

    inserted = 0

    if dataset_type == "cytobands":
        chromosomes: dict[str, dict[str, object]] = {}
        for row in _reader_from_text(text_value):
            if len(row) < 5 or row[0].startswith("#"):
                continue
            chrom, start, end, band, stain = row[:5]
            try:
                start_i = int(start)
                end_i = int(end)
            except ValueError:
                continue
            chrom = normalize_chromosome(chrom)
            entry = chromosomes.setdefault(chrom, {"size": 0, "bands": []})
            bands = entry["bands"]
            assert isinstance(bands, list)
            bands.append(
                {
                    "name": band,
                    "start": start_i,
                    "end": end_i,
                    "stain": stain,
                }
            )
            entry["size"] = max(int(entry["size"]), end_i)

        rows = [
            {
                "assembly_id": assembly_id,
                "chr": chrom,
                "size": int(data["size"]),
                "bands": json.dumps(data["bands"]),
            }
            for chrom, data in chromosomes.items()
        ]
        if not rows:
            raise HTTPException(status_code=400, detail="No valid cytoband rows found")
        await session.execute(
            text(
                """
                INSERT INTO chromosomes (assembly_id, chr, size, bands)
                VALUES (CAST(:assembly_id AS uuid), :chr, :size, CAST(:bands AS jsonb))
                """
            ),
            rows,
        )
        inserted = len(rows)

    elif dataset_type == "genes":
        rows: list[dict[str, object]] = []
        for row in _reader_from_text(text_value):
            if not row or row[0].startswith("#") or len(row) < 12:
                continue
            (
                chrom,
                start,
                end,
                gene,
                score,
                strand,
                ccds_id,
                transcript_id,
                exon_count,
                exon_intervals,
                intron_count,
                intron_intervals,
            ) = row[:12]
            try:
                start_i = int(start)
                end_i = int(end)
            except ValueError:
                continue

            exons = []
            if exon_intervals:
                for idx, interval in enumerate(filter(None, exon_intervals.split(","))):
                    try:
                        exon_start, exon_end = interval.split("-")
                        exons.append(
                            {
                                "name": f"exon{idx + 1}",
                                "start": int(exon_start),
                                "end": int(exon_end),
                            }
                        )
                    except ValueError:
                        continue

            rows.append(
                {
                    "assembly_id": assembly_id,
                    "gene_id": transcript_id or gene,
                    "hgnc_symbol": gene,
                    "chr": normalize_chromosome(chrom),
                    "start": start_i,
                    "end": end_i,
                    "exons": json.dumps(exons),
                    "strand": 1 if strand == "+" else -1,
                    "biotype": "unknown",
                    "description": "",
                    "source": "refgene",
                    "extra": json.dumps(
                        {
                            "score": score,
                            "ccds_id": ccds_id,
                            "transcript_id": transcript_id,
                            "exon_count": int(exon_count) if exon_count else 0,
                            "intron_count": int(intron_count) if intron_count else 0,
                            "intron_intervals": intron_intervals,
                        }
                    ),
                }
            )
        if not rows:
            raise HTTPException(status_code=400, detail="No valid gene rows found")
        await session.execute(
            text(
                """
                INSERT INTO genes (
                    assembly_id,
                    gene_id,
                    hgnc_symbol,
                    chr,
                    start,
                    "end",
                    exons,
                    strand,
                    biotype,
                    description,
                    source,
                    extra
                )
                VALUES (
                    CAST(:assembly_id AS uuid),
                    :gene_id,
                    :hgnc_symbol,
                    :chr,
                    :start,
                    :end,
                    CAST(:exons AS jsonb),
                    :strand,
                    :biotype,
                    :description,
                    :source,
                    CAST(:extra AS jsonb)
                )
                """
            ),
            rows,
        )
        inserted = len(rows)

    elif dataset_type == "blacklist":
        rows = []
        for row in _reader_from_text(text_value):
            if not row or row[0].startswith("#") or _is_interval_header_row(row) or len(row) < 4:
                continue
            chrom, start, end, label = row[:4]
            try:
                start_i = int(start)
                end_i = int(end)
            except ValueError:
                continue
            rows.append(
                {
                    "assembly_id": assembly_id,
                    "chr": normalize_chromosome(chrom),
                    "start": start_i,
                    "end": end_i,
                    "label": label,
                }
            )
        if not rows:
            raise HTTPException(status_code=400, detail="No valid blacklist rows found")
        await session.execute(
            text(
                """
                INSERT INTO blacklist (assembly_id, chr, start, "end", label)
                VALUES (CAST(:assembly_id AS uuid), :chr, :start, :end, :label)
                """
            ),
            rows,
        )
        inserted = len(rows)

    elif dataset_type == "clinical_cnvs":
        def _cell(cells: list[str], idx: int | None) -> str | None:
            if idx is None or len(cells) <= idx or cells[idx] is None:
                return None
            value = str(cells[idx]).strip()
            return value or None

        usable_rows = [
            row
            for row in _reader_from_text(text_value)
            if row and not row[0].startswith("#") and not row[0].startswith("track")
        ]
        header_index = _clinical_cnv_header_index(usable_rows[0]) if usable_rows else None
        body_rows = usable_rows[1:] if header_index else usable_rows

        rows = []
        for row in body_rows:
            if header_index is not None:
                # Named-column format (e.g. the knowledgebase builder output).
                chrom = _cell(row, header_index.get("chr"))
                start = _cell(row, header_index.get("start"))
                end = _cell(row, header_index.get("end"))
                if chrom is None or start is None or end is None:
                    continue
                try:
                    start_i = int(str(start).replace(",", ""))
                    end_i = int(str(end).replace(",", ""))
                except ValueError:
                    continue
                label = _cell(row, header_index.get("label")) or chrom
                cnv_type = _cell(row, header_index.get("type"))
                omim_id = _cell(row, header_index.get("omim_id"))
                decipher_id = _cell(row, header_index.get("decipher_id"))
                description = _cell(row, header_index.get("description"))
                cytoband = _cell(row, header_index.get("cytoband"))
                source_id = _cell(row, header_index.get("source_id"))
                omim_title = _cell(row, header_index.get("omim_title"))
                orpha_id = _cell(row, header_index.get("orpha_id"))
                orpha_name = _cell(row, header_index.get("orpha_name"))
                html = _cell(row, header_index.get("details_html"))
                if html is None:
                    parts = [
                        _cell(row, header_index.get("source")),
                        _cell(row, header_index.get("source_detail")),
                    ]
                    html_parts = [part for part in parts if part]
                    html = "<br/>".join(html_parts) if html_parts else None
            else:
                # Positional bedDetail-like (11 cols) or simplified tabular (9 cols)
                # formats. Optional OMIM/DECIPHER/description columns may follow.
                if _is_interval_header_row(row) or len(row) < 4:
                    continue
                chrom, start, end, name = row[:4]
                try:
                    start_i = int(start)
                    end_i = int(end)
                except ValueError:
                    continue
                if len(row) >= 11:
                    cnv_type = name or None
                    label = row[9] or name
                    html = row[10] or None
                    detail_base = 11
                else:
                    source = row[4] if len(row) > 4 else None
                    source_detail = row[5] if len(row) > 5 else None
                    cnv_type = source or None
                    label = name
                    html_parts = [part for part in [source, source_detail] if part]
                    html = "<br/>".join(html_parts) if html_parts else None
                    detail_base = 9
                omim_id = _cell(row, detail_base)
                decipher_id = _cell(row, detail_base + 1)
                description = _cell(row, detail_base + 2)
                cytoband = source_id = omim_title = orpha_id = orpha_name = None

            rows.append(
                {
                    "assembly_id": assembly_id,
                    "chr": normalize_chromosome(chrom),
                    "start": start_i,
                    "end": end_i,
                    "type": cnv_type,
                    "label": label,
                    # Untrusted imported HTML rendered via dangerouslySetInnerHTML — strip
                    # to the safe allowlist at ingest so stored data is clean at rest.
                    "details_html": sanitize_reference_html(html),
                    "omim_id": omim_id,
                    "decipher_id": decipher_id,
                    "description": description,
                    "cytoband": cytoband,
                    "source_id": source_id,
                    "omim_title": omim_title,
                    "orpha_id": orpha_id,
                    "orpha_name": orpha_name,
                }
            )
        if not rows:
            raise HTTPException(status_code=400, detail="No valid clinical CNV rows found")
        await session.execute(
            text(
                """
                INSERT INTO clinical_cnvs (
                    assembly_id, chr, start, "end", type, label, details_html,
                    omim_id, decipher_id, description,
                    cytoband, source_id, omim_title, orpha_id, orpha_name
                )
                VALUES (
                    CAST(:assembly_id AS uuid),
                    :chr,
                    :start,
                    :end,
                    :type,
                    :label,
                    :details_html,
                    :omim_id,
                    :decipher_id,
                    :description,
                    :cytoband,
                    :source_id,
                    :omim_title,
                    :orpha_id,
                    :orpha_name
                )
                """
            ),
            rows,
        )
        inserted = len(rows)

    elif dataset_type == "dgv":
        header_index: dict[str, int] | None = None
        batch: list[dict[str, object]] = []
        for row in _reader_from_text(text_value):
            if not row:
                continue
            first = row[0].strip().lower()
            if header_index is None and first in {"variantaccession", "variant_accession"}:
                header_index = {name.strip().lower(): idx for idx, name in enumerate(row)}
                continue
            if first.startswith("#") or first.startswith("track"):
                continue
            parsed = parse_dgv_row(row, header_index=header_index, assembly_id=assembly_id)
            if parsed is None:
                continue
            batch.append(parsed)
            if len(batch) >= _DGV_INSERT_CHUNK:
                await insert_dgv_batch(session, batch)
                inserted += len(batch)
                batch = []
        if batch:
            await insert_dgv_batch(session, batch)
            inserted += len(batch)
        if inserted == 0:
            raise HTTPException(status_code=400, detail="No valid DGV variant rows found")

    else:
        rows = []
        for row in _reader_from_text(text_value):
            if not row or row[0].startswith("#") or row[0].startswith("track") or _is_interval_header_row(row):
                continue
            if len(row) < 4:
                continue

            chrom, start, end, label = row[:4]
            try:
                start_i = int(start)
                end_i = int(end)
            except ValueError:
                continue

            item_rgb = row[8] if len(row) > 8 else None
            normalized_label = (label or "").strip()
            source = row[4].strip() if len(row) > 4 and row[4].strip() not in {"", ".", "0"} else None

            # ClinGen recurrent-CNV BED encodes LCR/segmental duplication anchors
            # in black and recurrent CNV intervals in orange.
            if item_rgb is not None and item_rgb.strip():
                if not _is_black_rgb(item_rgb):
                    continue
            elif normalized_label:
                label_upper = normalized_label.upper()
                if not any(token in label_upper for token in ("LCR", "SEG", "DUP", "REP")):
                    continue

            if not normalized_label:
                normalized_label = "Segmental duplication"

            rows.append(
                {
                    "assembly_id": assembly_id,
                    "chr": normalize_chromosome(chrom),
                    "start": start_i,
                    "end": end_i,
                    "label": normalized_label,
                    "source": source,
                }
            )

        if not rows:
            raise HTTPException(status_code=400, detail="No valid segmental duplication rows found")
        await session.execute(
            text(
                """
                INSERT INTO segmental_duplications (assembly_id, chr, start, "end", label, source)
                VALUES (CAST(:assembly_id AS uuid), :chr, :start, :end, :label, :source)
                """
            ),
            rows,
        )
        inserted = len(rows)

    await session.execute(
        text(
            """
            INSERT INTO reference_dataset_imports
                (assembly_id, dataset_type, inserted, replaced, source, performed_by)
            VALUES (CAST(:assembly_id AS uuid), :dataset_type, :inserted, :replaced, :source, :performed_by)
            """
        ),
        {
            "assembly_id": assembly_id,
            "dataset_type": dataset_type,
            "inserted": inserted,
            "replaced": replaced,
            "source": source,
            "performed_by": performed_by,
        },
    )
    if commit:
        await session.commit()
    return ReferenceUploadResult(
        assembly_id=assembly["id"],
        assembly_name=assembly["assembly_name"],
        dataset_type=dataset_type,
        inserted=inserted,
        replaced=replaced,
    )


_GENE_INSERT_SQL = text(
    """
    INSERT INTO genes (
        assembly_id, gene_id, hgnc_symbol, chr, start, "end",
        exons, strand, biotype, description, source, extra
    )
    VALUES (
        CAST(:assembly_id AS uuid), :gene_id, :hgnc_symbol, :chr, :start, :end,
        CAST(:exons AS jsonb), :strand, :biotype, :description, :source, CAST(:extra AS jsonb)
    )
    """
)

# GENCODE is ~350k transcripts; building one parameter list for all of them costs
# hundreds of megabytes before a single row reaches Postgres, so rows are streamed
# through in batches instead.
GENE_IMPORT_BATCH_SIZE = 5_000


async def apply_reference_gene_rows(
    session: AsyncSession,
    *,
    assembly_id: str,
    rows: Iterable[dict[str, Any]],
    overwrite: bool,
    commit: bool = True,
    performed_by: str | None = None,
    source: str | None = None,
) -> ReferenceUploadResult:
    """Import gene rows that are already structured, rather than via the 12-column text.

    The text import exists for hand-uploaded UCSC-style exports and flattens everything
    it does not have a column for — it hardcodes ``biotype`` to ``unknown`` and the
    source to ``refgene``. GENCODE carries real biotypes, Ensembl and HGNC identifiers
    and MANE tags, so it is imported as rows and keeps them.
    """
    assembly = await _get_assembly_by_id(session, assembly_id)
    existing_count = await _assembly_dataset_count(
        session, assembly_id=assembly_id, dataset_type="genes"
    )
    replaced = existing_count > 0
    if replaced and not overwrite:
        raise HTTPException(status_code=409, detail="genes already exist for this assembly")
    if replaced:
        await session.execute(
            text("DELETE FROM genes WHERE assembly_id = CAST(:assembly_id AS uuid)"),
            {"assembly_id": assembly_id},
        )

    inserted = 0
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= GENE_IMPORT_BATCH_SIZE:
            await session.execute(_GENE_INSERT_SQL, batch)
            inserted += len(batch)
            batch = []
    if batch:
        await session.execute(_GENE_INSERT_SQL, batch)
        inserted += len(batch)

    if not inserted:
        raise HTTPException(status_code=400, detail="No valid gene rows found")

    await session.execute(
        text(
            """
            INSERT INTO reference_dataset_imports
                (assembly_id, dataset_type, inserted, replaced, source, performed_by)
            VALUES (CAST(:assembly_id AS uuid), 'genes', :inserted, :replaced, :source, :performed_by)
            """
        ),
        {
            "assembly_id": assembly_id,
            "inserted": inserted,
            "replaced": replaced,
            "source": source,
            "performed_by": performed_by,
        },
    )
    if commit:
        await session.commit()
    return ReferenceUploadResult(
        assembly_id=assembly["id"],
        assembly_name=assembly["assembly_name"],
        dataset_type="genes",
        inserted=inserted,
        replaced=replaced,
    )


def _require_region_window(start: int, end: int) -> None:
    """Region queries must be bounded by a genomic window; a missing window
    (start >= end) would otherwise materialize a whole chromosome's rows."""
    if end <= start:
        raise HTTPException(
            status_code=400,
            detail="A genomic window with start < end is required for region queries.",
        )


async def get_gene_region_records(
    session: AsyncSession,
    *,
    assembly: str,
    chrom: str,
    start: int,
    end: int,
) -> list[GeneOut]:
    _require_region_window(start, end)
    assembly_row = await _get_assembly_by_name(session, assembly)
    stmt = text(
        """
        SELECT
            g.id::text AS id,
            g.gene_id,
            g.hgnc_symbol,
            g.chr,
            g.start,
            g."end",
            g.exons,
            g.strand,
            g.extra,
            gi.extra AS gene_info_extra
        FROM genes g
        LEFT JOIN gene_info gi
          ON gi.assembly_id = g.assembly_id
         AND upper(gi.hgnc_symbol) = upper(g.hgnc_symbol)
        WHERE g.assembly_id = CAST(:assembly_id AS uuid)
          AND g.chr IN :chromosomes
          AND (:apply_window = false OR (g.start < :end AND g."end" > :start))
        ORDER BY g.start, g."end", g.hgnc_symbol
        """
    ).bindparams(bindparam("chromosomes", expanding=True))
    result = await session.execute(
        stmt,
        {
            "assembly_id": assembly_row["id"],
            "chromosomes": chromosome_aliases(chrom),
            "apply_window": end > start,
            "start": start,
            "end": end,
        },
    )
    rows = [dict(row) for row in result.mappings().all()]
    preferred_rows = _select_preferred_gene_rows(rows)
    return [
        GeneOut(
            _id=row["id"],
            gene_id=row["gene_id"],
            hgnc_symbol=row["hgnc_symbol"],
            chr=row["chr"],
            start=int(row["start"]),
            end=int(row["end"]),
            exons=row.get("exons") or [],
            strand=int(row["strand"]),
        )
        for row in preferred_rows
    ]


async def get_blacklist_regions_data(
    session: AsyncSession,
    *,
    assembly: str,
    chrom: str,
    start: int,
    end: int,
) -> list[BlacklistRegionOut]:
    _require_region_window(start, end)
    assembly_row = await _get_assembly_by_name(session, assembly)
    stmt = text(
        """
        SELECT id::text AS id, chr, start, "end", label
        FROM blacklist
        WHERE assembly_id = CAST(:assembly_id AS uuid)
          AND chr IN :chromosomes
          AND (:apply_window = false OR (start < :end AND "end" > :start))
        ORDER BY start, "end", label
        """
    ).bindparams(bindparam("chromosomes", expanding=True))
    result = await session.execute(
        stmt,
        {
            "assembly_id": assembly_row["id"],
            "chromosomes": chromosome_aliases(chrom),
            "apply_window": end > start,
            "start": start,
            "end": end,
        },
    )
    return [
        BlacklistRegionOut(
            _id=row["id"],
            chr=row["chr"],
            start=int(row["start"]),
            end=int(row["end"]),
            label=row["label"],
        )
        for row in result.mappings().all()
    ]


async def get_segmental_duplications_data(
    session: AsyncSession,
    *,
    assembly: str,
    chrom: str,
    start: int,
    end: int,
) -> list[SegmentalDuplicationOut]:
    _require_region_window(start, end)
    assembly_row = await _get_assembly_by_name(session, assembly)
    stmt = text(
        """
        SELECT id::text AS id, chr, start, "end", label, source
        FROM segmental_duplications
        WHERE assembly_id = CAST(:assembly_id AS uuid)
          AND chr IN :chromosomes
          AND (:apply_window = false OR (start < :end AND "end" > :start))
        ORDER BY start, "end", label
        """
    ).bindparams(bindparam("chromosomes", expanding=True))
    result = await session.execute(
        stmt,
        {
            "assembly_id": assembly_row["id"],
            "chromosomes": chromosome_aliases(chrom),
            "apply_window": end > start,
            "start": start,
            "end": end,
        },
    )
    return [
        SegmentalDuplicationOut(
            _id=row["id"],
            chr=row["chr"],
            start=int(row["start"]),
            end=int(row["end"]),
            label=row["label"],
            source=row.get("source"),
        )
        for row in result.mappings().all()
    ]


_DGV_LINE_CAP = 1500
# Target bin count in density mode. bin_size = span // bin_count, so a higher
# target means smaller (finer) bins at every resolution. 400 halves the bin size
# relative to the previous 200, showing roughly twice as many bins per view.
_DGV_DENSITY_BINS = 400
_DGV_CLASSES = ("gain", "loss", "mixed", "other")


async def get_dgv_track_data(
    session: AsyncSession,
    *,
    assembly: str,
    chrom: str,
    start: int,
    end: int,
    line_cap: int = _DGV_LINE_CAP,
    bins: int = _DGV_DENSITY_BINS,
) -> DgvTrackOut:
    """DGV track payload for the Chromosome View. Returns individual variants
    when the in-view count is small enough to draw as stacked lines, otherwise a
    per-bin gain/loss/mixed density profile for a zoomed-out heat strip."""
    assembly_row = await _get_assembly_by_name(session, assembly)
    apply_window = end > start
    region_clause = (
        'assembly_id = CAST(:assembly_id AS uuid) '
        'AND chr IN :chromosomes '
        'AND (:apply_window = false OR (start < :end AND "end" > :start))'
    )
    common_params: dict[str, Any] = {
        "assembly_id": assembly_row["id"],
        "chromosomes": chromosome_aliases(chrom),
        "apply_window": apply_window,
        "start": start,
        "end": end,
    }

    count_stmt = text(f"SELECT COUNT(*) FROM dgv_variants WHERE {region_clause}").bindparams(
        bindparam("chromosomes", expanding=True)
    )
    total = int((await session.execute(count_stmt, common_params)).scalar_one() or 0)
    if total == 0:
        return DgvTrackOut(total=0, mode="lines")

    if total <= line_cap:
        detail_stmt = text(
            f"""
            SELECT chr, start, "end", accession, variant_type, variant_subtype,
                   variant_class, frequency, observed_gains, observed_losses, source
            FROM dgv_variants
            WHERE {region_clause}
            ORDER BY start, "end"
            LIMIT :limit
            """
        ).bindparams(bindparam("chromosomes", expanding=True))
        result = await session.execute(detail_stmt, {**common_params, "limit": line_cap})
        variants = [
            DgvVariantOut(
                chr=row["chr"],
                start=int(row["start"]),
                end=int(row["end"]),
                accession=row.get("accession"),
                variant_type=row.get("variant_type"),
                variant_subtype=row.get("variant_subtype"),
                variant_class=row.get("variant_class") or "other",
                frequency=row.get("frequency"),
                observed_gains=row.get("observed_gains"),
                observed_losses=row.get("observed_losses"),
                source=row.get("source"),
            )
            for row in result.mappings().all()
        ]
        return DgvTrackOut(total=total, mode="lines", variants=variants)

    # Density mode: bucket clamped start positions into `bins` and count per class.
    span = end - start
    if not apply_window or span <= 0:
        return DgvTrackOut(total=total, mode="density")
    bin_count = max(1, min(bins, span))
    bin_size = max(span // bin_count, 1)
    density_stmt = text(
        f"""
        SELECT
            LEAST(
                GREATEST(CAST((GREATEST(start, :start) - :start) / :bin_size AS int), 0),
                :max_bin
            ) AS bin,
            variant_class,
            COUNT(*) AS n
        FROM dgv_variants
        WHERE {region_clause}
        GROUP BY 1, variant_class
        """
    ).bindparams(bindparam("chromosomes", expanding=True))
    rows = (
        await session.execute(
            density_stmt,
            {**common_params, "bin_size": bin_size, "max_bin": bin_count - 1},
        )
    ).mappings().all()

    buckets = [dict.fromkeys(_DGV_CLASSES, 0) for _ in range(bin_count)]
    for row in rows:
        klass = row["variant_class"] if row["variant_class"] in _DGV_CLASSES else "other"
        buckets[int(row["bin"])][klass] += int(row["n"])
    density = [
        DgvDensityBin(
            start=start + i * bin_size,
            end=start + (i + 1) * bin_size,
            gain=b["gain"],
            loss=b["loss"],
            mixed=b["mixed"],
            other=b["other"],
        )
        for i, b in enumerate(buckets)
    ]
    return DgvTrackOut(total=total, mode="density", bins=density, bin_size=bin_size)


_CLINICAL_CNV_COLUMNS = (
    'id::text AS id, chr, start, "end", type, label, details_html, '
    "omim_id, omim_title, decipher_id, description, "
    "cytoband, source_id, orpha_id, orpha_name"
)


def _clinical_cnv_out(row: Mapping[str, Any], *, assembly: str | None) -> ClinicalCnvOut:
    return ClinicalCnvOut(
        _id=row["id"],
        chr=row["chr"],
        start=int(row["start"]),
        end=int(row["end"]),
        type=row.get("type"),
        label=row["label"],
        # Sanitise again on the way out so legacy rows stored before ingest sanitisation
        # (or any bypassed write path) can never serve unsafe HTML to the render sink.
        details_html=sanitize_reference_html(row.get("details_html")),
        assembly=assembly,
        omim_id=row.get("omim_id"),
        omim_title=row.get("omim_title"),
        decipher_id=row.get("decipher_id"),
        description=row.get("description"),
        cytoband=row.get("cytoband"),
        source_id=row.get("source_id"),
        orpha_id=row.get("orpha_id"),
        orpha_name=row.get("orpha_name"),
    )


async def get_clinical_cnvs_data(
    session: AsyncSession,
    *,
    assembly: str,
    chrom: str,
    start: int,
    end: int,
) -> list[ClinicalCnvOut]:
    assembly_row = await _get_assembly_by_name(session, assembly)
    stmt = text(
        f"""
        SELECT {_CLINICAL_CNV_COLUMNS}
        FROM clinical_cnvs
        WHERE assembly_id = CAST(:assembly_id AS uuid)
          AND chr IN :chromosomes
          AND (:apply_window = false OR (start < :end AND "end" > :start))
        ORDER BY start, "end", label
        """
    ).bindparams(bindparam("chromosomes", expanding=True))
    result = await session.execute(
        stmt,
        {
            "assembly_id": assembly_row["id"],
            "chromosomes": chromosome_aliases(chrom),
            "apply_window": end > start,
            "start": start,
            "end": end,
        },
    )
    return [
        _clinical_cnv_out(row, assembly=assembly_row["assembly_name"])
        for row in result.mappings().all()
    ]


async def list_clinical_cnvs_catalog_data(
    session: AsyncSession,
    *,
    assembly: str,
    search: str | None = None,
    limit: int = 500,
) -> list[ClinicalCnvOut]:
    """Region-independent clinical CNV catalog for the explorer view."""
    assembly_row = await _get_assembly_by_name(session, assembly)
    params: dict[str, Any] = {"assembly_id": assembly_row["id"]}
    where = ["assembly_id = CAST(:assembly_id AS uuid)"]
    cleaned = (search or "").strip()
    if cleaned:
        params["search"] = f"%{cleaned.lower()}%"
        where.append(
            "(lower(label) LIKE :search OR lower(chr) LIKE :search "
            "OR lower(COALESCE(type, '')) LIKE :search)"
        )
    params["limit"] = max(1, min(limit, 2000))
    stmt = text(
        f"""
        SELECT {_CLINICAL_CNV_COLUMNS}
        FROM clinical_cnvs
        WHERE {' AND '.join(where)}
        ORDER BY chr, start, "end", label
        LIMIT :limit
        """
    )
    result = await session.execute(stmt, params)
    return [
        _clinical_cnv_out(row, assembly=assembly_row["assembly_name"])
        for row in result.mappings().all()
    ]


async def get_clinical_cnv_by_id_data(
    session: AsyncSession,
    *,
    cnv_id: str,
) -> ClinicalCnvOut:
    _require_uuid(cnv_id, "Invalid clinical CNV id")
    result = await session.execute(
        text(
            f"""
            SELECT {_CLINICAL_CNV_COLUMNS}, assembly_id::text AS assembly_id
            FROM clinical_cnvs
            WHERE id = CAST(:cnv_id AS uuid)
            """
        ),
        {"cnv_id": cnv_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Clinical CNV not found")
    assembly_row = await _get_assembly_by_id(session, row["assembly_id"])
    return _clinical_cnv_out(row, assembly=assembly_row["assembly_name"])


async def list_chromosome_sizes_data(
    session: AsyncSession,
    *,
    assembly: str,
    chroms: list[str] | None = None,
) -> list[ChromosomeSizeOut]:
    assembly_row = await _get_assembly_by_name(session, assembly)
    normalized_chroms = chroms or []
    stmt = text(
        """
        SELECT chr, size
        FROM chromosomes
        WHERE assembly_id = CAST(:assembly_id AS uuid)
          AND (:apply_filter = false OR chr IN :chromosomes)
        ORDER BY chr
        """
    ).bindparams(bindparam("chromosomes", expanding=True))
    result = await session.execute(
        stmt,
        {
            "assembly_id": assembly_row["id"],
            "apply_filter": bool(normalized_chroms),
            "chromosomes": list(
                dict.fromkeys(
                    alias
                    for chrom in normalized_chroms
                    for alias in chromosome_aliases(chrom)
                )
            ) or [""],
        },
    )
    return [
        ChromosomeSizeOut(chr=row["chr"], size=int(row["size"]))
        for row in result.mappings().all()
    ]


async def list_chromosome_details_data(
    session: AsyncSession,
    *,
    assembly: str,
    chroms: list[str] | None = None,
) -> list[ChromosomeOut]:
    assembly_row = await _get_assembly_by_name(session, assembly)
    normalized_chroms = chroms or []
    stmt = text(
        """
        SELECT id::text AS id, assembly_id::text AS assembly_id, chr, size, bands
        FROM chromosomes
        WHERE assembly_id = CAST(:assembly_id AS uuid)
          AND (:apply_filter = false OR chr IN :chromosomes)
        ORDER BY chr
        """
    ).bindparams(bindparam("chromosomes", expanding=True))
    result = await session.execute(
        stmt,
        {
            "assembly_id": assembly_row["id"],
            "apply_filter": bool(normalized_chroms),
            "chromosomes": list(
                dict.fromkeys(
                    alias
                    for chrom in normalized_chroms
                    for alias in chromosome_aliases(chrom)
                )
            ) or [""],
        },
    )
    return [
        ChromosomeOut(
            _id=row["id"],
            assembly_id=row["assembly_id"],
            chr=row["chr"],
            size=int(row["size"]),
            bands=row.get("bands") or [],
        )
        for row in result.mappings().all()
    ]


async def get_chromosome_data(
    session: AsyncSession,
    *,
    assembly: str,
    chrom: str,
) -> ChromosomeOut:
    assembly_row = await _get_assembly_by_name(session, assembly)
    stmt = text(
        """
        SELECT id::text AS id, assembly_id::text AS assembly_id, chr, size, bands
        FROM chromosomes
        WHERE assembly_id = CAST(:assembly_id AS uuid)
          AND chr IN :chromosomes
        LIMIT 1
        """
    ).bindparams(bindparam("chromosomes", expanding=True))
    result = await session.execute(
        stmt,
        {
            "assembly_id": assembly_row["id"],
            "chromosomes": chromosome_aliases(chrom),
        },
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Chromosome not found")
    return ChromosomeOut(
        _id=row["id"],
        assembly_id=row["assembly_id"],
        chr=row["chr"],
        size=int(row["size"]),
        bands=row.get("bands") or [],
    )
