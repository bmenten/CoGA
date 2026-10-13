"""Structural variants filtered by type and by length (REQ-PGT-007).

A type filter keeps the SVs whose type is one of the types asked for: a comma-separated
list, in any capitals, where a subtype such as ``DUP:TANDEM`` belongs to its type. A minimum
length keeps the SVs at least that long, the bound included; an exact length keeps the SVs
of exactly that length; with a type, both must hold. The SQL of the callset's native page
applies neither filter, so a search with one is answered from the rows filtered by
``_structural_record_matches``, and its total and its summary by type and caller count the
matching SVs alone. A deletion of more than 10 Mb is found by a 10 Mb minimum and listed
with its length.

What a length filter measures (CLIN-25): the length the SV list shows, as a positive
number. That is the call's SVLEN, and END minus POS only for a call without one. Most
callers write an insertion with its END at its POS and its length in SVLEN: a 5 kb
insertion is 5 kb to the filters, as in the list, not 0 bp. A translocation (BND, TRA) has
no length: it passes any minimum and matches no exact length (the owner's decision,
2026-10-13). The rule holds wherever the filters apply: the SV list, ranked or not, read
from the rows as stored; its CSV export; the genome track; the track availability; and for
the calls of each importer. The records and the family are synthetic.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.services.clickhouse_family_variants import (
    export_family_structural_variants,
    get_family_structural_variants_page,
)
from backend.app.services.clickhouse_variant_queries import _structural_record_matches
from backend.app.services.clickhouse_variant_records import (
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import ParsedPed
from backend.app.services.family_package_variants import (
    _iter_cnv_structural_records,
    _iter_mito_sv_records,
    _iter_needlr_structural_records,
)
from backend.app.services.family_service import get_family_track_availability_for_user
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters
from backend.app.services.structural_variant_ingest import iter_structural_variant_records

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
    sample: str = "PROBAND",
) -> StructuralVariantRecord:
    """An SV whose END lies ``length`` past its POS, with that length as its SVLEN unless
    ``sv_len`` says otherwise."""
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
        calls=[StructuralVariantCall(sample=sample, gt="0/1", qual=None, read_support=None, filter="PASS")],
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
# An insertion written as most callers write one (END at POS, its length in SVLEN), and a
# breakend: the length filters measure them by their own rules (see "What a length filter
# measures" below), so the tests of the bounds leave them out.
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


_M = "backend.app.services.clickhouse_family_variants."


def _no_native_page_nor_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """The native page may not answer; Postgres holds no reviews, cytobands or phenotypes."""

    async def native_summary(*_args: Any, **_kwargs: Any):
        raise AssertionError("the native page's SQL applies no type or length filter")

    async def no_review_ids(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    async def nothing(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def no_phenotypes(*_args: Any, **_kwargs: Any) -> tuple[list[str], dict[str, str]]:
        return [], {}

    monkeypatch.setattr(_M + "_fetch_structural_variant_summary", native_summary)
    monkeypatch.setattr(_M + "list_matching_structural_variant_review_ids", no_review_ids)
    monkeypatch.setattr(_M + "get_structural_variant_review_map", nothing)
    monkeypatch.setattr(_M + "_fetch_structural_cytoband_map", nothing)
    monkeypatch.setattr(_M + "_affected_present_hpo", no_phenotypes)


@pytest.fixture()
def sv_list(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The SV list over ``_CALLSET``, read as its SQL returns it: every SV of the callset,
    since the SQL applies neither a type nor a length. The native page may not answer."""
    reads: list[dict[str, Any]] = []

    async def fetch_rows(_context, _filters, *, limit=None, offset=0, **kwargs):
        reads.append(kwargs)
        rows = _CALLSET[offset:]
        return rows if limit is None else rows[:limit]

    monkeypatch.setattr(_M + "_fetch_structural_variant_rows", fetch_rows)
    _no_native_page_nor_postgres(monkeypatch)
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
        # The breakend passes any minimum (CLIN-25). Before, a minimum dropped it: its END
        # minus POS is 0.
        ({"min_length": 15_000_000}, ["del-15mb", "bnd"]),
        ({"type": "inv"}, ["inv"]),
    ],
)
async def test_any_one_length_or_type_filter_is_applied_to_the_list(
    sv_list, filters: dict[str, Any], expected: list[str]
) -> None:
    page = await _page(**filters)

    assert [str(variant.id) for variant in page.variants] == expected
    assert page.total == len(expected)


# --------------------------------------------------------------------------- #
# What a length filter measures (CLIN-25)
# --------------------------------------------------------------------------- #

# SVs as their callers write them. Sniffles2 and pbsv write an insertion with its END at its
# POS and its length in SVLEN, a deletion with a negative SVLEN, and a breakend without SVLEN
# or END. The SV upload stores a call written without END with its END at its POS (the
# breakend, and the deletion "without end"). The demo data writes a breakend's END one past
# its POS, with SVLEN 1. A manual TSV upload carries no SVLEN at all.
_INS_5KB = _sv("ins-5kb", "INS", start=100_000, length=0, sv_len=5_000)
_INS_30 = _sv("ins-30", "INS", start=110_000, length=0, sv_len=30)
_DEL_5KB = _sv("del-5kb", "DEL", start=120_000, length=5_000)
_DEL_5KB_WITHOUT_END = _sv("del-5kb-without-end", "DEL", start=130_000, length=0, sv_len=-5_000)
_CALLER_BND = _sv("bnd-caller", "BND", start=140_000, length=0, sv_len=None)
_DEMO_BND = _sv("bnd-demo", "BND", start=150_000, length=1, sv_len=1)
_TRA = _sv("tra", "TRA", start=160_000, length=0, sv_len=None)
_TSV_DEL = _sv("tsv-del", "DEL", start=170_000, length=5_000, sv_len=None, source="manual_upload")
_TSV_INS = _sv("tsv-ins", "INS", start=180_000, length=0, sv_len=None, source="manual_upload")

_CALLER_SHAPED = [
    _INS_5KB,
    _INS_30,
    _DEL_5KB,
    _DEL_5KB_WITHOUT_END,
    _CALLER_BND,
    _DEMO_BND,
    _TRA,
    _TSV_DEL,
    _TSV_INS,
]
_TRANSLOCATIONS = ["bnd-caller", "bnd-demo", "tra"]
_AT_LEAST_50 = ["ins-5kb", "del-5kb", "del-5kb-without-end", *_TRANSLOCATIONS, "tsv-del"]
_EXACTLY_5000 = ["ins-5kb", "del-5kb", "del-5kb-without-end", "tsv-del"]


@pytest.mark.parametrize(
    ("min_length", "expected"),
    [
        (1, ["ins-5kb", "ins-30", "del-5kb", "del-5kb-without-end", *_TRANSLOCATIONS, "tsv-del"]),
        (50, _AT_LEAST_50),
        (5_000, _AT_LEAST_50),
        (5_001, _TRANSLOCATIONS),
        (_TEN_MB, _TRANSLOCATIONS),
        (3_000_000_000, _TRANSLOCATIONS),
    ],
)
def test_a_minimum_length_measures_the_svlen_and_passes_every_translocation(
    min_length: int, expected: list[str]
) -> None:
    # Before: END minus POS, so the 5 kb insertion and the deletion stored without END were
    # 0 bp and every breakend 0 or 1 bp; any minimum above 1 dropped them all.
    assert _kept(_CALLER_SHAPED, min_length=min_length) == expected


@pytest.mark.parametrize(
    ("length", "expected"),
    [
        (5_000, _EXACTLY_5000),
        (30, ["ins-30"]),
        # A call without SVLEN whose END is its POS is 0 bp, as the list shows it; a
        # translocation is not, though it has the same END and no SVLEN either.
        (0, ["tsv-ins"]),
        # The demo breakend's SVLEN and END minus POS are both 1.
        (1, []),
    ],
)
def test_an_exact_length_measures_the_svlen_and_never_matches_a_translocation(
    length: int, expected: list[str]
) -> None:
    assert _kept(_CALLER_SHAPED, length=length) == expected


@pytest.mark.parametrize("sv_type", ["BND", "bnd", " Bnd ", "TRA", "tra"])
def test_a_translocation_is_told_by_its_type_in_any_capitals(sv_type: str) -> None:
    record = _sv("translocation", sv_type, start=1_000, length=0, sv_len=None)

    assert _kept([record], min_length=3_000_000_000) == ["translocation"]
    assert _kept([record], length=0) == []
    assert _kept([record], min_length=1, length=0) == []


@pytest.mark.parametrize(
    "sv_type", ["INS", "DEL", "DUP", "INV", "CNV", "INS:ME:ALU", "BNDX", "XTRA", ""]
)
def test_no_other_type_is_a_translocation(sv_type: str) -> None:
    # The same call with any other type is measured: no SVLEN and END at POS is 0 bp.
    record = _sv("other", sv_type, start=1_000, length=0, sv_len=None)

    assert _kept([record], min_length=1) == []
    assert _kept([record], length=0) == ["other"]


def test_both_length_filters_and_a_type_must_all_hold() -> None:
    assert _kept(_CALLER_SHAPED, min_length=50, length=5_000) == _EXACTLY_5000
    assert _kept(_CALLER_SHAPED, variant_type="INS", min_length=50) == ["ins-5kb"]
    assert _kept(_CALLER_SHAPED, variant_type="BND,TRA", min_length=_TEN_MB) == _TRANSLOCATIONS
    assert _kept(_CALLER_SHAPED, variant_type="BND,TRA", length=0) == []


def test_without_a_length_filter_every_sv_is_kept() -> None:
    assert _kept(_CALLER_SHAPED) == [record.variant_id for record in _CALLER_SHAPED]


# --------------------------------------------------------------------------- #
# The same rule on every path that applies the filters, from the rows as stored
# --------------------------------------------------------------------------- #


def _stored_row(record: StructuralVariantRecord, key: int) -> tuple[Any, ...]:
    """``record`` as ClickHouse returns it to the SV candidate read
    (``_fetch_structural_variant_rows``): the entry's position, type and calls, and the
    SVLEN of its details row (``svLen``)."""
    return (
        key,
        record.variant_id,
        record.chr,
        record.start,
        record.end,
        record.sv_type,
        record.source,
        None,
        None,
        record.sv_len,
        list(record.filters),
        "",
        list(record.gene_symbols),
        [call.sample for call in record.calls],
        [call.gt for call in record.calls],
        [call.qual for call in record.calls],
        [call.read_support for call in record.calls],
        [call.filter for call in record.calls],
        [call.copy_number for call in record.calls],
    )


def _serve_stored(monkeypatch: pytest.MonkeyPatch, records: list[StructuralVariantRecord]) -> list[str]:
    """ClickHouse holding ``records``: every query gets all their rows. Its SQL applies no
    length, and every row meets the conditions it does send (the family, its samples).
    Returns the queries sent."""
    queries: list[str] = []
    rows = [_stored_row(record, key) for key, record in enumerate(records, start=1)]

    async def execute(query: str, _params: dict[str, Any]) -> list[tuple[Any, ...]]:
        queries.append(query)
        return rows

    monkeypatch.setattr(_M + "_execute_clickhouse", execute)
    _no_native_page_nor_postgres(monkeypatch)
    return queries


@pytest.fixture()
def stored(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    return _serve_stored(monkeypatch, _CALLER_SHAPED)


@pytest.mark.asyncio
@pytest.mark.parametrize("prioritize", [False, True])
async def test_the_list_measures_the_stored_svlen(stored, prioritize: bool) -> None:
    at_least_50 = await _page(min_length=50, prioritize=prioritize)
    exactly_5000 = await _page(length=5_000, prioritize=prioritize)

    # The candidate read takes each call's SVLEN from its details row.
    assert stored and all("d.svLen" in query for query in stored)
    assert sorted(str(variant.id) for variant in at_least_50.variants) == sorted(_AT_LEAST_50)
    assert at_least_50.total == len(_AT_LEAST_50)
    assert at_least_50.summary == {
        "INS": {"sniffles": 1},
        "DEL": {"sniffles": 2, "manual_upload": 1},
        "BND": {"sniffles": 2},
        "TRA": {"sniffles": 1},
    }
    assert sorted(str(variant.id) for variant in exactly_5000.variants) == sorted(_EXACTLY_5000)
    assert exactly_5000.total == len(_EXACTLY_5000)
    # The list shows what the filters measured: the insertion's SVLEN, and the deletion's
    # (negative, as VCF writes it), its END notwithstanding.
    shown = {str(variant.id): variant.length for variant in exactly_5000.variants}
    assert shown == {"ins-5kb": 5_000, "del-5kb": -5_000, "del-5kb-without-end": -5_000, "tsv-del": 5_000}


@pytest.mark.asyncio
async def test_the_list_finds_only_translocations_above_the_longest_sv(stored) -> None:
    page = await _page(min_length=_TEN_MB)

    assert [str(variant.id) for variant in page.variants] == _TRANSLOCATIONS
    assert page.summary == {"BND": {"sniffles": 2}, "TRA": {"sniffles": 1}}


@pytest.mark.asyncio
async def test_the_export_holds_the_svs_the_list_holds(stored) -> None:
    export = await export_family_structural_variants(
        None,  # type: ignore[arg-type]
        context=_family(),
        min_length=50,
    )

    assert [str(row.id) for row in export.rows] == _AT_LEAST_50
    assert export.truncated_reason is None


@pytest.mark.asyncio
async def test_the_genome_track_draws_the_svs_the_list_holds(stored) -> None:
    page = await _page(min_length=50, track_mode=True, samples=["PROBAND"])

    assert [str(variant.id) for variant in page.variants] == _AT_LEAST_50
    assert page.total == len(_AT_LEAST_50)


def _trio() -> FamilyMetadataContext:
    names = {"PROBAND": "sample-proband", "MOTHER": "sample-mother", "FATHER": "sample-father"}
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_id": name} for name in names],
        sample_uuid_to_name={uuid: name for name, uuid in names.items()},
        sample_name_to_uuid=dict(names),
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"min_length": 50}, {"PROBAND": True, "MOTHER": True, "FATHER": False}),
        ({"min_length": _TEN_MB}, {"PROBAND": False, "MOTHER": True, "FATHER": False}),
        ({"length": 5_000}, {"PROBAND": True, "MOTHER": False, "FATHER": False}),
    ],
)
async def test_track_availability_measures_the_svs_as_the_list_does(
    monkeypatch: pytest.MonkeyPatch, filters: dict[str, Any], expected: dict[str, bool]
) -> None:
    """A member's SV track is offered when one of its SVs passes the filters: the proband's
    only SV is a 5 kb insertion written with END at POS, the mother's a breakend, the
    father's a 30 bp insertion."""
    _serve_stored(
        monkeypatch,
        [
            _sv("ins-5kb", "INS", start=100_000, length=0, sv_len=5_000, sample="PROBAND"),
            _sv("bnd", "BND", start=140_000, length=0, sv_len=None, sample="MOTHER"),
            _sv("ins-30", "INS", start=110_000, length=0, sv_len=30, sample="FATHER"),
        ],
    )
    service = "backend.app.services.family_service."

    async def trio(*_args: Any, **_kwargs: Any) -> FamilyMetadataContext:
        return _trio()

    async def no_sources(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def no_presence(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    async def presence_aggregate(*_args: Any, **_kwargs: Any) -> set[str]:
        raise AssertionError("a length filter is applied to the SV rows, not by the presence aggregate")

    monkeypatch.setattr(service + "build_family_metadata_context", trio)
    monkeypatch.setattr(service + "get_track_sources_by_sample", no_sources)
    monkeypatch.setattr(service + "get_track_presence_by_sample", no_presence)
    monkeypatch.setattr(service + "_repeat_expansion_presence_by_sample", no_presence)
    monkeypatch.setattr(service + "_structural_present_sample_names", presence_aggregate)

    availability = await get_family_track_availability_for_user(
        None,  # type: ignore[arg-type]
        family_id="FAM001",
        user=None,  # type: ignore[arg-type]
        chromosomes=["1"],
        include_small_variants=False,
        **filters,
    )

    assert {name: sample.variants for name, sample in availability.samples.items()} == expected


# --------------------------------------------------------------------------- #
# Each importer stores the SVLEN its caller wrote
# --------------------------------------------------------------------------- #

_VCF_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"


def _uploaded(record_format: str, line: str) -> StructuralVariantRecord:
    """The record the SV upload stores for one call of a Sniffles (also pbsv), Spectre or
    manual TSV file: the POS, END, type and SVLEN its parser reads
    (``upload_structural_variant_file``)."""
    text_value = f"{line}\n" if record_format == "manual" else f"{_VCF_HEADER}{line}\n"
    (parsed,) = iter_structural_variant_records(text_value, record_format)  # type: ignore[arg-type]
    return StructuralVariantRecord(
        variant_key=None,
        variant_id="uploaded",
        chr=parsed.chrom,
        start=parsed.start,
        end=parsed.end,
        sv_type=parsed.svtype,
        source=record_format,
        remote_chr=parsed.remote_chr,
        remote_start=parsed.remote_start,
        remote_end=parsed.remote_end,
        sv_len=parsed.svlen,
        filters=[],
        gene_symbols=[],
        annotations=[],
        calls=[StructuralVariantCall(sample="PROBAND", gt=parsed.gt, qual=None, read_support=None, filter=None)],
    )


def _needlr(line: str) -> StructuralVariantRecord:
    proband = SampleMetadataContext(
        sample_uuid="sample-proband",
        sample_id="PROBAND",
        family_uuid="family-uuid",
        family_id="FAM001",
        sex="und",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
    ped = ParsedPed(family_ids=["FAM001"], members=[], sample_ids=["PROBAND"], text="")
    (record,) = _iter_needlr_structural_records(
        f"{_VCF_HEADER}{line}\n", ped=ped, sample_contexts={"PROBAND": proband}
    )
    return record


def _hificnv(line: str) -> StructuralVariantRecord:
    (record,) = _iter_cnv_structural_records(f"{_VCF_HEADER}{line}\n", sample_id="PROBAND")
    return record


def _mito_sv(line: str) -> StructuralVariantRecord:
    (record,) = _iter_mito_sv_records(f"{_VCF_HEADER}{line}\n", sample_id="PROBAND")
    return record


@pytest.mark.parametrize(
    ("read", "line", "sv_len", "end_minus_pos", "measured"),
    [
        pytest.param(
            lambda line: _uploaded("sniffles", line),
            "1\t100000\tSniffles2.INS.1\tN\t<INS>\t60\tPASS\tPRECISE;SVTYPE=INS;SVLEN=5000;END=100000;SUPPORT=12\tGT\t0/1",
            5_000,
            0,
            5_000,
            id="sniffles2-or-pbsv-upload",
        ),
        pytest.param(
            lambda line: _uploaded("spectre", line),
            "1\t100000\tSpectre.DEL.1\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;END=105000;SVLEN=-5000\tGT\t0/1",
            -5_000,
            5_000,
            5_000,
            id="spectre-upload",
        ),
        pytest.param(
            # A TSV has no SVLEN column: END minus POS is the only length it gives.
            lambda line: _uploaded("manual", line),
            "tsv-ins\t1\t100000\t100000\tN\t<INS>\tINS\t0/1",
            None,
            0,
            0,
            id="manual-tsv-upload",
        ),
        pytest.param(
            _needlr,
            "1\t100000\tNeedlR.INS.1\tN\t<INS>\t60\tPASS\tSVTYPE=INS;SVLEN=5000;END=100000;Query_ID=PROBAND;Genotype=0/1;Alt_Reads=12",
            5_000,
            0,
            5_000,
            id="needlr-package",
        ),
        pytest.param(
            _hificnv,
            "chr1\t100000\t.\tN\t<DEL>\t57\tPASS\tSVTYPE=DEL;END=105000;SVLEN=5000\tGT:CN\t0/1:1",
            5_000,
            5_000,
            5_000,
            id="hificnv-package",
        ),
        pytest.param(
            _mito_sv,
            "chrM\t8470\tSniffles2.DEL.1M0\tN\t<DEL>\t60\tPASS\tPRECISE;SVTYPE=DEL;SVLEN=-4977;END=13447;SUPPORT=140;VAF=0.35\tGT:GQ:DR:DV\t0/1:60:260:140",
            -4_977,
            4_977,
            4_977,
            id="mito-sniffles2-package",
        ),
    ],
)
def test_each_importer_stores_the_svlen_its_caller_wrote(
    read: Any, line: str, sv_len: int | None, end_minus_pos: int, measured: int
) -> None:
    record = read(line)

    assert (record.sv_len, record.end - record.start) == (sv_len, end_minus_pos)
    assert _kept([record], length=measured) == [record.variant_id]
    assert _kept([record], min_length=measured + 1) == []


def test_an_uploaded_breakend_is_a_translocation_whatever_its_end() -> None:
    record = _uploaded(
        "sniffles",
        "1\t140000\tSniffles2.BND.2\tN\tN]5:3000000]\t50\tPASS\tPRECISE;SVTYPE=BND;SUPPORT=8\tGT\t0/1",
    )

    assert (record.sv_type, record.sv_len, record.end - record.start) == ("BND", None, 0)
    assert (record.remote_chr, record.remote_start) == ("5", 3_000_000)
    assert _kept([record], min_length=_TEN_MB) == [record.variant_id]
    assert _kept([record], length=0) == []
