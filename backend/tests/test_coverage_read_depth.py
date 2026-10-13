"""Which coverage tracks hold read depth, and which one a reader takes (CLIN-46).

``coverage_read_depth`` decides it from a track's source and the transforms its records name
(``sample_interval_track_sources.metadata.normalization``). A coverage BED (``web``) records
none and is read as depth; HiFiCNV's depth is depth only when its records name ``none`` (stored
as read); WisecondorX's and QDNAseq's bins, which record none, are log2 ratios, and so is any
caller's track this module does not know, until its import records that its values are depth.
"""

from __future__ import annotations

import pytest

from backend.app.services import clickhouse_interval_tracks, coverage_read_depth
from backend.app.services.bed_service import BED_UPLOAD_SOURCE
from backend.app.services.coverage_read_depth import holds_read_depth, read_depth_source

LOG2_RATIO = "log2_ratio_to_autosomal_median"


def test_a_coverage_bed_is_the_source_a_bed_is_stored_under() -> None:
    # The upload and a package's coverage dataset store a BED under this source.
    assert BED_UPLOAD_SOURCE == "web"


@pytest.mark.parametrize(
    ("source", "normalizations", "expected"),
    [
        # A coverage BED records no transform: a BED of depths, as the importer calls it (TRACE-9).
        ("web", [], True),
        ("web", ["none"], True),
        # Should a BED's record ever name a log2 ratio, or a transform not known here, it is not depth.
        ("web", [LOG2_RATIO], False),
        ("web", ["counts_per_million"], False),
        # HiFiCNV's depth: stored as read, or as a log2 ratio to the autosomal median.
        ("hificnv", ["none"], True),
        ("hificnv", [LOG2_RATIO], False),
        # Two records that disagree: its rows are not all depth.
        ("hificnv", [LOG2_RATIO, "none"], False),
        # No record names a transform: not known to be depth, so not read as it.
        ("hificnv", [], False),
        # WisecondorX's and QDNAseq's bins record none and hold their files' log2 ratios.
        ("wisecondorx", [], False),
        ("qdnaseq", [], False),
        # A caller this module does not know is not depth until its record says it is.
        ("new_caller", [], False),
        ("new_caller", ["none"], True),
        ("new_caller", ["log2_ratio_to_diploid"], False),
    ],
)
def test_a_tracks_values_are_read_depth_by_its_source_and_recorded_transform(
    source: str, normalizations: list[str], expected: bool
) -> None:
    assert holds_read_depth(source, normalizations) is expected


@pytest.mark.parametrize(
    ("sources", "normalizations", "expected"),
    [
        # Every caller's track: the BED's, not the bins' or HiFiCNV's log2 ratio.
        (["hificnv", "qdnaseq", "web", "wisecondorx"], {"hificnv": [LOG2_RATIO]}, "web"),
        # Two tracks of depth: the coverage BED, never both.
        (["hificnv", "web"], {"hificnv": ["none"]}, "web"),
        # No BED: HiFiCNV's depths stored as read.
        (["hificnv", "qdnaseq", "wisecondorx"], {"hificnv": ["none"]}, "hificnv"),
        # Two other tracks of depth: the first by name, so the choice does not vary.
        (["zeta", "alpha", "wisecondorx"], {"zeta": ["none"], "alpha": ["none"]}, "alpha"),
        # Only log2 ratios: no depth.
        (["hificnv", "wisecondorx"], {"hificnv": [LOG2_RATIO]}, None),
        (["wisecondorx", "qdnaseq"], {}, None),
        ([], {}, None),
    ],
)
def test_a_sample_is_read_from_one_track_of_read_depth(
    sources: list[str], normalizations: dict[str, list[str]], expected: str | None
) -> None:
    assert read_depth_source(sources, normalizations) == expected


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
        }

    async def fake_normalizations(session, *, family_uuid, sample_uuid_to_name, track_type):
        asked["normalizations"] = (session, family_uuid, track_type)
        return {"MOTHER": {"hificnv": [LOG2_RATIO]}, "FATHER": {"hificnv": ["none"]}}

    monkeypatch.setattr(clickhouse_interval_tracks, "get_interval_track_sources_by_sample", fake_sources)
    monkeypatch.setattr(clickhouse_interval_tracks, "interval_track_normalizations_by_sample", fake_normalizations)
    session = object()

    chosen = await coverage_read_depth.read_depth_sources_by_sample(
        session,  # type: ignore[arg-type]
        assembly_name="GRCh38",
        family_uuid="family-uuid",
        sample_uuid_to_name={"u1": "PROBAND", "u2": "MOTHER", "u3": "FATHER", "u4": "SIBLING"},
        chromosomes=["MT", "chrM"],
    )

    # The mother's and the sibling's only tracks are log2 ratios: they have none.
    assert chosen == {"PROBAND": "web", "FATHER": "hificnv"}
    assert asked == {
        "sources": ("GRCh38", "family-uuid", "coverage", ("MT", "chrM")),
        "normalizations": (session, "family-uuid", "coverage"),
    }


@pytest.mark.asyncio
async def test_samples_without_coverage_need_no_record_read(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sources(*_args, **_kwargs):
        return {}

    async def records_not_read(*_args, **_kwargs):
        raise AssertionError("no coverage track: the records are not read")

    monkeypatch.setattr(clickhouse_interval_tracks, "get_interval_track_sources_by_sample", fake_sources)
    monkeypatch.setattr(clickhouse_interval_tracks, "interval_track_normalizations_by_sample", records_not_read)

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
