from __future__ import annotations

from dataclasses import replace
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .clickhouse_variant_records import (
    StoredStructuralVariantRow,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from .clickhouse_variant_ids import build_structural_variant_id
from .data_scope import normalize_chromosome
from .family_metadata_context import (
    SampleMetadataContext,
)

from .variant_annotation_parser import (
    AnnotationHeaderState,
    extract_small_variant_annotations,
    update_annotation_header_state,
)

from .family_package_common import MITO_SV_SOURCE, ParsedPed, _coerce_finite_float, _coerce_int, _first_info_value, _jsonb_safe, _metadata_dict, _missing_scalar, _parse_format, _parse_vcf_info, _split_gene_symbols, resolve_vcf_sample_id


logger = logging.getLogger(__name__)


def _needlr_query_sample_id(info: dict[str, str], sample_ids: set[str]) -> str | None:
    """The family sample NeedlR's query (``Query_ID``) names, by the shared VCF-sample rule.

    NeedlR names the query after its input file (``HG002_sv_phased``). This used its own
    copy of the rule: fewer tool suffixes (``_sv_phased`` and ``_sort`` fell through to
    the prefix match), and a prefix match over an unordered set, so with samples ``S1``
    and ``S1_A`` the query ``S1_A_sv_phased`` could be read as ``S1`` -- the SV calls then
    landed on the wrong member. ``resolve_vcf_sample_id`` strips the known suffixes first
    and tries the longest sample id first.
    """
    query_id = _first_info_value(info, "Query_ID", "QueryId", "Sample", "SAMPLE")
    if query_id is None:
        return None
    return resolve_vcf_sample_id(query_id, sample_ids)


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
    sample_column: int = 0,
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
      out again, an import that "succeeds" and shows nothing. ``sample_column`` is the
      column the importer checked holds that sample (``per_sample_vcf_column``).
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
        if len(parts) > 9 + sample_column:
            fmt_vals = _parse_format(parts[8], parts[9 + sample_column])
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


def _mt_genes_overlapping(start: int, end: int) -> list[str]:
    # Imported here: the mtDNA analysis module sits above the package importers.
    from .mitochondrial_analysis import MT_LOCI

    low, high = min(start, end), max(start, end)
    genes: list[str] = []
    for locus in MT_LOCI:
        gene = str(locus["gene"])
        if int(locus["start"]) <= high and int(locus["end"]) >= low and gene not in genes:
            genes.append(gene)
    return genes


def _iter_mito_sv_records(
    text_value: str, *, sample_id: str, sample_column: int = 0
) -> list[StructuralVariantRecord]:
    """Parse one sample's chrM SV VCF (Sniffles2 ``--mosaic -c chrM``) into SV records.

    A large mtDNA deletion is heteroplasmic, so the caller's ``INFO/VAF`` -- the fraction
    of reads that carry the event, i.e. its heteroplasmy -- is what a reader needs; it is
    kept per sample in the record's annotations, beside the read support (``FORMAT/DV``,
    else ``INFO/SUPPORT``). The record is bound to ``sample_id``, the sample the manifest
    declares the file for, as the HiFiCNV path does: these files carry one call.

    The id is built from the coordinates, not Sniffles' own (``Sniffles2.DEL.3M0`` repeats
    across samples and runs), so the same event called in a mother and her child is one
    SV with both calls once the importer merges the samples' records. ``sample_column`` is
    the column the importer checked holds that sample (``per_sample_vcf_column``).
    """
    records: list[StructuralVariantRecord] = []
    for line in text_value.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.rstrip("\n\r").split("\t")
        if len(parts) < 8:
            continue
        chrom, pos_raw, _record_id, ref, alt, qual_raw, filt_raw, info_raw = parts[:8]
        if normalize_chromosome(chrom).upper() not in {"M", "MT"}:
            continue
        start = _coerce_int(pos_raw)
        if start is None:
            continue
        info = _parse_vcf_info(info_raw)
        sv_type = (_first_info_value(info, "SVTYPE") or alt.strip("<>") or "SV").upper()
        sv_len = _coerce_int(_first_info_value(info, "SVLEN"))
        end = _coerce_int(_first_info_value(info, "END"))
        if end is None:
            end = start + abs(sv_len or 0)
        fmt_vals = (
            _parse_format(parts[8], parts[9 + sample_column]) if len(parts) > 9 + sample_column else {}
        )
        gt = fmt_vals.get("GT") or "./."
        alleles = gt.replace("|", "/").split("/")
        if not any(allele not in {"0", "."} for allele in alleles):
            # Sniffles writes 0/0 for a site it genotyped and found no support for.
            continue
        filt = None if filt_raw in {"", ".", "PASS"} else filt_raw
        read_support = _coerce_int(fmt_vals.get("DV")) or _coerce_int(_first_info_value(info, "SUPPORT"))
        heteroplasmy = _coerce_finite_float(_first_info_value(info, "VAF"))
        variant_id = build_structural_variant_id(chrom, start, end, sv_type, source=MITO_SV_SOURCE)
        records.append(
            StructuralVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr=normalize_chromosome(chrom),
                start=start,
                end=end,
                sv_type=sv_type,
                source=MITO_SV_SOURCE,
                remote_chr=None,
                remote_start=None,
                remote_end=None,
                sv_len=sv_len,
                filters=[] if filt is None else filt.split(";"),
                gene_symbols=_mt_genes_overlapping(start, end),
                annotations=[
                    {
                        "source": MITO_SV_SOURCE,
                        "sample": sample_id,
                        "heteroplasmy": heteroplasmy,
                        "ref": ref,
                        "alt": alt,
                        "info": info,
                    }
                ],
                calls=[
                    StructuralVariantCall(
                        sample=sample_id,
                        gt=gt,
                        qual=_coerce_finite_float(qual_raw),
                        read_support=read_support,
                        filter=filt,
                    )
                ],
            )
        )
    return records


def _merge_sv_records_by_id(records: list[StructuralVariantRecord]) -> list[StructuralVariantRecord]:
    """One record per SV id holding every sample's call and annotation; per-sample files
    call the same event once per sample."""
    merged: dict[str, StructuralVariantRecord] = {}
    for record in records:
        existing = merged.get(record.variant_id)
        if existing is None:
            merged[record.variant_id] = record
            continue
        known = {call.sample for call in existing.calls}
        existing.calls.extend(call for call in record.calls if call.sample not in known)
        existing.annotations.extend(record.annotations)
    return list(merged.values())


def _mito_sv_rows_after_import(
    stored_rows: list[StoredStructuralVariantRow],
    new_records: list[StructuralVariantRecord],
    *,
    replaced_samples: set[str],
    project_ids: list[str],
) -> list[StoredStructuralVariantRow]:
    """The family's ``mito_sv`` rows once ``replaced_samples`` have their new calls.

    As the SNV path does, an import replaces the samples it brings a file for and nothing
    else: every other sample's calls -- and their heteroplasmy annotations -- are written
    back as stored, under their own project; a row left with no call goes. A new SV joins
    the stored row of the same id, or gets a row in each of the family's projects.
    ``replaced_samples`` holds each sample's id and uuid: a stored call names either.
    """
    rows: list[StoredStructuralVariantRow] = []
    for row in stored_rows:
        calls = [call for call in row.record.calls if call.sample not in replaced_samples]
        if not calls:
            continue
        annotations = [
            annotation
            for annotation in row.record.annotations
            if str(annotation.get("sample") or "") not in replaced_samples
        ]
        rows.append(replace(row, record=replace(row.record, calls=calls, annotations=annotations)))
    by_identity = {(row.project_id, row.record.variant_id): row for row in rows}
    for record in new_records:
        for project_id in project_ids:
            existing = by_identity.get((project_id, record.variant_id))
            if existing is None:
                fresh = StoredStructuralVariantRow(
                    project_id=project_id,
                    record=replace(record, calls=list(record.calls), annotations=list(record.annotations)),
                )
                by_identity[(project_id, record.variant_id)] = fresh
                rows.append(fresh)
                continue
            existing.record.calls.extend(record.calls)
            existing.record.annotations.extend(record.annotations)
            existing.record.gene_symbols = list(
                dict.fromkeys([*existing.record.gene_symbols, *record.gene_symbols])
            )
    return rows
