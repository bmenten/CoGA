"""The readers that take coverage as read depth read only a track of read depth (CLIN-46).

A sample's ``coverage`` track can come from several callers, each under its own source: a
coverage BED (``web``), WisecondorX's and QDNAseq's bins and HiFiCNV's depth. The bins are log2
ratios, and HiFiCNV's depth is stored as a log2 ratio to the sample's autosomal median unless
its file gave none (its record then names ``none``: depths stored as read). The mtDNA coverage
summary, with its ``mtdna.mean_depth`` verdict (REQ-QC-006), and the monogenic NIPT coverage
check without a per-target table (REQ-NIPT-006) read every source's rows merged, as depth: a
log2 ratio near 0 halved a chrM mean depth, and a configured limit failed on it.

Here the coverage store is a fake that keeps each sample's rows by source and the transforms
each track's record names, and returns every source's rows merged when a reader names no
source, as ClickHouse does. The readers run through their public entries:

* the mtDNA summary reads the proband's coverage BED and not her WisecondorX bins, the father's
  HiFiCNV depths stored as read and not his QDNAseq bins, and for the mother, whose only track is
  HiFiCNV's log2 ratio, her calls' depth, as for a sample without a track; with a limit
  configured, every sample passes on its depth (before: each failed on a blend);
* the count-only presence finds no coverage in a family whose only chrM track is WisecondorX's
  bins, and does in one with a coverage BED;
* a mother whose only chrM track is WisecondorX's bins is not taken as sequenced: the proband's
  variant reads "mother not assessed" (before: "not seen in mother");
* the NIPT fallback reads the plasma's coverage BED, not its WisecondorX bins: the BED's median
  and its uncovered stretch (before: the bins' log2 ratio as the median, and their rows covering
  the stretch); with a HiFiCNV log2 track only, it reports no coverage (before: the log2 ratio as
  the median depth).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.schemas import FamilyMemberOut, FamilyOut, FamilyRegionOfInterestOut
from backend.app.services import clickhouse_interval_tracks, mitochondrial_analysis, nipt_service
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.data_scope import chromosome_aliases, normalize_chromosome
from backend.app.services.family_metadata_context import FamilyMetadataContext

LOG2_RATIO = "log2_ratio_to_autosomal_median"
STORED_AS_READ = "none"
MT_LENGTH = 16_569


class _CoverageStore:
    """Each sample's coverage rows by source, and the transforms each track's record names.

    ``tracks`` maps (sample uuid, source) to its rows as (chromosome, start, end, value);
    ``normalizations`` maps (sample name, source) to the transforms its records name, and a
    track without an entry records none (a coverage BED, WisecondorX's and QDNAseq's bins).
    """

    def __init__(
        self,
        tracks: dict[tuple[str, str], list[tuple[str, int, int, float]]],
        normalizations: dict[tuple[str, str], list[str]] | None = None,
    ) -> None:
        self.tracks = tracks
        self.normalizations = normalizations or {}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            clickhouse_interval_tracks, "get_interval_track_sources_by_sample", self.sources_by_sample
        )
        monkeypatch.setattr(
            clickhouse_interval_tracks, "interval_track_normalizations_by_sample", self.normalizations_by_sample
        )
        monkeypatch.setattr(mitochondrial_analysis, "fetch_interval_track_rows", self.fetch_rows)
        monkeypatch.setattr(nipt_service, "fetch_interval_track_rows", self.fetch_rows)

    @staticmethod
    def _wanted(chromosomes) -> set[str]:
        return {normalize_chromosome(alias) for chrom in chromosomes for alias in chromosome_aliases(chrom)}

    async def sources_by_sample(
        self, _assembly_name, *, family_uuid, sample_uuid_to_name, track_type, chromosomes, start=None, end=None
    ) -> dict[str, list[str]]:
        assert track_type == "coverage"
        wanted = self._wanted(chromosomes)
        found: dict[str, set[str]] = {}
        for (sample_uuid, source), rows in self.tracks.items():
            name = sample_uuid_to_name.get(sample_uuid)
            if name is not None and any(chrom in wanted for chrom, *_rest in rows):
                found.setdefault(name, set()).add(source)
        return {name: sorted(sources) for name, sources in found.items()}

    async def normalizations_by_sample(
        self, _session, *, family_uuid, sample_uuid_to_name, track_type
    ) -> dict[str, dict[str, list[str]]]:
        assert track_type == "coverage"
        names = set(sample_uuid_to_name.values())
        recorded: dict[str, dict[str, list[str]]] = {}
        for (name, source), transforms in self.normalizations.items():
            if name in names:
                recorded.setdefault(name, {})[source] = sorted(set(transforms))
        return recorded

    async def fetch_rows(
        self,
        _assembly_name,
        *,
        track_type,
        chromosomes,
        sample_uuid=None,
        sample_uuids=None,
        source=None,
        limit=None,
        **_kwargs,
    ) -> list[dict[str, Any]]:
        if track_type != "coverage":
            return []  # no per-target coverage table
        samples = {sample_uuid} if sample_uuid is not None else set(sample_uuids or ())
        wanted = self._wanted(chromosomes)
        rows = [
            {
                "sample_uuid": track_sample,
                "chr": chrom,
                "start": start,
                "end": end,
                "record_id": None,
                "value": value,
                "origin": None,
            }
            # Every source's rows when the reader names none, as the store returns them.
            for (track_sample, track_source), track_rows in self.tracks.items()
            if track_sample in samples and (source is None or track_source == source)
            for chrom, start, end, value in track_rows
            if chrom in wanted
        ]
        rows.sort(key=lambda row: (row["chr"], row["start"]))
        return rows[:limit] if limit is not None else rows


# --------------------------------------------------------------------------- #
# The mtDNA coverage summary and its verdict
# --------------------------------------------------------------------------- #


def _trio_context() -> FamilyMetadataContext:
    samples = {"PROBAND": "uuid-proband", "MOM": "uuid-mom", "DAD": "uuid-dad"}
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="MITO_FAMILY",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": samples["PROBAND"], "sample_id": "PROBAND", "role": "proband", "affected": True},
            {"sample_uuid": samples["MOM"], "sample_id": "MOM", "role": "mother", "affected": False},
            {"sample_uuid": samples["DAD"], "sample_id": "DAD", "role": "father", "affected": False},
        ],
        sample_uuid_to_name={uuid: name for name, uuid in samples.items()},
        sample_name_to_uuid=samples,
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        relationship_rows=[
            {
                "relationship_type": "parent_child",
                "sample_id_a": parent,
                "sample_id_b": "PROBAND",
                "role_a": role,
                "role_b": "child",
            }
            for parent, role in (("MOM", "mother"), ("DAD", "father"))
        ],
    )


def _mt_variant(*calls: tuple[str, str, int, float]) -> SmallVariantRecord:
    """m.3243A>G with each (sample, genotype, read depth, allele fraction) call."""
    return SmallVariantRecord(
        variant_key=1,
        variant_id="M-3243-A-G",
        chr="M",
        start=3243,
        end=3243,
        ref="A",
        alt="G",
        source="mito",
        rsid=None,
        filters=[],
        gene_symbols=["MT-TL1"],
        annotations=[],
        calls=[
            SmallVariantCall(
                sample=sample,
                gt=gt,
                gq=None,
                dp=depth,
                af=[fraction],
                ad=[round(depth * (1 - fraction)), round(depth * fraction)],
                ps=None,
            )
            for sample, gt, depth, fraction in calls
        ],
    )


async def _mito_response(
    monkeypatch: pytest.MonkeyPatch,
    store: _CoverageStore,
    records: list[SmallVariantRecord],
    *,
    count_only: bool = False,
):
    async def fake_records(_context):
        return records

    async def no_structural_variants(_context):
        return []

    async def no_reviews(_session, *, family_uuid, variant_ids):
        return {}

    async def thresholds(_session, *, family_uuid):
        # The lab's limits: below 300x warns, below 250x fails.
        return {
            "profile_key": "default",
            "profile_label": "Default",
            "thresholds": {"mtdna.mean_depth": {"warn_value": 300.0, "error_value": 250.0}},
        }

    store.install(monkeypatch)
    monkeypatch.setattr(mitochondrial_analysis, "_fetch_mt_records", fake_records)
    monkeypatch.setattr(mitochondrial_analysis, "_fetch_mt_structural_variants", no_structural_variants)
    monkeypatch.setattr(mitochondrial_analysis, "get_small_variant_review_map", no_reviews)
    monkeypatch.setattr(mitochondrial_analysis, "resolve_family_qc_thresholds", thresholds)
    return await mitochondrial_analysis.get_family_mitochondrial_analysis_response(
        None,  # type: ignore[arg-type]
        context=_trio_context(),
        count_only=count_only,
    )


def _whole_mt(value: float) -> list[tuple[str, int, int, float]]:
    return [("M", 0, MT_LENGTH, value)]


@pytest.mark.asyncio
async def test_the_mtdna_coverage_reads_only_each_samples_track_of_read_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _CoverageStore(
        {
            # The proband: a coverage BED of depths, and WisecondorX's bins (log2 ratios).
            ("uuid-proband", "web"): _whole_mt(400.0),
            ("uuid-proband", "wisecondorx"): _whole_mt(0.1),
            # The mother: HiFiCNV's depth only, stored as a log2 ratio to her autosomal median.
            ("uuid-mom", "hificnv"): _whole_mt(6.5),
            # The father: HiFiCNV's depth stored as read (no autosomal median), QDNAseq's bins.
            ("uuid-dad", "hificnv"): _whole_mt(320.0),
            ("uuid-dad", "qdnaseq"): _whole_mt(-0.2),
        },
        {("MOM", "hificnv"): [LOG2_RATIO], ("DAD", "hificnv"): [STORED_AS_READ]},
    )
    records = [_mt_variant(("PROBAND", "0/1", 420, 0.46), ("MOM", "0/1", 390, 0.88), ("DAD", "0/0", 310, 0.0))]

    response = await _mito_response(monkeypatch, store, records)

    coverage = {
        sample.sample_id: (
            sample.coverage.mean_depth,
            sample.coverage.min_depth,
            sample.coverage.max_depth,
            sample.coverage.source,
        )
        for sample in response.samples
    }
    # Before: the proband's mean was her BED's and her bins' blended (200.05x, minimum 0.1x),
    # the mother's her log2 ratio (6.5x), the father's his depths and bins blended (159.9x).
    assert coverage == {
        "PROBAND": (400.0, 400.0, 400.0, "coverage"),
        # No track of read depth: her calls' depth, as for a sample without a coverage track.
        "MOM": (390.0, 390.0, 390.0, "variant calls"),
        "DAD": (320.0, 320.0, 320.0, "coverage"),
    }
    assert {sample.sample_id: sample.coverage.breadth for sample in response.samples} == {
        "PROBAND": 1.0,
        "MOM": None,
        "DAD": 1.0,
    }
    # Each sample is judged on its depth against the configured limits (before: each failed,
    # "Mean mtDNA depth is below the configured limit of 250.0x").
    assert {sample.sample_id: (sample.qc.status, sample.qc.mean_depth) for sample in response.samples} == {
        "PROBAND": ("pass", 400.0),
        "MOM": ("pass", 390.0),
        "DAD": ("pass", 320.0),
    }
    assert not any(note.startswith("Mean mtDNA depth") for sample in response.samples for note in sample.qc.notes)


@pytest.mark.asyncio
async def test_a_family_whose_only_chrm_track_is_a_log2_ratio_has_no_mtdna_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bins_only = _CoverageStore({("uuid-proband", "wisecondorx"): _whole_mt(0.1)})
    # Before: any chrM row was coverage, so the family page offered the mtDNA workspace on
    # WisecondorX's bins alone.
    assert (await _mito_response(monkeypatch, bins_only, [], count_only=True)).has_coverage is False

    with_bed = _CoverageStore(
        {("uuid-proband", "wisecondorx"): _whole_mt(0.1), ("uuid-proband", "web"): _whole_mt(400.0)}
    )
    assert (await _mito_response(monkeypatch, with_bed, [], count_only=True)).has_coverage is True

    # The full response agrees, and says why the samples have no depth.
    response = await _mito_response(monkeypatch, bins_only, [])
    assert response.has_coverage is False
    assert all(sample.coverage.mean_depth is None for sample in response.samples)
    assert "No MT coverage track with read depth was found; variant-call depth is used when available." in (
        response.qc_notes
    )


@pytest.mark.asyncio
async def test_a_mother_with_only_a_log2_ratio_track_on_chrm_is_not_taken_as_sequenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _CoverageStore({("uuid-mom", "wisecondorx"): _whole_mt(0.1)})
    # The proband carries the variant; the mother has no call, her only chrM data WisecondorX's bins.
    records = [_mt_variant(("PROBAND", "0/1", 420, 0.46), ("DAD", "0/0", 310, 0.0))]

    response = await _mito_response(monkeypatch, store, records)

    # Before: her bins counted as mtDNA coverage, and the variant read "not seen in mother".
    assert response.variants[0].maternal_transmission == "mother_not_assessed"


# --------------------------------------------------------------------------- #
# The NIPT coverage check without a per-target table
# --------------------------------------------------------------------------- #


async def _nipt_coverage(monkeypatch: pytest.MonkeyPatch, store: _CoverageStore):
    family = FamilyOut(
        id="family-uuid",
        family_id="NIPT_FAMILY",
        created_at=datetime.now(timezone.utc),
        members=[
            FamilyMemberOut(sample_id="FATHER1", role="father", affected=False),
            FamilyMemberOut(
                sample_id="CFDNA1", role="mother", affected=False, sample_metadata={"assay": "nipt_cfdna"}
            ),
        ],
        metadata={"analysis_type": "monogenic_nipt"},
        roi=FamilyRegionOfInterestOut(
            query="1:1000-2000", label="ROI", source="region", chr="1", start=1000, end=2000
        ),
    )
    samples = {"FATHER1": "uuid-father", "CFDNA1": "uuid-cfdna"}

    async def fake_family_record(_session, _family_id, _user):
        return family

    async def fake_context(_session, *, family_identifier, user, project_id=None):
        return FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="NIPT_FAMILY",
            project_ids=["project-uuid"],
            sample_rows=[],
            sample_uuid_to_name={uuid: name for name, uuid in samples.items()},
            sample_name_to_uuid=samples,
            affected_sample_names=[],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )

    store.install(monkeypatch)
    monkeypatch.setattr(nipt_service, "get_family_record", fake_family_record)
    monkeypatch.setattr(nipt_service, "build_family_metadata_context", fake_context)
    coverage = await nipt_service.get_family_nipt_coverage(
        None,  # type: ignore[arg-type]
        family_id="NIPT_FAMILY",
        user=None,  # type: ignore[arg-type]
    )
    assert coverage.targets is None  # no per-target table: the coverage track over the ROI
    return coverage.regions


@pytest.mark.asyncio
async def test_the_nipt_fallback_reads_the_plasmas_coverage_bed_not_its_bins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _CoverageStore(
        {
            # The BED: 80x over the first 800 bp of the 1 kb ROI, nothing over the rest.
            ("uuid-cfdna", "web"): [("1", 1000, 1800, 80.0)],
            # WisecondorX's bin over the whole ROI: a log2 ratio.
            ("uuid-cfdna", "wisecondorx"): [("1", 0, 100_000, 0.1)],
        }
    )

    summary = await _nipt_coverage(monkeypatch, store)

    # Before: the median was the bin's 0.1 ("low depth"), and the bin's rows covered the
    # stretch the BED leaves uncovered (1800 of 1000 bases).
    assert summary.overall_median_on_target == 80.0
    [region] = summary.per_region
    assert (region.median_coverage, region.covered_bases, region.target_bases) == (80.0, 800, 1000)
    [low] = summary.low_coverage_regions
    assert (low.reason, low.covered_fraction) == ("partial_coverage", 0.8)


@pytest.mark.asyncio
async def test_the_nipt_fallback_reports_no_coverage_from_a_log2_ratio_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _CoverageStore(
        {("uuid-cfdna", "hificnv"): [("1", 0, 4000, 0.05)]},
        {("CFDNA1", "hificnv"): [LOG2_RATIO]},
    )

    summary = await _nipt_coverage(monkeypatch, store)

    # Before: the log2 ratios read as depth, a median of 0.05x ("low depth").
    assert summary.overall_median_on_target is None
    [region] = summary.per_region
    assert (region.median_coverage, region.covered_bases) == (None, 0)
    assert [(low.label, low.reason) for low in summary.low_coverage_regions] == [("ROI", "no_coverage")]
