from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import gzip
import io
import json
import os
import sqlite3
import tempfile
from typing import Any, Awaitable, Callable, Literal

from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .bed_service import get_track_presence_by_sample
from .clickhouse_family_variants import (
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
    _fetch_structural_variant_rows,
)
from .clickhouse_variant_storage import (
    build_small_variant_id,
    build_structural_variant_id,
    count_family_small_variants,
    delete_family_small_variants,
    insert_small_variant_records,
    replace_family_structural_variants,
)
from .clickhouse_interval_tracks import (
    delete_interval_track_sources,
    delete_interval_tracks,
    insert_interval_track_rows,
    upsert_interval_track_source,
)
from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from .family_variant_filters import StructuralVariantQueryFilters
from .structural_variant_ingest import (
    StructuralVariantRecordFormat,
    iter_structural_variant_records,
)
from .variant_annotation_parser import (
    AnnotationHeaderState,
    extract_small_variant_annotations,
    normalize_small_variant_annotation_entry,
    update_annotation_header_state,
)

SmallVariantFormat = Literal["auto", "clair3", "glimpse2"]
StructuralVariantFormat = Literal["auto", "manual", "sniffles", "spectre"]


@dataclass(slots=True)
class VepAnnotationLookup:
    by_variant_id: dict[str, list[dict[str, Any]]] | None
    by_locus_allele: dict[tuple[str, int, str], list[dict[str, Any]]] | None
    row_count: int
    conn: sqlite3.Connection | None = None
    temp_path: str | None = None

    def get(self, variant_id: str, chrom: str, start: int, alt: str) -> list[dict[str, Any]] | None:
        if self.conn is not None:
            rows = self.conn.execute(
                "SELECT annotation_json FROM annotations WHERE key_type = ? AND key_value = ?",
                ("variant_id", variant_id),
            ).fetchall()
            if rows:
                return [json.loads(row[0]) for row in rows]
            locus_key = f"{chrom}:{start}:{alt}"
            rows = self.conn.execute(
                "SELECT annotation_json FROM annotations WHERE key_type = ? AND key_value = ?",
                ("locus_allele", locus_key),
            ).fetchall()
            return [json.loads(row[0]) for row in rows] or None

        exact = (self.by_variant_id or {}).get(variant_id)
        if exact:
            return exact
        return (self.by_locus_allele or {}).get((chrom, start, alt))

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self.temp_path:
            try:
                os.unlink(self.temp_path)
            except FileNotFoundError:
                pass
            self.temp_path = None


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
    contents = await file.read()
    try:
        return contents.decode()
    except UnicodeDecodeError:
        try:
            return gzip.decompress(contents).decode()
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{kind} file must be plain text or gzipped",
            ) from exc


def _iter_upload_text_lines(file: UploadFile, *, kind: str):
    raw = file.file
    try:
        raw.seek(0)
    except (AttributeError, OSError):
        raise HTTPException(status_code=400, detail=f"{kind} file is not seekable")

    magic = raw.read(2)
    raw.seek(0)
    is_gzip = magic == b"\x1f\x8b" or (file.filename or "").endswith(".gz")
    try:
        if is_gzip:
            with gzip.open(raw, mode="rt", encoding="utf-8", errors="replace") as handle:
                yield from handle
        else:
            wrapper = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            try:
                yield from wrapper
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


def _parse_vep_uploaded_variation(value: str) -> tuple[str, int, str | None, str | None] | None:
    parts = value.strip().split("_", 2)
    if len(parts) < 2:
        return None
    chrom = normalize_chromosome(parts[0])
    try:
        start = int(parts[1])
    except ValueError:
        return None
    ref: str | None = None
    alt: str | None = None
    if len(parts) == 3 and "/" in parts[2]:
        ref_value, alt_value = parts[2].split("/", 1)
        ref = ref_value or None
        alt = alt_value or None
    return chrom, start, ref, alt


def _parse_vep_location(value: str) -> tuple[str, int] | None:
    if not value or ":" not in value:
        return None
    chrom_value, position_value = value.split(":", 1)
    start_text = position_value.split("-", 1)[0].replace(",", "")
    try:
        return normalize_chromosome(chrom_value), int(start_text)
    except ValueError:
        return None


def _append_annotation(
    mapping: dict[Any, list[dict[str, Any]]],
    key: Any,
    annotation: dict[str, Any],
) -> None:
    if not annotation:
        return
    mapping.setdefault(key, []).append(annotation)


def _sqlite_annotation_lookup() -> VepAnnotationLookup:
    temp_file = tempfile.NamedTemporaryFile(prefix="coga-vep-", suffix=".sqlite3", delete=False)
    temp_file.close()
    conn = sqlite3.connect(temp_file.name)
    conn.execute(
        "CREATE TABLE annotations (key_type TEXT NOT NULL, key_value TEXT NOT NULL, annotation_json TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX idx_annotations_key ON annotations (key_type, key_value)")
    return VepAnnotationLookup(
        by_variant_id=None,
        by_locus_allele=None,
        row_count=0,
        conn=conn,
        temp_path=temp_file.name,
    )


def _store_vep_annotation(
    lookup: VepAnnotationLookup,
    *,
    key_type: str,
    key_value: str,
    annotation: dict[str, Any],
) -> None:
    if lookup.conn is not None:
        lookup.conn.execute(
            "INSERT INTO annotations (key_type, key_value, annotation_json) VALUES (?, ?, ?)",
            (key_type, key_value, json.dumps(annotation)),
        )
        return
    if key_type == "variant_id":
        target = lookup.by_variant_id if lookup.by_variant_id is not None else {}
        _append_annotation(target, key_value, annotation)


def _parse_vep_tsv_annotation_lines(
    lines: Any,
    *,
    sqlite_backed: bool,
) -> VepAnnotationLookup:
    header: list[str] | None = None
    lookup = (
        _sqlite_annotation_lookup()
        if sqlite_backed
        else VepAnnotationLookup(by_variant_id={}, by_locus_allele={}, row_count=0)
    )
    row_count = 0

    for raw_line in lines:
        line = raw_line.rstrip("\n\r")
        if not line:
            continue
        if line.startswith("##"):
            continue
        if line.startswith("#"):
            header = line.lstrip("#").split("\t")
            continue
        if header is None:
            continue
        values = line.split("\t")
        row = {key: value for key, value in zip(header, values)}
        annotation = normalize_small_variant_annotation_entry(row)
        if not annotation:
            continue
        row_count += 1

        uploaded = _parse_vep_uploaded_variation(row.get("Uploaded_variation", ""))
        allele = row.get("Allele") or None
        if uploaded is not None:
            chrom, start, ref, alt = uploaded
            if ref and alt:
                _store_vep_annotation(
                    lookup,
                    key_type="variant_id",
                    key_value=build_small_variant_id(chrom, start, ref, alt),
                    annotation=annotation,
                )
            if allele:
                if lookup.conn is not None:
                    _store_vep_annotation(
                        lookup,
                        key_type="locus_allele",
                        key_value=f"{chrom}:{start}:{allele}",
                        annotation=annotation,
                    )
                else:
                    target = lookup.by_locus_allele if lookup.by_locus_allele is not None else {}
                    _append_annotation(target, (chrom, start, allele), annotation)
            elif alt:
                if lookup.conn is not None:
                    _store_vep_annotation(
                        lookup,
                        key_type="locus_allele",
                        key_value=f"{chrom}:{start}:{alt}",
                        annotation=annotation,
                    )
                else:
                    target = lookup.by_locus_allele if lookup.by_locus_allele is not None else {}
                    _append_annotation(target, (chrom, start, alt), annotation)
            continue

        location = _parse_vep_location(row.get("Location", ""))
        if location is not None and allele:
            chrom, start = location
            if lookup.conn is not None:
                _store_vep_annotation(
                    lookup,
                    key_type="locus_allele",
                    key_value=f"{chrom}:{start}:{allele}",
                    annotation=annotation,
                )
            else:
                target = lookup.by_locus_allele if lookup.by_locus_allele is not None else {}
                _append_annotation(target, (chrom, start, allele), annotation)

    if header is None:
        lookup.close()
        raise HTTPException(status_code=400, detail="VEP TSV annotation file is missing a header row")
    lookup.row_count = row_count
    if lookup.conn is not None:
        lookup.conn.commit()
    return lookup


def _parse_vep_tsv_annotations(text_value: str) -> VepAnnotationLookup:
    return _parse_vep_tsv_annotation_lines(text_value.splitlines(), sqlite_backed=False)


def _parse_vep_tsv_annotation_upload(file: UploadFile) -> VepAnnotationLookup:
    return _parse_vep_tsv_annotation_lines(
        _iter_upload_text_lines(file, kind="VEP TSV annotation"),
        sqlite_backed=True,
    )


def _parse_format(format_field: str, sample_field: str) -> dict[str, str]:
    keys = format_field.split(":")
    values = sample_field.split(":")
    return {k: v for k, v in zip(keys, values)}


def _parse_float_list(value: str | None) -> list[float]:
    if value in (None, ".", ""):
        return []
    parsed: list[float] = []
    for item in value.split(","):
        if item and item != ".":
            parsed.append(float(item))
    return parsed


def _parse_int_list(value: str | None) -> list[int]:
    if value in (None, ".", ""):
        return []
    parsed: list[int] = []
    for item in value.split(","):
        if not item:
            continue
        parsed.append(0 if item == "." else int(item))
    return parsed


def _detect_small_variant_format(text: str, format_hint: SmallVariantFormat) -> Literal["clair3", "glimpse2"]:
    if format_hint != "auto":
        return format_hint
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 9:
            break
        fmt = parts[8].split(":")
        if "GP" in fmt:
            return "glimpse2"
        return "clair3"
    raise HTTPException(status_code=400, detail="No valid VCF records found")


def _detect_small_variant_format_from_upload(
    file: UploadFile,
    format_hint: SmallVariantFormat,
) -> Literal["clair3", "glimpse2"]:
    if format_hint != "auto":
        return format_hint
    for line in _iter_upload_text_lines(file, kind="VCF"):
        if not line or line.startswith("#"):
            continue
        parts = line.rstrip("\n\r").split("\t")
        if len(parts) < 9:
            break
        fmt = parts[8].split(":")
        return "glimpse2" if "GP" in fmt else "clair3"
    raise HTTPException(status_code=400, detail="No valid VCF records found")


def _detect_structural_variant_format(
    text: str,
    filename: str | None,
    format_hint: StructuralVariantFormat,
) -> StructuralVariantRecordFormat:
    if format_hint != "auto":
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


def _haplotype_state_end(
    state: dict[str, Any],
    *,
    next_chrom: str | None,
    next_start: int | None,
    chromosome_sizes: dict[str, int],
) -> int:
    state_start = int(state["start"])
    state_last_pos = int(state["last_pos"] or state_start)
    state_chrom = normalize_chromosome(str(state["chr"]))
    if next_chrom is not None and normalize_chromosome(next_chrom) == state_chrom:
        return max(int(next_start or state_start), state_start + 1)
    chrom_size = chromosome_sizes.get(state_chrom)
    if chrom_size is not None:
        return max(chrom_size, state_last_pos + 1)
    return max(state_last_pos + 1, state_start + 1)


def _haplotype_row(
    sample_context: SampleMetadataContext,
    *,
    chrom: str,
    start: int,
    end: int,
    hap1: str,
    hap2: str,
    ps: int,
    metadata_json: str,
) -> dict[str, Any]:
    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id or "",
        "track_type": "haplotype",
        "source": "glimpse2",
        "chr": normalize_chromosome(chrom),
        "start": start,
        "end": end,
        "hap1": hap1,
        "hap2": hap2,
        "ps": ps,
        "metadata_json": metadata_json,
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
) -> dict[str, Any]:
    if not context.assembly_name:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve a single assembly for this family",
        )

    vep_annotations: VepAnnotationLookup | None = None
    if annotation_file is not None:
        vep_annotations = _parse_vep_tsv_annotation_upload(annotation_file)
    try:
        resolved_format = _detect_small_variant_format_from_upload(file, format_hint)
        existing_variants = await count_family_small_variants(
            context.assembly_name,
            context.family_uuid,
            project_ids=context.project_ids,
        )
        existing_haplotypes = len(
            await get_track_presence_by_sample(
                session,
                context=context,
                track_type="haplotype",
                chromosomes=[str(value) for value in range(1, 23)] + ["X", "Y", "M"],
            )
        )
        if existing_variants or existing_haplotypes:
            if not overwrite:
                raise HTTPException(
                    status_code=409,
                    detail="Small variants or haplotype blocks already exist for this family",
                )
            await delete_family_small_variants(context.assembly_name, context.family_uuid)
            await _delete_family_haplotype_blocks(
                session,
                assembly_name=context.assembly_name,
                family_uuid=context.family_uuid,
            )

        sample_names: list[str] = []
        annotation_state = AnnotationHeaderState()
        inserted = 0
        last_reported = 0
        haplotype_rows: list[dict[str, Any]] = []
        hap_prev: dict[str, dict[str, Any]] = {}
        variant_batch: list[SmallVariantRecord] = []
        metadata_json = _upload_metadata(resolved_format, file)
        chromosome_sizes = (
            await _fetch_chromosome_sizes(session, context.assembly_id)
            if resolved_format == "glimpse2"
            else {}
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
            )
            variant_batch.clear()
            if progress is not None and inserted - last_reported >= 50000:
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
            if line.startswith("#CHROM"):
                header = line.strip().split("\t")
                sample_names = header[9:]
                unique_names = list(dict.fromkeys(sample_names))
                for name in unique_names:
                    if name not in sample_contexts:
                        raise HTTPException(status_code=400, detail=f"Sample '{name}' not found in family")
                    hap_prev[name] = {
                        "start": None,
                        "hap1": None,
                        "hap2": None,
                        "ps": None,
                        "chr": None,
                        "last_pos": None,
                    }
                continue
            if not line or line.startswith("#"):
                continue
            fields = line.strip().split("\t")
            if len(fields) < 10:
                continue

            chrom, pos, _vid, ref, alt, qual, filt, info_field, fmt = fields[:9]
            sample_fields = fields[9:]
            chrom = normalize_chromosome(chrom)
            start = int(pos)
            end = start + len(ref) - 1
            info = _parse_info(info_field)
            variant_id = build_small_variant_id(chrom, start, ref, alt)
            annotations = (
                vep_annotations.get(variant_id, chrom, start, alt)
                if vep_annotations
                else None
            ) or extract_small_variant_annotations(info, annotation_state)

            calls: list[SmallVariantCall] = []
            for sample_name, sample_field in zip(sample_names, sample_fields):
                fmt_vals = _parse_format(fmt, sample_field)
                gt_val = fmt_vals.get("GT", "./.")
                call = SmallVariantCall(
                    sample=sample_name,
                    gt=gt_val,
                    gq=_first_present_float(fmt_vals, "GQ"),
                    dp=_first_present_int(fmt_vals, "DP"),
                    af=_parse_float_list(fmt_vals.get("AF")),
                    ad=_parse_int_list(fmt_vals.get("AD")),
                    ps=_first_present_int(fmt_vals, "PS"),
                )
                calls.append(call)
                if resolved_format == "glimpse2":
                    state = hap_prev[sample_name]
                    ps_val = call.ps
                    if "|" in gt_val and ps_val is not None:
                        hap1, hap2 = gt_val.split("|", 1)
                        if state["start"] is None:
                            hap_prev[sample_name] = {
                                "start": start,
                                "hap1": hap1,
                                "hap2": hap2,
                                "ps": ps_val,
                                "chr": chrom,
                                "last_pos": start,
                            }
                        elif (
                            state["ps"] != ps_val
                            or state["hap1"] != hap1
                            or state["hap2"] != hap2
                            or state["chr"] != chrom
                        ):
                            sample_context = sample_contexts[sample_name]
                            haplotype_rows.append(
                                _haplotype_row(
                                    sample_context,
                                    chrom=str(state["chr"]),
                                    start=int(state["start"]),
                                    end=_haplotype_state_end(
                                        state,
                                        next_chrom=chrom,
                                        next_start=start,
                                        chromosome_sizes=chromosome_sizes,
                                    ),
                                    hap1=str(state["hap1"]),
                                    hap2=str(state["hap2"]),
                                    ps=int(state["ps"]),
                                    metadata_json=metadata_json,
                                )
                            )
                            hap_prev[sample_name] = {
                                "start": start,
                                "hap1": hap1,
                                "hap2": hap2,
                                "ps": ps_val,
                                "chr": chrom,
                                "last_pos": start,
                            }
                        else:
                            state["last_pos"] = start
                    elif state["start"] is not None:
                        sample_context = sample_contexts[sample_name]
                        haplotype_rows.append(
                            _haplotype_row(
                                sample_context,
                                chrom=str(state["chr"]),
                                start=int(state["start"]),
                                end=_haplotype_state_end(
                                    state,
                                    next_chrom=chrom,
                                    next_start=start,
                                    chromosome_sizes=chromosome_sizes,
                                ),
                                hap1=str(state["hap1"]),
                                hap2=str(state["hap2"]),
                                ps=int(state["ps"]),
                                metadata_json=metadata_json,
                            )
                        )
                        hap_prev[sample_name] = {
                            "start": None,
                            "hap1": None,
                            "hap2": None,
                            "ps": None,
                            "chr": None,
                            "last_pos": None,
                        }

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
                )
            )
            inserted += 1
            if len(variant_batch) >= 5000:
                await flush_variant_batch()

        if inserted == 0:
            raise HTTPException(status_code=400, detail="No valid small-variant records found")

        await flush_variant_batch()

        if resolved_format == "glimpse2":
            for sample_name, state in hap_prev.items():
                if state["start"] is None:
                    continue
                sample_context = sample_contexts[sample_name]
                haplotype_rows.append(
                    _haplotype_row(
                        sample_context,
                        chrom=str(state["chr"]),
                        start=int(state["start"]),
                        end=_haplotype_state_end(
                            state,
                            next_chrom=None,
                            next_start=None,
                            chromosome_sizes=chromosome_sizes,
                        ),
                        hap1=str(state["hap1"]),
                        hap2=str(state["hap2"]),
                        ps=int(state["ps"]),
                        metadata_json=metadata_json,
                    )
                )

        await _insert_haplotype_rows(
            session,
            assembly_name=context.assembly_name,
            rows=haplotype_rows,
            sample_contexts=sample_contexts,
            filename=file.filename or "",
            metadata_json=metadata_json,
        )
        await session.commit()
        result = {
            "inserted": inserted,
            "haplotypes_inserted": len(haplotype_rows),
            "source_format": resolved_format,
            "annotation_rows": vep_annotations.row_count if vep_annotations else 0,
            "annotation_source": "vep_tsv" if vep_annotations else None,
            "insert_batch_size": 5000,
        }
        if progress is not None:
            await progress(result)
        return result
    finally:
        if vep_annotations is not None:
            vep_annotations.close()


async def _lookup_structural_gene_symbols(
    session: AsyncSession,
    *,
    assembly_id: str | None,
    chrom: str,
    start: int,
    end: int,
) -> list[str]:
    if not assembly_id:
        return []
    result = await session.execute(
        text(
            """
            SELECT DISTINCT hgnc_symbol
            FROM genes
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND chr = :chr
              AND start < :window_end
              AND "end" > :window_start
            ORDER BY hgnc_symbol
            """
        ),
        {
            "assembly_id": assembly_id,
            "chr": normalize_chromosome(chrom),
            "window_start": start,
            "window_end": end,
        },
    )
    return [str(row[0]) for row in result.all() if row[0]]


def _structural_record_call(
    sample_id: str,
    record: Any,
) -> StructuralVariantCall:
    return StructuralVariantCall(
        sample=sample_id,
        gt=str(record.gt or "./."),
        qual=record.qual,
        read_support=_first_present_int(record.info, "SUPPORT", "RE", "READS"),
        filter=None if record.filter in (None, "", ".") else str(record.filter),
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
    source_label = "manual_upload" if resolved_format == "manual" else resolved_format
    existing_records = await _fetch_structural_variant_rows(
        family_context,
        StructuralVariantQueryFilters(page=1, page_size=1, source=source_label),
    )
    sample_has_existing = any(
        any(call.sample == sample_context.sample_id for call in record.calls)
        for record in existing_records
    )
    if sample_has_existing and not overwrite:
        raise HTTPException(
            status_code=409,
            detail="Structural variants already exist for this sample and source",
        )

    merged: dict[str, StructuralVariantRecord] = {}
    for existing in existing_records:
        remaining_calls = [call for call in existing.calls if call.sample != sample_context.sample_id]
        if remaining_calls:
            merged[existing.variant_id] = replace(existing, calls=remaining_calls)

    processed = 0
    created = 0
    merged_count = 0
    for parsed in iter_structural_variant_records(text_value, resolved_format):
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
        gene_symbols = await _lookup_structural_gene_symbols(
            session,
            assembly_id=sample_context.assembly_id,
            chrom=parsed.chrom,
            start=parsed.start,
            end=parsed.end,
        )
        record = merged.get(variant_id)
        if record is None:
            merged[variant_id] = StructuralVariantRecord(
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
            created += 1
            continue
        updated_calls = [*record.calls, call]
        merged[variant_id] = replace(
            record,
            filters=list(dict.fromkeys([*record.filters, *([] if parsed.filter in (None, "", ".") else [str(parsed.filter)])])),
            gene_symbols=list(dict.fromkeys([*record.gene_symbols, *gene_symbols])),
            calls=sorted(updated_calls, key=lambda item: item.sample),
        )
        merged_count += 1

    if processed == 0:
        raise HTTPException(status_code=400, detail="No valid structural-variant records found")

    await replace_family_structural_variants(
        family_context.assembly_name,
        family_context.family_uuid,
        family_context.project_ids,
        list(merged.values()),
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
    await session.commit()
    return {
        "processed": processed,
        "created": created,
        "merged": merged_count,
        "source_format": resolved_format,
    }
