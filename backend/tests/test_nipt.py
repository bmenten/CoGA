from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.schemas import FamilyMemberOut, FamilyOut
from backend.app.services.analysis_scope import AnalysisNotNamed
from backend.app.services.metadata_service import _family_member_out_from_row
from backend.app.services.nipt import (
    CNIPT_CFDNA_ASSAY,
    CONVENTIONAL_NIPT_ANALYSIS_TYPE,
    DEFAULT_NIPT_ASSAY_KEY,
    MONOGENIC_NIPT_ANALYSIS_TYPE,
    NIPT_CFDNA_ASSAY,
    NiptTrio,
    is_maternal_plasma,
    is_monogenic_nipt_family,
    multiple_pregnancy_note,
    nipt_analysis_in_scope,
    nipt_analysis_of,
    nipt_assay_key,
    resolve_nipt_trio,
)

# The monogenic NIPT of the pregnancy ZW1, by its key (family_analysis_types.analysis_key).
MNIPT = f"{MONOGENIC_NIPT_ANALYSIS_TYPE}:ZW1"


def _member(
    sample_id: str,
    role: str,
    *,
    active: bool = True,
    assay: str | None = None,
    assay_panel: str | None = None,
) -> FamilyMemberOut:
    metadata: dict[str, object] = {}
    if assay:
        metadata["assays"] = [assay]
    if assay_panel:
        # The capture panel the monogenic NIPT's package named, kept as that analysis's.
        metadata["assay_panels"] = {MNIPT: assay_panel}
    return FamilyMemberOut(
        sample_id=sample_id,
        role=role,  # type: ignore[arg-type]
        affected=False,
        sample_metadata=metadata,
        active=active,
    )


def _family(
    members: list[FamilyMemberOut],
    *,
    analysis: str | None = MNIPT,
) -> FamilyOut:
    metadata = {"analyses": [analysis]} if analysis else {}
    return FamilyOut(
        id="family-uuid",
        family_id="FAM001",
        created_at=datetime.now(timezone.utc),
        members=members,
        metadata=metadata,
    )


def test_is_monogenic_nipt_family_reads_the_analysis_types() -> None:
    assert is_monogenic_nipt_family(_family([], analysis=MNIPT))
    assert not is_monogenic_nipt_family(_family([], analysis=None))
    assert not is_monogenic_nipt_family(_family([], analysis="other"))


def test_a_family_holding_both_nipts_is_a_monogenic_nipt_family() -> None:
    family = _family([])
    both = family.model_copy(update={"metadata": {"analyses": ["conventional_nipt:ZW1", MNIPT]}})
    assert is_monogenic_nipt_family(both)
    only_cnipt = family.model_copy(update={"metadata": {"analyses": ["conventional_nipt:ZW1"]}})
    assert not is_monogenic_nipt_family(only_cnipt)


def test_a_plasma_of_both_nipts_is_the_cfdna_sample() -> None:
    plasma = _member("PLASMA", "mother").model_copy(
        update={"sample_metadata": {"assays": ["cnipt_cfdna", "nipt_cfdna"]}}
    )
    trio = resolve_nipt_trio(_family([_member("FATHER1", "father"), plasma]), MNIPT)
    assert trio is not None and trio.cfdna_sample_id == "PLASMA"


def test_resolve_nipt_trio_identifies_cfdna_by_assay_tag() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
            _member("fetus-1", "fetus"),
        ]
    )

    assert resolve_nipt_trio(family, MNIPT) == NiptTrio(
        father_sample_id="father-1",
        cfdna_sample_id="cfdna-1",
        fetus_sample_ids=("fetus-1",),
        analysis=MNIPT,
    )


def test_the_fetus_is_the_fetus_member_never_a_proband_or_an_embryo() -> None:
    # A pregnancy's fetus has a role of its own (the owner's decision of 2026-11-21): a proband
    # (an affected sibling) or a PGT embryo is no fetus.
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
            _member("proband-1", "proband"),
            _member("embryo-1", "embryo"),
            _member("fetus-1", "fetus"),
        ]
    )

    trio = resolve_nipt_trio(family, MNIPT)
    assert trio is not None
    assert trio.fetus_sample_ids == ("fetus-1",)


def test_resolve_nipt_trio_falls_back_to_mother_role_without_assay_tag() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("mother-1", "mother"),
        ]
    )

    trio = resolve_nipt_trio(family, MNIPT)
    assert trio is not None
    assert trio.cfdna_sample_id == "mother-1"
    assert trio.fetus_sample_ids == ()


def _listing(family: FamilyOut, *members: str, analysis: str = MNIPT) -> FamilyOut:
    """``family`` whose package of the monogenic NIPT ``analysis`` lists ``members``."""
    records = dict(family.metadata.get("package_imports") or {})
    records[analysis] = {"members": list(members)}
    analyses = sorted({*family.metadata.get("analyses", []), analysis})
    metadata = {**family.metadata, "analyses": analyses, "package_imports": records}
    return family.model_copy(update={"metadata": metadata})


def test_of_a_mother_sampled_twice_the_plasma_is_the_one_the_package_lists() -> None:
    family = _family([_member("father-1", "father"), _member("mother-1", "mother"), _member("mother-2", "mother")])
    listed = resolve_nipt_trio(_listing(family, "father-1", "mother-1"), MNIPT)
    assert listed is not None and listed.cfdna_sample_id == "mother-1"
    assert resolve_nipt_trio(_listing(family, "father-1", "mother-2"), MNIPT).cfdna_sample_id == "mother-2"  # type: ignore[union-attr]
    # Neither listed: the plasma is not guessed, and no trio is resolved.
    assert resolve_nipt_trio(family, MNIPT) is None


def test_a_conventional_nipts_plasma_alone_is_not_the_monogenic_nipts() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("mother-1", "mother"),
            _member("cnipt-plasma", "mother", assay=CNIPT_CFDNA_ASSAY),
        ]
    )
    trio = resolve_nipt_trio(family, MNIPT)
    assert trio is not None and trio.cfdna_sample_id == "mother-1"


def test_of_two_tagged_plasmas_the_one_the_package_lists_is_the_cfdna_sample() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
            _member("cfdna-2", "mother", assay=NIPT_CFDNA_ASSAY),
        ]
    )
    assert resolve_nipt_trio(_listing(family, "father-1", "cfdna-2"), MNIPT).cfdna_sample_id == "cfdna-2"  # type: ignore[union-attr]
    assert resolve_nipt_trio(family, MNIPT) is None


def test_of_a_father_sampled_twice_the_father_is_the_one_the_package_lists() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("father-2", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
        ]
    )
    assert resolve_nipt_trio(_listing(family, "father-2", "cfdna-1"), MNIPT).father_sample_id == "father-2"  # type: ignore[union-attr]
    assert resolve_nipt_trio(family, MNIPT) is None


def test_each_pregnancys_trio_is_the_one_its_package_lists() -> None:
    # Two pregnancies of one woman (the owner's decision of 2026-11-21): a plasma and a fetus of
    # each, a new partner for the second. Each pregnancy's monogenic NIPT reads its own trio, never
    # the last embryo the family holds.
    later = f"{MONOGENIC_NIPT_ANALYSIS_TYPE}:ZW2"
    family = _family(
        [
            _member("FATHER1", "father"),
            _member("FATHER2", "father"),
            _member("CFDNA1", "mother", assay=NIPT_CFDNA_ASSAY),
            _member("CFDNA2", "mother", assay=NIPT_CFDNA_ASSAY),
            _member("FETUS1", "fetus"),
            _member("FETUS2", "fetus"),
        ]
    )
    family = _listing(_listing(family, "FATHER1", "CFDNA1", "FETUS1"), "FATHER2", "CFDNA2", "FETUS2", analysis=later)
    assert resolve_nipt_trio(family, MNIPT) == NiptTrio("FATHER1", "CFDNA1", ("FETUS1",), MNIPT)
    assert resolve_nipt_trio(family, later) == NiptTrio("FATHER2", "CFDNA2", ("FETUS2",), later)


def test_a_twin_pregnancys_trio_holds_both_fetuses_with_the_note() -> None:
    family = _listing(
        _family(
            [
                _member("FATHER1", "father"),
                _member("CFDNA1", "mother", assay=NIPT_CFDNA_ASSAY),
                _member("TWIN2", "fetus"),
                _member("TWIN1", "fetus"),
            ]
        ),
        "FATHER1",
        "CFDNA1",
        "TWIN1",
        "TWIN2",
    )
    trio = resolve_nipt_trio(family, MNIPT)
    assert trio is not None and trio.fetus_sample_ids == ("TWIN1", "TWIN2")
    # Whatever the zygosity, which CoGA is not told: a fetal result may be in one or both.
    note = multiple_pregnancy_note(len(trio.fetus_sample_ids))
    assert note is not None and "2 fetuses" in note and "one fetus or in more than one" in note
    assert "monozygotic" in note
    assert multiple_pregnancy_note(1) is None and multiple_pregnancy_note(0) is None


def test_a_reading_of_several_pregnancies_names_one() -> None:
    later = f"{MONOGENIC_NIPT_ANALYSIS_TYPE}:ZW2"
    cnipt = f"{CONVENTIONAL_NIPT_ANALYSIS_TYPE}:ZW1"
    metadata = {"analyses": [cnipt, MNIPT, later]}
    with pytest.raises(AnalysisNotNamed):
        nipt_analysis_of(metadata, MONOGENIC_NIPT_ANALYSIS_TYPE, None)
    with pytest.raises(AnalysisNotNamed):
        nipt_analysis_of(metadata, MONOGENIC_NIPT_ANALYSIS_TYPE, cnipt)
    assert nipt_analysis_of(metadata, MONOGENIC_NIPT_ANALYSIS_TYPE, later) == later
    # One of a type: read without naming it.
    assert nipt_analysis_of(metadata, CONVENTIONAL_NIPT_ANALYSIS_TYPE, None) == cnipt
    assert nipt_analysis_of({"analyses": [cnipt]}, MONOGENIC_NIPT_ANALYSIS_TYPE, None) is None
    # The analysis a sign-out of one of them reads: its own; of another type, the one of that type.
    assert nipt_analysis_in_scope(metadata, MONOGENIC_NIPT_ANALYSIS_TYPE, later) == later
    assert nipt_analysis_in_scope(metadata, CONVENTIONAL_NIPT_ANALYSIS_TYPE, later) == cnipt


def test_a_maternal_plasma_is_either_nipts() -> None:
    assert is_maternal_plasma({"assays": [NIPT_CFDNA_ASSAY]})
    assert is_maternal_plasma({"assays": [CNIPT_CFDNA_ASSAY]})
    assert not is_maternal_plasma({"assays": ["wgs"]})
    assert not is_maternal_plasma(None)


def test_resolve_nipt_trio_returns_none_when_family_not_tagged() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
        ],
        analysis=None,
    )

    assert resolve_nipt_trio(family, None) is None


def test_resolve_nipt_trio_returns_none_without_a_father() -> None:
    family = _family([_member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY)])

    assert resolve_nipt_trio(family, MNIPT) is None


def test_resolve_nipt_trio_ignores_inactive_members() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-old", "mother", assay=NIPT_CFDNA_ASSAY, active=False),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
        ]
    )

    trio = resolve_nipt_trio(family, MNIPT)
    assert trio is not None
    assert trio.cfdna_sample_id == "cfdna-1"


def test_nipt_assay_key_defaults_without_panel() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
        ]
    )
    trio = resolve_nipt_trio(family, MNIPT)
    assert trio is not None
    assert nipt_assay_key(family, trio) == DEFAULT_NIPT_ASSAY_KEY


def test_nipt_assay_key_reads_panel_from_cfdna_metadata() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY, assay_panel="nipt_panel_v2"),
        ]
    )
    trio = resolve_nipt_trio(family, MNIPT)
    assert trio is not None
    assert nipt_assay_key(family, trio) == "nipt_panel_v2"


def test_nipt_assay_key_reads_the_monogenic_nipts_panel_only() -> None:
    # One plasma, the sample of a monogenic NIPT on a capture panel and of a conventional NIPT
    # whose package names a panel of its own: the artifact list is the monogenic NIPT's.
    plasma = _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY)
    plasma.sample_metadata["assay_panels"] = {MNIPT: "nipt_panel_v2", "conventional_nipt:ZW1": "lowpass"}
    family = _family([_member("father-1", "father"), plasma])
    trio = resolve_nipt_trio(family, MNIPT)
    assert trio is not None
    assert nipt_assay_key(family, trio) == "nipt_panel_v2"
    # The conventional NIPT's panel alone: the default scope.
    plasma.sample_metadata["assay_panels"] = {"conventional_nipt:ZW1": "lowpass"}
    assert nipt_assay_key(family, trio) == DEFAULT_NIPT_ASSAY_KEY


def test_family_member_out_exposes_sample_metadata() -> None:
    member = _family_member_out_from_row(
        {
            "sample_id": "cfdna-1",
            "role": "mother",
            "sex": "female",
            "clinical_status": "unaffected",
            "carrier_status": "unknown",
            "carrier_type": None,
            "carrier_evidence": {},
            "active": True,
            "sample_metadata": {"assay": NIPT_CFDNA_ASSAY},
        }
    )

    assert member.sample_metadata == {"assay": NIPT_CFDNA_ASSAY}


def test_family_member_out_defaults_sample_metadata_to_empty_dict() -> None:
    member = _family_member_out_from_row(
        {
            "sample_id": "father-1",
            "role": "father",
            "sex": "male",
            "clinical_status": "unaffected",
            "carrier_status": "unknown",
            "carrier_type": None,
            "carrier_evidence": {},
            "active": True,
        }
    )

    assert member.sample_metadata == {}
