from __future__ import annotations

from hashlib import blake2b
from typing import Any

from ..core.clickhouse import clickhouse_dataset_key
from ..core.config import settings
from .data_scope import normalize_chromosome


def _require_clickhouse_identifier(value: str) -> str:
    # Normalize any assembly name to a ClickHouse-safe dataset key (shared with
    # the read paths) so e.g. "T2T CHM13v2.0" ingests and queries automatically.
    return clickhouse_dataset_key(value)


def _small_table_name(assembly_name: str, suffix: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SNV_INDEL/{suffix}`"


def _structural_table_name(assembly_name: str, suffix: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SV/{suffix}`"


def _expected_clickhouse_variant_tables(assembly_name: str) -> list[tuple[str, str, str]]:
    dataset = _require_clickhouse_identifier(assembly_name)
    return [
        ("small_variants", "table", f"{dataset}/SNV_INDEL/variants/details"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/variants/annotations"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/variants/annotation_index"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/variants/gene_index"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/entries"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/family_variant_summary"),
        ("small_variants", "table", f"{dataset}/SNV_INDEL/family_sample_variant_summary"),
        ("structural_variants", "table", f"{dataset}/SV/variants/details"),
        ("structural_variants", "table", f"{dataset}/SV/key_lookup"),
        ("structural_variants", "table", f"{dataset}/SV/entries"),
    ]


def _stable_uint64(*parts: Any) -> int:
    payload = "||".join(str(part) for part in parts).encode()
    return int.from_bytes(blake2b(payload, digest_size=8).digest(), byteorder="big", signed=False)


def build_small_variant_id(chrom: str, start: int, ref: str, alt: str) -> str:
    return f"{normalize_chromosome(chrom)}-{int(start)}-{ref}-{alt}"


def build_structural_variant_id(
    chrom: str,
    start: int,
    end: int,
    sv_type: str,
    *,
    remote_chr: str | None = None,
    remote_start: int | None = None,
    remote_end: int | None = None,
    source: str | None = None,
    discriminator: str | None = None,
) -> str:
    """Stable id for a structural variant within a family.

    ``source`` and ``discriminator`` are optional suffixes that keep distinct calls
    apart when coordinates and type alone are not unique. Both default to absent so
    every id built before they existed (the whole NeedlR callset) is byte-identical --
    the id is the ReplacingMergeTree sort key, so changing it for existing sources
    would orphan already-stored rows.

    * ``source`` separates callers that legitimately call the same event: a depth-based
      CNV DEL and an alignment-based SV DEL at the same coordinates are two independent
      pieces of evidence, not one row to overwrite.
    * ``discriminator`` separates same-coordinate, same-type calls from one caller --
      insertions in particular share a breakpoint and differ only in inserted sequence.
    """
    parts = [
        normalize_chromosome(chrom),
        str(int(start)),
        str(int(end)),
        str(sv_type or ""),
        normalize_chromosome(remote_chr) if remote_chr else "",
        "" if remote_start is None else str(int(remote_start)),
        "" if remote_end is None else str(int(remote_end)),
    ]
    variant_id = "-".join(parts)
    if source:
        variant_id = f"{variant_id}-{source}"
    if discriminator:
        variant_id = f"{variant_id}-{discriminator}"
    return variant_id


def small_variant_key(assembly_name: str, variant_id: str) -> int:
    return _stable_uint64("small", assembly_name, variant_id)


def structural_variant_key(assembly_name: str, family_uuid: str, variant_id: str) -> int:
    return _stable_uint64("structural", assembly_name, family_uuid, variant_id)


def _xpos(chrom: str, pos: int) -> int:
    normalized = normalize_chromosome(chrom).upper()
    rank_map = {
        "X": 23,
        "Y": 24,
        "M": 25,
        "MT": 25,
    }
    try:
        rank = int(normalized)
    except ValueError:
        rank = rank_map.get(normalized, 99)
    return (rank * 1_000_000_000) + int(pos)
