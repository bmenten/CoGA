"""What a sign-out of one of a family's analyses freezes and checks (signout_scope.py).

A family holding the monogenic and the conventional NIPT of one pregnancy, or short-read and then
long-read WGS of the same DNA, signs out each analysis on its own, and each sign-out freezes and
checks that analysis's data only (the owner's decisions of 2026-11-19). The data of an analysis are
read from its package record's sources and datasets; data no other analysis claims belong to every
analysis.
"""

from __future__ import annotations

import asyncio
import types
from typing import Any

import pytest

from backend.app.services import signout_scope as scope_module
from backend.app.services.family_package_records import PackageRecordShapeError, package_sources
from backend.app.services.signout_scope import (
    CNIPT_SECTION_ANALYSES,
    NIPT_SECTION_ANALYSES,
    PGT_SECTION_ANALYSES,
    WHOLE_FAMILY,
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
        "analysis_types": ["conventional_nipt", "monogenic_nipt"],
        "package_imports": {
            "monogenic_nipt": _record(["snv", "coverage", "haplotypes"], "nipt"),
            "conventional_nipt": _record(["ccnv"]),
            **extra_records,
        },
    }


def test_a_family_of_one_analysis_or_none_is_signed_out_whole() -> None:
    for metadata, analysis in (
        (None, None),
        ({"analysis_types": ["long_read_wgs"]}, "long_read_wgs"),
        # Even when asked for an analysis: one alone is the whole family.
        ({"analysis_types": ["monogenic_nipt"], "package_imports": {"monogenic_nipt": {}}}, "monogenic_nipt"),
    ):
        scope = signout_scope(metadata, analysis)
        assert scope.whole and scope.analysis == analysis
        assert scope.holds("small_variants", ["anything"])
        assert scope.holds_kind("repeats") and scope.holds_dataset("snv") and scope.holds_record("x")
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
        # A dataset no record lists.
        assert scope.holds_dataset("mito")
    # A package that named no analysis claims nothing: its sources are every analysis's.
    metadata = _two_nipts(unspecified=_record(["sv_needlr"]))
    assert signout_scope(metadata, "conventional_nipt").holds("structural_variants", ["needlr"])
    assert signout_scope(metadata, "conventional_nipt").holds_dataset("sv_needlr")


def test_a_kind_read_whole_is_the_analysis_s_when_it_or_no_other_holds_it() -> None:
    metadata = {
        "analysis_types": ["long_read_wgs", "short_read_wgs"],
        "package_imports": {
            "long_read_wgs": _record(["snv", "repeats_trgt", "paraphase"], "clair3"),
            "short_read_wgs": _record(["snv"], "gatk"),
        },
    }
    short_read = signout_scope(metadata, "short_read_wgs")
    long_read = signout_scope(metadata, "long_read_wgs")
    assert not short_read.holds_kind("repeats") and not short_read.holds_kind("paraphase")
    assert long_read.holds_kind("repeats") and long_read.holds_kind("paraphase")
    # A kind no analysis holds is every analysis's.
    assert short_read.holds_kind("tracks") and long_read.holds_kind("tracks")
    assert short_read.holds("small_variants", ["gatk"]) and not short_read.holds("small_variants", ["clair3"])


def test_an_analysis_with_no_package_record_holds_what_no_other_claims() -> None:
    metadata = {
        "analysis_types": ["conventional_nipt", "monogenic_nipt"],
        "package_imports": {"monogenic_nipt": _record(["snv"], "nipt")},
    }
    scope = signout_scope(metadata, "conventional_nipt")
    assert not scope.holds("small_variants", ["nipt"])
    assert scope.holds("structural_variants", ["ccnv"])
    assert not scope.holds_dataset("snv") and scope.holds_dataset("ccnv")


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
        {"analysis_types": ["long_read_wgs", "short_read_wgs"], "package_imports": {}}, "short_read_wgs"
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
        {"datasets": ["ccnv"], "sources": package_sources(["ccnv"], None)},
        {"sources": package_sources(["ccnv"], None)},
    ],
)
def test_a_record_not_in_the_shape_the_import_writes_stops_the_sign_out(record: dict[str, Any]) -> None:
    for own in (True, False):
        metadata = _two_nipts(**{"conventional_nipt" if own else "monogenic_nipt": record})
        with pytest.raises(PackageRecordShapeError):
            signout_scope(metadata, "conventional_nipt")


# --- The sources the family's stored rows of its variants are stored under --------------------


_CONTEXT = types.SimpleNamespace(family_uuid="u1", assembly_name="GRCh38")


def test_the_stored_sources_are_read_from_the_current_rows_of_the_family(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _execute(query: str, params: dict[str, Any]):
        calls.append((" ".join(query.split()), params))
        if "SV/entries" in query:
            return [("1-100-900-DEL", ["ccnv", "web"])]
        return [(variant_id, ["nipt"]) for variant_id in params["variant_ids"][:1]]

    monkeypatch.setattr(scope_module, "_CHUNK", 2)
    monkeypatch.setattr(scope_module, "execute_clickhouse", _execute)
    small, structural = asyncio.run(
        scope_module.stored_variant_sources(
            _CONTEXT, small_ids=["1-30-G-A", "1-10-A-G", "1-20-C-T", "1-10-A-G", ""], structural_ids=["1-100-900-DEL"]
        )
    )
    # Read in chunks, each variant once, its current rows only.
    assert [params["variant_ids"] for _query, params in calls[:2]] == [("1-10-A-G", "1-20-C-T"), ("1-30-G-A",)]
    assert all(params["family_guid"] == "u1" for _query, params in calls)
    assert all("sign = 1" in query for query, _params in calls)
    assert "`GRCh38/SNV_INDEL/entries`" in calls[0][0] and "`GRCh38/SV/entries`" in calls[2][0]
    assert small == {"1-10-A-G": frozenset({"nipt"}), "1-30-G-A": frozenset({"nipt"})}
    assert structural == {"1-100-900-DEL": frozenset({"ccnv", "web"})}


def test_a_family_with_no_assembly_has_no_stored_sources(monkeypatch) -> None:
    async def _must_not_read(query: str, params: dict[str, Any]):
        raise AssertionError("no ClickHouse read for a family without an assembly")

    monkeypatch.setattr(scope_module, "execute_clickhouse", _must_not_read)
    context = types.SimpleNamespace(family_uuid="u1", assembly_name=None)
    assert asyncio.run(scope_module.stored_variant_sources(context, small_ids=["1-1-A-G"], structural_ids=[])) == (
        {},
        {},
    )
