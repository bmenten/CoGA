"""The signal-track lookup finds a sample's track only in its analysis's own package folder.

The CNV caller's signal files (read depth, minor allele fraction, copy number) are served to IGV
from the path the import of an analysis recorded under ``samples.metadata['signal_tracks']``,
relative to the package folder that analysis's import read: the record names the analysis
(``package``), and the family's package record of it the folder
(``families.metadata.package_imports[<analysis>].folder_path``), when that folder lies below an
import root. Nowhere else is looked: not the family's own folders (``<data>/families/<family>``,
``<data>/<family>``), which could hold another analysis's file of the same name. A record that
names no analysis, or one the family holds no package record of, finds no file.

The recorded path and the folder come back from the database as data, and an edited row can hold
``..`` or an absolute path: the recorded path is joined to the folder and normalised as a string,
and dropped before anything is looked up when it leaves it; one that stays must also have its
real path inside that folder. A folder outside the import roots, the import root itself or a
relative one serves nothing. Through the app (``httpx`` on the ASGI app, a data directory in
``tmp_path``, its packages folder the import root as deployed) the manifest lists only the tracks
found, and GET and HEAD answer 404 for the others. All IDs and files are synthetic.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.app.core.config import settings
from backend.app.core.url_path import url_path
from backend.app.routers import signal_tracks
from backend.tests._family_folder_app import ORIGIN, asgi_client, sign_in_a_viewer_who_reads

KIND = "depth_bigwig"


def _content(package: str, sample: str) -> bytes:
    return f"{package} | {sample}".encode()


def _place(data: Path, relative: str, package: str, sample: str) -> Path:
    """A depth bigWig at ``relative`` under the data directory, holding ``<package> | <sample>``."""
    path = data / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_content(package, sample))
    return path


def _link(data: Path, link: str, target: Path | str) -> None:
    (data / link).parent.mkdir(parents=True, exist_ok=True)
    (data / link).symlink_to(data / target)


def _finds_nothing(relative_path: str, package_folder: Any) -> bool:
    """Whether the lookup finds no file for the recorded path, and the manifest would list no
    URL for it."""
    return (
        signal_tracks._resolve_track_path(relative_path, package_folder) is None
        and signal_tracks._track_url("F1", "S1", "hificnv", KIND, relative_path, package_folder) is None
    )


@pytest.fixture()
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty data directory in ``tmp_path``, its packages folder the import root (as deployed:
    ``/data/families``). Returned as its real path, the one the lookup builds its paths from."""
    root = tmp_path / "data"
    (root / "families").mkdir(parents=True)
    monkeypatch.setattr(settings, "family_import_roots", [str(root / "families")])
    return Path(os.path.realpath(root))


@pytest.fixture()
def tracks(data: Path) -> None:
    """The long-read package RUN-LR of family F1, the short-read package F1 of the same family in
    the family's own package folder, and family F2's package RUN-F2, each with its track."""
    _place(data, "families/RUN-LR/cnv/S1.depth.bw", "RUN-LR", "S1")
    _place(data, "families/F1/cnv/S1.depth.bw", "F1", "S1")
    _place(data, "families/RUN-F2/cnv/S2.depth.bw", "RUN-F2", "S2")


# --------------------------------------------------------------------------- #
# The recorded path, in the analysis's package folder
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("tracks")
def test_a_recorded_path_finds_the_track_in_the_analysis_package(data: Path) -> None:
    package = str(data / "families" / "RUN-LR")
    own = data / "families" / "RUN-LR" / "cnv" / "S1.depth.bw"

    assert signal_tracks._resolve_track_path("cnv/S1.depth.bw", package) == own
    # So does an absolute path into the package, and one whose `..` stays in it.
    assert signal_tracks._resolve_track_path(str(own), package) == own
    assert signal_tracks._resolve_track_path("cnv/../cnv/S1.depth.bw", package) == own
    assert signal_tracks._track_url("F1", "S1", "hificnv", KIND, "cnv/S1.depth.bw", package) == (
        "/signal-tracks/F1/S1/hificnv/depth_bigwig"
    )
    assert _finds_nothing("cnv/gone.bw", package)
    assert _finds_nothing("", package)


@pytest.mark.usefixtures("tracks")
def test_a_track_whose_analysis_has_no_package_folder_is_never_looked_for_in_the_familys_folders(data: Path) -> None:
    # The family's package folder <data>/families/F1 holds a track at the recorded path, another
    # analysis's: a record that names no package folder finds nothing, nor does one gone from its
    # analysis's package.
    _place(data, "F1/cnv/S1.depth.bw", "flat F1", "S1")
    assert _finds_nothing("cnv/S1.depth.bw", None)

    (data / "families" / "RUN-LR" / "cnv" / "S1.depth.bw").unlink()
    assert _finds_nothing("cnv/S1.depth.bw", str(data / "families" / "RUN-LR"))


# Recorded paths of F1's long-read package that lead into F1's other package or F2's, as an
# edited `samples.metadata` row could hold them.
CLIMBING_PATHS = {
    "into F2's package": "../RUN-F2/cnv/S2.depth.bw",
    "through a folder into F2's package": "cnv/../../RUN-F2/cnv/S2.depth.bw",
    "into the family's other package": "../F1/cnv/S1.depth.bw",
    "up to the data directory": "../../F1/cnv/S1.depth.bw",
    "absolute, into F2's package": "<data>/families/RUN-F2/cnv/S2.depth.bw",
    "absolute, into the family's other package": "<data>/families/F1/cnv/S1.depth.bw",
}


@pytest.mark.usefixtures("tracks")
@pytest.mark.parametrize("relative_path", CLIMBING_PATHS.values(), ids=CLIMBING_PATHS.keys())
def test_a_recorded_path_out_of_the_package_finds_no_track(data: Path, relative_path: str) -> None:
    _place(data, "F1/cnv/S1.depth.bw", "flat F1", "S1")

    assert _finds_nothing(relative_path.replace("<data>", str(data)), str(data / "families" / "RUN-LR"))


@pytest.mark.usefixtures("tracks")
def test_a_nul_in_the_recorded_path_or_the_folder_finds_nothing_and_raises_nothing(data: Path) -> None:
    package = str(data / "families" / "RUN-LR")

    assert _finds_nothing("cnv/S1.depth.bw\x00", package)
    assert _finds_nothing("cnv/S1.depth.bw", package + "\x00")


# The analysis's package folder as a changed families row could hold it.
UNUSABLE_PACKAGE_FOLDERS = {
    "a relative path": lambda data: "families/RUN-LR",
    "outside the import roots": lambda data: str(data / "outside" / "RUN-LR"),
    "the import root itself": lambda data: str(data / "families"),
}


@pytest.mark.usefixtures("tracks")
@pytest.mark.parametrize("folder", UNUSABLE_PACKAGE_FOLDERS.values(), ids=UNUSABLE_PACKAGE_FOLDERS.keys())
def test_no_track_is_served_from_a_package_folder_outside_the_import_roots(data: Path, folder: Any) -> None:
    _place(data, "outside/RUN-LR/cnv/S1.depth.bw", "outside", "S1")
    _place(data, "families/cnv/S1.depth.bw", "root", "S1")

    assert _finds_nothing("cnv/S1.depth.bw", folder(data))


# --------------------------------------------------------------------------- #
# Symlinks
# --------------------------------------------------------------------------- #

# (the link, where it leads), under the data directory. The long-read package records
# `cnv/S1.depth.bw`; F2's package, the family's other package and its flat folder hold a file of
# that name.
LINKS_OUT_OF_THE_PACKAGE = {
    "folder in the package, into F2's package": ("families/RUN-LR/cnv", "families/RUN-F2/cnv"),
    "folder in the package, into the family's other package": ("families/RUN-LR/cnv", "families/F1/cnv"),
    "folder in the package, into the family's flat folder": ("families/RUN-LR/cnv", "F1/cnv"),
    "file": ("families/RUN-LR/cnv/S1.depth.bw", "families/RUN-F2/cnv/S1.depth.bw"),
}


@pytest.mark.parametrize("link, target", LINKS_OUT_OF_THE_PACKAGE.values(), ids=LINKS_OUT_OF_THE_PACKAGE.keys())
def test_a_symlink_that_leads_out_of_the_package_is_not_followed(data: Path, link: str, target: str) -> None:
    _place(data, "families/RUN-F2/cnv/S1.depth.bw", "RUN-F2", "S1")
    _place(data, "families/F1/cnv/S1.depth.bw", "F1", "S1")
    _place(data, "F1/cnv/S1.depth.bw", "flat F1", "S1")
    (data / "families" / "RUN-LR").mkdir(parents=True, exist_ok=True)
    _link(data, link, target)

    assert _finds_nothing("cnv/S1.depth.bw", str(data / "families" / "RUN-LR"))


def test_a_symlink_that_leads_out_of_the_data_directory_is_not_followed(data: Path, tmp_path: Path) -> None:
    _place(tmp_path, "outside/cnv/S1.depth.bw", "outside", "S1")
    (data / "families" / "RUN-LR").mkdir(parents=True)
    _link(data, "families/RUN-LR/cnv", tmp_path / "outside" / "cnv")

    assert _finds_nothing("cnv/S1.depth.bw", str(data / "families" / "RUN-LR"))


# --------------------------------------------------------------------------- #
# Through the app
# --------------------------------------------------------------------------- #

# The analysis whose import recorded the tracks.
ANALYSIS = "long_read_wgs"


@pytest.fixture()
def recorded(data: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, tuple[str, str | None]]]:
    """The app over ``data`` with a signed-in viewer who may read every family. The families'
    members are the samples of the returned mapping, ``family -> sample -> (recorded path, the
    analysis's package folder)``: each sample's ``signal_tracks`` row records its path under the
    source ``hificnv``, naming the analysis, and the family's package record of it gives the folder
    (none recorded: no package record)."""
    families: dict[str, dict[str, tuple[str, str | None]]] = {}

    async def rows(session: Any, sample_ids: list[str]) -> list[tuple[str, dict[str, Any], Any]]:
        tracks = {sample: track for members in families.values() for sample, track in members.items()}
        return [
            (
                sample,
                {"hificnv": {KIND: tracks[sample][0], "package": ANALYSIS}},
                {ANALYSIS: {"folder_path": tracks[sample][1]}} if tracks[sample][1] is not None else {},
            )
            for sample in sample_ids
            if sample in tracks
        ]

    sign_in_a_viewer_who_reads(monkeypatch, families, signal_tracks)
    monkeypatch.setattr(signal_tracks, "_signal_track_rows", rows)
    return families


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
@pytest.mark.usefixtures("tracks")
async def test_the_manifest_lists_and_the_route_serves_only_the_analysis_own_tracks(
    data: Path, recorded: dict[str, dict[str, tuple[str, str | None]]]
) -> None:
    long_read = str(data / "families" / "RUN-LR")
    _link(data, "families/RUN-LR/linked", "families/RUN-F2/cnv")
    recorded["F2"] = {"S2": ("cnv/S2.depth.bw", str(data / "families" / "RUN-F2"))}
    recorded["F1"] = {
        "S1": ("cnv/S1.depth.bw", long_read),
        # A path into F2's package, an absolute path to F2's track, a folder of the package that
        # is a link into F2's, and a track without its analysis's package folder while the
        # family's own folder holds one of that name.
        "S3": ("../RUN-F2/cnv/S2.depth.bw", long_read),
        "S4": (str(data / "families" / "RUN-F2" / "cnv" / "S2.depth.bw"), long_read),
        "S5": ("linked/S2.depth.bw", long_read),
        "S6": ("cnv/S1.depth.bw", None),
    }

    async with asgi_client() as client:
        f1_entries = await _manifest(client, "F1")
        f2_entries = await _manifest(client, "F2")
        answered: dict[str, Any] = {
            "F1 listed": [entry["sample_id"] for entry in f1_entries],
            "F2 listed": [entry["sample_id"] for entry in f2_entries],
            # Following the manifests' URLs (the first entry each).
            "F1 track": (await client.get(f"{ORIGIN}/api{f1_entries[0]['url']}")).content,
            "F2 track": (await client.get(f"{ORIGIN}/api{f2_entries[0]['url']}")).content,
        }
        for sample in ("S3", "S4", "S5", "S6"):
            answered[f"F1/{sample}"] = await _fetch(client, "F1", sample)

    missing = (404, b'{"detail":"Signal track file is missing"}', 404)
    assert answered == {
        "F1 listed": ["S1"],
        "F2 listed": ["S2"],
        "F1 track": _content("RUN-LR", "S1"),
        "F2 track": _content("RUN-F2", "S2"),
        "F1/S3": missing,
        "F1/S4": missing,
        "F1/S5": missing,
        "F1/S6": missing,
    }
