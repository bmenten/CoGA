import pytest

from backend.app.services import ped_service


class _FakeMappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def one(self):
        assert len(self._rows) == 1
        return self._rows[0]

    def all(self):
        return self._rows


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self._sample_counter = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "INSERT INTO families" in sql:
            return _FakeMappingResult([{"family_uuid": "family-uuid", "family_id": "demo_family"}])
        if "INSERT INTO samples" in sql:
            assert isinstance(params, dict)
            self._sample_counter += 1
            return _FakeMappingResult(
                [
                    {
                        "sample_uuid": f"sample-{self._sample_counter}",
                        "sample_id": params["sample_id"],
                    }
                ]
            )
        if "INSERT INTO family_members" in sql:
            return _FakeMappingResult([{}])
        if "INSERT INTO family_projects" in sql:
            return _FakeMappingResult([{}])
        if "INSERT INTO sample_projects" in sql:
            return _FakeMappingResult([{}])
        if "FROM family_members" in sql:
            return _FakeMappingResult(
                [
                    {
                        "sample_id": "father",
                        "sex": "male",
                        "role": "father",
                        "clinical_status": "unknown",
                        "carrier_status": "unknown",
                        "carrier_type": None,
                        "carrier_evidence": {},
                        "active": True,
                    },
                    {
                        "sample_id": "proband",
                        "sex": "female",
                        "role": "proband",
                        "clinical_status": "affected",
                        "carrier_status": "unknown",
                        "carrier_type": None,
                        "carrier_evidence": {},
                        "active": True,
                    },
                ]
            )
        if "FROM family_relationships" in sql:
            return _FakeMappingResult([])
        if "FROM family_structure_versions" in sql:
            return _FakeScalarResult(1)
        if "INSERT INTO family_structure_versions" in sql:
            return _FakeMappingResult([{}])
        raise AssertionError(f"Unexpected SQL: {sql}")


@pytest.mark.asyncio
async def test_create_family_inserts_samples_one_by_one_for_returning_ids() -> None:
    session = _RecordingSession()

    result = await ped_service._create_family(
        session,
        family_id="demo_family",
        pedigree="demo pedigree",
        members=[
            {"sample_id": "father", "sex": "male", "role": "father", "affected": False},
            {"sample_id": "proband", "sex": "female", "role": "proband", "affected": True},
        ],
        project_id="00000000-0000-0000-0000-000000000001",
    )

    sample_insert_calls = [params for sql, params in session.calls if "INSERT INTO samples" in sql]
    assert sample_insert_calls == [
        {"sample_id": "father", "family_uuid": "family-uuid", "sex": "male", "metadata_json": "{}"},
        {"sample_id": "proband", "family_uuid": "family-uuid", "sex": "female", "metadata_json": "{}"},
    ]
    family_insert = next(params for sql, params in session.calls if "INSERT INTO families" in sql)
    assert family_insert["metadata_json"] == "{}"
    assert family_insert["roi_query"] is None

    family_member_insert = next(
        params for sql, params in session.calls if "INSERT INTO family_members" in sql
    )
    assert family_member_insert == [
        {
            "family_uuid": "family-uuid",
            "sample_uuid": "sample-1",
            "role": "father",
            "clinical_status": "unknown",
            "carrier_status": "unknown",
            "carrier_type": None,
            "carrier_evidence": "{}",
            "affected": False,
        },
        {
            "family_uuid": "family-uuid",
            "sample_uuid": "sample-2",
            "role": "proband",
            "clinical_status": "affected",
            "carrier_status": "unknown",
            "carrier_type": None,
            "carrier_evidence": "{}",
            "affected": True,
        },
    ]
    family_project_insert = next(
        params for sql, params in session.calls if "INSERT INTO family_projects" in sql
    )
    assert family_project_insert == {
        "family_uuid": "family-uuid",
        "project_id": "00000000-0000-0000-0000-000000000001",
    }
    # sample_projects is now a single batched (executemany) insert — params is the list of
    # per-sample dicts in one call, mirroring the family_members insert above.
    sample_project_insert = next(
        params for sql, params in session.calls if "INSERT INTO sample_projects" in sql
    )
    assert sample_project_insert == [
        {
            "sample_uuid": "sample-1",
            "project_id": "00000000-0000-0000-0000-000000000001",
        },
        {
            "sample_uuid": "sample-2",
            "project_id": "00000000-0000-0000-0000-000000000001",
        },
    ]
    assert result == {"family_id": "demo_family", "samples": ["father", "proband"]}


def test_ped_phenotype_conventions_follow_requested_mapping() -> None:
    assert ped_service._pedigree_status_from_phenotype("1") == "unaffected"
    assert ped_service._pedigree_status_from_phenotype("2") == "affected"
    assert ped_service._pedigree_status_from_phenotype("0") == "unknown"
    assert ped_service._pedigree_status_from_phenotype("-9") == "unknown"


def test_manual_member_validation_preserves_carrier_status_separately() -> None:
    family = ped_service.ManualPedFamilyCreate(
        family_id="demo_family",
        members=[
            ped_service.ManualPedMemberCreate(
                sample_id="parent",
                affected=False,
                carrier_status="carrier",
                carrier_type="obligate",
            )
        ],
    )

    member = ped_service._validate_manual_family(family)[0]

    assert member.clinical_status == "unknown"
    assert member.affected is False
    assert member.carrier_status == "carrier"
    assert member.carrier_type == "obligate"
