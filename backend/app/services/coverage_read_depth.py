"""Which of a sample's coverage tracks hold read depth (CLIN-46).

A sample's ``coverage`` track can come from several callers at once, each stored under its own
source: a coverage BED (``web``: a BED upload's, or a package's ``coverage`` dataset's),
WisecondorX's and QDNAseq's bins, and HiFiCNV's depth. They do not hold one quantity. The bins
of WisecondorX and QDNAseq are their files' log2 ratios. HiFiCNV's depth is stored as
log2(depth / the sample's autosomal median), or, when its file gave no usable median, as the
depths it read; its track's record names which (``normalization`` in
``sample_interval_track_sources.metadata``: ``log2_ratio_to_autosomal_median`` or ``none``,
TRACE-7). A coverage BED records no transform, and its values are taken for depths, as the
importer calls the file a BED of depths (what a coverage BED holds is TRACE-9's open question).

The readers that take coverage values as read depth (the mtDNA coverage summary with its
``mtdna.mean_depth`` verdict, and the monogenic NIPT coverage check without a per-target table)
read one track per sample, one whose values are read depth, chosen here; a sample without one
has no coverage track to them. They read every source's rows merged before (CLIN-46): a log2
ratio near 0 pulled a chrM mean depth of hundreds down to half of it or less, and a configured
limit warned or failed on it. One track, not every depth track: two tracks' rows over a region
count its bases twice, which can hide an uncovered stretch from a completeness check, and their
mean or median is neither track's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from . import clickhouse_interval_tracks
from .bed_service import BED_UPLOAD_SOURCE

COVERAGE_TRACK_TYPE = "coverage"

#: The transform a coverage track's record names when its values are its file's read depths,
#: stored as read: a HiFiCNV depth file without a usable autosomal median (TRACE-7).
DEPTH_STORED_AS_READ = "none"


def holds_read_depth(source: str, normalizations: Sequence[str]) -> bool:
    """Whether a coverage track's stored values are read depth, from its source and the
    transforms its records name (``interval_track_normalizations_by_sample``).

    Transforms named decide: the values are read depth when the transforms named are ``none``
    alone, and are not after a log2-ratio transform, a transform this module does not know, or
    two that disagree. A track whose records name no transform holds its file's values: depths
    for a coverage BED (``web``), log2 ratios for WisecondorX and QDNAseq, and unknown for any
    other caller, which is therefore not read as depth until its import records that its values
    are.
    """
    if normalizations:
        return set(normalizations) == {DEPTH_STORED_AS_READ}
    return source == BED_UPLOAD_SOURCE


def read_depth_source(
    sources: Sequence[str], normalizations: Mapping[str, Sequence[str]]
) -> str | None:
    """The one coverage track of a sample to read as its read depth, from the sources that have
    rows (``sources``) and the transforms each source's records name (``normalizations``):
    the coverage BED when its values are depth, otherwise the first other source in name order
    whose values are; None when none holds read depth."""
    depth_sources = sorted(
        source for source in set(sources) if holds_read_depth(source, normalizations.get(source, ()))
    )
    if not depth_sources:
        return None
    return BED_UPLOAD_SOURCE if BED_UPLOAD_SOURCE in depth_sources else depth_sources[0]


async def read_depth_sources_by_sample(
    session: AsyncSession,
    *,
    assembly_name: str,
    family_uuid: str,
    sample_uuid_to_name: dict[str, str],
    chromosomes: Sequence[str],
) -> dict[str, str]:
    """Sample name → the source of its coverage track to read as read depth on
    ``chromosomes`` (``read_depth_source``); a sample without one is left out.

    The sources are those with rows on these chromosomes, as stored in ClickHouse; the
    transforms are those their records in Postgres name. A track with rows and no record
    names no transform: a coverage BED's is read as depth, any other caller's is not.
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
    chosen: dict[str, str] = {}
    for sample_name, sources in sources_by_sample.items():
        source = read_depth_source(sources, normalizations.get(sample_name, {}))
        if source is not None:
            chosen[sample_name] = source
    return chosen
