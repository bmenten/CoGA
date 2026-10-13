"""The alignment lookup finds a sample's reads only in its own family's folders (SEC-20, SEC-13).

In local storage mode the CRAM endpoints look for ``<sample>.cram`` (``.crai``, ``.bam``,
``.bai``) under five layouts in the family's two folders, ``<data>/<family>`` and
``<data>/families/<family>``. The family and sample IDs come from the request, and the ID rule
lets them hold ``/`` and ``..`` (SEC-15). The lookup used to join them into a path and check
only that the path stayed inside the data directory, so a sample ID ``../F2/S2`` of family F1
found family F2's reads and the alignment manifest listed them, a family ID such as
``families/F2`` reached F2's package, and a symlinked folder could lead into another family's
folder. When every layout was refused, it fell back to ``<data>/<family>/<file>`` unchecked,
and the GET served that file even when it lay outside the data directory.

Now each path is joined and normalised as a string and dropped, before anything is looked up,
when it leaves the family's folder; a family ID that is no single folder name has no folder;
a path that stays must also have its real path, symlinks resolved, inside one of the family's
folders; and nothing outside the layouts is looked at. Through the app (``httpx`` on the ASGI
app, local storage, a data directory in ``tmp_path``) the manifest, GET and HEAD answer the
same way. All IDs and files are synthetic.
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
from backend.app.routers import cram
from backend.app.services.access_control import CurrentUser

# The five layouts, under the data directory, in the order they are probed.
LAYOUTS = (
    "{family}/{sample}.{ext}",
    "families/{family}/bams/{sample}.{ext}",
    "families/{family}/alignments/{sample}.{ext}",
    "families/{family}/{sample}.{ext}",
    "{family}/bams/{sample}.{ext}",
)
INDEX = {"cram": ".crai", "bam": ".bai"}


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _content(family: str, sample: str) -> bytes:
    return f"{family} | {sample}".encode()


def _place(data: Path, layout: str, family: str, sample: str, ext: str = "cram") -> Path:
    """The sample's reads and their index at ``layout``, each holding ``<family> | <sample>``."""
    path = data / layout.format(family=family, sample=sample, ext=ext)
    _write(path, _content(family, sample))
    _write(Path(f"{path}{INDEX[ext]}"), _content(family, sample))
    return path


def _inside(path: Path, *folders: Path) -> bool:
    """Whether ``path``, its ``..`` taken as written, lies inside one of ``folders``."""
    normalized = os.path.normpath(path)
    return any(normalized.startswith(f"{folder}{os.sep}") for folder in folders)


def _folders(data: Path, family: str) -> tuple[Path, Path]:
    return data / family, data / "families" / family


def _finds_nothing(family: str, sample: str) -> bool:
    """Whether the lookup finds neither reads nor index for the sample, in either format, and
    the manifest lists nothing for it."""
    found = [
        cram._alignment_path(family, sample, ext, suffix)
        for ext, index in INDEX.items()
        for suffix in ("", index)
    ]
    return found == [None] * 4 and cram._resolve_alignment_manifest_entry(family, sample) is None


@pytest.fixture()
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty data directory in ``tmp_path`` that the lookup reads, in local storage mode.
    Returned as its real path, the one the lookup builds its paths from."""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(cram, "DATA_DIR", root)
    monkeypatch.setattr(settings, "storage_backend", "local")
    return Path(os.path.realpath(root))


# --------------------------------------------------------------------------- #
# The normal layouts
# --------------------------------------------------------------------------- #


def test_the_layouts_are_probed_in_order_in_the_familys_folders(data: Path) -> None:
    for ext, index in INDEX.items():
        expected = [data / layout.format(family="F1", sample="S1", ext=ext) for layout in LAYOUTS]
        assert cram._alignment_candidate_paths("F1", "S1", ext) == expected
        assert cram._alignment_candidate_paths("F1", "S1", ext, index) == [Path(f"{path}{index}") for path in expected]


@pytest.mark.parametrize("ext", INDEX)
@pytest.mark.parametrize("layout", LAYOUTS)
def test_each_layout_finds_the_samples_own_reads(data: Path, layout: str, ext: str) -> None:
    path = _place(data, layout, "F1", "S1", ext)

    assert cram._alignment_path("F1", "S1", ext) == path
    assert cram._alignment_path("F1", "S1", ext, INDEX[ext]) == Path(f"{path}{INDEX[ext]}")
    entry = cram._resolve_alignment_manifest_entry("F1", "S1")
    assert entry is not None
    assert (entry.format, entry.url, entry.index_url) == (ext, f"/cram/F1/S1.{ext}", f"/cram/F1/S1.{ext}{INDEX[ext]}")


def test_the_first_layout_that_holds_the_file_wins(data: Path) -> None:
    _place(data, LAYOUTS[4], "F1", "S1")
    package = _place(data, LAYOUTS[1], "F1", "S1")

    assert cram._alignment_path("F1", "S1", "cram") == package


def test_a_family_named_after_the_packages_folder_keeps_its_package_folder(data: Path) -> None:
    own = _place(data, LAYOUTS[1], "families", "S1")

    assert cram._alignment_path("families", "S1", "cram") == own


# --------------------------------------------------------------------------- #
# IDs that hold a path
# --------------------------------------------------------------------------- #

# Sample IDs of family F1 that climb into family F2's folders from one layout or another.
CLIMBING_SAMPLE_IDS = {
    # <data>/F1/.. -> <data>/F2/S2.cram; families/F1/.. -> families/F2/S2.cram
    "into F2's folders": "../F2/S2",
    # families/F1/bams/../.. -> families/F2/bams/S2.cram; families/F1/.. -> <data>/F2/bams/S2.cram
    "into F2's bams folders": "../../F2/bams/S2",
    # <data>/F1/.. -> families/F2/bams/S2.cram
    "into F2's package": "../families/F2/bams/S2",
    # families/F1/bams/../../.. -> <data>/F2/S2.cram
    "up to the data directory": "../../../F2/S2",
    # an absolute path replaces the folder it is joined to
    "absolute path": "<data>/F2/S2",
}


@pytest.mark.parametrize("sample_id", CLIMBING_SAMPLE_IDS.values(), ids=CLIMBING_SAMPLE_IDS.keys())
def test_a_sample_id_that_climbs_into_another_family_finds_none_of_its_reads(data: Path, sample_id: str) -> None:
    for layout in LAYOUTS:
        _place(data, layout, "F2", "S2")
        _place(data, layout, "F2", "S2", "bam")
    sample_id = sample_id.replace("<data>", str(data))

    candidates = cram._alignment_candidate_paths("F1", sample_id, "cram")

    # Each path the lookup builds stays in F1's own folders, and none of F2's files is found.
    assert all(_inside(candidate, *_folders(data, "F1")) for candidate in candidates)
    assert _finds_nothing("F1", sample_id)


# Family IDs that are no single folder name. With F2's reads in every layout, several of these
# reached them: `../F2` (families/.. -> <data>/F2/bams), `families/F2`, `F2/bams` and F2's
# folder as an absolute path.
NO_FOLDER_FAMILY_IDS = ["", ".", "..", "../F2", "F2/..", "./F2", "F2/", "families/F2", "F2/bams", "<absolute F2>"]


@pytest.mark.parametrize("family_id", NO_FOLDER_FAMILY_IDS)
def test_a_family_id_that_is_no_folder_name_has_no_folder(data: Path, family_id: str) -> None:
    for layout in LAYOUTS:
        _place(data, layout, "F2", "S2")
    family_id = family_id.replace("<absolute F2>", str(data / "F2"))

    assert cram._alignment_candidate_paths(family_id, "S2", "cram") == []
    assert _finds_nothing(family_id, "S2")


def test_the_packages_folder_is_no_familys_flat_folder(data: Path) -> None:
    # As the flat folder of a family named `families`, the packages folder would lead into
    # every package: F2's by a sample ID holding a path, and a family named `bams` by a
    # plain one, through the flat `bams` layout.
    _place(data, LAYOUTS[1], "F2", "S2")
    _place(data, LAYOUTS[3], "bams", "S2")

    for sample_id in ("S2", "F2/bams/S2"):
        candidates = cram._alignment_candidate_paths("families", sample_id, "cram")
        assert all(_inside(candidate, data / "families" / "families") for candidate in candidates)
        assert _finds_nothing("families", sample_id)


def test_a_linked_packages_folder_still_serves_its_packages(data: Path) -> None:
    # The packages folder may be a link to another folder of the data directory. Its packages
    # are still found there, and the family whose flat folder holds it, here `store`, does
    # not reach into them.
    (data / "store" / "packages").mkdir(parents=True)
    (data / "families").symlink_to(data / "store" / "packages")
    _place(data, "store/packages/{family}/bams/{sample}.{ext}", "F1", "S1")

    found = cram._alignment_path("F1", "S1", "cram")
    assert found is not None and found.read_bytes() == _content("F1", "S1")
    assert _finds_nothing("store", "packages/F1/bams/S1")


def test_a_nul_in_an_id_finds_nothing_and_raises_nothing(data: Path) -> None:
    _place(data, LAYOUTS[0], "F1", "S1")

    assert cram._alignment_candidate_paths("F1", "S1\x00", "cram") == []
    assert cram._alignment_candidate_paths("F1\x00", "S1", "cram") == []
    assert _finds_nothing("F1", "S1\x00")


# --------------------------------------------------------------------------- #
# Symlinks
# --------------------------------------------------------------------------- #

# (the link, where it leads), under the data directory.
LINKS_INTO_F2 = {
    "package bams folder": ("families/F1/bams", "families/F2/bams"),
    "package folder": ("families/F1", "families/F2"),
    "flat folder": ("F1", "F2"),
    "flat bams folder": ("F1/bams", "families/F2/bams"),
    "file": ("F1/S1.cram", "F2/S1.cram"),
}


def _link(data: Path, link: str, target: str) -> None:
    (data / link).parent.mkdir(parents=True, exist_ok=True)
    (data / link).symlink_to(data / target)
    if link.endswith(".cram"):
        (data / f"{link}.crai").symlink_to(data / f"{target}.crai")


@pytest.mark.parametrize("link, target", LINKS_INTO_F2.values(), ids=LINKS_INTO_F2.keys())
def test_a_symlink_that_leads_into_another_familys_folder_is_not_followed(data: Path, link: str, target: str) -> None:
    # F2's folders hold reads named after F1's sample, in every layout.
    for layout in LAYOUTS:
        _place(data, layout, "F2", "S1")
    _link(data, link, target)

    assert _finds_nothing("F1", "S1")


def test_a_symlink_between_the_familys_own_folders_is_followed(data: Path) -> None:
    # The flat folder made a link to the package's reads, as before the package layouts were
    # probed.
    _place(data, LAYOUTS[1], "F1", "S1")
    (data / "F1").symlink_to(data / "families" / "F1" / "bams")

    assert cram._alignment_path("F1", "S1", "cram") == data / "F1" / "S1.cram"
    assert cram._resolve_alignment_manifest_entry("F1", "S1") is not None


def _folders_leading_out(data: Path, tmp_path: Path) -> None:
    """Both of F1's folders are links out of the data directory, to a folder that holds
    reads and an index named after F1's sample S1."""
    outside = tmp_path / "outside" / "F1"
    for name in ("S1.cram", "S1.cram.crai"):
        _write(outside / name, b"outside the data directory")
    (data / "F1").symlink_to(outside)
    (data / "families").mkdir()
    (data / "families" / "F1").symlink_to(outside)


def test_a_family_whose_folders_lead_out_of_the_data_directory_has_no_reads(data: Path, tmp_path: Path) -> None:
    # Every layout is refused, and nothing else is tried: the GET used to serve
    # <data>/F1/S1.cram from outside the data directory (SEC-13).
    _folders_leading_out(data, tmp_path)

    assert cram._alignment_candidate_paths("F1", "S1", "cram") == []
    assert _finds_nothing("F1", "S1")
    with pytest.raises(HTTPException) as refused:
        cram._serve_alignment("F1", "S1", "cram", "", "CRAM file not found")
    assert (refused.value.status_code, refused.value.detail) == (404, "CRAM file not found")


def test_no_fallback_when_an_id_climbs_out_of_every_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Four levels up from each layout leaves the data directory; from <data>/F1 it reaches
    # tmp_path, where a file waits. It is not looked at.
    data = tmp_path / "a" / "b" / "data"
    (data / "F1").mkdir(parents=True)
    monkeypatch.setattr(cram, "DATA_DIR", data)
    monkeypatch.setattr(settings, "storage_backend", "local")
    _write(tmp_path / "x" / "S9.cram", b"outside")

    assert cram._alignment_candidate_paths("F1", "../../../../x/S9", "cram") == []
    assert cram._alignment_path("F1", "../../../../x/S9", "cram") is None
    with pytest.raises(HTTPException) as refused:
        cram._serve_alignment("F1", "../../../../x/S9", "cram", "", "CRAM file not found")
    assert refused.value.status_code == 404


class _AlignmentFile:
    """Stands in for ``pysam.AlignmentFile``: records what it is asked to open."""

    opened: list[str] = []

    def __init__(self, path: str, mode: str) -> None:
        self.opened.append(path)
        self.header = SimpleNamespace(to_dict=lambda: {"HD": {"VN": "1.6"}})

    def __enter__(self) -> "_AlignmentFile":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_the_header_is_read_only_from_the_familys_own_folders(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cram.pysam, "AlignmentFile", _AlignmentFile)
    monkeypatch.setattr(_AlignmentFile, "opened", [])
    _folders_leading_out(data, tmp_path)
    own = _place(data, LAYOUTS[1], "F3", "S3")

    with pytest.raises(HTTPException) as refused:
        cram._read_alignment_header("F1", "S1")
    assert refused.value.status_code == 404
    assert cram._read_alignment_header("F3", "S3") == {"HD": {"VN": "1.6"}}
    assert _AlignmentFile.opened == [str(own)]


# --------------------------------------------------------------------------- #
# Through the app
# --------------------------------------------------------------------------- #

ORIGIN = "http://coga.test"


@pytest.fixture()
def members(data: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """The app over ``data`` with a signed-in viewer who may read every family; the family
    records (their members) are read from the returned mapping."""
    families: dict[str, list[str]] = {}

    async def family_record(session: Any, family_id: str, user: Any) -> Any:
        if family_id not in families:
            raise HTTPException(status_code=404, detail="Family not found")
        return SimpleNamespace(members=[SimpleNamespace(sample_id=sample) for sample in families[family_id]])

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

    monkeypatch.setattr(cram, "get_family_record", family_record)
    monkeypatch.setattr(request_logging, "write_audit_log_event", keep_audit_row)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, no_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, a_viewer)
    return families


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


async def _manifest(client: httpx.AsyncClient, family_id: str, *sample_ids: str) -> list[dict[str, Any]]:
    response = await client.get(f"/api/cram/{family_id}/manifest", params={"sample": list(sample_ids)})
    assert response.status_code == 200, response.text
    return response.json()


async def _fetch(client: httpx.AsyncClient, family_id: str, file_name: str) -> tuple[int, bytes, int]:
    """The GET's status and body, and the HEAD's status, of a file the endpoints serve."""
    url = f"/api{url_path('cram', family_id, file_name)}"
    got = await client.get(url)
    head = await client.head(url)
    return got.status_code, got.content, head.status_code


@pytest.mark.asyncio
async def test_the_manifest_lists_only_the_familys_own_reads(data: Path, members: dict[str, list[str]]) -> None:
    members["F1"] = ["S1", "../F2/S2"]
    members["F2"] = ["S2"]
    _place(data, LAYOUTS[1], "F1", "S1")
    for layout in LAYOUTS:
        _place(data, layout, "F2", "S2")

    async with _client() as client:
        entries = await _manifest(client, "F1", "S1", "../F2/S2")
        fetched = [await client.get(f"{ORIGIN}/api{entries[0][key]}") for key in ("url", "index_url")]

    assert [entry["sample_id"] for entry in entries] == ["S1"]
    assert [(response.status_code, response.content) for response in fetched] == [(200, _content("F1", "S1"))] * 2


@pytest.mark.asyncio
async def test_a_folder_linked_into_another_family_serves_nothing(data: Path, members: dict[str, list[str]]) -> None:
    members["F1"] = ["S3"]
    members["F2"] = ["S2"]
    _place(data, LAYOUTS[1], "F2", "S3")
    _link(data, "families/F1/bams", "families/F2/bams")

    async with _client() as client:
        entries = await _manifest(client, "F1", "S3")
        fetched = [await _fetch(client, "F1", "S3.cram"), await _fetch(client, "F1", "S3.cram.crai")]

    assert entries == []
    assert fetched == [
        (404, b'{"detail":"CRAM file not found"}', 404),
        (404, b'{"detail":"CRAI file not found"}', 404),
    ]


@pytest.mark.asyncio
async def test_a_family_folder_leading_out_of_the_data_directory_serves_nothing(
    data: Path, tmp_path: Path, members: dict[str, list[str]]
) -> None:
    members["F1"] = ["S1"]
    _folders_leading_out(data, tmp_path)

    async with _client() as client:
        entries = await _manifest(client, "F1", "S1")
        fetched = await _fetch(client, "F1", "S1.cram")

    assert entries == []
    assert fetched == (404, b'{"detail":"CRAM file not found"}', 404)
