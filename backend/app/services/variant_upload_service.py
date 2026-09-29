from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import io
import itertools
import json
import logging
import math
from typing import Any, Awaitable, Callable, Literal, Sequence

from fastapi import HTTPException, UploadFile
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from .annotation_table_parser import VepAnnotationLookup, _coerce_int, _parse_vep_tsv_annotation_lines
from .bed_service import get_track_presence_by_sample
from .upload_safety import decode_upload_text
from .clickhouse_variant_records import (
    SmallVariantCall,
    SmallVariantRecord,
    StoredStructuralVariantRow,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from .clickhouse_variant_ids import build_small_variant_id, build_structural_variant_id
from .clickhouse_variant_rows import _normalized_project_ids
from .clickhouse_variant_storage import (
    count_family_small_variants,
    delete_family_small_variants,
    fetch_family_structural_variant_rows,
    insert_small_variant_records,
    refresh_family_small_variant_summaries,
    rewrite_family_structural_variants,
)
from .clickhouse_interval_tracks import (
    delete_interval_track_sources,
    delete_interval_tracks,
    insert_interval_track_rows,
    upsert_interval_track_source,
)
from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from .haplotype_block_builder import HaplotypeBlockBuilder
from .structural_variant_ingest import (
    ParsedStructuralVariant,
    StructuralVariantRecordFormat,
    iter_structural_variant_records,
)
from .variant_annotation_parser import (
    AnnotationHeaderState,
    extract_small_variant_annotations,
    update_annotation_header_state,
)
from .vcf_header_provenance import (
    extract_header_provenance,
    extract_info_description_provenance,
    merge_module_maps,
)

# Meta-header lines that occur in bulk (one per contig/field) and carry no
# provenance — skipped from the capture buffer so only version-bearing lines are
# kept (the parser also ignores them, this just bounds memory).
_PROVENANCE_SKIP_PREFIXES = (
    "##FORMAT",
    "##FILTER",
    "##contig",
    "##ALT",
    "##SAMPLE",
    "##PEDIGREE",
    "##GVCFBlock",
)
_PROVENANCE_HEADER_CAP = 200

# The value doubles as the ClickHouse ``source`` tag, which scopes deletes and
# re-imports. "clair3" is the historical label for *the family's primary, directly
# called nuclear callset* whatever produced it (DeepVariant on the long-read
# pipeline) -- the actual caller and its version are recorded per family in the
# annotation manifest from the VCF header, which is the traceable record. "mito" is a
# separate source so re-importing the nuclear callset never deletes the chrM calls
# (they come from a different file and a different caller run).
SmallVariantFormat = Literal["auto", "clair3", "glimpse2", "mito"]
ResolvedSmallVariantFormat = Literal["clair3", "glimpse2", "mito"]
StructuralVariantFormat = Literal["auto", "manual", "sniffles", "spectre"]
# The ClickHouse ``source`` label each per-sample SV upload format is stored under. The
# upload's "already exists" check, its merge and its delete all match this label exactly,
# so one upload reads and rewrites only its own callset's rows and never the package
# imports' ("needlr", "hificnv") or another upload format's.
STRUCTURAL_VARIANT_SOURCE_LABELS: dict[str, str] = {
    "manual": "manual_upload",
    "sniffles": "sniffles",
    "spectre": "spectre",
}
SMALL_VARIANT_UPLOAD_BATCH_SIZE = 1_000
SMALL_VARIANT_PROGRESS_INTERVAL = 10_000
# Upper bound on a single streamed upload line. Real VCF lines are KB-scale even with
# thousands of samples; this only trips on a pathological un-newlined / bomb line.
MAX_UPLOAD_LINE_BYTES = 16 * 1024 * 1024

logger = logging.getLogger(__name__)


def _upload_metadata(source: str, file: UploadFile) -> str:
    return json.dumps(
        {
            "source": source,
            "filename": file.filename,
            "uploaded_from": "web",
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
    )


async def _decode_upload_text(file: UploadFile, *, kind: str) -> str:
    return await decode_upload_text(file, kind=kind)


def _iter_bounded_lines(handle, *, kind: str):
    # `for line in handle` buffers a whole line before yielding, so a VCF with no
    # newlines (or a gzip that inflates to one enormous line) could exhaust memory
    # even though the file streams. readline(cap+1) stops at the cap: a chunk that
    # reaches the cap without a trailing newline is a too-long line and is rejected.
    while True:
        line = handle.readline(MAX_UPLOAD_LINE_BYTES + 1)
        if not line:
            break
        if len(line) > MAX_UPLOAD_LINE_BYTES and not line.endswith("\n"):
            raise HTTPException(
                status_code=413,
                detail=f"{kind} file contains a line exceeding the maximum allowed length",
            )
        yield line


def _iter_upload_text_lines(file: UploadFile, *, kind: str):
    raw = file.file
    try:
        raw.seek(0)
    except (AttributeError, OSError):
        raise HTTPException(status_code=400, detail=f"{kind} file is not seekable") from None

    magic = raw.read(2)
    raw.seek(0)
    is_gzip = magic == b"\x1f\x8b" or (file.filename or "").endswith(".gz")
    try:
        if is_gzip:
            with gzip.open(raw, mode="rt", encoding="utf-8", errors="replace") as handle:
                yield from _iter_bounded_lines(handle, kind=kind)
        else:
            wrapper = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            try:
                yield from _iter_bounded_lines(wrapper, kind=kind)
            finally:
                wrapper.detach()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"{kind} file must be plain text or gzipped") from exc


def _parse_info(info_field: str) -> dict[str, str]:
    info: dict[str, str] = {}
    if info_field and info_field != ".":
        for item in info_field.split(";"):
            if "=" in item:
                key, value = item.split("=", 1)
                info[key] = value
    return info


def _parse_vep_tsv_annotation_upload(file: UploadFile) -> VepAnnotationLookup:
    return _parse_vep_tsv_annotation_lines(
        _iter_upload_text_lines(file, kind="VEP TSV annotation"),
    )


def _parse_format(format_field: str, sample_field: str) -> dict[str, str]:
    keys = format_field.split(":")
    values = sample_field.split(":")
    # A VCF sample field may legitimately carry fewer values than the FORMAT keys
    # (trailing per-sample fields dropped). zip() already truncates to the shorter of
    # the two, so keys without a matching value are simply absent from the mapping —
    # never paired with a value from the wrong FORMAT key.
    return {k: v for k, v in zip(keys, values)}


def _parse_float_list(value: str | None) -> list[float]:
    # Tolerant of malformed entries: a single bad AF value skips that entry rather than
    # aborting the whole ingest with a 500 (mirrors the package-import coercion path).
    if value in (None, ".", ""):
        return []
    parsed: list[float] = []
    for item in value.split(","):
        if item and item != ".":
            try:
                number = float(item)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                parsed.append(number)
    return parsed


def _parse_int_list(value: str | None) -> list[int]:
    # Tolerant of malformed entries (see _parse_float_list); a missing/bad token maps
    # to 0 so positional AD alignment is preserved without raising.
    if value in (None, ".", ""):
        return []
    parsed: list[int] = []
    for item in value.split(","):
        if not item:
            continue
        parsed.append(_coerce_int(item) or 0)
    return parsed


def _parse_qual(value: str | None) -> float | None:
    """Parse the site-level VCF QUAL column (a float, or '.'/'' when missing)."""
    if value in (None, ".", ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _format_has_phased_gt(format_keys: list[str], sample_fields: list[str]) -> bool:
    try:
        gt_index = format_keys.index("GT")
    except ValueError:
        return False
    for sample_field in sample_fields:
        values = sample_field.split(":")
        if gt_index < len(values) and "|" in values[gt_index]:
            return True
    return False


def _has_phasing_source_hint(header_lines: list[str], filename: str | None = None) -> bool:
    header_preview = "\n".join(header_lines).lower()
    file_name = (filename or "").lower()
    return any(
        marker in header_preview or marker in file_name
        for marker in ("glimpse", "shapeit", "phased")
    )


def _detect_small_variant_format(
    text: str,
    format_hint: SmallVariantFormat,
) -> ResolvedSmallVariantFormat:
    if format_hint != "auto":
        return format_hint
    header_lines: list[str] = []
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("#"):
            header_lines.append(line)
            continue
        parts = line.split("\t")
        if len(parts) < 9:
            break
        fmt = parts[8].split(":")
        if "GP" in fmt or (
            _has_phasing_source_hint(header_lines)
            and _format_has_phased_gt(fmt, parts[9:])
        ):
            return "glimpse2"
        return "clair3"
    raise HTTPException(status_code=400, detail="No valid VCF records found")


def _detect_small_variant_format_from_upload(
    file: UploadFile,
    format_hint: SmallVariantFormat,
) -> ResolvedSmallVariantFormat:
    if format_hint != "auto":
        return format_hint
    header_lines: list[str] = []
    for line in _iter_upload_text_lines(file, kind="VCF"):
        if not line:
            continue
        if line.startswith("#"):
            header_lines.append(line.rstrip("\n\r"))
            continue
        parts = line.rstrip("\n\r").split("\t")
        if len(parts) < 9:
            break
        fmt = parts[8].split(":")
        if "GP" in fmt or (
            _has_phasing_source_hint(header_lines, file.filename)
            and _format_has_phased_gt(fmt, parts[9:])
        ):
            return "glimpse2"
        return "clair3"
    raise HTTPException(status_code=400, detail="No valid VCF records found")


def _detect_structural_variant_format(
    text: str,
    filename: str | None,
    format_hint: StructuralVariantFormat,
) -> StructuralVariantRecordFormat:
    if format_hint != "auto":
        # The router passes the query value through unchecked, and the label it maps to
        # scopes this upload's read and delete: accept the upload's own formats only.
        if format_hint not in STRUCTURAL_VARIANT_SOURCE_LABELS:
            raise HTTPException(
                status_code=400,
                detail="Unsupported structural-variant source_format; use auto, manual, sniffles or spectre",
            )
        return format_hint
    file_name = (filename or "").lower()
    if file_name.endswith(".tsv") or file_name.endswith(".txt"):
        return "manual"

    header_preview = "\n".join(line.lower() for line in text.splitlines()[:20])
    if "spectre" in file_name or "##source=spectre" in header_preview:
        return "spectre"
    if "sniffles" in file_name or "##source=sniffles" in header_preview:
        return "sniffles"

    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 8:
            return "manual"
        pos = parts[1]
        info = _parse_info(parts[7])
        end_val = info.get("END", "")
        if ":" in pos or ":" in end_val:
            return "spectre"
        return "sniffles"
    raise HTTPException(status_code=400, detail="No valid structural variant records found")


def _first_present_int(mapping: dict[str, str], *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if value in (None, "", "."):
            continue
        try:
            return int(float(value))
        except ValueError:
            continue
    return None


def _first_present_float(mapping: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        value = mapping.get(key)
        if value in (None, "", "."):
            continue
        try:
            return float(value)
        except ValueError:
            continue
    return None


async def _delete_family_haplotype_blocks(
    session: AsyncSession,
    *,
    assembly_name: str,
    family_uuid: str,
) -> None:
    await delete_interval_tracks(
        assembly_name,
        family_uuid=family_uuid,
        track_type="haplotype",
    )
    await delete_interval_track_sources(
        session,
        family_uuid=family_uuid,
        track_type="haplotype",
    )


async def _fetch_chromosome_sizes(
    session: AsyncSession,
    assembly_id: str | None,
) -> dict[str, int]:
    if not assembly_id:
        return {}
    result = await session.execute(
        text(
            """
            SELECT chr, size
            FROM chromosomes
            WHERE assembly_id = CAST(:assembly_id AS uuid)
            """
        ),
        {"assembly_id": assembly_id},
    )
    return {
        normalize_chromosome(str(row["chr"])): int(row["size"])
        for row in result.mappings().all()
        if row["chr"] is not None and row["size"] is not None
    }


async def _insert_haplotype_rows(
    session: AsyncSession,
    *,
    assembly_name: str,
    rows: list[dict[str, Any]],
    sample_contexts: dict[str, SampleMetadataContext],
    filename: str,
    metadata_json: str,
) -> None:
    if not rows:
        return
    await insert_interval_track_rows(assembly_name, rows)
    sample_context_by_uuid = {
        sample_context.sample_uuid: sample_context
        for sample_context in sample_contexts.values()
    }
    counts_by_sample: dict[str, int] = {}
    for row in rows:
        sample_uuid = str(row["sample_id"])
        counts_by_sample[sample_uuid] = counts_by_sample.get(sample_uuid, 0) + 1
    metadata = json.loads(metadata_json)
    for sample_uuid, row_count in counts_by_sample.items():
        sample_context = sample_context_by_uuid.get(sample_uuid)
        if sample_context is None:
            continue
        await upsert_interval_track_source(
            session,
            sample_context=sample_context,
            track_type="haplotype",
            source="glimpse2",
            filename=filename,
            row_count=row_count,
            metadata=metadata,
        )


async def upload_family_small_variant_file(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
    file: UploadFile,
    overwrite: bool,
    format_hint: SmallVariantFormat,
    annotation_file: UploadFile | None = None,
    progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    sample_aliases: dict[str, str] | None = None,
    exclude_filters: Sequence[str] | None = None,
    vep_annotations: VepAnnotationLookup | None = None,
) -> dict[str, Any]:
    """Load a family's small-variant VCF into ClickHouse.

    ``sample_aliases`` maps a VCF ``#CHROM`` sample column onto a family sample id, for
    callers that name the column after the input file rather than the sample (the
    long-read CNV caller writes ``Sample0``, TRGT writes ``<sample>_sort``). Without it
    such a column is rejected as "not found in family".

    ``exclude_filters`` drops records carrying any of the named FILTER values before
    they are stored. The long-read DeepVariant callset marks reference blocks and
    no-call sites with ``RefCall``/``NoCall``; those are half of a 10.7M-record
    whole-genome VCF and carry no variant to review.

    ``vep_annotations`` supplies an already-parsed annotation lookup, for callers whose
    annotation file is not a VEP TSV (the mitochondrial callset is annotated by
    mutserve). It is mutually exclusive with ``annotation_file``, which parses one.
    """
    if not context.assembly_name:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve a single assembly for this family",
        )

    alias_map = {str(key): str(value) for key, value in (sample_aliases or {}).items()}
    excluded_filters = {str(value).strip() for value in (exclude_filters or []) if str(value).strip()}
    skipped_filtered = 0
    if annotation_file is not None:
        vep_annotations = await asyncio.to_thread(
            _parse_vep_tsv_annotation_upload,
            annotation_file,
        )
    annotation_version = "vep_tsv" if vep_annotations is not None else "vcf_info"
    # Defined before the try so the compensating except can scope its cleanup to this
    # upload's own source even if detection itself is what failed.
    resolved_format: SmallVariantFormat | None = None
    # Only compensate once we've entered the mutating phase (past the "refuse if data
    # already exists and not overwrite" 409). Otherwise a non-overwrite conflict would
    # trigger a delete of the very rows the refusal was protecting.
    mutation_started = False
    try:
        resolved_format = _detect_small_variant_format_from_upload(file, format_hint)
        # A family holds more than one small-variant callset at once (annotated
        # clair3 SNVs plus imputed glimpse2 genotypes), distinguished by ``source``.
        # Scope the overwrite to this upload's own source so re-importing one loader
        # never deletes the other's rows. Haplotype blocks belong to glimpse2 only.
        loads_haplotype_blocks = resolved_format == "glimpse2"
        existing_variants = await count_family_small_variants(
            context.assembly_name,
            context.family_uuid,
            project_ids=context.project_ids,
            source=resolved_format,
        )
        existing_haplotypes = (
            len(
                await get_track_presence_by_sample(
                    session,
                    context=context,
                    track_type="haplotype",
                    chromosomes=[str(value) for value in range(1, 23)] + ["X", "Y", "M"],
                )
            )
            if loads_haplotype_blocks
            else 0
        )
        if existing_variants or existing_haplotypes:
            if not overwrite:
                raise HTTPException(
                    status_code=409,
                    detail="Small variants or haplotype blocks already exist for this family",
                )
            await delete_family_small_variants(
                context.assembly_name, context.family_uuid, source=resolved_format
            )
            if loads_haplotype_blocks:
                await _delete_family_haplotype_blocks(
                    session,
                    assembly_name=context.assembly_name,
                    family_uuid=context.family_uuid,
                )

        # Past the conflict gate: from here any failure may have flushed rows (or, in
        # overwrite mode, already pre-cleared) so cleaning this source is now correct.
        mutation_started = True
        sample_names: list[str] = []
        annotation_state = AnnotationHeaderState()
        provenance_header_lines: list[str] = []
        inserted = 0
        skipped_malformed = 0
        last_reported = 0
        variant_batch: list[SmallVariantRecord] = []
        metadata_json = _upload_metadata(resolved_format, file)
        # Haplotype blocks come only from the imputed glimpse2 genotypes.
        haplotype_blocks = (
            HaplotypeBlockBuilder(
                context=context,
                sample_contexts=sample_contexts,
                chromosome_sizes=await _fetch_chromosome_sizes(session, context.assembly_id),
                metadata_json=metadata_json,
            )
            if resolved_format == "glimpse2"
            else None
        )

        async def flush_variant_batch() -> None:
            nonlocal last_reported
            if not variant_batch:
                return
            await insert_small_variant_records(
                context.assembly_name or "",
                context.family_uuid,
                context.project_ids,
                variant_batch,
                annotation_version=annotation_version,
            )
            variant_batch.clear()
            if progress is not None and inserted - last_reported >= SMALL_VARIANT_PROGRESS_INTERVAL:
                last_reported = inserted
                await progress(
                    {
                        "processed": inserted,
                        "inserted": inserted,
                        "annotation_rows": vep_annotations.row_count if vep_annotations else 0,
                    }
                )

        for line in _iter_upload_text_lines(file, kind="VCF"):
            if line.startswith("##INFO"):
                update_annotation_header_state(annotation_state, line.strip())
            elif (
                line.startswith("##")
                and not line.startswith(_PROVENANCE_SKIP_PREFIXES)
                and len(provenance_header_lines) < _PROVENANCE_HEADER_CAP
            ):
                provenance_header_lines.append(line.rstrip())
            if line.startswith("#CHROM"):
                header = line.strip().split("\t")
                # Rewrite each VCF sample column to its family sample id up front, so
                # every downstream lookup (calls, haplotype state, parent roles) works
                # in family-sample-id space and never has to know about the alias.
                sample_names = [alias_map.get(name, name) for name in header[9:]]
                unique_names = list(dict.fromkeys(sample_names))
                for name in unique_names:
                    if name not in sample_contexts:
                        raise HTTPException(status_code=400, detail=f"Sample '{name}' not found in family")
                    if haplotype_blocks is not None:
                        haplotype_blocks.add_sample(name)
                continue
            if not line or line.startswith("#"):
                continue
            fields = line.strip().split("\t")
            if len(fields) < 10:
                continue

            chrom, pos, _vid, ref, alt, qual, filt, info_field, fmt = fields[:9]
            # Drop caller-declared non-variant records (DeepVariant RefCall/NoCall)
            # before any parsing work: they are half of a whole-genome long-read VCF
            # and carry nothing to review. A record is excluded only when every one of
            # its FILTER values is excluded, so a genuine variant that also picked up
            # an excluded flag is kept.
            if excluded_filters and filt not in {"", "."}:
                record_filters = [value for value in filt.split(";") if value]
                if record_filters and all(value in excluded_filters for value in record_filters):
                    skipped_filtered += 1
                    continue
            sample_fields = fields[9:]
            # A data row whose sample-column count doesn't match the #CHROM header is
            # malformed: a bare zip() would silently truncate to the shorter side and
            # attach genotype calls to the wrong samples. Skip it (counted) instead.
            if sample_names and len(sample_fields) != len(sample_names):
                skipped_malformed += 1
                continue
            chrom = normalize_chromosome(chrom)
            # A malformed POS can't position the variant. Skip the row (counted +
            # reported below) rather than aborting the whole ingest with a 500 and
            # leaving partially-flushed rows behind (mirrors the package-import path,
            # which coerces + skips unparseable records).
            start = _coerce_int(pos)
            if start is None:
                skipped_malformed += 1
                continue
            end = start + len(ref) - 1
            info = _parse_info(info_field)
            variant_id = build_small_variant_id(chrom, start, ref, alt)
            annotations = (
                vep_annotations.get(variant_id, chrom, start, ref, alt)
                if vep_annotations
                else None
            ) or extract_small_variant_annotations(info, annotation_state)

            calls: list[SmallVariantCall] = []
            calls_by_sample: dict[str, SmallVariantCall] = {}
            for sample_name, sample_field in zip(sample_names, sample_fields):
                fmt_vals = _parse_format(fmt, sample_field)
                gt_val = fmt_vals.get("GT", "./.")
                call = SmallVariantCall(
                    sample=sample_name,
                    gt=gt_val,
                    gq=_first_present_float(fmt_vals, "GQ"),
                    dp=_first_present_int(fmt_vals, "DP", "MED_DP", "MIN_DP"),
                    # DeepVariant writes the per-allele alt fraction as VAF, not AF.
                    # On chrM that fraction *is* the heteroplasmy level the mtDNA
                    # workspace reads out of calls.af, so it must not be dropped.
                    af=_parse_float_list(fmt_vals.get("AF") or fmt_vals.get("VAF")),
                    ad=_parse_int_list(fmt_vals.get("AD")),
                    ps=_first_present_int(fmt_vals, "PS"),
                )
                calls.append(call)
                calls_by_sample[sample_name] = call
            if haplotype_blocks is not None:
                haplotype_blocks.observe(
                    chrom=chrom,
                    start=start,
                    sample_names=sample_names,
                    calls=calls,
                    calls_by_sample=calls_by_sample,
                )

            variant_batch.append(
                SmallVariantRecord(
                    variant_key=None,
                    variant_id=variant_id,
                    chr=chrom,
                    start=start,
                    end=end,
                    ref=ref,
                    alt=alt,
                    source=resolved_format,
                    rsid=info.get("RS") or info.get("dbSNP") or None,
                    filters=[] if filt in {"", "."} else [filt],
                    gene_symbols=[],
                    annotations=annotations,
                    calls=calls,
                    qual=_parse_qual(qual),
                )
            )
            inserted += 1
            if len(variant_batch) >= SMALL_VARIANT_UPLOAD_BATCH_SIZE:
                await flush_variant_batch()

        if inserted == 0:
            detail = "No valid small-variant records found"
            if skipped_filtered:
                detail = (
                    f"No small-variant records remained after excluding FILTER "
                    f"{sorted(excluded_filters)} ({skipped_filtered} record(s) dropped)"
                )
            raise HTTPException(status_code=400, detail=detail)

        await flush_variant_batch()
        await refresh_family_small_variant_summaries(
            context.assembly_name,
            context.family_uuid,
        )

        haplotype_rows = haplotype_blocks.finish() if haplotype_blocks is not None else []

        await _insert_haplotype_rows(
            session,
            assembly_name=context.assembly_name,
            rows=haplotype_rows,
            sample_contexts=sample_contexts,
            filename=file.filename or "",
            metadata_json=metadata_json,
        )
        # Capture annotation/tool provenance from the VCF header into the family's
        # annotation manifest (best-effort; never fails the upload). Joins this
        # transaction so provenance commits iff the variants do.
        from .annotation_manifest_service import merge_vcf_header_provenance

        annotation_provenance = extract_header_provenance(
            provenance_header_lines, modality="snv"
        ).as_modules()
        if vep_annotations is not None and vep_annotations.provenance_modules:
            # The VCF header carries the caller; a separate VEP TSV carries the
            # annotation-database releases (VEP/gnomAD/ClinVar/…). Combine both.
            annotation_provenance = merge_module_maps(
                vep_annotations.provenance_modules, annotation_provenance
            )
        await merge_vcf_header_provenance(
            session,
            family_uuid=context.family_uuid,
            assembly_id=getattr(context, "assembly_id", None),
            modules=annotation_provenance,
            modality="snv",
        )
        await session.commit()
        if skipped_malformed:
            logger.warning(
                "Skipped %d malformed small-variant row(s) (unparseable POS) during "
                "upload for family %s",
                skipped_malformed,
                context.family_id,
            )
        result = {
            "inserted": inserted,
            "skipped_malformed": skipped_malformed,
            "skipped_filtered": skipped_filtered,
            "excluded_filters": sorted(excluded_filters),
            "haplotypes_inserted": len(haplotype_rows),
            "source_format": resolved_format,
            "annotation_rows": vep_annotations.row_count if vep_annotations else 0,
            "annotation_source": "vep_tsv" if vep_annotations else None,
            "annotation_version": annotation_version,
            "annotation_provenance": annotation_provenance,
            "insert_batch_size": SMALL_VARIANT_UPLOAD_BATCH_SIZE,
        }
        if progress is not None:
            await progress(result)
        return result
    except Exception:
        # Any failure after rows have started flushing (a DB error mid-batch, a
        # malformed annotation, etc.) must not leave partially-flushed ClickHouse rows
        # queryable under this family — the package-import path compensates, and the
        # direct-upload path must too. Best-effort delete of this upload's own source,
        # then re-raise the original error unchanged. Skipped before the mutating phase
        # so a non-overwrite 409 never deletes the pre-existing rows it was protecting.
        if mutation_started and context.assembly_name and resolved_format is not None:
            try:
                await delete_family_small_variants(
                    context.assembly_name, context.family_uuid, source=resolved_format
                )
                if resolved_format == "glimpse2":
                    await _delete_family_haplotype_blocks(
                        session,
                        assembly_name=context.assembly_name,
                        family_uuid=context.family_uuid,
                    )
            except Exception:  # noqa: BLE001 - cleanup must not mask the original error
                logger.warning(
                    "Failed to clean up partial small-variant rows for family %s "
                    "after an upload error",
                    context.family_id,
                    exc_info=True,
                )
        raise
    finally:
        if vep_annotations is not None:
            vep_annotations.close()


async def _fetch_genes_for_chroms(
    session: AsyncSession,
    *,
    assembly_id: str | None,
    chroms: list[str],
) -> dict[str, list[tuple[int, int, str]]]:
    """Load (start, end, hgnc_symbol) for every gene on the given (normalized)
    chromosomes, grouped by chromosome, for in-memory interval overlap. One query
    replaces the previous per-structural-variant-record range query."""
    genes_by_chrom: dict[str, list[tuple[int, int, str]]] = {}
    if not assembly_id or not chroms:
        return genes_by_chrom
    stmt = text(
        """
        SELECT chr, start, "end", hgnc_symbol
        FROM genes
        WHERE assembly_id = CAST(:assembly_id AS uuid)
          AND chr IN :chroms
          AND hgnc_symbol IS NOT NULL
        """
    ).bindparams(bindparam("chroms", expanding=True))
    result = await session.execute(
        stmt,
        {"assembly_id": assembly_id, "chroms": chroms},
    )
    for chr_value, start, end, symbol in result.all():
        if not symbol:
            continue
        genes_by_chrom.setdefault(str(chr_value), []).append((int(start), int(end), str(symbol)))
    return genes_by_chrom


def _gene_symbols_for_window(
    genes_by_chrom: dict[str, list[tuple[int, int, str]]],
    *,
    chrom: str,
    start: int,
    end: int,
) -> list[str]:
    """Distinct, sorted gene symbols overlapping [start, end) on ``chrom`` —
    the in-memory equivalent of the old ``start < window_end AND end > window_start``
    range query."""
    return sorted(
        {
            symbol
            for gene_start, gene_end, symbol in genes_by_chrom.get(chrom, ())
            if gene_start < end and gene_end > start
        }
    )


def _structural_record_call(
    sample_id: str,
    record: ParsedStructuralVariant,
) -> StructuralVariantCall:
    return StructuralVariantCall(
        sample=sample_id,
        gt=str(record.gt or "./."),
        qual=record.qual,
        read_support=_first_present_int(record.info, "SUPPORT", "RE", "READS"),
        filter=None if record.filter in (None, "", ".") else str(record.filter),
        phase_set=record.phase_set,
    )


def _with_uploaded_calls(
    record: StructuralVariantRecord, uploaded: StructuralVariantRecord
) -> StructuralVariantRecord:
    """``record`` with the uploaded sample's calls added. Its own fields are kept, the
    filters and genes are joined, and the calls are sorted by sample."""
    return replace(
        record,
        filters=list(dict.fromkeys([*record.filters, *uploaded.filters])),
        gene_symbols=list(dict.fromkeys([*record.gene_symbols, *uploaded.gene_symbols])),
        calls=sorted([*record.calls, *uploaded.calls], key=lambda item: item.sample),
    )


def _rows_with_uploaded_calls(
    stored_rows: Sequence[StoredStructuralVariantRow],
    other_calls_by_row: Sequence[list[StructuralVariantCall]],
    uploaded: dict[str, StructuralVariantRecord],
    *,
    project_ids: Sequence[str],
) -> list[StoredStructuralVariantRow]:
    """The source's rows after a per-sample upload.

    Each stored row keeps its project and the other samples' calls as stored, and takes the
    uploaded calls for its SV; a row with no other call is replaced by the uploaded record,
    or dropped when the upload does not have that SV. An uploaded SV gets a row in each of
    the family's projects that has none, with the stored record's fields when another
    project holds it.
    """
    rows: list[StoredStructuralVariantRow] = []
    covered: set[tuple[str, str]] = set()
    templates: dict[str, StructuralVariantRecord] = {}
    for row, other_calls in zip(stored_rows, other_calls_by_row):
        variant_id = row.record.variant_id
        new = uploaded.get(variant_id)
        if other_calls:
            record = replace(row.record, calls=other_calls)
            templates.setdefault(variant_id, record)
            if new is not None:
                record = _with_uploaded_calls(record, new)
        elif new is not None:
            record = replace(new, variant_key=row.record.variant_key)
        else:
            continue
        if new is not None:
            covered.add((row.project_id, variant_id))
        rows.append(replace(row, record=record))
    for project_id in project_ids:
        for variant_id, new in uploaded.items():
            if (project_id, variant_id) in covered:
                continue
            template = templates.get(variant_id)
            record = new if template is None else _with_uploaded_calls(replace(template, calls=[]), new)
            rows.append(StoredStructuralVariantRow(project_id=project_id, record=record))
    return rows


def _structural_variant_header_provenance(text_value: str) -> dict[str, dict[str, Any]]:
    """The tool and database versions a structural-variant VCF's meta-header names: the
    caller (``##source=Sniffles2_2.2``, ``##source=Spectre``), ``##reference``, and the
    database releases in the ``##INFO`` descriptions, as the NeedlR package import reads
    them. A manual TSV has no meta-header and yields nothing."""
    header_lines = list(itertools.takewhile(lambda line: line.startswith("##"), io.StringIO(text_value)))
    return merge_module_maps(
        extract_header_provenance(header_lines, modality="sv").as_modules(),
        extract_info_description_provenance(header_lines),
    )


async def upload_structural_variant_file(
    session: AsyncSession,
    *,
    family_context: FamilyMetadataContext,
    sample_context: SampleMetadataContext,
    file: UploadFile,
    overwrite: bool,
    format_hint: StructuralVariantFormat,
) -> dict[str, Any]:
    if not family_context.assembly_name:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve a single assembly for this family",
        )

    text_value = await _decode_upload_text(file, kind="Structural variant")
    resolved_format = _detect_structural_variant_format(text_value, file.filename, format_hint)
    source_label = STRUCTURAL_VARIANT_SOURCE_LABELS[resolved_format]
    # A stored call names its sample by id or by uuid.
    sample_ids = {value for value in (sample_context.sample_id, sample_context.sample_uuid) if value}
    # This source's rows as stored, in every project and with every call: the conflict
    # check is about them, and they are exactly what the source-scoped rewrite below
    # deletes. Everything in them but this sample's calls is written back unchanged.
    stored_rows = await fetch_family_structural_variant_rows(
        family_context.assembly_name,
        family_context.family_uuid,
        source=source_label,
    )
    sample_has_existing = any(
        call.sample in sample_ids for row in stored_rows for call in row.record.calls
    )
    if sample_has_existing and not overwrite:
        raise HTTPException(
            status_code=409,
            detail="Structural variants already exist for this sample and source",
        )
    other_calls_by_row = [
        [call for call in row.record.calls if call.sample not in sample_ids] for row in stored_rows
    ]
    variants_with_other_calls = {
        row.record.variant_id for row, calls in zip(stored_rows, other_calls_by_row) if calls
    }

    uploaded: dict[str, StructuralVariantRecord] = {}
    parsed_records = list(iter_structural_variant_records(text_value, resolved_format))
    # Resolve overlapping gene symbols up front: one query for the genes on the
    # chromosomes this file touches, then in-memory interval overlap, instead of a
    # per-record range query against `genes`.
    genes_by_chrom = await _fetch_genes_for_chroms(
        session,
        assembly_id=sample_context.assembly_id,
        chroms=sorted({normalize_chromosome(record.chrom) for record in parsed_records}),
    )
    gene_symbol_cache: dict[tuple[str, int, int], list[str]] = {}

    processed = 0
    created = 0
    merged_count = 0
    for parsed in parsed_records:
        processed += 1
        variant_id = build_structural_variant_id(
            parsed.chrom,
            parsed.start,
            parsed.end,
            parsed.svtype,
            remote_chr=parsed.remote_chr,
            remote_start=parsed.remote_start,
            remote_end=parsed.remote_end,
        )
        call = _structural_record_call(sample_context.sample_id, parsed)
        window_key = (normalize_chromosome(parsed.chrom), int(parsed.start), int(parsed.end))
        gene_symbols = gene_symbol_cache.get(window_key)
        if gene_symbols is None:
            gene_symbols = _gene_symbols_for_window(
                genes_by_chrom,
                chrom=window_key[0],
                start=window_key[1],
                end=window_key[2],
            )
            gene_symbol_cache[window_key] = gene_symbols
        record = uploaded.get(variant_id)
        if record is None:
            uploaded[variant_id] = StructuralVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr=normalize_chromosome(parsed.chrom),
                start=int(parsed.start),
                end=int(parsed.end),
                sv_type=str(parsed.svtype or ""),
                source=source_label,
                remote_chr=normalize_chromosome(parsed.remote_chr) if parsed.remote_chr else None,
                remote_start=parsed.remote_start,
                remote_end=parsed.remote_end,
                sv_len=parsed.svlen,
                filters=[] if parsed.filter in (None, "", ".") else [str(parsed.filter)],
                gene_symbols=gene_symbols,
                annotations=[{"info": parsed.info}] if parsed.info else [],
                calls=[call],
            )
            if variant_id in variants_with_other_calls:
                merged_count += 1
            else:
                created += 1
            continue
        updated_calls = [*record.calls, call]
        uploaded[variant_id] = replace(
            record,
            filters=list(dict.fromkeys([*record.filters, *([] if parsed.filter in (None, "", ".") else [str(parsed.filter)])])),
            gene_symbols=list(dict.fromkeys([*record.gene_symbols, *gene_symbols])),
            calls=sorted(updated_calls, key=lambda item: item.sample),
        )
        merged_count += 1

    if processed == 0:
        raise HTTPException(status_code=400, detail="No valid structural-variant records found")

    await rewrite_family_structural_variants(
        family_context.assembly_name,
        family_context.family_uuid,
        _rows_with_uploaded_calls(
            stored_rows,
            other_calls_by_row,
            uploaded,
            project_ids=_normalized_project_ids(family_context.project_ids),
        ),
        source=source_label,
    )
    metadata_result = await session.execute(
        text("SELECT metadata FROM samples WHERE id = CAST(:sample_id AS uuid)"),
        {"sample_id": sample_context.sample_uuid},
    )
    metadata = dict(metadata_result.scalar_one_or_none() or {})
    sv_files = dict(metadata.get("sv_files") or {})
    sv_files[source_label] = file.filename or ""
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
    # The caller and database versions the VCF header names go into the family's
    # annotation manifest, as for a small-variant upload (best-effort; never fails the
    # upload). It joins this transaction, so it commits with the upload's records.
    from .annotation_manifest_service import merge_vcf_header_provenance

    annotation_provenance = _structural_variant_header_provenance(text_value)
    await merge_vcf_header_provenance(
        session,
        family_uuid=family_context.family_uuid,
        assembly_id=family_context.assembly_id,
        modules=annotation_provenance,
        modality="sv",
    )
    await session.commit()
    return {
        "processed": processed,
        "created": created,
        "merged": merged_count,
        "source_format": resolved_format,
        "annotation_provenance": annotation_provenance,
    }
