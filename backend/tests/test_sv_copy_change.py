"""One rule for whether an SV removes a copy of what it spans, adds one, or neither (CLIN-47).

The SV ranking matched exact type names (``DEL``, ``LOSS``, ``CNV_LOSS``), so a ``DEL:ME`` or
a CNV whose copy number shows a loss did not score as a deletion, nor take the
haploinsufficiency floor in a LoF-intolerant gene, and a ``DUP:TANDEM`` or a CNV gain did not
score as a duplication, while the SNV + SV second-hit badge counted a ``DEL:ME`` and a CNV loss
as copy losses (#840). Both now read ``sv_copy_change.copy_change``: these tests pin the rule,
and run the SV ranking and the badge on the same SVs. The SV list serves each SV's change by
the same rule, which the genome, chromosome and Circos views draw an SV by (CLIN-54).
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest

from backend.app.services.clickhouse_family_variants import (
    _prioritized_structural_variants_page,
    get_family_structural_variants_page,
)
from backend.app.services.clickhouse_variant_queries import _family_pedigree, _structural_variant_out
from backend.app.services.clickhouse_variant_records import (
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.compound_het_phase import FamilyPedigree
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters
from backend.app.services.sv_gene_index_service import summarize_second_hit

# On GRCh38: chrX outside the PARs, where a male carries one copy; inside PAR1, where he has
# two; chrY outside the PARs; an autosome.
X_OUTSIDE_PARS = ("X", 50_000_000, 50_100_000)
X_IN_PAR1 = ("X", 1_000_000, 1_100_000)
Y_OUTSIDE_PARS = ("Y", 10_000_000, 10_100_000)
AUTOSOME = ("1", 1_000_000, 1_100_000)
A_SON = FamilyPedigree(males=frozenset({"son"}), assembly_name="GRCh38")


@pytest.mark.parametrize(
    ("sv_type", "kind"),
    [("DEL:ME", "DEL"), ("dup:tandem", "DUP"), (" INS:ME:ALU ", "INS"), ("CNV", "CNV"), (None, "")],
)
def test_an_sv_is_read_by_its_type_without_subtypes(sv_type: str | None, kind: str) -> None:
    # Imported here, so the ranking test below still runs on code without the module.
    from backend.app.services.sv_copy_change import sv_kind

    assert sv_kind(sv_type) == kind


@pytest.mark.parametrize(
    ("sv_type", "calls", "change"),
    [
        ("DEL", [], "loss"),
        ("del", [], "loss"),
        ("DEL:ME", [], "loss"),
        ("DEL", [("child", "0/1", 3)], "loss"),  # the type names the direction
        ("LOSS", [], "loss"),
        ("CNV_LOSS", [], "loss"),
        ("DUP", [], "gain"),
        ("DUP:TANDEM", [], "gain"),
        ("DUP", [("child", "0/1", 1)], "gain"),
        ("GAIN", [], "gain"),
        ("CNV_GAIN", [], "gain"),
        ("CNV", [], "loss"),  # no copy number stored: may be a loss, and counts as one
        ("CNV", [("child", "0/1", None)], "loss"),
        ("CNV", [("child", "0/1", 1)], "loss"),
        ("CNV", [("child", "1/1", 0)], "loss"),
        ("CNV", [("child", "0/1", 2)], None),  # two copies kept: neither
        ("CNV", [("child", "0/1", 3)], "gain"),
        ("cnv:tr", [("child", "./1", 4)], "gain"),
        # A loss in any carrier makes it a loss.
        ("CNV", [("child", "0/1", 3), ("mother", "0/1", 1)], "loss"),
        # Only a carrier's copy number says what the CNV did: a reference call's says nothing.
        ("CNV", [("child", "0/1", 3), ("father", "0/0", 1)], "gain"),
        ("CNV", [("child", "0/1", None), ("father", "0/0", 2)], "loss"),
        ("INS", [], None),
        ("INS:ME", [], None),
        ("INV", [], None),
        ("BND", [], None),
        ("TRA", [], None),
        ("SV", [], None),
        (None, [], None),
    ],
)
def test_which_svs_remove_or_add_a_copy(sv_type: str | None, calls: list, change: str | None) -> None:
    from backend.app.services.sv_copy_change import copy_change

    chrom, start, end = AUTOSOME
    assert copy_change(sv_type, calls, chrom=chrom, start=start, end=end) == change


@pytest.mark.parametrize(
    ("locus", "copies", "change"),
    [
        (X_OUTSIDE_PARS, 1, None),  # his one copy kept
        (X_OUTSIDE_PARS, 0, "loss"),
        (X_OUTSIDE_PARS, 2, "gain"),
        (Y_OUTSIDE_PARS, 1, None),
        (X_IN_PAR1, 1, "loss"),  # two copies in a PAR
        (AUTOSOME, 1, "loss"),
    ],
)
def test_a_male_keeps_one_copy_of_chrx_and_chry_outside_the_pars(
    locus: tuple[str, int, int], copies: int, change: str | None
) -> None:
    from backend.app.services.sv_copy_change import copy_change

    chrom, start, end = locus
    calls = [("son", "1", copies)]
    assert copy_change("CNV", calls, chrom=chrom, start=start, end=end, pedigree=A_SON) == change


def test_a_female_and_a_sample_of_unknown_sex_have_two_copies_of_chrx() -> None:
    from backend.app.services.sv_copy_change import copy_change

    chrom, start, end = X_OUTSIDE_PARS
    where: dict[str, Any] = {"chrom": chrom, "start": start, "end": end}
    assert copy_change("CNV", [("daughter", "0/1", 1)], pedigree=A_SON, **where) == "loss"
    # Without a pedigree no one is known to be male.
    assert copy_change("CNV", [("son", "1", 1)], **where) == "loss"


# --- the two callers, on the same SVs ----------------------------------------------------------

# Each SV: its id, type, calls (sample, genotype, copy number) and locus, the change the rule
# gives it, and the pathogenicity the SV ranking gives it in one gene with a pLI of 0.99 (a
# loss 0.7, at least 0.9 in a gene that LoF-intolerant; a gain 0.5; an SV that changes no
# copy number by its type, 0.4 when the type is not one the ranking knows).
CASES: list[tuple[str, str, list[tuple[str, str, int | None]], tuple[str, int, int], str | None, float]] = [
    ("del", "DEL", [("SON", "0/1", None)], AUTOSOME, "loss", 0.9),
    ("del-me", "DEL:ME", [("SON", "0/1", None)], AUTOSOME, "loss", 0.9),
    ("cnv-one-copy", "CNV", [("SON", "0/1", 1)], AUTOSOME, "loss", 0.9),
    ("cnv-three-copies", "CNV", [("SON", "0/1", 3)], AUTOSOME, "gain", 0.5),
    ("cnv-son-x-one-copy", "CNV", [("SON", "1", 1)], X_OUTSIDE_PARS, None, 0.4),
    ("cnv-mother-x-one-copy", "CNV", [("MOTHER", "0/1", 1)], X_OUTSIDE_PARS, "loss", 0.9),
    ("dup", "DUP", [("SON", "0/1", None)], AUTOSOME, "gain", 0.5),
    ("dup-tandem", "DUP:TANDEM", [("SON", "0/1", None)], AUTOSOME, "gain", 0.5),
]


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM-1",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_id": "SON", "role": "proband", "clinical_status": "affected", "sex": "male"},
            {"sample_id": "MOTHER", "role": "mother", "clinical_status": "unaffected", "sex": "female"},
        ],
        sample_uuid_to_name={"sample-son": "SON", "sample-mother": "MOTHER"},
        sample_name_to_uuid={"SON": "sample-son", "MOTHER": "sample-mother"},
        affected_sample_names=["SON"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _record(
    variant_id: str,
    sv_type: str,
    calls: list[tuple[str, str, int | None]],
    locus: tuple[str, int, int],
) -> StructuralVariantRecord:
    chrom, start, end = locus
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=start,
        end=end,
        sv_type=sv_type,
        source="test",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=end - start,
        filters=["PASS"],
        gene_symbols=["GENE1"],
        annotations=[{"pLI": "0.99"}],
        calls=[
            StructuralVariantCall(
                sample=sample,
                gt=genotype,
                qual=None,
                read_support=None,
                filter="PASS",
                copy_number=copies,
            )
            for sample, genotype, copies in calls
        ],
    )


@pytest.mark.asyncio
async def test_the_sv_ranking_scores_a_copy_loss_as_a_deletion_and_a_gain_as_a_duplication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [_record(variant_id, sv_type, calls, locus) for variant_id, sv_type, calls, locus, _c, _p in CASES]
    module = "backend.app.services.clickhouse_family_variants."

    async def _fetch(*_args: Any, **_kwargs: Any) -> list[StructuralVariantRecord]:
        return list(records)

    async def _nothing(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def _no_ids(*_args: Any, **_kwargs: Any) -> set:
        return set()

    async def _no_hpo(*_args: Any, **_kwargs: Any) -> tuple[set, dict]:
        return set(), {}

    monkeypatch.setattr(module + "_fetch_structural_variant_rows", _fetch)
    monkeypatch.setattr(module + "_structural_record_matches", lambda *a, **k: True)
    monkeypatch.setattr(module + "_affected_present_hpo", _no_hpo)
    monkeypatch.setattr(module + "list_matching_structural_variant_review_ids", _no_ids)
    monkeypatch.setattr(module + "get_structural_variant_review_map", _nothing)
    monkeypatch.setattr(module + "_fetch_structural_cytoband_map", _nothing)

    page = await _prioritized_structural_variants_page(
        None,  # type: ignore[arg-type]
        context=_context(),
        filters=StructuralVariantQueryFilters(page=1, page_size=50),
        page=1,
        page_size=50,
        selected_samples=["SON", "MOTHER"],
    )

    scores = {str(variant.id): variant.priority.pathogenicity_score for variant in page.variants if variant.priority}
    assert scores == {variant_id: pathogenicity for variant_id, *_rest, pathogenicity in CASES}


@pytest.mark.parametrize(
    ("variant_id", "sv_type", "calls", "locus", "change"),
    [case[:5] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_the_second_hit_badge_counts_the_same_svs_as_copy_losses(
    variant_id: str,
    sv_type: str,
    calls: list[tuple[str, str, int | None]],
    locus: tuple[str, int, int],
    change: str | None,
) -> None:
    chrom, start, end = locus
    sv = {
        "sv_id": variant_id,
        "sv_type": sv_type,
        "chr": chrom,
        "start": start,
        "end": end,
        "gt": {sample: genotype for sample, genotype, _copies in calls},
        "ps": {},
        "cn": {sample: copies for sample, _genotype, copies in calls if copies is not None},
    }
    summary = summarize_second_hit([sv], ["SON"], pedigree=_family_pedigree(_context()))
    assert summary["svs"][0]["copy_loss"] is (change == "loss")
    assert summary["has_deletion"] is (change == "loss")


# --- what the SV views draw by (CLIN-54) -------------------------------------------------------


@pytest.mark.parametrize(
    ("variant_id", "sv_type", "calls", "locus", "change"),
    [case[:5] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_the_sv_list_serves_each_svs_copy_change_by_the_same_rule(
    variant_id: str,
    sv_type: str,
    calls: list[tuple[str, str, int | None]],
    locus: tuple[str, int, int],
    change: str | None,
) -> None:
    # The genome, chromosome and Circos views drew only five exact type names; they now draw a
    # DEL:ME as a deletion and a CNV by this change, which the list serves with each SV.
    served = _structural_variant_out(
        _record(variant_id, sv_type, calls, locus), [], pedigree=_family_pedigree(_context())
    )
    assert served.copy_change == change


@pytest.mark.parametrize("sv_type", ["INS", "INS:ME", "INV", "BND", "TRA", "CPX"])
def test_an_sv_that_changes_no_copy_number_is_served_with_none(sv_type: str) -> None:
    served = _structural_variant_out(
        _record("sv", sv_type, [("SON", "0/1", None)], AUTOSOME), [], pedigree=_family_pedigree(_context())
    )
    assert served.copy_change is None


@pytest.mark.asyncio
async def test_a_track_reads_the_copy_change_of_its_own_samples_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One CNV with one copy in the mother and three in her son, and the son's CNV keeping his
    # one copy of chrX: the family's list reads the first as a loss, as the ranking does, and
    # each member's track reads the member's own call.
    records = [
        _record("cnv-mixed", "CNV", [("SON", "0/1", 3), ("MOTHER", "0/1", 1)], AUTOSOME),
        _record("cnv-son-x", "CNV", [("SON", "1", 1)], X_OUTSIDE_PARS),
    ]
    module = "backend.app.services.clickhouse_family_variants."

    async def _fetch(
        *_args: Any, call_sample_names: Sequence[str] = (), **_kwargs: Any
    ) -> list[StructuralVariantRecord]:
        # A track's read keeps the SVs its sample has a call of, as the SQL does.
        return [
            record
            for record in records
            if not call_sample_names or any(call.sample in call_sample_names for call in record.calls)
        ]

    async def _nothing(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def _no_ids(*_args: Any, **_kwargs: Any) -> set:
        return set()

    monkeypatch.setattr(module + "_fetch_structural_variant_rows", _fetch)
    monkeypatch.setattr(module + "_structural_record_matches", lambda *a, **k: True)
    monkeypatch.setattr(module + "list_matching_structural_variant_review_ids", _no_ids)
    monkeypatch.setattr(module + "get_structural_variant_review_map", _nothing)
    monkeypatch.setattr(module + "_fetch_structural_cytoband_map", _nothing)

    async def served(samples: list[str], *, track_mode: bool) -> dict[str, str | None]:
        page = await get_family_structural_variants_page(
            None,  # type: ignore[arg-type]
            context=_context(),
            page=1,
            page_size=0,
            samples=samples,
            track_mode=track_mode,
        )
        return {str(variant.id): variant.copy_change for variant in page.variants}

    assert await served([], track_mode=False) == {"cnv-mixed": "loss", "cnv-son-x": None}
    assert await served(["SON"], track_mode=True) == {"cnv-mixed": "gain", "cnv-son-x": None}
    assert await served(["MOTHER"], track_mode=True) == {"cnv-mixed": "loss"}
