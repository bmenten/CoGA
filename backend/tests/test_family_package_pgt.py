"""Discovery, validation and import of the PGT pipeline's (nf-cmgg/copgtm) package layout.

The pipeline writes one folder per tool: the PED under ``ped/``, the QDNAseq bins under
``qdnaseq/``, a trio VCF per embryo under ``split_trio/filter_trio/`` (the APCAD input),
the SHAPEIT5-phased family VCF under ``phasing/shapeit_filter/``, and its QC per tool
(Picard ADO/ADI, RTG Tools concordance, mean coverage, KING, ngs-bits, Qualimap). Its PED
gives the embryos a sex, so CoGA cannot read them as embryos from the PED alone, and it
lacks the index, which the samplesheet the pipeline copies to ``dashboard/`` names. These
tests pin what makes such a package import: the roles and the added index, the file
patterns, the QC parsers and the importers that read them.

Every identifier and number here is synthetic.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.app.core.config import settings
from backend.app.schemas import FamilyImportDatasetSummary, FamilyPackageManifestBuildRequest
from backend.app.services import family_package_datasets, family_package_discovery, family_package_registration
from backend.app.services import family_package_source
from backend.app.services import family_package_tracks, family_package_validation
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import FamilyPackageBundle, ManifestDataset, PackageManifest, ParsedPed
from backend.app.services.family_package_datasets import DatasetImportJob
from backend.app.services.family_package_manifest import (
    _manifest_added_ped_rows,
    _manifest_affected_parent_statuses,
    _manifest_member_status_overrides,
    _manifest_pgt_metadata,
    _manifest_relationship_issues,
    _manifest_relationships,
    _parse_ped_text_strict,
    _ped_members_for_import,
)
from backend.app.services.family_package_qc import (
    parse_ado_adi_text,
    parse_haplotype_origin_text,
    parse_king_kin0_text,
    parse_ngsbits_sample_gender_text,
    parse_pipeline_params,
    parse_qualimap_genome_results_text,
    parse_sample_value_csv,
)
from backend.app.services.qc_threshold_service import evaluate_sequencing_qc


FAMILY = "PGT01"
FATHER, MOTHER, INDEX = "FATHER1", "MOTHER1", "INDEX1"
EMBRYOS = ("EMB1", "EMB2")
SAMPLES = (FATHER, MOTHER, *EMBRYOS, INDEX)


@pytest.fixture(autouse=True)
def _authorize_tmp_import_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name.endswith(".gz"):
        path.write_bytes(gzip.compress(content.encode("utf-8")))
    else:
        path.write_text(content, encoding="utf-8")
    return path


TRIO_VCF = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tFATHER1\tMOTHER1\t{embryo}\n"
    # Opposite homozygous parents, the alternate allele from the mother.
    "chr1\t100\t.\tA\tG\t50\tPASS\t.\tGT:AD\t0|0:.\t1|1:.\t0/1:6,4\n"
    # Opposite homozygous parents, the alternate allele from the father.
    "chr1\t200\t.\tC\tT\t50\tPASS\t.\tGT:AD\t1|1:.\t0|0:.\t0/1:3,7\n"
    # Both parents carry it: no parental origin.
    "chr1\t300\t.\tG\tA\t50\tPASS\t.\tGT:AD\t0|1:.\t1|1:.\t1/1:0,9\n"
    # The parents were not imputed here, and the embryo has no depth: skipped.
    "chr2\t400\t.\tT\tC\t50\tPASS\t.\tGT:AD\t./.:.\t./.:.\t0/0:0,0\n"
)

QUALIMAP = """BamQC report
-----------------------------------
>>>>>>> Globals
     number of reads = 1,000,000
     number of mapped reads = 990,000 (99.00%)
     number of duplicated reads (flagged) = 50,000
>>>>>>> Insert size
     median insert size = 350
>>>>>>> Mapping quality
     mean mapping quality = 40.5
>>>>>>> ACTG content
     GC percentage = 41.2%
>>>>>>> Mismatches and indels
    general error rate = 0.0071
>>>>>>> Coverage
     mean coverageData = 9.5X
     std coverageData = 20.1X
"""


def _write_copgtm_package(
    root: Path, *, index_in_ped: bool = False, index_parents: tuple[str, str] = (FATHER, MOTHER)
) -> Path:
    """A minimal nf-cmgg/copgtm output folder: a couple, two embryos and an index. With
    ``index_in_ped`` the PED holds the index, as the child of ``index_parents``; parents
    other than the couple are the mother's parents, and are in the PED too."""
    grandparents = index_parents if index_parents not in {(FATHER, MOTHER), ("0", "0")} else None
    ped_rows = [
        f"{FAMILY}\t{FATHER}\t0\t0\t1\t-9",
        f"{FAMILY}\t{MOTHER}\t{grandparents[0] if grandparents else 0}\t{grandparents[1] if grandparents else 0}\t2\t-9",
        f"{FAMILY}\tEMB1\t{FATHER}\t{MOTHER}\t1\t-9",
        f"{FAMILY}\tEMB2\t{FATHER}\t{MOTHER}\t2\t-9",
    ]
    if grandparents:
        ped_rows += [f"{FAMILY}\t{grandparents[0]}\t0\t0\t1\t-9", f"{FAMILY}\t{grandparents[1]}\t0\t0\t2\t-9"]
    if index_in_ped:
        ped_rows.append(f"{FAMILY}\t{INDEX}\t{index_parents[0]}\t{index_parents[1]}\t2\t-9")
    _write(root / "ped/combined.ped", "\n".join(ped_rows) + "\n")
    _write(
        root / "dashboard/samplesheet.csv",
        "id,fam,role,alignment,alignment_index,vcf,tbi\n"
        + "".join(
            f"{sample},{FAMILY},{role},/hpc/{sample}.bam,/hpc/{sample}.bam.bai,/hpc/{sample}.vcf.gz,/hpc/{sample}.vcf.gz.tbi\n"
            for sample, role in (
                ("EMB1", "embryo"),
                ("EMB2", "embryo"),
                (MOTHER, "mother"),
                (FATHER, "father"),
                (INDEX, "index"),
            )
        ),
    )
    _write(
        root / "dashboard/pedigree.csv",
        "fam,father_id,mother_id,index_id,embryo_id\n"
        + "".join(f"{FAMILY},{FATHER},{MOTHER},{INDEX},{embryo}\n" for embryo in EMBRYOS),
    )
    for sample in SAMPLES:
        _write(
            root / f"qdnaseq/{sample}_cnv.csv",
            '"","chr","start","end","position","copynumber","segmented"\n'
            '"1:1-500000","1",1,500000,250000.5,0.05,0.01\n'
            '"1:500001-1000000","1",500001,1000000,750000.5,NA,NA\n',
        )
        _write(root / f"mean/{sample}_coverage.csv", f"{sample},{1.0 if sample in (FATHER, MOTHER, INDEX) else 9.5}\n")
        sex = "male" if sample in (FATHER, "EMB1") else "female"
        _write(
            root / f"ngsbits/{sample}.tsv",
            "#file\tgender\treads_chry\treads_chrx\tratio_chry_chrx\n"
            f"{sample}.bam\t{sex}\t{100 if sex == 'male' else 1}\t1000\t{0.1 if sex == 'male' else 0.001}\n",
        )
        _write(root / f"qualimap/{sample}/genome_results.txt", QUALIMAP)
        _write(root / f"qualimap/{sample}/qualimapReport.html", "<html></html>")
        _write(root / f"cram/{sample}.cram")
        _write(root / f"cram/{sample}.cram.crai")
    for embryo in EMBRYOS:
        _write(root / f"split_trio/filter_trio/{embryo}.trio_filtered.vcf.gz", TRIO_VCF.format(embryo=embryo))
        _write(root / f"split_trio/filter_trio/{embryo}.trio_filtered.vcf.gz.tbi")
        # The embryo-only extract is an intermediate the import does not read.
        _write(root / f"split_trio/extract_embryo/{embryo}.embryo_extracted.vcf.gz")
        # The pipeline draws its PCF segmentation in a plot; CoGA reads only a PCF table.
        _write(root / f"apcad/{embryo}_pcf_apcad_plot.html", "<html></html>")
    _write(
        root / f"phasing/shapeit_filter/{FAMILY}_shapeit_rephased_final.vcf.gz",
        "##fileformat=VCFv4.2\n##source=SHAPEIT5 phase_common\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t"
        + "\t".join((INDEX, FATHER, MOTHER, *EMBRYOS))
        + "\nchr1\t100\t.\tA\tG\t.\t.\t.\tGT\t0|1\t0|0\t0|1\t0|1\t0|0\n",
    )
    _write(root / f"phasing/shapeit_filter/{FAMILY}_shapeit_rephased_final.vcf.gz.tbi")
    _write(
        root / f"phasing/haplotype_origin/{MOTHER}_affected_normal_haplotype.csv",
        '"haplotype","affected","normal"\n"H1",0,500\n"H2",400,0\n',
    )
    _write(
        root / f"phasing/haplotype_origin/{MOTHER}_haplotype_conclusion.txt",
        f"{MOTHER}: H1 is normal haplotype and H2 is affected haplotype.\n",
    )
    _write(
        root / f"picard/{FAMILY}_ADO_ADI.csv",
        "FAMILY_ID,OFFSPRING,ADO,ADI\n" f"{FAMILY},EMB1,15.5,3.25\n" f"{FAMILY},EMB2,18.0,2.75\n",
    )
    _write(root / f"rtgtools/{FAMILY}_cohort_concordance.csv", "EMB1,33.5\nEMB2,30.25\n")
    _write(root / f"rtgtools/{FAMILY}_imputed_concordance.csv", "EMB1,98.5\nEMB2,97.75\n")
    _write(
        root / f"king/{FAMILY}.kin0",
        "FID1\tID1\tFID2\tID2\tN_SNP\tHetHet\tIBS0\tKinship\n"
        f"{INDEX}\t{INDEX}\t{FATHER}\t{FATHER}\t5000\t0.0120\t0.0060\t-0.0100\n"
        f"{INDEX}\t{INDEX}\t{MOTHER}\t{MOTHER}\t5000\t0.0170\t0.0002\t0.2400\n"
        f"{FATHER}\t{FATHER}\tEMB1\tEMB1\t5000\t0.0170\t0.0001\t0.2370\n",
    )
    _write(
        root / "pipeline_info/params_2026-01-01_10-00-00.json",
        '{"affected_parent": "OLD", "roi": "chr1:1-2"}',
    )
    _write(
        root / "pipeline_info/params_2026-01-02_10-00-00.json",
        '{"affected_parent": "MOTHER1", "roi": "chr7:1000-2000", "bin_size": 500, '
        '"apcad_imputation": true, "cohort_vcf": "/hpc/user/joint_germline.vcf.gz", '
        '"outdir": "/hpc/user/out"}',
    )
    _write(
        root / "pipeline_info/copgtm_software_mqc_versions.yml",
        "QDNASEQ:\n  QDNAseq: 1.46.0\nSHAPEIT5_PHASECOMMON:\n  shapeit5: 5.1.1\nWorkflow:\n  nf-cmgg/copgtm: v1.0.0dev\n",
    )
    # The dashboard keeps copies of the results; discovery must not pick them up.
    _write(root / "dashboard/results/qdnaseq/EMB1_cnv.csv")
    return root


def _discover(root: Path) -> Any:
    return family_package_discovery.discover_family_package_manifest(
        FamilyPackageManifestBuildRequest(folder_path=str(root), naming_scheme="standard_v1")
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_reads_the_copgtm_layout(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)

    result = _discover(root)

    assert result.valid is True, result.errors
    assert result.ped_path == "ped/combined.ped"
    # The index is a sample of the family although the PED lacks it.
    assert result.sample_ids == [FATHER, MOTHER, *EMBRYOS, INDEX]
    manifest = yaml.safe_load(result.manifest_yaml)
    assert manifest["roi"] == "chr7:1000-2000"
    # The parent the run traced; the run does not say under which inheritance model.
    assert manifest["metadata"] == {"pgt": {"affected_parents": [MOTHER]}}
    assert manifest["family"] == {
        "members": {"EMB1": {"role": "embryo"}, "EMB2": {"role": "embryo"}},
        "add_members": [
            {"sample_id": INDEX, "sex": "female", "role": "relative", "clinical_status": "unknown"}
        ],
        # KING measured the index related to the mother only: a relative of unknown degree.
        "relationships": {"relatives": [{"member": INDEX, "related_to": [MOTHER]}]},
    }
    datasets = manifest["datasets"]
    assert datasets["qdnaseq"]["per_sample"]["EMB1"] == {
        "bins": "qdnaseq/EMB1_cnv.csv",
        "segments": "qdnaseq/EMB1_cnv.csv",
    }
    assert set(datasets["qdnaseq"]["per_sample"]) == set(SAMPLES)
    # Only the embryos have a trio VCF; the extracted embryo VCF is not the APCAD input.
    assert datasets["apcad"]["per_sample"] == {
        embryo: {
            "vcf": f"split_trio/filter_trio/{embryo}.trio_filtered.vcf.gz",
            "index": f"split_trio/filter_trio/{embryo}.trio_filtered.vcf.gz.tbi",
        }
        for embryo in EMBRYOS
    }
    assert datasets["haplotypes"] == {
        "enabled": True,
        "family_vcf": f"phasing/shapeit_filter/{FAMILY}_shapeit_rephased_final.vcf.gz",
        "source_format": "glimpse2",
        "index": f"phasing/shapeit_filter/{FAMILY}_shapeit_rephased_final.vcf.gz.tbi",
        "haplotype_origin": f"phasing/haplotype_origin/{MOTHER}_affected_normal_haplotype.csv",
        "haplotype_conclusion": f"phasing/haplotype_origin/{MOTHER}_haplotype_conclusion.txt",
    }
    qc = datasets["qc"]
    assert qc["per_sample"][INDEX] == {
        "report": f"qualimap/{INDEX}/qualimapReport.html",
        "qualimap_summary": f"qualimap/{INDEX}/genome_results.txt",
        "mean_coverage": f"mean/{INDEX}_coverage.csv",
        "sex_check": f"ngsbits/{INDEX}.tsv",
    }
    assert {key: qc[key] for key in ("ado_adi", "concordance", "imputed_concordance", "kinship")} == {
        "ado_adi": f"picard/{FAMILY}_ADO_ADI.csv",
        "concordance": f"rtgtools/{FAMILY}_cohort_concordance.csv",
        "imputed_concordance": f"rtgtools/{FAMILY}_imputed_concordance.csv",
        "kinship": f"king/{FAMILY}.kin0",
    }
    assert datasets["alignments"]["per_sample"]["EMB2"] == {
        "file": "cram/EMB2.cram",
        "index": "cram/EMB2.cram.crai",
    }
    # The newest run's parameters; the versions file named after the pipeline.
    assert datasets["pipeline_info"]["params"] == "pipeline_info/params_2026-01-02_10-00-00.json"
    assert datasets["pipeline_info"]["versions"] == "pipeline_info/copgtm_software_mqc_versions.yml"
    assert "dashboard/results" not in result.manifest_yaml


def test_pcf_segments_are_read_from_the_pcf_table_and_not_from_the_plot(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)

    plot_only = yaml.safe_load(_discover(root).manifest_yaml)["datasets"]
    assert not plot_only.get("pcf", {}).get("enabled")

    pcf_table = '"","sampleID","chrom","arm","start.pos","end.pos","n.probes","mean"\n"1","EMB1","7","p",1000,2000,12,0.5\n'
    _write(root / "apcad/EMB1_pcf_mat_data.csv", pcf_table)
    _write(root / "apcad/EMB1_pcf_pat_data.csv", pcf_table)

    datasets = yaml.safe_load(_discover(root).manifest_yaml)["datasets"]
    assert datasets["pcf"]["enabled"] is True
    assert datasets["pcf"]["per_sample"] == {
        "EMB1": {"maternal": "apcad/EMB1_pcf_mat_data.csv", "paternal": "apcad/EMB1_pcf_pat_data.csv"}
    }


def test_discovery_says_what_the_user_still_has_to_supply(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)

    warnings = {warning.code: warning for warning in _discover(root).warnings}

    index_warning = warnings["pgt_index_added"]
    assert index_warning.sample_id == INDEX
    assert "family.relationships" in index_warning.message
    # What KING measured against the couple, closest first.
    assert (
        f"KING (king/{FAMILY}.kin0) measured {INDEX} as first-degree to {MOTHER} "
        f"(kinship 0.240, IBS0 0.0002) and unrelated to {FATHER}"
    ) in index_warning.message
    affected = warnings["pgt_affected_parent"]
    assert affected.sample_id == MOTHER
    assert "pipeline_info/params_2026-01-02_10-00-00.json" in affected.message


def test_discovery_reads_an_index_in_the_ped_as_the_couples_proband(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY, index_in_ped=True)

    result = _discover(root)

    manifest = yaml.safe_load(result.manifest_yaml)
    assert manifest["family"] == {
        "members": {"EMB1": {"role": "embryo"}, "EMB2": {"role": "embryo"}, INDEX: {"role": "proband"}}
    }
    assert "pgt_index_added" not in {warning.code for warning in result.warnings}


def test_an_index_under_other_parents_in_the_ped_is_a_relative_linked_through_them(tmp_path: Path) -> None:
    # The affected mother's sister, under the grandparents: linked by the PED, not the couple's child.
    root = _write_copgtm_package(tmp_path / FAMILY, index_in_ped=True, index_parents=("GRANDPA1", "GRANDMA1"))

    result = _discover(root)

    family = yaml.safe_load(result.manifest_yaml)["family"]
    assert family["members"][INDEX] == {"role": "relative"}
    assert "relationships" not in family
    assert {"pgt_index_added", "pgt_index_unlinked"}.isdisjoint(warning.code for warning in result.warnings)


def test_an_index_the_ped_holds_without_parents_gets_a_proposed_link(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY, index_in_ped=True, index_parents=("0", "0"))

    result = _discover(root)

    family = yaml.safe_load(result.manifest_yaml)["family"]
    assert family["members"][INDEX] == {"role": "relative"}
    assert family["relationships"] == {"relatives": [{"member": INDEX, "related_to": [MOTHER]}]}
    unlinked = next(warning for warning in result.warnings if warning.code == "pgt_index_unlinked")
    assert f"linked under family.relationships.relatives to {MOTHER}" in unlinked.message


def test_discovery_reports_a_samplesheet_sample_the_ped_lacks(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)
    sheet = root / "dashboard/samplesheet.csv"
    sheet.write_text(sheet.read_text(encoding="utf-8") + f"EMB9,{FAMILY},embryo,,,,\n", encoding="utf-8")

    result = _discover(root)

    missing = [warning for warning in result.warnings if warning.code == "pgt_sample_not_in_ped"]
    assert [warning.sample_id for warning in missing] == ["EMB9"]
    assert "EMB9" not in result.sample_ids


def _write_index_kinship(root: Path, *, father: float, mother: float) -> None:
    _write(
        root / f"king/{FAMILY}.kin0",
        "FID1\tID1\tFID2\tID2\tN_SNP\tHetHet\tIBS0\tKinship\n"
        f"{INDEX}\t{INDEX}\t{FATHER}\t{FATHER}\t5000\t0.0170\t0.0002\t{father}\n"
        f"{INDEX}\t{INDEX}\t{MOTHER}\t{MOTHER}\t5000\t0.0170\t0.0002\t{mother}\n",
    )


def test_an_index_first_degree_to_both_parents_is_added_as_their_child(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)
    _write_index_kinship(root, father=0.24, mother=0.25)

    result = _discover(root)

    family = yaml.safe_load(result.manifest_yaml)["family"]
    assert family["add_members"] == [
        {
            "sample_id": INDEX,
            "sex": "female",
            "father": FATHER,
            "mother": MOTHER,
            "role": "proband",
            "clinical_status": "unknown",
        }
    ]
    assert "relationships" not in family
    index_warning = next(warning for warning in result.warnings if warning.code == "pgt_index_added")
    assert "as the couple's child, the proband" in index_warning.message

    # The PED the import reads holds it as their child.
    _write_discovered_manifest(root)
    validation, bundle = family_package_validation.load_validated_family_package(root)
    assert validation.valid is True, validation.errors
    assert bundle is not None
    assert f"{FAMILY} {INDEX} {FATHER} {MOTHER} 2 0 role=proband" in bundle.ped.text
    members = {
        member["sample_id"]: member
        for member in _ped_members_for_import(
            bundle.ped, member_overrides=_manifest_member_status_overrides(bundle.manifest, bundle.ped)
        )
    }
    assert (members[INDEX]["role"], members[INDEX]["father_id"], members[INDEX]["mother_id"]) == (
        "proband",
        FATHER,
        MOTHER,
    )


def test_an_index_king_finds_unrelated_is_linked_through_the_affected_parent(tmp_path: Path) -> None:
    # A relative beyond the third degree: KING sees nothing, the run's affected parent
    # gives the side.
    root = _write_copgtm_package(tmp_path / FAMILY)
    _write_index_kinship(root, father=-0.01, mother=0.01)

    result = _discover(root)

    family = yaml.safe_load(result.manifest_yaml)["family"]
    assert family["relationships"] == {"relatives": [{"member": INDEX, "related_to": [MOTHER]}]}
    index_warning = next(warning for warning in result.warnings if warning.code == "pgt_index_added")
    assert "the affected parent the run traced" in index_warning.message


def test_an_index_nothing_links_is_added_without_a_link(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)
    (root / f"king/{FAMILY}.kin0").unlink()
    for params in (root / "pipeline_info").glob("params_*.json"):
        params.unlink()

    result = _discover(root)

    family = yaml.safe_load(result.manifest_yaml)["family"]
    assert family["add_members"][0]["role"] == "relative"
    assert "relationships" not in family
    index_warning = next(warning for warning in result.warnings if warning.code == "pgt_index_added")
    assert "Declare how it is related under family.relationships" in index_warning.message


@pytest.mark.parametrize(
    ("relatives", "expected"),
    [
        ([{"member": INDEX, "related_to": [INDEX]}], ("manifest_relative_link_invalid", INDEX)),
        # A member and its parent in the PED.
        ([{"member": "EMB1", "related_to": MOTHER}], ("manifest_relative_link_invalid", "EMB1")),
        ([{"member": INDEX, "related_to": ["NOBODY"]}], ("manifest_relationship_unknown_member", "NOBODY")),
        # The couple: the import records the parents of a child as one.
        ([{"member": FATHER, "related_to": MOTHER}], ("manifest_relative_link_invalid", FATHER)),
        # The same pair twice, either way round.
        (
            [{"member": INDEX, "related_to": MOTHER}, {"member": MOTHER, "related_to": INDEX}],
            ("manifest_relative_link_invalid", MOTHER),
        ),
    ],
)
def test_a_relative_link_must_add_to_what_the_family_records(
    relatives: list[dict[str, Any]], expected: tuple[str, str]
) -> None:
    ped, errors = _parse_ped_text_strict(
        f"{FAMILY} {FATHER} 0 0 1 -9\n"
        f"{FAMILY} {MOTHER} 0 0 2 -9\n"
        f"{FAMILY} EMB1 {FATHER} {MOTHER} 1 -9\n"
        f"{FAMILY} {INDEX} 0 0 2 -9\n"
    )
    assert ped is not None and errors == []
    manifest = _manifest_with_family({"relationships": {"relatives": relatives}})

    issues = _manifest_relationship_issues(manifest, set(ped.sample_ids), ped)

    assert [(issue.code, issue.sample_id) for issue in issues] == [expected]


def test_rediscovery_keeps_the_family_block_the_user_wrote(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)
    written = yaml.safe_load(_discover(root).manifest_yaml)
    written["family"]["relationships"] = {"parent_child": [{"child": MOTHER, "parents": ["0", INDEX]}]}
    written["roi"] = "CFTR"
    (root / "manifest.yaml").write_text(yaml.safe_dump(written, sort_keys=False), encoding="utf-8")

    manifest = yaml.safe_load(_discover(root).manifest_yaml)

    assert manifest["family"]["relationships"] == written["family"]["relationships"]
    assert manifest["roi"] == "CFTR"
    assert INDEX in manifest["samples"]


def test_rediscovery_keeps_the_affected_parent_and_model_the_manifest_records(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)
    manifest = yaml.safe_load(_discover(root).manifest_yaml)
    manifest["metadata"]["pgt"] = {"inheritance_model": "AD", "affected_parents": [FATHER]}
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    rediscovered = yaml.safe_load(_discover(root).manifest_yaml)

    assert rediscovered["metadata"]["pgt"] == {"inheritance_model": "AD", "affected_parents": [FATHER]}


def test_package_list_finds_a_folder_whose_ped_is_in_its_ped_folder(tmp_path: Path) -> None:
    _write_copgtm_package(tmp_path / FAMILY)

    packages = family_package_source.scan_family_import_packages()

    assert [(package["name"], package["has_ped"]) for package in packages] == [(FAMILY, True)]


def test_every_file_role_discovery_writes_leaves_a_provenance_record() -> None:
    # A role missing from the provenance allowlist imports its file without a
    # raw_import_files row, so the traceability record would not name it.
    roles = {
        role
        for dataset in family_package_discovery.NAMING_SCHEMES["standard_v1"]["datasets"].values()
        for role in dataset
        # A pattern list, not a manifest key: discovery writes what it finds as `index`.
        if role != "sample_index"
    }

    assert roles - family_package_registration._PROVENANCE_PATH_KEYS == set()


# ---------------------------------------------------------------------------
# Validation and the added index
# ---------------------------------------------------------------------------


def _write_discovered_manifest(root: Path) -> None:
    family_package_discovery.write_family_package_manifest(
        folder_path=root, manifest_yaml=_discover(root).manifest_yaml, overwrite=True
    )


def test_the_discovered_manifest_validates_with_the_index_as_a_member(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)
    _write_discovered_manifest(root)

    validation, bundle = family_package_validation.load_validated_family_package(root)

    assert validation.valid is True, validation.errors
    assert validation.sample_ids == [FATHER, MOTHER, *EMBRYOS, INDEX]
    assert validation.metadata["added_members"] == [INDEX]
    assert validation.metadata["roi"] == "chr7:1000-2000"
    statuses = {summary.dataset_type: summary.status for summary in validation.datasets}
    for dataset_type in ("qdnaseq", "apcad", "haplotypes", "qc", "alignments", "pipeline_info"):
        assert statuses[dataset_type] == "valid", dataset_type
    assert bundle is not None
    members = {
        member["sample_id"]: member
        for member in _ped_members_for_import(
            bundle.ped, member_overrides=_manifest_member_status_overrides(bundle.manifest, bundle.ped)
        )
    }
    assert {sample_id: member["role"] for sample_id, member in members.items()} == {
        FATHER: "father",
        MOTHER: "mother",
        "EMB1": "embryo",
        "EMB2": "embryo",
        INDEX: "relative",
    }
    assert (members[INDEX]["sex"], members[INDEX]["father_id"], members[INDEX]["mother_id"]) == (
        "female",
        None,
        None,
    )
    # The family's stored pedigree holds the added member like any other.
    assert f"{FAMILY} {INDEX} 0 0 2 0 role=relative" in bundle.ped.text
    # ... and the family gets the proposed link to the mother.
    assert [
        (relationship["relationship_type"], relationship["sample_id_a"], relationship["sample_id_b"])
        for relationship in _manifest_relationships(bundle.manifest)
    ] == [("relative", MOTHER, INDEX)]


def test_a_phased_vcf_sample_the_family_lacks_fails_validation_before_anything_is_written(
    tmp_path: Path,
) -> None:
    # Without the added index the loader would stop at its column, after the other
    # datasets were imported.
    root = _write_copgtm_package(tmp_path / FAMILY)
    manifest = yaml.safe_load(_discover(root).manifest_yaml)
    del manifest["family"]["add_members"]
    del manifest["family"]["relationships"]
    del manifest["samples"][INDEX]
    for dataset in manifest["datasets"].values():
        dataset.get("per_sample", {}).pop(INDEX, None)
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    validation = family_package_validation.validate_family_package(root)

    assert validation.valid is False
    assert [(error.code, error.dataset) for error in validation.errors] == [
        ("dataset_vcf_sample_unknown", "haplotypes")
    ]
    assert INDEX in validation.errors[0].message


def test_a_trio_vcf_without_the_embryos_column_fails_and_without_a_parents_warns(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)
    _write(
        root / "split_trio/filter_trio/EMB1.trio_filtered.vcf.gz",
        TRIO_VCF.format(embryo="EMB1").replace("FATHER1\t", "OTHER1\t"),
    )
    _write(root / "split_trio/filter_trio/EMB2.trio_filtered.vcf.gz", TRIO_VCF.format(embryo="EMB9"))
    _write_discovered_manifest(root)

    validation = family_package_validation.validate_family_package(root)

    assert [(error.code, error.sample_id) for error in validation.errors] == [
        ("dataset_vcf_sample_missing", "EMB2")
    ]
    apcad_warnings = [warning for warning in validation.warnings if warning.code.startswith("apcad_")]
    assert [(warning.code, warning.sample_id) for warning in apcad_warnings] == [
        ("apcad_parent_column_missing", "EMB1")
    ]
    assert FATHER in apcad_warnings[0].message


def _manifest_with_family(family: dict[str, Any]) -> PackageManifest:
    return PackageManifest.model_validate({"ped": "ped/combined.ped", "family": family})


def test_adding_a_member_the_ped_already_holds_is_an_error() -> None:
    rows, errors = _manifest_added_ped_rows(
        _manifest_with_family({"add_members": [{"sample_id": MOTHER, "sex": "female"}]}),
        family_id=FAMILY,
        ped_sample_ids={FATHER, MOTHER},
    )

    assert rows == []
    assert [error.code for error in errors] == ["manifest_added_member_in_ped"]


def test_a_stored_pedigree_may_already_hold_an_added_member() -> None:
    # An import into an existing family without a PED file reads the stored pedigree,
    # which holds the member the first import added.
    rows, errors = _manifest_added_ped_rows(
        _manifest_with_family({"add_members": {INDEX: {"sex": "female"}}}),
        family_id=FAMILY,
        ped_sample_ids={FATHER, MOTHER, INDEX},
        ped_from_database=True,
    )

    assert (rows, errors) == ([], [])


@pytest.mark.parametrize(
    "entry",
    [
        {"sample_id": INDEX, "role": "grandmother"},
        {"sample_id": INDEX, "sex": "x"},
        {"sample_id": INDEX, "clinical_status": "maybe"},
        {"sex": "female"},
    ],
)
def test_an_added_member_with_an_unsupported_field_is_an_error(entry: dict[str, Any]) -> None:
    rows, errors = _manifest_added_ped_rows(
        _manifest_with_family({"add_members": [entry]}),
        family_id=FAMILY,
        ped_sample_ids={FATHER, MOTHER},
    )

    assert rows == []
    assert [error.code for error in errors] == ["manifest_added_member_invalid"]


def test_an_added_member_listed_twice_is_an_error() -> None:
    rows, errors = _manifest_added_ped_rows(
        _manifest_with_family({"add_members": [{"sample_id": INDEX}, {"sample_id": INDEX}]}),
        family_id=FAMILY,
        ped_sample_ids={FATHER, MOTHER},
    )

    assert rows == [f"{FAMILY} {INDEX} 0 0 0 0 role=relative"]
    assert [error.code for error in errors] == ["manifest_added_member_duplicate"]


def test_a_relationship_naming_someone_outside_the_family_fails_validation(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)
    manifest = yaml.safe_load(_discover(root).manifest_yaml)
    manifest["family"]["relationships"] = {"parent_child": [{"child": MOTHER, "parents": ["0", "NOBODY"]}]}
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    validation = family_package_validation.validate_family_package(root)

    assert validation.valid is False
    assert [(error.code, error.sample_id) for error in validation.errors] == [
        ("manifest_relationship_unknown_member", "NOBODY")
    ]


def test_linking_the_added_index_to_the_affected_parent_validates(tmp_path: Path) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)
    manifest = yaml.safe_load(_discover(root).manifest_yaml)
    manifest["family"]["relationships"] = {"parent_child": [{"child": MOTHER, "parents": ["0", INDEX]}]}
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    assert family_package_validation.validate_family_package(root).valid is True


# ---------------------------------------------------------------------------
# The affected parent's status
# ---------------------------------------------------------------------------


def _couple_ped(mother_phenotype: str = "-9", mother_columns: str = "") -> ParsedPed:
    ped, errors = _parse_ped_text_strict(
        f"{FAMILY} {FATHER} 0 0 1 -9\n"
        f"{FAMILY} {MOTHER} 0 0 2 {mother_phenotype}{' ' + mother_columns if mother_columns else ''}\n"
        f"{FAMILY} EMB1 {FATHER} {MOTHER} 1 -9\n"
    )
    assert ped is not None and errors == []
    return ped


def _pgt_manifest(pgt: dict[str, Any], family: dict[str, Any] | None = None) -> PackageManifest:
    payload: dict[str, Any] = {"ped": "ped/combined.ped", "metadata": {"pgt": pgt}}
    if family is not None:
        payload["family"] = family
    return PackageManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("inheritance_model", "parent", "status"),
    [
        ("AD", MOTHER, {"clinical_status": "affected"}),
        ("XLD", FATHER, {"clinical_status": "affected"}),
        ("AR", FATHER, {"carrier_status": "carrier", "carrier_type": "proven"}),
        ("XLR", MOTHER, {"carrier_status": "carrier", "carrier_type": "proven"}),
        ("XLR", FATHER, {"clinical_status": "affected"}),
    ],
)
def test_the_affected_parent_gets_the_status_the_inheritance_model_asks(
    inheritance_model: str, parent: str, status: dict[str, str]
) -> None:
    manifest = _pgt_manifest({"inheritance_model": inheritance_model, "affected_parents": [parent]})

    derived, errors, warnings = _manifest_affected_parent_statuses(manifest, _couple_ped())

    assert errors == []
    assert [(warning.code, warning.sample_id) for warning in warnings] == [("pgt_affected_parent_status", parent)]
    expected = dict(status)
    if "carrier_status" in status:
        expected["carrier_evidence"] = {
            "derived_from": "metadata.pgt.affected_parents",
            "inheritance_model": inheritance_model,
        }
    assert derived == {parent: expected}
    # The import writes it, and leaves the other parent as the PED has it.
    members = {
        member["sample_id"]: member
        for member in _ped_members_for_import(
            _couple_ped(), member_overrides=_manifest_member_status_overrides(manifest, _couple_ped())
        )
    }
    assert {key: members[parent][key] for key in expected} == expected
    other = FATHER if parent == MOTHER else MOTHER
    assert (members[other]["clinical_status"], members[other]["carrier_status"]) == ("unknown", "unknown")


def test_without_an_inheritance_model_the_affected_parent_keeps_its_status() -> None:
    derived, errors, warnings = _manifest_affected_parent_statuses(
        _pgt_manifest({"affected_parents": [MOTHER]}), _couple_ped()
    )

    assert (derived, errors) == ({}, [])
    assert [warning.code for warning in warnings] == ["pgt_affected_parent_needs_model"]
    assert "metadata.pgt.inheritance_model" in warnings[0].message


def test_mitochondrial_inheritance_gives_the_affected_parent_no_status() -> None:
    derived, errors, warnings = _manifest_affected_parent_statuses(
        _pgt_manifest({"inheritance_model": "mitochondrial", "affected_parents": [MOTHER]}), _couple_ped()
    )

    assert (derived, errors) == ({}, [])
    assert [warning.code for warning in warnings] == ["pgt_affected_parent_no_status"]


@pytest.mark.parametrize(
    ("inheritance_model", "pgt_extra", "family", "mother_phenotype", "conflict"),
    [
        ("AD", {}, {"members": {MOTHER: {"clinical_status": "unaffected"}}}, "-9", True),
        ("AD", {}, None, "1", True),
        ("AD", {}, {"members": {MOTHER: {"clinical_status": "affected"}}}, "-9", False),
        ("AR", {}, {"members": {MOTHER: {"carrier_status": "not_carrier"}}}, "-9", True),
        ("AR", {"obligate_carriers": [MOTHER]}, None, "-9", False),
    ],
)
def test_a_recorded_status_wins_over_the_derived_one(
    inheritance_model: str,
    pgt_extra: dict[str, Any],
    family: dict[str, Any] | None,
    mother_phenotype: str,
    conflict: bool,
) -> None:
    manifest = _pgt_manifest(
        {"inheritance_model": inheritance_model, "affected_parents": [MOTHER], **pgt_extra}, family
    )

    derived, errors, warnings = _manifest_affected_parent_statuses(manifest, _couple_ped(mother_phenotype))

    assert (derived, errors) == ({}, [])
    assert [warning.code for warning in warnings] == (["pgt_affected_parent_status_conflict"] if conflict else [])


def test_a_ped_that_says_not_a_carrier_wins_over_the_derived_carrier_status() -> None:
    manifest = _pgt_manifest({"inheritance_model": "AR", "affected_parents": [MOTHER]})

    derived, errors, warnings = _manifest_affected_parent_statuses(manifest, _couple_ped(mother_columns="carrier=0"))

    assert (derived, errors) == ({}, [])
    assert [warning.code for warning in warnings] == ["pgt_affected_parent_status_conflict"]
    assert "not carrier" in warnings[0].message


@pytest.mark.parametrize(
    ("parent", "code"),
    [("NOBODY", "manifest_affected_parent_unknown"), ("EMB1", "manifest_affected_parent_not_parent")],
)
def test_the_affected_parent_must_be_a_parent_in_the_family(parent: str, code: str) -> None:
    derived, errors, warnings = _manifest_affected_parent_statuses(
        _pgt_manifest({"inheritance_model": "AD", "affected_parents": [parent]}), _couple_ped()
    )

    assert (derived, warnings) == ({}, [])
    assert [(error.code, error.sample_id) for error in errors] == [(code, parent)]


def test_the_pipelines_name_for_the_affected_parent_is_read_and_kept_on_the_family() -> None:
    manifest = _pgt_manifest({"inheritance_model": "AR", "affected_parent": MOTHER})

    assert _manifest_pgt_metadata(manifest) == {"inheritance_model": "AR", "affected_parents": [MOTHER]}


def test_a_discovered_manifest_with_the_model_set_shows_the_derived_status_before_the_import(
    tmp_path: Path,
) -> None:
    root = _write_copgtm_package(tmp_path / FAMILY)
    manifest = yaml.safe_load(_discover(root).manifest_yaml)
    manifest["metadata"]["pgt"]["inheritance_model"] = "AD"
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    validation = family_package_validation.validate_family_package(root)

    assert validation.valid is True, validation.errors
    status = [warning for warning in validation.warnings if warning.code.startswith("pgt_affected_parent")]
    assert [(warning.code, warning.sample_id) for warning in status] == [("pgt_affected_parent_status", MOTHER)]
    assert f"{MOTHER} is recorded as affected" in status[0].message


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def test_sample_value_csv_reads_headerless_rows_and_skips_a_header() -> None:
    assert parse_sample_value_csv("sample,coverage\nEMB1,9.5\nEMB2,NA\n\nEMB3,10\n") == {
        "EMB1": 9.5,
        "EMB3": 10.0,
    }


def test_ado_adi_summary_is_read_by_its_header() -> None:
    assert parse_ado_adi_text("FAMILY_ID,OFFSPRING,ADO,ADI\nF,EMB1,15.5,3.25\nF,EMB2,,1\n") == {
        "EMB1": {"allele_dropout_rate": 15.5, "allele_dropin_rate": 3.25},
        "EMB2": {"allele_dropin_rate": 1.0},
    }
    assert parse_ado_adi_text("a,b\n1,2\n") == {}


def test_king_kin0_pairs_are_read_by_column_name() -> None:
    pairs = parse_king_kin0_text(
        "FID1\tID1\tFID2\tID2\tN_SNP\tHetHet\tIBS0\tKinship\n"
        "A\tA\tB\tB\t5000\t0.0170\t0.0002\t0.2400\n"
        "A\tA\tC\tC\t5000\t0.0120\t0.0060\tnan\n"
    )

    assert pairs == [
        {"sample_a": "A", "sample_b": "B", "kinship": 0.24, "n_snp": 5000, "het_het": 0.017, "ibs0": 0.0002}
    ]


def test_ngsbits_sex_check_reports_the_inferred_sex() -> None:
    assert parse_ngsbits_sample_gender_text(
        "#file\tgender\treads_chry\treads_chrx\tratio_chry_chrx\nS.bam\tmale\t100\t1000\t0.1\n"
    ) == {
        "method": "ngs-bits SampleGender",
        "inferred_sex": "male",
        "reads_chry": 100,
        "reads_chrx": 1000,
        "ratio_chry_chrx": 0.1,
    }
    assert parse_ngsbits_sample_gender_text(
        "#file\tgender\nS.bam\tunknown (too few reads)\n"
    )["inferred_sex"] == "indeterminate"


def test_qualimap_summary_lifts_the_headline_alignment_metrics() -> None:
    assert parse_qualimap_genome_results_text(QUALIMAP) == {
        "read_count": 1_000_000,
        "mapped_reads": 990_000,
        "mapped_reads_percent": 99.0,
        "duplicated_reads": 50_000,
        "duplicated_reads_percent": 5.0,
        "median_insert_size": 350.0,
        "mean_mapping_quality": 40.5,
        "gc_percent": 41.2,
        "general_error_rate": 0.0071,
        "mean_coverage": 9.5,
        "std_coverage": 20.1,
    }


def test_haplotype_origin_table_keeps_the_counts_per_haplotype() -> None:
    assert parse_haplotype_origin_text('"haplotype","affected","normal"\n"H1",0,500\n"H2",400,0\n') == [
        {"haplotype": "H1", "affected": 0, "normal": 500},
        {"haplotype": "H2", "affected": 400, "normal": 0},
    ]


def test_pgt_run_parameters_are_kept_and_site_paths_reduced() -> None:
    assert parse_pipeline_params(
        '{"affected_parent": "MOTHER1", "roi": "chr7:1000-2000", "bin_size": 500, '
        '"apcad_imputation": true, "cohort_vcf": "/hpc/user/joint.vcf.gz", '
        '"shapeit_reference_panel": "/hpc/user/panel.csv", "outdir": "/hpc/user/out"}'
    ) == {
        "affected_parent": "MOTHER1",
        "roi": "chr7:1000-2000",
        "bin_size": 500,
        "apcad_imputation": True,
        "cohort_vcf": "joint.vcf.gz",
        "shapeit_reference_panel": "panel.csv",
    }


def test_embryo_qc_metrics_are_gated_by_the_configured_cut_offs() -> None:
    qc = {"depth": {"mean_depth": 9.5}, "pgt": {"allele_dropout_rate": 18.0, "mendelian_concordance_imputed": 97.75}}

    result = evaluate_sequencing_qc(
        qc,
        {
            "pgt.allele_dropout_rate": {"warn_value": 15.0, "error_value": 25.0},
            "pgt.mendelian_concordance_imputed": {"warn_value": 95.0, "error_value": 90.0},
        },
    )

    assert result is not None
    verdicts = {entry["metric_key"]: entry["verdict"] for entry in result["metrics"]}
    assert verdicts["pgt.allele_dropout_rate"] == "warn"
    assert verdicts["pgt.mendelian_concordance_imputed"] == "pass"


# ---------------------------------------------------------------------------
# Importers
# ---------------------------------------------------------------------------


def _sample_context(sample_id: str) -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid=f"uuid-{sample_id}",
        sample_id=sample_id,
        family_uuid="family-uuid",
        family_id=FAMILY,
        sex="und",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _family_context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id=FAMILY,
        project_ids=["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={},
        sample_name_to_uuid={},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _job(root: Path, dataset_type: str, progress: Any = None) -> DatasetImportJob:
    _write_discovered_manifest(root)
    validation, bundle = family_package_validation.load_validated_family_package(root)
    assert bundle is not None, validation.errors
    return DatasetImportJob(
        session=object(),  # type: ignore[arg-type]
        bundle=bundle,
        dataset=bundle.manifest.datasets[dataset_type],
        summary=FamilyImportDatasetSummary(dataset_type=dataset_type, enabled=True, status="valid"),
        family_context=_family_context(),
        sample_contexts={sample: _sample_context(sample) for sample in SAMPLES},
        progress=progress,
    )


@pytest.mark.asyncio
async def test_qc_import_records_each_samples_qc_and_the_familys_kinship(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorded: dict[str, dict[str, Any]] = {}
    family_qc: list[dict[str, Any]] = []

    async def record_sample(_session: Any, *, sample_context: SampleMetadataContext, metrics: dict[str, Any]) -> None:
        recorded[sample_context.sample_id] = metrics

    async def record_family(_session: Any, *, family_uuid: str, pipeline_qc: dict[str, Any]) -> None:
        family_qc.append(pipeline_qc)

    monkeypatch.setattr(family_package_datasets, "_record_sample_qc_metadata", record_sample)
    monkeypatch.setattr(family_package_datasets, "_record_family_pipeline_qc", record_family)
    root = _write_copgtm_package(tmp_path / FAMILY)

    result = await family_package_datasets._import_qc_dataset(_job(root, "qc"))

    assert result.status == "imported"
    assert set(recorded) == set(SAMPLES)
    embryo = recorded["EMB2"]
    assert embryo["depth"] == {"mean_depth": 9.5}
    assert embryo["pgt"] == {
        "allele_dropout_rate": 18.0,
        "allele_dropin_rate": 2.75,
        "mendelian_concordance": 30.25,
        "mendelian_concordance_imputed": 97.75,
    }
    assert embryo["sex_check"]["inferred_sex"] == "female"
    assert embryo["alignment"]["mapped_reads_percent"] == 99.0
    assert embryo["report"] == "qualimap/EMB2/qualimapReport.html"
    # A parent has no embryo metrics; the index's QC is recorded like any member's.
    assert "pgt" not in recorded[MOTHER]
    assert recorded[INDEX]["depth"] == {"mean_depth": 1.0}
    assert len(family_qc) == 1
    assert family_qc[0]["files"]["kinship"] == f"king/{FAMILY}.kin0"
    assert [(pair["sample_a"], pair["sample_b"]) for pair in family_qc[0]["kinship"]] == [
        (INDEX, FATHER),
        (INDEX, MOTHER),
        (FATHER, "EMB1"),
    ]


@pytest.mark.asyncio
async def test_a_trio_vcf_gives_the_embryos_allele_fraction_and_its_parental_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    inserted: list[dict[str, Any]] = []

    async def capture(_session: Any, rows: list[dict[str, Any]]) -> None:
        inserted.extend(rows)

    monkeypatch.setattr(family_package_tracks, "_insert_interval_track_rows", capture)
    root = _write_copgtm_package(tmp_path / FAMILY)
    job = _job(root, "apcad")

    results = await family_package_tracks._import_apcad_vcf_file(
        object(),  # type: ignore[arg-type]
        path=root / "split_trio/filter_trio/EMB1.trio_filtered.vcf.gz",
        sample_contexts=job.sample_contexts,
        ped=job.bundle.ped,
        selected_sample_id="EMB1",
    )

    assert results == {"EMB1": {"processed": 4, "inserted": 3, "skipped": 1}}
    assert [(row["chr"], row["end"], row["value"], row["origin"]) for row in inserted] == [
        ("1", 100, 0.4, "maternal"),
        ("1", 200, 0.7, "paternal"),
        ("1", 300, 1.0, "und"),
    ]
    assert {row["sample_id"] for row in inserted} == {"uuid-EMB1"}


@pytest.mark.asyncio
async def test_the_apcad_import_reads_each_embryos_trio_vcf_and_reports_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str | None, str]] = []
    reports: list[FamilyImportDatasetSummary] = []

    async def fake_track_file(_session: Any, *, path: Path, selected_sample_id: str | None = None, **_kwargs: Any) -> dict[str, Any]:
        calls.append((selected_sample_id, path.name))
        return {selected_sample_id: {"processed": 4, "inserted": 3, "skipped": 1}}

    async def no_tracks(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def progress(summary: FamilyImportDatasetSummary) -> None:
        reports.append(summary)

    monkeypatch.setattr(family_package_datasets, "_import_apcad_track_file", fake_track_file)
    monkeypatch.setattr(family_package_datasets, "_interval_track_count", no_tracks)
    root = _write_copgtm_package(tmp_path / FAMILY)

    result = await family_package_datasets._import_apcad_dataset(_job(root, "apcad", progress=progress))

    assert result.status == "imported"
    assert calls == [("EMB1", "EMB1.trio_filtered.vcf.gz"), ("EMB2", "EMB2.trio_filtered.vcf.gz")]
    assert result.summary == {
        "EMB1": {"processed": 4, "inserted": 3, "skipped": 1},
        "EMB2": {"processed": 4, "inserted": 3, "skipped": 1},
    }
    # One report after each embryo, so a long APCAD import keeps the job's heartbeat.
    assert [sorted(report.summary) for report in reports] == [["EMB1"], ["EMB1", "EMB2"]]
    assert {report.status for report in reports} == {"running"}


@pytest.mark.asyncio
async def test_the_pipelines_haplotype_origin_is_recorded_on_the_family(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorded: list[dict[str, Any]] = []

    async def record(_session: Any, *, family_uuid: str, haplotype_origin: dict[str, Any]) -> None:
        recorded.append(haplotype_origin)

    monkeypatch.setattr(family_package_datasets, "_record_family_haplotype_origin", record)
    root = _write_copgtm_package(tmp_path / FAMILY)

    origin = await family_package_datasets._record_pipeline_haplotype_origin(_job(root, "haplotypes"))

    assert origin == {
        "parent": MOTHER,
        "haplotypes": [
            {"haplotype": "H1", "affected": 0, "normal": 500},
            {"haplotype": "H2", "affected": 400, "normal": 0},
        ],
        "conclusion": f"{MOTHER}: H1 is normal haplotype and H2 is affected haplotype.",
        "files": {
            "haplotype_origin": f"phasing/haplotype_origin/{MOTHER}_affected_normal_haplotype.csv",
            "haplotype_conclusion": f"phasing/haplotype_origin/{MOTHER}_haplotype_conclusion.txt",
        },
    }
    assert recorded == [origin]


@pytest.mark.asyncio
async def test_a_qc_dataset_without_files_records_nothing(tmp_path: Path) -> None:
    root = tmp_path / FAMILY
    root.mkdir()
    job = DatasetImportJob(
        session=object(),  # type: ignore[arg-type]
        bundle=FamilyPackageBundle(
            root=root,
            manifest_path=root / "manifest.yaml",
            manifest=PackageManifest(ped="family.ped"),
            ped_path=root / "family.ped",
            ped=family_package_validation.ParsedPed(family_ids=[], members=[], sample_ids=[], text=""),
        ),
        dataset=ManifestDataset(),
        summary=FamilyImportDatasetSummary(dataset_type="qc", enabled=True, status="valid"),
        family_context=_family_context(),
        sample_contexts={},
    )

    result = await family_package_datasets._import_qc_dataset(job)

    assert result.status == "registered"
