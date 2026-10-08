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
                ),
                # No usable entries at all: must not appear.
                ("OTHER", {"hificnv": {"unknown_kind": "x"}}),
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
# Remote mode (STORAGE_BACKEND=gcs/s3): a package imported from a bucket
# ---------------------------------------------------------------------------
#
# The staged copies of a bucket package are deleted after the import, so the signal
# files are served from their objects: the location the import recorded first -- under
# the CRAM endpoint's rules, so a tampered row cannot get any other object signed --
# then the package layout under the storage prefix, as the CRAM probes look.

from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services import family_package_datasets  # noqa: E402
from app.services.family_package_common import FamilyPackageBundle, PackageManifest, ParsedPed  # noqa: E402
from backend.tests._object_store_fakes import BUCKET, use_store  # noqa: E402

DEPTH = "cnv/HG002/HG002.Sample0.depth.bw"
DEPTH_KEY = f"imports/pacbio/{DEPTH}"


def _recorded(uri: Any = f"gs://{BUCKET}/{DEPTH_KEY}", path: str = DEPTH) -> dict[str, Any]:
    """``samples.metadata['signal_tracks']`` as the importer writes it for a bucket package."""
    return {"hificnv": {"depth_bigwig": path, "uris": {"depth_bigwig": uri}}}


class _RowsSession:
    def __init__(self, rows: dict[str, Any]) -> None:
        self.rows = rows

    async def execute(self, statement: Any, params: Any = None) -> Any:
        wanted = set(params["sample_ids"])
        rows = [(sample_id, value) for sample_id, value in self.rows.items() if sample_id in wanted]
        return SimpleNamespace(all=lambda: rows)


def _members(monkeypatch: pytest.MonkeyPatch, *sample_ids: str) -> None:
    async def family_record(session: Any, family_id: str, user: Any) -> Any:
        return SimpleNamespace(members=[SimpleNamespace(sample_id=sample_id) for sample_id in sample_ids])

    monkeypatch.setattr(signal_tracks, "get_family_record", family_record)


def _sign(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    signed: list[str] = []

    def presign(key: str, *, filename: str | None = None, expires: int | None = None) -> str:
        signed.append(key)
        return f"https://signed.example/{key}"

    monkeypatch.setattr(signal_tracks, "presigned_get_url", presign)
    return signed


def test_the_importer_records_the_objects_of_a_bucket_package(tmp_path: Path) -> None:
    depth = tmp_path / DEPTH
    depth.parent.mkdir(parents=True)
    depth.write_bytes(b"bigwig")
    paths = {"depth_bigwig": depth, "maf_bigwig": None, "copy_number_bedgraph": tmp_path / "cnv/HG002/gone.bedgraph"}

    def bundle(source_uri: str | None) -> FamilyPackageBundle:
        return FamilyPackageBundle(
            root=tmp_path,
            manifest_path=tmp_path / "manifest.yaml",
            manifest=PackageManifest(schema_version=1, ped="family.ped"),
            ped_path=tmp_path / "family.ped",
            ped=ParsedPed(family_ids=["pacbio"], members=[], sample_ids=["HG002"], text=""),
            source_uri=source_uri,
        )

    assert family_package_datasets._signal_track_entry(bundle(f"gs://{BUCKET}/imports/pacbio"), paths) == {
        "depth_bigwig": DEPTH,
        "uris": {"depth_bigwig": f"gs://{BUCKET}/{DEPTH_KEY}"},
    }
    # A local package records the relative path only, as before.
    assert family_package_datasets._signal_track_entry(bundle(None), paths) == {"depth_bigwig": DEPTH}


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["gs", "s3"])
async def test_the_remote_manifest_signs_the_recorded_object(
    monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    use_store(monkeypatch, scheme, {DEPTH_KEY: "bigwig"})
    _members(monkeypatch, "HG002")
    _sign(monkeypatch)
    session: Any = _RowsSession({"HG002": _recorded(uri=f"{scheme}://{BUCKET}/{DEPTH_KEY}")})

    entries = await signal_tracks.get_signal_track_manifest("pacbio", sample_ids=["HG002"], session=session, user=object())

    assert [(entry.sample_id, entry.kind, entry.url) for entry in entries] == [
        ("HG002", "depth_bigwig", f"https://signed.example/{DEPTH_KEY}")
    ]


@pytest.mark.asyncio
async def test_remote_get_redirects_and_head_reports_the_objects_size(monkeypatch: pytest.MonkeyPatch) -> None:
    use_store(monkeypatch, "gs", {DEPTH_KEY: "bigwig"})
    _members(monkeypatch, "HG002")
    _sign(monkeypatch)
    session: Any = _RowsSession({"HG002": _recorded()})

    response = await signal_tracks.get_signal_track(
        "pacbio", "HG002", "hificnv", "depth_bigwig", session=session, user=object()
    )
    head = await signal_tracks.head_signal_track(
        "pacbio", "HG002", "hificnv", "depth_bigwig", session=session, user=object()
    )

    assert response.status_code == 302
    assert response.headers["location"] == f"https://signed.example/{DEPTH_KEY}"
    # A HEAD cannot follow a URL signed for GET, so it answers the size itself.
    assert head.status_code == 200
    assert head.headers["content-length"] == str(len(b"bigwig"))
    assert head.headers["accept-ranges"] == "bytes"


# Recorded locations a tampered or stale row could carry; the bucket holds an object at
# each one's key, so only the check on the location keeps it from being signed.
UNTRUSTED_TRACK_LOCATIONS = {
    "outside FAMILY_IMPORT_ROOTS": "gs://phi/elsewhere/HG002.depth.bw",
    "another bucket": f"gs://other-bucket/{DEPTH_KEY}",
    "another store's scheme": f"s3://{BUCKET}/{DEPTH_KEY}",
    "dot segments": "gs://phi/imports/pacbio/../../secrets/x.bw",
    "a query string": f"gs://{BUCKET}/{DEPTH_KEY}?generation=1",
    "an extension that is not the kind's": "gs://phi/imports/pacbio/cnv/HG002/HG002.copynum.bedgraph",
    "not a URI": 42,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("uri", UNTRUSTED_TRACK_LOCATIONS.values(), ids=UNTRUSTED_TRACK_LOCATIONS.keys())
async def test_an_untrusted_recorded_signal_track_is_never_signed(
    monkeypatch: pytest.MonkeyPatch, uri: Any
) -> None:
    from app.core import object_storage

    key = object_storage.parse_remote_uri(uri).key if isinstance(uri, str) else DEPTH_KEY
    use_store(monkeypatch, "gs", {key: "bigwig"})
    _members(monkeypatch, "HG002")
    signed = _sign(monkeypatch)
    session: Any = _RowsSession({"HG002": _recorded(uri=uri)})

    assert await signal_tracks.get_signal_track_manifest("pacbio", sample_ids=["HG002"], session=session, user=object()) == []
    with pytest.raises(HTTPException) as exc:
        await signal_tracks.get_signal_track("pacbio", "HG002", "hificnv", "depth_bigwig", session=session, user=object())
    assert exc.value.status_code == 404
    assert signed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "imports"])
async def test_a_track_recorded_without_a_uri_is_found_at_the_package_layout(
    monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    # A row written before bucket imports recorded URIs: the probe looks where the CRAM
    # probes look, <storage prefix>/<family>/..., at the recorded relative path.
    probe_key = "/".join(part for part in (prefix, "pacbio", DEPTH) if part)
    use_store(monkeypatch, "gs", {probe_key: "bigwig"})
    monkeypatch.setattr(settings, "gcs_prefix", prefix)
    _members(monkeypatch, "HG002")
    _sign(monkeypatch)
    session: Any = _RowsSession({"HG002": {"hificnv": {"depth_bigwig": DEPTH}}})

    entries = await signal_tracks.get_signal_track_manifest("pacbio", sample_ids=["HG002"], session=session, user=object())

    assert [entry.url for entry in entries] == [f"https://signed.example/{probe_key}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../other-family/x.bw", "cnv/../../other-family/x.bw", "/abs/x.bw", "cnv//x.bw"])
async def test_the_layout_probe_stays_in_the_family_folder(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    use_store(monkeypatch, "gs", {f"pacbio/{path.lstrip('/')}": "bigwig", "other-family/x.bw": "bigwig"})
    _members(monkeypatch, "HG002")
    signed = _sign(monkeypatch)
    session: Any = _RowsSession({"HG002": {"hificnv": {"depth_bigwig": path}}})

    assert await signal_tracks.get_signal_track_manifest("pacbio", sample_ids=["HG002"], session=session, user=object()) == []
    assert signed == []


@pytest.mark.asyncio
async def test_local_mode_still_serves_the_data_directory(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "storage_backend", "local")
    _members(monkeypatch, "HG002")
    session: Any = _RowsSession({"HG002": _recorded()})

    entries = await signal_tracks.get_signal_track_manifest("pacbio", sample_ids=["HG002"], session=session, user=object())
    response = await signal_tracks.get_signal_track("pacbio", "HG002", "hificnv", "depth_bigwig", session=session, user=object())

    assert [entry.url for entry in entries] == ["/signal-tracks/pacbio/HG002/hificnv/depth_bigwig"]
    assert Path(response.path) == data_root / "families/pacbio" / DEPTH
