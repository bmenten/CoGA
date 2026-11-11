"""Corrections of a delivered package made in CoGA: the rules, without a database.

Which fields change (the laboratory's, and those the pipeline used) and which are refused (the
pipeline's own, and sample IDs); a correction set, removed or emptied, with each field's value
before and after; one that names a field twice, a member CoGA does not have, or changes nothing is
refused. A member is added to a pipeline's package only when its files name the sample and no
member is, and only a member added in CoGA is removed; a folder added by hand takes any member. An
edited package is a correction of what differs, field by field. A correction of what the pipeline
used (a member added with parents, or removed, among it) makes the delivery need attention while a
dataset it computed with the pedigree is in the import. A correction is made only on an open delivery
whose package was read, on its latest revision, while its folder is as it was checked. The paths
through the real stores are in e2e/test_e2e_import_delivery_corrections.py.

All samples and values here are synthetic.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest

from backend.app.schemas import ImportDeliveryChange
from backend.app.services import import_delivery_revisions as revisions
from backend.app.services.coga_package import CogaPackage, package_document
from backend.app.services.import_watcher import _pedigree_change_issue


def _document(**fields: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "coga-package/1",
        "status": "complete",
        "written_at": "2026-11-10T09:00:00Z",
        "pipeline": {"name": "p", "version": "1", "run_id": "RUN1"},
        "project": "PGT",
        "assembly": "GRCh38",
        "analysis_type": "pgt_m",
        "family": {"family_id": "FAM001", "pgt": {"inheritance_model": "AR"}},
        "members": [
            {"sample_id": "FATHER1", "sex": "male", "role": "father"},
            {"sample_id": "MOTHER", "sex": "female", "role": "mother"},
            {"sample_id": "EMB1", "sex": "female", "father": "FATHER1", "mother": "MOTHER", "role": "embryo"},
        ],
        "couples": [{"partners": ["FATHER1", "MOTHER"]}],
        "datasets": {"apcad": {"per_sample": {"EMB1": {"file": "apcad/EMB1.csv"}}}},
        "pipeline_used_pedigree": ["apcad"],
        "files": [{"path": "apcad/EMB1.csv", "size": 1, "sha256": "0" * 64}],
    }
    document.update(fields)
    return document


def _changes(*items: tuple[list[str], Any]) -> list[ImportDeliveryChange]:
    return [ImportDeliveryChange(path=path, value=value) for path, value in items]


# --- which fields change ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        (["project"], "laboratory"),
        (["analysis_type"], "laboratory"),
        (["family", "roi"], "laboratory"),
        (["family", "pgt"], "laboratory"),
        (["family", "pgt", "inheritance_model"], "laboratory"),
        (["family", "pgt", "indexes"], "laboratory"),
        (["datasets", "apcad"], "laboratory"),
        (["members", "EMB1", "clinical_status"], "laboratory"),
        (["members", "EMB1", "hpo"], "laboratory"),
        (["members", "EMB1", "related_to"], "laboratory"),
        (["members", "EMB1", "assay_panel"], "laboratory"),
        (["members", "EMB1", "sex"], "pipeline_used"),
        (["members", "EMB1", "father"], "pipeline_used"),
        (["couples"], "pipeline_used"),
    ],
)
def test_the_laboratorys_fields_and_those_the_pipeline_used_change(path: list[str], kind: str) -> None:
    assert revisions.change_kind(path) == kind


@pytest.mark.parametrize(
    "path",
    [
        ["members", "EMB1", "sample_id"],
        ["family", "family_id"],
        ["assembly"],
        ["files"],
        ["pipeline", "run_id"],
        ["qc"],
        ["status"],
        ["pipeline_used_pedigree"],
        ["extra"],
        ["members", "EMB1", "unknown_field"],
    ],
)
def test_the_pipelines_own_fields_are_never_changed_in_coga(path: list[str]) -> None:
    with pytest.raises(HTTPException) as refused:
        revisions.change_kind(path)
    assert refused.value.status_code == 400
    assert revisions.FIXED_FIELD in refused.value.detail


def test_a_field_the_package_does_not_have_is_refused() -> None:
    with pytest.raises(HTTPException) as refused:
        revisions.change_kind(["nonsense"])
    assert (refused.value.status_code, refused.value.detail) == (400, "nonsense is no field of the package that can be corrected.")


# --- making a correction ---------------------------------------------------------------------


def test_a_correction_sets_removes_and_empties_and_says_what_it_changed() -> None:
    document = _document()

    revised, made = revisions.apply_changes(
        document,
        _changes(
            (["members", "EMB1", "clinical_status"], "affected"),
            (["members", "FATHER1", "role"], None),
            (["family", "pgt", "inheritance_model"], None),
            (["couples"], None),
            (["family", "roi"], "CFTR"),
            (["datasets", "apcad"], {"enabled": False}),
        ),
    )

    assert document == _document()  # the package corrected is a copy
    members = {member["sample_id"]: member for member in revised["members"]}
    assert members["EMB1"]["clinical_status"] == "affected"
    assert "role" not in members["FATHER1"]
    assert revised["couples"] == []
    assert revised["family"] == {"family_id": "FAM001", "roi": "CFTR"}  # an emptied pgt goes
    assert revised["datasets"]["apcad"] == {"enabled": False}
    assert [(change.path, change.kind, change.before, change.after) for change in made] == [
        (["members", "EMB1", "clinical_status"], "laboratory", None, "affected"),
        (["members", "FATHER1", "role"], "laboratory", "father", None),
        (["family", "pgt", "inheritance_model"], "laboratory", "AR", None),
        (["couples"], "pipeline_used", [{"partners": ["FATHER1", "MOTHER"]}], []),
        (["family", "roi"], "laboratory", None, "CFTR"),
        (["datasets", "apcad"], "laboratory", {"per_sample": {"EMB1": {"file": "apcad/EMB1.csv"}}}, {"enabled": False}),
    ]
    CogaPackage.model_validate(revised)


def test_a_correction_of_a_family_without_pgt_or_datasets_makes_them() -> None:
    document = _document(datasets=None)
    document["family"] = {"family_id": "FAM001"}
    document.pop("datasets")

    revised, _made = revisions.apply_changes(
        document, _changes((["family", "pgt", "indexes"], ["MOTHER"]), (["datasets", "qc"], {"report": "r.html"}))
    )

    assert revised["family"]["pgt"] == {"indexes": ["MOTHER"]}
    assert revised["datasets"] == {"qc": {"report": "r.html"}}


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ([(["members", "EMB1", "role"], "proband"), (["members", "EMB1", "role"], "sibling")], "members.EMB1.role is changed twice in one correction."),
        ([(["members", "NOBODY", "role"], "proband")], "members.NOBODY is no member of the package"),
        ([(["members", "EMB1", "role"], "embryo"), (["members", "EMB1", "related_to"], None)], "The correction changes nothing in the package."),
    ],
)
def test_a_correction_that_names_a_field_twice_a_member_coga_lacks_or_changes_nothing_is_refused(
    changes: list[tuple[list[str], Any]], detail: str
) -> None:
    with pytest.raises(HTTPException) as refused:
        revisions.apply_changes(_document(), _changes(*changes))
    assert refused.value.status_code == 400
    assert refused.value.detail.startswith(detail)


# --- members added and removed ---------------------------------------------------------------

_RELATIVE = {"sample_id": "REL1", "sex": "male", "role": "relative", "clinical_status": "affected", "related_to": ["MOTHER"]}
_OFFERED = revisions.MemberRule(addable=frozenset({"REL1"}), as_checked=frozenset({"FATHER1", "MOTHER", "EMB1"}))


def test_the_samples_offered_as_members_are_those_the_files_name_and_no_member_is() -> None:
    issues = [
        {"code": "dataset_vcf_sample_unknown", "message": "m", "sample_id": "REL1"},
        {"code": "dataset_vcf_sample_unknown", "message": "m", "sample_id": "REL1"},
        {"code": "dataset_vcf_sample_unknown", "message": "m", "sample_id": None},
        {"code": "package_project_unknown", "message": "m", "sample_id": "EMB1"},
    ]
    assert revisions.addable_samples(issues) == ["REL1"]
    assert revisions.addable_samples('[{"code": "dataset_vcf_sample_unknown", "message": "m", "sample_id": "REL2"}]') == ["REL2"]
    assert revisions.addable_samples(None) == []


def test_a_sample_the_files_name_is_added_as_a_member_with_its_fields_and_its_index_role() -> None:
    revised, made = revisions.apply_changes(
        _document(),
        _changes(
            (["family", "pgt", "indexes"], ["REL1"]),
            (["members", "REL1"], {key: value for key, value in _RELATIVE.items() if key != "sample_id"}),
        ),
        _OFFERED,
    )

    assert revised["members"][-1] == _RELATIVE
    assert revised["family"]["pgt"]["indexes"] == ["REL1"]
    # The member is added first, whatever the order of the changes.
    assert [(change.path, change.kind, change.before, change.after) for change in made] == [
        (["members", "REL1"], "laboratory", None, _RELATIVE),
        (["family", "pgt", "indexes"], "laboratory", None, ["REL1"]),
    ]
    CogaPackage.model_validate(revised)

    # Added, it is corrected and removed again like any member CoGA added.
    _again, corrected = revisions.apply_changes(revised, _changes((["members", "REL1", "sex"], "female")), _OFFERED)
    assert corrected[0].kind == "pipeline_used"
    removed, made = revisions.apply_changes(revised, _changes((["members", "REL1"], None)), _OFFERED)
    assert [member["sample_id"] for member in removed["members"]] == ["FATHER1", "MOTHER", "EMB1"]
    assert (made[0].before, made[0].after) == (_RELATIVE, None)


@pytest.mark.parametrize(
    ("change", "rule", "detail"),
    [
        ((["members", "OTHER"], {"sex": "male"}), _OFFERED, "members.OTHER is no sample the package's files name"),
        ((["members", "REL1"], {"sex": "male"}), None, "members.REL1 is no sample the package's files name"),
        ((["members", "EMB1"], {"sex": "male"}), _OFFERED, "members.EMB1 is a member already"),
        ((["members", "REL1"], {"sample_id": "REL2", "sex": "male"}), _OFFERED, "members.REL1 is added with another sample_id"),
        ((["members", "REL1"], "male"), _OFFERED, "members.REL1 is added as a member: give its fields"),
        ((["members", "EMB1"], None), _OFFERED, "members.EMB1 is a member of the pipeline's package and is not removed"),
        ((["members", "EMB1"], None), None, "members.EMB1 is a member of the pipeline's package and is not removed"),
        ((["members", "NOBODY"], None), _OFFERED, "members.NOBODY is no member of the package"),
    ],
)
def test_no_other_member_is_added_to_or_removed_from_a_pipelines_package(
    change: tuple[list[str], Any], rule: revisions.MemberRule | None, detail: str
) -> None:
    with pytest.raises(HTTPException) as refused:
        revisions.apply_changes(_document(), _changes(change), rule)
    assert refused.value.status_code == 400
    assert refused.value.detail.startswith(detail)


def test_a_folder_added_by_hand_takes_any_member_added_or_removed() -> None:
    by_hand = revisions.MemberRule(any_member=True, as_checked=frozenset({"FATHER1", "MOTHER", "EMB1"}))

    revised, made = revisions.apply_changes(
        _document(),
        _changes((["members", "EMB1"], None), (["members", "EMB2"], {"sex": "unknown", "father": "FATHER1", "mother": "MOTHER"})),
        by_hand,
    )

    assert [member["sample_id"] for member in revised["members"]] == ["FATHER1", "MOTHER", "EMB2"]
    assert [(change.path, change.kind) for change in made] == [(["members", "EMB1"], "laboratory"), (["members", "EMB2"], "laboratory")]


# --- an edited package -----------------------------------------------------------------------


def test_an_edited_package_is_the_changes_that_differ_field_by_field() -> None:
    edited = _document()
    edited["members"] = [dict(member) for member in edited["members"]]
    edited["members"][2]["clinical_status"] = "affected"
    edited["members"].append(_RELATIVE)
    edited["family"] = {"family_id": "FAM001", "roi": "CFTR", "pgt": {"inheritance_model": "AR", "indexes": ["REL1"]}}
    edited["datasets"] = {"apcad": {"per_sample": {"EMB1": {"file": "apcad/EMB1.csv"}}, "enabled": False}}
    edited["couples"] = []

    changes = revisions.package_changes(_document(), edited)

    assert [(change.path, change.value) for change in changes] == [
        (["couples"], []),
        (["datasets", "apcad"], edited["datasets"]["apcad"]),
        (["family", "pgt", "indexes"], ["REL1"]),
        (["family", "roi"], "CFTR"),
        (["members", "EMB1", "clinical_status"], "affected"),
        (["members", "REL1"], _RELATIVE),
    ]
    revised, _made = revisions.apply_changes(_document(), changes, _OFFERED)
    assert package_document(CogaPackage.model_validate(revised)) == package_document(CogaPackage.model_validate(edited))


def test_an_edited_package_that_changes_a_fixed_field_nothing_or_the_format_is_refused() -> None:
    renamed = _document(assembly="GRCh37")
    with pytest.raises(HTTPException) as refused:
        revisions.apply_changes(_document(), revisions.package_changes(_document(), renamed), _OFFERED)
    assert (refused.value.status_code, refused.value.detail) == (400, f"assembly {revisions.FIXED_FIELD}")

    with pytest.raises(HTTPException) as refused:
        revisions.package_changes(_document(), _document())
    assert refused.value.detail == "The correction changes nothing in the package."

    with pytest.raises(HTTPException) as refused:
        revisions.package_changes(_document(), _document(surprise=True))
    assert refused.value.detail == "The edited package does not follow the format: surprise."

    with pytest.raises(HTTPException) as refused:
        revisions.package_changes(_document(), ["not", "an", "object"])
    assert refused.value.detail == "The edited package is no JSON object."


# --- what the pipeline used ------------------------------------------------------------------


def test_a_change_of_what_the_pipeline_used_needs_attention_while_its_dataset_is_imported() -> None:
    base = CogaPackage.model_validate(_document())
    unchanged = _pedigree_change_issue(base, base)
    assert unchanged is None

    corrected, _made = revisions.apply_changes(_document(), _changes((["members", "EMB1", "sex"], "male")))
    issue = _pedigree_change_issue(base, CogaPackage.model_validate(corrected))
    assert issue is not None and issue.code == "package_pedigree_changed"
    assert "(EMB1)" in issue.message and "computed apcad" in issue.message

    # The dataset left out of the import: nothing the pipeline computed with the pedigree is read.
    corrected["datasets"]["apcad"]["enabled"] = False
    assert _pedigree_change_issue(base, CogaPackage.model_validate(corrected)) is None

    # The couples, and a package whose pipeline says it used no pedigree.
    uncoupled, _made = revisions.apply_changes(_document(), _changes((["couples"], None)))
    issue = _pedigree_change_issue(base, CogaPackage.model_validate(uncoupled))
    assert issue is not None and "(the couples)" in issue.message
    unused = CogaPackage.model_validate(_document(pipeline_used_pedigree=[]))
    resexed, _made = revisions.apply_changes(_document(pipeline_used_pedigree=[]), _changes((["members", "EMB1", "sex"], "male")))
    assert _pedigree_change_issue(unused, CogaPackage.model_validate(resexed)) is None


def test_a_member_added_with_parents_or_removed_changes_the_pedigree_one_added_without_does_not() -> None:
    base = CogaPackage.model_validate(_document())
    by_hand = revisions.MemberRule(any_member=True)

    relative, _made = revisions.apply_changes(_document(), _changes((["members", "REL1"], _RELATIVE)), _OFFERED)
    assert _pedigree_change_issue(base, CogaPackage.model_validate(relative)) is None

    child, _made = revisions.apply_changes(
        _document(), _changes((["members", "EMB2"], {"sex": "male", "father": "FATHER1", "mother": "MOTHER"})), by_hand
    )
    issue = _pedigree_change_issue(base, CogaPackage.model_validate(child))
    assert issue is not None and "(EMB2)" in issue.message

    fewer = _document()
    fewer["members"] = fewer["members"][:2]
    issue = _pedigree_change_issue(base, CogaPackage.model_validate(fewer))
    assert issue is not None and "(EMB1)" in issue.message


# --- when a correction is made ---------------------------------------------------------------


_ID = "00000000-0000-4000-8000-000000000001"
_USER = SimpleNamespace(id="00000000-0000-4000-8000-0000000000aa", username="superuser", email="superuser@example.org")


class _Session:
    """Answers the locked read with ``row``."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.statements: list[str] = []

    async def execute(self, statement: Any, _params: Any = None) -> Any:
        self.statements.append(str(statement))
        row = self.row
        return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))


def _delivery(**fields: Any) -> dict[str, Any]:
    row = {
        "id": _ID,
        "folder_path": "/data/share/FAM001",
        "folder_name": "FAM001",
        "state": "ready",
        "package_fingerprint": "a" * 64,
        "marker_sha256": "b" * 64,
        "marker": _document(),
        "run_id": "RUN1",
        "family_id": "FAM001",
        "project_id": None,
        "issues": [],
        "warnings": [],
        "package": _document(),
        "package_check": {"issues": [], "warnings": [], "sha256": {}, "proposed": [], "proposal_issues": []},
        "origin": "watched",
    }
    row.update(fields)
    return row


@pytest.fixture
def unchanged_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fingerprint(_folder: str) -> str:
        return "a" * 64

    async def revision(_session: Any, _delivery_id: str) -> tuple[int, None]:
        return 0, None

    monkeypatch.setattr(revisions, "_folder_fingerprint", fingerprint)
    monkeypatch.setattr(revisions, "latest_revision", revision)


async def _revise(row: dict[str, Any] | None, *, revision: int = 0, changes: Any = None, package: Any = None) -> Any:
    return await revisions.revise_delivery(
        _Session(row),  # type: ignore[arg-type]
        delivery_id=_ID,
        user=_USER,  # type: ignore[arg-type]
        revision=revision,
        changes=changes or (None if package is not None else _changes((["members", "EMB1", "clinical_status"], "affected"))),
        package=package,
        reason="From the request form.",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "revision", "status", "detail"),
    [
        (None, 0, 404, revisions.DELIVERY_NOT_FOUND),
        ({"state": "queued"}, 0, 409, "The delivery is queued: only one that is ready or needs attention is corrected."),
        ({"package": None}, 0, 409, "CoGA could not read the delivery's package"),
        ({}, 1, 409, revisions.DELIVERY_REVISED),
    ],
)
async def test_a_correction_is_made_only_on_an_open_read_delivery_at_its_latest_revision(
    unchanged_folder: None, row: dict[str, Any] | None, revision: int, status: int, detail: str
) -> None:
    with pytest.raises(HTTPException) as refused:
        await _revise(_delivery(**row) if row is not None else None, revision=revision)
    assert refused.value.status_code == status
    assert refused.value.detail.startswith(detail)


@pytest.mark.asyncio
async def test_a_correction_of_a_delivery_whose_folder_changed_is_refused(
    unchanged_folder: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def changed(_folder: str) -> str:
        return "c" * 64

    monkeypatch.setattr(revisions, "_folder_fingerprint", changed)

    with pytest.raises(HTTPException) as refused:
        await _revise(_delivery())
    assert (refused.value.status_code, refused.value.detail) == (409, revisions.DELIVERY_CHANGED)


@pytest.mark.asyncio
async def test_a_correction_the_format_refuses_is_refused_before_anything_is_stored(unchanged_folder: None) -> None:
    session_row = _delivery()

    with pytest.raises(HTTPException) as refused:
        await _revise(session_row, changes=_changes((["members", "EMB1", "sex"], "M")))
    assert refused.value.status_code == 400
    assert refused.value.detail == "The corrected package does not follow the format: members.2.sex."


class _Checked(Exception):
    """Raised by the stand-in check: the correction got as far as checking the delivery again."""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "added"),
    [
        ({}, False),
        ({"issues": [{"code": "dataset_vcf_sample_unknown", "message": "m", "sample_id": "EMB2"}]}, True),
        ({"origin": "manual"}, True),
    ],
)
async def test_an_edited_package_adds_a_member_by_the_deliverys_own_rule(
    unchanged_folder: None, monkeypatch: pytest.MonkeyPatch, row: dict[str, Any], added: bool
) -> None:
    checked: list[CogaPackage] = []

    async def check(*_args: Any, effective: CogaPackage, **_kwargs: Any) -> None:
        checked.append(effective)
        raise _Checked

    monkeypatch.setattr(revisions, "assess_content", check)
    edited = _document()
    edited["members"] = [*edited["members"], {"sample_id": "EMB2", "sex": "male"}]

    if added:
        with pytest.raises(_Checked):
            await _revise(_delivery(**row), package=edited)
        assert [member.sample_id for member in checked[0].members or []] == ["FATHER1", "MOTHER", "EMB1", "EMB2"]
    else:
        with pytest.raises(HTTPException) as refused:
            await _revise(_delivery(**row), package=edited)
        assert refused.value.status_code == 400
        assert refused.value.detail.startswith("members.EMB2 is no sample the package's files name")


def test_a_kept_package_is_read_from_its_json() -> None:
    assert revisions._json_object('{"a": 1}') == {"a": 1}
    assert revisions._json_object(None) is None
