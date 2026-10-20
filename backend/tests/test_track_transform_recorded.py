"""A track whose values the import transforms records the transform (TRACE-4, TRACE-7).

HiFiCNV's copy-number bedGraph holds integer copy number and its depth bigWig holds read
depth. The package import stores both as log2 ratios, the scale of the WisecondorX and
QDNAseq tracks stacked beside them: log2(copy number / 2) on the ``segments`` track and
log2(depth / the sample's autosomal median) on the ``coverage`` track. A stored value then
means something its file does not say, so the track's source record (the ``metadata``
column of ``sample_interval_track_sources``) names the transform. The depth track always
did. The copy-number track was handed its own, ``log2_ratio_to_diploid``, and dropped it,
so nothing recorded told its stored log2 ratios from the copy numbers in the file.

A depth bigWig without autosomal depth gives no median to normalise by, and its depths are
stored as read. Its record said ``log2_ratio_to_autosomal_median`` all the same, with a null
median, and the viewers drew the raw depths on their log2-ratio axis (TRACE-7). The record
now names the transform the stored values went through, ``none`` here, and the track
availability the viewers read serves it with the track.

The datastores are faked at their edge: the session keeps what each track-source INSERT
binds, and the interval rows bound for ClickHouse are kept as they are inserted.
"""

from __future__ import annotations

import json
from math import log2
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import family_package_datasets, family_package_tracks
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import ManifestDataset
from backend.app.services.family_service import get_family_track_availability_for_user

SAMPLE = "PROBAND"

_CNV_VCF = (
    "##fileformat=VCFv4.2\n"
    '##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type of the SV.">\n'
    '##INFO=<ID=END,Number=1,Type=Integer,Description="End position">\n'
    '##FORMAT=<ID=CN,Number=1,Type=Float,Description="Copy number">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
    "chr1\t1001\t.\tN\t<DEL>\t57\tPASS\tSVTYPE=DEL;END=2000\tGT:CN\t0/1:1\n"
)

# Copy number per 1 kb bin: diploid, a one-copy loss, a one-copy gain, a homozygous deletion.
_BEDGRAPH = "chr1\t0\t1000\t2\nchr1\t1000\t2000\t1\nchr1\t2000\t3000\t3\nchr1\t3000\t4000\t0\n"


class _Session:
    """Keeps the ``metadata`` each track-source INSERT binds, by (track type, source, file).

    Any other statement fails the test: whatever else the import would ask Postgres is
    faked by name in ``_import``.
    """

    def __init__(self) -> None:
        self.track_sources: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:
        sql = str(statement)
        assert "INSERT INTO sample_interval_track_sources" in sql, sql
        assert params is not None
        key = (params["track_type"], params["source"], params["filename"])
        self.track_sources[key] = json.loads(params["metadata_json"])

    async def commit(self) -> None:
        return None


def _write_bigwig(path: Path, values: list[float]) -> None:
    """A bigWig of 1 kb bins on chr1, one per value."""
    _write_bigwig_contigs(path, {"chr1": values})


def _write_bigwig_contigs(path: Path, contigs: dict[str, list[float]]) -> None:
    """A bigWig of 1 kb bins on each contig, one per value, the contigs in this order."""
    import pyBigWig  # the importer's own reader, a pinned dependency

    handle = pyBigWig.open(str(path), "w")
    handle.addHeader([(contig, 100_000) for contig in contigs], maxZooms=0)
    for contig, bins in contigs.items():
        handle.addEntries(
            [contig] * len(bins),
            [index * 1000 for index in range(len(bins))],
            ends=[(index + 1) * 1000 for index in range(len(bins))],
            values=bins,
        )
    handle.close()


async def _import(
    monkeypatch: pytest.MonkeyPatch, root: Path, dataset_type: str, entry: dict[str, str]
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], list[dict[str, Any]]]:
    """Import ``dataset_type`` with ``entry`` as SAMPLE's files, through the importer the
    package import runs for it. Returns the track-source records, without the time each
    was registered, and the interval rows inserted."""
    session = _Session()
    inserted: list[dict[str, Any]] = []

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def no_rows_yet(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def known_samples(*_args: Any, **_kwargs: Any) -> set[str]:
        return {SAMPLE}

    async def insert_rows(_assembly_name: str, rows: list[dict[str, Any]]) -> None:
        inserted.extend(rows)

    for module, name, fake in (
        (family_package_datasets, "_interval_track_count", no_rows_yet),
        (family_package_datasets, "known_vcf_sample_ids", known_samples),
        (family_package_datasets, "_record_sample_signal_tracks", nothing),
        (family_package_datasets, "lock_family_variant_writes", nothing),
        (family_package_datasets, "replace_family_structural_variants", nothing),
        (family_package_datasets, "_update_sv_file_metadata", nothing),
        (family_package_tracks, "_delete_sample_interval_source", nothing),
        (family_package_tracks, "insert_interval_track_rows", insert_rows),
    ):
        monkeypatch.setattr(module, name, fake)
    job = family_package_datasets.DatasetImportJob(
        session=session,  # type: ignore[arg-type]
        bundle=SimpleNamespace(root=root, source_uri=None),  # type: ignore[arg-type]
        dataset=ManifestDataset(per_sample={SAMPLE: entry}),
        summary=FamilyImportDatasetSummary(dataset_type=dataset_type, status="valid"),
        family_context=FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="FAM",
            project_ids=["project-uuid"],
            sample_rows=[],
            sample_uuid_to_name={},
            sample_name_to_uuid={},
            affected_sample_names=[],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        sample_contexts={
            SAMPLE: SampleMetadataContext(
                sample_uuid="proband-uuid",
                sample_id=SAMPLE,
                family_uuid="family-uuid",
                family_id="FAM",
                sex="female",
                project_ids=["project-uuid"],
                assembly_id="assembly-uuid",
                assembly_name="GRCh38",
            )
        },
    )

    summary = await family_package_datasets.DATASET_IMPORTERS[dataset_type](job)

    assert summary.status == "imported", summary
    records = {
        key: {field: value for field, value in metadata.items() if field != "registered_at"}
        for key, metadata in session.track_sources.items()
    }
    return records, inserted


def _values(rows: list[dict[str, Any]], track_type: str) -> list[float]:
    return [row["value"] for row in rows if row["track_type"] == track_type]


@pytest.mark.asyncio
async def test_the_hificnv_copy_number_track_records_its_transform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "cnv").mkdir()
    (tmp_path / "cnv" / f"{SAMPLE}.vcf").write_text(_CNV_VCF, encoding="utf-8")
    (tmp_path / "cnv" / f"{SAMPLE}.copynum.bedgraph").write_text(_BEDGRAPH, encoding="utf-8")

    records, rows = await _import(
        monkeypatch,
        tmp_path,
        "cnv",
        {"vcf": f"cnv/{SAMPLE}.vcf", "copy_number_bedgraph": f"cnv/{SAMPLE}.copynum.bedgraph"},
    )

    # Stored as log2(copy number / 2), a homozygous deletion at the floor ...
    assert _values(rows, "segments") == [0.0, -1.0, log2(1.5), -10.0]
    # ... and recorded as such, where and as the depth track records its own transform.
    assert records[("segments", "hificnv", f"{SAMPLE}.copynum.bedgraph")] == {
        "source": "hificnv",
        "filename": f"{SAMPLE}.copynum.bedgraph",
        "uploaded_from": "family_package",
        "normalization": "log2_ratio_to_diploid",
    }


@pytest.mark.asyncio
async def test_the_hificnv_bigwig_tracks_record_what_they_recorded_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "cnv").mkdir()
    (tmp_path / "cnv" / f"{SAMPLE}.vcf").write_text(_CNV_VCF, encoding="utf-8")
    # Read depth per bin, its autosomal median 20; and one minor allele fraction.
    _write_bigwig(tmp_path / "cnv" / f"{SAMPLE}.depth.bw", [20.0, 10.0, 40.0])
    _write_bigwig(tmp_path / "cnv" / f"{SAMPLE}.maf.bw", [0.25])

    records, rows = await _import(
        monkeypatch,
        tmp_path,
        "cnv",
        {
            "vcf": f"cnv/{SAMPLE}.vcf",
            "depth_bigwig": f"cnv/{SAMPLE}.depth.bw",
            "maf_bigwig": f"cnv/{SAMPLE}.maf.bw",
        },
    )

    # Depth is stored as log2(depth / 20), and recorded with that median.
    assert _values(rows, "coverage") == [0.0, -1.0, 1.0]
    assert records[("coverage", "hificnv", f"{SAMPLE}.depth.bw")] == {
        "source": "hificnv",
        "filename": f"{SAMPLE}.depth.bw",
        "uploaded_from": "family_package",
        "format": "bigwig",
        "normalization": "log2_ratio_to_autosomal_median",
        "autosomal_median_depth": 20.0,
    }
    # The minor allele fraction is stored as read, and its record names no transform.
    assert _values(rows, "apcad") == [0.25]
    assert records[("apcad", "hificnv", f"{SAMPLE}.maf.bw")] == {
        "source": "hificnv",
        "filename": f"{SAMPLE}.maf.bw",
        "uploaded_from": "family_package",
        "format": "bigwig",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contigs", "median", "stored"),
    [
        # No autosomal depth: the autosome's bins are zero, which the median skips as it
        # skips the assembly gaps, and chrX, where the depth is, it leaves out as
        # sex-dependent. The import drops the zero bins and keeps chrX's depths.
        ({"chr1": [0.0, 0.0], "chrX": [15.0, 30.0]}, None, [15.0, 30.0]),
        # A median that is no depth, from values at or below zero (log2 ratios given as
        # the depth bigWig, say): no ratio can be taken against it either.
        ({"chr1": [-1.0, -2.0, -3.0]}, -2.0, [-1.0, -2.0, -3.0]),
    ],
    ids=["no-autosomal-depth", "non-positive-median"],
)
async def test_a_depth_track_without_a_usable_median_records_its_depths_as_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    contigs: dict[str, list[float]],
    median: float | None,
    stored: list[float],
) -> None:
    (tmp_path / "cnv").mkdir()
    (tmp_path / "cnv" / f"{SAMPLE}.vcf").write_text(_CNV_VCF, encoding="utf-8")
    _write_bigwig_contigs(tmp_path / "cnv" / f"{SAMPLE}.depth.bw", contigs)

    records, rows = await _import(
        monkeypatch,
        tmp_path,
        "cnv",
        {"vcf": f"cnv/{SAMPLE}.vcf", "depth_bigwig": f"cnv/{SAMPLE}.depth.bw"},
    )

    # Without a baseline the depths are stored as the file holds them, not against an
    # invented one ...
    assert _values(rows, "coverage") == stored
    # ... and the record says so, with the median it could not use. It said
    # log2_ratio_to_autosomal_median over these raw depths, and the viewers drew them on
    # their log2-ratio axis as a genome-wide gain.
    assert records[("coverage", "hificnv", f"{SAMPLE}.depth.bw")] == {
        "source": "hificnv",
        "filename": f"{SAMPLE}.depth.bw",
        "uploaded_from": "family_package",
        "format": "bigwig",
        "normalization": "none",
        "autosomal_median_depth": median,
    }


def _trio_context() -> FamilyMetadataContext:
    names = {"PROBAND": "proband-uuid", "MOTHER": "mother-uuid", "FATHER": "father-uuid"}
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_id": name} for name in names],
        sample_uuid_to_name={uuid: name for name, uuid in names.items()},
        sample_name_to_uuid=dict(names),
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


async def _track_availability(
    monkeypatch: pytest.MonkeyPatch,
    *,
    coverage_sources: dict[str, list[str]],
    normalizations: Any,
) -> dict[str, dict[str, list[str]]]:
    """Each member's ``coverage_normalizations`` as the track availability serves them,
    with the callers that have coverage rows in the chromosomes asked for and the recorded
    transforms read by ``normalizations`` (a fake of the Postgres read)."""
    service = "backend.app.services.family_service."

    async def trio(*_args: Any, **_kwargs: Any) -> FamilyMetadataContext:
        return _trio_context()

    async def sources(_session: Any, *, track_type: str, **_kwargs: Any) -> dict[str, list[str]]:
        return coverage_sources if track_type == "coverage" else {}

    async def nobody(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    monkeypatch.setattr(service + "build_family_metadata_context", trio)
    monkeypatch.setattr(service + "get_track_sources_by_sample", sources)
    monkeypatch.setattr(service + "get_track_presence_by_sample", nobody)
    monkeypatch.setattr(service + "_repeat_expansion_presence_by_sample", nobody)
    monkeypatch.setattr(service + "_structural_present_sample_names", nobody)
    monkeypatch.setattr(service + "interval_track_normalizations_by_sample", normalizations)

    async def no_declarations(*_args: Any, **_kwargs: Any) -> dict[str, dict[str, list[str]]]:
        return {}  # no coverage BED among these tracks (TRACE-9)

    monkeypatch.setattr(service + "interval_track_contents_by_sample", no_declarations)

    availability = await get_family_track_availability_for_user(
        None,  # type: ignore[arg-type]
        family_id="FAM",
        user=None,  # type: ignore[arg-type]
        chromosomes=["1"],
        include_small_variants=False,
    )
    return {name: sample.coverage_normalizations for name, sample in availability.samples.items()}


@pytest.mark.asyncio
async def test_the_track_availability_serves_each_coverage_track_s_recorded_transform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, dict[str, str], str]] = []

    async def recorded(
        _session: Any, *, family_uuid: str, sample_uuid_to_name: dict[str, str], track_type: str
    ) -> dict[str, dict[str, list[str]]]:
        asked.append((family_uuid, sample_uuid_to_name, track_type))
        return {
            # The proband's depths had no autosomal median and are stored as read.
            "PROBAND": {"hificnv": ["none"]},
            "MOTHER": {"hificnv": ["log2_ratio_to_autosomal_median"]},
            # The father's depth track has no rows in the chromosomes asked for: it is not
            # offered, so nothing is said of it.
            "FATHER": {"hificnv": ["log2_ratio_to_autosomal_median"]},
        }

    served = await _track_availability(
        monkeypatch,
        coverage_sources={"PROBAND": ["hificnv", "wisecondorx"], "MOTHER": ["hificnv"]},
        normalizations=recorded,
    )

    # Each offered track with the transform its records name. WisecondorX's bins are its
    # file's log2 ratios, recorded with no transform, so it is not listed.
    assert served == {
        "PROBAND": {"hificnv": ["none"]},
        "MOTHER": {"hificnv": ["log2_ratio_to_autosomal_median"]},
        "FATHER": {},
    }
    # Read once, for the family's samples' coverage tracks.
    assert asked == [("family-uuid", _trio_context().sample_uuid_to_name, "coverage")]


@pytest.mark.asyncio
async def test_the_track_availability_reads_no_transform_when_no_member_has_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unread(*_args: Any, **_kwargs: Any) -> dict[str, dict[str, list[str]]]:
        raise AssertionError("no coverage track is offered, so there is no transform to read")

    served = await _track_availability(monkeypatch, coverage_sources={}, normalizations=unread)

    assert served == {"PROBAND": {}, "MOTHER": {}, "FATHER": {}}


@pytest.mark.asyncio
async def test_a_copy_number_track_stored_as_read_records_no_transform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "qdnaseq").mkdir()
    header = "chr,start,end,copynumber,segmented\n"
    (tmp_path / "qdnaseq" / f"{SAMPLE}_bins.csv").write_text(header + "1,1,1000,0.12,0.08\n", encoding="utf-8")
    (tmp_path / "qdnaseq" / f"{SAMPLE}_segments.csv").write_text(header + "1,1,1000,0.12,0.08\n", encoding="utf-8")

    records, rows = await _import(
        monkeypatch,
        tmp_path,
        "qdnaseq",
        {"bins": f"qdnaseq/{SAMPLE}_bins.csv", "segments": f"qdnaseq/{SAMPLE}_segments.csv"},
    )

    # QDNAseq's values are stored as its files hold them, and their records name no
    # transform: the record is what it was before copy-number tracks recorded one.
    assert _values(rows, "coverage") == [0.12]
    assert _values(rows, "segments") == [0.08]
    for track_type, filename in (("coverage", f"{SAMPLE}_bins.csv"), ("segments", f"{SAMPLE}_segments.csv")):
        assert records[(track_type, "qdnaseq", filename)] == {
            "source": "qdnaseq",
            "filename": filename,
            "uploaded_from": "family_package",
        }
