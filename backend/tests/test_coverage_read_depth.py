"""What each coverage track holds, and which one a depth reader takes (CLIN-46, TRACE-9).

``coverage_read_depth`` decides it from a track's source and its records
(``sample_interval_track_sources.metadata``): a coverage BED (``web``) holds what it declared when
it was loaded (``content``: ``read_depth`` or ``log2_ratio``), and values of unknown kind when it
declared nothing -- it used to be read as depth whatever it held, while the views drew it as a log2
ratio (TRACE-9). Another caller's track holds what its import's transform made of its file
(``normalization``): HiFiCNV's depth is depth only when its records name ``none`` (stored as read);
WisecondorX's and QDNAseq's bins, which record none, are log2 ratios; any caller's track this module
does not know is of unknown kind until its import records what it is. A depth reader reads one track
of read depth per sample and names each track it leaves out, with the reason.
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.services import clickhouse_interval_tracks, coverage_read_depth
from backend.app.services.bed_service import BED_UPLOAD_SOURCE
from backend.app.services.coverage_content import declared_coverage_content
from backend.app.services.coverage_read_depth import (
    SampleCoverageTracks,
    coverage_contents,
    coverage_track_content,
    holds_read_depth,
    not_read_as_depth_note,
    read_depth_source,
)

LOG2_RATIO = "log2_ratio_to_autosomal_median"


def test_a_coverage_bed_is_the_source_a_bed_is_stored_under() -> None:
    # The upload and a package's coverage dataset store a BED under this source.
    assert BED_UPLOAD_SOURCE == "web"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("read_depth", "read_depth"),
        ("log2_ratio", "log2_ratio"),
        # A manifest entry without the key declares nothing: unknown, never a default kind.
        (None, "unknown"),
        ("unknown", "unknown"),
        ("depth", "unknown"),
        (3, "unknown"),
    ],
)
def test_a_declaration_is_read_depth_or_a_log2_ratio_or_unknown(value: object, expected: str) -> None:
    assert declared_coverage_content(value) == expected


@pytest.mark.parametrize(
    ("source", "normalizations", "contents", "expected"),
    [
        # A coverage BED holds what it declared (TRACE-9) ...
        ("web", [], ["read_depth"], "read_depth"),
        ("web", [], ["log2_ratio"], "log2_ratio"),
        # ... and, declaring nothing, values of unknown kind: before, it was read as depth.
        ("web", [], [], "unknown"),
        ("web", [], ["unknown"], "unknown"),
        # Two records that disagree, or a declaration not known here: unknown.
        ("web", [], ["log2_ratio", "read_depth"], "unknown"),
        ("web", [], ["counts"], "unknown"),
        # A transform its record might name declares nothing of a BED.
        ("web", ["none"], [], "unknown"),
        # HiFiCNV's depth: stored as read, or as a log2 ratio to the autosomal median.
        ("hificnv", ["none"], [], "read_depth"),
        ("hificnv", [LOG2_RATIO], [], "log2_ratio"),
        # Two records that disagree: its rows are not all of one kind.
        ("hificnv", [LOG2_RATIO, "none"], [], "unknown"),
        # No record names a transform: not known, so not read as depth.
        ("hificnv", [], [], "unknown"),
        # Only a BED declares what it holds.
        ("hificnv", [], ["read_depth"], "unknown"),
        # WisecondorX's and QDNAseq's bins record none and hold their files' log2 ratios.
        ("wisecondorx", [], [], "log2_ratio"),
        ("qdnaseq", [], [], "log2_ratio"),
        # A caller this module does not know holds what its record says, else unknown.
        ("new_caller", [], [], "unknown"),
        ("new_caller", ["none"], [], "read_depth"),
        ("new_caller", ["log2_ratio_to_diploid"], [], "log2_ratio"),
        ("new_caller", ["counts_per_million"], [], "unknown"),
    ],
)
def test_what_a_track_holds_by_its_source_and_its_records(
    source: str, normalizations: list[str], contents: list[str], expected: str
) -> None:
    assert coverage_track_content(source, normalizations, contents) == expected
    assert holds_read_depth(source, normalizations, contents) is (expected == "read_depth")


@pytest.mark.parametrize(
    ("sources", "normalizations", "contents", "expected"),
    [
        # Every caller's track: the BED declared as depth, not the bins or HiFiCNV's log2 ratio.
        (["hificnv", "qdnaseq", "web", "wisecondorx"], {"hificnv": [LOG2_RATIO]}, {"web": ["read_depth"]}, "web"),
        # Two tracks of depth: the coverage BED, never both.
        (["hificnv", "web"], {"hificnv": ["none"]}, {"web": ["read_depth"]}, "web"),
        # A BED that declares nothing is not depth: HiFiCNV's depths stored as read are read.
        (["hificnv", "web"], {"hificnv": ["none"]}, {}, "hificnv"),
        # A BED declared as a log2 ratio, or declaring nothing, alone: no depth (TRACE-9).
        (["web"], {}, {"web": ["log2_ratio"]}, None),
        (["web"], {}, {}, None),
        # No BED: HiFiCNV's depths stored as read.
        (["hificnv", "qdnaseq", "wisecondorx"], {"hificnv": ["none"]}, {}, "hificnv"),
        # Two other tracks of depth: the first by name, so the choice does not vary.
        (["zeta", "alpha", "wisecondorx"], {"zeta": ["none"], "alpha": ["none"]}, {}, "alpha"),
        # Only log2 ratios: no depth.
        (["hificnv", "wisecondorx"], {"hificnv": [LOG2_RATIO]}, {}, None),
        (["wisecondorx", "qdnaseq"], {}, {}, None),
        ([], {}, {}, None),
    ],
)
def test_a_sample_is_read_from_one_track_of_read_depth(
    sources: list[str],
    normalizations: dict[str, list[str]],
    contents: dict[str, list[str]],
    expected: str | None,
) -> None:
    assert read_depth_source(sources, normalizations, contents) == expected
    assert SampleCoverageTracks(coverage_contents(sources, normalizations, contents)).depth_source == expected


def test_each_track_a_depth_reader_leaves_out_is_named_with_its_reason() -> None:
    tracks = SampleCoverageTracks(
        coverage_contents(
            ["web", "wisecondorx", "hificnv", "new_caller"],
            {"hificnv": ["none"]},
            {},
        )
    )

    assert tracks.depth_source == "hificnv"
    assert tracks.not_read_as_depth() == [
        ("new_caller", "unknown"),
        ("web", "unknown"),
        ("wisecondorx", "log2_ratio"),
    ]
    assert [not_read_as_depth_note("S1", source, content) for source, content in tracks.not_read_as_depth()] == [
        "S1: what the new_caller coverage holds is not recorded; it is not read as depth.",
        "S1: the coverage BED does not declare whether it holds read depth or a log2 ratio; "
        "it is not read as depth.",
        "S1: the WisecondorX coverage holds log2 ratios, not read depth; it is not read as depth.",
    ]
    assert not_read_as_depth_note("S1", "web", "log2_ratio") == (
        "S1: the coverage BED holds log2 ratios, not read depth; it is not read as depth."
    )


@pytest.mark.asyncio
async def test_each_samples_depth_track_is_chosen_from_its_stored_sources_and_their_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: dict[str, object] = {}

    async def fake_sources(assembly_name, *, family_uuid, sample_uuid_to_name, track_type, chromosomes):
        asked["sources"] = (assembly_name, family_uuid, track_type, tuple(chromosomes))
        return {
            "PROBAND": ["web", "wisecondorx"],
            "MOTHER": ["hificnv"],
            "FATHER": ["hificnv", "qdnaseq"],
            "SIBLING": ["wisecondorx"],
            "AUNT": ["web"],
        }

    async def fake_normalizations(session, *, family_uuid, sample_uuid_to_name, track_type):
        asked["normalizations"] = (session, family_uuid, track_type)
        return {"MOTHER": {"hificnv": [LOG2_RATIO]}, "FATHER": {"hificnv": ["none"]}}

    async def fake_contents(session, *, family_uuid, sample_uuid_to_name, track_type):
        asked["contents"] = (session, family_uuid, track_type)
        # The proband's BED was declared as read depth; the aunt's declared nothing.
        return {"PROBAND": {"web": ["read_depth"]}}

    monkeypatch.setattr(clickhouse_interval_tracks, "get_interval_track_sources_by_sample", fake_sources)
    monkeypatch.setattr(clickhouse_interval_tracks, "interval_track_normalizations_by_sample", fake_normalizations)
    monkeypatch.setattr(clickhouse_interval_tracks, "interval_track_contents_by_sample", fake_contents)
    session = object()
    names = {"u1": "PROBAND", "u2": "MOTHER", "u3": "FATHER", "u4": "SIBLING", "u5": "AUNT"}

    chosen = await coverage_read_depth.read_depth_sources_by_sample(
        session,  # type: ignore[arg-type]
        assembly_name="GRCh38",
        family_uuid="family-uuid",
        sample_uuid_to_name=names,
        chromosomes=["MT", "chrM"],
    )

    # The mother's and the sibling's only tracks are log2 ratios, the aunt's an undeclared BED:
    # they have none.
    assert chosen == {"PROBAND": "web", "FATHER": "hificnv"}
    assert asked == {
        "sources": ("GRCh38", "family-uuid", "coverage", ("MT", "chrM")),
        "normalizations": (session, "family-uuid", "coverage"),
        "contents": (session, "family-uuid", "coverage"),
    }

    tracks = await coverage_read_depth.coverage_tracks_by_sample(
        session,  # type: ignore[arg-type]
        assembly_name="GRCh38",
        family_uuid="family-uuid",
        sample_uuid_to_name=names,
        chromosomes=["MT", "chrM"],
    )
    assert {name: sample.not_read_as_depth() for name, sample in tracks.items()} == {
        "PROBAND": [("wisecondorx", "log2_ratio")],
        "MOTHER": [("hificnv", "log2_ratio")],
        "FATHER": [("qdnaseq", "log2_ratio")],
        "SIBLING": [("wisecondorx", "log2_ratio")],
        "AUNT": [("web", "unknown")],
    }


@pytest.mark.asyncio
async def test_samples_without_coverage_need_no_record_read(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sources(*_args, **_kwargs):
        return {}

    async def records_not_read(*_args, **_kwargs):
        raise AssertionError("no coverage track: the records are not read")

    monkeypatch.setattr(clickhouse_interval_tracks, "get_interval_track_sources_by_sample", fake_sources)
    monkeypatch.setattr(clickhouse_interval_tracks, "interval_track_normalizations_by_sample", records_not_read)
    monkeypatch.setattr(clickhouse_interval_tracks, "interval_track_contents_by_sample", records_not_read)

    assert (
        await coverage_read_depth.read_depth_sources_by_sample(
            None,  # type: ignore[arg-type]
            assembly_name="GRCh38",
            family_uuid="family-uuid",
            sample_uuid_to_name={"u1": "PROBAND"},
            chromosomes=["MT"],
        )
        == {}
    )


class _RecordedSession:
    """A session that answers the record read with ``rows`` and keeps what it was asked."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.asked: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement, params):
        self.asked.append((str(statement), params))
        rows = self.rows

        class _Result:
            def mappings(self):
                return self

            def all(self):
                return rows

        return _Result()


@pytest.mark.asyncio
async def test_what_a_bed_declared_is_read_from_its_records_content() -> None:
    proband, mother, other = (str(uuid.UUID(int=number)) for number in (1, 2, 3))
    session = _RecordedSession(
        [
            {"sample_uuid": proband, "source": "web", "value": "read_depth"},
            {"sample_uuid": mother, "source": "web", "value": "log2_ratio"},
            {"sample_uuid": mother, "source": "web", "value": "read_depth"},
            {"sample_uuid": other, "source": "web", "value": "read_depth"},
        ]
    )

    declared = await clickhouse_interval_tracks.interval_track_contents_by_sample(
        session,  # type: ignore[arg-type]
        family_uuid=str(uuid.UUID(int=9)),
        sample_uuid_to_name={proband: "PROBAND", mother: "MOTHER"},
        track_type="coverage",
    )

    # Two records of one track that disagree are both named, not one picked.
    assert declared == {"PROBAND": {"web": ["read_depth"]}, "MOTHER": {"web": ["log2_ratio", "read_depth"]}}
    [(sql, params)] = session.asked
    # The key is a bound value, never written into the statement.
    assert "metadata ->> CAST(:metadata_key AS text)" in sql
    assert params["metadata_key"] == "content" and params["track_type"] == "coverage"


@pytest.mark.asyncio
async def test_no_sample_needs_no_record_read() -> None:
    session = _RecordedSession([])

    assert (
        await clickhouse_interval_tracks.interval_track_contents_by_sample(
            session,  # type: ignore[arg-type]
            family_uuid="family-uuid",
            sample_uuid_to_name={},
            track_type="coverage",
        )
        == {}
    )
    assert session.asked == []
