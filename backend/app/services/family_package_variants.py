from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .clickhouse_column_bounds import UINT32, column_int
from .clickhouse_variant_records import (
    StoredStructuralVariantRow,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from .clickhouse_variant_ids import SvEvent, build_structural_variant_id, event_discriminators
from .data_scope import normalize_chromosome
from .family_metadata_context import (
    SampleMetadataContext,
)
from .genotypes import NO_CALL, classify_genotype

from .variant_annotation_parser import (
    AnnotationHeaderState,
    extract_small_variant_annotations,
    update_annotation_header_state,
)

from .structural_variant_ingest import breakend_mate, record_end, record_sv_len, sv_event, sv_type_from_alt
from .family_package_common import MITO_SV_SOURCE, ParsedPed, _coerce_finite_float, _coerce_int, _first_info_value, _jsonb_safe, _metadata_dict, _missing_scalar, _parse_format, _parse_vcf_info, _split_gene_symbols, resolve_vcf_sample_id


def _unpositionable(start: int, end: int) -> bool:
    """Whether the SV tables' UInt32 position columns cannot hold a record's span (a
    negative POS, an END beyond 4,294,967,295): the record is skipped as one without a POS
    is (SAFE-30)."""
    return column_int(start, UINT32) is None or column_int(end, UINT32) is None


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


def _needlr_call_rank(call: StructuralVariantCall, *, own: bool) -> tuple[bool, bool]:
    """How a line's call of a sample ranks against another line's call of that sample, when
    the lines of one event merge: a call with a genotype over one without (``./.``, a
    parent's call from a line without its ``Maternal_GT`` or ``Paternal_GT``), then the
    sample's own call, ``own`` -- its line's query call -- over one that another sample's
    line gives it as a parent. Of two calls that rank alike the later line's is kept, as
    every later call was before.

    One event in two samples' per-sample files is one SV (CLIN-43): a mother's own call,
    from her file, must not give way to the ``./.`` her child's line gives her.
    """
    return (classify_genotype(call.gt) != NO_CALL, own)


def _needlr_parent_sample_ids(ped: ParsedPed, sample_id: str) -> tuple[str | None, str | None]:
    member = next((item for item in ped.members if item.iid == sample_id), None)
    if member is None:
        return None, None
    mother = member.mid if member.mid not in {"", "0"} else None
    father = member.pid if member.pid not in {"", "0"} else None
    return mother, father


def _needlr_variant_id(
    chrom: str,
    start: int,
    end: int,
    sv_type: str,
    *,
    remote_chr: str | None,
    remote_start: int | None,
    discriminator: str | None = None,
) -> str:
    """A NeedlR SV's id: its coordinates and type, and a breakend's mate, as an uploaded
    SV's call of the same event is named (``chrom-start-end-type-mate``), never the
    record's ID (CLIN-43).

    A caller numbers its calls per run (``Sniffles2.DEL.1S0``), so two samples' files, or
    two runs in one family VCF, give one ID to different calls. Named by the ID, they were
    one SV at the first call's position: the other call was attached to it, and its own
    SV was lost. The chrM reader names its SVs by their coordinates for the same reason.
    The same event called by NeedlR and by an upload has one id, and so one review, as two
    uploads' calls of one event have; the storage key names the source
    (``structural_variant_key``), so their rows stay apart. ``discriminator`` keeps apart
    two events of one position and type (``_needlr_records``).
    """
    return build_structural_variant_id(
        chrom,
        start,
        end,
        sv_type,
        remote_chr=remote_chr,
        remote_start=remote_start,
        remote_end=remote_start,
        discriminator=discriminator,
    )


def needlr_query_ids(text_value: str) -> set[str]:
    """Every query sample (``Query_ID``) a NeedlR VCF's records name."""
    queries: set[str] = set()
    for line in text_value.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        query_id = _first_info_value(_parse_vcf_info(parts[7]), "Query_ID", "QueryId", "Sample", "SAMPLE")
        if query_id is not None:
            queries.add(query_id)
    return queries


def _iter_needlr_structural_records(
    text_value: str,
    *,
    ped: ParsedPed,
    sample_contexts: dict[str, SampleMetadataContext],
) -> list[StructuralVariantRecord]:
    """A family NeedlR VCF's records, each query call under the sample its ``Query_ID``
    names."""
    return _needlr_records([(text_value, None)], ped=ped, sample_contexts=sample_contexts)


def _iter_needlr_per_sample_records(
    files: Sequence[tuple[str, str]],
    *,
    ped: ParsedPed,
    sample_contexts: dict[str, SampleMetadataContext],
) -> list[StructuralVariantRecord]:
    """The records of one NeedlR VCF per sample, ``(sample id, text)``, as one callset: each
    file's query calls belong to its sample (the importer checked that its ``Query_ID``
    names no other), and one event called in two samples -- the same coordinates, type,
    mate, alleles and length -- is one record holding both calls. Calls of two events stay
    two records, whatever their IDs."""
    return _needlr_records(
        [(text_value, sample_id) for sample_id, text_value in files],
        ped=ped,
        sample_contexts=sample_contexts,
    )


@dataclass(frozen=True)
class _NeedlrLine:
    """One NeedlR line as read: its call's coordinates, type, mate and event (``base_id`` is
    the id they give it without a discriminator), its calls, and what its annotation keeps."""

    base_id: str
    event: SvEvent
    chrom: str
    start: int
    end: int
    sv_type: str
    remote_chr: str | None
    remote_start: int | None
    sv_len: int | None
    ref: str
    alt: str
    record_id: str
    info: dict[str, str]
    filt: str | None
    calls: list[StructuralVariantCall]
    query_sample: str | None


def _needlr_records(
    texts: Sequence[tuple[str, str | None]],
    *,
    ped: ParsedPed,
    sample_contexts: dict[str, SampleMetadataContext],
) -> list[StructuralVariantRecord]:
    """The records of the NeedlR ``texts``, each with the sample its query calls are bound
    to, or None to read it from the record's ``Query_ID``.

    Lines merge by their SV id, which their coordinates, type and mate give them
    (``_needlr_variant_id``), with a discriminator for another event on that position
    (``event_discriminators``): a line repeated in the family VCF, or one event called in
    two samples' files, is one record with every call; two events are two records, whatever
    their IDs. A sample's calls from two lines of one event are ranked by
    ``_needlr_call_rank``."""
    sample_ids = set(sample_contexts)
    read: list[_NeedlrLine] = []
    lines = (
        (line, bound_sample)
        for text_value, bound_sample in texts
        for line in text_value.splitlines()
    )
    for line, bound_sample in lines:
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
        sv_type = _first_info_value(info, "SVTYPE") or sv_type_from_alt(ref, alt) or "SV"
        # A breakend's mate as every SV reader reads it (``breakend_mate``): the reader stored
        # none before CLIN-40, so a NeedlR translocation named no partner in the SV list.
        remote_chr, remote_start = breakend_mate(sv_type=sv_type, alt=alt, info=info, chrom=chrom)
        sv_len = _coerce_int(_first_info_value(info, "SVLEN"))
        # Without END, where VCF 4.4 puts it, as the uploads do: it was POS + SVLEN for any
        # type, so an insertion or a breakend spanned bases after its POS, and the same call
        # had another id than an upload's (CLIN-39).
        end = record_end(
            pos=start,
            ref=ref,
            alt=alt,
            sv_len=sv_len,
            info=info,
            chrom=chrom,
            end=_first_info_value(info, "END", "End_Pos", "END_POS", "End"),
        )
        if end is None or _unpositionable(start, end):
            continue
        qual = _coerce_finite_float(qual_raw)
        filt = None if filt_raw in {"", "."} else filt_raw
        query_sample = (
            bound_sample if bound_sample is not None else _needlr_query_sample_id(info, sample_ids)
        )
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
        read.append(
            _NeedlrLine(
                # The id is the call's coordinates, type and mate, never the record's ID,
                # which a caller repeats across samples and runs (CLIN-43).
                base_id=_needlr_variant_id(
                    chrom, start, end, sv_type, remote_chr=remote_chr, remote_start=remote_start
                ),
                event=sv_event(ref=ref, alt=alt, sv_len=sv_len, start=start, end=end),
                chrom=chrom,
                start=start,
                end=end,
                sv_type=sv_type,
                remote_chr=remote_chr,
                remote_start=remote_start,
                sv_len=sv_len,
                ref=ref,
                alt=alt,
                record_id=record_id,
                info=info,
                filt=filt,
                calls=calls,
                query_sample=query_sample,
            )
        )

    # Two calls of one position, type and mate are two events when their alleles differ
    # (insertions in particular share a breakpoint and differ only in inserted sequence), or
    # their length does: a symbolic allele (<INS>) does not hold it. They would otherwise
    # collapse onto one id, one call attached to the other's SV. One keeps the id, another
    # gets a discriminator, its length, by the order of the events, not of the lines: by the
    # lines, a reordered file gave each event the other's id (CLIN-39).
    discriminators = event_discriminators((item.base_id, item.event) for item in read)
    merged: dict[str, StructuralVariantRecord] = {}
    call_ranks: dict[str, dict[str, tuple[bool, bool]]] = {}
    for item in read:
        discriminator = discriminators[(item.base_id, item.event)]
        variant_id = (
            item.base_id
            if discriminator is None
            else _needlr_variant_id(
                item.chrom,
                item.start,
                item.end,
                item.sv_type,
                remote_chr=item.remote_chr,
                remote_start=item.remote_start,
                discriminator=discriminator,
            )
        )
        annotation: dict[str, Any] = {"source": "needlr"}
        if not _missing_scalar(item.record_id):
            # The caller's own ID, as written: the SV's id does not hold it, and it traces
            # the SV to the caller's record. A record without one keeps the annotation it
            # had, so the evidence a classification froze on it does not drift.
            annotation["id"] = item.record_id
        annotation.update({"ref": item.ref, "alt": item.alt, "info": item.info})
        gene_symbols = _split_gene_symbols(item.info.get("Genes"))
        filt = item.filt
        ranks = call_ranks.setdefault(variant_id, {})
        existing = merged.get(variant_id)
        if existing is None:
            ranks.update(
                (call.sample, _needlr_call_rank(call, own=call.sample == item.query_sample))
                for call in item.calls
            )
            merged[variant_id] = StructuralVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr=normalize_chromosome(item.chrom),
                start=item.start,
                end=item.end,
                sv_type=item.sv_type,
                source="needlr",
                remote_chr=item.remote_chr,
                remote_start=item.remote_start,
                remote_end=item.remote_start,
                # The SVLEN as written gave the END; the one stored is a sequence allele's own
                # length without it (CLIN-41), the length its event states.
                sv_len=record_sv_len(sv_len=item.sv_len, ref=item.ref, alt=item.alt),
                filters=[] if filt is None else [filt],
                gene_symbols=gene_symbols,
                annotations=[annotation],
                calls=sorted(item.calls, key=lambda call: call.sample),
            )
            continue
        call_by_sample = {call.sample: call for call in existing.calls}
        for call in item.calls:
            rank = _needlr_call_rank(call, own=call.sample == item.query_sample)
            if call.sample not in ranks or rank >= ranks[call.sample]:
                call_by_sample[call.sample] = call
                ranks[call.sample] = rank
        # Every line under one id has the same ALT (a clash got a discriminator above),
        # so the record keeps the mate its first line gave it.
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
        sv_type = _first_info_value(info, "SVTYPE") or sv_type_from_alt(ref, alt) or "CNV"
        sv_len = _coerce_int(_first_info_value(info, "SVLEN"))
        # Without END where VCF 4.4 puts it, and a breakend with its mate, as every SV reader
        # reads them (CLIN-39).
        end = record_end(
            pos=start,
            ref=ref,
            alt=alt,
            sv_len=sv_len,
            info=info,
            chrom=chrom,
            end=_first_info_value(info, "END", "End_Pos"),
        )
        if end is None or _unpositionable(start, end):
            continue
        remote_chr, remote_start = breakend_mate(sv_type=sv_type, alt=alt, info=info, chrom=chrom)
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
        # Named by its coordinates, type and mate, and its source, as every other reader
        # names a call, never by the record's ID, which a caller numbers per run: two
        # samples' calls of one event share it (CLIN-39). The ID stays in the annotation.
        variant_id = build_structural_variant_id(
            chrom,
            start,
            end,
            sv_type,
            remote_chr=remote_chr,
            remote_start=remote_start,
            remote_end=remote_start,
            source=source,
        )
        annotation: dict[str, Any] = {"source": source}
        if not _missing_scalar(record_id):
            annotation["id"] = record_id
        annotation.update({"ref": ref, "alt": alt, "info": info})
        records.append(
            StructuralVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr=normalize_chromosome(chrom),
                start=start,
                end=end,
                sv_type=sv_type,
                source=source,
                remote_chr=remote_chr,
                remote_start=remote_start,
                remote_end=remote_start,
                sv_len=record_sv_len(sv_len=sv_len, ref=ref, alt=alt),
                filters=[] if filt is None else filt.split(";"),
                gene_symbols=gene_symbols,
                annotations=[annotation],
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
    filename: str | dict[str, str],
) -> None:
    """Record ``source``'s SV file on each sample: one family file for every sample, or
    ``{sample id: file name}`` for the samples of a per-sample dataset."""
    filenames = (
        filename
        if isinstance(filename, dict)
        else {sample_id: filename for sample_id in sample_contexts}
    )
    for sample_id, sample_filename in filenames.items():
        sample_context = sample_contexts.get(sample_id)
        if sample_context is None:
            continue
        result = await session.execute(
            text("SELECT metadata FROM samples WHERE id = CAST(:sample_id AS uuid)"),
            {"sample_id": sample_context.sample_uuid},
        )
        metadata = _metadata_dict(result.scalar_one_or_none())
        sv_files = dict(metadata.get("sv_files") or {})
        sv_files[source] = sample_filename
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
        sv_type = (_first_info_value(info, "SVTYPE") or sv_type_from_alt(ref, alt) or "SV").upper()
        sv_len = _coerce_int(_first_info_value(info, "SVLEN"))
        # Without END where VCF 4.4 puts it, and a breakend with its mate, as every SV reader
        # reads them (CLIN-39).
        end = record_end(pos=start, ref=ref, alt=alt, sv_len=sv_len, info=info, chrom=chrom)
        if end is None or _unpositionable(start, end):
            continue
        remote_chr, remote_start = breakend_mate(sv_type=sv_type, alt=alt, info=info, chrom=chrom)
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
        variant_id = build_structural_variant_id(
            chrom,
            start,
            end,
            sv_type,
            remote_chr=remote_chr,
            remote_start=remote_start,
            remote_end=remote_start,
            source=MITO_SV_SOURCE,
        )
        records.append(
            StructuralVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr=normalize_chromosome(chrom),
                start=start,
                end=end,
                sv_type=sv_type,
                source=MITO_SV_SOURCE,
                remote_chr=remote_chr,
                remote_start=remote_start,
                remote_end=remote_start,
                sv_len=record_sv_len(sv_len=sv_len, ref=ref, alt=alt),
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
