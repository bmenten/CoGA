"""A monogenic NIPT pair: two one-sample VCFs (the maternal plasma and the father) as one
callset, with each call's own FILTER and caller metrics.

The lab calls the plasma and the paternal sample one file each with Mutect2 in tumour-only
mode, so a record's QUAL is missing, its quality is TLOD, and its FILTER and INFO describe
that one call. The import keeps them with the call (``calls.filters``, ``calls.metrics``),
stores both files as the ``nipt`` callset, one row per variant holding each sample's call,
and leaves out the paternal file's low-level noise except where the plasma has a call.
All samples and values here are synthetic.
"""

from __future__ import annotations

import gzip
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import UploadFile
import pytest

from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import family_package_datasets, variant_upload_service
from backend.app.services.clickhouse_variant_records import SmallVariantCall
from backend.app.services.clickhouse_variant_storage import SMALL_VARIANT_ENTRY_COLUMNS
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import (
    FamilyPackageBundle,
    ManifestDataset,
    PackageManifest,
    ParsedPed,
    PedMember,
)
from backend.app.services.family_package_datasets import DatasetImportJob
from backend.app.services.family_package_nipt import (
    NIPT_PATERNAL_KEEP_MIN_VAF,
    call_alt_fraction,
    discover_nipt_package_files,
    nipt_import_order,
    nipt_package_cfdna_sample,
    paternal_record_filter,
    scan_vcf_positions,
)
from backend.app.services.vcf_call_metrics import record_filter_values, single_sample_call_metrics


# --------------------------------------------------------------------------- #
# The caller metrics of a one-sample record
# --------------------------------------------------------------------------- #


def test_mutect2_metrics_keep_tlod_strand_bias_and_the_alt_alleles_values() -> None:
    info = "AS_FilterStatus=SITE;DP=1783;ECNT=1;FS=3.2;MBQ=20,30;MMQ=60,58;MPOS=22;POPAF=7.3;TLOD=4856.41"
    metrics = single_sample_call_metrics(info, ".")
    assert metrics == {
        "ECNT": 1.0,
        "FS": 3.2,
        "MBQ_REF": 20.0,
        "MBQ": 30.0,
        "MMQ_REF": 60.0,
        "MMQ": 58.0,
        "MPOS": 22.0,
        "POPAF": 7.3,
        "TLOD": 4856.41,
    }


def test_a_short_tandem_repeat_keeps_its_unit_counts_and_flag() -> None:
    metrics = single_sample_call_metrics("RPA=7,6;RU=CA;STR;TLOD=12.5", "35")
    assert metrics["STR"] == 1.0
    assert metrics["RPA_REF"] == 7.0 and metrics["RPA"] == 6.0
    assert metrics["RU_LEN"] == 2.0
    # A record's own QUAL, when it has one, is the call's.
    assert metrics["QUAL"] == 35.0


def test_vardict_metrics_and_missing_values() -> None:
    metrics = single_sample_call_metrics("SBF=0.04;MQ=60;NM=1.5;MSI=4;MSILEN=1;VD=12;TLOD=.", "120")
    assert metrics == {"QUAL": 120.0, "SBF": 0.04, "MQ": 60.0, "NM": 1.5, "MSI": 4.0, "MSILEN": 1.0, "VD": 12.0}
    assert single_sample_call_metrics(".", ".") == {}


def test_record_filter_values() -> None:
    assert record_filter_values("PASS") == ["PASS"]
    assert record_filter_values("haplotype;weak_evidence") == ["haplotype", "weak_evidence"]
    assert record_filter_values(".") == []


# --------------------------------------------------------------------------- #
# The upload keeps them with the call of a one-sample VCF only
# --------------------------------------------------------------------------- #


class _FakeSession:
    async def commit(self) -> None:
        return None


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="NIPTFAM1",
        project_ids=["p1"],
        sample_rows=[],
        sample_uuid_to_name={"cfdna-uuid": "CFDNA1", "father-uuid": "FATHER1"},
        sample_name_to_uuid={"CFDNA1": "cfdna-uuid", "FATHER1": "father-uuid"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _sample_contexts() -> dict[str, SampleMetadataContext]:
    return {
        name: SampleMetadataContext(
            sample_uuid=f"{name.lower()}-uuid",
            sample_id=name,
            family_uuid="family-uuid",
            family_id="NIPTFAM1",
            sex="und",
            project_ids=["p1"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for name in ("CFDNA1", "FATHER1")
    }


_HEADER = "##fileformat=VCFv4.2\n##source=Mutect2\n"

_PLASMA_VCF = (
    _HEADER
    + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tCFDNA1\n"
    + "chr7\t1000\t.\tA\tG\t.\tPASS\tFS=1.1;MMQ=60,60;TLOD=310.2\tGT:AD:AF:DP\t0/1:900,100:0.1:1000\n"
    + "chr7\t2000\t.\tC\tT\t.\tweak_evidence\tFS=0;MMQ=60,60;TLOD=1.2\tGT:AD:AF:DP\t0/1:995,5:0.005:1000\n"
)

_FATHER_VCF = (
    _HEADER
    + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tFATHER1\n"
    # A het genotype call: kept.
    + "chr7\t1000\t.\tA\tG\t.\tPASS\tTLOD=500\tGT:AD:AF:DP\t0/1:150,150:0.5:300\n"
    # Low-level noise at a plasma call position: kept (the paternal signal there matters).
    + "chr7\t2000\t.\tC\tT\t.\tweak_evidence\tTLOD=0.8\tGT:AD:AF:DP\t0/1:297,3:0.01:300\n"
    # Low-level noise elsewhere: left out.
    + "chr7\t3000\t.\tG\tT\t.\tweak_evidence\tTLOD=0.5\tGT:AD:AF:DP\t0/1:296,4:0.013:300\n"
    # A hom-alt genotype call the plasma has no call at: kept.
    + "chr7\t4000\t.\tT\tC\t.\tPASS\tTLOD=900\tGT:AD:AF:DP\t0/1:1,299:0.997:300\n"
)


@pytest.fixture()
def storage(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake storage around the real upload: the callset's rows, rewritten per sample."""
    state: dict[str, Any] = {"stored": [], "rewrites": []}

    async def fetch(_assembly, _family, *, source=None):
        state["fetched_source"] = source
        return [dict(row) for row in state["stored"]]

    async def insert(*_args, **_kwargs):
        return None

    async def rewrite(_assembly, _family, entries, *, source=None):
        state["rewrites"].append(source)
        state["stored"] = [dict(row) for row in entries]

    async def no_op(*_args, **_kwargs):
        return None

    for name, fn in {
        "fetch_family_small_variant_entries": fetch,
        "insert_small_variant_records": insert,
        "rewrite_family_small_variant_entries": rewrite,
        "delete_family_small_variants": no_op,
        "lock_family_variant_writes": no_op,
    }.items():
        monkeypatch.setattr(variant_upload_service, name, fn)
    return state


def _calls(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        sample: {"ad": ad, "filters": filters, "metrics": metrics}
        for sample, ad, filters, metrics in zip(
            row["calls.sampleId"], row["calls.ad"], row["calls.filters"], row["calls.metrics"]
        )
    }


async def _upload(text: str, name: str, **kwargs: Any) -> dict[str, Any]:
    return await variant_upload_service.upload_family_small_variant_file(
        _FakeSession(),  # type: ignore[arg-type]
        context=_context(),
        sample_contexts=_sample_contexts(),
        file=UploadFile(file=BytesIO(text.encode()), filename=name),
        overwrite=True,
        format_hint="nipt",
        overwrite_scope="samples",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_a_one_sample_record_keeps_its_filter_and_metrics_with_the_call(storage) -> None:
    result = await _upload(_PLASMA_VCF, "CFDNA1.vcf")
    assert result["inserted"] == 2 and result["source_format"] == "nipt"
    rows = {row["pos"]: row for row in storage["stored"]}
    assert set(rows) == {1000, 2000}
    plasma = _calls(rows[1000])["CFDNA1"]
    assert plasma["filters"] == ["PASS"]
    assert plasma["metrics"] == {"FS": 1.1, "MMQ_REF": 60.0, "MMQ": 60.0, "TLOD": 310.2}
    assert _calls(rows[2000])["CFDNA1"]["filters"] == ["weak_evidence"]
    # Every stored row has a value of each per-call column for each of its calls.
    for row in storage["stored"]:
        assert set(SMALL_VARIANT_ENTRY_COLUMNS) <= set(row)
        assert len(row["calls.filters"]) == len(row["calls.metrics"]) == len(row["calls.sampleId"])


@pytest.mark.asyncio
async def test_the_record_filter_leaves_out_records_and_counts_them(storage) -> None:
    await _upload(_PLASMA_VCF, "CFDNA1.vcf")
    keep = paternal_record_filter({("7", 1000), ("7", 2000)})
    result = await _upload(_FATHER_VCF, "FATHER1.vcf", record_filter=keep)
    assert result["inserted"] == 3 and result["skipped_by_filter"] == 1
    rows = {row["pos"]: _calls(row) for row in storage["stored"]}
    # One row per variant, holding each sample's call where its file had one.
    assert set(rows) == {1000, 2000, 4000}
    assert set(rows[1000]) == {"CFDNA1", "FATHER1"}
    assert set(rows[2000]) == {"CFDNA1", "FATHER1"}
    assert set(rows[4000]) == {"FATHER1"}
    assert rows[1000]["FATHER1"]["metrics"] == {"TLOD": 500.0}
    assert rows[1000]["CFDNA1"]["metrics"]["TLOD"] == 310.2


@pytest.mark.asyncio
async def test_a_record_the_filter_leaves_out_has_no_annotation_parsed(storage, monkeypatch) -> None:
    # A NIPT father's file drops over a million noise calls: their VEP annotation, most of a
    # record's parsing, was parsed and thrown away.
    parsed: list[str] = []
    real = variant_upload_service.extract_small_variant_annotations

    def counting(info, state):
        parsed.append(str(info.get("TLOD")))
        return real(info, state)

    monkeypatch.setattr(variant_upload_service, "extract_small_variant_annotations", counting)
    keep = paternal_record_filter({("7", 1000), ("7", 2000)})
    result = await _upload(_FATHER_VCF, "FATHER1.vcf", record_filter=keep)
    assert result["skipped_by_filter"] == 1
    assert len(parsed) == result["inserted"] == 3


@pytest.mark.asyncio
async def test_a_multi_sample_record_describes_the_site_not_the_calls(storage) -> None:
    joint = (
        _HEADER
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tCFDNA1\tFATHER1\n"
        + "chr7\t1000\t.\tA\tG\t50\tPASS\tTLOD=310.2\tGT:AD:DP\t0/1:900,100:1000\t0/1:150,150:300\n"
    )
    await variant_upload_service.upload_family_small_variant_file(
        _FakeSession(),  # type: ignore[arg-type]
        context=_context(),
        sample_contexts=_sample_contexts(),
        file=UploadFile(file=BytesIO(joint.encode()), filename="joint.vcf"),
        overwrite=True,
        format_hint="clair3",
        overwrite_scope="samples",
    )
    [row] = storage["stored"]
    assert row["calls.filters"] == [[], []]
    assert row["calls.metrics"] == [{}, {}]


# --------------------------------------------------------------------------- #
# The pair's helpers
# --------------------------------------------------------------------------- #


def _call(ad: list[int], af: list[float] | None = None) -> SmallVariantCall:
    return SmallVariantCall(sample="FATHER1", gt="0/1", gq=None, dp=sum(ad), af=af or [], ad=ad, ps=None)


def test_alt_fraction_reads_the_allele_depths_before_the_callers_af() -> None:
    assert call_alt_fraction(_call([150, 150], af=[0.9])) == 0.5
    assert call_alt_fraction(_call([], af=[0.2])) == 0.2
    assert call_alt_fraction(_call([0, 0])) is None


def test_paternal_filter_keeps_genotype_calls_and_plasma_positions() -> None:
    keep = paternal_record_filter({("7", 2000)})
    assert keep("7", 2000, 2000, [_call([297, 3])])  # noise at a plasma call position
    assert not keep("7", 3000, 3000, [_call([297, 3])])  # noise elsewhere
    assert keep("7", 3000, 3000, [_call([170, 30])])  # 15%: at the keep floor
    assert NIPT_PATERNAL_KEEP_MIN_VAF < 0.20  # below the het floor: no genotype is lost
    # A paternal MNV over a plasma call's base, at any of its bases.
    assert keep("7", 1999, 2000, [_call([297, 3])])
    assert not keep("7", 2001, 2002, [_call([297, 3])])


def test_the_scan_lists_every_base_of_an_mnv(tmp_path: Path) -> None:
    path = tmp_path / "CFDNA1.mutect2.vcf.gz"
    body = (
        _HEADER
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tCFDNA1\n"
        + "7\t1000\t.\tACG\tGCA\t.\tPASS\t.\tGT:AD\t0/1:270,30\n"
        + "7\t2000\t.\tAT\tA\t.\tPASS\t.\tGT:AD\t0/1:270,30\n"
    )
    _write_gz(path, body)
    # The MNV's three bases; the deletion only at its position.
    assert scan_vcf_positions(path) == {("7", 1000), ("7", 1001), ("7", 1002), ("7", 2000)}


def _ped(rows: list[tuple[str, str, str, str]]) -> ParsedPed:
    members = [
        PedMember(
            family_id="NIPTFAM1",
            iid=iid,
            pid=pid,
            mid=mid,
            sex=sex,
            phen="1",
            line_no=index + 1,
            clinical_status="unaffected",
        )
        for index, (iid, pid, mid, sex) in enumerate(rows)
    ]
    return ParsedPed(family_ids=["NIPTFAM1"], members=members, sample_ids=[m.iid for m in members], text="")


_TRIO = [("FATHER1", "0", "0", "1"), ("CFDNA1", "0", "0", "2"), ("FETUS1", "FATHER1", "CFDNA1", "0")]


def _bundle(root: Path, samples: Any = None) -> FamilyPackageBundle:
    return FamilyPackageBundle(
        root=root,
        manifest_path=root / "manifest.yaml",
        manifest=PackageManifest(ped="nipt_trio.ped", analysis_type="monogenic_nipt", samples=samples),
        ped_path=root / "nipt_trio.ped",
        ped=_ped(_TRIO),
    )


def test_the_plasma_sample_is_the_tagged_one_else_the_mother(tmp_path: Path) -> None:
    assert nipt_package_cfdna_sample(_bundle(tmp_path, {"FATHER1": {}, "CFDNA1": {"assay": "nipt_cfdna"}})) == "CFDNA1"
    assert nipt_package_cfdna_sample(_bundle(tmp_path)) == "CFDNA1"
    assert nipt_import_order("CFDNA1", ["FATHER1", "CFDNA1"]) == ["CFDNA1", "FATHER1"]
    assert nipt_import_order(None, ["FATHER1", "CFDNA1"]) == ["FATHER1", "CFDNA1"]


def _write_gz(path: Path, text: str) -> None:
    with gzip.open(path, "wt") as handle:
        handle.write(text)


def test_scan_positions_normalises_the_chromosomes(tmp_path: Path) -> None:
    path = tmp_path / "CFDNA1.mutect2.vcf.gz"
    _write_gz(path, _PLASMA_VCF)
    assert scan_vcf_positions(path) == {("7", 1000), ("7", 2000)}


def test_discover_finds_a_parent_pair_with_one_vcf_each(tmp_path: Path) -> None:
    _write_gz(tmp_path / "CFDNA1.mutect2.vcf.gz", _PLASMA_VCF)
    _write_gz(tmp_path / "FATHER1.mutect2.vcf.gz", _FATHER_VCF)
    (tmp_path / "coverage_CFDNA1.txt").write_text("#build\tchromosome\tstart\tend\tattribute\tmean\n")
    found = discover_nipt_package_files(tmp_path, _ped(_TRIO))
    assert found is not None
    assert (found.cfdna_sample_id, found.father_sample_id, found.fetus_sample_id) == ("CFDNA1", "FATHER1", "FETUS1")
    assert found.vcfs == {"CFDNA1": "CFDNA1.mutect2.vcf.gz", "FATHER1": "FATHER1.mutect2.vcf.gz"}
    assert found.coverage_tables == {"CFDNA1": "coverage_CFDNA1.txt"}


def test_discover_needs_both_parents_and_a_fetus_without_data(tmp_path: Path) -> None:
    _write_gz(tmp_path / "CFDNA1.mutect2.vcf.gz", _PLASMA_VCF)
    assert discover_nipt_package_files(tmp_path, _ped(_TRIO)) is None
    _write_gz(tmp_path / "FATHER1.mutect2.vcf.gz", _FATHER_VCF)
    # A PED that names one parent as both father and mother is no pair.
    broken = [("FATHER1", "0", "0", "1"), ("CFDNA1", "0", "0", "2"), ("FETUS1", "FATHER1", "FATHER1", "0")]
    assert discover_nipt_package_files(tmp_path, _ped(broken)) is None
    # A child with a VCF of its own is a sequenced child, not a fetus.
    _write_gz(tmp_path / "FETUS1.vcf.gz", _PLASMA_VCF.replace("CFDNA1", "FETUS1"))
    assert discover_nipt_package_files(tmp_path, _ped(_TRIO)) is None


# --------------------------------------------------------------------------- #
# The package importer: the plasma first, the paternal file filtered against it
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_importer_loads_the_plasma_first_and_filters_the_paternal_file(
    tmp_path: Path, storage: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_gz(tmp_path / "FATHER1.mutect2.vcf.gz", _FATHER_VCF)
    _write_gz(tmp_path / "CFDNA1.mutect2.vcf.gz", _PLASMA_VCF)
    dataset = ManifestDataset(
        per_sample={
            # Listed father first: the plasma still goes first.
            "FATHER1": {"vcf": "FATHER1.mutect2.vcf.gz"},
            "CFDNA1": {"vcf": "CFDNA1.mutect2.vcf.gz"},
        }
    )

    async def no_count(*_args, **_kwargs):
        raise AssertionError("an overwrite import does not count the callset first")

    monkeypatch.setattr(family_package_datasets, "count_family_small_variants", no_count)
    job = DatasetImportJob(
        session=_FakeSession(),  # type: ignore[arg-type]
        bundle=_bundle(tmp_path, {"CFDNA1": {"assay": "nipt_cfdna"}}),
        dataset=dataset,
        summary=FamilyImportDatasetSummary(dataset_type="snv", enabled=True, status="valid"),
        family_context=_context(),
        sample_contexts=_sample_contexts(),
    )
    summary = await family_package_datasets.DATASET_IMPORTERS["snv"](job)

    assert summary.status == "imported"
    assert list(summary.summary) == ["CFDNA1", "FATHER1"]
    assert summary.summary["CFDNA1"]["role"] == "cfdna"
    assert summary.summary["FATHER1"]["skipped_by_filter"] == 1
    assert storage["rewrites"] == ["nipt", "nipt"]
    assert {row["pos"] for row in storage["stored"]} == {1000, 2000, 4000}


@pytest.mark.asyncio
async def test_the_importer_reports_the_bytes_of_both_files_read(
    tmp_path: Path, storage: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The time left of the dataset is of both files: the father's bytes count on from the
    # end of the plasma's (import_progress).
    _write_gz(tmp_path / "FATHER1.mutect2.vcf.gz", _FATHER_VCF)
    _write_gz(tmp_path / "CFDNA1.mutect2.vcf.gz", _PLASMA_VCF)
    monkeypatch.setattr(variant_upload_service, "SMALL_VARIANT_PROGRESS_INTERVAL", 1)
    reports: list[FamilyImportDatasetSummary] = []

    async def progress(summary: FamilyImportDatasetSummary) -> None:
        reports.append(summary)

    job = DatasetImportJob(
        session=_FakeSession(),  # type: ignore[arg-type]
        bundle=_bundle(tmp_path, {"CFDNA1": {"assay": "nipt_cfdna"}}),
        dataset=ManifestDataset(
            per_sample={
                "FATHER1": {"vcf": "FATHER1.mutect2.vcf.gz"},
                "CFDNA1": {"vcf": "CFDNA1.mutect2.vcf.gz"},
            }
        ),
        summary=FamilyImportDatasetSummary(dataset_type="snv", enabled=True, status="valid"),
        family_context=_context(),
        sample_contexts=_sample_contexts(),
        progress=progress,
    )
    summary = await family_package_datasets.DATASET_IMPORTERS["snv"](job)

    plasma = (tmp_path / "CFDNA1.mutect2.vcf.gz").stat().st_size
    father = (tmp_path / "FATHER1.mutect2.vcf.gz").stat().st_size
    reading = [report.summary for report in reports if "bytes_read" in report.summary]
    assert {report.status for report in reports} == {"running"}
    assert {(stats["importing"], stats["bytes_read"]) for stats in reading} == {
        ("CFDNA1", plasma),
        ("FATHER1", plasma + father),
    }
    assert {stats["bytes_total"] for stats in reading} == {plasma + father}
    assert summary.status == "imported"
    assert list(summary.summary) == ["CFDNA1", "FATHER1"]
