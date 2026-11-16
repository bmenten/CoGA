"""The Variant Explorer's gene, transcript and cohort-frequency views.

Each view summarises the variants the variant view keeps under the same filters, from the
same rows: the variant carrier summary (``variant_carrier_summary``) in the user's projects,
filtered by ``variant_explorer_service._carriers_where``. What the owner chose (2026-11-12):

* **Genes**: one row per gene a kept variant's own annotation names (VEP's gene symbol): the
  number of variants, by their most severe impact on the gene, and the carriers *summed over
  those variants* (a sample with two variants in the gene counts twice). A variant counts
  for a gene over the rows whose annotation names that gene, as the variant view with that
  gene filter counts it, so a gene's row is the sum of that view's rows.
* **Transcripts**: one row per transcript a kept variant's annotation names, every
  transcript (MANE and canonical marked, and selectable), with the variants by their most
  severe consequence class on it (loss of function, missense or in-frame, splice region,
  synonymous, other) and the carriers summed over them.
* **Cohort frequency**: the kept variants counted by the class of their carriers in the
  projects in scope (1, 2–5, 6–20, 21–100, more than 100 samples) against the class of their
  own annotation's gnomAD allele frequency (absent, below 0.1 %, 0.1–1 %, 1–5 %, 5 % and
  over). A cell is read back on the variant view by its two filters
  (``min_total_samples``/``max_total_samples`` and ``gnomad_af_class``).

A view joins the summary's rows to their own annotation (key and annotation set) one genomic
window at a time (:data:`WINDOW_BASES`): each window's query holds that window's variants
alone, whatever the cohort's size, and the windows' rows are summed, a variant lying in one
window.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.clickhouse import execute_clickhouse
from ..core.sql import canonical_uuid
from ..schemas import (
    ExplorerFrequencyCellOut,
    ExplorerFrequencyOut,
    ExplorerGenePageOut,
    ExplorerGeneRowOut,
    ExplorerTranscriptPageOut,
    ExplorerTranscriptRowOut,
)
from .access_control import CurrentUser
from .clickhouse_variant_ids import _small_table_name
from .data_scope import chromosome_aliases
from .variant_explorer_service import (
    _VARIANT_COUNTS,
    GNOMAD_AF_CLASS_CONDITIONS,
    ExplorerScope,
    GlobalVariantFilters,
    _carriers_table,
    _carriers_where,
    _panel_constraints,
    _panel_keeps_nothing,
    _pending_families,
    _review_filter_variant_ids,
    _shown_members,
    _total_samples_having,
    resolve_scope,
)

# The length of a window, in bases: about a million variants of 1,000 genomes.
WINDOW_BASES = 50_000_000
# Windows read at once.
_WINDOWS_AT_ONCE = 4
# The chromosome of each ``xpos`` rank (``clickhouse_variant_queries._xpos``).
_RANK_CHROMOSOME = {**{rank: str(rank) for rank in range(1, 23)}, 23: "X", 24: "Y", 25: "M"}

# The carrier classes of the cohort-frequency view: (label, fewest, most) samples.
CARRIER_CLASSES: tuple[tuple[str, int, int | None], ...] = (
    ("1", 1, 1),
    ("2-5", 2, 5),
    ("6-20", 6, 20),
    ("21-100", 21, 100),
    (">100", 101, None),
)
# The gnomAD allele-frequency classes, as conditions on ``annotation_index.max_gnomad_af``
# (alias ``ai``), in the view's order: the classes the variant view reads back as its
# ``gnomad_af_class`` filter.
GNOMAD_AF_CLASSES: tuple[tuple[str, str], ...] = tuple(GNOMAD_AF_CLASS_CONDITIONS.items())

# The consequence classes of the transcript view, most severe first, by VEP's terms.
_LOF_TERMS = (
    "transcript_ablation",
    "splice_acceptor_variant",
    "splice_donor_variant",
    "stop_gained",
    "frameshift_variant",
    "stop_lost",
    "start_lost",
)
_MISSENSE_TERMS = ("missense_variant", "inframe_insertion", "inframe_deletion", "protein_altering_variant")
_SPLICE_TERMS = (
    "splice_region_variant",
    "splice_donor_5th_base_variant",
    "splice_donor_region_variant",
    "splice_polypyrimidine_tract_variant",
)
_SYNONYMOUS_TERMS = ("synonymous_variant", "start_retained_variant", "stop_retained_variant")
TRANSCRIPT_SELECTIONS = ("all", "mane", "canonical")

GENE_SORTS = {"variants", "carriers", "gene"}
TRANSCRIPT_SORTS = {"variants", "carriers", "transcript", "gene"}


@dataclass(frozen=True, slots=True)
class _Window:
    chromosome: str
    start: int
    end: int


async def _windows(scope: ExplorerScope, filters: GlobalVariantFilters) -> list[_Window]:
    """The windows of the variants the summary holds, within the filters' locus."""
    rows = await execute_clickhouse(
        f"""
        SELECT intDiv(xpos, 1000000000) AS rank, min(xpos % 1000000000), max(xpos % 1000000000)
        FROM {_carriers_table(scope)}
        WHERE key != 0
        GROUP BY rank
        ORDER BY rank
        """,
        {},
    )
    wanted = set(chromosome_aliases(filters.chromosome)) if filters.chromosome else None
    windows: list[_Window] = []
    for rank, first, last in rows or []:
        chromosome = _RANK_CHROMOSOME.get(int(rank))
        if chromosome is None:
            continue
        if wanted is not None and not wanted.intersection(chromosome_aliases(chromosome)):
            continue
        low = max(int(first), filters.start or 0)
        high = min(int(last), filters.end if filters.end is not None else int(last))
        start = (low // WINDOW_BASES) * WINDOW_BASES
        while start <= high:
            end = start + WINDOW_BASES - 1
            windows.append(_Window(chromosome, max(start, low), min(end, high)))
            start += WINDOW_BASES
    return windows


@dataclass(slots=True)
class _ViewInputs:
    scope: ExplorerScope
    pending: int
    windows: list[_Window]
    run: Callable[[_Window, str], Awaitable[list[tuple[Any, ...]]]]


async def _view_inputs(
    session: AsyncSession,
    user: CurrentUser,
    filters: GlobalVariantFilters,
    assembly_id: str | None,
    joined: Callable[[ExplorerScope, _Window, dict[str, Any]], str],
) -> _ViewInputs | None:
    """What a view reads: the scope, the families pending, the windows, and how one window is
    read (``joined`` builds its query over a window's WHERE, bound in its params). No window
    when nothing can be kept (a panel without genes or regions, a review filter that matched
    nothing, no member shown); None without a scope."""
    scope = await resolve_scope(session, user, assembly_id)
    if scope is None:
        return None
    pending = len(await _pending_families(session, scope))
    panel_constraints = await _panel_constraints(session, scope, filters)
    tag_variant_ids, _capped = await _review_filter_variant_ids(session, scope, filters)
    shown_members = await _shown_members(session, scope.project_ids)

    async def nothing(_window: _Window, _label: str) -> list[tuple[Any, ...]]:
        return []

    if _panel_keeps_nothing(panel_constraints) or tag_variant_ids == [] or not shown_members:
        return _ViewInputs(scope=scope, pending=pending, windows=[], run=nothing)

    async def run(window: _Window, _label: str) -> list[tuple[Any, ...]]:
        window_filters = replace(filters, chromosome=window.chromosome, start=window.start, end=window.end)
        params: dict[str, Any] = {}
        where = " AND ".join(
            _carriers_where(
                scope,
                window_filters,
                params,
                tag_variant_ids=tag_variant_ids,
                shown_members=shown_members,
                panel_constraints=panel_constraints,
            )
        )
        having = _total_samples_having(window_filters, params)
        query = joined(scope, window, params).format(where=where, having=having)
        return list(await execute_clickhouse(query, params) or [])

    return _ViewInputs(scope=scope, pending=pending, windows=await _windows(scope, filters), run=run)


async def _over_windows(inputs: _ViewInputs, label: str) -> list[tuple[Any, ...]]:
    """Every window's rows, read a few windows at a time."""
    semaphore = asyncio.Semaphore(_WINDOWS_AT_ONCE)

    async def one(window: _Window) -> list[tuple[Any, ...]]:
        async with semaphore:
            return await inputs.run(window, label)

    results = await asyncio.gather(*(one(window) for window in inputs.windows))
    return [row for rows in results for row in rows]


def _window_scope(window: _Window, params: dict[str, Any], alias: str) -> str:
    """The window, on an annotation table's rows (alias ``alias``)."""
    params["window_chromosomes"] = tuple(chromosome_aliases(window.chromosome))
    params["window_start"] = window.start
    params["window_end"] = window.end
    return (
        f"{alias}.chrom IN %(window_chromosomes)s AND {alias}.pos >= %(window_start)s "
        f"AND {alias}.pos <= %(window_end)s"
    )


# The per-variant counts over the kept rows joined to an annotation part, and the variant's
# total (``total_samples > 0``: it has a carrier in those rows).
def _counted(scope: ExplorerScope, part_key: str, part_sql: str, extra: str) -> str:
    return f"""
        SELECT e.key AS key, {part_key}, {extra}, {_VARIANT_COUNTS}
        FROM {_carriers_table(scope)} AS e
        INNER JOIN ({part_sql}) AS part
            ON part.key = e.key AND part.annotationSetHash = e.annotationSetHash
        WHERE {{where}}
        GROUP BY e.key, {part_key.split(' AS ')[0]}
        HAVING total_samples > 0 {{having}}
    """


def _rank_sql(column: str) -> str:
    return (
        f"multiIf(lower({column}) = 'high', 4, lower({column}) = 'moderate', 3, "
        f"lower({column}) = 'low', 2, 1)"
    )


def _consequence_rank_sql() -> str:
    def any_of(terms: Sequence[str]) -> str:
        return "hasAny(a.effects, [" + ", ".join(f"'{term}'" for term in terms) + "])"

    return (
        f"multiIf({any_of(_LOF_TERMS)}, 5, {any_of(_MISSENSE_TERMS)}, 4, {any_of(_SPLICE_TERMS)}, 3, "
        f"{any_of(_SYNONYMOUS_TERMS)}, 2, 1)"
    )


async def _reference_symbols(session: AsyncSession, assembly_id: str, stored: set[str]) -> dict[str, str]:
    """The gene reference's symbol (``genes.hgnc_symbol`` of the assembly) of each gene
    symbol the annotation tables store casefolded; a symbol the reference lacks is shown as
    stored."""
    assembly = canonical_uuid(assembly_id)
    if not stored or assembly is None:
        return {}
    result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (lower(hgnc_symbol)) lower(hgnc_symbol) AS stored, hgnc_symbol
            FROM genes
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND lower(hgnc_symbol) = ANY(CAST(:symbols AS text[]))
            ORDER BY lower(hgnc_symbol), hgnc_symbol
            """
        ),
        # One array parameter: asyncpg takes at most 32,767 parameters a statement.
        {"assembly_id": assembly, "symbols": sorted(stored)},
    )
    return {str(row[0]): str(row[1]) for row in result.all()}


def _page(rows: list[Any], offset: int, page_size: int) -> list[Any]:
    offset = max(0, int(offset))
    return rows[offset : offset + max(1, min(int(page_size), 500))]


async def explorer_gene_view(
    session: AsyncSession,
    *,
    user: CurrentUser,
    filters: GlobalVariantFilters,
    assembly_id: str | None = None,
    sort: str = "variants",
    order: str = "desc",
    offset: int = 0,
    page_size: int = 100,
) -> ExplorerGenePageOut:
    """One row per gene a kept variant's own annotation names (see the module docstring)."""

    def joined(scope: ExplorerScope, window: _Window, params: dict[str, Any]) -> str:
        annotations = _small_table_name(scope.assembly_name, "variants/annotations")
        part = (
            f"SELECT a.key AS key, a.annotationSetHash AS annotationSetHash, a.gene_symbol AS gene, "
            f"max({_rank_sql('a.impact')}) AS impact_rank FROM {annotations} AS a "
            f"WHERE {_window_scope(window, params, 'a')} AND a.gene_symbol != '' "
            "GROUP BY key, annotationSetHash, gene"
        )
        return f"""
            SELECT gene, count(), countIf(impact_rank = 4), countIf(impact_rank = 3),
                   countIf(impact_rank = 2), countIf(impact_rank = 1),
                   sum(total_samples), sum(het_samples), sum(hom_samples)
            FROM ({_counted(scope, 'part.gene AS gene', part, 'max(part.impact_rank) AS impact_rank')})
            GROUP BY gene
        """

    inputs = await _view_inputs(session, user, filters, assembly_id, joined)
    if inputs is None:
        return ExplorerGenePageOut(total=0, assembly_id=assembly_id)
    totals: dict[str, list[int]] = {}
    for gene, *counts in await _over_windows(inputs, "genes"):
        summed = totals.setdefault(str(gene), [0] * len(counts))
        for index, value in enumerate(counts):
            summed[index] += int(value or 0)
    symbols = await _reference_symbols(session, inputs.scope.assembly_id, set(totals))
    rows = [
        ExplorerGeneRowOut(
            gene=symbols.get(gene, gene),
            variants=counts[0],
            high=counts[1],
            moderate=counts[2],
            low=counts[3],
            modifier=counts[4],
            carriers=counts[5],
            het=counts[6],
            hom=counts[7],
        )
        for gene, counts in totals.items()
    ]
    sort = sort if sort in GENE_SORTS else "variants"
    reverse = str(order).lower() != "asc"
    if sort == "gene":
        rows.sort(key=lambda row: (row.gene.lower(), row.gene), reverse=reverse)
    else:
        rows.sort(key=lambda row: (row.gene.lower(), row.gene))
        rows.sort(key=lambda row: getattr(row, sort), reverse=reverse)
    return ExplorerGenePageOut(
        total=len(rows),
        assembly_id=inputs.scope.assembly_id,
        assembly_name=inputs.scope.assembly_name,
        pending_families=inputs.pending,
        sort=sort,
        order="desc" if reverse else "asc",
        offset=max(0, int(offset)),
        genes=_page(rows, offset, page_size),
    )


async def explorer_transcript_view(
    session: AsyncSession,
    *,
    user: CurrentUser,
    filters: GlobalVariantFilters,
    assembly_id: str | None = None,
    transcripts: str = "all",
    sort: str = "variants",
    order: str = "desc",
    offset: int = 0,
    page_size: int = 100,
) -> ExplorerTranscriptPageOut:
    """One row per transcript a kept variant's own annotation names; ``transcripts`` keeps
    every one, the MANE ones (Select or Plus Clinical) or the canonical ones."""
    selection = transcripts if transcripts in TRANSCRIPT_SELECTIONS else "all"
    chosen = {
        "all": "",
        "mane": " AND (a.mane_select OR a.mane_plus_clinical)",
        "canonical": " AND a.canonical",
    }[selection]

    def joined(scope: ExplorerScope, window: _Window, params: dict[str, Any]) -> str:
        annotations = _small_table_name(scope.assembly_name, "variants/annotations")
        part = (
            "SELECT a.key AS key, a.annotationSetHash AS annotationSetHash, a.transcript_id AS transcript, "
            "min(a.gene_symbol) AS gene, max(a.mane_select OR a.mane_plus_clinical) AS mane, "
            f"max(a.canonical) AS canonical, max({_consequence_rank_sql()}) AS consequence_rank "
            f"FROM {annotations} AS a WHERE {_window_scope(window, params, 'a')} AND a.transcript_id != ''"
            f"{chosen} GROUP BY key, annotationSetHash, transcript"
        )
        extra = (
            "min(part.gene) AS gene, max(part.mane) AS mane, max(part.canonical) AS canonical, "
            "max(part.consequence_rank) AS consequence_rank"
        )
        return f"""
            SELECT transcript, min(gene), max(mane), max(canonical), count(),
                   countIf(consequence_rank = 5), countIf(consequence_rank = 4),
                   countIf(consequence_rank = 3), countIf(consequence_rank = 2),
                   countIf(consequence_rank = 1), sum(total_samples)
            FROM ({_counted(scope, 'part.transcript AS transcript', part, extra)})
            GROUP BY transcript
        """

    inputs = await _view_inputs(session, user, filters, assembly_id, joined)
    if inputs is None:
        return ExplorerTranscriptPageOut(total=0, assembly_id=assembly_id, transcripts=selection)
    totals: dict[str, dict[str, Any]] = {}
    for transcript, gene, mane, canonical, *counts in await _over_windows(inputs, "transcripts"):
        entry = totals.setdefault(
            str(transcript), {"gene": str(gene or ""), "mane": False, "canonical": False, "counts": [0] * len(counts)}
        )
        entry["gene"] = min(entry["gene"], str(gene or "")) or entry["gene"] or str(gene or "")
        entry["mane"] = entry["mane"] or bool(mane)
        entry["canonical"] = entry["canonical"] or bool(canonical)
        for index, value in enumerate(counts):
            entry["counts"][index] += int(value or 0)
    symbols = await _reference_symbols(
        session, inputs.scope.assembly_id, {entry["gene"] for entry in totals.values() if entry["gene"]}
    )
    rows = [
        ExplorerTranscriptRowOut(
            transcript_id=transcript,
            gene=symbols.get(entry["gene"], entry["gene"]) or None,
            mane=entry["mane"],
            canonical=entry["canonical"],
            variants=entry["counts"][0],
            lof=entry["counts"][1],
            missense=entry["counts"][2],
            splice=entry["counts"][3],
            synonymous=entry["counts"][4],
            other=entry["counts"][5],
            carriers=entry["counts"][6],
        )
        for transcript, entry in totals.items()
    ]
    sort = sort if sort in TRANSCRIPT_SORTS else "variants"
    reverse = str(order).lower() != "asc"
    rows.sort(key=lambda row: row.transcript_id)
    if sort == "transcript":
        rows.sort(key=lambda row: row.transcript_id, reverse=reverse)
    elif sort == "gene":
        rows.sort(key=lambda row: ((row.gene or "").lower(), row.gene or ""), reverse=reverse)
    else:
        rows.sort(key=lambda row: getattr(row, sort), reverse=reverse)
    return ExplorerTranscriptPageOut(
        total=len(rows),
        assembly_id=inputs.scope.assembly_id,
        assembly_name=inputs.scope.assembly_name,
        pending_families=inputs.pending,
        transcripts=selection,
        sort=sort,
        order="desc" if reverse else "asc",
        offset=max(0, int(offset)),
        rows=_page(rows, offset, page_size),
    )


def _carrier_class_sql() -> str:
    parts = []
    for index, (_label, fewest, most) in enumerate(CARRIER_CLASSES):
        condition = f"total_samples >= {fewest}" + (f" AND total_samples <= {most}" if most is not None else "")
        parts.append(f"{condition}, {index}")
    return "multiIf(" + ", ".join(parts) + ", -1)"


async def explorer_frequency_view(
    session: AsyncSession,
    *,
    user: CurrentUser,
    filters: GlobalVariantFilters,
    assembly_id: str | None = None,
) -> ExplorerFrequencyOut:
    """The kept variants by carrier class and gnomAD frequency class (see the module
    docstring): every cell, zeros included."""

    def joined(scope: ExplorerScope, window: _Window, params: dict[str, Any]) -> str:
        annotation_index = _small_table_name(scope.assembly_name, "variants/annotation_index")
        af_class = "multiIf(" + ", ".join(
            f"{condition}, {index}" for index, (_label, condition) in enumerate(GNOMAD_AF_CLASSES)
        ) + ", -1)"
        part = (
            f"SELECT ai.key AS key, ai.annotationSetHash AS annotationSetHash, min({af_class}) AS af_class "
            f"FROM {annotation_index} AS ai WHERE {_window_scope(window, params, 'ai')} "
            "GROUP BY key, annotationSetHash"
        )
        return f"""
            SELECT {_carrier_class_sql()} AS carrier_class, af_class, count()
            FROM ({_counted(scope, 'part.af_class AS af_class', part, 'count() AS joined_rows')})
            GROUP BY carrier_class, af_class
        """

    inputs = await _view_inputs(session, user, filters, assembly_id, joined)
    counts: dict[tuple[int, int], int] = {}
    if inputs is not None:
        for carrier_class, af_class, variants in await _over_windows(inputs, "frequency"):
            cell = (int(carrier_class), int(af_class))
            counts[cell] = counts.get(cell, 0) + int(variants or 0)
    return ExplorerFrequencyOut(
        assembly_id=inputs.scope.assembly_id if inputs else assembly_id,
        assembly_name=inputs.scope.assembly_name if inputs else None,
        pending_families=inputs.pending if inputs else 0,
        carrier_classes=[label for label, _fewest, _most in CARRIER_CLASSES],
        gnomad_af_classes=[label for label, _condition in GNOMAD_AF_CLASSES],
        cells=[
            ExplorerFrequencyCellOut(
                carrier_class=carrier_label,
                carrier_min=fewest,
                carrier_max=most,
                gnomad_af_class=af_label,
                variants=counts.get((carrier_index, af_index), 0),
            )
            for carrier_index, (carrier_label, fewest, most) in enumerate(CARRIER_CLASSES)
            for af_index, (af_label, _condition) in enumerate(GNOMAD_AF_CLASSES)
        ],
    )
