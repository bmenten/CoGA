"""Synthetic evidence and explicit exploratory genotype-policy boundaries."""
from collections import Counter
import hashlib

import pytest

from backend.app.services.wgs_evidence import WgsInputError
from backend.app.services.ancestry_projection import AncestryMarker, _call
from backend.app.services.wgs_evidence import WgsEvidence, gt_hash
from backend.app.services.wgs_source_service import scan_vcf


def source(rows, samples="PROBAND", build="GRCh38"):
    return (f"##fileformat=VCFv4.2\n##reference={build}\n"
            f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{samples}\n" + "".join(rows))


def record(pos=10, gt="0/1", filt="."):
    return f"chr1\t{pos}\t.\tA\tG\t40\t{filt}\t.\tGT\t{gt}\n"


def test_genome_distribution_is_an_evidence_hint_not_a_full_wgs_declaration():
    evidence = WgsEvidence(samples=["PROBAND"], records=1_000_000,
        chromosomes=Counter({str(i): 40_000 for i in range(1, 23)}),
        bins={(str(c), b) for c in range(1, 23) for b in range(50)}, builds={"GRCh38"})
    evidence.filters["."] = 1_000_000
    result = evidence.summary()
    assert result["genome_wide_candidate"] and len(result["autosomes"]) == 22
    assert "full_wgs" not in result and "absent_record_is_hom_ref" not in result
    assert "not PASS" in result["warnings"][0]
    evidence.imputed = True
    assert not evidence.summary()["genome_wide_candidate"]


def test_fingerprint_has_an_independent_utf8_and_byte_order_oracle():
    payload = b"1\t10\tA\tG\tPROBAND\t0|1\t."
    assert gt_hash("1", 10, "A", "G", "PROBAND", "0|1", ".") == int.from_bytes(hashlib.sha256(payload).digest()[:8], "little")


def test_source_scan_is_order_independent_and_preserves_unassessed_filter_evidence(tmp_path):
    a = tmp_path / "source.vcf"
    b = tmp_path / "reordered.vcf"
    a.write_text(source([record(10, "0/1\t1/1"), record(20, "./.\t0/1", "PASS")], "PROBAND\tMOTHER"))
    b.write_text(source([record(20, "0/1\t./.", "PASS"), record(10, "1/1\t0/1")], "MOTHER\tPROBAND"))
    first = scan_vcf(a, ["MOTHER", "PROBAND"])
    second = scan_vcf(b, ["PROBAND", "MOTHER"])
    assert first.fingerprint() == second.fingerprint()
    assert first.summary()["filter_counts"] == {".": 1, "PASS": 1}
    assert not first.summary()["genome_wide_candidate"]
    b.write_text(b.read_text().replace("1/1", "0/0"))
    assert scan_vcf(b, ["PROBAND", "MOTHER"]).fingerprint() != first.fingerprint()


@pytest.mark.parametrize("samples", ["MOTHER", "PROBAND\tPROBAND"])
def test_other_or_duplicate_sample_columns_are_refused_before_registration(tmp_path, samples):
    p = tmp_path / "source.vcf"
    p.write_text(source([record()], samples))
    with pytest.raises(WgsInputError, match="exactly this family's"):
        scan_vcf(p, ["PROBAND"])


def test_only_primary_reference_header_sets_build_and_symbolic_blocks_are_identified(tmp_path):
    p = tmp_path / "source.vcf"
    p.write_text(source([record()]).replace("##reference=GRCh38", "##source_reference_GRCh37=hg19\n##reference=GRCh38"))
    assert scan_vcf(p, ["PROBAND"]).builds == {"GRCh38"}
    p.write_text(p.read_text().replace("\tG\t", "\t<NON_REF>\t"))
    assert scan_vcf(p, ["PROBAND"]).gvcf


def test_prepared_callset_warnings_preserve_the_absent_site_limitation():
    evidence = WgsEvidence()
    evidence.header('##CoGA_RecordPolicy="Conflicting calls are retained separately for review"')
    result = evidence.summary()
    assert result["curated_callset"]
    assert any("omitted call" in warning for warning in result["warnings"])


def test_unfiltered_calls_need_opt_in_and_are_never_counted_as_pass():
    marker = AncestryMarker('1', 10, 'A', 'G')
    call = record().strip().split('\t')
    assert _call(call, 0, marker) == ('filtered', 0)
    assert _call(call, 0, marker, allow_unfiltered=True) == ('called_unfiltered', 1)


@pytest.mark.parametrize("gt,filt,status", [("./.", ".", "no_call"), ("0/.", ".", "no_call"),
    ("0/1", "LowQual", "filtered"), ("0/1:LowQual", ".", "filtered"), ("1", ".", "invalid_gt")])
def test_explicit_unfiltered_policy_never_overrides_failed_or_missing_genotypes(gt, filt, status):
    row = record(gt=gt, filt=filt).replace("\tGT\t", "\tGT:FT\t" if ":" in gt else "\tGT\t")
    kind, dosage = _call(row.strip().split('\t'), 0, AncestryMarker('1', 10, 'A', 'G'), allow_unfiltered=True)
    assert kind == status and dosage == 0
