"""What each of a sample's coverage tracks holds, and which one is read as depth (CLIN-46, TRACE-9).

A sample's ``coverage`` track can come from several callers at once, each stored under its own
source: a coverage BED (``web``: a BED upload's, or a package's ``coverage`` dataset's),
WisecondorX's and QDNAseq's bins, and HiFiCNV's depth. They do not hold one quantity. What each
holds -- read depth, a log2 ratio, or unknown -- is decided here, by one rule, for the readers
that take coverage as read depth and for the views that draw it (``coverage_contents`` in the
track availability):

* A coverage BED holds what it was declared to hold when it was loaded (TRACE-9): the BED
  upload's ``content`` and the ``content`` key of the package manifest's coverage entry, which
  its record keeps (``content`` in ``sample_interval_track_sources.metadata``): ``read_depth``
  or ``log2_ratio``. A BED that declares neither -- a manifest entry without the key, or a track
  stored before BEDs declared it -- holds values of unknown kind. It used to be read as depth
  (the importer called the file a BED of depths) while the views drew it on their log2-ratio
  axis.
* Another caller's track holds what its import's transform made of its file
  (``normalization``, TRACE-7): read depth after ``none`` alone (HiFiCNV's depths stored as
  read, its file having given no usable autosomal median), a log2 ratio after a log2-ratio
  transform, and values of unknown kind after a transform not known here or two that
  disagree. A track whose records name no transform holds its file's values: WisecondorX's
  and QDNAseq's are log2 ratios, any other caller's are of unknown kind until its import
  records what they are.

The readers that take coverage values as read depth (the mtDNA coverage summary with its
``mtdna.mean_depth`` verdict, and the monogenic NIPT coverage check without a per-target table)
read one track per sample, one that holds read depth, chosen here; a sample without one has no
coverage track to them, and each of its tracks they leave out is named with the reason
(``not_read_as_depth_note``). They read every source's rows merged before (CLIN-46): a log2
ratio near 0 pulled a chrM mean depth of hundreds down to half of it or less, and a configured
limit warned or failed on it. One track, not every depth track: two tracks' rows over a region
count its bases twice, which can hide an uncovered stretch from a completeness check, and their
mean or median is neither track's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from . import clickhouse_interval_tracks
from .bed_service import BED_UPLOAD_SOURCE
from .coverage_content import (
    DECLARED_COVERAGE_CONTENTS,
    LOG2_RATIO,
    READ_DEPTH,
    UNKNOWN_CONTENT,
    CoverageContent,
    DeclaredCoverageContent,
    declared_coverage_content,
)

__all__ = [
    "COVERAGE_TRACK_TYPE",
    "DECLARED_COVERAGE_CONTENTS",
    "DEPTH_STORED_AS_READ",
    "LOG2_RATIO",
    "READ_DEPTH",
    "UNKNOWN_CONTENT",
    "CoverageContent",
    "DeclaredCoverageContent",
    "SampleCoverageTracks",
    "coverage_contents",
    "coverage_track_content",
    "coverage_track_label",
    "coverage_tracks_by_sample",
    "declared_coverage_content",
    "holds_read_depth",
    "not_read_as_depth_note",
    "read_depth_source",
    "read_depth_sources_by_sample",
]

COVERAGE_TRACK_TYPE = "coverage"

#: The transform a coverage track's record names when its values are its file's read depths,
#: stored as read: a HiFiCNV depth file without a usable autosomal median (TRACE-7).
DEPTH_STORED_AS_READ = "none"

#: The transforms after which a track's values are a log2 ratio (TRACE-7, #812).
_LOG2_RATIO_TRANSFORMS = frozenset({"log2_ratio_to_autosomal_median", "log2_ratio_to_diploid"})

#: The callers whose coverage files hold log2 ratios, stored as their files hold them.
_LOG2_RATIO_CALLERS = frozenset({"wisecondorx", "qdnaseq"})

#: How a note names a caller's coverage track.
_TRACK_LABELS = {
    BED_UPLOAD_SOURCE: "coverage BED",
    "wisecondorx": "WisecondorX coverage",
    "qdnaseq": "QDNAseq coverage",
    "hificnv": "HiFiCNV coverage",
}


def coverage_track_content(
    source: str, normalizations: Sequence[str] = (), contents: Sequence[str] = ()
) -> CoverageContent:
    """What a coverage track's stored values are: ``read_depth``, ``log2_ratio`` or
    ``unknown``, from its source, the transforms its records name
    (``interval_track_normalizations_by_sample``) and, for a coverage BED, what its records
    declare (``interval_track_contents_by_sample``). See the module docstring for the rule."""
    if source == BED_UPLOAD_SOURCE:
        declared = set(contents)
        # Undeclared (a manifest entry without the key, a track stored before BEDs declared
        # it), declared unknown, or two records that disagree: unknown.
        return declared_coverage_content(declared.pop()) if len(declared) == 1 else UNKNOWN_CONTENT
    named = set(normalizations)
    if named:
        if named == {DEPTH_STORED_AS_READ}:
            return READ_DEPTH
        if named <= _LOG2_RATIO_TRANSFORMS:
            return LOG2_RATIO
        return UNKNOWN_CONTENT
    return LOG2_RATIO if source in _LOG2_RATIO_CALLERS else UNKNOWN_CONTENT


def holds_read_depth(
    source: str, normalizations: Sequence[str] = (), contents: Sequence[str] = ()
) -> bool:
    """Whether a coverage track's stored values are read depth (``coverage_track_content``)."""
    return coverage_track_content(source, normalizations, contents) == READ_DEPTH


def coverage_contents(
    sources: Sequence[str],
    normalizations: Mapping[str, Sequence[str]],
    contents: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, CoverageContent]:
    """Each source's track content (``coverage_track_content``), for the sources that have
    rows, from the transforms (``normalizations``) and declarations (``contents``) each
    source's records name."""
    declared = contents or {}
    return {
        source: coverage_track_content(
            source, normalizations.get(source, ()), declared.get(source, ())
        )
        for source in sorted(set(sources))
    }


def read_depth_source(
    sources: Sequence[str],
    normalizations: Mapping[str, Sequence[str]],
    contents: Mapping[str, Sequence[str]] | None = None,
) -> str | None:
    """The one coverage track of a sample to read as its read depth, from the sources that have
    rows (``sources``) and what each source's records name (``normalizations``, ``contents``):
    the coverage BED when it holds depth, otherwise the first other source in name order that
    does; None when none holds read depth."""
    return _depth_source(coverage_contents(sources, normalizations, contents))


def _depth_source(contents: Mapping[str, CoverageContent]) -> str | None:
    depth_sources = sorted(source for source, content in contents.items() if content == READ_DEPTH)
    if not depth_sources:
        return None
    return BED_UPLOAD_SOURCE if BED_UPLOAD_SOURCE in depth_sources else depth_sources[0]


@dataclass(frozen=True)
class SampleCoverageTracks:
    """A sample's coverage tracks on the chromosomes read: what each source's holds
    (``contents``), and the one a depth reader reads (``depth_source``, None without one)."""

    contents: dict[str, CoverageContent] = field(default_factory=dict)

    @property
    def depth_source(self) -> str | None:
        return _depth_source(self.contents)

    def not_read_as_depth(self) -> list[tuple[str, CoverageContent]]:
        """Each (source, content) of a track that does not hold read depth, in name order: a
        depth reader leaves it out, and says so (``not_read_as_depth_note``)."""
        return [
            (source, content)
            for source, content in sorted(self.contents.items())
            if content != READ_DEPTH
        ]


def coverage_track_label(source: str) -> str:
    """How a note names a caller's coverage track: ``coverage BED``, ``WisecondorX coverage``,
    or a caller not known here by its source."""
    return _TRACK_LABELS.get(source, f"{source} coverage")


def not_read_as_depth_note(sample_id: str, source: str, content: str) -> str:
    """Why a depth reader left a sample's coverage track out, as a note the page shows."""
    track = coverage_track_label(source)
    if content == LOG2_RATIO:
        return f"{sample_id}: the {track} holds log2 ratios, not read depth; it is not read as depth."
    if source == BED_UPLOAD_SOURCE:
        return (
            f"{sample_id}: the coverage BED does not declare whether it holds read depth or a "
            "log2 ratio; it is not read as depth."
        )
    return f"{sample_id}: what the {track} holds is not recorded; it is not read as depth."


async def coverage_tracks_by_sample(
    session: AsyncSession,
    *,
    assembly_name: str,
    family_uuid: str,
    sample_uuid_to_name: dict[str, str],
    chromosomes: Sequence[str],
) -> dict[str, SampleCoverageTracks]:
    """Sample name → its coverage tracks with rows on ``chromosomes`` and what each holds; a
    sample without one is left out.

    The sources are those with rows on these chromosomes, as stored in ClickHouse; what each
    holds is read from their records in Postgres (``coverage_track_content``). A track with rows
    and no record names nothing: a coverage BED's is then unknown, and so is any other caller's
    but WisecondorX's and QDNAseq's log2 ratios.
    """
    sources_by_sample = await clickhouse_interval_tracks.get_interval_track_sources_by_sample(
        assembly_name,
        family_uuid=family_uuid,
        sample_uuid_to_name=sample_uuid_to_name,
        track_type=COVERAGE_TRACK_TYPE,
        chromosomes=chromosomes,
    )
    if not any(sources_by_sample.values()):
        return {}
    normalizations = await clickhouse_interval_tracks.interval_track_normalizations_by_sample(
        session,
        family_uuid=family_uuid,
        sample_uuid_to_name=sample_uuid_to_name,
        track_type=COVERAGE_TRACK_TYPE,
    )
    declared = await clickhouse_interval_tracks.interval_track_contents_by_sample(
        session,
        family_uuid=family_uuid,
        sample_uuid_to_name=sample_uuid_to_name,
        track_type=COVERAGE_TRACK_TYPE,
    )
    return {
        sample_name: SampleCoverageTracks(
            contents=coverage_contents(
                sources, normalizations.get(sample_name, {}), declared.get(sample_name, {})
            )
        )
        for sample_name, sources in sources_by_sample.items()
        if sources
    }


async def read_depth_sources_by_sample(
    session: AsyncSession,
    *,
    assembly_name: str,
    family_uuid: str,
    sample_uuid_to_name: dict[str, str],
    chromosomes: Sequence[str],
) -> dict[str, str]:
    """Sample name → the source of its coverage track to read as read depth on
    ``chromosomes`` (``coverage_tracks_by_sample``); a sample without one is left out."""
    tracks = await coverage_tracks_by_sample(
        session,
        assembly_name=assembly_name,
        family_uuid=family_uuid,
        sample_uuid_to_name=sample_uuid_to_name,
        chromosomes=chromosomes,
    )
    return {
        sample_name: source
        for sample_name, sample_tracks in tracks.items()
        if (source := sample_tracks.depth_source) is not None
    }
