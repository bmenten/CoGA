"""The gene-level constraint a small variant is served with: gnomAD's pLI and missense Z.

They come from the gene's profile (``gene_info``) in the family's assembly, not from the
variant's annotation. The variant list looks them up for the gene of each variant's primary
transcript (``clickhouse_family_variants._fetch_gene_constraint_metric_map``), and an ACMG
classification freezes the missense Z the PP2 suggestion reads through the same lookup
(``small_variant_review_acmg.evidence_gene_missense_z``), so the value frozen is the one the
classification dialog was given.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .clickhouse_variant_queries import _extract_gene_constraint_metrics


async def fetch_gene_constraint_metrics(
    session: AsyncSession,
    genes: Sequence[tuple[str, str | None, str | None]],
    *,
    assembly_id: str | None,
) -> dict[str, dict[str, float | None]]:
    """The pLI and missense Z of each ``(key, gene symbol, gene id)``, by its key, from the
    gene profiles of the family's assembly (``assembly_id``).

    A gene is found by its id first, else by its symbol in any case, as the profiles are read;
    of the profiles found for one, the most recently updated is read. A key whose gene has no
    profile is left out, and so is every key without an assembly. Before TRACE-15 the newest
    profile of any assembly was read, so a family could be served, and its classifications
    could freeze, the value of another assembly's profile; and a symbol was matched only as
    written after the read had matched it in any case, so a gene written otherwise than its
    profile had no constraint.
    """
    if not genes or not assembly_id:
        return {}
    gene_symbols = sorted({symbol for _key, symbol, _gene_id in genes if symbol})
    gene_ids = sorted({gene_id for _key, _symbol, gene_id in genes if gene_id})
    # Each list goes as one text[] parameter, not one parameter per gene (an expanding IN):
    # a CSV export's 50,001 rows can hit more genes than the 32,767 parameters asyncpg
    # takes in one statement (DATA-3).
    clauses: list[str] = []
    params: dict[str, Any] = {"assembly_id": assembly_id}
    if gene_symbols:
        clauses.append("upper(hgnc_symbol) = ANY(CAST(:gene_symbols AS text[]))")
        params["gene_symbols"] = [symbol.upper() for symbol in gene_symbols]
    if gene_ids:
        clauses.append("gene_id = ANY(CAST(:gene_ids AS text[]))")
        params["gene_ids"] = gene_ids
    if not clauses:
        return {}
    result = await session.execute(
        text(
            f"""
            SELECT hgnc_symbol, gene_id, extra, source_status, updated_at
            FROM gene_info
            WHERE assembly_id = CAST(:assembly_id AS uuid) AND ({' OR '.join(clauses)})
            ORDER BY updated_at DESC
            """
        ),
        params,
    )
    # Symbols by their upper case, as the read above matches them.
    by_symbol: dict[str, dict[str, float | None]] = {}
    by_gene_id: dict[str, dict[str, float | None]] = {}
    for row in result.mappings().all():
        doc = dict(row)
        metrics = _extract_gene_constraint_metrics(doc)
        symbol = str(doc.get("hgnc_symbol") or "").strip().upper()
        gene_id = str(doc.get("gene_id") or "").strip()
        if symbol and symbol not in by_symbol:
            by_symbol[symbol] = metrics
        if gene_id and gene_id not in by_gene_id:
            by_gene_id[gene_id] = metrics
    found: dict[str, dict[str, float | None]] = {}
    for key, symbol, gene_id in genes:
        if gene_id and gene_id in by_gene_id:
            found[key] = by_gene_id[gene_id]
        elif symbol and symbol.upper() in by_symbol:
            found[key] = by_symbol[symbol.upper()]
    return found
