"""The knowledgebase build reads the loss/gain side of a ClinVar CNV correctly (#566).

The side came from words in the record's name only. ClinVar names an array-based record in
ISCN notation, e.g. ``GRCh38/hg38 7q11.23(chr7:73330452-74799773)x1``, without any of
those words, so it counted toward neither the loss nor the gain support of a region. The
side now comes from ClinVar's ``Type`` column, then from the copy number in the name, and
only then from the name's words.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parents[2] / "scripts"))
import clinical_cnv_knowledgebase as kb_script

WBS = "GRCh38/hg38 7q11.23(chr7:73330452-74799773)"


@pytest.mark.parametrize(
    ("type_value", "name", "chromosome", "side"),
    [
        # ClinVar's Type states the side, and wins over anything in the name.
        ("copy number loss", f"{WBS}x1", "7", "loss"),
        ("copy number gain", f"{WBS}x3", "7", "gain"),
        ("copy number gain", f"{WBS}x1", "7", "gain"),
        ("Deletion", "NC_000007.14:g.(?_73330452)_(74799773_?)del", "7", "loss"),
        ("Duplication", "NC_000007.14:g.(?_73330452)_(74799773_?)dup", "7", "gain"),
        # Without a usable Type: the ISCN copy number. Two copies are normal on an autosome.
        ("", f"{WBS}x0", "7", "loss"),
        ("", f"{WBS}x1", "7", "loss"),
        ("", f"{WBS}x3", "7", "gain"),
        ("", f"{WBS}x4", "7", "gain"),
        ("nan", f"{WBS}x1", "chr7", "loss"),
        ("", "GRCh38/hg38 11p15.5(chr11:1-2000000)x2 hmz", "11", "unknown"),
        # On X and Y the normal count depends on the carrier's sex, which ClinVar's summary
        # does not give: only x0 and x3+ are read.
        ("", "GRCh38/hg38 Xp22.31(chrX:6537236-8167639)x0", "X", "loss"),
        ("", "GRCh38/hg38 Xp22.31(chrX:6537236-8167639)x1", "X", "unknown"),
        ("", "GRCh38/hg38 Xp22.31(chrX:6537236-8167639)x2", "chrX", "unknown"),
        ("", "GRCh38/hg38 Xp22.31(chrX:6537236-8167639)x3", "X", "gain"),
        ("", "GRCh38/hg38 Yq11.223(chrY:22000000-24000000)x0", "Y", "loss"),
        # Last, the name's words, as before.
        ("", "Williams-Beuren syndrome deletion", "7", "loss"),
        ("", "1q21.1 duplication", "1", "gain"),
        ("", "complex rearrangement", "7", "unknown"),
    ],
)
def test_the_side_comes_from_type_then_copy_number_then_words(
    type_value: str, name: str, chromosome: str, side: str
) -> None:
    assert kb_script.clinvar_cnv_side(type_value, name, chromosome) == side


def _clinvar_frame(*records: tuple[str, str, int, int, str]) -> pd.DataFrame:
    # The variant_summary.txt columns the build reads, as strings (it reads with dtype=str).
    return pd.DataFrame(
        [
            {
                "Assembly": "GRCh38",
                "Chromosome": chromosome,
                "Start": str(start),
                "Stop": str(stop),
                "ClinicalSignificance": "Pathogenic",
                "Type": type_value,
                "Name": f"GRCh38/hg38 7q11.23(chr{chromosome}:{start}-{stop}){copies}",
                "VariationID": f"TEST-{index}",
            }
            for index, (chromosome, type_value, start, stop, copies) in enumerate(records)
        ]
    )


def test_array_records_become_support_rows_with_their_side() -> None:
    rows = kb_script.clinvar_support_rows(
        _clinvar_frame(
            ("7", "copy number loss", 73_330_452, 74_799_773, "x1"),
            ("7", "copy number gain", 73_330_452, 74_799_773, "x3"),
            ("7", "", 73_400_000, 74_700_000, "x1"),
        )
    )

    assert list(rows["cnv_type"]) == ["loss", "gain", "loss"]
    assert list(rows["accession"]) == ["TEST-0", "TEST-1", "TEST-2"]
    assert rows["chromosome"].tolist() == ["7", "7", "7"]


def test_array_records_count_toward_a_regions_loss_and_gain_support() -> None:
    region = pd.DataFrame(
        [{"chromosome": "7", "start": 73_330_452, "end": 74_799_773, "syndrome_name": "Williams-Beuren"}]
    )
    clinvar = kb_script.clinvar_support_rows(
        _clinvar_frame(
            ("7", "copy number loss", 73_330_452, 74_799_773, "x1"),
            ("7", "copy number loss", 73_300_000, 74_800_000, "x1"),
            ("7", "copy number gain", 73_330_452, 74_799_773, "x3"),
        )
    )

    out = kb_script.add_clinvar_overlap_support(region, clinvar)

    assert out.loc[0, "clinvar_pathogenic_loss_count"] == 2
    assert out.loc[0, "clinvar_pathogenic_gain_count"] == 1
    assert out.loc[0, "clinvar_pathogenic_accessions"] == "TEST-0;TEST-1;TEST-2"


# #624 — the counts are written only once ClinVar support has been computed: a region the
# build never checked against ClinVar is not a region without ClinVar support.
def test_counts_stay_empty_until_clinvar_support_is_computed() -> None:
    clingen = pd.DataFrame(
        [{"ISCA ID": "ISCA-37446", "ISCA Region Name": "Williams-Beuren", "Genomic Location": "chr7:73330452-74799773"}]
    )
    kb = kb_script.normalize_clingen_table(clingen, "https://example.org/clingen.tsv", "GRCh38")

    assert kb.loc[0, "clinvar_pathogenic_loss_count"] == ""
    assert kb.loc[0, "clinvar_pathogenic_gain_count"] == ""
    # An empty ClinVar load leaves them unrecorded too.
    unchanged = kb_script.add_clinvar_overlap_support(kb, pd.DataFrame())
    assert unchanged.loc[0, "clinvar_pathogenic_loss_count"] == ""
    # Once counted, a region without overlapping records reads 0.
    counted = kb_script.add_clinvar_overlap_support(
        kb,
        kb_script.clinvar_support_rows(_clinvar_frame(("1", "copy number loss", 1_000_000, 2_000_000, "x1"))),
    )
    assert counted.loc[0, "clinvar_pathogenic_loss_count"] == 0
