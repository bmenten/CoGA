"""Family and sample IDs that CoGA cannot store are refused before anything is written.

An ID names its family or sample in the pedigree, the report, the audit trail, the import
job's record, the logs and file paths. One holding a control character (C0 or DEL: a line
break, an escape, a NUL) or whitespace could start a line in a log or report, hide in what a
screen shows, or (a NUL) not be stored at all. Discover and the package validation report
it (``family_id_invalid``, ``sample_id_invalid``) and read nothing else under it; the import
job, the Family Builder and the PED upload refuse it; the whitespace around an ID is
stripped. A PED field holds no whitespace but can hold the other control characters, and a
package without a PED takes its IDs from the request, the manifest or folder names. All IDs
here are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import io
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest
import yaml

from backend.app.core.config import settings
from backend.app.schemas import FamilyPackageManifestBuildRequest, ManualPedFamilyCreate, ManualPedMemberCreate
from backend.app.services import family_package_import as package_import
from backend.app.services import ped_service
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_identifiers import CONTROL_CHARACTERS, identifier_problem, visible
from backend.app.services.family_package_common import PackageManifest, _issue
from backend.app.services.family_package_discovery import discover_family_package_manifest
from backend.app.services.family_package_jobs import queue_family_import_job
from backend.app.services.family_package_long_read import long_read_sample_ids
from backend.app.services.family_package_manifest import _manifest_added_ped_rows
from backend.app.services.family_package_registration import existing_family_sample_ids
from backend.app.services.family_package_validation import load_validated_family_package

FAMILY = "FAM001"
MOTHER, FATHER, CHILD, INDEX = "MOTHER1", "FATHER1", "CHILD1", "INDEX1"
NUL, LF, ESC, BEL, DEL = "\x00", "\n", "\x1b", "\x07", "\x7f"


@pytest.fixture(autouse=True)
def _authorize_tmp_import_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])


class _ExplodingSession:
    """A session every query fails on: what ran against it read and wrote nothing."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("an ID no family can have reached the database")


def _admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


def _trio_ped(family_id: str = FAMILY, *, mother: str = MOTHER, father: str = FATHER, child: str = CHILD) -> str:
    return f"{family_id} {father} 0 0 1 1\n{family_id} {mother} 0 0 2 1\n{family_id} {child} {father} {mother} 1 2\n"


def _write_package(root: Path, *, manifest: dict[str, Any] | None = None, ped: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if ped is not None:
        (root / "family.ped").write_text(ped, encoding="utf-8")
    if manifest is not None:
        # safe_dump writes a control character as a YAML escape ("\e", "\0"), as an editor
        # or a script would have to.
        (root / "manifest.yaml").write_text(yaml.safe_dump({"schema_version": 1, **manifest}), encoding="utf-8")
    return root


def _write_long_read_sample(root: Path, sample_id: str, karyotype: str) -> None:
    """The per-sample folders of a long-read package: an annotated SNV VCF and a TRGT VCF
    whose header names the karyotype, each named after the sample."""
    snv = root / "snv" / sample_id / "annotation" / f"{sample_id}_annot.vcf.gz"
    trgt = root / "repeats" / sample_id / f"{sample_id}_tr.vcf.gz"
    for path, header in (
        (snv, "##fileformat=VCFv4.2\n"),
        (trgt, f"##fileformat=VCFv4.2\n##trgtCommand=trgt genotype --karyotype {karyotype}\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt") as handle:
            handle.write(f"{header}#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample_id}\n")


def _strings_with_control_characters(value: Any) -> list[str]:
    """The strings of a dumped model that hold a control character: the job's record of a
    validation stores them, and Postgres cannot store a NUL."""
    if isinstance(value, str):
        return [value] if CONTROL_CHARACTERS.search(value) else []
    if isinstance(value, dict):
        return [found for pair in value.items() for part in pair for found in _strings_with_control_characters(part)]
    if isinstance(value, list):
        return [found for item in value for found in _strings_with_control_characters(item)]
    return []


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", [*range(0x20), 0x7F])
def test_every_c0_control_character_and_del_is_refused(code: int) -> None:
    character = chr(code)
    assert identifier_problem(f"FAM{character}001") == f"contains a control character ({visible(character)})"
    # The message shows it as an escape, never as the character itself.
    assert visible(character).startswith("\\")


@pytest.mark.parametrize("value", [FAMILY, MOTHER, "FAM_TRIO", "fam-01.b", "FAMÉ01"])
def test_printable_text_without_spaces_is_an_id(value: str) -> None:
    assert identifier_problem(value) is None


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("FAM 001", "contains a space"),
        (" FAM001", "contains a space"),
        ("FAM\xa0001", "contains whitespace (\\xa0)"),
        ("FAM 001", "contains whitespace (\\u2028)"),
        ("", "is empty"),
    ],
)
def test_whitespace_in_an_id_is_refused(value: str, problem: str) -> None:
    # A PED row is split on whitespace: such an ID could not be read back from the stored
    # pedigree. The whitespace around an ID is stripped where it is read, before this.
    assert identifier_problem(value) == problem


def test_a_message_writes_what_a_reader_cannot_see_as_its_escape() -> None:
    assert visible(f"A{LF}B{ESC}C{NUL}D{DEL}E\xa0Fé") == "A\\nB\\x1bC\\x00D\\x7fE\\xa0Fé"


def test_a_validation_issue_carries_no_control_character() -> None:
    # An ID, a file name or an exception's text an issue names becomes a space there, as in
    # a log line: the import job stores the issue, and Postgres cannot store a NUL.
    issue = _issue(
        "ped_family_mismatch",
        f"PED family ID 'FAM{NUL}001'{LF}does not match",
        dataset=f"snv{DEL}",
        sample_id=f"MOTHER{ESC}1",
        path=f"/data/FAM{BEL}001/family.ped",
    )
    assert issue.message == "PED family ID 'FAM 001' does not match"
    assert (issue.dataset, issue.sample_id, issue.path) == ("snv ", "MOTHER 1", "/data/FAM 001/family.ped")


# --------------------------------------------------------------------------- #
# Discover
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("family_id", [f"FAM{NUL}001", f"FAM{ESC}001", f"FAM{LF}001", "FAM 001"])
def test_discover_refuses_a_requested_family_id_no_family_can_have(tmp_path: Path, family_id: str) -> None:
    root = _write_package(tmp_path / FAMILY, ped=_trio_ped())

    out = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(root), family_id=family_id))

    # No draft, and no file was looked up under the ID: a NUL could not even name one.
    assert (out.valid, out.family_id, out.manifest_yaml) == (False, None, "")
    assert [issue.code for issue in out.errors] == ["family_id_invalid"]
    message = out.errors[0].message
    assert message.startswith(f"Family ID '{visible(family_id)}' (the family_id of the request) contains")
    assert not CONTROL_CHARACTERS.search(message)


def test_discover_strips_the_whitespace_around_a_requested_family_id(tmp_path: Path) -> None:
    root = _write_package(tmp_path / FAMILY, ped=_trio_ped())

    out = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(root), family_id=f"  {FAMILY}\t"))

    assert out.valid, out.errors
    assert out.family_id == FAMILY
    assert yaml.safe_load(out.manifest_yaml)["family_id"] == FAMILY


def test_discover_refuses_a_folder_name_no_family_can_have(tmp_path: Path) -> None:
    root = tmp_path / f"COUPLE{ESC}1"
    _write_long_read_sample(root, MOTHER, "XX")
    _write_long_read_sample(root, FATHER, "XY")

    out = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(root)))

    assert [issue.code for issue in out.errors] == ["family_id_invalid"]
    assert "'COUPLE\\x1b1' (the folder name) contains a control character (\\x1b)" in out.errors[0].message


def test_discover_refuses_a_sample_id_in_the_ped_that_cannot_be_stored(tmp_path: Path) -> None:
    root = _write_package(tmp_path / FAMILY, ped=_trio_ped(mother=f"MOTHER{NUL}1"))

    out = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(root)))

    # A PED field holds no whitespace, but it can hold another control character.
    assert (out.valid, out.manifest_yaml) == (False, "")
    assert [issue.code for issue in out.errors] == ["sample_id_invalid"]
    issue = out.errors[0]
    assert issue.message.startswith("Sample ID 'MOTHER\\x001' (in the PED) contains a control character (\\x00).")
    assert issue.path == str(root / "family.ped")


def test_discover_reports_a_per_sample_folder_no_sample_can_be_stored_under(tmp_path: Path) -> None:
    root = tmp_path / "COUPLE1"
    _write_long_read_sample(root, f"MOTHER{ESC}1", "XX")
    _write_long_read_sample(root, "FATHER 1", "XY")

    # Listed rather than left out, so that no member goes missing without a word.
    assert long_read_sample_ids(root) == ["FATHER 1", f"MOTHER{ESC}1"]
    out = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(root)))

    assert (out.valid, out.manifest_yaml) == (False, "")
    messages = [issue.message for issue in out.errors if issue.code == "sample_id_invalid"]
    assert messages == [
        "Sample ID 'FATHER 1' (the name of its per-sample folder) contains a space. "
        "Family and sample IDs are printable text without spaces.",
        "Sample ID 'MOTHER\\x1b1' (the name of its per-sample folder) contains a control character (\\x1b). "
        "Family and sample IDs are printable text without spaces.",
    ]


def test_discover_refuses_a_sample_id_of_the_family_in_the_database_that_cannot_be_stored(tmp_path: Path) -> None:
    root = _write_package(tmp_path / FAMILY)

    out = discover_family_package_manifest(
        FamilyPackageManifestBuildRequest(folder_path=str(root), family_id=FAMILY),
        db_sample_ids=[MOTHER, f"FATHER{BEL}1"],
    )

    assert [issue.code for issue in out.errors] == ["sample_id_invalid"]
    assert "'FATHER\\x071' (of the family in the database)" in out.errors[0].message


# --------------------------------------------------------------------------- #
# The package validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("family_id", [f"FAM{LF}001", f"FAM{ESC}001", f"FAM{NUL}001", f"FAM{DEL}001", "FAM 001"])
def test_a_manifest_family_id_no_family_can_have_stops_the_validation(tmp_path: Path, family_id: str) -> None:
    root = _write_package(
        tmp_path / FAMILY,
        manifest={"family_id": family_id, "ped": "family.ped", "datasets": {"snv": {"family_vcf": "missing.vcf"}}},
        ped=_trio_ped(),
    )

    validation, bundle = load_validated_family_package(root)

    # Nothing else is read under it: no PED mismatch, no missing file, no dataset.
    assert bundle is None
    assert [issue.code for issue in validation.errors] == ["family_id_invalid"]
    assert validation.errors[0].message.startswith(f"Family ID '{visible(family_id)}' (the manifest's family_id)")
    assert (validation.family_id, validation.sample_ids, validation.datasets) == (None, [], [])
    # The job stores this as it is.
    assert _strings_with_control_characters(validation.model_dump(mode="json")) == []


def test_a_folder_name_no_family_can_have_is_refused(tmp_path: Path) -> None:
    root = _write_package(tmp_path / f"FAM{ESC}001", manifest={"ped": "family.ped"}, ped=_trio_ped(f"FAM{ESC}001"))

    validation, bundle = load_validated_family_package(root)

    assert bundle is None
    assert [issue.code for issue in validation.errors] == ["family_id_invalid"]
    assert "(the folder name) contains a control character (\\x1b)" in validation.errors[0].message


@pytest.mark.parametrize("control", [NUL, ESC, BEL, DEL])
def test_a_sample_id_in_the_ped_that_cannot_be_stored_stops_the_validation(tmp_path: Path, control: str) -> None:
    mother = f"MOTHER{control}1"
    root = _write_package(
        tmp_path / FAMILY,
        manifest={
            "family_id": FAMILY,
            "ped": "family.ped",
            "datasets": {"coverage": {"per_sample": {mother: {"bed": "coverage/mother.bed"}}}},
        },
        ped=_trio_ped(mother=mother),
    )

    validation, bundle = load_validated_family_package(root)

    assert bundle is None
    assert [issue.code for issue in validation.errors] == ["sample_id_invalid"]
    assert validation.errors[0].message.startswith(f"Sample ID '{visible(mother)}' (in the PED) contains a control")
    # The datasets were not read: their summaries would carry the ID into the job's record.
    assert (validation.family_id, validation.sample_ids, validation.datasets) == (FAMILY, [], [])
    assert _strings_with_control_characters(validation.model_dump(mode="json")) == []


def test_a_ped_family_id_no_family_can_have_is_named(tmp_path: Path) -> None:
    root = _write_package(
        tmp_path / FAMILY, manifest={"family_id": FAMILY, "ped": "family.ped"}, ped=_trio_ped(f"FAM{ESC}001")
    )

    validation, bundle = load_validated_family_package(root)

    assert bundle is None
    assert [issue.code for issue in validation.errors] == ["family_id_invalid"]
    assert "Family ID 'FAM\\x1b001' (in the PED)" in validation.errors[0].message


def _added_rows(add_members: Any) -> tuple[list[str], list[Any]]:
    return _manifest_added_ped_rows(
        PackageManifest.model_validate({"family": {"add_members": add_members}}),
        family_id=FAMILY,
        ped_sample_ids={FATHER, MOTHER},
    )


@pytest.mark.parametrize("sample_id", [f"INDEX{ESC}1", f"INDEX{NUL}1", "INDEX 1"])
def test_an_added_member_no_sample_can_be_stored_under_is_refused(sample_id: str) -> None:
    rows, errors = _added_rows([{"sample_id": sample_id, "sex": "female"}])

    assert rows == []
    assert [error.code for error in errors] == ["sample_id_invalid"]
    assert errors[0].message.startswith(f"Sample ID '{visible(sample_id)}' (under family.add_members)")


def test_an_added_members_parent_no_sample_can_be_stored_under_is_refused() -> None:
    rows, errors = _added_rows([{"sample_id": CHILD, "father": f"FATHER{LF}1", "mother": MOTHER}])

    assert rows == []
    assert [error.code for error in errors] == ["sample_id_invalid"]
    assert errors[0].message.startswith(f"Sample ID 'FATHER\\n1' (a parent of {CHILD} under family.add_members)")


def test_the_whitespace_around_an_added_member_is_stripped() -> None:
    rows, errors = _added_rows({f" {INDEX}\t": {"sex": "female", "father": f" {FATHER} "}})

    assert errors == []
    assert rows == [f"{FAMILY} {INDEX} {FATHER} 0 2 0 role=relative"]


def test_a_package_without_a_ped_refuses_a_member_no_sample_can_be_stored_under(tmp_path: Path) -> None:
    root = _write_package(
        tmp_path / "COUPLE1",
        manifest={
            "family_id": "COUPLE1",
            "family": {"add_members": [{"sample_id": f"MOTHER{ESC}1", "sex": "female"}, {"sample_id": FATHER, "sex": "male"}]},
            "datasets": {"repeats_trgt": {"per_sample": {f"MOTHER{ESC}1": {"file": "repeats/missing.vcf.gz"}}}},
        },
    )

    validation, bundle = load_validated_family_package(root)

    assert bundle is None
    assert [issue.code for issue in validation.errors] == ["sample_id_invalid"]
    assert (validation.sample_ids, validation.datasets) == ([], [])
    assert _strings_with_control_characters(validation.model_dump(mode="json")) == []


def test_the_whitespace_around_the_ids_of_a_package_is_stripped(tmp_path: Path) -> None:
    root = _write_package(
        tmp_path / FAMILY,
        manifest={
            "family_id": f"  {FAMILY} ",
            "ped": "family.ped",
            "family": {"add_members": [{"sample_id": f" {INDEX} ", "sex": "female"}]},
        },
        ped=_trio_ped(),
    )

    validation, bundle = load_validated_family_package(root)

    assert validation.valid, validation.errors
    assert bundle is not None
    assert validation.family_id == FAMILY
    assert validation.sample_ids == [FATHER, MOTHER, CHILD, INDEX]


# --------------------------------------------------------------------------- #
# The import: refused before anything is written
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_import_of_a_family_id_no_family_can_have_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _write_package(
        tmp_path / FAMILY, manifest={"family_id": f"FAM{NUL}001", "ped": "family.ped"}, ped=_trio_ped()
    )
    recorded: list[str] = []
    registered: list[Any] = []

    async def record_family(family_id: str) -> None:
        recorded.append(family_id)

    async def register(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        registered.append(kwargs)
        raise AssertionError("the family was registered")

    monkeypatch.setattr(package_import, "_ensure_family_from_ped", register)

    result = await package_import.execute_family_package_import(
        _ExplodingSession(),  # type: ignore[arg-type]
        folder_path=root,
        project_id="project-uuid",
        dry_run=False,
        user=_admin(),
        record_family=record_family,
    )

    assert (result.completed, result.error, result.family_id) == (False, "Package validation failed", None)
    assert [issue.code for issue in result.validation.errors] == ["family_id_invalid"]
    # The job never named a family, and nothing of one was written.
    assert (recorded, registered) == ([], [])
    assert result.logs[-1] == "Package validation failed; no data were imported."


@pytest.mark.asyncio
async def test_the_lookups_of_a_requested_family_skip_an_id_no_family_can_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Discover, Write manifest and Validate look the request's family up before the package
    # is read; such an ID reaches no query (Postgres cannot even take a NUL).
    for family_id in (f"FAM{NUL}001", f"FAM{ESC}001", "FAM 001", "  "):
        assert await existing_family_sample_ids(_ExplodingSession(), family_id) == []  # type: ignore[arg-type]
        assert await package_import.db_pedigree_fallback(_ExplodingSession(), family_id) is None  # type: ignore[arg-type]

    asked: list[str] = []

    async def build_pedigree_text(_session: Any, *, family_id: str) -> str:
        asked.append(family_id)
        return _trio_ped(family_id)

    monkeypatch.setattr(package_import.ped_service, "build_pedigree_text", build_pedigree_text)
    assert await package_import.db_pedigree_fallback(object(), f" {FAMILY}\t") == _trio_ped()  # type: ignore[arg-type]
    assert asked == [FAMILY]


@pytest.mark.asyncio
async def test_the_requested_family_is_compared_without_the_whitespace_around_it(tmp_path: Path) -> None:
    root = _write_package(tmp_path / FAMILY, manifest={"family_id": FAMILY, "ped": "family.ped"}, ped=_trio_ped())

    result = await package_import.execute_family_package_import(
        None,
        folder_path=root,
        project_id=None,
        dry_run=True,
        user=None,
        requested_family_id=f" {FAMILY} ",
    )

    assert result.completed, result.validation.errors
    assert result.validation.metadata["requested_family_id"] == FAMILY


@pytest.mark.asyncio
@pytest.mark.parametrize("family_id", [f"FAM{NUL}001", f"FAM{LF}001", "FAM 001"])
async def test_an_import_job_for_a_family_id_no_family_can_have_is_refused_before_it_is_written(family_id: str) -> None:
    with pytest.raises(HTTPException) as refused:
        await queue_family_import_job(
            _ExplodingSession(),  # type: ignore[arg-type]
            folder_path="/data/families/FAM001",
            project_id=None,
            dry_run=True,
            requested_family_id=family_id,
            requested_by="admin@example.com",
        )

    assert refused.value.status_code == 400
    assert str(refused.value.detail).startswith(f"family_id '{visible(family_id)}' contains")


@pytest.mark.asyncio
async def test_an_import_job_records_the_requested_family_without_the_whitespace_around_it() -> None:
    class _Written(Exception):
        pass

    class _CapturingSession:
        params: dict[str, Any] = {}

        async def execute(self, _statement: Any, params: dict[str, Any]) -> Any:
            self.params = params
            raise _Written

    session = _CapturingSession()
    with pytest.raises(_Written):
        await queue_family_import_job(
            session,  # type: ignore[arg-type]
            folder_path="/data/families/FAM001",
            project_id=None,
            dry_run=True,
            requested_family_id=f" {FAMILY} ",
            requested_by="admin@example.com",
        )

    assert json.loads(session.params["metadata"])["requested_family_id"] == FAMILY


# --------------------------------------------------------------------------- #
# The Family Builder and the PED upload
# --------------------------------------------------------------------------- #


def _family(family_id: str = FAMILY, *sample_ids: str) -> ManualPedFamilyCreate:
    return ManualPedFamilyCreate(
        family_id=family_id,
        members=[ManualPedMemberCreate(sample_id=sample_id) for sample_id in (sample_ids or (MOTHER,))],
    )


@pytest.mark.parametrize(
    ("family", "detail"),
    [
        (_family(f"FAM{ESC}001"), "Family ID 'FAM\\x1b001' contains a control character (\\x1b)."),
        (_family(FAMILY, MOTHER, f"FATHER{LF}1"), "Sample ID 'FATHER\\n1' contains a control character (\\n)."),
        (_family(FAMILY, "MOTHER 1"), "Sample ID 'MOTHER 1' contains a space."),
    ],
)
def test_the_family_builder_refuses_an_id_no_family_or_sample_can_have(family: ManualPedFamilyCreate, detail: str) -> None:
    with pytest.raises(HTTPException) as refused:
        ped_service._validate_manual_family(family)

    assert refused.value.status_code == 400
    assert str(refused.value.detail).startswith(detail)


@pytest.mark.asyncio
async def test_the_family_builder_strips_the_family_id_and_refuses_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[dict[str, Any]] = []

    async def no_project(_session: Any, _user: Any, _project_id: Any) -> None:
        return None

    async def replace_existing(_session: Any, family_ids: list[str], *_args: Any) -> None:
        written.append({"replaced": family_ids})

    async def available(*_args: Any) -> None:
        return None

    async def create_family(_session: Any, **kwargs: Any) -> dict[str, Any]:
        written.append({"created": kwargs["family_id"], "pedigree": kwargs["pedigree"]})
        return {"family_id": kwargs["family_id"], "samples": []}

    class _Session:
        async def commit(self) -> None:
            return None

    monkeypatch.setattr(ped_service, "_resolve_accessible_project_id", no_project)
    monkeypatch.setattr(ped_service, "_replace_existing_families", replace_existing)
    monkeypatch.setattr(ped_service, "_ensure_sample_ids_are_available", available)
    monkeypatch.setattr(ped_service, "_create_family", create_family)

    await ped_service.create_manual_family_data(_Session(), _family(f" {FAMILY} "), False, _admin())  # type: ignore[arg-type]
    assert written == [{"replaced": [FAMILY]}, {"created": FAMILY, "pedigree": f"{FAMILY} {MOTHER} 0 0 0 0"}]

    written.clear()
    with pytest.raises(HTTPException):
        await ped_service.create_manual_family_data(_Session(), _family(f"FAM{NUL}001"), False, _admin())  # type: ignore[arg-type]
    assert written == []


@pytest.mark.parametrize(
    ("ped", "detail"),
    [
        (_trio_ped(f"FAM{ESC}001"), "Family ID 'FAM\\x1b001' contains a control character (\\x1b)."),
        (_trio_ped(mother=f"MOTHER{NUL}1"), "Sample ID 'MOTHER\\x001' contains a control character (\\x00)."),
        (f"{FAMILY} {CHILD} FATHER{DEL}1 0 1 2\n", "Parent ID 'FATHER\\x7f1' contains a control character (\\x7f)."),
    ],
)
def test_a_ped_upload_with_an_id_no_family_or_sample_can_have_is_refused(ped: str, detail: str) -> None:
    with pytest.raises(HTTPException) as refused:
        ped_service._parse_ped_text(ped)

    assert refused.value.status_code == 400
    assert str(refused.value.detail).startswith(detail)


def test_a_ped_upload_of_printable_ids_parses() -> None:
    families = ped_service._parse_ped_text(_trio_ped())

    assert [member["iid"] for member in families[FAMILY]] == [FATHER, MOTHER, CHILD]


@pytest.mark.asyncio
async def test_a_ped_upload_is_refused_before_anything_is_written() -> None:
    upload = UploadFile(file=io.BytesIO(_trio_ped(mother=f"MOTHER{ESC}1").encode()), filename="family.ped")

    with pytest.raises(HTTPException) as refused:
        await ped_service.upload_ped_data(_ExplodingSession(), upload, False, _admin(), None)  # type: ignore[arg-type]

    assert refused.value.status_code == 400
