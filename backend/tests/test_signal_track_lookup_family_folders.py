"""The signal-track lookup finds a track only in its own family's folders (SEC-25).

The CNV caller's signal files (read depth, minor allele fraction, copy number) are served to
IGV from the path the import recorded under ``samples.metadata['signal_tracks']``, relative to
the family's package folder, ``<data>/families/<family>``. The path comes back from the
database as data, and an edited row can hold ``..`` or an absolute path. The lookup used to
join it to the package folder and check only that the result stayed inside the data
directory, so a recorded path such as ``../F2/cnv/S2.depth.bw``, or an absolute path into
family F2's folder, served F2's track under a sample of F1, the manifest listed it, and a
symlinked folder or file could lead into another family's folder; a family ID that is no
single folder name (``..``, ``../F2``) reached other families' folders too. This is what the
alignment lookup did before SEC-20 (#836, ``test_alignment_lookup_family_folders.py``).

Now the recorded path is joined to the package folder and normalised as a string, and dropped
before anything is looked up when it leaves that folder; one that stays must also have its
real path, symlinks resolved, inside one of the family's own folders, ``<data>/<family>`` and
``<data>/families/<family>``; a family ID that is no single folder name has no folder
(``core/family_folders.py``, shared with the alignment lookup). Through the app (``httpx`` on
the ASGI app, local storage, a data directory in ``tmp_path``) the manifest, GET and HEAD
answer the same way. The remote-mode layout probe is tested in
``test_signal_track_router.py``. All IDs and files are synthetic.
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
from backend.app.routers import signal_tracks
from backend.app.services.access_control import CurrentUser

KIND = "depth_bigwig"


def _content(family: str, sample: str) -> bytes:
    return f"{family} | {sample}".encode()


def _place(data: Path, relative: str, family: str, sample: str) -> Path:
    """A depth bigWig at ``relative`` under the data directory, holding ``<family> | <sample>``."""
    path = data / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_content(family, sample))
    return path


def _finds_nothing(family_id: str, relative_path: str) -> bool:
    """Whether the lookup finds no file for the recorded path, and the manifest would list no
    URL for it."""
    return (
        signal_tracks._resolve_track_path(family_id, relative_path) is None
        and signal_tracks._track_url(family_id, "S1", "hificnv", KIND, relative_path, None) is None
    )


def _found(family_id: str, relative_path: str) -> bytes | None:
    """What the file the lookup finds for the recorded path holds, or None."""
    found = signal_tracks._resolve_track_path(family_id, relative_path)
    return None if found is None else found.read_bytes()


@pytest.fixture()
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty data directory in ``tmp_path`` that the lookup reads, in local storage mode.
    Returned as its real path, the one the lookup builds its paths from."""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(signal_tracks, "DATA_DIR", root)
    monkeypatch.setattr(settings, "storage_backend", "local")
    return Path(os.path.realpath(root))


@pytest.fixture()
def tracks(data: Path) -> None:
    """Family F1's own track in its package, and family F2's in its package and in its flat
    folder."""
    _place(data, "families/F1/cnv/S1.depth.bw", "F1", "S1")
    _place(data, "families/F2/cnv/S2.depth.bw", "F2", "S2")
    _place(data, "F2/cnv/S2.depth.bw", "F2", "S2")


# --------------------------------------------------------------------------- #
# The recorded path
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("tracks")
def test_a_recorded_path_finds_the_track_in_the_familys_package(data: Path) -> None:
    own = data / "families" / "F1" / "cnv" / "S1.depth.bw"

    assert signal_tracks._resolve_track_path("F1", "cnv/S1.depth.bw") == own
    # So does an absolute path into the family's own package, and one whose `..` stays in it.
    assert _found("F1", str(own)) == _content("F1", "S1")
    assert _found("F1", "cnv/../cnv/S1.depth.bw") == _content("F1", "S1")
    assert signal_tracks._track_url("F1", "S1", "hificnv", KIND, "cnv/S1.depth.bw", None) == (
        "/signal-tracks/F1/S1/hificnv/depth_bigwig"
    )


def test_a_family_named_after_the_packages_folder_keeps_its_package(data: Path) -> None:
    _place(data, "families/families/cnv/S1.depth.bw", "families", "S1")

    assert _found("families", "cnv/S1.depth.bw") == _content("families", "S1")


# Recorded paths of a sample of family F1 that lead into family F2's folders, as an edited
# `samples.metadata` row could hold them.
CLIMBING_PATHS = {
    # families/F1/.. -> families/F2/cnv/S2.depth.bw
    "into F2's package": "../F2/cnv/S2.depth.bw",
    # families/F1/cnv/../.. -> families/F2/cnv/S2.depth.bw
    "through a folder into F2's package": "cnv/../../F2/cnv/S2.depth.bw",
    # families/F1/../.. -> <data>/F2/cnv/S2.depth.bw
    "into F2's flat folder": "../../F2/cnv/S2.depth.bw",
    # families/F1/../.. -> <data>/families/F2/cnv/S2.depth.bw
    "up to the data directory": "../../families/F2/cnv/S2.depth.bw",
    # an absolute path replaces the folder it is joined to
    "absolute, into F2's package": "<data>/families/F2/cnv/S2.depth.bw",
    "absolute, into F2's flat folder": "<data>/F2/cnv/S2.depth.bw",
}


@pytest.mark.usefixtures("tracks")
@pytest.mark.parametrize("relative_path", CLIMBING_PATHS.values(), ids=CLIMBING_PATHS.keys())
def test_a_recorded_path_into_another_family_finds_none_of_its_tracks(data: Path, relative_path: str) -> None:
    assert _finds_nothing("F1", relative_path.replace("<data>", str(data)))


@pytest.mark.usefixtures("tracks")
def test_a_recorded_path_out_of_the_data_directory_finds_nothing(data: Path, tmp_path: Path) -> None:
    _place(tmp_path, "outside/S1.depth.bw", "outside", "S1")

    for relative_path in ("../../../outside/S1.depth.bw", str(tmp_path / "outside" / "S1.depth.bw")):
        assert _finds_nothing("F1", relative_path)


@pytest.mark.usefixtures("tracks")
def test_a_nul_in_the_recorded_path_or_the_family_finds_nothing_and_raises_nothing(data: Path) -> None:
    assert _finds_nothing("F1", "cnv/S1.depth.bw\x00")
    assert _finds_nothing("F1\x00", "cnv/S1.depth.bw")


# Family IDs that are no single folder name, each with a recorded path that reached one of
# F2's tracks from it: `..` from the packages folder is the data directory, `../F2` is F2's
# flat folder, and an absolute family ID replaces the packages folder.
NO_FOLDER_FAMILY_IDS = {
    "empty": ("", "F2/cnv/S2.depth.bw"),
    "dot": (".", "F2/cnv/S2.depth.bw"),
    "dot dot": ("..", "families/F2/cnv/S2.depth.bw"),
    "dot dot, then a family": ("../F2", "cnv/S2.depth.bw"),
    "a family, then dot dot": ("F2/..", "F2/cnv/S2.depth.bw"),
    "a family, dot dot and another": ("F1/../F2", "cnv/S2.depth.bw"),
    "dot, then a family": ("./F2", "cnv/S2.depth.bw"),
    "trailing slash": ("F2/", "cnv/S2.depth.bw"),
    "absolute": ("<data>/families/F2", "cnv/S2.depth.bw"),
}


@pytest.mark.usefixtures("tracks")
@pytest.mark.parametrize("family_id, relative_path", NO_FOLDER_FAMILY_IDS.values(), ids=NO_FOLDER_FAMILY_IDS.keys())
def test_a_family_id_that_is_no_folder_name_has_no_track(data: Path, family_id: str, relative_path: str) -> None:
    assert _finds_nothing(family_id.replace("<data>", str(data)), relative_path)


# --------------------------------------------------------------------------- #
# Symlinks
# --------------------------------------------------------------------------- #

# (the link, where it leads), under the data directory. F1's sample records
# `cnv/S1.depth.bw`; F2's folders hold a file of that name.
LINKS_INTO_F2 = {
    "package folder": ("families/F1", "families/F2"),
    "folder in the package": ("families/F1/cnv", "families/F2/cnv"),
    "folder in the package, into F2's flat folder": ("families/F1/cnv", "F2/cnv"),
    "file": ("families/F1/cnv/S1.depth.bw", "families/F2/cnv/S1.depth.bw"),
}


def _link(data: Path, link: str, target: Path | str) -> None:
    (data / link).parent.mkdir(parents=True, exist_ok=True)
    (data / link).symlink_to(data / target)


@pytest.mark.parametrize("link, target", LINKS_INTO_F2.values(), ids=LINKS_INTO_F2.keys())
def test_a_symlink_that_leads_into_another_familys_folder_is_not_followed(data: Path, link: str, target: str) -> None:
    _place(data, "families/F2/cnv/S1.depth.bw", "F2", "S1")
    _place(data, "F2/cnv/S1.depth.bw", "F2", "S1")
    _link(data, link, target)

    assert _finds_nothing("F1", "cnv/S1.depth.bw")


def test_a_symlink_that_leads_out_of_the_data_directory_is_not_followed(data: Path, tmp_path: Path) -> None:
    _place(tmp_path, "outside/cnv/S1.depth.bw", "outside", "S1")
    _link(data, "families/F1/cnv", tmp_path / "outside" / "cnv")

    assert _finds_nothing("F1", "cnv/S1.depth.bw")


def test_a_symlink_between_the_familys_own_folders_is_followed(data: Path) -> None:
    # The package's folder a link to the family's flat folder, as for the alignments.
    _place(data, "F1/cnv/S1.depth.bw", "F1", "S1")
    _link(data, "families/F1/cnv", "F1/cnv")

    found = signal_tracks._resolve_track_path("F1", "cnv/S1.depth.bw")
    assert found is not None and found.read_bytes() == _content("F1", "S1")


# --------------------------------------------------------------------------- #
# Through the app
# --------------------------------------------------------------------------- #

ORIGIN = "http://coga.test"


@pytest.fixture()
def recorded(data: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    """The app over ``data`` with a signed-in viewer who may read every family. The families'
    members are the samples of the returned mapping, ``family -> sample -> recorded path``,
    and each sample's ``signal_tracks`` row records its path under the source ``hificnv``."""
    families: dict[str, dict[str, str]] = {}

    async def family_record(session: Any, family_id: str, user: Any) -> Any:
        if family_id not in families:
            raise HTTPException(status_code=404, detail="Family not found")
        return SimpleNamespace(members=[SimpleNamespace(sample_id=sample) for sample in families[family_id]])

    async def rows(session: Any, sample_ids: list[str]) -> list[tuple[str, dict[str, Any]]]:
        paths = {sample: path for members in families.values() for sample, path in members.items()}
        return [(sample, {"hificnv": {KIND: paths[sample]}}) for sample in sample_ids if sample in paths]

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

    monkeypatch.setattr(signal_tracks, "get_family_record", family_record)
    monkeypatch.setattr(signal_tracks, "_signal_track_rows", rows)
    monkeypatch.setattr(request_logging, "write_audit_log_event", keep_audit_row)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, no_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, a_viewer)
    return families


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


async def _manifest(client: httpx.AsyncClient, family_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/api/signal-tracks/{family_id}/manifest")
    assert response.status_code == 200, response.text
    return response.json()


async def _fetch(client: httpx.AsyncClient, family_id: str, sample_id: str) -> tuple[int, bytes, int]:
    """The GET's status and body, and the HEAD's status, of a sample's depth track."""
    url = f"/api{url_path('signal-tracks', family_id, sample_id, 'hificnv', KIND)}"
    got = await client.get(url)
    head = await client.head(url)
    return got.status_code, got.content, head.status_code


@pytest.mark.asyncio
async def test_the_manifest_lists_and_the_route_serves_only_the_familys_own_tracks(
    data: Path, recorded: dict[str, dict[str, str]]
) -> None:
    _place(data, "families/F2/cnv/S2.depth.bw", "F2", "S2")
    _place(data, "families/F1/cnv/S1.depth.bw", "F1", "S1")
    _link(data, "families/F1/linked", "families/F2/cnv")
    recorded["F2"] = {"S2": "cnv/S2.depth.bw"}
    recorded["F1"] = {
        "S1": "cnv/S1.depth.bw",
        # An edited row: `..` into F2's package, an absolute path to F2's track, and a
        # folder of F1's package that is a link into F2's.
        "S3": "../F2/cnv/S2.depth.bw",
        "S4": str(data / "families" / "F2" / "cnv" / "S2.depth.bw"),
        "S5": "linked/S2.depth.bw",
    }

    async with _client() as client:
        f1_entries = await _manifest(client, "F1")
        f2_entries = await _manifest(client, "F2")
        answered = {
            "F1 listed": [entry["sample_id"] for entry in f1_entries],
            "F2 listed": [entry["sample_id"] for entry in f2_entries],
            # Following the manifests' URLs (the first entry each).
            "F1 track": (await client.get(f"{ORIGIN}/api{f1_entries[0]['url']}")).content,
            "F2 track": (await client.get(f"{ORIGIN}/api{f2_entries[0]['url']}")).content,
        }
        for sample in ("S3", "S4", "S5"):
            answered[f"F1/{sample}"] = await _fetch(client, "F1", sample)

    missing = (404, b'{"detail":"Signal track file is missing"}', 404)
    assert answered == {
        "F1 listed": ["S1"],
        "F2 listed": ["S2"],
        "F1 track": _content("F1", "S1"),
        "F2 track": _content("F2", "S2"),
        "F1/S3": missing,
        "F1/S4": missing,
        "F1/S5": missing,
    }
