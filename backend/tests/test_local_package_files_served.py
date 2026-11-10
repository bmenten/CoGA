"""A local package's files are served from the folder its import read (STORAGE_BACKEND=local).

A package is imported where it lies: one on a network share mounted read-only as a second
import root (``FAMILY_IMPORT_ROOTS=/data/families,/data/share``, docs/deployment-vm.md) stays
there. The imports record each file relative to the package (``samples.metadata``:
``alignment``, ``signal_tracks``, ``sequencing_qc.report``), and the family the folder it read
(``families.metadata.package_import.folder_path``). With local storage the genome browser's
reads, the CNV caller's signal tracks and the QC report are looked up beyond the family's
folders, ``<data>/<family>`` and ``<data>/families/<family>``, so a package on the share, or one
under ``<data>/families`` in a folder not named after the family, shows its reads in IGV.

The recorded file comes first, in the folder the family's import read, as in remote mode:

* the reads are served at ``/cram/<family>/<sample>.cram`` whatever the file is called, with the
  recorded index or the one beside them, never an index a layout probe found elsewhere;
* the layouts in the family's folders stay the fallback when nothing usable is recorded or the
  recorded file is gone;
* a recorded path is data: one that leaves the package (``..``, an absolute path, a symlink into
  another package) or lacks its file's extension names no file;
* the package folder is data too: it is used only when it lies, symlinks resolved, below a local
  import root, and not when it is the root itself, a URI, a relative path, or when no local root
  is configured.

All IDs and files are synthetic.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest

from app.core.config import settings
from app.routers import cram, family_qc_reports, signal_tracks
from app.services import family_package_source

# The package on the share, in a folder not named after its family F1, as the pipeline wrote it.
PACKAGE_FILES = {
    "bams/S1.markdup.cram": "reads of S1",
    "bams/S1.markdup.cram.crai": "index of S1",
    "cnv/S1.depth.bw": "depth of S1",
    "qc/S1.html": "report of S1",
}
# Another family's package beside it on the share.
OTHER_PACKAGE_FILES = {
    "bams/S2.cram": "reads of S2",
    "bams/S2.cram.crai": "index of S2",
    "cnv/S2.depth.bw": "depth of S2",
    "qc/S2.html": "report of S2",
}
DEPTH = "depth_bigwig"


def _write(folder: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


@pytest.fixture()
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A data directory with two import roots, ``families`` and ``share``; on the share F1's
    package ``PKG1`` and F2's ``PKG2``. Returned as its real path."""
    root = Path(os.path.realpath(tmp_path)) / "data"
    (root / "families").mkdir(parents=True)
    _write(root / "share" / "PKG1", PACKAGE_FILES)
    _write(root / "share" / "PKG2", OTHER_PACKAGE_FILES)
    for module in (cram, signal_tracks, family_qc_reports):
        monkeypatch.setattr(module, "DATA_DIR", root)
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "family_import_roots", [str(root / "families"), str(root / "share")])
    monkeypatch.setattr(settings, "secret_key", "k" * 64)
    return root


def _package(data: Path) -> str:
    return str(data / "share" / "PKG1")


def _alignment(**overrides: Any) -> dict[str, Any]:
    """``samples.metadata['alignment']`` as the import of PKG1 wrote it."""
    return {"path": "bams/S1.markdup.cram", "format": "cram", "index_path": "bams/S1.markdup.cram.crai", **overrides}


class _Session:
    """Answers the endpoints' queries: each sample's recorded ``alignment`` or
    ``signal_tracks`` with its family's package folder, and the QC report's row."""

    def __init__(self, recorded: dict[str, Any], package_folder: Any) -> None:
        self.recorded = recorded
        self.package_folder = package_folder

    async def execute(self, statement: Any, params: Any = None) -> Any:
        params = params or {}
        if "sample_ids" in params:
            rows = [
                (sample_id, value, self.package_folder)
                for sample_id, value in self.recorded.items()
                if sample_id in set(params["sample_ids"])
            ]
            return SimpleNamespace(all=lambda: rows)
        metadata = self.recorded.get(params.get("sample_id"))
        row = None if metadata is None else (metadata, self.package_folder)
        return SimpleNamespace(one_or_none=lambda: row)


def _members(monkeypatch: pytest.MonkeyPatch, family_id: str = "F1", *sample_ids: str) -> None:
    async def family_record(session: Any, asked: str, user: Any) -> Any:
        if asked != family_id:
            raise HTTPException(status_code=404, detail="Family not found")
        return SimpleNamespace(members=[SimpleNamespace(sample_id=sample_id) for sample_id in sample_ids or ("S1",)])

    for module in (cram, signal_tracks, family_qc_reports):
        monkeypatch.setattr(module, "get_family_record", family_record)


async def _served(endpoint: Any, *args: Any, session: Any) -> str | int:
    """What a GET serves: the file's text, or the status it answers with."""
    try:
        response = await endpoint(*args, session=session, user=object())
    except HTTPException as exc:
        return exc.status_code
    return Path(response.path).read_text()


async def _cram_answers(session: Any, family_id: str = "F1", sample_id: str = "S1") -> dict[str, Any]:
    manifest = await cram.get_alignment_manifest(family_id, sample_ids=[sample_id], session=session, user=object())
    try:
        head = (await cram.head_cram(family_id, sample_id, session=session, user=object())).status_code
    except HTTPException as exc:
        head = exc.status_code
    return {
        "manifest": [(entry.format, entry.url, entry.index_url) for entry in manifest],
        "cram": await _served(cram.get_cram, family_id, sample_id, session=session),
        "crai": await _served(cram.get_crai, family_id, sample_id, session=session),
        "head": head,
        "bam": await _served(cram.get_bam, family_id, sample_id, session=session),
    }


NOTHING = {"manifest": [], "cram": 404, "crai": 404, "head": 404, "bam": 404}


# --------------------------------------------------------------------------- #
# Aligned reads
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reads_recorded_in_a_package_on_a_second_import_root_are_served(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": _alignment()}, _package(data))

    assert await _cram_answers(session) == {
        # Served under the sample's name, whatever the pipeline called the file.
        "manifest": [("cram", "/cram/F1/S1.cram", "/cram/F1/S1.cram.crai")],
        "cram": "reads of S1",
        "crai": "index of S1",
        "head": 200,
        # The recorded CRAM answers for the sample: no BAM is looked for.
        "bam": 404,
    }


@pytest.mark.asyncio
async def test_a_package_under_the_families_root_in_a_folder_named_otherwise_is_served(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The layouts look in <data>/families/F1 only; the package's folder is named after its run.
    _write(data / "families" / "run-0001", PACKAGE_FILES)
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": _alignment()}, str(data / "families" / "run-0001"))

    answers = await _cram_answers(session)

    assert (answers["cram"], answers["crai"]) == ("reads of S1", "index of S1")


@pytest.mark.asyncio
async def test_a_recorded_index_named_otherwise_is_served(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(data / "share" / "PKG1", {"bams/S1.crai": "short-named index of S1"})
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": _alignment(index_path="bams/S1.crai")}, _package(data))

    assert await _served(cram.get_crai, "F1", "S1", session=session) == "short-named index of S1"


@pytest.mark.asyncio
async def test_without_a_recorded_index_the_one_beside_the_reads_is_served(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _members(monkeypatch, "F1", "S1")
    recorded = _alignment()
    del recorded["index_path"]
    session = _Session({"S1": recorded}, _package(data))

    answers = await _cram_answers(session)

    assert (answers["manifest"], answers["crai"]) == ([("cram", "/cram/F1/S1.cram", "/cram/F1/S1.cram.crai")], "index of S1")


@pytest.mark.asyncio
async def test_a_recorded_cram_is_never_paired_with_a_probed_index(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The recorded index is gone; a layout in F1's folder holds one, of some other file. IGV
    # would read the recorded reads at the wrong offsets.
    (data / "share" / "PKG1" / "bams" / "S1.markdup.cram.crai").unlink()
    _write(data / "F1", {"S1.cram.crai": "index of another file"})
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": _alignment()}, _package(data))

    answers = await _cram_answers(session)

    assert (answers["manifest"], answers["crai"], answers["cram"]) == ([], 404, "reads of S1")


@pytest.mark.asyncio
async def test_the_layouts_serve_a_sample_whose_recorded_reads_are_gone(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (data / "share" / "PKG1" / "bams" / "S1.markdup.cram").unlink()
    _write(data / "families" / "F1", {"bams/S1.cram": "copied reads of S1", "bams/S1.cram.crai": "copied index of S1"})
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": _alignment()}, _package(data))

    answers = await _cram_answers(session)

    assert (answers["cram"], answers["crai"], answers["manifest"]) == (
        "copied reads of S1",
        "copied index of S1",
        [("cram", "/cram/F1/S1.cram", "/cram/F1/S1.cram.crai")],
    )


@pytest.mark.asyncio
async def test_the_header_is_read_from_the_recorded_reads(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[tuple[str, str]] = []

    class _Alignments:
        def __init__(self, path: str, mode: str) -> None:
            opened.append((path, mode))
            self.header = SimpleNamespace(to_dict=lambda: {"SQ": [{"SN": "chr1", "LN": 1000}]})

        def __enter__(self) -> "_Alignments":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr(cram.pysam, "AlignmentFile", _Alignments)
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": _alignment()}, _package(data))

    header = await cram.get_cram_header("F1", "S1", session=session, user=object())  # type: ignore[arg-type]

    assert header == {"SQ": [{"SN": "chr1", "LN": 1000}]}
    assert opened == [(str(data / "share" / "PKG1" / "bams" / "S1.markdup.cram"), "rc")]


def _link(at: Path, to: Path) -> None:
    at.parent.mkdir(parents=True, exist_ok=True)
    at.symlink_to(to)


# Recorded alignments a tampered or stale row could carry, with F1's package folder. F2's
# package on the same share holds a file at each, so only the check keeps it from being served.
UNTRUSTED_ALIGNMENTS = {
    "climbing into another package": _alignment(path="../PKG2/bams/S2.cram", index_path="../PKG2/bams/S2.cram.crai"),
    "an absolute path into another package": _alignment(path="{share}/PKG2/bams/S2.cram", index_path=None),
    "a file linked into another package": _alignment(path="bams/linked.cram", index_path="bams/linked.cram.crai"),
    "a folder linked into another package": _alignment(path="linked/S2.cram", index_path="linked/S2.cram.crai"),
    "an extension that is not the format": _alignment(path="bams/S2.bam", index_path=None),
    "an unknown format": _alignment(format="sam"),
    "a format that is not a string": _alignment(format=["cram"]),
    "a path that is not a string": _alignment(path=["bams/S1.markdup.cram"]),
    "no path": {"format": "cram", "index_path": "bams/S1.markdup.cram.crai"},
    "a NUL in the path": _alignment(path="bams/S1.markdup.cram\x00.cram"),
    "not a mapping": ["bams/S1.markdup.cram"],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", UNTRUSTED_ALIGNMENTS.values(), ids=UNTRUSTED_ALIGNMENTS.keys())
async def test_a_recorded_path_out_of_the_package_serves_nothing(
    data: Path, monkeypatch: pytest.MonkeyPatch, recorded: Any
) -> None:
    package, other = data / "share" / "PKG1", data / "share" / "PKG2"
    _link(package / "bams" / "linked.cram", other / "bams" / "S2.cram")
    _link(package / "bams" / "linked.cram.crai", other / "bams" / "S2.cram.crai")
    _link(package / "linked", other / "bams")
    _write(package, {"bams/S2.bam": "a BAM recorded as a CRAM"})
    # Nothing at F1's own layouts either: what is served could only come from the record.
    (package / "bams" / "S1.markdup.cram").unlink()
    if isinstance(recorded, dict) and isinstance(recorded.get("path"), str):
        recorded = {**recorded, "path": recorded["path"].format(share=data / "share")}
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": recorded}, str(package))

    assert await _cram_answers(session) == NOTHING


# The family's package folder as a changed or missing families row could hold it. A package
# folder outside the import roots holds S1's reads at the recorded path, so only the check
# keeps them from being served.
UNUSABLE_PACKAGE_FOLDERS = {
    "none recorded": lambda data: None,
    "a bucket's URI": lambda data: "gs://phi/imports/PKG1",
    "a relative path": lambda data: "share/PKG1",
    "a NUL": lambda data: f"{data}/share/PKG1\x00",
    "outside the import roots": lambda data: str(data.parent / "outside" / "PKG1"),
    "a sibling of an import root": lambda data: str(data / "share-old" / "PKG1"),
    "a link from an import root to outside it": lambda data: str(data / "share" / "LINKED"),
    "climbing out of an import root": lambda data: str(data / "share" / ".." / ".." / "outside" / "PKG1"),
    "not a string": lambda data: 42,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("folder", UNUSABLE_PACKAGE_FOLDERS.values(), ids=UNUSABLE_PACKAGE_FOLDERS.keys())
async def test_no_file_is_served_from_a_package_folder_outside_the_import_roots(
    data: Path, monkeypatch: pytest.MonkeyPatch, folder: Any
) -> None:
    _write(data.parent / "outside" / "PKG1", PACKAGE_FILES)
    _write(data / "share-old" / "PKG1", PACKAGE_FILES)
    _link(data / "share" / "LINKED", data.parent / "outside" / "PKG1")
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": _alignment()}, folder(data))

    assert await _cram_answers(session) == NOTHING


@pytest.mark.asyncio
async def test_no_file_is_served_from_an_import_root_itself(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _members(monkeypatch, "F1", "S1")
    session = _Session(
        {"S1": _alignment(path="PKG1/bams/S1.markdup.cram", index_path="PKG1/bams/S1.markdup.cram.crai")},
        str(data / "share"),
    )

    assert await _cram_answers(session) == NOTHING


@pytest.mark.asyncio
@pytest.mark.parametrize("roots", [[], ["gs://phi/imports"]], ids=["no roots", "remote roots only"])
async def test_no_file_is_served_from_a_package_folder_without_a_local_import_root(
    data: Path, monkeypatch: pytest.MonkeyPatch, roots: list[str]
) -> None:
    # An empty FAMILY_IMPORT_ROOTS lets an import read any folder (development), but nothing
    # is served from a folder no root lists.
    monkeypatch.setattr(settings, "family_import_roots", roots)
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": _alignment()}, _package(data))

    assert await _cram_answers(session) == NOTHING


def test_a_folder_named_like_an_import_root_is_not_below_it(data: Path) -> None:
    folder = family_package_source.local_family_package_folder

    assert folder(str(data / "share" / "PKG1")) == str(data / "share" / "PKG1")
    assert folder(str(data / "share")) is None
    assert folder(str(data / "share-old" / "PKG1")) is None


@pytest.mark.asyncio
async def test_the_recorded_folder_is_resolved_off_the_event_loop(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Resolving a folder on a network share reads the share: never on the event loop.
    seen: list[int] = []
    real = cram.local_family_package_folder

    def resolve(folder: object) -> str | None:
        seen.append(threading.get_ident())
        return real(folder)

    monkeypatch.setattr(cram, "local_family_package_folder", resolve)
    session = _Session({"S1": _alignment()}, _package(data))
    loop_thread = threading.get_ident()

    recorded = await cram._recorded_alignments(session, ["S1"])  # type: ignore[arg-type]

    assert recorded["S1"].location == str(data / "share" / "PKG1" / "bams" / "S1.markdup.cram")
    assert seen and loop_thread not in seen


# --------------------------------------------------------------------------- #
# Signal tracks and the QC report
# --------------------------------------------------------------------------- #


async def _track(session: Any) -> tuple[list[str], str | int, int]:
    """The manifest's URLs, what the GET serves and what the HEAD answers for S1's depth track."""
    manifest = await signal_tracks.get_signal_track_manifest("F1", sample_ids=["S1"], session=session, user=object())
    served = await _served(signal_tracks.get_signal_track, "F1", "S1", "hificnv", DEPTH, session=session)
    try:
        head = (
            await signal_tracks.head_signal_track("F1", "S1", "hificnv", DEPTH, session=session, user=object())
        ).status_code
    except HTTPException as exc:
        head = exc.status_code
    return [entry.url for entry in manifest], served, head


@pytest.mark.asyncio
async def test_a_signal_track_recorded_in_a_package_on_the_share_is_served(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": {"hificnv": {DEPTH: "cnv/S1.depth.bw"}}}, _package(data))

    assert await _track(session) == (["/signal-tracks/F1/S1/hificnv/depth_bigwig"], "depth of S1", 200)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relative, folder",
    [
        ("../PKG2/cnv/S2.depth.bw", "share/PKG1"),
        ("{share}/PKG2/cnv/S2.depth.bw", "share/PKG1"),
        ("cnv/S2.depth.bw", "../outside/PKG2"),
    ],
    ids=["climbing into another package", "an absolute path", "a package folder outside the roots"],
)
async def test_a_signal_track_out_of_the_package_is_not_served(
    data: Path, monkeypatch: pytest.MonkeyPatch, relative: str, folder: str
) -> None:
    _write(data.parent / "outside" / "PKG2", OTHER_PACKAGE_FILES)
    _members(monkeypatch, "F1", "S1")
    recorded = {"S1": {"hificnv": {DEPTH: relative.format(share=data / "share")}}}
    session = _Session(recorded, os.path.normpath(data / folder))

    assert await _track(session) == ([], 404, 404)


@pytest.mark.asyncio
async def test_the_families_folder_still_serves_a_track_the_package_folder_lacks(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(data / "families" / "F1", {"cnv/S1.copied.bw": "copied depth of S1"})
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": {"hificnv": {DEPTH: "cnv/S1.copied.bw"}}}, _package(data))

    assert (await _track(session))[1:] == ("copied depth of S1", 200)


async def _report(session: Any) -> str | int:
    """What following S1's QC-report link serves, or the status the link request answers."""
    try:
        link = await family_qc_reports.get_family_qc_report_link("F1", "S1", session=session, user=object())  # type: ignore[arg-type]
    except HTTPException as exc:
        return exc.status_code
    token = link.url.split("token=", 1)[1]
    response = await family_qc_reports.get_family_qc_report("F1", "S1", token=token, session=session)
    return Path(response.path).read_text()


@pytest.mark.asyncio
async def test_a_qc_report_recorded_in_a_package_on_the_share_is_served(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": {"sequencing_qc": {"report": "qc/S1.html"}}}, _package(data))

    assert await _report(session) == "report of S1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relative, folder",
    [("../PKG2/qc/S2.html", "share/PKG1"), ("qc/S2.html", "../outside/PKG2")],
    ids=["climbing into another package", "a package folder outside the roots"],
)
async def test_a_qc_report_out_of_the_package_is_not_served(
    data: Path, monkeypatch: pytest.MonkeyPatch, relative: str, folder: str
) -> None:
    _write(data.parent / "outside" / "PKG2", OTHER_PACKAGE_FILES)
    _members(monkeypatch, "F1", "S1")
    session = _Session({"S1": {"sequencing_qc": {"report": relative}}}, os.path.normpath(data / folder))

    assert await _report(session) == 404


def test_the_qc_report_query_reads_the_familys_package_folder(data: Path) -> None:
    class _Recording(_Session):
        sql: list[str] = []

        async def execute(self, statement: Any, params: Any = None) -> Any:
            self.sql.append(" ".join(str(statement).split()))
            return await super().execute(statement, params)

    session = _Recording({"S1": {"sequencing_qc": {"report": " qc/S1.html "}}}, _package(data))

    report = asyncio.run(family_qc_reports._recorded_report(session, family_id="F1", sample_id="S1"))  # type: ignore[arg-type]

    assert report == family_qc_reports._RecordedReport(" qc/S1.html ", _package(data))
    assert "f.metadata -> 'package_import' ->> 'folder_path'" in session.sql[0]
