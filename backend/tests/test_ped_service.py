"""The pedigree service (``ped_service``): a family's PED, made, read and written.

* Creating a family inserts its samples one by one (for their returned IDs) and stores its
  first structure version on the family's chain, with its ``pedigree`` audit event (SEC-7).
* The PED's phenotype column reads 1 as unaffected, 2 as affected and 0 or -9 as unknown; a
  carrier status is kept apart from the clinical status. A PED upload records the family's
  inheritance model and its obligate and proven carriers. Its proband is the first affected
  member no member names as a parent, wherever the parents' rows stand; with none affected, the
  first member not named as a parent. A parent is never the proband; every other member is a
  sibling.
* The Family Builder (manual family creation) threads the family's and the samples' metadata
  into the family it creates, and takes only the metadata its page sends: a monogenic NIPT
  family and its cfDNA sample. Every other key or value is refused with 400 before anything
  is replaced or written (SAFE-12): a family's annotation manifest or import state, another
  analysis type, a sample's sequencing QC or assay panel, an assay that is no text. So is a
  pedigree it cannot store.
* A refusal that quotes an ID or a PED field (an unknown father or mother, a carrier outside
  the PED, a missing family, an unknown phenotype) writes each character a reader cannot see
  (a control, text-direction or zero-width character) as its escape (SEC-15).
* The PED download (``build_pedigree_text``) is written from the family's current members and
  parent-child relationships in the database, not the text stored on the family; a family
  without members is not found (404).

All IDs are synthetic.
"""

from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile

from backend.app.schemas import FamilyStructureCoupleUpdate, ManualPedFamilyCreate, ManualPedMemberCreate
from backend.app.services import ped_service
from backend.app.services.nipt import MONOGENIC_NIPT_ANALYSIS_TYPE, NIPT_CFDNA_ASSAY


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

    def first(self):
        return self._rows[0] if self._rows else None


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
        # The structure version's and the audit event's chains (SEC-7): the family's lock,
        # an empty chain head, the next version number and the two inserts.
        if "pg_advisory_xact_lock" in sql:
            return _FakeScalarResult(None)
        if "SELECT created_at, row_hash" in sql:
            return _FakeMappingResult([])
        if "INSERT INTO family_structure_versions" in sql:
            return _FakeMappingResult([{}])
        if "FROM family_structure_versions" in sql:
            return _FakeScalarResult(1)
        if "INSERT INTO clinical_audit_events" in sql:
            return _FakeMappingResult([{}])
        raise AssertionError(f"Unexpected SQL: {sql}")


class _FakeUser:
    id = "user-1"


def _no_database(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """The Family Builder's and the PED upload's reads and writes faked; returns what they
    would have replaced and created."""
    written: list[dict[str, object]] = []

    async def resolve_project(_session, _user, project_id):
        return project_id

    async def replace_existing(_session, family_ids, *_args, **_kwargs):
        written.append({"replaced": list(family_ids)})
        return []

    async def available(*_args, **_kwargs):
        return None

    async def create_family(_session, **kwargs):
        written.append({"created": kwargs})
        return {"family_id": kwargs["family_id"]}

    async def nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ped_service, "_resolve_accessible_project_id", resolve_project)
    monkeypatch.setattr(ped_service, "_replace_existing_families", replace_existing)
    monkeypatch.setattr(ped_service, "_ensure_sample_ids_are_available", available)
    monkeypatch.setattr(ped_service, "_create_family", create_family)
    monkeypatch.setattr(ped_service, "_delete_replaced_family_data", nothing)
    monkeypatch.setattr(ped_service, "_end_replacement", nothing)
    return written


class _CommitSession:
    async def commit(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# Creating a family
# --------------------------------------------------------------------------- #


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
    # sample_projects is a single batched (executemany) insert — params is the list of
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


@pytest.mark.asyncio
async def test_create_family_records_its_first_structure_version_and_pedigree_event() -> None:
    # SEC-7: the new family's pedigree is version 1 of its chained structure versions and the
    # first event of its clinical audit trail, both under the family's own ID.
    session = _RecordingSession()

    await ped_service._create_family(
        session,
        family_id="demo_family",
        pedigree="demo pedigree",
        members=[
            {"sample_id": "father", "sex": "male", "role": "father", "affected": False},
            {"sample_id": "proband", "sex": "female", "role": "proband", "affected": True},
        ],
        project_id=None,
        created_by="00000000-0000-0000-0000-0000000000aa",
        actor="analyst-d",
    )

    locks = [params["k"] for sql, params in session.calls if "pg_advisory_xact_lock" in sql]
    assert locks == ["fsv:demo_family", "fsv:demo_family", "cae:demo_family"]
    [version] = [params for sql, params in session.calls if "INSERT INTO family_structure_versions" in sql]
    assert (version["family_identifier"], version["version"], version["prev_hash"]) == ("demo_family", 1, None)
    assert version["created_by"] == "00000000-0000-0000-0000-0000000000aa" and len(version["row_hash"]) == 64
    [event] = [params for sql, params in session.calls if "INSERT INTO clinical_audit_events" in sql]
    assert (event["action"], event["actor"], event["family_identifier"]) == ("pedigree", "analyst-d", "demo_family")
    assert event["summary"] == (
        "Pedigree recorded with 2 members: father (father, male, affected status unknown); "
        "proband (proband, female, affected)"
    )


# --------------------------------------------------------------------------- #
# Reading a PED
# --------------------------------------------------------------------------- #


def test_ped_phenotype_conventions_follow_requested_mapping() -> None:
    assert ped_service._pedigree_status_from_phenotype("1") == "unaffected"
    assert ped_service._pedigree_status_from_phenotype("2") == "affected"
    assert ped_service._pedigree_status_from_phenotype("0") == "unknown"
    assert ped_service._pedigree_status_from_phenotype("-9") == "unknown"


def test_an_empty_ped_is_refused_and_a_line_of_fewer_than_six_fields_is_skipped() -> None:
    with pytest.raises(HTTPException) as refused:
        ped_service._parse_ped_text("\n  \n")
    assert (refused.value.status_code, refused.value.detail) == (400, "PED file is empty")

    families = ped_service._parse_ped_text("FAM1 FATHER1 0 0 1 1\nFAM1 PARTIAL 0 0 2\nFAM1 MOTHER1 0 0 2 1\n")
    assert [member["iid"] for member in families["FAM1"]] == ["FATHER1", "MOTHER1"]


@pytest.mark.parametrize(
    ("given", "recorded"),
    [("AR", "AR"), (" xlr ", "XLR"), ("MITO", "mitochondrial"), ("mitochondrial", "mitochondrial"), ("  ", None)],
)
def test_the_inheritance_model_is_one_the_analysis_knows(given: str, recorded: str | None) -> None:
    assert ped_service._normalize_inheritance_model(given) == recorded


def test_an_unknown_phenotype_is_quoted_with_its_unseen_characters_escaped() -> None:
    with pytest.raises(HTTPException) as refused:
        ped_service._pedigree_status_from_phenotype("2\u202e")
    assert refused.value.status_code == 400
    assert str(refused.value.detail).startswith("Unsupported PED phenotype '2\\u202e'. ")


def test_an_inheritance_model_the_analysis_does_not_know_is_refused() -> None:
    with pytest.raises(HTTPException) as refused:
        ped_service._normalize_inheritance_model("dominant")
    assert refused.value.status_code == 400


@pytest.mark.asyncio
async def test_a_ped_upload_records_the_inheritance_model_and_each_carrier(monkeypatch: pytest.MonkeyPatch) -> None:
    written = _no_database(monkeypatch)
    ped = "FAM1 FATHER1 0 0 1 1\nFAM1 MOTHER1 0 0 2 1\nFAM1 CHILD1 FATHER1 MOTHER1 2 2\n"
    upload = UploadFile(file=io.BytesIO(ped.encode()), filename="family.ped")

    await ped_service.upload_ped_data(
        _CommitSession(),  # type: ignore[arg-type]
        upload,
        False,
        _FakeUser(),  # type: ignore[arg-type]
        None,
        inheritance_model="mito",
        obligate_carriers="FATHER1; MOTHER1",
        proven_carriers="MOTHER1",
    )

    [replaced, created] = written
    assert replaced == {"replaced": ["FAM1"]}
    family = created["created"]
    assert family["family_metadata"] == {
        "pgt": {
            "inheritance_model": "mitochondrial",
            "obligate_carriers": ["FATHER1", "MOTHER1"],
            "proven_carriers": ["MOTHER1"],
        }
    }
    by_id = {member["sample_id"]: member for member in family["members"]}
    # A proven carrier is recorded as proven, though also listed as obligate.
    assert (by_id["MOTHER1"]["carrier_status"], by_id["MOTHER1"]["carrier_type"]) == ("carrier", "proven")
    assert (by_id["FATHER1"]["carrier_status"], by_id["FATHER1"]["carrier_type"]) == ("carrier", "obligate")
    assert (by_id["CHILD1"]["carrier_status"], by_id["CHILD1"]["carrier_type"]) == ("unknown", None)
    assert (by_id["FATHER1"]["role"], by_id["MOTHER1"]["role"]) == ("father", "mother")
    assert {
        key: by_id["CHILD1"][key] for key in ("role", "father_id", "mother_id", "sex", "clinical_status", "affected")
    } == {
        "role": "proband",
        "father_id": "FATHER1",
        "mother_id": "MOTHER1",
        "sex": "female",
        "clinical_status": "affected",
        "affected": True,
    }


@pytest.mark.asyncio
async def test_a_carrier_outside_the_ped_is_quoted_with_its_unseen_characters_escaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = _no_database(monkeypatch)
    ped = "FAM1 FATHER1 0 0 1 1\nFAM1 MOTHER1 0 0 2 1\n"
    upload = UploadFile(file=io.BytesIO(ped.encode()), filename="family.ped")

    with pytest.raises(HTTPException) as refused:
        await ped_service.upload_ped_data(
            _CommitSession(),  # type: ignore[arg-type]
            upload,
            False,
            _FakeUser(),  # type: ignore[arg-type]
            None,
            obligate_carriers="FATHER1; MOTHER\u202e1",
            proven_carriers="CARRIER\x1b1",
        )

    assert (refused.value.status_code, refused.value.detail) == (
        400,
        "Carrier sample IDs are not present in the PED: CARRIER\\x1b1, MOTHER\\u202e1",
    )
    assert all("created" not in step for step in written)


async def _uploaded_roles(monkeypatch: pytest.MonkeyPatch, ped: str) -> dict[str, str]:
    written = _no_database(monkeypatch)
    upload = UploadFile(file=io.BytesIO(ped.encode()), filename="family.ped")
    await ped_service.upload_ped_data(
        _CommitSession(),  # type: ignore[arg-type]
        upload,
        False,
        _FakeUser(),  # type: ignore[arg-type]
        None,
    )
    [_replaced, created] = written
    return {member["sample_id"]: member["role"] for member in created["created"]["members"]}


_TRIO_PARENTS = "FAM1 FATHER1 0 0 1 1\nFAM1 MOTHER1 0 0 2 1\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ped", "roles"),
    [
        pytest.param(
            _TRIO_PARENTS + "FAM1 PROBAND FATHER1 MOTHER1 1 2\n",
            {"FATHER1": "father", "MOTHER1": "mother", "PROBAND": "proband"},
            id="parents-first",
        ),
        pytest.param(
            "FAM1 PROBAND FATHER1 MOTHER1 1 2\n" + _TRIO_PARENTS,
            {"PROBAND": "proband", "FATHER1": "father", "MOTHER1": "mother"},
            id="child-first",
        ),
        pytest.param(
            "FAM1 PROBAND FATHER1 MOTHER1 1 2\nFAM1 FATHER1 0 0 1 1\nFAM1 SIB1 FATHER1 MOTHER1 2 1\n"
            "FAM1 MOTHER1 0 0 2 1\n",
            {"PROBAND": "proband", "FATHER1": "father", "SIB1": "sibling", "MOTHER1": "mother"},
            id="rows-mixed",
        ),
        pytest.param(
            "FAM1 PROBAND 0 0 2 2\n",
            {"PROBAND": "proband"},
            id="single-sample",
        ),
    ],
)
async def test_a_ped_upload_makes_the_affected_child_the_proband_wherever_its_row_stands(
    monkeypatch: pytest.MonkeyPatch, ped: str, roles: dict[str, str]
) -> None:
    """A member another member names as a parent is the father or mother; the first affected
    member no one names as a parent is the proband, whether the parents' rows come before or
    after it; any other member is a sibling."""
    assert await _uploaded_roles(monkeypatch, ped) == roles


@pytest.mark.asyncio
async def test_a_ped_upload_without_an_affected_child_takes_the_first_child_for_the_proband(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An affected parent stays the father: a parent is never the proband.
    ped = "FAM1 FATHER1 0 0 1 2\nFAM1 MOTHER1 0 0 2 1\nFAM1 CHILD1 FATHER1 MOTHER1 2 1\nFAM1 CHILD2 FATHER1 MOTHER1 1 0\n"

    assert await _uploaded_roles(monkeypatch, ped) == {
        "FATHER1": "father",
        "MOTHER1": "mother",
        "CHILD1": "proband",
        "CHILD2": "sibling",
    }


@pytest.mark.asyncio
async def test_a_ped_upload_of_several_children_makes_the_first_affected_one_the_proband(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The unaffected child listed before it is a sibling, as is the second affected child.
    ped = (
        "FAM1 SIB1 FATHER1 MOTHER1 2 1\nFAM1 PROBAND FATHER1 MOTHER1 1 2\nFAM1 SIB2 FATHER1 MOTHER1 2 2\n"
        + _TRIO_PARENTS
    )

    assert await _uploaded_roles(monkeypatch, ped) == {
        "SIB1": "sibling",
        "PROBAND": "proband",
        "SIB2": "sibling",
        "FATHER1": "father",
        "MOTHER1": "mother",
    }


# --------------------------------------------------------------------------- #
# The Family Builder (manual family creation)
# --------------------------------------------------------------------------- #


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


@pytest.mark.asyncio
async def test_manual_family_creation_threads_family_and_sample_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = _no_database(monkeypatch)

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
        _CommitSession(),  # type: ignore[arg-type]
        family,
        overwrite=False,
        user=_FakeUser(),  # type: ignore[arg-type]
    )

    captured = written[-1]["created"]
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
    written = _no_database(monkeypatch)

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
            user=_FakeUser(),  # type: ignore[arg-type]
        )

    assert refused.value.status_code == 400
    assert str(refused.value.detail).startswith(f"{named} metadata may hold only ")
    assert written == []


def _member(sample_id: str, sex: str = "und", **fields: object) -> ManualPedMemberCreate:
    return ManualPedMemberCreate(sample_id=sample_id, sex=sex, **fields)  # type: ignore[arg-type]


_PARENTS = [_member("FATHER1", "male"), _member("MOTHER1", "female")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("members", "couples", "detail"),
    [
        pytest.param([_member("  ")], [], "Sample id is required for every member", id="blank-id"),
        pytest.param([_member("S1"), _member(" S1 ")], [], "Sample ids must be unique within a family", id="same-id-twice"),
        pytest.param(
            [_member("S1", is_proband=True), _member("S2", is_proband=True)],
            [],
            "Only one proband can be selected",
            id="two-probands",
        ),
        pytest.param(
            [_member("S1", father_id="S1")], [], "A member cannot reference themselves as a parent", id="own-parent"
        ),
        pytest.param(
            [*_PARENTS, _member("CHILD1", father_id="FATHER1", mother_id="FATHER1")],
            [],
            "Father and mother must be different individuals",
            id="one-parent-twice",
        ),
        pytest.param([_member("CHILD1", father_id="FATHER9")], [], "Father id not found: FATHER9", id="unknown-father"),
        pytest.param([_member("CHILD1", mother_id="MOTHER9")], [], "Mother id not found: MOTHER9", id="unknown-mother"),
        # A parent's ID is quoted with each character a reader cannot see escaped (SEC-15).
        pytest.param(
            [_member("CHILD1", father_id="FATHER\u202e9")],
            [],
            "Father id not found: FATHER\\u202e9",
            id="unknown-father-text-direction",
        ),
        pytest.param(
            [_member("CHILD1", mother_id="MOTHER\x1b9")],
            [],
            "Mother id not found: MOTHER\\x1b9",
            id="unknown-mother-control-character",
        ),
        pytest.param(
            [_member("CHILD1", father_id="FATHER\u200b1")],
            [],
            "Father id not found: FATHER\\u200b1",
            id="unknown-father-zero-width",
        ),
        pytest.param(
            [*_PARENTS, _member("CHILD1", father_id="MOTHER1")],
            [],
            "Father must not have female sex",
            id="female-father",
        ),
        pytest.param(
            [*_PARENTS, _member("CHILD1", mother_id="FATHER1")],
            [],
            "Mother must not have male sex",
            id="male-mother",
        ),
        pytest.param(
            _PARENTS,
            [FamilyStructureCoupleUpdate(partners=["FATHER1", " FATHER1 "])],
            "Couple relationships require two different sample ids",
            id="couple-of-one",
        ),
        pytest.param(
            _PARENTS,
            [FamilyStructureCoupleUpdate(partners=["FATHER1", "OTHER1"])],
            "Couple partners must be members of the family",
            id="couple-outside-the-family",
        ),
    ],
)
async def test_the_family_builder_refuses_a_pedigree_it_cannot_store_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch,
    members: list[ManualPedMemberCreate],
    couples: list[FamilyStructureCoupleUpdate],
    detail: str,
) -> None:
    written = _no_database(monkeypatch)
    family = ManualPedFamilyCreate(family_id="FAM1", members=members, couples=couples)

    with pytest.raises(HTTPException) as refused:
        await ped_service.create_manual_family_data(
            _CommitSession(),  # type: ignore[arg-type]
            family,
            overwrite=True,
            user=_FakeUser(),  # type: ignore[arg-type]
        )

    assert (refused.value.status_code, refused.value.detail) == (400, detail)
    assert written == []


# --------------------------------------------------------------------------- #
# The PED download: written from the database's structure
# --------------------------------------------------------------------------- #


class _FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self):  # mimics SQLAlchemy Result.mappings()
        return self

    def all(self):
        return self._rows


class _QueuedSession:
    """Returns canned results for successive execute() calls."""

    def __init__(self, *result_rows: list[dict]) -> None:
        self._queue = list(result_rows)
        self.statements = 0

    async def execute(self, *_args, **_kwargs):
        self.statements += 1
        return _FakeResult(self._queue.pop(0))


@pytest.mark.asyncio
async def test_build_pedigree_text_reconstructs_from_database() -> None:
    members = [
        {"sample_id": "DAD", "sex": "male", "clinical_status": "unaffected", "role": "father"},
        {"sample_id": "MOM", "sex": "female", "clinical_status": "unaffected", "role": "mother"},
        {"sample_id": "KID", "sex": "male", "clinical_status": "affected", "role": "proband"},
    ]
    relationships = [
        {"parent_id": "DAD", "child_id": "KID", "role_a": "father"},
        {"parent_id": "MOM", "child_id": "KID", "role_a": "mother"},
    ]
    session = _QueuedSession(members, relationships)

    ped_text = await ped_service.build_pedigree_text(session, family_id="FAM1")  # type: ignore[arg-type]

    assert ped_text == (
        "FAM1 DAD 0 0 1 1\n"
        "FAM1 MOM 0 0 2 1\n"
        "FAM1 KID DAD MOM 1 2\n"
    )


@pytest.mark.asyncio
async def test_build_pedigree_text_missing_family_raises() -> None:
    session = _QueuedSession([])  # no members

    with pytest.raises(HTTPException) as missing:
        await ped_service.build_pedigree_text(session, family_id="UNKNOWN")  # type: ignore[arg-type]

    assert (missing.value.status_code, missing.value.detail) == (
        404,
        "Family 'UNKNOWN' was not found or has no members",
    )
    # Its relationships are not read.
    assert session.statements == 1


@pytest.mark.asyncio
async def test_a_missing_familys_id_is_quoted_with_its_unseen_characters_escaped() -> None:
    session = _QueuedSession([])

    with pytest.raises(HTTPException) as missing:
        await ped_service.build_pedigree_text(session, family_id="FAM\u202e1")  # type: ignore[arg-type]

    assert (missing.value.status_code, missing.value.detail) == (
        404,
        "Family 'FAM\\u202e1' was not found or has no members",
    )
