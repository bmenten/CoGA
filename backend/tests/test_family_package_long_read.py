"""Discovery, parsing and validation for the long-read (nf-core/lrsvar) package layout.

The pipeline writes ``<dataset>/<sample_id>/[annotation/]<sample_id>_<suffix>``, embeds
tool versions in filenames, and names VCF sample columns after its own intermediate
files rather than the sample. These tests pin the behaviour that makes such a package
import: version-tolerant discovery, sample-name resolution, and the new dataset types.
"""

from pathlib import Path

import gzip

from app.services.family_package_import import (
    CNV_SOURCE,
    NAMING_SCHEMES,
    SUPPORTED_DATASETS,
    _build_manifest_payload,
    _choose_candidate_path,
    _glob_candidate_paths,
    _iter_cnv_structural_records,
    _natural_sort_key,
    _validate_dataset,
    extract_pipeline_versions,
    parse_mosdepth_summary_text,
    parse_nanostats_text,
    parse_pipeline_params,
    read_vcf_sample_columns,
    resolve_vcf_sample_id,
    vcf_sample_alias_map,
)
from app.services.clickhouse_variant_ids import build_small_variant_id
from app.services.family_package_common import ManifestDataset
from app.services.variant_upload_service import parse_mutserve_annotation_path


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
    # callset, which would silently present one member's genotypes as the family's.
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
    assert snv.complete is False
    assert payload["datasets"]["snv"]["enabled"] is False


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
