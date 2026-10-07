"""Discovery, parsing and validation for the long-read (nf-core/lrsvar) package layout.

The pipeline writes ``<dataset>/<sample_id>/[annotation/]<sample_id>_<suffix>``, embeds
tool versions in filenames, and names VCF sample columns after its own intermediate
files rather than the sample. These tests pin the behaviour that makes such a package
import: version-tolerant discovery, sample-name resolution, and the new dataset types.
"""

from pathlib import Path

import gzip
import tempfile

import pytest

from app.services.family_package_common import (
    CNV_SOURCE,
    SUPPORTED_DATASETS,
    read_vcf_sample_columns,
    resolve_vcf_sample_id,
    vcf_sample_alias_map,
)
from app.services.family_package_discovery import (
    NAMING_SCHEMES,
    _build_manifest_payload,
    _choose_candidate_path,
    _glob_candidate_paths,
    _natural_sort_key,
)
from app.services.family_package_variants import _iter_cnv_structural_records
from app.services.family_package_validation import _validate_dataset
from app.services.family_package_qc import (
    extract_pipeline_versions,
    parse_mosdepth_summary_text,
    parse_nanostats_text,
    parse_pipeline_params,
)
from app.services.clickhouse_variant_ids import build_small_variant_id
from app.services.family_package_common import ManifestDataset
from app.services.annotation_table_parser import parse_mutserve_annotation_lines, parse_mutserve_annotation_path


PACBIO_SNV_PATTERNS = NAMING_SCHEMES["standard_v1"]["datasets"]["snv"]
PACBIO_SV_PATTERNS = NAMING_SCHEMES["standard_v1"]["datasets"]["sv_needlr"]


def _touch(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Version-tolerant discovery
# ---------------------------------------------------------------------------


def test_natural_sort_orders_embedded_versions_numerically() -> None:
    # Lexicographic order would put needLR.10.0 before needLR.4.0 and pick the older
    # annotation release as "newest".
    values = ["a.needLR.4.0.vcf.gz", "a.needLR.10.0.vcf.gz", "a.needLR.3.1.vcf.gz"]
    assert sorted(values, key=_natural_sort_key) == [
        "a.needLR.3.1.vcf.gz",
        "a.needLR.4.0.vcf.gz",
        "a.needLR.10.0.vcf.gz",
    ]


def test_glob_pattern_resolves_versioned_needlr_filename(tmp_path: Path) -> None:
    _touch(tmp_path / "sv/S1/annotation/S1_sv_phased.needLR.3.1.vcf.gz")
    _touch(tmp_path / "sv/S1/annotation/S1_sv_phased.needLR.4.0.vcf.gz")

    value, exists = _choose_candidate_path(
        tmp_path, PACBIO_SV_PATTERNS["family_vcf"], family_id="F1", sample_id="S1"
    )

    assert exists is True
    # The concrete path, never the pattern: validation and import treat manifest values
    # as literal paths and would fail on a '*'.
    assert value == "sv/S1/annotation/S1_sv_phased.needLR.4.0.vcf.gz"
    assert "*" not in value


def test_unmatched_glob_falls_back_to_a_literal_expected_path(tmp_path: Path) -> None:
    value, exists = _choose_candidate_path(
        tmp_path, PACBIO_SV_PATTERNS["family_vcf"], family_id="F1", sample_id="S1"
    )

    assert exists is False
    assert "*" not in value


def test_glob_candidates_cannot_escape_the_package_root(tmp_path: Path) -> None:
    _touch(tmp_path / "outside/secret.vcf.gz")
    root = tmp_path / "package"
    root.mkdir()

    assert _glob_candidate_paths(root, "../outside/*.vcf.gz") == []
    assert _glob_candidate_paths(root, "**/../../outside/*.vcf.gz") == []


def test_paraphase_glob_tolerates_a_suffixed_sample_directory(tmp_path: Path) -> None:
    # A copied package can carry a suffixed per-sample directory ("S1 2").
    _touch(tmp_path / "paraphase/S1 2/S1.paraphase.json", "{}")

    value, exists = _choose_candidate_path(
        tmp_path,
        NAMING_SCHEMES["standard_v1"]["datasets"]["paraphase"]["json"],
        family_id="F1",
        sample_id="S1",
    )

    assert exists is True
    assert value == "paraphase/S1 2/S1.paraphase.json"


def test_single_sample_family_resolves_the_per_sample_annotated_snv_vcf(tmp_path: Path) -> None:
    _touch(tmp_path / "snv/S1/annotation/S1_annot.vcf.gz")
    _touch(tmp_path / "snv/S1/annotation/S1_annot.vcf.gz.tbi")

    value, exists = _choose_candidate_path(
        tmp_path, PACBIO_SNV_PATTERNS["family_vcf"], family_id="F1", sample_id="S1"
    )

    assert (value, exists) == ("snv/S1/annotation/S1_annot.vcf.gz", True)


def test_multi_sample_family_does_not_present_one_sample_as_the_family_callset(
    tmp_path: Path,
) -> None:
    # Two samples: the per-sample annotated VCFs must NOT be picked as the family
    # callset, which would silently present one member's genotypes as the family's. They
    # are proposed as one VCF per sample instead, and a sample without one is named.
    _touch(tmp_path / "snv/S1/annotation/S1_annot.vcf.gz")
    _touch(tmp_path / "snv/S1/annotation/S1_annot.vcf.gz.tbi")
    _touch(tmp_path / "family.ped", "F1 S1 0 0 1 2\nF1 S2 0 0 2 1\n")

    payload, availability = _build_manifest_payload(
        root=tmp_path,
        family_id="F1",
        ped_relative_path="family.ped",
        sample_ids=["S1", "S2"],
        naming_scheme="standard_v1",
        hpo_terms=[],
        notes=None,
    )

    snv = next(item for item in availability if item.dataset_type == "snv")
    block = payload["datasets"]["snv"]
    assert "family_vcf" not in block
    assert block["per_sample"] == {
        "S1": {
            "vcf": "snv/S1/annotation/S1_annot.vcf.gz",
            "index": "snv/S1/annotation/S1_annot.vcf.gz.tbi",
        }
    }
    assert block["source_format"] == "clair3"
    assert snv.samples == ["S1"]
    assert snv.message is not None and "none for S2, which would have no calls" in snv.message


# ---------------------------------------------------------------------------
# VCF sample-name resolution
# ---------------------------------------------------------------------------


def test_resolve_vcf_sample_id_strips_tool_suffixes() -> None:
    family = {"HG002"}
    assert resolve_vcf_sample_id("HG002", family) == "HG002"
    # TRGT names the column after its sorted input alignment.
    assert resolve_vcf_sample_id("HG002_sort", family) == "HG002"
    # longphase names it after its own output prefix.
    assert resolve_vcf_sample_id("HG002_sv_phased", family) == "HG002"
    # An unrelated name is not silently coerced onto a family sample.
    assert resolve_vcf_sample_id("Sample0", family) is None


def test_resolve_vcf_sample_id_prefers_the_longest_matching_sample_id() -> None:
    # A short id must not shadow a longer one that shares its prefix.
    assert resolve_vcf_sample_id("S10_sort", {"S1", "S10"}) == "S10"


def test_declared_alias_overrides_the_heuristics() -> None:
    assert resolve_vcf_sample_id("weird-name", {"S1"}, declared="S1") == "S1"


def test_single_column_per_sample_vcf_binds_to_the_declared_sample() -> None:
    # HiFiCNV writes its internal slot name; the manifest says which sample the file
    # is for, and a one-column VCF under that entry belongs to that sample.
    aliases, unresolved = vcf_sample_alias_map(["Sample0"], {"HG002"}, target_sample_id="HG002")
    assert aliases == {"Sample0": "HG002"}
    assert unresolved == []


def test_unresolvable_family_level_columns_are_reported() -> None:
    aliases, unresolved = vcf_sample_alias_map(["S1_sort", "Stranger"], {"S1", "S2"})
    assert aliases == {"S1_sort": "S1"}
    assert unresolved == ["Stranger"]


def test_read_vcf_sample_columns_reads_only_the_header(tmp_path: Path) -> None:
    path = tmp_path / "calls.vcf.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("##fileformat=VCFv4.2\n")
        handle.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n")
        handle.write("chr1\t1\t.\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL\tGT:CN\t0/1:1\n")

    assert read_vcf_sample_columns(path) == ["Sample0"]


def test_read_vcf_sample_columns_handles_a_sample_less_vcf(tmp_path: Path) -> None:
    # The annotated NeedlR VCF has only the eight fixed columns.
    path = tmp_path / "sv.vcf"
    _touch(path, "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")

    assert read_vcf_sample_columns(path) == []


# ---------------------------------------------------------------------------
# CNV records
# ---------------------------------------------------------------------------


CNV_VCF = """##fileformat=VCFv4.2
##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type of the SV.">
##INFO=<ID=SVLEN,Number=1,Type=Integer,Description="Length of the SV">
##INFO=<ID=END,Number=1,Type=Integer,Description="End position">
##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: Allele|Consequence|IMPACT|SYMBOL|Gene">
##FORMAT=<ID=CN,Number=1,Type=Float,Description="Copy number">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0
chr1\t1000\t.\tN\t<DEL>\t57\tPASS\tSVTYPE=DEL;END=5000;SVLEN=4000;CSQ=deletion|transcript_ablation|HIGH|BRCA1|ENSG1,deletion|transcript_ablation|HIGH|NBR2|ENSG2\tGT:CN\t0/1:1
chr2\t2000\t.\tN\t<DUP>\t12\tTARGET_SIZE\tSVTYPE=DUP;END=9000;SVLEN=7000\tGT:CN\t1/1:4
"""


def test_cnv_records_bind_to_the_manifest_sample_not_the_vcf_column() -> None:
    records = _iter_cnv_structural_records(CNV_VCF, sample_id="HG002")

    assert [record.calls[0].sample for record in records] == ["HG002", "HG002"]


def test_cnv_records_capture_copy_number_type_and_filters() -> None:
    deletion, duplication = _iter_cnv_structural_records(CNV_VCF, sample_id="HG002")

    assert (deletion.sv_type, deletion.start, deletion.end, deletion.sv_len) == ("DEL", 1000, 5000, 4000)
    assert deletion.calls[0].copy_number == 1
    assert deletion.filters == ["PASS"]
    assert duplication.sv_type == "DUP"
    # Copy number, not just GT: 1/1 alone cannot distinguish CN=4 from CN=6.
    assert duplication.calls[0].copy_number == 4
    assert duplication.filters == ["TARGET_SIZE"]


def test_cnv_genes_come_from_the_vep_csq_field() -> None:
    # This caller records overlapping genes only in CSQ; without them the CNV dosage
    # scoring has nothing to work with.
    deletion, duplication = _iter_cnv_structural_records(CNV_VCF, sample_id="HG002")

    assert deletion.gene_symbols == ["BRCA1", "NBR2"]
    assert duplication.gene_symbols == []


def test_cnv_records_are_tagged_with_their_own_source() -> None:
    records = _iter_cnv_structural_records(CNV_VCF, sample_id="HG002")

    assert {record.source for record in records} == {CNV_SOURCE}
    # Source-scoped ids keep a depth-based CNV call from overwriting an alignment-based
    # SV call at the same coordinates.
    assert all(record.variant_id.endswith(CNV_SOURCE) for record in records)


# ---------------------------------------------------------------------------
# QC / pipeline-run parsers
# ---------------------------------------------------------------------------


NANOSTATS = """General summary:
Mean read length:                 11,981.4
Mean read quality:                    26.4
Median read length:               11,798.0
Median read quality:                  32.1
Number of reads:               5,038,912.0
Read length N50:                  14,283.0
Total bases:              60,373,225,167.0
Number, percentage and megabases of reads above quality cutoffs
>Q20:\t4736066 (94.0%) 55943.9Mb
Top 5 longest reads and their mean basecall quality score
1:\t47768 (20.6; m84299/171835517/ccs)
"""


def test_nanostats_parser_reads_the_summary_and_quality_cutoffs() -> None:
    metrics = parse_nanostats_text(NANOSTATS)

    assert metrics["mean_read_length"] == 11981.4
    assert metrics["read_length_n50"] == 14283.0
    assert metrics["read_count"] == 5038912.0
    assert metrics["total_bases"] == 60373225167.0
    assert metrics["quality_cutoffs"]["q20"] == {"reads": 4736066, "percent": 94.0}
    # The per-read "top 5" listing is detail, not a summary metric.
    assert "1" not in metrics


MOSDEPTH_SUMMARY = """chrom\tlength\tbases\tmean\tmin\tmax
chr1\t248956422\t4733592330\t19.01\t0\t10541
chr1_region\t248956422\t4733592330\t19.01\t0\t10541
chrM\t16569\t25801000\t1557.21\t0\t2000
total\t3099851116\t57549232303\t18.57\t0\t43890
"""


def test_mosdepth_summary_parser_lifts_genome_and_mito_depth() -> None:
    depth = parse_mosdepth_summary_text(MOSDEPTH_SUMMARY)

    assert depth["mean_depth"] == 18.57
    assert depth["mito_mean_depth"] == 1557.21
    # The duplicated *_region rows must not double the per-chromosome map.
    assert set(depth["mean_depth_by_chromosome"]) == {"chr1", "chrM"}


SOFTWARE_VERSIONS = """BAM_SORT:
samtools: 1.23.1
ENSEMBLVEP_VEP_CNV:
ensemblvep: 116.0
ENSEMBLVEP_VEP_SNV:
ensemblvep: 115.2
MUTSERVE_ANNOTATE:
mutserve: mtDNA Variant Detection Tools v2.0.3
https://github.com/seppinho/mutserve
(c) Sebastian Schoenherr
Workflow:
    nf-core/lrsvar: v1.0.0dev
    Nextflow: 26.04.3
"""


def test_pipeline_version_parser_survives_a_tool_that_prints_a_banner() -> None:
    # The banner lines make the file invalid YAML; losing every version because one
    # tool is chatty would silently break the traceability record.
    modules = extract_pipeline_versions(SOFTWARE_VERSIONS)

    assert modules["samtools"]["version"] == "1.23.1"
    assert modules["mutserve"]["version"].endswith("v2.0.3")
    assert modules["nf-core/lrsvar"]["version"] == "v1.0.0dev"
    # A URL inside the banner is not a tool.
    assert "https" not in modules


def test_pipeline_version_parser_keeps_both_versions_of_a_tool_run_twice() -> None:
    modules = extract_pipeline_versions(SOFTWARE_VERSIONS)

    assert "116.0" in modules["ensemblvep"]["version"]
    assert "115.2" in modules["ensemblvep"]["version"]
    assert "ENSEMBLVEP_VEP_SNV" in modules["ensemblvep"]["detail"]


def test_pipeline_params_parser_keeps_analysis_settings_and_drops_site_paths() -> None:
    parameters = parse_pipeline_params(
        '{"genome": "GRCh38", "snv_caller": "deepvariant",'
        ' "fasta": "/scratch/refs/GRCh38.fna", "max_cpus": 192}'
    )

    assert parameters["genome"] == "GRCh38"
    assert parameters["snv_caller"] == "deepvariant"
    # The reference is identified by name; the cluster path is site-specific noise.
    assert parameters["fasta"] == "GRCh38.fna"
    assert "max_cpus" not in parameters


def test_malformed_pipeline_params_do_not_raise() -> None:
    assert parse_pipeline_params("not json") == {}


# ---------------------------------------------------------------------------
# Validation of the new dataset types
# ---------------------------------------------------------------------------


def test_every_supported_dataset_reaches_a_validator(tmp_path: Path) -> None:
    # A dataset added to SUPPORTED_DATASETS without a validator branch used to be
    # reported as status="error" with no error recorded, leaving the package "valid".
    for dataset_type in SUPPORTED_DATASETS:
        errors: list = []
        summary = _validate_dataset(
            root=tmp_path,
            dataset_type=dataset_type,
            dataset=ManifestDataset(enabled=True),
            ped_sample_ids={"S1"},
            errors=errors,
        )
        assert summary.status != "error" or errors, (
            f"{dataset_type} reports an error without recording one"
        )
        assert "dataset_validator_missing" not in {issue.code for issue in errors}, (
            f"{dataset_type} has no validator branch"
        )


def test_qc_dataset_validates_with_only_optional_artefacts(tmp_path: Path) -> None:
    _touch(tmp_path / "qc/nanoplot/S1/S1NanoStats.txt", "General summary:\n")
    errors: list = []

    summary = _validate_dataset(
        root=tmp_path,
        dataset_type="qc",
        dataset=ManifestDataset(
            enabled=True,
            per_sample={"S1": {"read_stats": "qc/nanoplot/S1/S1NanoStats.txt"}},
        ),
        ped_sample_ids={"S1"},
        errors=errors,
    )

    assert (summary.status, errors) == ("valid", [])


def test_qc_dataset_without_any_artefact_is_an_error(tmp_path: Path) -> None:
    errors: list = []

    _validate_dataset(
        root=tmp_path,
        dataset_type="qc",
        dataset=ManifestDataset(enabled=True, per_sample={"S1": {}}),
        ped_sample_ids={"S1"},
        errors=errors,
    )

    assert "dataset_missing_path" in {issue.code for issue in errors}


def test_repeats_can_be_declared_per_sample(tmp_path: Path) -> None:
    # The long-read pipeline writes no joint TRGT VCF, so requiring family_vcf would
    # abort the whole package.
    _touch(tmp_path / "repeats/S1/S1_tr.vcf.gz")
    errors: list = []

    summary = _validate_dataset(
        root=tmp_path,
        dataset_type="repeats_trgt",
        dataset=ManifestDataset(
            enabled=True, per_sample={"S1": {"file": "repeats/S1/S1_tr.vcf.gz"}}
        ),
        ped_sample_ids={"S1"},
        errors=errors,
    )

    assert (summary.status, summary.samples, errors) == ("valid", ["S1"], [])


def test_pipeline_info_requires_at_least_one_run_record(tmp_path: Path) -> None:
    errors: list = []

    _validate_dataset(
        root=tmp_path,
        dataset_type="pipeline_info",
        dataset=ManifestDataset(enabled=True),
        ped_sample_ids={"S1"},
        errors=errors,
    )

    assert "dataset_missing_path" in {issue.code for issue in errors}


# ---------------------------------------------------------------------------
# Mitochondrial annotation (mutserve TSV)
# ---------------------------------------------------------------------------


MUTSERVE_TSV = (
    "ID\tFilter\tPos\tRef\tVariant\tVariantLevel\tCoverage\tMaplocus\t"
    "Phylotree17_haplogroups\tHelix_vaf_hom\n"
    "sample\tGERMLINE\t263\tA\tG\t0.98\t332\tMT-DLOOP2\tH\t0.9891725\n"
    "sample\tGERMLINE\t750\tA\tG\t.\t394\tMT-RNR1\tH,U\t.\n"
)


def test_mutserve_annotations_key_on_chrm_position_and_alleles(tmp_path: Path) -> None:
    path = _touch(tmp_path / "S1_snv_annot.txt", MUTSERVE_TSV)

    lookup = parse_mutserve_annotation_path(path)

    assert lookup is not None
    try:
        annotations = lookup.get(build_small_variant_id("M", 263, "A", "G"), "M", 263, "A", "G")
        assert annotations is not None
        annotation = annotations[0]
        assert annotation["variant_level"] == "0.98"
        assert annotation["coverage"] == "332"
        assert annotation["gene"] == "MT-DLOOP2"
        assert annotation["haplogroup"] == "H"
        assert annotation["helix_af"] == "0.9891725"
    finally:
        lookup.close()


def test_mutserve_id_column_is_never_read_as_an_rsid(tmp_path: Path) -> None:
    # The column holds the caller's sample label ("sample"); the VEP parser's alias
    # list would otherwise store it as the rsid of every mitochondrial variant.
    path = _touch(tmp_path / "S1_snv_annot.txt", MUTSERVE_TSV)

    lookup = parse_mutserve_annotation_path(path)

    assert lookup is not None
    try:
        annotations = lookup.get(build_small_variant_id("M", 263, "A", "G"), "M", 263, "A", "G")
        assert annotations is not None
        assert "rsid" not in annotations[0]
        assert "sample" not in annotations[0].values()
    finally:
        lookup.close()


def test_empty_mutserve_annotation_file_yields_no_lookup(tmp_path: Path) -> None:
    # A header-only file is the normal outcome for a run with nothing to annotate;
    # returning an empty lookup would suppress the VCF's own annotations.
    path = _touch(tmp_path / "S1_sv_annot.txt", "ID\tFilter\tPos\tRef\tVariant\n")

    assert parse_mutserve_annotation_path(path) is None


def test_missing_mutserve_annotation_file_yields_no_lookup(tmp_path: Path) -> None:
    assert parse_mutserve_annotation_path(tmp_path / "absent.txt") is None


def test_a_mutserve_parse_that_fails_leaves_no_temporary_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lookup is an SQLite file in the temporary directory, which on Cloud Run is memory.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    lookup = parse_mutserve_annotation_lines(MUTSERVE_TSV.splitlines(keepends=True))
    assert len(list(tmp_path.iterdir())) == 1
    lookup.close()
    assert list(tmp_path.iterdir()) == []

    def unreadable():
        yield from MUTSERVE_TSV.splitlines(keepends=True)
        raise OSError("read failed")

    with pytest.raises(OSError, match="read failed"):
        parse_mutserve_annotation_lines(unreadable())
    assert list(tmp_path.iterdir()) == []


# ---- chrM structural variants (Sniffles2 --mosaic -c chrM) ----------------------------

_MITO_SV_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.7.3\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
)


def _mito_sv_line(start: int, end: int, gt: str, vaf: float, dv: int, *, chrom: str = "chrM") -> str:
    return (
        f"{chrom}\t{start}\tSniffles2.DEL.1M0\tN\t<DEL>\t60\tPASS\t"
        f"PRECISE;SVTYPE=DEL;SVLEN=-{end - start};END={end};SUPPORT={dv};VAF={vaf}\t"
        f"GT:GQ:DR:DV\t{gt}:60:{400 - dv}:{dv}\n"
    )


def test_mito_sv_records_keep_the_deletion_its_heteroplasmy_and_genes() -> None:
    """The common 4977 bp deletion (m.8470-13447) at 35% heteroplasmy."""
    from app.services.family_package_variants import _iter_mito_sv_records

    text_value = _MITO_SV_HEADER + (
        _mito_sv_line(8470, 13447, "0/1", 0.35, 140)
        # Genotyped, no support: not a call.
        + _mito_sv_line(3000, 3400, "0/0", 0.0, 0)
        # Not chrM.
        + _mito_sv_line(1000, 5000, "0/1", 0.5, 20, chrom="chr1")
    )

    records = _iter_mito_sv_records(text_value, sample_id="PROBAND")

    assert len(records) == 1
    record = records[0]
    assert (record.chr, record.start, record.end, record.sv_type, record.source) == ("M", 8470, 13447, "DEL", "mito_sv")
    assert record.sv_len == -4977
    assert record.variant_id != "Sniffles2.DEL.1M0"
    assert ["MT-ATP8", "MT-ND5"] == [gene for gene in record.gene_symbols if gene in {"MT-ATP8", "MT-ND5"}]
    assert "MT-ND1" not in record.gene_symbols
    assert [(call.sample, call.gt, call.read_support) for call in record.calls] == [("PROBAND", "0/1", 140)]
    assert record.annotations[0]["sample"] == "PROBAND"
    assert record.annotations[0]["heteroplasmy"] == 0.35


def test_mito_sv_records_of_two_samples_merge_into_one_sv() -> None:
    from app.services.family_package_variants import _iter_mito_sv_records, _merge_sv_records_by_id

    mother = _iter_mito_sv_records(_MITO_SV_HEADER + _mito_sv_line(8470, 13447, "0/1", 0.05, 20), sample_id="MOTHER")
    child = _iter_mito_sv_records(_MITO_SV_HEADER + _mito_sv_line(8470, 13447, "0/1", 0.35, 140), sample_id="CHILD")

    merged = _merge_sv_records_by_id([*mother, *child])

    assert len(merged) == 1
    assert [call.sample for call in merged[0].calls] == ["MOTHER", "CHILD"]
    assert {a["sample"]: a["heteroplasmy"] for a in merged[0].annotations} == {"MOTHER": 0.05, "CHILD": 0.35}


def test_mito_sv_reimport_replaces_only_the_samples_it_brings() -> None:
    from app.services.clickhouse_variant_records import StoredStructuralVariantRow
    from app.services.family_package_variants import _iter_mito_sv_records, _mito_sv_rows_after_import

    deletion = _mito_sv_line(8470, 13447, "0/1", 0.05, 20)
    stored = _iter_mito_sv_records(_MITO_SV_HEADER + deletion, sample_id="MOTHER")[0]
    child_old = _iter_mito_sv_records(_MITO_SV_HEADER + deletion, sample_id="CHILD")[0]
    stored.calls.extend(child_old.calls)
    stored.annotations.extend(child_old.annotations)
    only_child = _iter_mito_sv_records(_MITO_SV_HEADER + _mito_sv_line(5000, 6000, "0/1", 0.2, 50), sample_id="CHILD")[0]
    stored_only_child = StoredStructuralVariantRow(project_id="p1", record=only_child)

    # The child's new file has a different deletion and lacks the shared one.
    new = _iter_mito_sv_records(_MITO_SV_HEADER + _mito_sv_line(10000, 12000, "0/1", 0.4, 160), sample_id="CHILD")

    rows = _mito_sv_rows_after_import(
        [StoredStructuralVariantRow(project_id="p1", record=stored), stored_only_child],
        new,
        replaced_samples={"CHILD", "child-uuid"},
        project_ids=["p1"],
    )

    by_start = {row.record.start: row.record for row in rows}
    # The mother's call and heteroplasmy stay; the child's old calls are gone.
    assert sorted(by_start) == [8470, 10000]
    assert [call.sample for call in by_start[8470].calls] == ["MOTHER"]
    assert [a["sample"] for a in by_start[8470].annotations] == ["MOTHER"]
    assert [call.sample for call in by_start[10000].calls] == ["CHILD"]
    assert all(row.project_id == "p1" for row in rows)



def test_mito_sv_records_read_the_column_the_importer_checked() -> None:
    from app.services.family_package_variants import _iter_mito_sv_records

    two_columns = (_MITO_SV_HEADER + _mito_sv_line(8470, 13447, "0/1", 0.35, 140)).replace(
        "FORMAT\tSample0", "FORMAT\tMOTHER\tCHILD"
    ).replace("GT:GQ:DR:DV\t0/1:60:260:140", "GT:GQ:DR:DV\t0/0:60:400:0\t0/1:60:260:140")

    # Column 0 (the mother's 0/0) would be no call at all.
    assert _iter_mito_sv_records(two_columns, sample_id="CHILD", sample_column=0) == []
    [record] = _iter_mito_sv_records(two_columns, sample_id="CHILD", sample_column=1)
    assert [(call.sample, call.gt, call.read_support) for call in record.calls] == [("CHILD", "0/1", 140)]


@pytest.mark.asyncio
async def test_the_mothers_chrm_sv_file_under_the_childs_entry_fails_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from app.schemas import FamilyImportDatasetSummary
    from app.services import family_package_datasets
    from app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
    from app.services.family_package_common import VcfSampleColumnError

    (tmp_path / "CHILD.vcf").write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tCHILD\n"
        "chrM\t3243\t.\tA\tG\t30\tPASS\t.\tGT:DP:AD:VAF\t0/1:500:400,100:0.2\n",
        encoding="utf-8",
    )
    (tmp_path / "MOTHER_sv.vcf").write_text(
        (_MITO_SV_HEADER + _mito_sv_line(8470, 13447, "0/1", 0.05, 20)).replace("Sample0", "MOTHER"),
        encoding="utf-8",
    )
    writes: list[str] = []

    async def known(_session, **_kwargs) -> set[str]:
        return {"MOTHER", "CHILD"}

    async def must_not_write(*_args, **_kwargs):
        writes.append("write")
        raise AssertionError("nothing may be written for a refused mito dataset")

    for name, fn in {
        "known_vcf_sample_ids": known,
        "upload_family_small_variant_file": must_not_write,
        "lock_family_variant_writes": must_not_write,
        "rewrite_family_structural_variants": must_not_write,
    }.items():
        monkeypatch.setattr(family_package_datasets, name, fn)
    contexts = {
        name: SampleMetadataContext(
            sample_uuid=f"{name.lower()}-uuid",
            sample_id=name,
            family_uuid="family-uuid",
            family_id="FAM",
            sex="female",
            project_ids=["p1"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for name in ("MOTHER", "CHILD")
    }
    job = family_package_datasets.DatasetImportJob(
        session=SimpleNamespace(),  # type: ignore[arg-type]
        bundle=SimpleNamespace(root=tmp_path),  # type: ignore[arg-type]
        dataset=ManifestDataset(per_sample={"CHILD": {"vcf": "CHILD.vcf", "sv_vcf": "MOTHER_sv.vcf"}}),
        summary=FamilyImportDatasetSummary(dataset_type="mito", status="valid"),
        family_context=FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="FAM",
            project_ids=["p1"],
            sample_rows=[],
            sample_uuid_to_name={},
            sample_name_to_uuid={},
            affected_sample_names=[],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        sample_contexts=contexts,
    )

    with pytest.raises(VcfSampleColumnError, match="Mito SV VCF of CHILD has no sample column for CHILD: 'MOTHER' is MOTHER"):
        await family_package_datasets._import_mito_dataset(job)

    # Refused before the child's own chrM calls were written.
    assert writes == []

# ---- per-sample files: which column holds the entry's sample ----------------------------
# A per-sample file used to be bound to its entry's sample whatever its one column named,
# so another member's -- or another patient's -- file was stored as this sample's.

_FAMILY = {"MOTHER", "CHILD", "FATHER"}


@pytest.mark.parametrize(
    ("columns", "declared", "expected"),
    [
        # HiFiCNV's own sample slot belongs to the entry's sample.
        (["Sample0"], None, 0),
        (["CHILD_sv_phased"], None, 0),
        # A joint file: the child's own column, wherever it is.
        (["MOTHER_sort", "CHILD_sort"], None, 1),
        # The operator's recorded word (the entry's vcf_sample): the column it names ...
        (["OTHER_TUBE"], "OTHER_TUBE", 0),
        # ... or, as the entry's own sample, the file's one column.
        (["MOTHER"], "CHILD", 0),
    ],
)
def test_per_sample_vcf_column_reads_the_entrys_sample(
    columns: list[str], declared: str | None, expected: int
) -> None:
    from app.services.family_package_common import per_sample_vcf_column

    assert (
        per_sample_vcf_column(
            columns, target_sample_id="CHILD", known_sample_ids=_FAMILY | {"OTHER_TUBE"}, declared=declared
        )
        == expected
    )


@pytest.mark.parametrize(
    ("columns", "declared", "message"),
    [
        (["MOTHER"], None, "'MOTHER' is MOTHER"),
        # Another family's sample (sample ids are unique across CoGA).
        (["OTHER_PATIENT_sort"], None, "'OTHER_PATIENT_sort' is OTHER_PATIENT"),
        (["CHILD", "CHILD_sort"], None, "more than one sample column for CHILD"),
        (["Sample0", "Sample1"], None, "name no known sample"),
        ([], None, "has no sample column"),
        (["Sample0"], "BOGUS", "vcf_sample 'BOGUS'"),
    ],
)
def test_per_sample_vcf_column_refuses_a_file_without_one_column_for_the_entrys_sample(
    columns: list[str], declared: str | None, message: str
) -> None:
    from app.services.family_package_common import VcfSampleColumnError, per_sample_vcf_column

    with pytest.raises(VcfSampleColumnError, match=message):
        per_sample_vcf_column(
            columns,
            target_sample_id="CHILD",
            known_sample_ids=_FAMILY | {"OTHER_PATIENT"},
            declared=declared,
            label="CNV VCF of CHILD",
        )


@pytest.mark.asyncio
async def test_known_vcf_sample_ids_never_look_a_caller_slot_up_as_a_sample() -> None:
    from types import SimpleNamespace

    from app.services.family_package_common import known_vcf_sample_ids

    seen: dict = {}

    class _Session:
        async def execute(self, statement, params=None):
            seen.update(params or {})
            rows = [{"sample_id": "CHILD"}, {"sample_id": "MOTHER"}]
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    known = await known_vcf_sample_ids(
        _Session(), family_uuid="family-uuid", header_samples=["Sample0", "CHILD_sort"]
    )

    assert known == {"CHILD", "MOTHER"}
    assert seen["family_uuid"] == "family-uuid"
    # A sample someone named "Sample0" must not turn every HiFiCNV file into theirs.
    assert "Sample0" not in seen["names"]
    assert {"CHILD_sort", "CHILD"} <= set(seen["names"])


def test_cnv_records_read_the_column_the_importer_checked() -> None:
    two_columns = CNV_VCF.replace("FORMAT\tSample0", "FORMAT\tMOTHER\tSample0").replace(
        "GT:CN\t0/1:1", "GT:CN\t0/0:2\t0/1:1"
    ).replace("GT:CN\t1/1:4", "GT:CN\t0/0:2\t1/1:4")

    deletion, duplication = _iter_cnv_structural_records(two_columns, sample_id="HG002", sample_column=1)

    assert [(call.sample, call.gt, call.copy_number) for call in deletion.calls] == [("HG002", "0/1", 1)]
    assert [(call.sample, call.gt, call.copy_number) for call in duplication.calls] == [("HG002", "1/1", 4)]


@pytest.mark.asyncio
async def test_the_cnv_import_refuses_another_members_file_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.schemas import FamilyImportDatasetSummary
    from app.services import family_package_datasets
    from app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
    from app.services.family_package_common import VcfSampleColumnError

    writes: list[str] = []

    async def known(_session, **_kwargs) -> set[str]:
        return set(_FAMILY)

    async def must_not_write(*_args, **_kwargs):
        writes.append("write")

    for name, fn in {
        "_resolve_package_path": lambda _root, value: Path(value) if value else None,
        # The mother's HiFiCNV file, renamed by hand after her, under the child's entry.
        "_read_package_text": lambda _path: CNV_VCF.replace("FORMAT\tSample0", "FORMAT\tMOTHER"),
        "known_vcf_sample_ids": known,
        "lock_family_variant_writes": must_not_write,
        "replace_family_structural_variants": must_not_write,
    }.items():
        monkeypatch.setattr(family_package_datasets, name, fn)
    contexts = {
        name: SampleMetadataContext(
            sample_uuid=f"{name.lower()}-uuid",
            sample_id=name,
            family_uuid="family-uuid",
            family_id="FAM",
            sex="female",
            project_ids=["p1"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for name in _FAMILY
    }
    job = family_package_datasets.DatasetImportJob(
        session=SimpleNamespace(),  # type: ignore[arg-type]
        bundle=SimpleNamespace(root=Path("/package")),  # type: ignore[arg-type]
        dataset=ManifestDataset(per_sample={"CHILD": {"vcf": "cnv/CHILD.vcf"}}),
        summary=FamilyImportDatasetSummary(dataset_type="cnv", status="valid"),
        family_context=FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="FAM",
            project_ids=["p1"],
            sample_rows=[],
            sample_uuid_to_name={},
            sample_name_to_uuid={},
            affected_sample_names=[],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        sample_contexts=contexts,
    )

    with pytest.raises(VcfSampleColumnError, match="'MOTHER' is MOTHER"):
        await family_package_datasets._import_cnv_dataset(job)

    assert writes == []


@pytest.mark.parametrize(
    ("query_id", "expected"),
    [
        ("S1_A", "S1_A"),
        # longphase / NeedlR name the query after the input file.
        ("S1_A_sv_phased", "S1_A"),
        ("S1_A_sort", "S1_A"),
        ("S1_sv", "S1"),
        ("S1-sv", "S1"),
        ("S10.sv", "S10"),
        # A prefix match takes the longest sample id: S1 must not shadow S1_A or S10.
        ("S1_A_run3", "S1_A"),
        ("S10_run3", "S10"),
        ("S2_sv_phased", None),
    ],
)
def test_needlr_query_resolves_to_the_family_sample_by_the_shared_rule(query_id: str, expected: str | None) -> None:
    from app.services.family_package_variants import _needlr_query_sample_id

    samples = {"S1", "S1_A", "S10"}
    assert _needlr_query_sample_id({"Query_ID": query_id}, samples) == expected
