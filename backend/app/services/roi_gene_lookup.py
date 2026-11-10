"""The gene a family's region of interest names, looked up one way wherever it is set.

The family page (``family_service._build_family_roi_payload``), the Family Builder and PED
upload (``ped_service``) and a package import's manifest (``family_package_registration``)
each read a region of interest that is no ``chrom:start-end`` region as a gene of the family's
assembly, and resolve it to the same region: the PGT calls and the NIPT coverage check read
the family's region of interest, whichever of them set it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .data_scope import normalize_chromosome

_ROI_GENE_SQL = text(
    """
    SELECT hgnc_symbol, gene_id, chr, start, "end"
    FROM genes
    WHERE assembly_id = CAST(:assembly_id AS uuid)
      AND (
        lower(hgnc_symbol) = lower(:query)
        OR lower(gene_id) = lower(:query)
        -- Under GENCODE, gene_id is an Ensembl transcript accession. Match the
        -- unversioned form, the Ensembl gene, and the gene's RefSeq accessions, so a
        -- query naming any of them resolves.
        OR lower(COALESCE(extra->>'ensembl_transcript_id', '')) = lower(:query)
        OR lower(COALESCE(extra->>'ensembl_gene_id', '')) = lower(:query)
        OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(
                COALESCE(extra->'refseq_accessions', '[]'::jsonb)
            ) AS accession
            WHERE lower(accession) = lower(:query)
               OR lower(split_part(accession, '.', 1)) = lower(:query)
        )
      )
    -- Resolve to the effective chromosome (1-22, X, Y, M/MT) before any
    -- ALT/scaffold contig (e.g. "1_KI270766v1_alt", "Un_GL000195v1").
    ORDER BY
      (CASE WHEN chr ~* '^(chr)?([0-9]{1,2}|X|Y|M|MT)$' THEN 0 ELSE 1 END),
      ("end" - start) DESC,
      hgnc_symbol
    LIMIT 1
    """
)


async def roi_gene_region(session: AsyncSession, *, assembly_id: str, query: str) -> dict[str, Any] | None:
    """The region of interest ``query`` names as a gene of the assembly ``assembly_id``, as the
    family's ROI record holds it (``source`` ``gene``); None when no gene matches.

    The gene is matched, in any capitals, by its HGNC symbol, its gene ID, its Ensembl
    transcript or gene ID, or one of its RefSeq accessions with or without the version. A gene
    on its chromosome (1-22, X, Y, M/MT) is taken before a copy on an ALT or scaffold contig,
    then the longest. ``query`` is matched and recorded as given: the caller strips it."""
    gene_row = (
        await session.execute(_ROI_GENE_SQL, {"assembly_id": assembly_id, "query": query})
    ).mappings().first()
    if gene_row is None:
        return None
    return {
        "query": query,
        "label": gene_row.get("hgnc_symbol") or gene_row.get("gene_id") or query,
        "source": "gene",
        "assembly_id": assembly_id,
        "chr": normalize_chromosome(str(gene_row["chr"])),
        "start": int(gene_row["start"]),
        "end": int(gene_row["end"]),
    }
