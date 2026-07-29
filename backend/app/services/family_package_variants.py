from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .clickhouse_family_variants import StructuralVariantCall, StructuralVariantRecord
from .clickhouse_variant_storage import (
    build_structural_variant_id,
)
from .data_scope import normalize_chromosome
from .family_metadata_context import (
    SampleMetadataContext,
)

from .variant_annotation_parser import (
    AnnotationHeaderState,
    extract_small_variant_annotations,
    update_annotation_header_state,
)

from .family_package_common import ParsedPed, _coerce_finite_float, _coerce_int, _first_info_value, _jsonb_safe, _metadata_dict, _missing_scalar, _parse_format, _parse_vcf_info, _split_gene_symbols  # noqa: F401


logger = logging.getLogger(__name__)


def _needlr_query_sample_id(info: dict[str, str], sample_ids: set[str]) -> str | None:
    query_id = _first_info_value(info, "Query_ID", "QueryId", "Sample", "SAMPLE")
    if query_id is None:
        return None
    if query_id in sample_ids:
        return query_id
    for suffix in ("_sv", ".sv", "-sv"):
        if query_id.endswith(suffix) and query_id[: -len(suffix)] in sample_ids:
            return query_id[: -len(suffix)]
    for sample_id in sample_ids:
        if query_id.startswith(f"{sample_id}_") or query_id.startswith(f"{sample_id}."):
            return sample_id
    return None


def _needlr_call(
    sample_id: str,
    *,
    info: dict[str, str],
    gt_key: str,
    alt_reads_key: str,
    qual: float | None,
    filt: str | None,
) -> StructuralVariantCall:
    gt = _first_info_value(info, gt_key) or "./."
    read_support = _coerce_int(_first_info_value(info, alt_reads_key))
    return StructuralVariantCall(
        sample=sample_id,
        gt=gt,
        qual=qual,
        read_support=read_support,
        filter=filt,
    )


def _needlr_parent_sample_ids(ped: ParsedPed, sample_id: str) -> tuple[str | None, str | None]:
    member = next((item for item in ped.members if item.iid == sample_id), None)
    if member is None:
        return None, None
    mother = member.mid if member.mid not in {"", "0"} else None
    father = member.pid if member.pid not in {"", "0"} else None
    return mother, father


def _iter_needlr_structural_records(
    text_value: str,
    *,
    ped: ParsedPed,
    sample_contexts: dict[str, SampleMetadataContext],
) -> list[StructuralVariantRecord]:
    sample_ids = set(sample_contexts)
    merged: dict[str, StructuralVariantRecord] = {}
    allele_by_variant_id: dict[str, tuple[str, str]] = {}
    for line in text_value.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        chrom, pos_raw, record_id, ref, alt, qual_raw, filt_raw, info_raw = parts[:8]
        start = _coerce_int(pos_raw)
        if start is None:
            continue
        info = _parse_vcf_info(info_raw)
        sv_type = _first_info_value(info, "SVTYPE") or alt.strip("<>") or "SV"
        sv_len = _coerce_int(_first_info_value(info, "SVLEN"))
        end = _coerce_int(_first_info_value(info, "END", "End_Pos", "END_POS", "End"))
        if end is None:
            end = start + abs(sv_len or 1)
        qual = _coerce_finite_float(qual_raw)
        filt = None if filt_raw in {"", "."} else filt_raw
        query_sample = _needlr_query_sample_id(info, sample_ids)
        calls: list[StructuralVariantCall] = []
        if query_sample is not None:
            calls.append(
                _needlr_call(
                    query_sample,
                    info=info,
                    gt_key="Genotype",
                    alt_reads_key="Alt_Reads",
                    qual=qual,
                    filt=filt,
                )
            )
            mother_id, father_id = _needlr_parent_sample_ids(ped, query_sample)
            if mother_id in sample_ids:
                calls.append(
                    _needlr_call(
                        mother_id,
                        info=info,
                        gt_key="Maternal_GT",
                        alt_reads_key="Maternal_Alt_Reads",
                        qual=qual,
                        filt=filt,
                    )
                )
            if father_id in sample_ids:
                calls.append(
                    _needlr_call(
                        father_id,
                        info=info,
                        gt_key="Paternal_GT",
                        alt_reads_key="Paternal_Alt_Reads",
                        qual=qual,
                        filt=filt,
                    )
                )
        if not calls:
            continue

        variant_id = (
            record_id
            if record_id and record_id != "."
            else build_structural_variant_id(chrom, start, end, sv_type)
        )
        # Co-located same-type calls (insertions in particular share a breakpoint and
        # differ only in inserted sequence) would otherwise collapse onto one id and
        # the last record would win. Only a genuine clash gets a discriminator, so
        # every non-colliding id stays byte-identical to what earlier imports stored.
        alleles = (ref, alt)
        attempt = 0
        while True:
            previous_alleles = allele_by_variant_id.get(variant_id)
            if previous_alleles is None or previous_alleles == alleles:
                break
            attempt += 1
            suffix = str(abs(sv_len) if sv_len is not None else len(alt))
            variant_id = build_structural_variant_id(
                chrom,
                start,
                end,
                sv_type,
                discriminator=suffix if attempt == 1 else f"{suffix}.{attempt}",
            )
        allele_by_variant_id.setdefault(variant_id, alleles)
        annotation = {
            "source": "needlr",
            "ref": ref,
            "alt": alt,
            "info": info,
        }
        gene_symbols = _split_gene_symbols(info.get("Genes"))
        existing = merged.get(variant_id)
        if existing is None:
            merged[variant_id] = StructuralVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr=normalize_chromosome(chrom),
                start=start,
                end=end,
                sv_type=sv_type,
                source="needlr",
                remote_chr=None,
                remote_start=None,
                remote_end=None,
                sv_len=sv_len,
                filters=[] if filt is None else [filt],
                gene_symbols=gene_symbols,
                annotations=[annotation],
                calls=sorted(calls, key=lambda call: call.sample),
            )
            continue
        call_by_sample = {call.sample: call for call in existing.calls}
        for call in calls:
            call_by_sample[call.sample] = call
        merged[variant_id] = StructuralVariantRecord(
            variant_key=existing.variant_key,
            variant_id=existing.variant_id,
            chr=existing.chr,
            start=existing.start,
            end=existing.end,
            sv_type=existing.sv_type,
            source=existing.source,
            remote_chr=existing.remote_chr,
            remote_start=existing.remote_start,
            remote_end=existing.remote_end,
            sv_len=existing.sv_len,
            filters=list(dict.fromkeys([*existing.filters, *([] if filt is None else [filt])])),
            gene_symbols=list(dict.fromkeys([*existing.gene_symbols, *gene_symbols])),
            annotations=[*existing.annotations, annotation],
            calls=sorted(call_by_sample.values(), key=lambda call: call.sample),
        )
    return list(merged.values())


def _iter_cnv_structural_records(
    text_value: str,
    *,
    sample_id: str,
    source: str = "hificnv",
) -> list[StructuralVariantRecord]:
    """Parse a depth-based CNV caller's VCF (HiFiCNV) into structural-variant records.

    The calls land in the structural-variant store rather than an interval track so
    they are filterable, reviewable and reachable by the ClinGen CNV dosage scoring,
    which needs the overlapping genes and the copy number.

    Two things differ from the NeedlR path:

    * genes come from ``INFO/CSQ`` (VEP), the only place this caller records them;
    * the VCF's single sample column is named after the caller's internal sample slot
      (``Sample0``), not the sample. The record is bound to ``sample_id`` -- the sample
      the manifest declares this file for -- so the calls stay visible. Copying the
      caller's name through would store rows that the project-scoped read path filters
      out again, an import that "succeeds" and shows nothing.
    """
    annotation_state = AnnotationHeaderState()
    records: list[StructuralVariantRecord] = []
    for line in text_value.splitlines():
        if line.startswith("##INFO"):
            update_annotation_header_state(annotation_state, line.strip())
            continue
        if not line or line.startswith("#"):
            continue
        parts = line.rstrip("\n\r").split("\t")
        if len(parts) < 8:
            continue
        chrom, pos_raw, record_id, ref, alt, qual_raw, filt_raw, info_raw = parts[:8]
        start = _coerce_int(pos_raw)
        if start is None:
            continue
        info = _parse_vcf_info(info_raw)
        sv_type = _first_info_value(info, "SVTYPE") or alt.strip("<>") or "CNV"
        sv_len = _coerce_int(_first_info_value(info, "SVLEN"))
        end = _coerce_int(_first_info_value(info, "END", "End_Pos"))
        if end is None:
            end = start + abs(sv_len or 1)
        qual = _coerce_finite_float(qual_raw)
        filt = None if filt_raw in {"", "."} else filt_raw
        copy_number: int | None = None
        gt = "./."
        if len(parts) >= 10:
            fmt_vals = _parse_format(parts[8], parts[9])
            gt = fmt_vals.get("GT") or "./."
            copy_number = _coerce_int(fmt_vals.get("CN"))
        annotations = extract_small_variant_annotations(info, annotation_state)
        gene_symbols: list[str] = []
        seen_genes: set[str] = set()
        for annotation in annotations:
            gene = annotation.get("gene")
            if gene and gene not in seen_genes:
                seen_genes.add(str(gene))
                gene_symbols.append(str(gene))
        variant_id = (
            record_id
            if record_id and record_id != "."
            else build_structural_variant_id(chrom, start, end, sv_type, source=source)
        )
        records.append(
            StructuralVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr=normalize_chromosome(chrom),
                start=start,
                end=end,
                sv_type=sv_type,
                source=source,
                remote_chr=None,
                remote_start=None,
                remote_end=None,
                sv_len=sv_len,
                filters=[] if filt is None else filt.split(";"),
                gene_symbols=gene_symbols,
                annotations=[{"source": source, "ref": ref, "alt": alt, "info": info}],
                calls=[
                    StructuralVariantCall(
                        sample=sample_id,
                        gt=gt,
                        qual=qual,
                        read_support=None,
                        filter=filt,
                        copy_number=copy_number,
                    )
                ],
            )
        )
    return records


async def _update_sv_file_metadata(
    session: AsyncSession,
    *,
    sample_contexts: dict[str, SampleMetadataContext],
    source: str,
    filename: str,
) -> None:
    for sample_context in sample_contexts.values():
        result = await session.execute(
            text("SELECT metadata FROM samples WHERE id = CAST(:sample_id AS uuid)"),
            {"sample_id": sample_context.sample_uuid},
        )
        metadata = _metadata_dict(result.scalar_one_or_none())
        sv_files = dict(metadata.get("sv_files") or {})
        sv_files[source] = filename
        metadata["sv_files"] = sv_files
        await session.execute(
            text(
                """
                UPDATE samples
                SET metadata = CAST(:metadata_json AS jsonb)
                WHERE id = CAST(:sample_id AS uuid)
                """
            ),
            {
                "sample_id": sample_context.sample_uuid,
                "metadata_json": json.dumps(metadata),
            },
        )
    await session.commit()


def _paraphase_rows_for_sample(
    *,
    sample_context: SampleMetadataContext,
    path: Path,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    metadata_json = json.dumps(
        {
            "source": "paraphase",
            "filename": path.name,
            "uploaded_from": "family_package",
        }
    )
    rows: list[dict[str, Any]] = []
    for gene_symbol, raw_result in sorted(payload.items()):
        if not isinstance(raw_result, dict):
            continue
        rows.append(
            {
                "sample_id": sample_context.sample_uuid,
                "family_id": sample_context.family_uuid,
                "assembly_id": sample_context.assembly_id or "",
                "gene_symbol": str(gene_symbol),
                "total_cn": _coerce_int(raw_result.get("total_cn")),
                "gene_cn": _coerce_int(raw_result.get("gene_cn")),
                "highest_total_cn": _coerce_int(raw_result.get("highest_total_cn")),
                "sample_sex": (
                    None
                    if _missing_scalar(raw_result.get("sample_sex"))
                    else str(raw_result.get("sample_sex"))
                ),
                "phase_region": (
                    None
                    if _missing_scalar(raw_result.get("phase_region"))
                    else str(raw_result.get("phase_region"))
                ),
                "region_depth_json": json.dumps(_jsonb_safe(raw_result.get("region_depth") or {})),
                "genome_depth": _coerce_finite_float(raw_result.get("genome_depth")),
                "payload_json": json.dumps(_jsonb_safe(raw_result)),
                "metadata_json": metadata_json,
            }
        )
    return rows


async def _replace_sample_paraphase_rows(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    rows: list[dict[str, Any]],
) -> None:
    await session.execute(
        text(
            """
            DELETE FROM sample_paraphase_results
            WHERE sample_id = CAST(:sample_id AS uuid)
            """
        ),
        {"sample_id": sample_context.sample_uuid},
    )
    for index in range(0, len(rows), 1000):
        await session.execute(
            text(
                """
                INSERT INTO sample_paraphase_results (
                    sample_id,
                    family_id,
                    assembly_id,
                    gene_symbol,
                    total_cn,
                    gene_cn,
                    highest_total_cn,
                    sample_sex,
                    phase_region,
                    region_depth,
                    genome_depth,
                    payload,
                    metadata,
                    uploaded_at
                )
                VALUES (
                    CAST(:sample_id AS uuid),
                    CAST(:family_id AS uuid),
                    CAST(NULLIF(:assembly_id, '') AS uuid),
                    :gene_symbol,
                    :total_cn,
                    :gene_cn,
                    :highest_total_cn,
                    :sample_sex,
                    :phase_region,
                    CAST(:region_depth_json AS jsonb),
                    :genome_depth,
                    CAST(:payload_json AS jsonb),
                    CAST(:metadata_json AS jsonb),
                    timezone('utc', now())
                )
                """
            ),
            rows[index : index + 1000],
        )
    await session.commit()
