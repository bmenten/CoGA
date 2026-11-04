"""A user's choice, per individual, of how the ancestry PCA reads it.

Exploratory and outside CoGA's intended purpose (TF-01 §4). By default (``auto``) a calculation
reads each individual as ``ancestry_callsets`` measures it. A user who may change the project can
instead, for one individual:

- read one callset (``callset``): one of the stored callsets that hold it. This is also how an
  embryo is included, which ``auto`` leaves out;
- give, with that callset, the regions it covers: a gene panel's (its genes' coding markers) or a
  BED file's. An absent record then counts as homozygous reference at the markers inside them, if
  the callset carries there the non-reference calls a sequenced genome has;
- leave the individual out (``exclude``).

A maternal-plasma cfDNA sample takes no choice: it is a mixture, not one individual. Choices are
append-only (``ancestry_sample_choices``): the latest of an individual holds, and every
calculation records the ones it read. The regions are kept as the reference markers they cover
(``chrom:pos``), never the BED file itself.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..ancestry_schemas import AncestryRegionsOut, AncestrySampleChoiceIn, AncestrySampleChoiceOut
from ..core.sql import require_uuid
from .ancestry_projection import AncestryReference
from .data_scope import normalize_chromosome
from .nipt import is_cfdna_sample_metadata

# A BED larger than this is refused; an exome capture BED is a few megabytes.
MAX_BED_BYTES = 32 * 1024 * 1024
_CHOICES = """
    SELECT DISTINCT ON (sample_id) sample_id, mode, callset, regions, chosen_at
    FROM ancestry_sample_choices
    WHERE family_uuid = CAST(:family AS uuid) AND project_id = CAST(:project AS uuid)
    ORDER BY sample_id, chosen_at DESC, id DESC
"""


def _out(row: Any) -> AncestrySampleChoiceOut:
    regions = row["regions"] if isinstance(row["regions"], dict) else None
    return AncestrySampleChoiceOut(
        sample_id=str(row["sample_id"]), mode=row["mode"], callset=row["callset"],
        regions=AncestryRegionsOut(**{k: v for k, v in regions.items() if k != "loci"}) if regions else None,
        chosen_at=row["chosen_at"])


async def current_choices(session: AsyncSession, family_uuid: str, project: str) -> dict[str, dict[str, Any]]:
    """The latest choice of each individual whose choice is not CoGA's own reading, as stored
    (regions with their loci)."""
    rows = (await session.execute(text(_CHOICES), {"family": family_uuid, "project": project})).mappings().all()
    return {str(row["sample_id"]): {"mode": row["mode"], "callset": row["callset"],
                                    "regions": row["regions"] if isinstance(row["regions"], dict) else None,
                                    "chosen_at": row["chosen_at"]}
            for row in rows if row["mode"] != "auto"}


async def choices_out(session: AsyncSession, family_uuid: str, project: str) -> list[AncestrySampleChoiceOut]:
    rows = (await session.execute(text(_CHOICES), {"family": family_uuid, "project": project})).mappings().all()
    return [_out(row) for row in rows]


def marker_loci_in(reference: AncestryReference, intervals: dict[str, list[tuple[int, int]]], *,
                   coding_only: bool) -> list[str]:
    """The reference markers inside the (1-based, inclusive) intervals, as ``chrom:pos``; only the
    exome mask's when ``coding_only``."""
    import bisect

    merged: dict[str, list[tuple[int, int]]] = {}
    for chrom, spans in intervals.items():
        out: list[list[int]] = []
        for start, end in sorted(spans):
            if out and start <= out[-1][1] + 1:
                out[-1][1] = max(out[-1][1], end)
            else:
                out.append([start, end])
        merged[chrom] = [(a, b) for a, b in out]
    starts = {chrom: [a for a, _ in spans] for chrom, spans in merged.items()}
    mask = reference.exome.covered if (coding_only and reference.exome is not None) else None
    loci = []
    for i, marker in enumerate(reference.markers):
        if mask is not None and not mask[i]:
            continue
        spans = merged.get(marker.chrom, [])
        at = bisect.bisect_right(starts.get(marker.chrom, []), marker.pos) - 1
        if at >= 0 and spans[at][1] >= marker.pos:
            loci.append(f"{marker.chrom}:{marker.pos}")
    return loci


def parse_bed(data: bytes) -> dict[str, list[tuple[int, int]]]:
    """A BED file's intervals by chromosome (no ``chr``), 1-based and inclusive.

    Header, ``track``, ``browser`` and comment lines are skipped. A line that is no interval is
    refused by its number, never its content.
    """
    if len(data) > MAX_BED_BYTES:
        raise HTTPException(413, "The BED file is larger than 32 MB.")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "The BED file is not UTF-8 text.") from exc
    intervals: dict[str, list[tuple[int, int]]] = {}
    for number, line in enumerate(lines, start=1):
        if not line.strip() or line.startswith(("#", "track", "browser")):
            continue
        fields = line.split("\t") if "\t" in line else line.split()
        try:
            chrom, start, end = normalize_chromosome(fields[0]), int(fields[1]), int(fields[2])
        except (IndexError, ValueError) as exc:
            raise HTTPException(400, f"Line {number} of the BED file is not an interval.") from exc
        if start < 0 or end <= start:
            raise HTTPException(400, f"Line {number} of the BED file is not an interval.")
        # BED is 0-based and half-open: bases start+1 .. end.
        intervals.setdefault(chrom, []).append((start + 1, end))
    if not intervals:
        raise HTTPException(400, "The BED file holds no interval.")
    return intervals


async def panel_intervals(session: AsyncSession, panel_id: str, assembly_id: str | None) -> tuple[dict[str, Any], dict[str, list[tuple[int, int]]]]:
    """A gene panel and the GRCh38 regions of its genes, by chromosome."""
    panel = (await session.execute(text("SELECT id::text, name, version FROM gene_panels WHERE id = CAST(:id AS uuid)"),
                                   {"id": require_uuid(panel_id, "Gene panel not found", status_code=404)})).mappings().first()
    if panel is None:
        raise HTTPException(404, "Gene panel not found")
    rows = (await session.execute(text("""
        SELECT chr, start, "end" FROM gene_panel_regions
        WHERE panel_id = CAST(:id AS uuid) AND assembly_id = CAST(:assembly AS uuid)
    """), {"id": panel["id"], "assembly": assembly_id})).mappings().all()
    intervals: dict[str, list[tuple[int, int]]] = {}
    for row in rows:
        intervals.setdefault(normalize_chromosome(str(row["chr"])), []).append((int(row["start"]), int(row["end"])))
    return dict(panel), intervals


def file_label(name: str | None) -> str:
    """A BED file's name as the page shows it: its last path part, printable, at most 120 characters."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    printable = "".join(ch for ch in base if ch.isprintable()).strip()
    return printable[:120] or "BED file"


def check_choice(member: dict[str, Any] | None, choice: AncestrySampleChoiceIn) -> None:
    if member is None:
        raise HTTPException(404, "Sample not found in this family")
    if is_cfdna_sample_metadata(member.get("sample_metadata")):
        raise HTTPException(409, "A maternal-plasma cfDNA sample is not one individual: it takes no ancestry choice.")
    if (choice.mode == "callset") != (choice.callset is not None):
        raise HTTPException(422, "Name a callset exactly when the choice is to read one.")
    if choice.panel_id is not None and choice.mode != "callset":
        raise HTTPException(422, "Regions go with a chosen callset.")


async def insert_choice(session: AsyncSession, *, family_uuid: str, project: str, sample_id: str, mode: str,
                        callset: str | None, regions: dict[str, Any] | None, user_id: str) -> None:
    await session.execute(text("""
        INSERT INTO ancestry_sample_choices(family_uuid, project_id, sample_id, mode, callset, regions, chosen_by)
        VALUES (CAST(:family AS uuid), CAST(:project AS uuid), :sample, :mode, :callset, CAST(:regions AS jsonb),
                CAST(:user AS uuid))
    """), {"family": family_uuid, "project": project, "sample": sample_id, "mode": mode, "callset": callset,
            "regions": json.dumps(regions) if regions is not None else None, "user": user_id})


def regions_record(*, source: str, name: str, loci: list[str], interval_count: int, reference_sha256: str,
                   panel_id: str | None = None, panel_version: int | None = None,
                   data: bytes | None = None) -> dict[str, Any]:
    if not loci:
        raise HTTPException(409, "The regions hold none of the reference markers.")
    return {"source": source, "name": name, "panel_id": panel_id, "panel_version": panel_version,
            "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
            "interval_count": interval_count, "covered_markers": len(loci),
            "reference_sha256": reference_sha256, "loci": loci}
