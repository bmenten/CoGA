from __future__ import annotations

from hashlib import blake2b
from typing import Any, Iterable, Mapping

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

    Without the suffixes it names an uploaded SV and a package's NeedlR call alike
    (CLIN-43): one event called by either has one id, so the reviews and the SV gene index,
    which attach by the id, cover every caller's call of it. The storage key names the
    source whatever the id holds (:func:`structural_variant_key`), so two callers' rows
    never collide in storage.

    ``source`` and ``discriminator`` are optional suffixes that keep distinct calls
    apart when coordinates and type alone are not unique:

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


# A call's event within its SV id: its REF, its ALT and the length the SV list measures it by,
# without its sign (``structural_variant_ingest.sv_event``). Two calls of one position, type
# and mate are two events when these differ, insertions in particular.
SvEvent = tuple[str, str, int]


def event_discriminators(
    events: Iterable[tuple[str, SvEvent]],
    *,
    plain_lengths: Mapping[str, int] | None = None,
) -> dict[tuple[str, SvEvent], str | None]:
    """The discriminator of each event, by ``(id, event)``, among the events that share an
    id (``build_structural_variant_id`` without one): None for the event that keeps the id,
    else a suffix that keeps it apart, its length (an ALT's own length for an event of none,
    such as a breakend), then ``.2``, ``.3`` where that is taken.

    The event that keeps the id is the one whose length ``plain_lengths`` names for the id
    (the length of the SV already stored under it, so a later file's call of that SV keeps
    its id; an id it does not name has no SV stored), else the first in a fixed order: by
    length, then REF and ALT. The order
    is the events', never their lines': a file read in another order, or the samples' files
    in another order, give each event the id it had. Following the lines, a changed input
    could move a review to the other event (CLIN-39).
    """
    by_id: dict[str, set[SvEvent]] = {}
    for variant_id, event in events:
        by_id.setdefault(variant_id, set()).add(event)
    discriminators: dict[tuple[str, SvEvent], str | None] = {}
    for variant_id, distinct in by_id.items():
        ordered = sorted(distinct, key=_event_order)
        if plain_lengths is not None and variant_id in plain_lengths:
            plain = next((event for event in ordered if event[2] == plain_lengths[variant_id]), None)
        else:
            plain = ordered[0]
        used: set[str] = set()
        for event in ordered:
            if event == plain:
                discriminators[(variant_id, event)] = None
                continue
            suffix = str(event[2] or len(event[1]))
            candidate, attempt = suffix, 1
            while candidate in used:
                attempt += 1
                candidate = f"{suffix}.{attempt}"
            used.add(candidate)
            discriminators[(variant_id, event)] = candidate
    return discriminators


def _event_order(event: SvEvent) -> tuple[int, str, str]:
    """An event's place among the events of one id: by length, then REF and ALT."""
    ref, alt, length = event
    return (length, ref, alt)


def small_variant_key(assembly_name: str, variant_id: str) -> int:
    """The storage key of a small variant: the same for every family and callset.

    The shared tables (``variants/details``, the annotations and their indexes) are keyed
    by it, so every family and callset finds the one annotation of a variant. A family's
    ``entries`` rows add the callset to the row identity (their sort key ends with
    ``key, source``), so a clair3 call and a GLIMPSE2 imputation of one variant are two rows.
    """
    return _stable_uint64("small", assembly_name, variant_id)


def structural_variant_key(
    assembly_name: str, family_uuid: str, variant_id: str, *, source: str
) -> int:
    """The storage key of one caller's call of an SV in one family.

    It hashes the source because the variant id need not name the caller: a per-sample
    upload's id is ``chrom-start-end-type---``, so a Sniffles and a Spectre call at the same
    coordinates share it, as a package's NeedlR call of the same event does. Keyed on the
    id alone, the two calls shared one ``entries`` row identity, one ``variants/details``
    row and one ``key_lookup`` row, and a part merge kept only one of them. The id stays as
    it is: reviews and the SV gene index attach by it.
    """
    return _stable_uint64("structural", assembly_name, family_uuid, variant_id, source)


# The highest chromosome rank whose xpos a UInt64 column holds for every UInt32 position.
_MAX_XPOS_RANK = (2**64 - 1 - (2**32 - 1)) // 1_000_000_000


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
    if not 0 <= rank <= _MAX_XPOS_RANK:
        # A contig named by a negative or an enormous number ranks with the other
        # contigs (SAFE-30): its xpos would not fit the UInt64 column, and the insert of
        # every record of the batch would fail.
        rank = 99
    return (rank * 1_000_000_000) + int(pos)
