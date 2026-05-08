from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable, Iterator, Literal, Optional

from .data_scope import normalize_chromosome

StructuralVariantRecordFormat = Literal["manual", "sniffles", "spectre"]

BND_ALT_RE = re.compile(r"[\[\]]?([^:\[\]]+):(\d+)[\[\]]?")


@dataclass(frozen=True)
class StructuralVariantRecord:
    variant_id: str
    chrom: str
    start: int
    end: int
    ref: str
    alt: str
    svtype: str
    gt: str
    info: Dict[str, str]
    qual: float | None = None
    filter: str | None = None
    svlen: int | None = None
    remote_chr: str | None = None
    remote_start: int | None = None
    remote_end: int | None = None


def parse_info(info_field: str) -> Dict[str, str]:
    info: Dict[str, str] = {}
    if info_field and info_field != ".":
        for item in info_field.split(";"):
            if "=" in item:
                key, value = item.split("=", 1)
                info[key] = value
    return info


def parse_format(format_field: str, sample_field: str) -> Dict[str, str]:
    keys = format_field.split(":")
    values = sample_field.split(":")
    return {key: value for key, value in zip(keys, values)}


def parse_bnd_alt(alt: str) -> tuple[Optional[str], Optional[int]]:
    match = BND_ALT_RE.search(alt)
    if match:
        chrom, pos = match.groups()
        return normalize_chromosome(chrom), int(pos)
    return None, None


def split_chrom_pos(chrom: str, pos: str) -> tuple[str, int]:
    if ":" in pos:
        chrom_from_pos, pos = pos.split(":", 1)
        chrom = chrom_from_pos or chrom
    if "-" in pos:
        pos, _ = pos.split("-", 1)
    if not pos.isdigit():
        raise ValueError(f"Unparsable POS field: {pos}")
    return chrom, int(pos)


def parse_end(end_val: str) -> int:
    if ":" in end_val:
        _, end_val = end_val.split(":", 1)
    if "-" in end_val:
        _, end_val = end_val.split("-", 1)
    return int(end_val)


def _iter_manual_records(lines: Iterable[str]) -> Iterator[StructuralVariantRecord]:
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.strip().split()
        if len(parts) < 8:
            continue
        variant_id, chrom, start_s, end_s, ref, alt, svtype, gt = parts[:8]
        remote_chr: str | None = None
        remote_start: int | None = None
        remote_end: int | None = None
        if svtype == "BND":
            remote_chr, remote_start = parse_bnd_alt(alt)
            if remote_start is not None:
                remote_end = remote_start
        yield StructuralVariantRecord(
            variant_id=variant_id,
            chrom=normalize_chromosome(chrom),
            start=int(start_s),
            end=int(end_s),
            ref=ref,
            alt=alt,
            svtype=svtype,
            gt=gt,
            info={},
            remote_chr=remote_chr,
            remote_start=remote_start,
            remote_end=remote_end,
        )


def _iter_sniffles_records(lines: Iterable[str]) -> Iterator[StructuralVariantRecord]:
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.strip().split("\t")
        if len(parts) < 10:
            continue

        chrom, pos, variant_id, ref, alt, qual, filt, info_f, fmt, sample_f = parts[:10]
        info = parse_info(info_f)
        fmt_vals = parse_format(fmt, sample_f)
        svtype = info.get("SVTYPE", alt.strip("<>"))
        remote_chr: str | None = None
        remote_start: int | None = None
        remote_end: int | None = None

        if svtype == "BND":
            remote_chr, remote_start = parse_bnd_alt(alt)
            if remote_start is not None:
                remote_end = remote_start

        yield StructuralVariantRecord(
            variant_id=variant_id,
            chrom=normalize_chromosome(chrom),
            start=int(pos),
            end=int(info.get("END", pos)),
            ref=ref,
            alt=alt,
            svtype=svtype,
            gt=fmt_vals.get("GT", "./."),
            info=info,
            qual=float(qual) if qual not in {"", "."} else None,
            filter=filt or None,
            svlen=int(info["SVLEN"]) if info.get("SVLEN") not in (None, ".") else None,
            remote_chr=remote_chr,
            remote_start=remote_start,
            remote_end=remote_end,
        )


def _iter_spectre_records(lines: Iterable[str]) -> Iterator[StructuralVariantRecord]:
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.strip().split("\t")
        if len(parts) < 10:
            continue

        chrom_raw, pos, variant_id, ref, alt, qual, filt, info_f, fmt, sample_f = parts[:10]
        info = parse_info(info_f)
        fmt_vals = parse_format(fmt, sample_f)
        chrom, start = split_chrom_pos(chrom_raw, pos)

        yield StructuralVariantRecord(
            variant_id=variant_id,
            chrom=normalize_chromosome(chrom),
            start=start,
            end=parse_end(info.get("END", pos)),
            ref=ref,
            alt=alt,
            svtype=info.get("SVTYPE", alt.strip("<>")),
            gt=fmt_vals.get("GT", "./."),
            info=info,
            qual=float(qual) if qual not in {"", "."} else None,
            filter=filt or None,
            svlen=int(info["SVLEN"]) if info.get("SVLEN") not in (None, ".") else None,
        )


def iter_structural_variant_records(
    text: str,
    record_format: StructuralVariantRecordFormat,
) -> Iterator[StructuralVariantRecord]:
    lines = text.splitlines()
    if record_format == "manual":
        return _iter_manual_records(lines)
    if record_format == "sniffles":
        return _iter_sniffles_records(lines)
    if record_format == "spectre":
        return _iter_spectre_records(lines)
    raise ValueError(f"Unsupported structural variant record format: {record_format}")


def structural_variant_identity_query(
    *,
    scope: Dict[str, Any],
    source: str,
    record: StructuralVariantRecord,
) -> Dict[str, Any]:
    return {
        "family_id": scope.get("family_id"),
        "assembly_id": scope.get("assembly_id"),
        "source": source,
        "chr": record.chrom,
        "start": record.start,
        "end": record.end,
        "type": record.svtype,
        "remote_chr": record.remote_chr,
        "remote_start": record.remote_start,
        "remote_end": record.remote_end,
    }


def structural_variant_identity_key(
    *,
    scope: Dict[str, Any],
    source: str,
    record: StructuralVariantRecord,
) -> tuple[Any, ...]:
    return (
        scope.get("family_id"),
        scope.get("assembly_id"),
        source,
        record.chrom,
        record.start,
        record.end,
        record.svtype,
        record.remote_chr,
        record.remote_start,
        record.remote_end,
    )


def build_structural_variant_metadata_key(source: str) -> str:
    return (
        f"metadata.sv_files.{source}"
        if source in {"sniffles", "spectre"}
        else "metadata.sv_files.manual_upload"
    )


async def clear_sample_source_structural_variants(
    *,
    variant_collection: Any,
    sample_id: Any,
    source: str,
) -> None:
    await variant_collection.update_many(
        {"genotypes.sample_id": sample_id, "source": source},
        {"$pull": {"genotypes": {"sample_id": sample_id}}},
    )
    await variant_collection.delete_many({"genotypes": {"$size": 0}})


def _normalized_filter_value(value: str | None) -> str | None:
    if value in (None, "", "."):
        return None
    return value


def _build_genotype_payload(
    *,
    record: StructuralVariantRecord,
    sample_id: Any,
    include_empty_metrics: bool,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"sample_id": sample_id, "gt": record.gt}
    if include_empty_metrics:
        payload["qual"] = record.qual
        payload["filter"] = record.filter or "."
        payload["info"] = record.info
        return payload

    if record.qual is not None:
        payload["qual"] = record.qual
    normalized_filter = _normalized_filter_value(record.filter)
    if normalized_filter is not None:
        payload["filter"] = normalized_filter
    if record.info:
        payload["info"] = record.info
    return payload


def _build_variant_document(
    *,
    record: StructuralVariantRecord,
    scope: Dict[str, Any],
    source: str,
    genotype_payload: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    document: Dict[str, Any] = {
        "variant_id": record.variant_id,
        "family_id": scope.get("family_id"),
        "assembly_id": scope.get("assembly_id"),
        "project_ids": scope.get("project_ids", []),
        "chr": record.chrom,
        "start": record.start,
        "end": record.end,
        "ref": record.ref,
        "alt": record.alt,
        "type": record.svtype,
        "source": source,
        "sv_len": record.svlen,
        "qual": record.qual,
        "filter": _normalized_filter_value(record.filter),
        "info": record.info,
        "annotations": [],
        "genotypes": [genotype_payload],
        "metadata": metadata,
        "uploaded_at": datetime.now(timezone.utc),
    }
    if record.remote_chr is not None:
        document.update(
            {
                "remote_chr": record.remote_chr,
                "remote_start": record.remote_start,
                "remote_end": record.remote_end,
            }
        )
    return document


async def _insert_variant_documents(variant_collection: Any, documents: list[Dict[str, Any]]) -> None:
    if not documents:
        return
    if hasattr(variant_collection, "insert_many"):
        await variant_collection.insert_many(documents)
        return
    for document in documents:
        await variant_collection.insert_one(document)


async def ingest_structural_variant_records(
    *,
    variant_collection: Any,
    records: Iterable[StructuralVariantRecord],
    sample_id: Any,
    scope: Dict[str, Any],
    source: str,
    metadata: Dict[str, Any],
    include_empty_genotype_metrics: bool = False,
) -> Dict[str, int]:
    processed = 0
    created = 0
    merged = 0
    seen_keys: set[tuple[Any, ...]] = set()
    new_documents: list[Dict[str, Any]] = []

    for record in records:
        key = structural_variant_identity_key(scope=scope, source=source, record=record)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        genotype_payload = _build_genotype_payload(
            record=record,
            sample_id=sample_id,
            include_empty_metrics=include_empty_genotype_metrics,
        )
        existing_variant = await variant_collection.find_one(
            structural_variant_identity_query(scope=scope, source=source, record=record)
        )
        if existing_variant is not None:
            await variant_collection.update_one(
                {"_id": existing_variant["_id"]},
                {"$addToSet": {"genotypes": genotype_payload}},
            )
            merged += 1
        else:
            new_documents.append(
                _build_variant_document(
                    record=record,
                    scope=scope,
                    source=source,
                    genotype_payload=genotype_payload,
                    metadata=metadata,
                )
            )
            created += 1
        processed += 1

    await _insert_variant_documents(variant_collection, new_documents)
    return {"processed": processed, "created": created, "merged": merged}
