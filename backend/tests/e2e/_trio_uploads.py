"""The synthetic trio files the upload and deletion end-to-end modules send through the
single-file upload routes, and the family mark they read back.

A trio (``FATHER_…``, ``MOTHER_…``, ``CHILD_…``) gets a family VCF of directly called SNVs
(the father and the child heterozygous, the mother homozygous reference) and per-sample
Sniffles SV calls. Used by ``test_e2e_deletions_mark_the_family.py``,
``test_e2e_writes_mark_before_delete.py`` and
``test_e2e_small_variant_upload_reads_before_replacing.py``.
"""

from __future__ import annotations

from typing import Any

# The family VCF's header, its sample columns named by role.
TRIO_VCF_HEADER = (
    "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{father}\t{mother}\t{child}\n"
)
SNIFFLES_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.2\n"
    "##contig=<ID=1,length=248956422>\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
)
# A deletion on chr1 that two members can both carry, with the genotype ``gt``.
DEL_30000 = "1\t30000\tS.DEL.1\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=9\tGT\t{gt}\n"


def trio_calls(count: int, *, start: int = 10_000) -> str:
    """``count`` records of the directly called callset on chr1, 100 bases apart from
    ``start``."""
    return "".join(
        f"1\t{start + index * 100}\t.\tA\tG\t50\tPASS\t.\tGT:GQ:DP\t0/1:40:30\t0/0:40:31\t0/1:40:29\n"
        for index in range(count)
    )


def the_one_mark(step: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The one entry of the family's ``import_unfinished`` mark a step read back: its key and
    what it lists (the step fails when the mark holds no entry or several)."""
    [(key, entry)] = (step["unfinished"] or {}).items()
    return key, entry
