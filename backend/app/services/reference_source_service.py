from __future__ import annotations

import asyncio
import csv
import gzip
import io
import itertools
import logging
import re
import time
from collections import defaultdict
from datetime import date
from typing import Any, NoReturn

import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..schemas import (
    ReferenceAutoImportResult,
    ReferenceImportSourceAssemblyOut,
    ReferenceImportSourceOrganismOut,
)
from .bounded_download import download_bounded_bytes, gunzip_bounded
from .gencode_import import (
    GencodeRelease,
    iter_gencode_gene_rows,
    parse_gencode_refseq_metadata,
    parse_gencode_release,
)
from .reference_metadata_service import (
    _assembly_dataset_count,
    apply_reference_dataset_text,
    apply_reference_gene_rows,
)

UCSC_API_ROOT = "https://api.genome.ucsc.edu"
UCSC_DOWNLOAD_ROOT = "https://hgdownload.soe.ucsc.edu/goldenPath"
HUMAN_GRCH38_TAX_ID = 9606
HUMAN_GRCH38_SCIENTIFIC_NAME = "Homo sapiens"
HUMAN_GRCH38_COMMON_NAME = "human"
HUMAN_GRCH38_UCSC_GENOME = "hg38"
# T2T-CHM13v2.0. UCSC calls it hs1; it is a separate assembly, not a GRCh38 patch.
HUMAN_T2T_UCSC_GENOME = "hs1"
HUMAN_GRCH38_ASSEMBLY_NAME = "GRCh38"
HUMAN_GRCH38_FALLBACK_VERSION = "hg38"
HUMAN_GRCH38_RELEASE_DATE = date(2013, 12, 1)
# 0001-01-01 sentinel meaning "upstream release date unknown/unparseable". Keeps
# assemblies.release_date (DATE NOT NULL) honest instead of stamping today(), and
# matches the sort sentinel used in the catalog ordering below so an undated assembly
# sorts oldest rather than masquerading as the newest.
_UNKNOWN_RELEASE_DATE = date.min
_CATALOG_TTL_SECONDS = 60 * 60
_catalog_lock = asyncio.Lock()
_catalog_cached_at = 0.0
_catalog_entries: list[dict[str, Any]] | None = None
logger = logging.getLogger(__name__)

_MONTH_BY_PREFIX = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _raise_upstream_error(source: str, exc: Exception) -> NoReturn:
    raise HTTPException(
        status_code=502,
        detail=f"Failed to download reference data from {source}",
    ) from exc


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        _raise_upstream_error(url, exc)
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail=f"Unexpected response from {url}")
    return payload


async def _get_optional_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        _raise_upstream_error(url, exc)
    if response.status_code == 404:
        return None
    try:
        response.raise_for_status()
    except httpx.HTTPError as exc:
        _raise_upstream_error(url, exc)
    return response.text


async def _get_optional_gzip_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        raw = await download_bounded_bytes(client, url, source=url, none_on_404=True)
    except httpx.HTTPError as exc:
        _raise_upstream_error(url, exc)
    if raw is None:
        return None
    try:
        return gunzip_bounded(raw, source=url).decode()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"Unexpected compressed response from {url}") from exc


def _parse_release_date(description: str) -> date | None:
    match = re.match(r"([A-Za-z]+)\.\s+(\d{4})", description.strip())
    if match is None:
        return None
    month = _MONTH_BY_PREFIX.get(match.group(1).lower())
    if month is None:
        return None
    return date(int(match.group(2)), month, 1)


def _parse_assembly_name(description: str, ucsc_genome: str) -> str:
    match = re.search(r"\(([^()/]+)/([^)]+)\)", description)
    if match is not None:
        return match.group(1).strip()
    return ucsc_genome


def _split_local_assembly_identity(
    assembly_name: str,
    alias: str | None,
    ucsc_genome: str,
) -> tuple[str, str]:
    normalized_name = assembly_name.strip() or ucsc_genome
    normalized_alias = (alias or "").strip()
    if normalized_alias and (
        normalized_alias.startswith(f"{normalized_name}.")
        or normalized_alias.startswith(f"{normalized_name}_")
    ):
        # +1 skips the single-char separator ("." or "_").
        suffix = normalized_alias[len(normalized_name) + 1 :].strip()
        if suffix:
            return normalized_name, suffix
    if normalized_alias and normalized_alias != normalized_name:
        return normalized_name, normalized_alias
    return normalized_name, ucsc_genome


def _parse_sql_columns(sql_text: str) -> list[str]:
    columns: list[str] = []
    in_create_table = False
    for raw_line in sql_text.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line:
            continue
        if line.startswith("CREATE TABLE"):
            in_create_table = True
            continue
        if not in_create_table:
            continue
        if line.startswith(")") or line.startswith("PRIMARY KEY"):
            break
        if line.startswith("KEY") or line.startswith("UNIQUE KEY"):
            continue
        match = re.match(r"`([^`]+)`", line)
        if match is not None:
            columns.append(match.group(1))
            continue
    if not columns:
        raise HTTPException(status_code=502, detail="Unable to parse UCSC table schema")
    return columns


def _pick_column(columns: list[str], *candidates: str) -> str | None:
    column_set = set(columns)
    for candidate in candidates:
        if candidate in column_set:
            return candidate
    return None


def _build_gene_import_text(
    *,
    track: str,
    sql_text: str,
    data_text: str,
) -> str:
    columns = _parse_sql_columns(sql_text)
    chrom_key = _pick_column(columns, "chrom", "tName")
    start_key = _pick_column(columns, "txStart", "chromStart", "tStart")
    end_key = _pick_column(columns, "txEnd", "chromEnd", "tEnd")
    strand_key = _pick_column(columns, "strand")
    exon_starts_key = _pick_column(columns, "exonStarts", "blockStarts")
    exon_ends_key = _pick_column(columns, "exonEnds", "blockEnds")
    gene_id_key = _pick_column(columns, "name", "mrnaAcc", "kgID", "transcript", "gene_id")
    gene_symbol_key = _pick_column(columns, "name2", "geneName", "gene", "gene_symbol", "symbol")
    if None in (
        chrom_key,
        start_key,
        end_key,
        strand_key,
        exon_starts_key,
        exon_ends_key,
        gene_id_key,
        gene_symbol_key,
    ):
        raise HTTPException(status_code=502, detail=f"Unsupported UCSC gene schema for track {track}")

    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    reader = csv.reader(io.StringIO(data_text), delimiter="\t")

    for row in reader:
        if not row or row[0].startswith("#"):
            continue
        if len(row) < len(columns):
            continue
        values = dict(zip(columns, row))
        chrom = str(values.get(chrom_key, "")).strip()
        gene_id = str(values.get(gene_id_key, "")).strip()
        gene_symbol = str(values.get(gene_symbol_key, "")).strip() or gene_id
        strand = str(values.get(strand_key, "")).strip() or "+"
        if not chrom or not gene_id or not gene_symbol:
            continue
        try:
            start = int(str(values.get(start_key, "")).strip())
            end = int(str(values.get(end_key, "")).strip())
        except ValueError:
            continue

        exon_starts = [value for value in str(values.get(exon_starts_key, "")).strip().rstrip(",").split(",") if value]
        exon_ends = [value for value in str(values.get(exon_ends_key, "")).strip().rstrip(",").split(",") if value]
        exon_pairs: list[tuple[int, int]] = []
        for exon_start, exon_end in zip(exon_starts, exon_ends):
            try:
                exon_pairs.append((int(exon_start), int(exon_end)))
            except ValueError:
                continue
        if not exon_pairs:
            exon_pairs = [(start, end)]

        exon_pairs.sort()
        exon_intervals = ",".join(f"{exon_start}-{exon_end}" for exon_start, exon_end in exon_pairs)
        intron_pairs = [
            (left_end, right_start)
            for (_, left_end), (right_start, _) in zip(exon_pairs, exon_pairs[1:])
            if right_start > left_end
        ]
        intron_intervals = ",".join(f"{intron_start}-{intron_end}" for intron_start, intron_end in intron_pairs)
        writer.writerow(
            [
                chrom,
                start,
                end,
                gene_symbol,
                "",
                strand,
                "",
                gene_id,
                len(exon_pairs),
                exon_intervals,
                len(intron_pairs),
                intron_intervals,
            ]
        )

    text_value = output.getvalue()
    if not text_value.strip():
        raise HTTPException(status_code=502, detail=f"No gene rows were parsed from UCSC track {track}")
    return text_value


def _build_single_band_cytobands_text(chromosome_sizes: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")

    for chrom, raw_size in chromosome_sizes.items():
        chrom_name = str(chrom or "").strip()
        if not chrom_name:
            continue
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        writer.writerow([chrom_name, 0, size, chrom_name, "gneg"])

    text_value = output.getvalue()
    if not text_value.strip():
        raise HTTPException(status_code=502, detail="No chromosome sizes were parsed from UCSC")
    return text_value


async def _load_catalog_entries() -> list[dict[str, Any]]:
    global _catalog_cached_at, _catalog_entries

    async with _catalog_lock:
        now = time.time()
        if _catalog_entries is not None and now - _catalog_cached_at < _CATALOG_TTL_SECONDS:
            return _catalog_entries

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            payload = await _get_json(client, f"{UCSC_API_ROOT}/list/ucscGenomes")

        raw_entries = payload.get("ucscGenomes")
        if not isinstance(raw_entries, dict):
            raise HTTPException(status_code=502, detail="Unexpected UCSC genome catalog payload")

        parsed_entries: list[dict[str, Any]] = []
        for ucsc_genome, raw_entry in raw_entries.items():
            if not isinstance(raw_entry, dict):
                continue
            tax_id = raw_entry.get("taxId")
            scientific_name = str(raw_entry.get("scientificName") or "").strip()
            common_name = str(raw_entry.get("organism") or raw_entry.get("genome") or "").strip()
            if not scientific_name or not isinstance(tax_id, int):
                continue
            description = str(raw_entry.get("description") or "").strip()
            assembly_name = _parse_assembly_name(description, ucsc_genome)
            parsed_entries.append(
                {
                    "ucsc_genome": str(ucsc_genome),
                    "tax_id": tax_id,
                    "scientific_name": scientific_name,
                    "common_name": common_name,
                    "description": description,
                    "source_name": str(raw_entry.get("sourceName") or "").strip(),
                    "release_date": _parse_release_date(description),
                    "assembly_name": assembly_name,
                }
            )

        parsed_entries.sort(
            key=lambda entry: (
                str(entry["scientific_name"]).lower(),
                str(entry["assembly_name"]).lower(),
                entry["release_date"] or _UNKNOWN_RELEASE_DATE,
            )
        )
        _catalog_entries = parsed_entries
        _catalog_cached_at = now
        return parsed_entries


async def list_reference_source_organisms() -> list[ReferenceImportSourceOrganismOut]:
    entries = await _load_catalog_entries()
    grouped: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "scientific_name": "",
            "common_name": "",
            "assembly_count": 0,
        }
    )
    for entry in entries:
        group = grouped[int(entry["tax_id"])]
        if not group["scientific_name"]:
            group["scientific_name"] = entry["scientific_name"]
        if not group["common_name"] and entry["common_name"]:
            group["common_name"] = entry["common_name"]
        group["assembly_count"] += 1

    return [
        ReferenceImportSourceOrganismOut(
            scientific_name=str(group["scientific_name"]),
            common_name=str(group["common_name"]),
            tax_id=tax_id,
            assembly_count=int(group["assembly_count"]),
        )
        for tax_id, group in sorted(grouped.items(), key=lambda item: str(item[1]["scientific_name"]).lower())
    ]


async def _resolve_find_genome_record(
    client: httpx.AsyncClient,
    *,
    ucsc_genome: str,
) -> dict[str, Any]:
    payload = await _get_json(
        client,
        f"{UCSC_API_ROOT}/findGenome",
        params={"q": ucsc_genome, "browser": "mustExist"},
    )
    candidate_items = [
        (key, value)
        for key, value in payload.items()
        if key not in {"downloadTime", "downloadTimeStamp", "browser", "liftable", "q", "itemCount", "totalMatchCount", "availableAssemblies"}
        and isinstance(value, dict)
    ]
    if not candidate_items:
        raise HTTPException(status_code=404, detail=f"Assembly {ucsc_genome} was not found in UCSC")
    exact = next((value for key, value in candidate_items if key == ucsc_genome), None)
    return exact or candidate_items[0][1]


async def list_reference_source_assemblies(*, tax_id: int) -> list[ReferenceImportSourceAssemblyOut]:
    entries = [entry for entry in await _load_catalog_entries() if int(entry["tax_id"]) == tax_id]
    if not entries:
        return []

    results: list[ReferenceImportSourceAssemblyOut] = []
    for entry in entries:
        assembly_name, assembly_version = _split_local_assembly_identity(
            str(entry["assembly_name"]),
            None,
            str(entry["ucsc_genome"]),
        )
        results.append(
            ReferenceImportSourceAssemblyOut(
                scientific_name=str(entry["scientific_name"]),
                common_name=str(entry["common_name"]),
                tax_id=int(entry["tax_id"]),
                ucsc_genome=str(entry["ucsc_genome"]),
                assembly_name=assembly_name,
                assembly_version=assembly_version,
                release_date=entry["release_date"],
                description=str(entry["description"]),
                source_name=str(entry["source_name"]),
                gene_source="UCSC gene tables",
            )
        )

    results.sort(
        key=lambda entry: (
            entry.release_date or _UNKNOWN_RELEASE_DATE,
            entry.assembly_name.lower(),
            entry.assembly_version.lower(),
        ),
        reverse=True,
    )
    return results


# UCSC genome/db identifiers are short alphanumerics (hg38, mm39, GCF_000001405.40).
# The value can originate from an admin-supplied assembly name, so constrain it before
# interpolating into a download URL: no path- or URL-significant character can enter the
# request path, closing a partial-SSRF (request-forgery) vector on the fixed UCSC host.
_UCSC_GENOME_RE = re.compile(r"[A-Za-z0-9._-]+")


def _safe_ucsc_genome(ucsc_genome: str) -> str:
    if not _UCSC_GENOME_RE.fullmatch(ucsc_genome):
        raise HTTPException(status_code=400, detail=f"Invalid UCSC genome identifier: {ucsc_genome!r}")
    return ucsc_genome


async def _download_cytobands(
    client: httpx.AsyncClient,
    *,
    ucsc_genome: str,
) -> tuple[str, str]:
    ucsc_genome = _safe_ucsc_genome(ucsc_genome)
    for table_name in ("cytoBandIdeo", "cytoBand"):
        source_url = f"{UCSC_DOWNLOAD_ROOT}/{ucsc_genome}/database/{table_name}.txt.gz"
        text_value = await _get_optional_gzip_text(client, source_url)
        if text_value:
            return text_value, source_url

    chromosome_sizes_payload = await _get_json(
        client,
        f"{UCSC_API_ROOT}/list/chromosomes",
        params={"genome": ucsc_genome},
    )
    chromosome_sizes = chromosome_sizes_payload.get("chromosomes")
    if not isinstance(chromosome_sizes, dict):
        raise HTTPException(status_code=502, detail=f"Unexpected UCSC chromosome-size payload for {ucsc_genome}")
    return (
        _build_single_band_cytobands_text(chromosome_sizes),
        f"{UCSC_API_ROOT}/list/chromosomes?genome={ucsc_genome}",
    )


def _iter_gencode_rows_from_gzip(
    raw: bytes,
    *,
    assembly_id: str,
    refseq_by_transcript: dict[str, list[str]],
    source: str = "gencode",
):
    """Stream GTF rows straight out of the compressed bytes.

    The annotation is ~350k transcripts; decompressing it to one string, or collecting
    the rows into a list, costs around a gigabyte before anything reaches Postgres.
    Decompression is incremental and the caller batches, so neither ever happens.
    """
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        stream = io.TextIOWrapper(gz, encoding="utf-8", errors="replace")
        yield from iter_gencode_gene_rows(
            stream,
            assembly_id=assembly_id,
            refseq_by_transcript=refseq_by_transcript,
            source=source,
        )


def _gencode_release_from_gzip(raw: bytes) -> GencodeRelease:
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        stream = io.TextIOWrapper(gz, encoding="utf-8", errors="replace")
        return parse_gencode_release(itertools.islice(stream, 20))


async def _download_gencode_genes(
    client: httpx.AsyncClient,
    *,
    assembly_id: str,
    ucsc_genome: str,
) -> tuple[Any, str, str] | None:
    """Fetch gene rows from a GTF, or ``None`` to let the caller fall back to UCSC.

    GRCh38 gets GENCODE. T2T-CHM13 gets UCSC's RefSeq-derived GTF, because GENCODE
    publishes no CHM13 annotation and UCSC serves none of the gene tables the
    refGene-era importer looked for — that GTF carries coordinates but no biotypes,
    Ensembl identifiers or MANE tags, which is why its rows are labelled by their real
    source rather than as GENCODE.

    A reference bootstrap must not be blocked by an unreachable annotation, so every
    other genome and every failure returns ``None`` rather than raising.
    """
    if ucsc_genome == HUMAN_GRCH38_UCSC_GENOME:
        url = str(settings.reference_gencode_gtf_url or "").strip()
        source_label, wants_refseq_metadata = "gencode", True
    elif ucsc_genome == HUMAN_T2T_UCSC_GENOME:
        url = str(settings.reference_t2t_gtf_url or "").strip()
        source_label, wants_refseq_metadata = "ucsc ncbiRefSeq", False
    else:
        return None
    if not url:
        return None

    try:
        raw = await download_bounded_bytes(client, url, source=url)
        refseq_by_transcript = (
            await _download_gencode_refseq_metadata(client) if wants_refseq_metadata else {}
        )
        release = _gencode_release_from_gzip(raw)
        rows = _iter_gencode_rows_from_gzip(
            raw,
            assembly_id=assembly_id,
            refseq_by_transcript=refseq_by_transcript,
            source=source_label,
        )
        return rows, url, f"{source_label} {release.label}" if release.label else source_label
    except Exception as exc:  # pragma: no cover - network shape varies
        logger.warning("GTF gene import failed for %s (%s); falling back to UCSC", ucsc_genome, exc)
        return None


async def _download_gencode_refseq_metadata(client: httpx.AsyncClient) -> dict[str, list[str]]:
    """Best-effort: a missing RefSeq map costs accession lookups, not the whole import."""
    url = str(settings.reference_gencode_refseq_metadata_url or "").strip()
    if not url:
        return {}
    try:
        raw = await download_bounded_bytes(client, url, source=url)
        return parse_gencode_refseq_metadata(
            io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8", errors="replace")
        )
    except Exception:  # pragma: no cover - network shape varies
        logger.warning("GENCODE RefSeq metadata unavailable; accession lookups will be reduced")
        return {}


async def _download_genes(
    client: httpx.AsyncClient,
    *,
    ucsc_genome: str,
) -> tuple[str, str, str]:
    ucsc_genome = _safe_ucsc_genome(ucsc_genome)
    for track in ("ncbiRefSeqCurated", "ncbiRefSeq", "refGene", "ensGene"):
        base_url = f"{UCSC_DOWNLOAD_ROOT}/{ucsc_genome}/database/{track}"
        sql_text = await _get_optional_text(client, f"{base_url}.sql")
        data_text = await _get_optional_gzip_text(client, f"{base_url}.txt.gz")
        if sql_text is None or data_text is None:
            continue
        try:
            converted = _build_gene_import_text(track=track, sql_text=sql_text, data_text=data_text)
        except HTTPException as exc:
            if exc.status_code == 502 and (
                exc.detail.startswith("Unsupported UCSC gene schema")
                or exc.detail.startswith("No gene rows were parsed")
            ):
                continue
            raise
        return converted, f"{base_url}.txt.gz", track
    raise HTTPException(status_code=404, detail=f"No supported UCSC gene table was found for {ucsc_genome}")


async def _get_or_create_species(
    session: AsyncSession,
    *,
    scientific_name: str,
    common_name: str,
    tax_id: int,
) -> tuple[str, str, bool]:
    existing = await session.execute(
        text(
            """
            SELECT id::text AS id, name
            FROM species
            WHERE tax_id = :tax_id OR lower(name) = lower(:name)
            ORDER BY CASE WHEN tax_id = :tax_id THEN 0 ELSE 1 END
            LIMIT 1
            """
        ),
        {"tax_id": tax_id, "name": scientific_name},
    )
    row = existing.mappings().first()
    if row is not None:
        return str(row["id"]), str(row["name"]), False

    created = await session.execute(
        text(
            """
            INSERT INTO species (name, common_name, tax_id)
            VALUES (:name, :common_name, :tax_id)
            RETURNING id::text AS id, name
            """
        ),
        {
            "name": scientific_name,
            "common_name": common_name or scientific_name,
            "tax_id": tax_id,
        },
    )
    created_row = created.mappings().one()
    return str(created_row["id"]), str(created_row["name"]), True


async def _get_or_create_assembly(
    session: AsyncSession,
    *,
    species_id: str,
    assembly_name: str,
    assembly_version: str,
    release_date: date | None,
) -> tuple[str, bool]:
    existing = await session.execute(
        text(
            """
            SELECT id::text AS id
            FROM assemblies
            WHERE species_id = CAST(:species_id AS uuid)
              AND assembly_name = :assembly_name
              AND version = :version
            LIMIT 1
            """
        ),
        {
            "species_id": species_id,
            "assembly_name": assembly_name,
            "version": assembly_version,
        },
    )
    row = existing.mappings().first()
    if row is not None:
        return str(row["id"]), False

    created = await session.execute(
        text(
            """
            INSERT INTO assemblies (species_id, assembly_name, version, release_date)
            VALUES (
                CAST(:species_id AS uuid),
                :assembly_name,
                :version,
                :release_date
            )
            RETURNING id::text AS id
            """
        ),
        {
            "species_id": species_id,
            "assembly_name": assembly_name,
            "version": assembly_version,
            "release_date": release_date or _UNKNOWN_RELEASE_DATE,
        },
    )
    created_row = created.mappings().one()
    return str(created_row["id"]), True


async def _find_human_grch38_assembly(
    session: AsyncSession,
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT
                s.id::text AS species_id,
                s.name AS species_name,
                a.id::text AS assembly_id,
                a.assembly_name,
                a.version
            FROM species s
            JOIN assemblies a ON a.species_id = s.id
            WHERE (s.tax_id = :tax_id OR s.name = :scientific_name)
              AND (
                    a.assembly_name = :assembly_name
                 OR lower(a.version) = :ucsc_genome
                 OR lower(a.assembly_name) = :ucsc_genome
              )
            ORDER BY
                CASE WHEN a.assembly_name = :assembly_name THEN 0 ELSE 1 END,
                CASE WHEN lower(a.version) = :ucsc_genome THEN 0 ELSE 1 END,
                a.release_date DESC,
                a.version DESC
            LIMIT 1
            """
        ),
        {
            "tax_id": HUMAN_GRCH38_TAX_ID,
            "scientific_name": HUMAN_GRCH38_SCIENTIFIC_NAME,
            "assembly_name": HUMAN_GRCH38_ASSEMBLY_NAME,
            "ucsc_genome": HUMAN_GRCH38_UCSC_GENOME,
        },
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def ensure_human_grch38_species_assembly(
    session: AsyncSession,
) -> dict[str, Any]:
    existing = await _find_human_grch38_assembly(session)
    if existing is not None:
        return {
            **existing,
            "created_species": False,
            "created_assembly": False,
        }

    species_id, species_name, created_species = await _get_or_create_species(
        session,
        scientific_name=HUMAN_GRCH38_SCIENTIFIC_NAME,
        common_name=HUMAN_GRCH38_COMMON_NAME,
        tax_id=HUMAN_GRCH38_TAX_ID,
    )
    assembly_id, created_assembly = await _get_or_create_assembly(
        session,
        species_id=species_id,
        assembly_name=HUMAN_GRCH38_ASSEMBLY_NAME,
        assembly_version=HUMAN_GRCH38_FALLBACK_VERSION,
        release_date=HUMAN_GRCH38_RELEASE_DATE,
    )
    return {
        "species_id": species_id,
        "species_name": species_name,
        "assembly_id": assembly_id,
        "assembly_name": HUMAN_GRCH38_ASSEMBLY_NAME,
        "version": HUMAN_GRCH38_FALLBACK_VERSION,
        "created_species": created_species,
        "created_assembly": created_assembly,
    }


async def import_reference_from_ucsc(
    session: AsyncSession,
    *,
    tax_id: int,
    ucsc_genome: str,
    overwrite: bool,
    missing_only: bool = False,
    performed_by: str | None = None,
) -> ReferenceAutoImportResult:
    source_entries = await list_reference_source_assemblies(tax_id=tax_id)
    source_assembly = next((entry for entry in source_entries if entry.ucsc_genome == ucsc_genome), None)
    if source_assembly is None:
        raise HTTPException(status_code=404, detail="Selected upstream assembly was not found")

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        find_genome_record = await _resolve_find_genome_record(client, ucsc_genome=ucsc_genome)
        resolved_description = str(find_genome_record.get("description") or source_assembly.description).strip()
        alias_matches = re.findall(r"(?:GC[AF]_\d+\.\d+)_([A-Za-z0-9._-]+)", resolved_description)
        resolved_alias = alias_matches[-1] if alias_matches else None
        assembly_name, assembly_version = _split_local_assembly_identity(
            source_assembly.assembly_name,
            resolved_alias,
            ucsc_genome,
        )

    existing_startup_assembly = (
        await _find_human_grch38_assembly(session)
        if missing_only and tax_id == HUMAN_GRCH38_TAX_ID and ucsc_genome == HUMAN_GRCH38_UCSC_GENOME
        else None
    )
    if existing_startup_assembly is not None:
        species_id = str(existing_startup_assembly["species_id"])
        species_name = str(existing_startup_assembly["species_name"])
        assembly_id = str(existing_startup_assembly["assembly_id"])
        assembly_name = str(existing_startup_assembly["assembly_name"])
        assembly_version = str(existing_startup_assembly["version"])
        created_species = False
        created_assembly = False
    else:
        species_id, species_name, created_species = await _get_or_create_species(
            session,
            scientific_name=source_assembly.scientific_name,
            common_name=source_assembly.common_name,
            tax_id=source_assembly.tax_id,
        )
        assembly_id, created_assembly = await _get_or_create_assembly(
            session,
            species_id=species_id,
            assembly_name=assembly_name,
            assembly_version=assembly_version,
            release_date=source_assembly.release_date,
        )

    cytobands_existing = (
        await _assembly_dataset_count(
            session,
            assembly_id=assembly_id,
            dataset_type="cytobands",
        )
        if missing_only
        else 0
    )
    genes_existing = (
        await _assembly_dataset_count(
            session,
            assembly_id=assembly_id,
            dataset_type="genes",
        )
        if missing_only
        else 0
    )
    cytoband_source_url = ""
    gene_source_url = ""
    gene_source = "UCSC gene tables"
    gene_warning: str | None = None
    cytobands_inserted = 0
    genes_inserted = 0
    cytobands_replaced = False
    genes_replaced = False

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        if not missing_only or cytobands_existing == 0 or overwrite:
            cytoband_text, cytoband_source_url = await _download_cytobands(client, ucsc_genome=ucsc_genome)
            cytobands = await apply_reference_dataset_text(
                session,
                assembly_id=assembly_id,
                dataset_type="cytobands",
                text_value=cytoband_text,
                overwrite=overwrite,
                commit=False,
                performed_by=performed_by,
                source="ucsc",
            )
            cytobands_inserted = cytobands.inserted
            cytobands_replaced = cytobands.replaced
        if not missing_only or genes_existing == 0 or overwrite:
            # GENCODE first: it is the annotation the variant pipeline is built on, and
            # it carries biotypes, Ensembl/HGNC identifiers and MANE tags that no UCSC
            # track does. Anything it cannot serve falls back to the UCSC track.
            gencode = await _download_gencode_genes(
                client, assembly_id=assembly_id, ucsc_genome=ucsc_genome
            )
            if gencode is not None:
                gencode_rows, gene_source_url, gene_source = gencode
                genes = await apply_reference_gene_rows(
                    session,
                    assembly_id=assembly_id,
                    rows=gencode_rows,
                    overwrite=overwrite,
                    commit=False,
                    performed_by=performed_by,
                    source=gene_source,
                )
                genes_inserted = genes.inserted
                genes_replaced = genes.replaced
            else:
                # Not every UCSC genome publishes a supported gene table (e.g.
                # T2T/hs1). Treat that as a soft failure: still create the assembly
                # with cytobands so genes can be uploaded manually later.
                try:
                    gene_text, gene_source_url, gene_source = await _download_genes(
                        client, ucsc_genome=ucsc_genome
                    )
                except HTTPException as exc:
                    gene_source = "unavailable"
                    gene_warning = exc.detail if isinstance(exc.detail, str) else "Gene table unavailable"
                    gene_text = None
                if gene_text is not None:
                    genes = await apply_reference_dataset_text(
                        session,
                        assembly_id=assembly_id,
                        dataset_type="genes",
                        text_value=gene_text,
                        overwrite=overwrite,
                        commit=False,
                        performed_by=performed_by,
                        source="ucsc",
                    )
                    genes_inserted = genes.inserted
                    genes_replaced = genes.replaced
    await session.commit()

    return ReferenceAutoImportResult(
        species_id=species_id,
        species_name=species_name,
        assembly_id=assembly_id,
        assembly_name=assembly_name,
        assembly_version=assembly_version,
        ucsc_genome=ucsc_genome,
        created_species=created_species,
        created_assembly=created_assembly,
        cytobands_inserted=cytobands_inserted,
        genes_inserted=genes_inserted,
        cytobands_replaced=cytobands_replaced,
        genes_replaced=genes_replaced,
        cytoband_source_url=cytoband_source_url,
        gene_source_url=gene_source_url,
        gene_source=gene_source,
        gene_warning=gene_warning,
    )


async def ensure_human_grch38_reference_on_startup(
    session: AsyncSession,
) -> ReferenceAutoImportResult | None:
    if not settings.reference_bootstrap_enabled:
        return None
    existing = await _find_human_grch38_assembly(session)
    if existing is not None:
        assembly_id = str(existing["assembly_id"])
        cytobands_existing = await _assembly_dataset_count(
            session,
            assembly_id=assembly_id,
            dataset_type="cytobands",
        )
        genes_existing = await _assembly_dataset_count(
            session,
            assembly_id=assembly_id,
            dataset_type="genes",
        )
        if cytobands_existing > 0 and genes_existing > 0:
            return None
    try:
        result = await import_reference_from_ucsc(
            session,
            tax_id=HUMAN_GRCH38_TAX_ID,
            ucsc_genome=HUMAN_GRCH38_UCSC_GENOME,
            overwrite=False,
            missing_only=True,
        )
        if result.cytobands_inserted or result.genes_inserted:
            logger.info(
                "Bootstrapped Homo sapiens %s/%s reference data from UCSC (%d chromosomes, %d genes)",
                result.assembly_name,
                result.assembly_version,
                result.cytobands_inserted,
                result.genes_inserted,
            )
        return result
    except Exception:
        logger.exception(
            "Failed to bootstrap Homo sapiens GRCh38 reference data from UCSC; "
            "creating the species and assembly shell only"
        )
        try:
            await session.rollback()
        except Exception:  # pragma: no cover
            logger.exception("Failed to roll back failed GRCh38 reference bootstrap")
        fallback = await ensure_human_grch38_species_assembly(session)
        await session.commit()
        logger.info(
            "Ensured Homo sapiens %s/%s assembly shell (%s)",
            fallback["assembly_name"],
            fallback["version"],
            fallback["assembly_id"],
        )
        return None


async def ensure_human_t2t_reference_on_startup(
    session: AsyncSession,
) -> ReferenceAutoImportResult | None:
    """Import T2T-CHM13v2.0 alongside GRCh38 when the deployment asks for it.

    Off unless ``REFERENCE_BOOTSTRAP_T2T`` is set: a second assembly roughly doubles the
    reference footprint, and not every deployment reports on T2T. The gene page's T2T
    locus rows stay empty until it is enabled — which is the honest state, since without
    this there is no T2T assembly for them to describe.

    T2T is a genuinely poorer annotation than GRCh38 here. UCSC publishes no cytoband
    table for hs1, so cytobands fall back to one band per chromosome built from the
    chromosome sizes, and its gene GTF is RefSeq-derived: coordinates, but no biotypes,
    Ensembl identifiers or MANE tags. Failure is never fatal — GRCh38 is the primary
    assembly and must come up regardless.
    """
    if not settings.reference_bootstrap_enabled or not settings.reference_bootstrap_t2t:
        return None
    try:
        result = await import_reference_from_ucsc(
            session,
            tax_id=HUMAN_GRCH38_TAX_ID,
            ucsc_genome=HUMAN_T2T_UCSC_GENOME,
            overwrite=False,
            missing_only=True,
        )
        if result.cytobands_inserted or result.genes_inserted:
            logger.info(
                "Bootstrapped Homo sapiens %s/%s reference data (%d chromosomes, %d genes)",
                result.assembly_name,
                result.assembly_version,
                result.cytobands_inserted,
                result.genes_inserted,
            )
        return result
    except Exception:
        logger.exception(
            "Failed to bootstrap Homo sapiens T2T-CHM13 reference data; GRCh38 is unaffected"
        )
        try:
            await session.rollback()
        except Exception:  # pragma: no cover
            logger.exception("Failed to roll back failed T2T reference bootstrap")
        return None
