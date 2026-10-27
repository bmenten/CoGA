"""gnomAD's counts are read as gnomAD's total: the combined count, else exomes and genomes added
(CLIN-67).

A VEP ``--custom`` gnomAD file names its fields after the file's short name, so a pipeline that
annotates gnomAD's exomes and genomes writes ``gnomADe_nhomalt`` and ``gnomADg_nhomalt``. The
parser read neither: the maximum homozygote filter and the BS2 suggestion never saw a count.
Of ``gnomADe_AC`` and ``gnomADg_AC`` it took the exome count alone. The owner chose gnomAD's
own total (2026-10-27), as its browser shows it: the combined count where the record has one,
else the exome and genome counts added, either alone when the other is missing.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.variant_annotation_parser import (
    AnnotationHeaderState,
    extract_small_variant_annotations,
    normalize_small_variant_annotation_entry,
    update_annotation_header_state,
)
from backend.app.services.clickhouse_variant_queries import _matches_small_annotations
from backend.app.services.clickhouse_variant_records import SmallVariantRecord
from backend.app.services.clickhouse_variant_rows import _annotation_count
from backend.app.services.family_variant_filters import SmallVariantQueryFilters

_COUNT_FIELDS = (
    "gnomAD_AC",
    "gnomAD_nhomalt",
    "gnomAD_Hemi",
    "gnomADe_AC",
    "gnomADe_nhomalt",
    "gnomADe_Hemi",
    "gnomADg_AC",
    "gnomADg_nhomalt",
    "gnomADg_Hemi",
)
_CSQ_HEADER = (
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. '
    "Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|gnomADe_AF|"
    + "|".join(_COUNT_FIELDS)
    + '">'
)


def _state() -> AnnotationHeaderState:
    state = AnnotationHeaderState()
    update_annotation_header_state(state, _CSQ_HEADER)
    return state


def _csq(**counts: str) -> str:
    """One missense CSQ entry with the given gnomAD count fields, the others empty."""
    return "|".join(
        ["T", "missense_variant", "MODERATE", "GENE1", "ENSG1", "Transcript", "ENST1", "0.0001"]
        + [counts.get(field, "") for field in _COUNT_FIELDS]
    )


def _counts(annotation: dict[str, Any]) -> tuple[Any, Any, Any]:
    """The annotation's gnomAD allele, homozygote and hemizygote counts, as stored."""
    return (
        _annotation_count(annotation, "gnomad_ac"),
        _annotation_count(annotation, "gnomad_hom_count"),
        _annotation_count(annotation, "gnomad_hemi_count"),
    )


def _only(info: dict[str, str]) -> dict[str, Any]:
    annotations = extract_small_variant_annotations(info, _state())
    assert len(annotations) == 1
    return annotations[0]


def test_the_exome_and_genome_counts_are_added() -> None:
    annotation = _only(
        {
            "CSQ": _csq(
                gnomADe_AC="12",
                gnomADg_AC="7",
                gnomADe_nhomalt="3",
                gnomADg_nhomalt="2",
                gnomADe_Hemi="1",
                gnomADg_Hemi="4",
            )
        }
    )

    # Before: (12, None, None), the exome AC alone and neither prefixed count read.
    assert _counts(annotation) == (19, 5, 5)


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"gnomADe_nhomalt": "3"}, (None, 3, None)),
        ({"gnomADg_nhomalt": "2"}, (None, 2, None)),
        ({"gnomADg_AC": "7"}, (7, None, None)),
        ({"gnomADe_Hemi": "1"}, (None, None, 1)),
        ({"gnomADg_Hemi": "4"}, (None, None, 4)),
    ],
    ids=["exome-hom", "genome-hom", "genome-ac", "exome-hemi", "genome-hemi"],
)
def test_either_count_alone_when_the_other_is_missing(fields: dict[str, str], expected: tuple) -> None:
    assert _counts(_only({"CSQ": _csq(**fields)})) == expected


def test_the_combined_count_wins_over_its_parts() -> None:
    annotation = _only(
        {
            "CSQ": _csq(
                gnomAD_AC="30",
                gnomAD_nhomalt="6",
                gnomAD_Hemi="2",
                gnomADe_AC="12",
                gnomADg_AC="7",
                gnomADe_nhomalt="3",
                gnomADg_nhomalt="2",
                gnomADe_Hemi="1",
                gnomADg_Hemi="4",
            )
        }
    )

    assert _counts(annotation) == (30, 6, 2)


def test_the_upper_case_spellings_are_added_too() -> None:
    annotation = normalize_small_variant_annotation_entry(
        {
            "SYMBOL": "GENE1",
            "Consequence": "missense_variant",
            "GNOMADE_NHOMALT": "3",
            "GNOMADG_NHOMALT": "2",
            "GNOMADE_AC": "12",
            "GNOMADG_AC": "7",
        }
    )

    assert _counts(annotation) == (19, 5, None)


def test_a_vep_table_row_adds_them_as_the_vcf_does() -> None:
    annotation = normalize_small_variant_annotation_entry(
        {
            "SYMBOL": "GENE1",
            "Consequence": "missense_variant",
            "gnomADe_nhomalt": "3",
            "gnomADg_nhomalt": "2",
            "gnomADe_Hemi": "1",
            "gnomADg_Hemi": "4",
        }
    )

    assert _counts(annotation) == (None, 5, 5)


def test_an_entry_without_counts_keeps_the_info_total() -> None:
    # The counts in INFO, the CSQ entry without any: the entry keeps INFO's, as for every
    # population field.
    annotation = _only({"gnomADe_nhomalt": "3", "gnomADg_nhomalt": "2", "CSQ": _csq()})

    assert _counts(annotation)[1] == 5


@pytest.mark.parametrize("maximum, kept", [(4, False), (5, True)])
def test_a_maximum_homozygote_filter_reads_the_total(maximum: int, kept: bool) -> None:
    annotations = extract_small_variant_annotations(
        {"CSQ": _csq(gnomADe_nhomalt="3", gnomADg_nhomalt="2")}, _state()
    )
    record = SmallVariantRecord(
        variant_key=None,
        variant_id="1-1000-C-T",
        chr="1",
        start=1000,
        end=1000,
        ref="C",
        alt="T",
        source="clair3",
        filters=[],
        gene_symbols=["GENE1"],
        rsid=None,
        calls=[],
        annotations=annotations,
    )

    # Before, the maximum kept the variant at any value: it had no homozygote count.
    filters = SmallVariantQueryFilters(page=1, page_size=100, max_gnomad_hom_count=maximum)

    assert _matches_small_annotations(record, filters) is kept
