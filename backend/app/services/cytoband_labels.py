"""The cytoband a region lies in, read from the assembly's cytoband track (the ideogram's).

Small variants, structural variants and the clinical CNV catalogue carry no band of their
own: each is looked up against the bands of its assembly's chromosomes when it is shown.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from .data_scope import normalize_chromosome


def _band_bound(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def band_position_contains(band: dict[str, Any], position: int) -> bool:
    """Whether a 1-based position lies in a band. Bands are stored as the cytoband files give
    them: zero-based and half-open, so a band's end is the first base of the next."""
    start = _band_bound(band.get("start"))
    end = _band_bound(band.get("end"))
    if start is None or end is None:
        return False
    zero_based_position = max(0, position - 1)
    return start <= zero_based_position < end


def band_name_for_position(bands: Sequence[dict[str, Any]], position: int) -> str | None:
    for band in bands:
        name = str(band.get("name") or "").strip()
        if name and band_position_contains(band, position):
            return name
    return None


def format_cytoband_label(chromosome: str, start_band: str | None, end_band: str | None) -> str | None:
    if not start_band and not end_band:
        return None
    chrom = normalize_chromosome(chromosome)
    if start_band and end_band and start_band != end_band:
        return f"{chrom}{start_band}-{chrom}{end_band}"
    return f"{chrom}{start_band or end_band}"


def _is_named_band(chromosome: str, band: dict[str, Any]) -> bool:
    """Whether a band has a name of its own. An assembly without cytobands (T2T) stores one
    band per chromosome, named after the chromosome (`chr21`): it names no band, and the
    mitochondrion has none."""
    name = str(band.get("name") or "").strip()
    if not name or chromosome.upper() in {"M", "MT"}:
        return False
    return normalize_chromosome(name) != chromosome


async def fetch_cytoband_labels(
    session: AsyncSession,
    *,
    assembly_id: str | None,
    loci: Sequence[tuple[str, str, int, int]],
) -> dict[str, str]:
    """Cytoband label per locus, keyed by the caller's id.

    ``loci`` is ``(key, chromosome, start, end)``, both 1-based and inclusive. A locus whose
    chromosome has no named bands gets no label.
    """
    if not assembly_id or not loci:
        return {}
    chromosomes = list(
        dict.fromkeys(
            alias
            for _key, chrom, _start, _end in loci
            for alias in (normalize_chromosome(chrom), f"chr{normalize_chromosome(chrom)}")
        )
    )
    result = await session.execute(
        text(
            """
            SELECT chr, bands
            FROM chromosomes
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND chr IN :chromosomes
            """
        ).bindparams(bindparam("chromosomes", expanding=True)),
        {
            "assembly_id": assembly_id,
            "chromosomes": chromosomes or [""],
        },
    )
    band_map: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        bands = row.get("bands") or []
        if isinstance(bands, str):
            try:
                bands = json.loads(bands)
            except json.JSONDecodeError:
                bands = []
        if not isinstance(bands, list):
            continue
        normalized = normalize_chromosome(str(row["chr"]))
        band_map[normalized] = [
            band for band in bands if isinstance(band, dict) and _is_named_band(normalized, band)
        ]

    cytobands: dict[str, str] = {}
    for key, chrom, start, end in loci:
        bands = band_map.get(normalize_chromosome(chrom))
        if not bands:
            continue
        start_band = band_name_for_position(bands, start)
        end_band = band_name_for_position(bands, end)
        label = format_cytoband_label(chrom, start_band, end_band)
        if label:
            cytobands[key] = label
    return cytobands
