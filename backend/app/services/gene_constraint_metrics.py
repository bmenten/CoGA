"""The gene-level constraint a small variant is served with: gnomAD's pLI and missense Z.

They come from the gene's profile (``gene_info``), not from the variant's annotation. The
variant list looks them up for the gene of each variant's primary transcript
(``clickhouse_family_variants._fetch_gene_constraint_metric_map``), and an ACMG
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
) -> dict[str, dict[str, float | None]]:
    """The pLI and missense Z of each ``(key, gene symbol, gene id)``, by its key.

    A gene is found by its id first, else by its symbol; of the profiles found for one, the
    most recently updated is read. A key whose gene has no profile is left out.
    """
    if not genes:
        return {}
    gene_symbols = sorted({symbol for _key, symbol, _gene_id in genes if symbol})
    gene_ids = sorted({gene_id for _key, _symbol, gene_id in genes if gene_id})
    # Each list goes as one text[] parameter, not one parameter per gene (an expanding IN):
    # a CSV export's 50,001 rows can hit more genes than the 32,767 parameters asyncpg
    # takes in one statement (DATA-3).
    clauses: list[str] = []
    params: dict[str, Any] = {}
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
            WHERE {' OR '.join(clauses)}
            ORDER BY updated_at DESC
            """
        ),
        params,
    )
    by_symbol: dict[str, dict[str, float | None]] = {}
    by_gene_id: dict[str, dict[str, float | None]] = {}
    for row in result.mappings().all():
        doc = dict(row)
        metrics = _extract_gene_constraint_metrics(doc)
        symbol = str(doc.get("hgnc_symbol") or "").strip()
        gene_id = str(doc.get("gene_id") or "").strip()
        if symbol and symbol not in by_symbol:
            by_symbol[symbol] = metrics
        if gene_id and gene_id not in by_gene_id:
            by_gene_id[gene_id] = metrics
    found: dict[str, dict[str, float | None]] = {}
    for key, symbol, gene_id in genes:
        if gene_id and gene_id in by_gene_id:
            found[key] = by_gene_id[gene_id]
        elif symbol and symbol in by_symbol:
            found[key] = by_symbol[symbol]
    return found
