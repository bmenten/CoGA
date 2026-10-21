from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app.schemas import ManualPedFamilyCreate, ManualPedMemberCreate
from backend.app.services import ped_service
from backend.app.services.nipt import MONOGENIC_NIPT_ANALYSIS_TYPE, NIPT_CFDNA_ASSAY


@pytest.mark.asyncio
async def test_manual_family_creation_threads_family_and_sample_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_resolve_project(_session, _user, project_id):
        return project_id

    async def fake_replace_existing(*_args, **_kwargs):
        return None

    async def fake_ensure_available(*_args, **_kwargs):
        return None

    async def fake_create_family(_session, **kwargs):
        captured.update(kwargs)
        return {"family_id": kwargs["family_id"]}

    monkeypatch.setattr(ped_service, "_resolve_accessible_project_id", fake_resolve_project)
    monkeypatch.setattr(ped_service, "_replace_existing_families", fake_replace_existing)
    monkeypatch.setattr(ped_service, "_ensure_sample_ids_are_available", fake_ensure_available)
    monkeypatch.setattr(ped_service, "_create_family", fake_create_family)

    class FakeSession:
        async def commit(self) -> None:
            return None

    class FakeUser:
        id = "user-1"

    family = ManualPedFamilyCreate(
        family_id="NIPT001",
        metadata={"analysis_type": MONOGENIC_NIPT_ANALYSIS_TYPE},
        members=[
            ManualPedMemberCreate(sample_id="father-1", sex="male"),
            ManualPedMemberCreate(
                sample_id="cfdna-1",
                sex="female",
                metadata={"assay": NIPT_CFDNA_ASSAY},
            ),
            ManualPedMemberCreate(
                sample_id="fetus-1",
                father_id="father-1",
                mother_id="cfdna-1",
                is_proband=True,
            ),
        ],
    )

    await ped_service.create_manual_family_data(
        FakeSession(),  # type: ignore[arg-type]
        family,
        overwrite=False,
        user=FakeUser(),  # type: ignore[arg-type]
    )

    assert captured["family_metadata"] == {"analysis_type": MONOGENIC_NIPT_ANALYSIS_TYPE}

    members_by_id = {member["sample_id"]: member for member in captured["members"]}
    assert members_by_id["cfdna-1"]["metadata"] == {"assay": NIPT_CFDNA_ASSAY}
    assert members_by_id["father-1"]["metadata"] == {}
    # The fetus is the child of both parents; manual creation labels it proband.
    assert members_by_id["father-1"]["role"] == "father"
    assert members_by_id["cfdna-1"]["role"] == "mother"
    assert members_by_id["fetus-1"]["role"] == "proband"


# What an import or an audited edit writes, and the backend trusts where it reads it (SAFE-12):
# the Family Builder refuses it before anything is replaced or written.
REFUSED_METADATA = [
    pytest.param({"annotation_manifest": {"pipeline": "client"}}, {}, "Family", id="family-annotation-manifest"),
    pytest.param({"import_incomplete": False}, {}, "Family", id="family-import-state"),
    pytest.param({"analysis_type": "pgt"}, {}, "Family", id="family-other-analysis-type"),
    pytest.param({}, {"sequencing_qc": {"mean_coverage": 40}}, "Sample S2", id="sample-sequencing-qc"),
    pytest.param({}, {"assay_panel": "panel-x"}, "Sample S2", id="sample-assay-panel"),
    pytest.param({}, {"assay": {"name": NIPT_CFDNA_ASSAY}}, "Sample S2", id="sample-assay-not-text"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("family_metadata", "sample_metadata", "named"), REFUSED_METADATA)
async def test_the_family_builder_refuses_metadata_its_page_does_not_send(
    monkeypatch: pytest.MonkeyPatch,
    family_metadata: dict[str, object],
    sample_metadata: dict[str, object],
    named: str,
) -> None:
    written: list[str] = []

    async def fake_resolve_project(_session, _user, project_id):
        return project_id

    async def fake_replace_existing(*_args, **_kwargs):
        written.append("replace")

    async def fake_create_family(_session, **_kwargs):
        written.append("create")
        return {}

    monkeypatch.setattr(ped_service, "_resolve_accessible_project_id", fake_resolve_project)
    monkeypatch.setattr(ped_service, "_replace_existing_families", fake_replace_existing)
    monkeypatch.setattr(ped_service, "_create_family", fake_create_family)

    class FakeUser:
        id = "user-1"

    family = ManualPedFamilyCreate(
        family_id="F1",
        metadata=family_metadata,
        members=[
            ManualPedMemberCreate(sample_id="S1", sex="male"),
            ManualPedMemberCreate(sample_id="S2", sex="female", metadata=sample_metadata),
        ],
    )

    with pytest.raises(HTTPException) as refused:
        await ped_service.create_manual_family_data(
            object(),  # type: ignore[arg-type]
            family,
            overwrite=True,
            user=FakeUser(),  # type: ignore[arg-type]
        )

    assert refused.value.status_code == 400
    assert str(refused.value.detail).startswith(f"{named} metadata may hold only ")
    assert written == []
