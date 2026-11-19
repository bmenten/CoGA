from __future__ import annotations

from datetime import datetime, timezone

from backend.app.schemas import FamilyMemberOut, FamilyOut
from backend.app.services.metadata_service import _family_member_out_from_row
from backend.app.services.nipt import (
    DEFAULT_NIPT_ASSAY_KEY,
    MONOGENIC_NIPT_ANALYSIS_TYPE,
    NIPT_CFDNA_ASSAY,
    NiptTrio,
    is_monogenic_nipt_family,
    nipt_assay_key,
    resolve_nipt_trio,
)


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
        metadata["assay_panel"] = assay_panel
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
    analysis_type: str | None = MONOGENIC_NIPT_ANALYSIS_TYPE,
) -> FamilyOut:
    metadata = {"analysis_types": [analysis_type]} if analysis_type else {}
    return FamilyOut(
        id="family-uuid",
        family_id="FAM001",
        created_at=datetime.now(timezone.utc),
        members=members,
        metadata=metadata,
    )


def test_is_monogenic_nipt_family_reads_the_analysis_types() -> None:
    assert is_monogenic_nipt_family(_family([], analysis_type=MONOGENIC_NIPT_ANALYSIS_TYPE))
    assert not is_monogenic_nipt_family(_family([], analysis_type=None))
    assert not is_monogenic_nipt_family(_family([], analysis_type="other"))


def test_a_family_holding_both_nipts_is_a_monogenic_nipt_family() -> None:
    family = _family([])
    both = family.model_copy(update={"metadata": {"analysis_types": ["conventional_nipt", "monogenic_nipt"]}})
    assert is_monogenic_nipt_family(both)
    only_cnipt = family.model_copy(update={"metadata": {"analysis_types": ["conventional_nipt"]}})
    assert not is_monogenic_nipt_family(only_cnipt)


def test_a_plasma_of_both_nipts_is_the_cfdna_sample() -> None:
    plasma = _member("PLASMA", "mother").model_copy(
        update={"sample_metadata": {"assays": ["cnipt_cfdna", "nipt_cfdna"]}}
    )
    trio = resolve_nipt_trio(_family([_member("FATHER1", "father"), plasma]))
    assert trio is not None and trio.cfdna_sample_id == "PLASMA"


def test_resolve_nipt_trio_identifies_cfdna_by_assay_tag() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
            _member("fetus-1", "embryo"),
        ]
    )

    assert resolve_nipt_trio(family) == NiptTrio(
        father_sample_id="father-1",
        cfdna_sample_id="cfdna-1",
        fetus_sample_id="fetus-1",
    )


def test_resolve_nipt_trio_treats_proband_child_as_the_fetus() -> None:
    # Manual family creation labels the child of both parents `proband`, not
    # `embryo`, so the resolver must accept a proband as the fetus.
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
            _member("fetus-1", "proband"),
        ]
    )

    trio = resolve_nipt_trio(family)
    assert trio is not None
    assert trio.fetus_sample_id == "fetus-1"


def test_resolve_nipt_trio_prefers_embryo_over_proband_for_fetus() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
            _member("proband-1", "proband"),
            _member("embryo-1", "embryo"),
        ]
    )

    trio = resolve_nipt_trio(family)
    assert trio is not None
    assert trio.fetus_sample_id == "embryo-1"


def test_resolve_nipt_trio_falls_back_to_mother_role_without_assay_tag() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("mother-1", "mother"),
        ]
    )

    trio = resolve_nipt_trio(family)
    assert trio is not None
    assert trio.cfdna_sample_id == "mother-1"
    assert trio.fetus_sample_id is None


def test_resolve_nipt_trio_returns_none_when_family_not_tagged() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
        ],
        analysis_type=None,
    )

    assert resolve_nipt_trio(family) is None


def test_resolve_nipt_trio_returns_none_without_a_father() -> None:
    family = _family([_member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY)])

    assert resolve_nipt_trio(family) is None


def test_resolve_nipt_trio_ignores_inactive_members() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-old", "mother", assay=NIPT_CFDNA_ASSAY, active=False),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
        ]
    )

    trio = resolve_nipt_trio(family)
    assert trio is not None
    assert trio.cfdna_sample_id == "cfdna-1"


def test_nipt_assay_key_defaults_without_panel() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY),
        ]
    )
    trio = resolve_nipt_trio(family)
    assert trio is not None
    assert nipt_assay_key(family, trio) == DEFAULT_NIPT_ASSAY_KEY


def test_nipt_assay_key_reads_panel_from_cfdna_metadata() -> None:
    family = _family(
        [
            _member("father-1", "father"),
            _member("cfdna-1", "mother", assay=NIPT_CFDNA_ASSAY, assay_panel="nipt_panel_v2"),
        ]
    )
    trio = resolve_nipt_trio(family)
    assert trio is not None
    assert nipt_assay_key(family, trio) == "nipt_panel_v2"


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
