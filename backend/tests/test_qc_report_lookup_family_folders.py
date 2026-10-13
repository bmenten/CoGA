"""The QC-report lookup finds a sample's report only in its own family's folders (SEC-25).

The sequencing-QC report is served from the path the import recorded under
``samples.metadata['sequencing_qc']['report']``, relative to the family's package folder
``<data>/families/<family>`` (or its flat folder ``<data>/<family>``), when that folder lies
under the configured import roots. The path comes back from the database as data, and an
edited row can hold ``..`` or an absolute path. The lookup resolved the family's folder,
symlinks and all, and kept the report inside the folder it resolved to, so a package folder
linked to another family's package served that family's report; a family named ``families``
took the packages folder for its flat folder and reached every package; and a family ID that
is no single folder name (``..``, ``../F2``) reached other families' folders when the data
directory lies under the import roots. A recorded path that left the folder was refused with
a 400 meant for manifest paths.

Now the folders are the family's own, as for the alignments and the signal tracks
(``core/family_folders.py``): the recorded path is joined to the folder and normalised as a
string, and dropped before anything is looked up when it leaves it; one that stays must also
have its real path inside that folder; a family ID that is no single folder name has no
folder; the folder must still lie under the import roots. A report found nowhere is answered
404, as before. Through the app (``httpx`` on the ASGI app, local storage, a data directory
in ``tmp_path``, its packages folder the import root as deployed) the link and the report
answer the same way. All IDs and files are synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import httpx
import pytest

from backend.app.core.config import settings
from backend.app.core.postgres import get_postgres_session
from backend.app.core.url_path import url_path
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import family_qc_reports
from backend.app.services.access_control import CurrentUser


def _content(family: str, sample: str) -> bytes:
    return f"<html>{family} | {sample}</html>".encode()


def _place(data: Path, relative: str, family: str, sample: str) -> Path:
    """A QC report at ``relative`` under the data directory, naming ``<family> | <sample>``."""
    path = data / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_content(family, sample))
    return path


def _link(data: Path, link: str, target: Path | str) -> None:
    (data / link).parent.mkdir(parents=True, exist_ok=True)
    (data / link).symlink_to(data / target)


def _refused(family_id: str, relative_path: str) -> int | None:
    """The status the lookup answers for the recorded path, or None when it finds a file."""
    try:
        family_qc_reports._resolve_report_file(family_id, relative_path)
    except HTTPException as refused:
        return refused.status_code
    return None


@pytest.fixture()
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty data directory in ``tmp_path`` that the lookup reads, in local storage mode,
    its packages folder the import root (as deployed: ``/data/families``). Returned as its
    real path, the one the lookup builds its paths from."""
    root = tmp_path / "data"
    (root / "families").mkdir(parents=True)
    monkeypatch.setattr(family_qc_reports, "DATA_DIR", root)
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "family_import_roots", [str(root / "families")])
    monkeypatch.setattr(settings, "secret_key", "x" * 40)
    return Path(os.path.realpath(root))


@pytest.fixture()
def reports(data: Path) -> None:
    """Family F1's own report in its package, and family F2's in its package and in its flat
    folder."""
    _place(data, "families/F1/qc/S1.html", "F1", "S1")
    _place(data, "families/F2/qc/S2.html", "F2", "S2")
    _place(data, "F2/qc/S2.html", "F2", "S2")


def _roots_hold_the_data_directory(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(data)])


def _found(family_id: str, relative_path: str) -> bytes:
    """What the report the lookup finds for the recorded path holds."""
    return family_qc_reports._resolve_report_file(family_id, relative_path).read_bytes()


# --------------------------------------------------------------------------- #
# The recorded path
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("reports")
def test_a_recorded_path_finds_the_report_in_the_familys_package(data: Path) -> None:
    own = data / "families" / "F1" / "qc" / "S1.html"

    assert family_qc_reports._resolve_report_file("F1", "qc/S1.html") == own
    assert _found("F1", str(own)) == _content("F1", "S1")
    assert _found("F1", "qc/../qc/S1.html") == _content("F1", "S1")
    assert _refused("F1", "qc/gone.html") == 404


def test_the_flat_folder_is_read_only_when_it_lies_under_the_import_roots(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flat = _place(data, "F1/qc/S1.html", "F1", "S1")

    assert _refused("F1", "qc/S1.html") == 404
    _roots_hold_the_data_directory(data, monkeypatch)
    assert family_qc_reports._resolve_report_file("F1", "qc/S1.html") == flat


# Recorded paths of a sample of family F1 that lead into family F2's folders, as an edited
# `samples.metadata` row could hold them. Each was refused with a 400 meant for manifest paths.
CLIMBING_PATHS = {
    "into F2's package": "../F2/qc/S2.html",
    "through a folder into F2's package": "qc/../../F2/qc/S2.html",
    "into F2's flat folder": "../../F2/qc/S2.html",
    "absolute, into F2's package": "<data>/families/F2/qc/S2.html",
    "absolute, into F2's flat folder": "<data>/F2/qc/S2.html",
}


@pytest.mark.usefixtures("reports")
@pytest.mark.parametrize("relative_path", CLIMBING_PATHS.values(), ids=CLIMBING_PATHS.keys())
def test_a_recorded_path_into_another_family_finds_no_report(
    data: Path, monkeypatch: pytest.MonkeyPatch, relative_path: str
) -> None:
    _roots_hold_the_data_directory(data, monkeypatch)

    assert _refused("F1", relative_path.replace("<data>", str(data))) == 404


def test_the_packages_folder_is_no_familys_flat_folder(data: Path) -> None:
    # A family named `families` took the packages folder, the import root itself, for its
    # flat folder, and read F2's report there.
    _place(data, "families/F2/qc/S2.html", "F2", "S2")
    _place(data, "families/families/qc/S1.html", "families", "S1")

    assert _refused("families", "F2/qc/S2.html") == 404
    # Its own package is read as before.
    assert _found("families", "qc/S1.html") == _content("families", "S1")


# Family IDs that are no single folder name, each with a recorded path that reached one of
# F2's reports from it when the data directory lies under the import roots.
NO_FOLDER_FAMILY_IDS = {
    "empty": ("", "F2/qc/S2.html"),
    "dot": (".", "F2/qc/S2.html"),
    "dot dot": ("..", "families/F2/qc/S2.html"),
    "dot dot, then a family": ("../F2", "qc/S2.html"),
    "a family, then dot dot": ("F2/..", "F2/qc/S2.html"),
    "a family, dot dot and another": ("F1/../F2", "qc/S2.html"),
    "absolute": ("<data>/families/F2", "qc/S2.html"),
}


@pytest.mark.usefixtures("reports")
@pytest.mark.parametrize("family_id, relative_path", NO_FOLDER_FAMILY_IDS.values(), ids=NO_FOLDER_FAMILY_IDS.keys())
def test_a_family_id_that_is_no_folder_name_has_no_report(
    data: Path, monkeypatch: pytest.MonkeyPatch, family_id: str, relative_path: str
) -> None:
    _roots_hold_the_data_directory(data, monkeypatch)

    assert _refused(family_id.replace("<data>", str(data)), relative_path) == 404


# --------------------------------------------------------------------------- #
# Symlinks
# --------------------------------------------------------------------------- #

# (the link, where it leads), under the data directory. F1's sample records `qc/S1.html`;
# F2's folders hold a file of that name. The package folder's link served F2's report; the
# others were refused with a 400.
LINKS_INTO_F2 = {
    "package folder": ("families/F1", "families/F2"),
    "folder in the package": ("families/F1/qc", "families/F2/qc"),
    "folder in the package, into F2's flat folder": ("families/F1/qc", "F2/qc"),
    "file": ("families/F1/qc/S1.html", "families/F2/qc/S1.html"),
}


@pytest.mark.parametrize("link, target", LINKS_INTO_F2.values(), ids=LINKS_INTO_F2.keys())
def test_a_symlink_that_leads_into_another_familys_folder_is_not_followed(data: Path, link: str, target: str) -> None:
    _place(data, "families/F2/qc/S1.html", "F2", "S1")
    _place(data, "F2/qc/S1.html", "F2", "S1")
    _link(data, link, target)

    assert _refused("F1", "qc/S1.html") == 404


def test_a_symlink_that_leads_out_of_the_data_directory_is_not_followed(data: Path, tmp_path: Path) -> None:
    _place(tmp_path, "outside/qc/S1.html", "outside", "S1")
    _link(data, "families/F1/qc", tmp_path / "outside" / "qc")

    assert _refused("F1", "qc/S1.html") == 404


# --------------------------------------------------------------------------- #
# Through the app
# --------------------------------------------------------------------------- #

ORIGIN = "http://coga.test"


@pytest.fixture()
def recorded(data: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    """The app over ``data`` with a signed-in viewer who may read every family. The families'
    members are the samples of the returned mapping, ``family -> sample -> recorded report
    path``."""
    families: dict[str, dict[str, str]] = {}

    async def family_record(session: Any, family_id: str, user: Any) -> Any:
        if family_id not in families:
            raise HTTPException(status_code=404, detail="Family not found")
        return SimpleNamespace(members=[SimpleNamespace(sample_id=sample) for sample in families[family_id]])

    async def recorded_report(session: Any, *, family_id: str, sample_id: str) -> str:
        report = families.get(family_id, {}).get(sample_id)
        if report is None:
            raise HTTPException(status_code=404, detail="No QC report is recorded for this sample")
        return report

    async def no_session():
        yield None

    async def a_viewer() -> CurrentUser:
        return CurrentUser(
            id="user-viewer",
            username="viewer@example.com",
            email="viewer@example.com",
            role="viewer",
            created_at=datetime.now(timezone.utc),
        )

    async def keep_audit_row(payload: Any) -> None:
        return None

    monkeypatch.setattr(family_qc_reports, "get_family_record", family_record)
    monkeypatch.setattr(family_qc_reports, "_recorded_report", recorded_report)
    monkeypatch.setattr(request_logging, "write_audit_log_event", keep_audit_row)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, no_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, a_viewer)
    return families


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


async def _open(client: httpx.AsyncClient, family_id: str, sample_id: str) -> tuple[int, int, bytes]:
    """The link request's status, then the report's status and body, fetched with a link token
    minted for the family and sample (as the link request would hand it out)."""
    link = await client.get(f"/api{url_path('families', family_id, 'qc-report', sample_id, 'link')}")
    token = family_qc_reports._qc_report_token(family_id, sample_id)
    report = await client.get(
        f"/api{url_path('families', family_id, 'qc-report', sample_id)}", params={"token": token}
    )
    return link.status_code, report.status_code, report.content


@pytest.mark.asyncio
async def test_the_link_and_the_report_reach_only_the_familys_own_report(
    data: Path, recorded: dict[str, dict[str, str]]
) -> None:
    _place(data, "families/F2/qc/S2.html", "F2", "S2")
    _place(data, "families/F1/qc/S1.html", "F1", "S1")
    _link(data, "families/F3", "families/F2")
    recorded["F2"] = {"S2": "qc/S2.html"}
    recorded["F1"] = {"S1": "qc/S1.html", "S4": "../F2/qc/S2.html"}
    # F3's package folder is a link to F2's; the family named `families` records a path
    # into F2's package from the packages folder.
    recorded["F3"] = {"S3": "qc/S2.html"}
    recorded["families"] = {"S5": "F2/qc/S2.html"}

    async with _client() as client:
        own = await _open(client, "F1", "S1")
        link = await client.get(f"/api{url_path('families', 'F1', 'qc-report', 'S1', 'link')}")
        followed = await client.get(f"{ORIGIN}/api{link.json()['url']}")
        refused = {
            f"{family}/{sample}": await _open(client, family, sample)
            for family, sample in (("F1", "S4"), ("F3", "S3"), ("families", "S5"))
        }
        f2 = await _open(client, "F2", "S2")

    assert own == (200, 200, _content("F1", "S1"))
    assert (followed.status_code, followed.content) == (200, _content("F1", "S1"))
    assert followed.headers["content-security-policy"] == "sandbox allow-scripts allow-popups"
    not_found = b'{"detail":"QC report file is no longer available"}'
    assert refused == {case: (404, 404, not_found) for case in refused}
    assert f2 == (200, 200, _content("F2", "S2"))
