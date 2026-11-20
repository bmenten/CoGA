"""A conventional NIPT's ccnv output: how CoGA reads it, how Discover drafts its package, what
validation refuses, and what the import stores.

ccnv writes, per maternal plasma, its bins, segments, calls VCF and QC file (ccnv_output.py).
The import stores the bins and segments as the sample's log2-ratio ``coverage`` and ``segments``
tracks (source ``ccnv``), with ccnv's values beside each row; the calls as structural variants
(source ``ccnv``) with their FORMAT values; and the QC file under ``samples.metadata["ccnv"]``.
The files are one run's result: a QC file and a calls VCF that name different samples, or state
another fetal sex, fetal fraction or sex-chromosome call, are refused, before anything is
written. All IDs and values are synthetic (``_ccnv_output.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportDatasetSummary, FamilyPackageManifestBuildRequest
from backend.app.services import annotation_manifest_service, family_package_datasets, family_package_tracks
from backend.app.services.ccnv_output import (
    CcnvOutputError,
    ccnv_track_rows,
    ccnv_vcf_header_problems,
    read_ccnv_qc,
)
from backend.app.services.coverage_read_depth import coverage_track_content
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import ManifestDataset
from backend.app.services.family_package_discovery import discover_family_package_manifest
from backend.app.services.family_package_validation import load_validated_family_package
from backend.tests._ccnv_output import BINS_HEADER, SEGMENTS_HEADER, calls_vcf, qc_payload, write_ccnv_output
from backend.tests._kept_manifest import kept_manifest

SAMPLE = "CFDNA1"


def _qc(**prenatal: Any) -> bytes:
    return json.dumps(qc_payload(SAMPLE, **prenatal)).encode()


# --- Reading ccnv's files -------------------------------------------------------------------------


def test_the_qc_file_gives_the_run_its_fetal_fractions_sex_and_sex_chromosome_call() -> None:
    qc = read_ccnv_qc(_qc(), file="CFDNA1_qc.json")

    assert (qc.sample, qc.fetal_sex, qc.sca_call) == (SAMPLE, "F", "XX")
    assert (qc.active_ff, qc.effective_ff) == (0.081234, 0.121111)
    # The object is kept as ccnv wrote it, every estimator with it.
    assert qc.payload["prenatal"]["ffx"] == 0.09


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        pytest.param(json.dumps({**qc_payload(SAMPLE), "mode": "germline"}).encode(), "prenatal mode", id="germline"),
        pytest.param(json.dumps({**qc_payload(SAMPLE), "schema_version": "1.4.0"}).encode(), "format version", id="old-format"),
        pytest.param(_qc(effective_ff=None), "prenatal.effective_ff", id="no-effective-ff"),
        pytest.param(_qc(active_ff=1.5), "prenatal.active_ff", id="ff-above-one"),
        pytest.param(_qc(sca=None), "sex-chromosome call", id="no-sca"),
        pytest.param(b'{"schema_version": "2.0.0", "schema_version": "2.0.0"}', "a key twice", id="duplicate-key"),
        pytest.param(b'{"schema_version": NaN}', "no JSON text", id="nan"),
        pytest.param(b"[1, 2]", "no JSON object", id="not-an-object"),
    ],
)
def test_a_qc_file_that_is_no_ccnv_prenatal_qc_is_refused(raw: bytes, words: str) -> None:
    with pytest.raises(CcnvOutputError, match=words):
        read_ccnv_qc(raw, file="CFDNA1_qc.json")


def test_a_bins_row_keeps_its_ratio_and_ccnvs_values_and_a_masked_bin_is_left_out() -> None:
    lines = [
        BINS_HEADER,
        "chr1\t0\t100000\tchr1:0-100000\t.\t.\t0.000000\t0\ttrue\tautosomal\n",
        "chr1\t100000\t200000\tchr1:100000-200000\t-0.0125\t-0.4\t150.5\t300\tfalse\tautosomal\n",
    ]

    masked, row = list(ccnv_track_rows(lines, track_type="coverage", file="bins.bed.gz"))

    assert masked is None
    assert row is not None
    assert (row.chrom, row.start, row.end, row.value, row.record_id) == ("1", 100000, 200000, -0.0125, "chr1:100000-200000")
    assert row.kept == {"zscore": -0.4, "weight": 150.5, "reference_size": 300, "branch": "autosomal"}


def test_a_segment_keeps_its_call_expected_ratio_implied_ff_discordance_and_origin() -> None:
    lines = [
        SEGMENTS_HEADER,
        "chr13\t25000000\t75000000\t0.015\t4.2\t81234.5\t480\tgain\t0.08482\t0.0135\ttrue\tSUB_FETAL_RATIO\t0.18\t-51234.5\tMATERNAL_MOSAIC\n",
    ]

    (row,) = list(ccnv_track_rows(lines, track_type="segments", file="segments.bed.gz"))

    assert row is not None and row.value == 0.015
    assert row.kept == {
        "zscore": 4.2,
        "weight": 81234.5,
        "bin_count": 480,
        "call": "gain",
        "expected_ratio": 0.08482,
        "implied_ff": 0.0135,
        "discordant": True,
        "discordance_reason": "SUB_FETAL_RATIO",
        "frag_short_ratio": 0.18,
        "frag_llr_size": -51234.5,
        "origin_call": "MATERNAL_MOSAIC",
    }


def test_a_track_file_without_its_header_or_a_column_is_refused_before_any_row() -> None:
    with pytest.raises(CcnvOutputError, match="does not start with its header line"):
        list(ccnv_track_rows(["chr1\t0\t100000\t0.1\n"], track_type="segments", file="segments.bed.gz"))
    with pytest.raises(CcnvOutputError, match="no column origin_call"):
        list(ccnv_track_rows([SEGMENTS_HEADER.replace("\torigin_call", "")], track_type="segments", file="s.bed.gz"))


def test_the_calls_vcf_must_state_the_results_its_qc_file_states() -> None:
    qc = read_ccnv_qc(_qc(), file="CFDNA1_qc.json")

    def problems(vcf: str) -> list[str]:
        return ccnv_vcf_header_problems(vcf.splitlines(), qc, file="CFDNA1_calls.vcf.gz")

    assert problems(calls_vcf(SAMPLE)) == []
    assert problems(calls_vcf(SAMPLE, fetal_sex="M")) == [
        "The ccnv calls VCF CFDNA1_calls.vcf.gz states another fetal sex than its QC file"
    ]
    assert problems(calls_vcf(SAMPLE, fetal_fraction="0.0950")) == [
        "The ccnv calls VCF CFDNA1_calls.vcf.gz states another fetal fraction than its QC file"
    ]
    assert problems(calls_vcf(SAMPLE, sca_call="XO")) == [
        "The ccnv calls VCF CFDNA1_calls.vcf.gz states another sex-chromosome call than its QC file"
    ]
    assert len(problems(calls_vcf(SAMPLE).replace("##scaCall=XX\n", ""))) == 1


def test_ccnv_coverage_is_read_as_a_log2_ratio() -> None:
    # Drawn on the viewers' log2-ratio axis with its segments, and never read as depth.
    assert coverage_track_content("ccnv") == "log2_ratio"


# --- Discover -------------------------------------------------------------------------------------


def test_discover_drafts_a_conventional_nipt_from_a_folder_of_ccnv_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])
    package = tmp_path / "CNIPT1"
    entry = write_ccnv_output(package)

    result = discover_family_package_manifest(FamilyPackageManifestBuildRequest(folder_path=str(package)))

    manifest = yaml.safe_load(result.manifest_yaml)
    assert manifest["analysis_type"] == "conventional_nipt"
    # The plasma is the member, the mother of the pregnancy, tagged as a conventional NIPT's.
    assert manifest["family"]["add_members"] == [{"sample_id": SAMPLE, "sex": "female", "role": "mother"}]
    assert manifest["samples"][SAMPLE] == {"assay": "cnipt_cfdna"}
    assert manifest["datasets"]["ccnv"] == {"enabled": True, "per_sample": {SAMPLE: entry}}
    assert any(issue.code == "ccnv_plasma_proposed" for issue in result.warnings)
    assert not result.errors


# --- Validation -----------------------------------------------------------------------------------


def _package(tmp_path: Path, *, analysis_type: str = "conventional_nipt", **output: Any) -> Path:
    package = tmp_path / "CNIPT1"
    entry = write_ccnv_output(package, **output)
    (package / "CNIPT1.ped").write_text(f"CNIPT1\t{SAMPLE}\t0\t0\t2\t-9\trole=mother\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "family_id": "CNIPT1",
        "ped": "CNIPT1.ped",
        "analysis_type": analysis_type,
        "samples": {SAMPLE: {"assay": "cnipt_cfdna"}},
        "datasets": {"ccnv": {"per_sample": {SAMPLE: entry}}},
    }
    (package / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return package


def _validated(package: Path) -> Any:
    validation, _bundle = load_validated_family_package(package, manifest_text=kept_manifest(package))
    return validation


def test_a_conventional_nipt_package_validates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])

    validation = _validated(_package(tmp_path))

    assert validation.valid, validation.errors
    (summary,) = [item for item in validation.datasets if item.dataset_type == "ccnv"]
    assert summary.status == "valid" and summary.samples == [SAMPLE]
    assert len(summary.files) == 4


def test_ccnv_output_is_read_for_a_conventional_nipt_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])

    validation = _validated(_package(tmp_path, analysis_type="monogenic_nipt"))

    assert [issue.code for issue in validation.errors] == ["ccnv_analysis_type"]


@pytest.mark.parametrize(
    ("output", "code"),
    [
        pytest.param({"vcf": calls_vcf("OTHERTUBE", fetal_fraction="0.0812")}, "ccnv_samples_disagree", id="two-samples"),
        pytest.param({"vcf": calls_vcf(SAMPLE, fetal_fraction="0.1500")}, "ccnv_results_disagree", id="another-run"),
        pytest.param({"qc": {**qc_payload(SAMPLE), "mode": "germline"}}, "ccnv_qc_invalid", id="germline"),
    ],
)
def test_ccnv_files_that_are_no_one_runs_result_are_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, output: dict[str, Any], code: str
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])

    validation = _validated(_package(tmp_path, **output))

    assert not validation.valid
    assert code in [issue.code for issue in validation.errors]


def test_output_named_after_another_tube_is_read_as_the_members_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [])

    validation = _validated(
        _package(tmp_path, qc=qc_payload("TUBE9"), vcf=calls_vcf("TUBE9"))
    )

    assert validation.valid, validation.errors
    (warning,) = [issue for issue in validation.warnings if issue.code == "ccnv_sample_named_otherwise"]
    assert "names its sample 'TUBE9' not CFDNA1" in warning.message


# --- The import -----------------------------------------------------------------------------------


class _Session:
    """Keeps the ``metadata`` each track-source INSERT binds, by (track type, source, file)."""

    def __init__(self) -> None:
        self.track_sources: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:
        sql = str(statement)
        assert "INSERT INTO sample_interval_track_sources" in sql, sql
        assert params is not None
        self.track_sources[(params["track_type"], params["source"], params["filename"])] = json.loads(
            params["metadata_json"]
        )

    async def commit(self) -> None:
        return None


def _job(root: Path, entries: dict[str, dict[str, str]], session: Any, *, conflict_mode: str = "overwrite") -> Any:
    samples = {name: f"uuid-{name.lower()}" for name in entries}
    return family_package_datasets.DatasetImportJob(
        session=session,
        bundle=SimpleNamespace(root=root, manifest=SimpleNamespace(analysis_type=None)),  # type: ignore[arg-type]
        dataset=ManifestDataset(per_sample=entries),
        summary=FamilyImportDatasetSummary(dataset_type="ccnv", status="valid"),
        family_context=FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="CNIPT1",
            project_ids=["project-uuid"],
            sample_rows=[],
            sample_uuid_to_name={uuid: name for name, uuid in samples.items()},
            sample_name_to_uuid=samples,
            affected_sample_names=[],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        sample_contexts={
            name: SampleMetadataContext(
                sample_uuid=uuid,
                sample_id=name,
                family_uuid="family-uuid",
                family_id="CNIPT1",
                sex="female",
                project_ids=["project-uuid"],
                assembly_id="assembly-uuid",
                assembly_name="GRCh38",
            )
            for name, uuid in samples.items()
        },
        conflict_mode=conflict_mode,
    )


class _Store:
    """The writes of the import, faked at their edge."""

    def __init__(self, *, stored_rows: int = 0, stored_calls: int = 0) -> None:
        self.stored_rows = stored_rows
        self.stored_calls = stored_calls
        self.rows: list[dict[str, Any]] = []
        self.sv_records: list[Any] = []
        self.sample_metadata: dict[str, dict[str, Any]] = {}
        self.writes: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def nothing(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def stored(*_args: Any, **_kwargs: Any) -> int:
            return self.stored_rows

        async def calls(*_args: Any, **_kwargs: Any) -> int:
            return self.stored_calls

        async def no_rows(*_args: Any, **_kwargs: Any) -> int:
            return 0

        async def insert(_assembly: str, rows: list[dict[str, Any]]) -> None:
            self.writes.append("rows")
            self.rows.extend(rows)

        async def replace(_assembly, _family, _projects, records, *, source=None) -> None:
            assert source == "ccnv"
            self.writes.append("calls")
            self.sv_records.extend(records)

        async def metadata(_session: Any, *, sample_context: Any, ccnv: dict[str, Any]) -> None:
            self.writes.append("qc")
            self.sample_metadata[sample_context.sample_id] = ccnv

        for module, name, fake in (
            (family_package_datasets, "_stored_track_rows", stored),
            (family_package_datasets, "_interval_track_count", no_rows),
            (family_package_datasets, "count_family_structural_variants", calls),
            (family_package_datasets, "lock_family_variant_writes", nothing),
            (family_package_datasets, "replace_family_structural_variants", replace),
            (family_package_datasets, "_update_sv_file_metadata", nothing),
            (family_package_datasets, "record_sample_ccnv_metadata", metadata),
            (family_package_tracks, "_delete_sample_interval_source", nothing),
            (family_package_tracks, "insert_interval_track_rows", insert),
            (annotation_manifest_service, "merge_vcf_header_provenance", nothing),
        ):
            monkeypatch.setattr(module, name, fake)


@pytest.mark.asyncio
async def test_the_import_stores_the_tracks_the_calls_and_the_qc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = _Store()
    store.install(monkeypatch)
    session = _Session()
    entry = write_ccnv_output(tmp_path)

    summary = await family_package_datasets.DATASET_IMPORTERS["ccnv"](_job(tmp_path, {SAMPLE: entry}, session))

    assert summary.status == "imported", summary
    # The bins (the masked one left out) as the coverage track, the segments as the segments
    # track, each a log2 ratio with ccnv's values beside it.
    coverage = [row for row in store.rows if row["track_type"] == "coverage"]
    segments = [row for row in store.rows if row["track_type"] == "segments"]
    assert [(row["chr"], row["start"], row["value"]) for row in coverage][:2] == [("1", 100000, 0.01), ("1", 200000, -0.02)]
    assert len(coverage) == 6 and {row["source"] for row in store.rows} == {"ccnv"}
    gain = next(row for row in segments if row["chr"] == "21")
    assert json.loads(gain["metadata_json"]) | {"line_no": 0} == {
        "source": "ccnv",
        "filename": f"{SAMPLE}_segments.bed.gz",
        "line_no": 0,
        "zscore": 5.6,
        "weight": 282.0,
        "bin_count": 2,
        "call": "gain",
        "expected_ratio": 0.08482,
        "implied_ff": 0.118,
        "discordant": False,
        "frag_short_ratio": 0.205,
        "frag_llr_size": 120.5,
        "origin_call": "FETAL_CONSTITUTIONAL",
    }
    assert set(session.track_sources) == {
        ("coverage", "ccnv", f"{SAMPLE}_bins.bed.gz"),
        ("segments", "ccnv", f"{SAMPLE}_segments.bed.gz"),
    }
    # The gain as an SV of the plasma, its FORMAT values in its annotation.
    (record,) = store.sv_records
    assert (record.variant_id, record.sv_type) == ("21-1-200000-DUP----ccnv", "DUP")
    assert record.annotations[0]["format"]["IMPLIED_FF"] == "0.118"
    assert record.annotations[0]["info"]["ORIGIN"] == "FETAL_CONSTITUTIONAL"
    # ccnv writes no genotype: the plasma carries the call, at no stated zygosity.
    assert [(call.sample, call.gt) for call in record.calls] == [(SAMPLE, "./1")]
    # The QC file as ccnv wrote it.
    assert store.sample_metadata[SAMPLE] == {"qc": qc_payload(SAMPLE), "qc_file": f"{SAMPLE}_qc.json"}


@pytest.mark.asyncio
async def test_a_later_samples_refused_file_fails_the_dataset_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _Store()
    store.install(monkeypatch)
    first = write_ccnv_output(tmp_path, "CFDNA1")
    second = write_ccnv_output(tmp_path, "CFDNA2", vcf=calls_vcf("CFDNA2", fetal_sex="M"))

    with pytest.raises(family_package_datasets.ImportRefused, match="another fetal sex"):
        await family_package_datasets.DATASET_IMPORTERS["ccnv"](
            _job(tmp_path, {"CFDNA1": first, "CFDNA2": second}, _Session())
        )

    assert store.writes == []


@pytest.mark.asyncio
async def test_an_update_leaves_the_ccnv_data_a_family_holds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = _Store(stored_rows=6)
    store.install(monkeypatch)
    entry = write_ccnv_output(tmp_path)

    summary = await family_package_datasets.DATASET_IMPORTERS["ccnv"](
        _job(tmp_path, {SAMPLE: entry}, _Session(), conflict_mode="update")
    )

    assert summary.status == "skipped"
    assert store.writes == []
