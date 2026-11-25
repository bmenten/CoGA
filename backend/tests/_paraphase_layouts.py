"""One sample's Paraphase results in Paraphase 3's layout and in Paraphase 4's, and the way they
take through CoGA: the import's rows (``_paraphase_rows_for_sample``), stored as the import stores
them, and read back by the Paraphase page (``get_family_paraphase_table_response``), which the
signed record reads too.

Paraphase 3 writes a region's own fields (``gene_cn``, ``fusions_called``, SMN1's copy numbers,
read counts and haplotypes, RCCX's alleles and phasing) at a result's top level; Paraphase 4
writes them under the result's ``region_specific_info``, ``{}`` for a region without any. The
calls are the same in both files. All values are synthetic.
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any

from backend.app.schemas import FamilyParaphaseTableOut
from backend.app.services import paraphase_pg
from backend.app.services.family_metadata_context import SampleMetadataContext
from backend.app.services.family_package_variants import _paraphase_rows_for_sample
from backend.tests._query_results import MappingRows

SAMPLE = "PROBAND"
SAMPLE_UUID = "33333333-3333-3333-3333-333333333333"
FAMILY_UUID = "11111111-1111-1111-1111-111111111111"

# The fields at a result's top level in either layout.
_EVERY_RESULT = {
    "sample_sex": "female",
    "genome_depth": 31.5,
    "region_depth": {"median": 30.0, "percentile80": 34.0},
}

# Per result: its top-level fields, and the fields of the region's own analysis.
_RESULTS: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    # GBA1 with its pseudogene at four copies, and a fusion: reads review.
    "gba": (
        {"total_cn": 4, "highest_total_cn": 4, "phase_region": "38:chr1:155200000-155250000"},
        {"fusions_called": {"gba_hap2": {"type": "deletion", "sequence": "ACGA", "breakpoint": [[155234000, 155235000]]}}},
    ),
    # TNXB counted apart from TNXA: one copy of two reads review.
    "tnxb": ({"total_cn": 4, "phase_region": "38:chr6:32040000-32080000"}, {"gene_cn": 1}),
    # SMN1 at one copy: a carrier, by the SMN1/SMN2 rule.
    "smn1": (
        {"total_cn": 3, "highest_total_cn": 3, "phase_region": "38:chr5:70900000-70960000"},
        {
            "smn1_cn": 1,
            "smn2_cn": 2,
            "smn_del78_cn": 0,
            "smn1_read_number": 12,
            "smn2_read_number": 25,
            "smn_del78_read_number": 0,
            "smn1_haplotypes": {"ACGT": "smn1_smn1hap1"},
            "smn2_haplotypes": {"ACTT": "smn1_smn2hap1", "AGTT": "smn1_smn2hap2"},
            "smn_del78_haplotypes": {},
        },
    ),
    # RCCX with two normal alleles, phased: no status to look at.
    "rccx": (
        {"total_cn": 4, "phase_region": "38:chr6:31980000-32050000"},
        {"annotated_alleles": ["WT", "pseudogene_deletion"], "phasing_success": True, "alleles_final": [["rccx_hap1"], ["rccx_hap2"]]},
    ),
    # A region whose analysis has no fields of its own.
    "cfc1": ({"total_cn": 4, "phase_region": "38:chr2:130600000-130700000"}, {}),
}


def paraphase_3_json() -> dict[str, Any]:
    """The sample's Paraphase JSON as Paraphase 3 writes it."""
    return {gene: {**_EVERY_RESULT, **top, **own} for gene, (top, own) in _RESULTS.items()}


def paraphase_4_json() -> dict[str, Any]:
    """The same calls as Paraphase 4 writes them."""
    return {
        gene: {**_EVERY_RESULT, **top, paraphase_pg.REGION_SPECIFIC_INFO: dict(own)}
        for gene, (top, own) in _RESULTS.items()
    }


def import_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows the import writes of the sample's JSON."""
    return _paraphase_rows_for_sample(
        sample_context=SampleMetadataContext(
            sample_uuid=SAMPLE_UUID,
            sample_id=SAMPLE,
            family_uuid=FAMILY_UUID,
            family_id="FAM1",
            sex="female",
            project_ids=["project-uuid"],
            assembly_id="22222222-2222-2222-2222-222222222222",
            assembly_name="GRCh38",
        ),
        path=Path(f"{SAMPLE}.paraphase.json"),
        payload=payload,
    )


class StoredParaphaseSession:
    """Answers the page's query with the rows the import wrote, as ``sample_paraphase_results``
    holds them: the copy-number columns, and the result's JSON as Paraphase wrote it."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [
            {
                "sample_uuid": row["sample_id"],
                "gene_symbol": row["gene_symbol"],
                "total_cn": row["total_cn"],
                "gene_cn": row["gene_cn"],
                "highest_total_cn": row["highest_total_cn"],
                "sample_sex": row["sample_sex"],
                "phase_region": row["phase_region"],
                "region_depth": json.loads(row["region_depth_json"]),
                "genome_depth": row["genome_depth"],
                "payload": json.loads(row["payload_json"]),
                "uploaded_at": None,
            }
            for row in rows
        ]

    async def execute(self, statement: Any, params: Any = None) -> MappingRows:
        assert "FROM sample_paraphase_results" in str(statement)
        return MappingRows(self._rows)


def family_context() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        family_uuid=FAMILY_UUID,
        family_id="FAM1",
        sample_uuid_to_name={SAMPLE_UUID: SAMPLE},
        sample_rows=[{"sample_uuid": SAMPLE_UUID, "sample_id": SAMPLE, "role": "proband", "affected": True, "sex": "female"}],
    )


async def paraphase_page(payload: dict[str, Any]) -> FamilyParaphaseTableOut:
    """The Paraphase page of the family after an import of ``payload``."""
    session = StoredParaphaseSession(import_rows(payload))
    return await paraphase_pg.get_family_paraphase_table_response(session, context=family_context())  # type: ignore[arg-type]
