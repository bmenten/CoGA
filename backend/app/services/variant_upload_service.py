from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import gzip
import io
import json
import logging
import math
import os
import sqlite3
import tempfile
from typing import Any, Awaitable, Callable, Literal, Sequence

from fastapi import HTTPException, UploadFile
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from .bed_service import get_track_presence_by_sample
from .upload_safety import decode_upload_text
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
    refresh_family_small_variant_summaries,
    replace_family_structural_variants,
)
from .clickhouse_interval_tracks import (
    delete_interval_track_sources,
    delete_interval_tracks,
    insert_interval_track_rows,
    upsert_interval_track_source,
)
from .data_scope import normalize_chromosome
from .family_package_common import _normalize_header_key
from .family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from .haplotype_lineage_service import build_pedigree, identify_core
from .family_variant_filters import StructuralVariantQueryFilters
from .structural_variant_ingest import (
    ParsedStructuralVariant,
    StructuralVariantRecordFormat,
    iter_structural_variant_records,
)
from .variant_annotation_parser import (
    AnnotationHeaderState,
    extract_small_variant_annotations,
    normalize_small_variant_annotation_entry,
    update_annotation_header_state,
)
from .vcf_header_provenance import (
    extract_header_provenance,
    extract_vep_tab_provenance,
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
SEGREGATION_HAPLOTYPE_SWITCH_MIN_MARKERS = 50
SEGREGATION_HAPLOTYPE_SWITCH_MIN_SPAN = 500_000
SMALL_VARIANT_UPLOAD_BATCH_SIZE = 1_000
SMALL_VARIANT_PROGRESS_INTERVAL = 10_000
# Upper bound on a single streamed upload line. Real VCF lines are KB-scale even with
# thousands of samples; this only trips on a pathological un-newlined / bomb line.
MAX_UPLOAD_LINE_BYTES = 16 * 1024 * 1024

logger = logging.getLogger(__name__)


def _coerce_int(value: Any) -> int | None:
    """Parse an integer, tolerating float-like text; None when unparseable/missing."""
    if value is None or str(value).strip() in {"", "."}:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None


@dataclass(slots=True)
class VepAnnotationLookup:
    row_count: int
    conn: sqlite3.Connection | None = None
    temp_path: str | None = None
    # Annotation/DB versions parsed from the VEP TSV's ``## … version …`` header
    # (where VEP/gnomAD/ClinVar/… releases live for TSV-annotated families).
    provenance_modules: dict[str, Any] | None = None

    def get(
        self, variant_id: str, chrom: str, start: int, ref: str, alt: str
    ) -> list[dict[str, Any]] | None:
        if self.conn is None:
            return None
        rows = self.conn.execute(
            "SELECT annotation_json FROM annotations WHERE key_type = ? AND key_value = ?",
            ("variant_id", variant_id),
        ).fetchall()
        if rows:
            return [json.loads(row[0]) for row in rows]
        # VEP left-aligns/trims indels, so its reported coordinate (and the
        # Uploaded_variation behind ``variant_id``) is shifted relative to the VCF
        # POS. Fall back to VEP's normalized Location + Allele representation, which
        # we can reconstruct from the VCF allele without a reference genome.
        locus_key = _vep_location_allele_key(chrom, start, ref, alt)
        if locus_key is None:
            return None
        rows = self.conn.execute(
            "SELECT annotation_json FROM annotations WHERE key_type = ? AND key_value = ?",
            ("locus_allele", locus_key),
        ).fetchall()
        return [json.loads(row[0]) for row in rows] or None

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
        raise HTTPException(status_code=400, detail=f"{kind} file is not seekable")

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


def _vep_location_allele_key(chrom: str, pos: int, ref: str, alt: str) -> str | None:
    """Reproduce VEP's ``{chrom}:{location}:{allele}`` for a VCF allele.

    VEP normalizes (left-aligns and trims the shared anchor base) before
    reporting a variant, so for indels its Location/Allele — and the
    Uploaded_variation it derives — sit at a different coordinate than the VCF
    POS. A VCF insertion ``A>AT`` is reported with Allele ``T`` and a deletion
    ``AC>A`` with Allele ``-`` at the shifted position. Reconstructing the same
    key here (no reference genome required) lets the annotation join find indels
    that the exact ``variant_id`` match misses.
    """
    if not ref or not alt:
        return None
    # Trim any shared suffix, keeping at least one base on each side.
    while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    prefix = 0
    while prefix < len(ref) and prefix < len(alt) and ref[prefix] == alt[prefix]:
        prefix += 1
    ref_rem, alt_rem = ref[prefix:], alt[prefix:]
    if not ref_rem and alt_rem:
        # Insertion: bases inserted just after the last shared base.
        return f"{chrom}:{pos + prefix - 1}:{alt_rem}"
    if ref_rem and not alt_rem:
        # Deletion: VEP marks the deleted span with a dash allele.
        return f"{chrom}:{pos + prefix}:-"
    if alt_rem:
        # SNV / MNV / substitution.
        return f"{chrom}:{pos + prefix}:{alt_rem}"
    return None


def _sqlite_annotation_lookup() -> VepAnnotationLookup:
    temp_file = tempfile.NamedTemporaryFile(prefix="coga-vep-", suffix=".sqlite3", delete=False)
    temp_file.close()
    conn = sqlite3.connect(temp_file.name, check_same_thread=False)
    conn.execute(
        "CREATE TABLE annotations (key_type TEXT NOT NULL, key_value TEXT NOT NULL, annotation_json TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX idx_annotations_key ON annotations (key_type, key_value)")
    return VepAnnotationLookup(
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
    if lookup.conn is None:
        return
    lookup.conn.execute(
        "INSERT INTO annotations (key_type, key_value, annotation_json) VALUES (?, ?, ?)",
        (key_type, key_value, json.dumps(annotation)),
    )


def _parse_vep_tsv_annotation_lines(lines: Any) -> VepAnnotationLookup:
    header: list[str] | None = None
    lookup = _sqlite_annotation_lookup()
    row_count = 0
    # VEP states its tool/database versions in the leading ``## … version …`` block
    # (before "## Column descriptions:"); buffer it for provenance capture.
    provenance_header: list[str] = []

    for raw_line in lines:
        line = raw_line.rstrip("\n\r")
        if not line:
            continue
        if line.startswith("##"):
            if len(provenance_header) < 80:
                provenance_header.append(line)
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
        location = _parse_vep_location(row.get("Location", ""))

        if uploaded is not None:
            chrom, start, ref, alt = uploaded
            if ref and alt:
                _store_vep_annotation(
                    lookup,
                    key_type="variant_id",
                    key_value=build_small_variant_id(chrom, start, ref, alt),
                    annotation=annotation,
                )

        # Index by VEP's normalized Location + Allele. VEP shifts/trims indels, so
        # the Uploaded_variation position differs from the VCF POS; keying on the
        # Location column (not the Uploaded_variation position) is what lets the
        # join recover indels — see _vep_location_allele_key for the lookup side.
        if location is not None and allele:
            loc_chrom, loc_start = location
            _store_vep_annotation(
                lookup,
                key_type="locus_allele",
                key_value=f"{loc_chrom}:{loc_start}:{allele}",
                annotation=annotation,
            )
        elif uploaded is not None:
            # No usable Location column: fall back to the Uploaded_variation
            # position (correct for SNVs, the best available for indels).
            chrom, start, ref, alt = uploaded
            fallback_allele = allele or alt
            if fallback_allele:
                _store_vep_annotation(
                    lookup,
                    key_type="locus_allele",
                    key_value=f"{chrom}:{start}:{fallback_allele}",
                    annotation=annotation,
                )

    if header is None:
        lookup.close()
        raise HTTPException(status_code=400, detail="VEP TSV annotation file is missing a header row")
    lookup.row_count = row_count
    lookup.provenance_modules = extract_vep_tab_provenance(provenance_header) or None
    if lookup.conn is not None:
        lookup.conn.commit()
    return lookup


# mutserve's mtDNA annotation columns, mapped onto the annotation keys the mtDNA
# workspace reads. Everything not listed here is still carried through verbatim (the
# annotation is stored as JSON), so haplogroup/selection/NuMT context is not lost.
# Keys are matched after _normalize_header_key, which drops separators and case.
_MUTSERVE_ANNOTATION_FIELDS = {
    _normalize_header_key(column): key
    for column, key in {
        "VariantLevel": "variant_level",
        "Coverage": "coverage",
        "MeanBaseQuality": "mean_base_quality",
        "Mutation": "mutation",
        "Substitution": "substitution",
        "Maplocus": "gene",
        "Category": "category",
        "Phylotree17_haplogroups": "haplogroup",
        "Phylotree17_clades": "haplogroup_clades",
        "HaploGrep2_weight": "haplogroup_weight",
        "AminoAcid": "amino_acid",
        "NewAminoAcid": "new_amino_acid",
        "AminoAcid_pos_protein": "amino_acid_position",
        "MutPred_Score": "mutpred_score",
        "mtDNA_Selection_Score": "mtdna_selection_score",
        "OXPHOS_complex": "oxphos_complex",
        "Helix_vaf_hom": "helix_af",
        "Helix_vaf_het": "helix_af_het",
        "Helix_count_hom": "helix_count_hom",
        "Helix_count_het": "helix_count_het",
        "NuMTs_dayama": "numts",
        "LowComplexityRegion": "low_complexity_region",
    }.items()
}

# The mutserve TSV's ID column holds the caller's internal sample label (literally
# "sample"), never a variant identifier. It must never be read as an rsid.
_MUTSERVE_IGNORED_COLUMNS = frozenset({"id", "filter"})


def _mutserve_annotation_row(row: dict[str, str]) -> dict[str, Any]:
    annotation: dict[str, Any] = {}
    for column, raw_value in row.items():
        key = _normalize_header_key(column)
        if key in _MUTSERVE_IGNORED_COLUMNS:
            continue
        value = (raw_value or "").strip()
        if value in {"", "."}:
            continue
        annotation[_MUTSERVE_ANNOTATION_FIELDS.get(key, key)] = value
    return annotation


def parse_mutserve_annotation_lines(lines: Any) -> VepAnnotationLookup:
    """Index a mutserve mtDNA annotation TSV by ``chrM`` variant id.

    This is a sibling of the VEP-TSV parser rather than a reuse of it: mutserve writes
    a bare (un-``#``-prefixed) header, has no ``Uploaded_variation``/``Location``
    columns to key on, and its ``ID`` column holds the caller's sample label, which the
    VEP parser's ``rsid`` alias list would otherwise store as every variant's rsid. The
    join key here is built from ``Pos``/``Ref``/``Variant`` against the chrM contig.
    """
    lookup = _sqlite_annotation_lookup()
    header: list[str] | None = None
    row_count = 0
    for raw_line in lines:
        line = raw_line.rstrip("\n\r")
        if not line or line.startswith("##"):
            continue
        values = line.split("\t")
        if header is None:
            header = [value.lstrip("#").strip() for value in values]
            continue
        row = {key: value for key, value in zip(header, values)}
        normalized = {_normalize_header_key(key): value for key, value in row.items()}
        position = _coerce_int(normalized.get("pos"))
        ref = (normalized.get("ref") or "").strip()
        alt = (normalized.get("variant") or normalized.get("alt") or "").strip()
        if position is None or not ref or not alt:
            continue
        annotation = _mutserve_annotation_row(row)
        if not annotation:
            continue
        row_count += 1
        _store_vep_annotation(
            lookup,
            key_type="variant_id",
            key_value=build_small_variant_id("M", position, ref, alt),
            annotation=annotation,
        )
    lookup.row_count = row_count
    if lookup.conn is not None:
        lookup.conn.commit()
    return lookup


def parse_mutserve_annotation_path(path) -> VepAnnotationLookup | None:
    """Parse a mutserve annotation TSV from disk, or None when it holds no rows.

    A header-only file is the normal outcome for a run with nothing to annotate (the
    mitochondrial SV annotation in this pipeline is routinely empty), so it returns
    None rather than an empty lookup that would suppress the VCF's own annotations.
    """
    if path is None or not path.is_file():
        return None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lookup = parse_mutserve_annotation_lines(handle)
    if lookup.row_count == 0:
        lookup.close()
        return None
    return lookup


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


def _phased_haplotype_alleles(gt_value: str | None) -> tuple[str, str] | None:
    if not gt_value or "|" not in gt_value:
        return None
    hap1, hap2 = gt_value.split("|", 1)
    if not hap1 or not hap2 or hap1 == "." or hap2 == ".":
        return None
    return hap1, hap2


def _new_haplotype_state(
    *,
    chrom: str,
    start: int,
    hap1: str,
    hap2: str,
    ps: int | None,
) -> dict[str, Any]:
    return {
        "start": start,
        "hap1": hap1,
        "hap2": hap2,
        "ps": ps,
        "chr": chrom,
        "last_pos": start,
    }


def _empty_haplotype_state() -> dict[str, Any]:
    return {
        "start": None,
        "hap1": None,
        "hap2": None,
        "ps": None,
        "chr": None,
        "last_pos": None,
    }


def _empty_segregation_side_state() -> dict[str, Any]:
    return {
        "chr": None,
        "hap": None,
        "pending_hap": None,
        "pending_start": None,
        "pending_last": None,
        "pending_count": 0,
    }


def _clear_segregation_pending(state: dict[str, Any]) -> None:
    state["pending_hap"] = None
    state["pending_start"] = None
    state["pending_last"] = None
    state["pending_count"] = 0


def _observe_segregation_haplotype(
    state: dict[str, Any],
    *,
    chrom: str,
    start: int,
    hap: str | None,
) -> tuple[int, str] | None:
    if hap not in {"0", "1"}:
        return None
    if state["chr"] != chrom:
        state["chr"] = chrom
        state["hap"] = hap
        _clear_segregation_pending(state)
        return start, hap
    if state["hap"] is None:
        state["hap"] = hap
        _clear_segregation_pending(state)
        return start, hap
    if hap == state["hap"]:
        _clear_segregation_pending(state)
        return None
    if state["pending_hap"] == hap:
        state["pending_count"] = int(state["pending_count"]) + 1
        state["pending_last"] = start
    else:
        state["pending_hap"] = hap
        state["pending_start"] = start
        state["pending_last"] = start
        state["pending_count"] = 1

    pending_start = int(state["pending_start"] or start)
    pending_last = int(state["pending_last"] or start)
    if (
        int(state["pending_count"]) >= SEGREGATION_HAPLOTYPE_SWITCH_MIN_MARKERS
        and pending_last - pending_start >= SEGREGATION_HAPLOTYPE_SWITCH_MIN_SPAN
    ):
        state["hap"] = hap
        _clear_segregation_pending(state)
        return pending_start, hap
    return None


def _confirmed_segregation_haplotype(state: dict[str, Any], chrom: str) -> str:
    if state["chr"] == chrom and state["hap"] in {"0", "1"}:
        return str(state["hap"])
    return "?"


def _haplotype_state_matches_block(
    state: dict[str, Any],
    *,
    chrom: str,
    ps: int | None,
) -> bool:
    if state["chr"] != chrom:
        return False
    if state["ps"] is not None or ps is not None:
        return state["ps"] == ps
    return True


def _haplotype_state_matches_segment(
    state: dict[str, Any],
    *,
    chrom: str,
    hap1: str,
    hap2: str,
    ps: int | None,
) -> bool:
    return (
        state["chr"] == chrom
        and state["hap1"] == hap1
        and state["hap2"] == hap2
        and state["ps"] == ps
    )


def _append_haplotype_state_row(
    rows: list[dict[str, Any]],
    sample_context: SampleMetadataContext,
    state: dict[str, Any],
    *,
    next_chrom: str | None,
    next_start: int | None,
    chromosome_sizes: dict[str, int],
    metadata_json: str,
) -> None:
    if state["start"] is None:
        return
    rows.append(
        _haplotype_row(
            sample_context,
            chrom=str(state["chr"]),
            start=int(state["start"]),
            end=_haplotype_state_end(
                state,
                next_chrom=next_chrom,
                next_start=next_start,
                chromosome_sizes=chromosome_sizes,
            ),
            hap1=str(state["hap1"]),
            hap2=str(state["hap2"]),
            ps=state["ps"],
            metadata_json=metadata_json,
        )
    )


def _update_haplotype_state(
    *,
    states: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    sample_contexts: dict[str, SampleMetadataContext],
    sample_name: str,
    chrom: str,
    start: int,
    hap1: str,
    hap2: str,
    ps: int | None,
    chromosome_sizes: dict[str, int],
    metadata_json: str,
    split_on_haplotype_change: bool,
) -> None:
    state = states[sample_name]
    if state["start"] is None:
        states[sample_name] = _new_haplotype_state(
            chrom=chrom,
            start=start,
            hap1=hap1,
            hap2=hap2,
            ps=ps,
        )
        return
    if split_on_haplotype_change:
        matches = _haplotype_state_matches_segment(
            state,
            chrom=chrom,
            hap1=hap1,
            hap2=hap2,
            ps=ps,
        )
    else:
        matches = _haplotype_state_matches_block(state, chrom=chrom, ps=ps)
    if matches:
        state["last_pos"] = start
        return
    _append_haplotype_state_row(
        rows,
        sample_contexts[sample_name],
        state,
        next_chrom=chrom,
        next_start=start,
        chromosome_sizes=chromosome_sizes,
        metadata_json=metadata_json,
    )
    states[sample_name] = _new_haplotype_state(
        chrom=chrom,
        start=start,
        hap1=hap1,
        hap2=hap2,
        ps=ps,
    )


def _close_haplotype_state(
    *,
    states: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    sample_contexts: dict[str, SampleMetadataContext],
    sample_name: str,
    next_chrom: str | None,
    next_start: int | None,
    chromosome_sizes: dict[str, int],
    metadata_json: str,
) -> None:
    state = states[sample_name]
    _append_haplotype_state_row(
        rows,
        sample_contexts[sample_name],
        state,
        next_chrom=next_chrom,
        next_start=next_start,
        chromosome_sizes=chromosome_sizes,
        metadata_json=metadata_json,
    )
    states[sample_name] = _empty_haplotype_state()


def _role_first_parent_names(
    context: FamilyMetadataContext,
) -> tuple[str | None, str | None]:
    """First sample (in ``sample_rows`` order) tagged with the flat ``father`` /
    ``mother`` role. Used only as a fallback when the pedigree cannot resolve the
    index couple."""
    father_name: str | None = None
    mother_name: str | None = None
    for row in context.sample_rows:
        role = str(row.get("role") or "").strip().lower()
        sample_name = str(row.get("sample_id") or "")
        if role == "father" and sample_name:
            father_name = father_name or sample_name
        elif role == "mother" and sample_name:
            mother_name = mother_name or sample_name
    return father_name, mother_name


def _parent_sample_names(context: FamilyMetadataContext) -> tuple[str | None, str | None]:
    """Resolve the *index couple* — the father/mother who co-parent the index
    children — so the marker overlay and the upload block builder agree with the
    pedigree-aware lineage path (``identify_core``).

    The flat role model reuses ``father`` / ``mother`` for *any* parent (a paternal
    grandfather and the index father can both be ``role = "father"``), so the naive
    role-first match can pick the wrong individual. We instead derive the index
    parent(s) from the pedigree graph: ``identify_core`` selects the parents of the
    embryos. This returns ``None`` for the donor side of a SINGLE-PARENT family — we
    must preserve that ``None`` (not back-fill it with a role-first match, which would
    grab a grandparent). We fall back to the role-first match only when no index
    parent can be identified from the pedigree at all (missing relationships)."""
    if context.relationship_rows:
        pedigree = build_pedigree(context.sample_rows, context.relationship_rows)
        core = identify_core(pedigree)
        if core.children and (core.father or core.mother):
            return core.father, core.mother
    return _role_first_parent_names(context)


def _transmitted_parent_haplotype(
    parent_alleles: tuple[str, str] | None,
    other_parent_alleles: tuple[str, str] | None,
    child_alleles: tuple[str, str] | None,
) -> str | None:
    if parent_alleles is None or other_parent_alleles is None or child_alleles is None:
        return None
    child_state = tuple(sorted(child_alleles))
    possible: set[int] = set()
    for parent_index, parent_allele in enumerate(parent_alleles):
        for other_allele in other_parent_alleles:
            if tuple(sorted((parent_allele, other_allele))) == child_state:
                possible.add(parent_index)
    if len(possible) != 1:
        return None
    return str(next(iter(possible)))


def _flip_parent_haplotype(value: str, *, flip: bool) -> str:
    if not flip:
        return value
    if value == "0":
        return "1"
    if value == "1":
        return "0"
    return value


def _orient_haplotype_rows_by_affected_child(
    rows: list[dict[str, Any]],
    *,
    sample_contexts: dict[str, SampleMetadataContext],
    father_name: str,
    mother_name: str,
    affected_parent_counts: dict[str, dict[str, int]],
) -> None:
    father_counts = affected_parent_counts["father"]
    mother_counts = affected_parent_counts["mother"]
    father_flip = father_counts.get("0", 0) > father_counts.get("1", 0)
    mother_flip = mother_counts.get("0", 0) > mother_counts.get("1", 0)
    if not father_flip and not mother_flip:
        return
    sample_name_by_uuid = {
        sample_context.sample_uuid: sample_name
        for sample_name, sample_context in sample_contexts.items()
    }
    for row in rows:
        sample_name = sample_name_by_uuid.get(str(row.get("sample_id")))
        if sample_name == father_name:
            row["hap1"] = _flip_parent_haplotype(str(row["hap1"]), flip=father_flip)
            row["hap2"] = _flip_parent_haplotype(str(row["hap2"]), flip=father_flip)
        elif sample_name == mother_name:
            row["hap1"] = _flip_parent_haplotype(str(row["hap1"]), flip=mother_flip)
            row["hap2"] = _flip_parent_haplotype(str(row["hap2"]), flip=mother_flip)
        else:
            row["hap1"] = _flip_parent_haplotype(str(row["hap1"]), flip=father_flip)
            row["hap2"] = _flip_parent_haplotype(str(row["hap2"]), flip=mother_flip)


def _haplotype_row(
    sample_context: SampleMetadataContext,
    *,
    chrom: str,
    start: int,
    end: int,
    hap1: str,
    hap2: str,
    ps: int | None,
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
        haplotype_rows: list[dict[str, Any]] = []
        hap_prev: dict[str, dict[str, Any]] = {}
        segregation_side_prev: dict[str, dict[str, dict[str, Any]]] = {}
        variant_batch: list[SmallVariantRecord] = []
        metadata_json = _upload_metadata(resolved_format, file)
        father_name, mother_name = _parent_sample_names(context)
        use_segregation_haplotypes = (
            resolved_format == "glimpse2"
            and father_name in sample_contexts
            and mother_name in sample_contexts
        )
        affected_sample_names = set(context.affected_sample_names)
        affected_parent_counts = {
            "father": {"0": 0, "1": 0},
            "mother": {"0": 0, "1": 0},
        }
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
                    hap_prev[name] = _empty_haplotype_state()
                    segregation_side_prev[name] = {
                        "father": _empty_segregation_side_state(),
                        "mother": _empty_segregation_side_state(),
                    }
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
                if resolved_format == "glimpse2" and not use_segregation_haplotypes:
                    state = hap_prev[sample_name]
                    ps_val = call.ps
                    phased_alleles = _phased_haplotype_alleles(gt_val)
                    if phased_alleles is not None:
                        hap1, hap2 = phased_alleles
                        _update_haplotype_state(
                            states=hap_prev,
                            rows=haplotype_rows,
                            sample_contexts=sample_contexts,
                            sample_name=sample_name,
                            chrom=chrom,
                            start=start,
                            hap1=hap1,
                            hap2=hap2,
                            ps=ps_val,
                            chromosome_sizes=chromosome_sizes,
                            metadata_json=metadata_json,
                            split_on_haplotype_change=False,
                        )
                    elif state["start"] is not None:
                        _close_haplotype_state(
                            states=hap_prev,
                            rows=haplotype_rows,
                            sample_contexts=sample_contexts,
                            sample_name=sample_name,
                            next_chrom=chrom,
                            next_start=start,
                            chromosome_sizes=chromosome_sizes,
                            metadata_json=metadata_json,
                        )

            if use_segregation_haplotypes and father_name and mother_name:
                father_call = calls_by_sample.get(father_name)
                mother_call = calls_by_sample.get(mother_name)
                father_alleles = _phased_haplotype_alleles(father_call.gt if father_call else None)
                mother_alleles = _phased_haplotype_alleles(mother_call.gt if mother_call else None)

                if father_call is not None and father_alleles is not None:
                    _update_haplotype_state(
                        states=hap_prev,
                        rows=haplotype_rows,
                        sample_contexts=sample_contexts,
                        sample_name=father_name,
                        chrom=chrom,
                        start=start,
                        hap1="0",
                        hap2="1",
                        ps=father_call.ps,
                        chromosome_sizes=chromosome_sizes,
                        metadata_json=metadata_json,
                        split_on_haplotype_change=True,
                    )
                if mother_call is not None and mother_alleles is not None:
                    _update_haplotype_state(
                        states=hap_prev,
                        rows=haplotype_rows,
                        sample_contexts=sample_contexts,
                        sample_name=mother_name,
                        chrom=chrom,
                        start=start,
                        hap1="0",
                        hap2="1",
                        ps=mother_call.ps,
                        chromosome_sizes=chromosome_sizes,
                        metadata_json=metadata_json,
                        split_on_haplotype_change=True,
                    )

                for sample_name in sample_names:
                    if sample_name in {father_name, mother_name}:
                        continue
                    child_call = calls_by_sample.get(sample_name)
                    child_alleles = _phased_haplotype_alleles(child_call.gt if child_call else None)
                    paternal_hap = _transmitted_parent_haplotype(
                        father_alleles,
                        mother_alleles,
                        child_alleles,
                    )
                    maternal_hap = _transmitted_parent_haplotype(
                        mother_alleles,
                        father_alleles,
                        child_alleles,
                    )
                    if paternal_hap is None and maternal_hap is None:
                        continue
                    if sample_name in affected_sample_names:
                        if paternal_hap is not None:
                            affected_parent_counts["father"][paternal_hap] += 1
                        if maternal_hap is not None:
                            affected_parent_counts["mother"][maternal_hap] += 1
                    side_states = segregation_side_prev[sample_name]
                    changes: dict[int, dict[str, str]] = {}
                    paternal_change = _observe_segregation_haplotype(
                        side_states["father"],
                        chrom=chrom,
                        start=start,
                        hap=paternal_hap,
                    )
                    maternal_change = _observe_segregation_haplotype(
                        side_states["mother"],
                        chrom=chrom,
                        start=start,
                        hap=maternal_hap,
                    )
                    if paternal_change is not None:
                        switch_start, confirmed_hap = paternal_change
                        changes.setdefault(switch_start, {})["hap1"] = confirmed_hap
                    if maternal_change is not None:
                        switch_start, confirmed_hap = maternal_change
                        changes.setdefault(switch_start, {})["hap2"] = confirmed_hap
                    for switch_start, change in sorted(changes.items()):
                        state = hap_prev[sample_name]
                        if state["chr"] == chrom and state["start"] is not None:
                            hap1 = str(state["hap1"])
                            hap2 = str(state["hap2"])
                        else:
                            hap1 = _confirmed_segregation_haplotype(side_states["father"], chrom)
                            hap2 = _confirmed_segregation_haplotype(side_states["mother"], chrom)
                        hap1 = change.get("hap1", hap1)
                        hap2 = change.get("hap2", hap2)
                        if (
                            state["chr"] == chrom
                            and state["start"] is not None
                            and switch_start <= int(state["start"])
                        ):
                            state["hap1"] = hap1
                            state["hap2"] = hap2
                            state["ps"] = child_call.ps if child_call else None
                            continue
                        _update_haplotype_state(
                            states=hap_prev,
                            rows=haplotype_rows,
                            sample_contexts=sample_contexts,
                            sample_name=sample_name,
                            chrom=chrom,
                            start=switch_start,
                            hap1=hap1,
                            hap2=hap2,
                            ps=child_call.ps if child_call else None,
                            chromosome_sizes=chromosome_sizes,
                            metadata_json=metadata_json,
                            split_on_haplotype_change=True,
                        )
                    state = hap_prev[sample_name]
                    if (
                        state["chr"] == chrom
                        and state["start"] is not None
                        and start >= int(state["start"])
                    ):
                        state["last_pos"] = start

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

        if resolved_format == "glimpse2":
            for sample_name, state in hap_prev.items():
                if state["start"] is None:
                    continue
                _append_haplotype_state_row(
                    haplotype_rows,
                    sample_contexts[sample_name],
                    state,
                    next_chrom=None,
                    next_start=None,
                    chromosome_sizes=chromosome_sizes,
                    metadata_json=metadata_json,
                )
            if use_segregation_haplotypes and father_name and mother_name:
                _orient_haplotype_rows_by_affected_child(
                    haplotype_rows,
                    sample_contexts=sample_contexts,
                    father_name=father_name,
                    mother_name=mother_name,
                    affected_parent_counts=affected_parent_counts,
                )

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
