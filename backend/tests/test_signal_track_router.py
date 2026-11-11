"""Serving CNV caller signal files to the genome browser.

These endpoints hand out file paths that were written into the database by an
import and are joined with a `family_id` taken from the URL, so the containment
check is what keeps a crafted id from reaching outside the data directory. The
manifest also has to be honest about what exists: an entry for a missing file
gives the browser a broken track instead of no track.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.routers import signal_tracks


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(signal_tracks, "DATA_DIR", tmp_path)
    package = tmp_path / "families" / "pacbio" / "cnv" / "HG002"
    package.mkdir(parents=True)
    (package / "HG002.Sample0.depth.bw").write_bytes(b"bigwig")
    return tmp_path


def test_a_recorded_path_resolves_inside_the_package(data_root: Path) -> None:
    resolved = signal_tracks._resolve_track_path(
        "pacbio", "cnv/HG002/HG002.Sample0.depth.bw"
    )
    assert resolved == data_root / "families/pacbio/cnv/HG002/HG002.Sample0.depth.bw"


def test_a_missing_file_resolves_to_nothing(data_root: Path) -> None:
    # The path was recorded at import; the file can be moved or deleted afterwards.
    assert signal_tracks._resolve_track_path("pacbio", "cnv/HG002/gone.bw") is None
    assert signal_tracks._resolve_track_path("pacbio", "") is None


@pytest.mark.parametrize(
    "relative_path",
    [
        "../../../etc/passwd",
        "cnv/../../../../etc/passwd",
        "/etc/passwd",
    ],
)
def test_a_path_escaping_the_data_directory_is_refused(
    data_root: Path, relative_path: str
) -> None:
    assert signal_tracks._resolve_track_path("pacbio", relative_path) is None


def test_a_family_id_escaping_the_data_directory_is_refused(data_root: Path) -> None:
    # family_id comes from the URL and is joined into the path.
    assert (
        signal_tracks._resolve_track_path("../../etc", "passwd") is None
    )


def test_every_track_kind_declares_how_to_draw_it() -> None:
    for kind, spec in signal_tracks._TRACK_KINDS.items():
        assert spec["format"] in {"bigwig", "bedgraph"}, kind
        assert spec["label"], kind
        assert spec["media_type"], kind

    # MAF is 0-0.5 by construction and gets a fixed axis; read depth is unbounded
    # and sample-specific, so it must autoscale rather than clip.
    assert signal_tracks._TRACK_KINDS["maf_bigwig"]["min"] == 0.0
    assert signal_tracks._TRACK_KINDS["maf_bigwig"]["max"] == 0.5
    assert "max" not in signal_tracks._TRACK_KINDS["depth_bigwig"]


@pytest.mark.asyncio
async def test_only_known_kinds_are_read_out_of_the_recorded_metadata() -> None:
    class FakeResult:
        def all(self):
            return [
                (
                    "HG002",
                    {
                        "hificnv": {
                            "depth_bigwig": "cnv/HG002/HG002.Sample0.depth.bw",
                            # Not a track kind this router serves.
                            "summary_html": "cnv/HG002/annotation/summary.html",
                            # Recorded but empty.
                            "maf_bigwig": "",
                        },
                        # Not a mapping.
                        "broken": "nonsense",
                    },
                    None,
                ),
                # No usable entries at all: must not appear.
                ("OTHER", {"hificnv": {"unknown_kind": "x"}}, None),
            ]

    class FakeSession:
        async def execute(self, *args, **kwargs):
            return FakeResult()

    recorded = signal_tracks._track_paths(
        await signal_tracks._signal_track_rows(FakeSession(), ["HG002", "OTHER"])
    )

    # sample -> source -> kind -> package-relative path. The unknown kind, the empty
    # path and the non-mapping source are all dropped, and a sample left with
    # nothing usable does not appear at all.
    assert recorded == {
        "HG002": {"hificnv": {"depth_bigwig": "cnv/HG002/HG002.Sample0.depth.bw"}}
    }


@pytest.mark.asyncio
async def test_no_samples_means_no_query(monkeypatch: pytest.MonkeyPatch) -> None:
    class ExplodingSession:
        async def execute(self, *args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("should not query for an empty sample list")

    assert await signal_tracks._signal_track_rows(ExplodingSession(), []) == []


# ---------------------------------------------------------------------------
# The importer and the endpoints
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402

from app.services import family_package_datasets  # noqa: E402
from app.services.family_package_common import FamilyPackageBundle, PackageManifest, ParsedPed  # noqa: E402

DEPTH = "cnv/HG002/HG002.Sample0.depth.bw"


class _RowsSession:
    """Answers the query for each sample's ``signal_tracks`` and its family's package folder,
    ``families.metadata.package_import.folder_path`` (none, unless given)."""

    def __init__(self, rows: dict[str, Any], package_folder: Any = None) -> None:
        self.rows = rows
        self.package_folder = package_folder
        self.sql: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.sql.append(" ".join(str(statement).split()))
        wanted = set(params["sample_ids"])
        rows = [(sample_id, value, self.package_folder) for sample_id, value in self.rows.items() if sample_id in wanted]
        return SimpleNamespace(all=lambda: rows)


def _members(monkeypatch: pytest.MonkeyPatch, *sample_ids: str) -> None:
    async def family_record(session: Any, family_id: str, user: Any) -> Any:
        return SimpleNamespace(members=[SimpleNamespace(sample_id=sample_id) for sample_id in sample_ids])

    monkeypatch.setattr(signal_tracks, "get_family_record", family_record)


def test_the_importer_records_the_package_relative_path_of_each_file_present(tmp_path: Path) -> None:
    depth = tmp_path / DEPTH
    depth.parent.mkdir(parents=True)
    depth.write_bytes(b"bigwig")
    paths = {"depth_bigwig": depth, "maf_bigwig": None, "copy_number_bedgraph": tmp_path / "cnv/HG002/gone.bedgraph"}
    bundle = FamilyPackageBundle(
        root=tmp_path,
        manifest=PackageManifest(schema_version=1),
        ped=ParsedPed(family_ids=["pacbio"], members=[], sample_ids=["HG002"], text=""),
        kept_manifest_text='{"schema_version": 1}',
    )

    assert family_package_datasets._signal_track_entry(bundle, paths) == {"depth_bigwig": DEPTH}


@pytest.mark.asyncio
async def test_the_endpoints_serve_the_recorded_file_with_its_size(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _members(monkeypatch, "HG002")
    session: Any = _RowsSession({"HG002": {"hificnv": {"depth_bigwig": DEPTH}}})

    entries = await signal_tracks.get_signal_track_manifest("pacbio", sample_ids=["HG002"], session=session, user=object())
    response = await signal_tracks.get_signal_track("pacbio", "HG002", "hificnv", "depth_bigwig", session=session, user=object())
    head = await signal_tracks.head_signal_track("pacbio", "HG002", "hificnv", "depth_bigwig", session=session, user=object())

    assert [entry.url for entry in entries] == ["/signal-tracks/pacbio/HG002/hificnv/depth_bigwig"]
    assert Path(response.path) == data_root / "families/pacbio" / DEPTH
    # A HEAD answers the headers the GET would send: a client sizes the file before it ranges.
    assert head.status_code == 200
    assert head.headers["content-length"] == str(len(b"bigwig"))
    assert head.headers["accept-ranges"] == "bytes"
    # The family's package folder is read with each sample's row, from the sample's family.
    assert "JOIN families f ON f.id = s.family_id" in session.sql[0]
