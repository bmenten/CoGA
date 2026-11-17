"""A human Ensembl gene ID as a gene panel takes one.

A panel names each of its genes by an HGNC symbol or by an Ensembl gene ID. The ID is the gene's
stable name: a symbol can be renamed, or read as another gene's, where the ID cannot. A panel
keeps an ID as ``ENSG`` and eleven digits, in capitals and without its version
(``ENSG00000012048.24`` is ``ENSG00000012048``): the version changes with each Ensembl release
of the same gene, and the gene reference keeps the ID without it (``gencode_import``).
"""

from __future__ import annotations

import re

_ENSEMBL_GENE_ID_RE = re.compile(r"^(ENSG\d{11})(?:\.\d+)?$", re.IGNORECASE)


def ensembl_gene_id(name: str | None) -> str | None:
    """The human Ensembl gene ID ``name`` is, as a panel keeps it; None for any other name.

    Only a human gene's ID qualifies: ``ENSG``, eleven digits and an optional version. A symbol
    that starts with ``ENS`` (ENSA, ENSAP1) is a symbol, as is a transcript or protein ID.
    """
    match = _ENSEMBL_GENE_ID_RE.match(str(name or "").strip())
    return match.group(1).upper() if match else None


def panel_gene_name(name: str) -> str:
    """A gene name as a panel keeps it: an Ensembl gene ID as ``ensembl_gene_id`` gives it, any
    other name trimmed."""
    stripped = str(name or "").strip()
    return ensembl_gene_id(stripped) or stripped
