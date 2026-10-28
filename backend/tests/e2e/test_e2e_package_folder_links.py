"""A package folder that is a symbolic link is followed, and reported, over the API (E2E).

The package list and Discover follow a package's ``ped/`` folder and its long-read folders
when one is a symbolic link, wherever it leads: Discover proposes a member from a linked
per-sample folder, though what it holds may be another family's. The owner chose a warning
over a refusal (SEC-27, 2026-10-28). Over the real API (an in-process ``httpx.ASGITransport``
client that answers an app failure with 500, see test_e2e_api_contract.py) and Postgres, with
synthetic packages in an import root of the test's own:

* FAMLINK1, a long-read couple without a PED whose father's SNV folder is a symbolic link to
  his folder in another package, FAMLINK2: the package list lists FAMLINK1, Discover still
  proposes the father from the linked folder, and says that ``snv/FATHERL1`` is a symbolic
  link to ``FAMLINK2/snv/FATHERL1`` in the import folders (``package_folder_link``); once its
  manifest is written, Validate and a dry-run import job report the same warning, and pass;
* FAMLINK3, a trio whose ``ped/`` folder is a symbolic link to a folder outside the import
  root: Validate passes and says the folder leads outside the import folders, naming no
  path there;
* no message names an absolute path, and nothing is written.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
import yaml

pytestmark = pytest.mark.integration

_WORKER = "e2e-package-folder-links-worker"


def _long_read_sample(package: Path, sample_id: str, karyotype: str) -> None:
    """A long-read sample's per-sample folders: an annotated SNV VCF, and a TRGT VCF whose
    header names the karyotype, each named after the sample."""
    snv = package / "snv" / sample_id / "annotation" / f"{sample_id}_annot.vcf.gz"
    trgt = package / "repeats" / sample_id / f"{sample_id}_tr.vcf.gz"
    for path, header in (
        (snv, "##fileformat=VCFv4.2\n"),
        (trgt, f"##fileformat=VCFv4.2\n##trgtCommand=trgt genotype --karyotype {karyotype}\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt") as handle:
            handle.write(f"{header}#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample_id}\n")


def _cap(resp: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # a 500 answers in text
        out["json"] = None
    return out


async def _counts() -> dict[str, int]:
    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        row = (
            await session.execute(
                text("SELECT (SELECT count(*) FROM families) AS families, (SELECT count(*) FROM samples) AS samples")
            )
        ).mappings().one()
    return dict(row)


async def _run_dry_run_job(ac: Any, folder: Path, project_id: str) -> dict[str, Any]:
    """Queue a dry run of ``folder`` as the page does, claim it as the worker does, run it."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import family_package_import

    resp = await ac.post(
        "/api/family-imports",
        json={"folder_path": str(folder), "project_id": project_id, "dry_run": True, "conflict_mode": "cancel"},
    )
    assert resp.status_code == 200, resp.text
    job_id = str(resp.json()["_id"])
    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE family_import_jobs SET status = 'validating', worker_id = :w, "
                "started_at = now(), heartbeat_at = now() WHERE id = CAST(:j AS uuid) AND status = 'queued'"
            ),
            {"j": job_id, "w": _WORKER},
        )
        await session.commit()
    await family_package_import.run_family_import_job(job_id=job_id, worker_id=_WORKER)
    async with get_postgres_sessionmaker()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, error, validation_errors, validation_warnings "
                    "FROM family_import_jobs WHERE id = CAST(:j AS uuid)"
                ),
                {"j": job_id},
            )
        ).mappings().one()
    return dict(row)


async def _collect(base: Path) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    imports = base / "imports"
    couple = imports / "FAMLINK1"
    _long_read_sample(couple, "MOTHERL1", "XX")
    other = imports / "FAMLINK2"
    _long_read_sample(other, "FATHERL1", "XY")
    (couple / "snv" / "FATHERL1").symlink_to(other / "snv" / "FATHERL1")

    trio = imports / "FAMLINK3"
    trio.mkdir(parents=True)
    ped = "FAMLINK3 FATHERL3 0 0 1 1\nFAMLINK3 MOTHERL3 0 0 2 1\nFAMLINK3 CHILDL3 FATHERL3 MOTHERL3 1 2\n"
    (trio / "family.ped").write_text(ped, encoding="utf-8")
    (trio / "manifest.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "family_id": "FAMLINK3", "ped": "family.ped", "datasets": {}}),
        encoding="utf-8",
    )
    outside_peds = base / "outside" / "peds"
    outside_peds.mkdir(parents=True)
    (outside_peds / "FAMLINK3.ped").write_text(ped, encoding="utf-8")
    (trio / "ped").symlink_to(outside_peds)

    out: dict[str, Any] = {
        "before": await _counts(),
        "bases": sorted({str(base), str(base.resolve())}),
        "couple": str(couple.resolve()),
    }
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"

        out["packages"] = _cap(await ac.get("/api/family-imports/packages"))
        out["discover"] = _cap(await ac.post("/api/family-imports/manifest/discover", json={"folder_path": str(couple)}))
        manifest_yaml = (out["discover"]["json"] or {}).get("manifest_yaml") or ""
        out["write"] = _cap(
            await ac.post(
                "/api/family-imports/manifest/write",
                json={"folder_path": str(couple), "manifest_yaml": manifest_yaml, "overwrite": False},
            )
        )
        out["validate"] = _cap(await ac.post("/api/family-imports/validate", json={"folder_path": str(couple)}))
        out["job"] = await _run_dry_run_job(ac, couple, project_id)
        out["validate_ped"] = _cap(await ac.post("/api/family-imports/validate", json={"folder_path": str(trio)}))
    out["after"] = await _counts()
    return out


@pytest.fixture(scope="module")
def snap(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("package_folder_links")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(base / "imports")])
    request.addfinalizer(mp.undo)
    return _harness.run_async(lambda: _collect(base))


def _links(issues: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(issue["path"], issue["message"]) for issue in issues if issue["code"] == "package_folder_link"]


def _warning(folder: str, leads_to: str) -> str:
    return (
        f"Folder '{folder}' of the package is a symbolic link to {leads_to}: check that what it "
        "holds belongs to this family."
    )


def test_the_package_list_still_lists_the_package(snap: dict[str, Any]) -> None:
    listed = snap["packages"]
    assert listed["status"] == 200, listed["text"]
    assert {"FAMLINK1", "FAMLINK2", "FAMLINK3"} <= {item["name"] for item in listed["json"]}


def test_discover_still_proposes_a_member_from_a_linked_folder_and_reports_the_link(snap: dict[str, Any]) -> None:
    discover = snap["discover"]
    assert discover["status"] == 200, discover["text"]
    members = yaml.safe_load(discover["json"]["manifest_yaml"])["family"]["add_members"]
    assert [member["sample_id"] for member in members] == ["FATHERL1", "MOTHERL1"]
    assert _links(discover["json"]["warnings"]) == [
        (
            f"{snap['couple']}/snv/FATHERL1",
            _warning("snv/FATHERL1", "'FAMLINK2/snv/FATHERL1' in the import folders"),
        )
    ]


def test_validate_and_a_dry_run_report_the_link_and_pass(snap: dict[str, Any]) -> None:
    assert snap["write"]["status"] == 200, snap["write"]["text"]
    expected = [
        (
            f"{snap['couple']}/snv/FATHERL1",
            _warning("snv/FATHERL1", "'FAMLINK2/snv/FATHERL1' in the import folders"),
        )
    ]
    validate = snap["validate"]
    assert validate["status"] == 200, validate["text"]
    assert validate["json"]["valid"] is True, validate["json"]["errors"]
    assert _links(validate["json"]["warnings"]) == expected
    job = snap["job"]
    assert (job["status"], job["error"], job["validation_errors"]) == ("completed", None, []), job
    assert _links(job["validation_warnings"]) == expected


def test_a_ped_folder_linked_out_of_the_import_root_is_reported_without_its_path(snap: dict[str, Any]) -> None:
    validate = snap["validate_ped"]
    assert validate["status"] == 200, validate["text"]
    assert validate["json"]["valid"] is True, validate["json"]["errors"]
    [(path, message)] = _links(validate["json"]["warnings"])
    assert path.endswith("/FAMLINK3/ped")
    assert message == _warning("ped", "a folder outside the import folders")


def test_no_message_names_an_absolute_path_and_nothing_is_written(snap: dict[str, Any]) -> None:
    def naming_a_path(issues: list[dict[str, Any]]) -> list[str]:
        return [issue["message"] for issue in issues if any(base in issue["message"] for base in snap["bases"])]

    for name in ("discover", "validate", "validate_ped"):
        assert naming_a_path([*snap[name]["json"]["errors"], *snap[name]["json"]["warnings"]]) == [], name
    assert naming_a_path(snap["job"]["validation_warnings"]) == []
    assert snap["after"] == snap["before"]
