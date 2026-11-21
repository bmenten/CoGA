"""What a sign-out of one of a family's analyses freezes and checks (signout_scope.py).

A family holding the monogenic and the conventional NIPT of one pregnancy, or short-read and then
long-read WGS of the same DNA, signs out each analysis on its own, and each sign-out freezes and
checks that analysis's data only (the owner's decisions of 2026-11-19). The data of an analysis are
read from its package record's sources; data no other analysis claims belong to every analysis. An
import mark belongs to the analysis it records, or to every analysis when it records none.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.services.family_package_records import PackageRecordShapeError, package_sources
from backend.app.services.signout_scope import (
    CNIPT_SECTION_ANALYSES,
    NIPT_SECTION_ANALYSES,
    PGT_SECTION_ANALYSES,
    WHOLE_FAMILY,
    analysis_listing_source,
    signout_scope,
)


def _record(datasets: list[str], snv_callset: str | None = None) -> dict[str, Any]:
    return {
        "folder_path": "/data/FAM1",
        "datasets": {name: {"enabled": True} for name in datasets},
        "sources": package_sources(datasets, snv_callset),
    }


def _two_nipts(**extra_records: dict[str, Any]) -> dict[str, Any]:
    return {
        "analyses": ["conventional_nipt", "monogenic_nipt"],
        "package_imports": {
            "monogenic_nipt": _record(["snv", "coverage", "haplotypes"], "nipt"),
            "conventional_nipt": _record(["ccnv"]),
            **extra_records,
        },
    }


def test_a_family_of_one_analysis_or_none_is_signed_out_whole() -> None:
    for metadata, analysis in (
        (None, None),
        ({"analyses": ["long_read_wgs"]}, "long_read_wgs"),
        # Even when asked for an analysis: one alone is the whole family.
        ({"analyses": ["monogenic_nipt"], "package_imports": {"monogenic_nipt": {}}}, "monogenic_nipt"),
    ):
        scope = signout_scope(metadata, analysis)
        assert scope.whole and scope.analysis == analysis
        assert scope.holds("small_variants", ["anything"])
        assert scope.holds("repeats", ["trgt"]) and scope.holds_record("x")
        assert scope.holds_mark("conventional_nipt") and scope.holds_mark(None)
        assert scope.holds_application(CNIPT_SECTION_ANALYSES)
    assert WHOLE_FAMILY.whole and WHOLE_FAMILY.analysis is None


def test_each_nipt_holds_its_own_sources_and_not_the_others() -> None:
    cnipt = signout_scope(_two_nipts(), "conventional_nipt")
    mnipt = signout_scope(_two_nipts(), "monogenic_nipt")
    assert not cnipt.whole and cnipt.analysis == "conventional_nipt"
    # The conventional NIPT's segments and tracks; not the monogenic NIPT's callset or coverage BED.
    assert cnipt.holds("structural_variants", ["ccnv"]) and cnipt.holds("tracks", ["ccnv"])
    assert not cnipt.holds("small_variants", ["nipt"])
    assert not cnipt.holds("tracks", ["web"]) and not cnipt.holds("tracks", ["glimpse2"])
    assert mnipt.holds("small_variants", ["nipt"]) and mnipt.holds("tracks", ["web"])
    assert not mnipt.holds("structural_variants", ["ccnv"]) and not mnipt.holds("tracks", ["ccnv"])
    # A variant stored under both, one of them its own, is the analysis's.
    assert cnipt.holds("tracks", ["web", "ccnv"])


def test_data_no_other_analysis_claims_belong_to_every_analysis() -> None:
    for analysis in ("conventional_nipt", "monogenic_nipt"):
        scope = signout_scope(_two_nipts(), analysis)
        # An upload, an analyst's addition: a source no record lists.
        assert scope.holds("structural_variants", ["web"])
        assert scope.holds("small_variants", ["upload"])
        # A variant whose stored rows are gone, whose sources cannot be told.
        assert scope.holds("small_variants", []) and scope.holds("small_variants", [None, ""])
    # A package that named no analysis claims nothing: its sources are every analysis's.
    metadata = _two_nipts(unspecified=_record(["sv_needlr"]))
    assert signout_scope(metadata, "conventional_nipt").holds("structural_variants", ["needlr"])


def _short_and_long_read() -> dict[str, Any]:
    return {
        "analyses": ["long_read_wgs", "short_read_wgs"],
        "package_imports": {
            "long_read_wgs": _record(["snv", "repeats_trgt", "paraphase"], "clair3"),
            "short_read_wgs": _record(["snv"], "gatk"),
        },
    }


def test_the_repeat_and_paraphase_calls_are_the_analysis_s_by_their_sources() -> None:
    short_read = signout_scope(_short_and_long_read(), "short_read_wgs")
    long_read = signout_scope(_short_and_long_read(), "long_read_wgs")
    assert not short_read.holds("repeats", ["trgt"]) and not short_read.holds("paraphase", ["paraphase"])
    assert long_read.holds("repeats", ["trgt"]) and long_read.holds("paraphase", ["paraphase"])
    # A family holding no such call: nothing can be told, so the kind is every analysis's.
    assert short_read.holds("repeats", []) and long_read.holds("repeats", [])
    assert short_read.holds("small_variants", ["gatk"]) and not short_read.holds("small_variants", ["clair3"])


def test_the_analysis_listing_a_source_is_the_one_whose_record_lists_it() -> None:
    metadata = _short_and_long_read()
    assert analysis_listing_source(metadata, "small_variants", "gatk") == "short_read_wgs"
    assert analysis_listing_source(metadata, "small_variants", "clair3") == "long_read_wgs"
    assert analysis_listing_source(metadata, "repeats", "trgt") == "long_read_wgs"
    # A source no record lists (an upload's), or none: no analysis.
    assert analysis_listing_source(metadata, "structural_variants", "web") is None
    assert analysis_listing_source(metadata, "small_variants", None) is None
    assert analysis_listing_source(metadata, "small_variants", "") is None
    # A source two analyses list cannot be told apart.
    both = _short_and_long_read()
    both["package_imports"]["short_read_wgs"] = _record(["snv"], "clair3")
    assert analysis_listing_source(both, "small_variants", "clair3") is None
    # A record of no analysis the family holds (a package that named none) is no analysis's own.
    unnamed = {**metadata, "package_imports": {**metadata["package_imports"], "unspecified": _record(["sv_needlr"])}}
    assert analysis_listing_source(unnamed, "structural_variants", "needlr") is None
    # A family of one analysis: its record's sources are its own.
    single = {"analyses": ["short_read_wgs"], "package_imports": {"short_read_wgs": _record(["snv"], "gatk")}}
    assert analysis_listing_source(single, "small_variants", "gatk") == "short_read_wgs"


def test_an_analysis_with_no_package_record_holds_what_no_other_claims() -> None:
    metadata = {
        "analyses": ["conventional_nipt", "monogenic_nipt"],
        "package_imports": {"monogenic_nipt": _record(["snv"], "nipt")},
    }
    scope = signout_scope(metadata, "conventional_nipt")
    assert not scope.holds("small_variants", ["nipt"])
    assert scope.holds("structural_variants", ["ccnv"])


def test_an_import_mark_is_the_analysis_s_it_records_or_every_analysis_s() -> None:
    metadata = {
        "analyses": ["long_read_wgs", "short_read_wgs"],
        "package_imports": {
            "long_read_wgs": _record(["snv", "qc"], "deepvariant"),
            "short_read_wgs": _record(["snv", "qc"], "haplotypecaller"),
        },
    }
    short_read = signout_scope(metadata, "short_read_wgs")
    long_read = signout_scope(metadata, "long_read_wgs")
    # Both analyses list snv and qc: a mark is told apart by the analysis it records, never by
    # the dataset it names.
    assert short_read.holds_mark("short_read_wgs") and not short_read.holds_mark("long_read_wgs")
    assert long_read.holds_mark("long_read_wgs") and not long_read.holds_mark("short_read_wgs")
    # An upload's or a deletion's mark records none; a package that named no analysis records
    # `unspecified`, which the family never holds: each is every analysis's.
    for scope in (short_read, long_read):
        assert scope.holds_mark(None) and scope.holds_mark("unspecified")
    # A mark of an analysis the family no longer holds (its import was put back) holds every
    # analysis's sign-out: the safe side.
    assert short_read.holds_mark("pgt") and long_read.holds_mark("pgt")


def test_package_records_and_application_sections_by_analysis() -> None:
    scope = signout_scope(_two_nipts(), "conventional_nipt")
    assert scope.holds_record("conventional_nipt") and not scope.holds_record("monogenic_nipt")
    # A record of no analysis the family holds (a package that named none).
    assert scope.holds_record("unspecified")
    assert scope.holds_application(CNIPT_SECTION_ANALYSES)
    assert not scope.holds_application(NIPT_SECTION_ANALYSES)
    # A monogenic NIPT fetus's embryo calls are the monogenic NIPT's.
    assert not scope.holds_application(PGT_SECTION_ANALYSES)
    assert signout_scope(_two_nipts(), "monogenic_nipt").holds_application(PGT_SECTION_ANALYSES)
    # An application none of whose analyses the family holds is every analysis's.
    germline = signout_scope(
        {"analyses": ["long_read_wgs", "short_read_wgs"], "package_imports": {}}, "short_read_wgs"
    )
    assert germline.holds_application(PGT_SECTION_ANALYSES)


@pytest.mark.parametrize(
    "record",
    [
        {"datasets": {}},
        {"datasets": {}, "sources": ["ccnv"]},
        {"datasets": {}, "sources": {"tracks": ["ccnv"]}},
        {"datasets": {}, "sources": {**package_sources([], None), "tracks": "ccnv"}},
        {"datasets": {}, "sources": {**package_sources([], None), "tracks": [1]}},
    ],
)
def test_a_record_not_in_the_shape_the_import_writes_stops_the_sign_out(record: dict[str, Any]) -> None:
    for own in (True, False):
        metadata = _two_nipts(**{"conventional_nipt" if own else "monogenic_nipt": record})
        with pytest.raises(PackageRecordShapeError):
            signout_scope(metadata, "conventional_nipt")
