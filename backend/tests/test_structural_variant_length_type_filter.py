"""Structural variants filtered by type and by length (REQ-PGT-007).

A type filter keeps the SVs whose type is one of the types asked for: a comma-separated
list, in any capitals, where a subtype such as ``DUP:TANDEM`` belongs to its type. A minimum
length keeps the SVs at least that long, the bound included; an exact length keeps the SVs
of exactly that length; with a type, both must hold. The SQL of the callset's native page
applies neither filter, so a search with one is answered from the rows filtered by
``_structural_record_matches``, and its total and its summary by type and caller count the
matching SVs alone. A deletion of more than 10 Mb is found by a 10 Mb minimum and listed
with its length.

Every SV a length filter meets here is a deletion, duplication, inversion or CNV whose
END minus POS is its length (the absolute SVLEN), so the tests hold whichever of the two
the length is read from. The records and the family are synthetic.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.services.clickhouse_family_variants import get_family_structural_variants_page
from backend.app.services.clickhouse_variant_queries import _structural_record_matches
from backend.app.services.clickhouse_variant_records import (
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters

_TEN_MB = 10_000_000
_FROM_LENGTH = object()


def _sv(
    variant_id: str,
    sv_type: str,
    *,
    start: int,
    length: int,
    source: str = "sniffles",
    sv_len: Any = _FROM_LENGTH,
) -> StructuralVariantRecord:
    if sv_len is _FROM_LENGTH:
        # A deletion's SVLEN is negative, as Sniffles and Spectre write it.
        sv_len = -length if sv_type.startswith("DEL") else length
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr="1",
        start=start,
        end=start + length,
        sv_type=sv_type,
        source=source,
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=sv_len,
        filters=["PASS"],
        gene_symbols=[],
        annotations=[{}],
        calls=[StructuralVariantCall(sample="PROBAND", gt="0/1", qual=None, read_support=None, filter="PASS")],
    )


# One SV of each type, with lengths around the bounds the tests use.
_DEL_49 = _sv("del-49", "DEL", start=1_000, length=49)
_DEL_50 = _sv("del-50", "DEL", start=2_000, length=50)
_DEL_51 = _sv("del-51", "DEL", start=3_000, length=51)
_DUP_TANDEM = _sv("dup-tandem", "DUP:TANDEM", start=4_000, length=5_000)
_INV = _sv("inv", "INV", start=20_000, length=120_000)
_CNV = _sv("cnv", "CNV", start=200_000, length=2_500_000, source="spectre")
_DEL_15MB = _sv("del-15mb", "DEL", start=5_000_000, length=15_000_000, source="spectre")
_DEL_JUST_10MB = _sv("del-10mb", "DEL", start=30_000_000, length=_TEN_MB, source="spectre")
_DEL_JUST_UNDER = _sv("del-under-10mb", "DEL", start=45_000_000, length=_TEN_MB - 1, source="spectre")
# An insertion and a breakend: no length bound in these tests lies between their END minus
# POS (0) and their SVLEN, so a length filter leaves them out whichever of the two it reads.
_INS_ALU = _sv("ins-alu", "INS:ME:ALU", start=60_000_000, length=0, sv_len=300)
_BND = _sv("bnd", "BND", start=70_000_000, length=0, sv_len=None)

_CALLSET = [
    _DEL_49,
    _DEL_50,
    _DEL_51,
    _DUP_TANDEM,
    _INV,
    _CNV,
    _DEL_15MB,
    _DEL_JUST_10MB,
    _DEL_JUST_UNDER,
    _INS_ALU,
    _BND,
]
_LENGTH_FILTERED = [record for record in _CALLSET if record not in (_INS_ALU, _BND)]


def _kept(records: list[StructuralVariantRecord], **filters: Any) -> list[str]:
    query = StructuralVariantQueryFilters(page=1, page_size=100, **filters)
    return [record.variant_id for record in records if _structural_record_matches(record, query, [], [])]


# --------------------------------------------------------------------------- #
# The filters, record by record
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("variant_type", "expected"),
    [
        ("DEL", ["del-49", "del-50", "del-51", "del-15mb", "del-10mb", "del-under-10mb"]),
        ("del", ["del-49", "del-50", "del-51", "del-15mb", "del-10mb", "del-under-10mb"]),
        ("DUP", ["dup-tandem"]),
        ("INS", ["ins-alu"]),
        ("BND", ["bnd"]),
        ("INV,CNV", ["inv", "cnv"]),
        (" DUP , bnd ,", ["dup-tandem", "bnd"]),
        (None, [record.variant_id for record in _CALLSET]),
    ],
)
def test_a_type_filter_keeps_the_types_asked_for(variant_type: str | None, expected: list[str]) -> None:
    assert _kept(_CALLSET, variant_type=variant_type) == expected


@pytest.mark.parametrize(
    ("min_length", "expected"),
    [
        (50, ["del-50", "del-51", "dup-tandem", "inv", "cnv", "del-15mb", "del-10mb", "del-under-10mb"]),
        (51, ["del-51", "dup-tandem", "inv", "cnv", "del-15mb", "del-10mb", "del-under-10mb"]),
        (_TEN_MB, ["del-15mb", "del-10mb"]),
        (15_000_001, []),
    ],
)
def test_a_minimum_length_keeps_the_svs_at_least_that_long(min_length: int, expected: list[str]) -> None:
    assert _kept(_LENGTH_FILTERED, min_length=min_length) == expected


@pytest.mark.parametrize(
    ("length", "expected"),
    [(50, ["del-50"]), (5_000, ["dup-tandem"]), (_TEN_MB, ["del-10mb"]), (7, [])],
)
def test_an_exact_length_keeps_the_svs_of_that_length(length: int, expected: list[str]) -> None:
    assert _kept(_LENGTH_FILTERED, length=length) == expected


def test_a_type_and_a_minimum_length_must_both_hold() -> None:
    assert _kept(_LENGTH_FILTERED, variant_type="DEL", min_length=50) == [
        "del-50",
        "del-51",
        "del-15mb",
        "del-10mb",
        "del-under-10mb",
    ]
    assert _kept(_LENGTH_FILTERED, variant_type="DUP,CNV", min_length=_TEN_MB) == []


# --------------------------------------------------------------------------- #
# The SV list
# --------------------------------------------------------------------------- #


def _family() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_id": "PROBAND", "role": "proband", "affected": True, "sex": "female"}],
        sample_uuid_to_name={"sample-proband": "PROBAND"},
        sample_name_to_uuid={"PROBAND": "sample-proband"},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


@pytest.fixture()
def sv_list(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The SV list over ``_CALLSET``, read as its SQL returns it: every SV of the callset,
    since the SQL applies neither a type nor a length. The native page may not answer."""
    reads: list[dict[str, Any]] = []
    m = "backend.app.services.clickhouse_family_variants."

    async def fetch_rows(_context, _filters, *, limit=None, offset=0, **kwargs):
        reads.append(kwargs)
        rows = _CALLSET[offset:]
        return rows if limit is None else rows[:limit]

    async def native_summary(*_args: Any, **_kwargs: Any):
        raise AssertionError("the native page's SQL applies no type or length filter")

    async def no_review_ids(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    async def nothing(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    monkeypatch.setattr(m + "_fetch_structural_variant_rows", fetch_rows)
    monkeypatch.setattr(m + "_fetch_structural_variant_summary", native_summary)
    monkeypatch.setattr(m + "list_matching_structural_variant_review_ids", no_review_ids)
    monkeypatch.setattr(m + "get_structural_variant_review_map", nothing)
    monkeypatch.setattr(m + "_fetch_structural_cytoband_map", nothing)
    return reads


async def _page(**filters: Any):
    return await get_family_structural_variants_page(
        None,  # type: ignore[arg-type]
        context=_family(),
        page=1,
        page_size=100,
        **filters,
    )


@pytest.mark.asyncio
async def test_a_deletion_over_10_mb_is_found_by_type_and_minimum_length_with_its_length(sv_list) -> None:
    page = await _page(type="DEL", min_length=_TEN_MB)

    assert [str(variant.id) for variant in page.variants] == ["del-15mb", "del-10mb"]
    assert [(variant.type, variant.length) for variant in page.variants] == [
        ("DEL", -15_000_000),
        ("DEL", -_TEN_MB),
    ]
    assert page.total == 2
    assert page.summary == {"DEL": {"spectre": 2}}
    assert page.candidates_capped is False
    assert sv_list, "the rows were read"


@pytest.mark.asyncio
async def test_a_type_search_lists_counts_and_summarises_the_matching_svs_alone(sv_list) -> None:
    page = await _page(type="DEL,DUP")

    assert [str(variant.id) for variant in page.variants] == [
        "del-49",
        "del-50",
        "del-51",
        "dup-tandem",
        "del-15mb",
        "del-10mb",
        "del-under-10mb",
    ]
    assert page.total == 7
    assert page.summary == {"DEL": {"sniffles": 3, "spectre": 3}, "DUP:TANDEM": {"sniffles": 1}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"length": 5_000}, ["dup-tandem"]),
        ({"min_length": 15_000_000}, ["del-15mb"]),
        ({"type": "inv"}, ["inv"]),
    ],
)
async def test_any_one_length_or_type_filter_is_applied_to_the_list(
    sv_list, filters: dict[str, Any], expected: list[str]
) -> None:
    page = await _page(**filters)

    assert [str(variant.id) for variant in page.variants] == expected
    assert page.total == len(expected)
