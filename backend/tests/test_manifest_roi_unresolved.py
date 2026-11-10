"""A manifest's region of interest that does not resolve is reported, and clears the family's.

The PGT calls (``pgt_analysis.family_pgt_analysis``) and the NIPT coverage check read the family's
region of interest. Kept when the import cannot find the one its manifest names, the family's
earlier region would have the embryos called at a region the package does not name. So:

- the import leaves the family with the manifest's region or with none, and its result warns
  ``manifest_roi_unresolved``, naming the region it cleared;
- the dry run looks the region up as the import will, in the
  assembly of the import's project or of the existing family, and warns the same; with no
  assembly to look it up in it warns ``manifest_roi_not_checked``;
- a gene is looked up on its chromosome before a copy on an ALT contig, as the family page does.

All values are synthetic.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import types
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportValidationIssue
from backend.app.services import family_package_import as package_import
from backend.app.services import family_package_registration as registration
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.tests._family_import_fakes import admin_user
from backend.tests._kept_manifest import kept_manifest

FAMILY_UUID = "00000000-0000-0000-0000-00000000f001"
ASSEMBLY = "00000000-0000-0000-0000-0000000000a1"
PROJECT = "00000000-0000-0000-0000-0000000000b1"

_EARLIER = {
    "roi_query": "CFTR",
    "roi_label": "CFTR",
    "roi_chr": "7",
    "roi_start": 117480025,
    "roi_end": 117668665,
}


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return next(iter(self._rows[0].values())) if self._rows else None


class _Session:
    """Answers each statement with the rows of the first table it names in ``tables``, and
    records its SQL and parameters."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.tables = tables or {}
        self.statements: list[tuple[str, Any]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> _Rows:
        sql = " ".join(str(statement).split())
        self.statements.append((sql, params))
        for marker, rows in self.tables.items():
            if marker in sql:
                return _Rows(rows)
        return _Rows([])

    async def commit(self) -> None:
        self.commits += 1

    def updates(self) -> list[Any]:
        return [params for sql, params in self.statements if sql.startswith("UPDATE families")]

    def sql(self) -> str:
        return " ".join(sql for sql, _params in self.statements)


def _bundle(roi: str | None) -> Any:
    return types.SimpleNamespace(manifest=types.SimpleNamespace(roi=roi, metadata={}))


def _context(assembly_id: str | None = ASSEMBLY) -> Any:
    return types.SimpleNamespace(family_uuid=FAMILY_UUID, assembly_id=assembly_id)


def _apply(session: _Session, roi: str | None, *, assembly_id: str | None = ASSEMBLY) -> FamilyImportValidationIssue | None:
    return asyncio.run(registration._apply_manifest_roi(session, bundle=_bundle(roi), context=_context(assembly_id)))


_CLEARED = {
    "family_uuid": FAMILY_UUID,
    "query": None,
    "label": None,
    "source": None,
    "assembly_id": None,
    "chr": None,
    "start": None,
    "end": None,
}


# --- The import: the family's region is the manifest's, or none ------------------------------


def test_an_roi_the_import_does_not_find_clears_the_familys_and_says_which() -> None:
    session = _Session({"FROM genes": [], "FROM families": [dict(_EARLIER)]})

    issue = _apply(session, "NOT_A_GENE")

    # The family's earlier region is no longer read by the PGT calls or the coverage check.
    assert session.updates() == [_CLEARED]
    assert session.commits == 1
    assert issue is not None and issue.code == registration.MANIFEST_ROI_UNRESOLVED
    assert issue.message == (
        "The manifest's region of interest (NOT_A_GENE) was not found: no gene of the assembly has "
        "that name, and it is not a region such as chr7:117480025-117668665. The family's region "
        "of interest (CFTR, 7:117480025-117668665) was cleared, so no embryo is called and the "
        "NIPT coverage check reads no region: set it on the family page."
    )
    # Nothing goes into the family's metadata (ENG-6).
    assert "metadata" not in session.sql() and "unresolved_roi" not in session.sql()


def test_an_roi_the_import_does_not_find_for_a_family_without_one_says_so() -> None:
    session = _Session({"FROM genes": [], "FROM families": [dict.fromkeys(_EARLIER)]})

    issue = _apply(session, "NOT_A_GENE")

    assert session.updates() == [_CLEARED]
    assert issue is not None and issue.message.endswith(
        "The family has no region of interest, so no embryo is called and the NIPT coverage check "
        "reads no region: set it on the family page."
    )


def test_an_roi_of_a_family_in_no_project_with_an_assembly_is_not_looked_up_and_clears_it() -> None:
    session = _Session({"FROM families": [dict(_EARLIER)]})

    issue = _apply(session, "chr7:117480025-117668665", assembly_id=None)

    assert "FROM genes" not in session.sql()
    assert session.updates() == [_CLEARED]
    assert issue is not None and issue.message.startswith(
        "The manifest's region of interest (chr7:117480025-117668665) was not looked up: the "
        "family is in no project with an assembly. The family's region of interest (CFTR, "
    )


def test_an_roi_the_import_finds_is_the_familys_without_a_warning() -> None:
    session = _Session({"FROM genes": [{"hgnc_symbol": "CFTR", "gene_id": "ENST1", "chr": "chr7", "start": 10, "end": 20}]})

    issue = _apply(session, "cftr")

    assert issue is None
    assert session.updates() == [
        {
            "family_uuid": FAMILY_UUID,
            "query": "cftr",
            "label": "CFTR",
            "source": "gene",
            "assembly_id": ASSEMBLY,
            "chr": "7",
            "start": 10,
            "end": 20,
        }
    ]


def test_a_manifest_without_an_roi_leaves_the_familys_as_it_is() -> None:
    session = _Session()

    assert _apply(session, None) is None
    assert session.statements == []


def test_a_gene_is_looked_up_on_its_chromosome_before_an_alt_contig() -> None:
    # As the family page does (family_service._build_family_roi_payload): a gene with a longer
    # copy on an ALT contig is the ROI on its chromosome, where the embryos are called.
    session = _Session({"FROM genes": []})

    _apply(session, "NOT_A_GENE")

    [genes] = [sql for sql, _params in session.statements if "FROM genes" in sql]
    order_by = genes[genes.index("ORDER BY") :]
    assert order_by.index("(CASE WHEN chr ~* '^(chr)?([0-9]{1,2}|X|Y|M|MT)$' THEN 0 ELSE 1 END)") < order_by.index(
        '("end" - start) DESC'
    )


def test_the_import_and_the_family_page_look_a_gene_up_with_one_query() -> None:
    # ENG-23: both run roi_gene_lookup's, and resolve a gene to the same region.
    from backend.app.services import family_service

    gene = {"hgnc_symbol": "CFTR", "gene_id": "ENSG00000001626", "chr": "chr7", "start": 117480025, "end": 117668665}
    page, manifest = _Session({"FROM genes": [gene]}), _Session({"FROM genes": [gene]})

    on_the_page = asyncio.run(family_service._build_family_roi_payload(page, assembly_id=ASSEMBLY, query=" cftr "))  # type: ignore[arg-type]
    in_the_manifest = asyncio.run(registration._resolve_manifest_roi(manifest, assembly_id=ASSEMBLY, query="cftr"))  # type: ignore[arg-type]

    assert page.statements == manifest.statements
    assert on_the_page == in_the_manifest == {
        "query": "cftr",
        "label": "CFTR",
        "source": "gene",
        "assembly_id": ASSEMBLY,
        "chr": "7",
        "start": 117480025,
        "end": 117668665,
    }


def test_a_value_the_message_names_shows_its_control_characters() -> None:
    session = _Session({"FROM genes": [], "FROM families": []})

    issue = _apply(session, "GENE\x1b[2J")

    assert issue is not None and "(GENE\\x1b[2J)" in issue.message


# --- The validation and the dry run: looked up as the import will ----------------------------


def _check(session: _Session, roi: str | None, *, project_id: str | None = PROJECT, family_id: str | None = "FAM001") -> Any:
    return asyncio.run(
        registration.manifest_roi_issue(session, roi_query=roi, project_id=project_id, family_id=family_id)
    )


def test_the_validation_warns_of_an_roi_the_projects_assembly_does_not_have() -> None:
    session = _Session({"FROM projects": [{"assembly_id": ASSEMBLY}], "FROM genes": []})

    issue = _check(session, "NOT_A_GENE")

    assert issue is not None and issue.code == registration.MANIFEST_ROI_UNRESOLVED
    assert issue.message == (
        "The manifest's region of interest (NOT_A_GENE) was not found: no gene of the assembly has "
        "that name, and it is not a region such as chr7:117480025-117668665. The import leaves "
        "the family without a region of interest, so no embryo is called and the NIPT coverage "
        "check reads no region: correct the manifest's roi, or set the region on the family page "
        "after the import."
    )
    [(_sql, project), (_genes, genes)] = session.statements
    assert project == {"project_id": PROJECT} and genes == {"assembly_id": ASSEMBLY, "query": "NOT_A_GENE"}
    # A check writes nothing.
    assert session.updates() == [] and session.commits == 0


def test_the_validation_says_nothing_of_an_roi_it_finds() -> None:
    gene = {"hgnc_symbol": "CFTR", "gene_id": "ENST1", "chr": "7", "start": 1, "end": 2}
    assert _check(_Session({"FROM projects": [{"assembly_id": ASSEMBLY}], "FROM genes": [gene]}), "CFTR") is None
    assert _check(_Session({"FROM projects": [{"assembly_id": ASSEMBLY}]}), "chr7:1-2") is None
    assert _check(_Session(), None) is None


def test_without_a_project_the_validation_looks_the_roi_up_in_the_existing_familys_assembly() -> None:
    session = _Session(
        {
            "FROM families": [{"family_uuid": FAMILY_UUID, "metadata": {}, "sample_ids": ["S1"]}],
            "FROM family_projects": [{"assembly_id": ASSEMBLY, "assembly_name": "GRCh38"}],
            "FROM genes": [],
        }
    )

    issue = _check(session, "NOT_A_GENE", project_id=None)

    assert issue is not None and issue.code == registration.MANIFEST_ROI_UNRESOLVED
    assert [params for sql, params in session.statements if "FROM genes" in sql] == [
        {"assembly_id": ASSEMBLY, "query": "NOT_A_GENE"}
    ]


def test_with_no_assembly_to_look_it_up_in_the_validation_says_the_roi_was_not_checked() -> None:
    # A new family and no project, or a project id that names none.
    for project_id in (None, "not-a-uuid"):
        session = _Session({"FROM families": []})

        issue = _check(session, "CFTR", project_id=project_id)

        assert issue is not None and issue.code == registration.MANIFEST_ROI_NOT_CHECKED
        assert issue.message == (
            "The manifest's region of interest (CFTR) was not checked: it is looked up in the "
            "assembly of the family's project, and none is chosen. Choose the project the family "
            "is imported into."
        )
        assert "FROM projects" not in session.sql() and "FROM genes" not in session.sql()


def test_an_unknown_project_leaves_the_roi_unchecked() -> None:
    session = _Session({"FROM projects": []})

    issue = _check(session, "CFTR")

    assert issue is not None and issue.code == registration.MANIFEST_ROI_NOT_CHECKED


# --- The import job: the dry run and the import's result ---------------------------------------


@pytest.fixture
def package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    root = tmp_path / "FAM001"
    root.mkdir()
    (root / "family.ped").write_text("FAM001 S1 0 0 1 2\nFAM001 S2 0 0 2 1\n", encoding="utf-8")
    (root / "manifest.yaml").write_text(
        "schema_version: 1\nfamily_id: FAM001\nped: family.ped\nroi: NOT_A_GENE\n", encoding="utf-8"
    )

    async def no_warnings(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    @asynccontextmanager
    async def hold(_family_uuid: str, **_kwargs: Any):
        yield

    monkeypatch.setattr(package_import, "_existing_package_entity_warnings", no_warnings)
    monkeypatch.setattr(package_import, "hold_family_variant_writes", hold)
    for name in (
        "_mark_family_import_unfinished",
        "_end_import_failed_before_datasets",
        "_record_family_import_finished",
        "_end_family_import_unfinished",
    ):
        monkeypatch.setattr(package_import, name, nothing)
    return root


def _roi_warnings(warnings: list[FamilyImportValidationIssue]) -> list[str]:
    return [issue.code for issue in warnings if issue.code.startswith("manifest_roi_")]


@pytest.mark.asyncio
async def test_the_dry_run_warns_of_an_roi_the_import_will_not_find(package: Path) -> None:
    session = _Session({"FROM projects": [{"assembly_id": ASSEMBLY}], "FROM genes": []})

    result = await package_import.execute_family_package_import(
        session,  # type: ignore[arg-type]
        folder_path=package,
        manifest_text=kept_manifest(package),
        project_id=PROJECT,
        dry_run=True,
        user=admin_user(),
    )

    assert (result.completed, result.error) == (True, None)
    assert _roi_warnings(result.validation.warnings) == [registration.MANIFEST_ROI_UNRESOLVED]
    assert session.updates() == []


@pytest.mark.asyncio
async def test_the_import_puts_the_roi_warning_in_its_result(monkeypatch: pytest.MonkeyPatch, package: Path) -> None:
    warning = FamilyImportValidationIssue(code=registration.MANIFEST_ROI_UNRESOLVED, message="The ROI was cleared.")
    seen: list[list[FamilyImportValidationIssue]] = []

    async def register(_session: Any, *, validation: Any, warnings: list[FamilyImportValidationIssue], **_kwargs: Any) -> Any:
        warnings.append(warning)
        context = FamilyMetadataContext(
            family_uuid=FAMILY_UUID,
            family_id="FAM001",
            project_ids=[PROJECT],
            sample_rows=[],
            sample_uuid_to_name={},
            sample_name_to_uuid={},
            affected_sample_names=[],
            assembly_id=ASSEMBLY,
            assembly_name="GRCh38",
        )
        return context, False

    async def progress(validation: Any, *_args: Any) -> None:
        seen.append(list(validation.warnings) if validation is not None else [])

    monkeypatch.setattr(package_import, "_ensure_family_from_ped", register)
    monkeypatch.setattr(package_import, "_enabled_dataset_summaries", lambda _validation: [])

    result = await package_import.execute_family_package_import(
        _Session(),  # type: ignore[arg-type]
        folder_path=package,
        manifest_text=kept_manifest(package),
        project_id=PROJECT,
        dry_run=False,
        user=admin_user(),
        progress=progress,
    )

    assert result.completed is True and result.error is None
    assert result.validation.warnings[-1] == warning
    assert "The manifest's region of interest was not found: see the warnings." in result.logs
    # The job shows it from the progress after the family's registration on.
    assert warning not in seen[0] and warning in seen[-1]


