"""The QC-report lookup finds a sample's report only in its analysis's own package folder.

The sequencing-QC report is served from the path the import of an analysis recorded under
``samples.metadata['sequencing_qc'][<analysis>]['report']``, relative to the package folder that
analysis's import read (``families.metadata.package_imports[<analysis>].folder_path``), when that
folder lies below an import root. Nowhere else is looked: not the family's own folders
(``<data>/families/<family>``, ``<data>/<family>``), which could hold another analysis's report
of the same name, served under this analysis's name.

The recorded path and the folder come back from the database as data, and an edited row can hold
``..`` or an absolute path: the recorded path is joined to the folder and normalised as a string,
and dropped before anything is looked up when it leaves it; one that stays must also have its
real path inside that folder. A folder outside the import roots, the import root itself, a
relative one, or any folder when no root is configured, serves nothing. A report found nowhere is
answered 404. Through the app (``httpx`` on the ASGI app, a data directory in ``tmp_path``, its
packages folder the import root as deployed) the link and the report answer the same way. All IDs
and files are synthetic.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
import httpx
import pytest

from backend.app.core.config import settings
from backend.app.core.url_path import url_path
from backend.app.routers import family_qc_reports
from backend.tests._family_folder_app import ORIGIN, asgi_client, sign_in_a_viewer_who_reads


def _content(package: str, sample: str) -> bytes:
    return f"<html>{package} | {sample}</html>".encode()


def _place(data: Path, relative: str, package: str, sample: str) -> Path:
    """A QC report at ``relative`` under the data directory, naming ``<package> | <sample>``."""
    path = data / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_content(package, sample))
    return path


def _link(data: Path, link: str, target: Path | str) -> None:
    (data / link).parent.mkdir(parents=True, exist_ok=True)
    (data / link).symlink_to(data / target)


def _refused(relative_path: str, package_folder: object) -> int | None:
    """The status the lookup answers for the recorded path, or None when it finds a file."""
    try:
        family_qc_reports._resolve_report_file(relative_path, package_folder)
    except HTTPException as refused:
        return refused.status_code
    return None


def _found(relative_path: str, package_folder: object) -> bytes:
    """What the report the lookup finds for the recorded path holds."""
    return family_qc_reports._resolve_report_file(relative_path, package_folder).read_bytes()


@pytest.fixture()
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty data directory in ``tmp_path``, its packages folder the import root (as deployed:
    ``/data/families``). Returned as its real path, the one the lookup builds its paths from."""
    root = tmp_path / "data"
    (root / "families").mkdir(parents=True)
    monkeypatch.setattr(settings, "family_import_roots", [str(root / "families")])
    monkeypatch.setattr(settings, "secret_key", "x" * 40)
    return Path(os.path.realpath(root))


@pytest.fixture()
def reports(data: Path) -> None:
    """The short-read package RUN-SR of family F1, the long-read package F1 of the same family in
    the family's own package folder, and family F2's package RUN-F2, each with its report."""
    _place(data, "families/RUN-SR/qc/S1.html", "RUN-SR", "S1")
    _place(data, "families/F1/qc/S1.html", "F1", "S1")
    _place(data, "families/RUN-F2/qc/S2.html", "RUN-F2", "S2")


# --------------------------------------------------------------------------- #
# The recorded path, in the analysis's package folder
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("reports")
def test_a_recorded_path_finds_the_report_in_the_analysis_package(data: Path) -> None:
    package = str(data / "families" / "RUN-SR")
    own = data / "families" / "RUN-SR" / "qc" / "S1.html"

    assert family_qc_reports._resolve_report_file("qc/S1.html", package) == own
    assert _found(str(own), package) == _content("RUN-SR", "S1")
    assert _found("qc/../qc/S1.html", package) == _content("RUN-SR", "S1")
    assert _refused("qc/gone.html", package) == 404


@pytest.mark.usefixtures("reports")
def test_a_report_gone_from_its_package_is_never_looked_for_in_the_familys_folders(data: Path) -> None:
    # The short-read package's report is gone; the family's package folder <data>/families/F1,
    # the long-read package, holds a report of that name. Served, it would be the long-read
    # analysis's report under the short-read analysis's name.
    (data / "families" / "RUN-SR" / "qc" / "S1.html").unlink()
    _place(data, "F1/qc/S1.html", "flat F1", "S1")

    assert _refused("qc/S1.html", str(data / "families" / "RUN-SR")) == 404
    # Nor is any family folder read when the analysis's package folder is not known.
    assert _refused("qc/S1.html", None) == 404


# Recorded paths of F1's short-read package that lead into F1's other package or F2's, as an
# edited `samples.metadata` row could hold them.
CLIMBING_PATHS = {
    "into F2's package": "../RUN-F2/qc/S2.html",
    "through a folder into F2's package": "qc/../../RUN-F2/qc/S2.html",
    "into the family's other package": "../F1/qc/S1.html",
    "absolute, into F2's package": "<data>/families/RUN-F2/qc/S2.html",
    "absolute, into the family's other package": "<data>/families/F1/qc/S1.html",
}


@pytest.mark.usefixtures("reports")
@pytest.mark.parametrize("relative_path", CLIMBING_PATHS.values(), ids=CLIMBING_PATHS.keys())
def test_a_recorded_path_out_of_the_package_finds_no_report(data: Path, relative_path: str) -> None:
    assert _refused(relative_path.replace("<data>", str(data)), str(data / "families" / "RUN-SR")) == 404


# The analysis's package folder as a changed or missing families row could hold it.
UNUSABLE_PACKAGE_FOLDERS = {
    "none recorded": lambda data: None,
    "a relative path": lambda data: "families/RUN-SR",
    "outside the import roots": lambda data: str(data / "outside" / "RUN-SR"),
    "the import root itself": lambda data: str(data / "families"),
    "not a string": lambda data: 42,
}


@pytest.mark.usefixtures("reports")
@pytest.mark.parametrize("folder", UNUSABLE_PACKAGE_FOLDERS.values(), ids=UNUSABLE_PACKAGE_FOLDERS.keys())
def test_no_report_is_served_from_a_package_folder_outside_the_import_roots(data: Path, folder: Any) -> None:
    _place(data, "outside/RUN-SR/qc/S1.html", "outside", "S1")
    _place(data, "families/qc/S1.html", "root", "S1")

    assert _refused("qc/S1.html", folder(data)) == 404


@pytest.mark.usefixtures("reports")
def test_no_report_is_served_without_an_import_root(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty FAMILY_IMPORT_ROOTS lets an import read any folder (development), but nothing is
    # served from a folder no root lists.
    monkeypatch.setattr(settings, "family_import_roots", [])

    assert _refused("qc/S1.html", str(data / "families" / "RUN-SR")) == 404


# --------------------------------------------------------------------------- #
# Symlinks
# --------------------------------------------------------------------------- #

# (the link, where it leads), under the data directory. The short-read package records
# `qc/S1.html`; F2's package and the family's other package hold a file of that name.
LINKS_OUT_OF_THE_PACKAGE = {
    "folder in the package, into F2's package": ("families/RUN-SR/qc", "families/RUN-F2/qc"),
    "folder in the package, into the family's other package": ("families/RUN-SR/qc", "families/F1/qc"),
    "file": ("families/RUN-SR/qc/S1.html", "families/RUN-F2/qc/S1.html"),
}


@pytest.mark.parametrize("link, target", LINKS_OUT_OF_THE_PACKAGE.values(), ids=LINKS_OUT_OF_THE_PACKAGE.keys())
def test_a_symlink_that_leads_out_of_the_package_is_not_followed(data: Path, link: str, target: str) -> None:
    _place(data, "families/RUN-F2/qc/S1.html", "RUN-F2", "S1")
    _place(data, "families/F1/qc/S1.html", "F1", "S1")
    (data / "families" / "RUN-SR").mkdir(parents=True, exist_ok=True)
    _link(data, link, target)

    assert _refused("qc/S1.html", str(data / "families" / "RUN-SR")) == 404


def test_a_symlink_that_leads_out_of_the_data_directory_is_not_followed(data: Path, tmp_path: Path) -> None:
    _place(tmp_path, "outside/qc/S1.html", "outside", "S1")
    (data / "families" / "RUN-SR").mkdir(parents=True)
    _link(data, "families/RUN-SR/qc", tmp_path / "outside" / "qc")

    assert _refused("qc/S1.html", str(data / "families" / "RUN-SR")) == 404


# --------------------------------------------------------------------------- #
# Through the app
# --------------------------------------------------------------------------- #


@pytest.fixture()
def recorded(data: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, tuple[str, str | None]]]:
    """The app over ``data`` with a signed-in viewer who may read every family. The families'
    members are the samples of the returned mapping, ``family -> sample -> (recorded report path,
    the analysis's package folder)``."""
    families: dict[str, dict[str, tuple[str, str | None]]] = {}

    async def recorded_report(session: Any, *, family_id: str, sample_id: str, analysis: str) -> Any:
        report = families.get(family_id, {}).get(sample_id)
        if report is None:
            raise HTTPException(status_code=404, detail="No QC report is recorded for this sample")
        return family_qc_reports._RecordedReport(*report)

    sign_in_a_viewer_who_reads(monkeypatch, families, family_qc_reports)
    monkeypatch.setattr(family_qc_reports, "_recorded_report", recorded_report)
    return families


async def _open(client: httpx.AsyncClient, family_id: str, sample_id: str) -> tuple[int, int, bytes]:
    """The link request's status, then the report's status and body, fetched with a link token
    minted for the family and sample (as the link request would hand it out)."""
    link = await client.get(
        f"/api{url_path('families', family_id, 'qc-report', sample_id, 'link')}", params={"analysis": "short_read_wgs"}
    )
    token = family_qc_reports._qc_report_token(family_id, sample_id, "short_read_wgs")
    report = await client.get(
        f"/api{url_path('families', family_id, 'qc-report', sample_id)}",
        params={"analysis": "short_read_wgs", "token": token},
    )
    return link.status_code, report.status_code, report.content


@pytest.mark.asyncio
@pytest.mark.usefixtures("reports")
async def test_the_link_and_the_report_reach_only_the_analysis_own_report(
    data: Path, recorded: dict[str, dict[str, tuple[str, str | None]]]
) -> None:
    short_read = str(data / "families" / "RUN-SR")
    recorded["F2"] = {"S2": ("qc/S2.html", str(data / "families" / "RUN-F2"))}
    recorded["F1"] = {
        "S1": ("qc/S1.html", short_read),
        # A path into F2's package; a report gone from the package while the family's own folder
        # holds one of that name; a report without its analysis's package folder.
        "S3": ("../RUN-F2/qc/S2.html", short_read),
        "S4": ("qc/S4.html", short_read),
        "S5": ("qc/S1.html", None),
    }
    _place(data, "families/F1/qc/S4.html", "F1", "S4")

    async with asgi_client() as client:
        own = await _open(client, "F1", "S1")
        link = await client.get(
            f"/api{url_path('families', 'F1', 'qc-report', 'S1', 'link')}", params={"analysis": "short_read_wgs"}
        )
        followed = await client.get(f"{ORIGIN}/api{link.json()['url']}")
        refused = {sample: await _open(client, "F1", sample) for sample in ("S3", "S4", "S5")}
        f2 = await _open(client, "F2", "S2")

    assert own == (200, 200, _content("RUN-SR", "S1"))
    assert (followed.status_code, followed.content) == (200, _content("RUN-SR", "S1"))
    assert followed.headers["content-security-policy"] == "sandbox allow-scripts allow-popups"
    not_found = b'{"detail":"QC report file is no longer available"}'
    assert refused == {sample: (404, 404, not_found) for sample in refused}
    assert f2 == (200, 200, _content("RUN-F2", "S2"))
