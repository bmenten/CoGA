from __future__ import annotations

import csv
import gzip
import io
import json
from typing import Iterable, Literal
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    AssemblyReferenceStatusOut,
    BlacklistRegionOut,
    ClinicalCnvOut,
    ChromosomeOut,
    ChromosomeSizeOut,
    GeneOut,
    ReferenceUploadResult,
)
from .data_scope import chromosome_aliases, normalize_chromosome

ReferenceDatasetType = Literal["cytobands", "genes", "blacklist", "clinical_cnvs"]


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
    contents = await file.read()
    try:
        return contents.decode()
    except UnicodeDecodeError:
        try:
            return gzip.decompress(contents).decode()
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail="Reference upload must be plain text or gzipped",
            ) from exc


def _reader_from_text(text_value: str) -> Iterable[list[str]]:
    return csv.reader(io.StringIO(text_value), delimiter="\t")


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
                COALESCE(cnv_counts.count, 0) AS clinical_cnvs
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
            ORDER BY a.assembly_name, a.version
            """
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
        )
        for row in result.mappings().all()
    ]


async def upload_reference_dataset(
    session: AsyncSession,
    *,
    assembly_id: str,
    dataset_type: ReferenceDatasetType,
    file: UploadFile,
    overwrite: bool,
) -> ReferenceUploadResult:
    text_value = await decode_reference_upload(file)
    return await apply_reference_dataset_text(
        session,
        assembly_id=assembly_id,
        dataset_type=dataset_type,
        text_value=text_value,
        overwrite=overwrite,
    )


async def apply_reference_dataset_text(
    session: AsyncSession,
    *,
    assembly_id: str,
    dataset_type: ReferenceDatasetType,
    text_value: str,
    overwrite: bool,
    commit: bool = True,
) -> ReferenceUploadResult:
    assembly = await _get_assembly_by_id(session, assembly_id)

    count_query = {
        "cytobands": "SELECT COUNT(*) FROM chromosomes WHERE assembly_id = CAST(:assembly_id AS uuid)",
        "genes": "SELECT COUNT(*) FROM genes WHERE assembly_id = CAST(:assembly_id AS uuid)",
        "blacklist": "SELECT COUNT(*) FROM blacklist WHERE assembly_id = CAST(:assembly_id AS uuid)",
        "clinical_cnvs": "SELECT COUNT(*) FROM clinical_cnvs WHERE assembly_id = CAST(:assembly_id AS uuid)",
    }[dataset_type]
    existing = await session.execute(text(count_query), {"assembly_id": assembly_id})
    existing_count = int(existing.scalar_one() or 0)
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
            if not row or row[0].startswith("#") or len(row) < 4:
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

    else:
        rows = []
        for row in _reader_from_text(text_value):
            if not row or row[0].startswith("#") or row[0].startswith("track") or len(row) < 11:
                continue
            (
                chrom,
                start,
                end,
                name,
                _score,
                _strand,
                _thick_start,
                _thick_end,
                _item_rgb,
                label,
                html,
            ) = row[:11]
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
                    "type": name or None,
                    "label": label,
                    "details_html": html,
                }
            )
        if not rows:
            raise HTTPException(status_code=400, detail="No valid clinical CNV rows found")
        await session.execute(
            text(
                """
                INSERT INTO clinical_cnvs (assembly_id, chr, start, "end", type, label, details_html)
                VALUES (
                    CAST(:assembly_id AS uuid),
                    :chr,
                    :start,
                    :end,
                    :type,
                    :label,
                    :details_html
                )
                """
            ),
            rows,
        )
        inserted = len(rows)

    if commit:
        await session.commit()
    return ReferenceUploadResult(
        assembly_id=assembly["id"],
        assembly_name=assembly["assembly_name"],
        dataset_type=dataset_type,
        inserted=inserted,
        replaced=replaced,
    )


async def get_gene_region_records(
    session: AsyncSession,
    *,
    assembly: str,
    chrom: str,
    start: int,
    end: int,
) -> list[GeneOut]:
    assembly_row = await _get_assembly_by_name(session, assembly)
    stmt = text(
        """
        SELECT id::text AS id, gene_id, hgnc_symbol, chr, start, "end", exons, strand
        FROM genes
        WHERE assembly_id = CAST(:assembly_id AS uuid)
          AND chr IN :chromosomes
          AND (:apply_window = false OR (start < :end AND "end" > :start))
        ORDER BY start, "end", hgnc_symbol
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
        for row in result.mappings().all()
    ]


async def get_blacklist_regions_data(
    session: AsyncSession,
    *,
    assembly: str,
    chrom: str,
    start: int,
    end: int,
) -> list[BlacklistRegionOut]:
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
        """
        SELECT id::text AS id, chr, start, "end", type, label, details_html
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
        ClinicalCnvOut(
            _id=row["id"],
            chr=row["chr"],
            start=int(row["start"]),
            end=int(row["end"]),
            type=row.get("type"),
            label=row["label"],
            details_html=row.get("details_html"),
        )
        for row in result.mappings().all()
    ]


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
