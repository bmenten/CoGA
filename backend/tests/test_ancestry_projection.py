"""Independent synthetic PCA arithmetic and fail-closed genotype/reference boundaries."""
import gzip
from io import StringIO
import json

import numpy as np
import pytest

from backend.app.ancestry_schemas import AncestryReferenceOut
from backend.app.services import ancestry_projection as ancestry
from backend.app.services.wgs_evidence import WgsInputError, sha256_file
from backend.app.services.ancestry_projection import AncestryMarker


def reference():
    public = AncestryReferenceOut(sha256="0" * 64, name="Synthetic reference", source_url="https://example.org/reference",
        source_revision="synthetic", method="Synthetic orthogonal axes", variant_count=4, sample_count=5,
        explained_variance=[0.4, 0.3, 0.2, 0.1], populations={"SYN": "Synthetic"}, points=[])
    return ancestry.AncestryReference(public, [AncestryMarker("1", i + 1, "A", "G") for i in range(4)],
        np.array([1, 0.5, 1.5, 0.25]), np.array([1, 0.5, 1, 0.25]), np.eye(4))


def vcf(rows, samples="PROBAND"):
    return StringIO("##fileformat=VCFv4.2\n##reference=GRCh38\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{samples}\n" + "".join(rows))


def row(pos, gt, *, ref="A", alt="G", site_filter="PASS", fmt="GT"):
    return f"chr1\t{pos}\t.\t{ref}\t{alt}\t50\t{site_filter}\t.\t{fmt}\t{gt}\n"


def test_reference_centering_scaling_and_same_axes_for_every_individual():
    stream = vcf([row(1, "0/1\t0/0"), row(2, "1|1\t0/1"), row(3, "0/0\t1/1"), row(4, "1/1\t0/0")], "PROBAND\tMOTHER")
    result = ancestry.project_vcf(stream, reference(), ["PROBAND", "MOTHER"], assume_ref=False, min_markers=4)
    assert result[0].pcs == [0, 3, -1.5, 7]
    assert result[1].pcs == [-1, 1, 0.5, -1]
    assert all(sample.qc == {"total": 4, "called": 4, "called_unfiltered": 0, "assumed_ref": 0} for sample in result)


@pytest.mark.parametrize("bad,status", [
    (row(4, "./."), "no_call"), (row(4, "0/."), "no_call"),
    (row(4, "0/1", site_filter="LowQual"), "filtered"), (row(4, "0/1", site_filter="."), "filtered"),
    (row(4, "0/1:LowQual", fmt="GT:FT"), "filtered"),
    (row(4, "0/1", ref="C"), "allele_mismatch"),
    (row(4, "0/1", alt="T"), "allele_mismatch"),
    (row(4, "1"), "invalid_gt"), (row(4, "0/2"), "invalid_gt"),
    (row(4, "0/1") + row(4, "1/1"), "ambiguous_record"),
])
def test_bad_calls_never_become_assumed_reference_or_fabricated_coordinates(bad, status):
    rows = [row(1, "0/1"), row(2, "1/1"), row(3, "0/0"), bad]
    result = ancestry.project_vcf(vcf(rows), reference(), ["PROBAND"], assume_ref=True, min_markers=3)[0]
    assert result.status == "insufficient_data" and result.pcs is None
    assert result.call_rate == 0.75 and result.qc[status] == 1 and result.qc["assumed_ref"] == 0


def test_only_explicit_wgs_absence_can_supply_a_reference_genotype():
    rows = [row(1, "0/1"), row(2, "1/1"), row(3, "0/0")]
    with_ref = ancestry.project_vcf(vcf(rows), reference(), ["PROBAND"], assume_ref=True, min_markers=4)[0]
    assert with_ref.pcs == [0, 3, -1.5, -1]
    assert with_ref.qc["assumed_ref"] == 1
    without_ref = ancestry.project_vcf(vcf(rows), reference(), ["PROBAND"], assume_ref=False, min_markers=4)[0]
    assert without_ref.pcs is None and without_ref.qc["missing_record"] == 1


def test_accepted_missingness_is_mean_centered_and_missing_sample_still_abstains():
    rows = [row(1, "0/1"), row(2, "1/1"), row(3, "0/0"), row(4, "./.")]
    results = ancestry.project_vcf(vcf(rows), reference(), ["PROBAND", "FATHER1"], assume_ref=True,
                                  min_markers=3, min_call_rate=0.75)
    assert results[0].pcs == [0, 3, -1.5, 0]
    assert results[1].status == "insufficient_data" and results[1].pcs is None
    assert results[1].qc["missing_sample"] == 4 and results[1].qc["assumed_ref"] == 0


def test_minimum_marker_count_applies_even_with_complete_calls():
    result = ancestry.project_vcf(vcf([row(i, "0/1") for i in range(1, 5)]), reference(), ["PROBAND"], assume_ref=True)[0]
    assert result.call_rate == 1 and result.status == "insufficient_data" and result.pcs is None


def test_reference_is_checksumming_safe_numeric_and_contains_all_public_groups(tmp_path):
    model = ancestry.load_reference()
    assert model.public.sample_count == 2504 and model.public.variant_count >= 1000
    assert {p.population for p in model.public.points} == {"AFR", "AMR", "EAS", "EUR", "SAS"}
    assert np.allclose(model.loadings.T @ model.loadings, np.eye(4))
    bad = tmp_path / "reference.npz"
    bad.write_bytes(b"changed public reference")
    bad.with_suffix(".json").write_text(json.dumps({"sha256": "0" * 64}))
    with pytest.raises(WgsInputError, match="reference is unavailable or failed validation"):
        ancestry.load_reference(bad)


def test_source_checksum_is_verified_before_reading_or_publishing(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.vcf"
    path.write_text(vcf([row(1, "0/1")]).read())
    checksum = sha256_file(path)
    with pytest.raises(WgsInputError, match="changed since import"):
        ancestry.project_file(path, "0" * 64, reference(), ["PROBAND"], assume_ref=True)
    calls = iter([checksum, "changed"])
    monkeypatch.setattr(ancestry, "sha256_file", lambda path: next(calls))
    with pytest.raises(WgsInputError, match="changed during calculation"):
        ancestry.project_file(path, checksum, reference(), ["PROBAND"], assume_ref=True)


def test_absent_records_taken_as_reference_need_a_fifth_of_the_markers_observed():
    # One call among ten markers: under the hom-ref policy the other nine would count as usable
    # and an exome or a one-record VCF would project as an all-reference point. It abstains.
    markers = [AncestryMarker("1", i + 1, "A", "G") for i in range(10)]
    public = reference().public.model_copy(update={"variant_count": 10})
    wide = ancestry.AncestryReference(public, markers, np.ones(10), np.ones(10), np.eye(10, 4))
    sparse = ancestry.project_vcf(vcf([row(1, "0/1")]), wide, ["PROBAND"], assume_ref=True, min_markers=1)[0]
    assert sparse.call_rate == 1 and sparse.qc["assumed_ref"] == 9
    assert sparse.status == "insufficient_data" and sparse.pcs is None
    # Two of ten observed is the floor: the individual is projected.
    enough = ancestry.project_vcf(vcf([row(1, "0/1"), row(2, "1/1")]), wide, ["PROBAND"], assume_ref=True,
                                  min_markers=1)[0]
    assert enough.status == "projected" and enough.pcs is not None


def test_accepted_unfiltered_calls_count_apart_and_only_with_the_policy():
    rows = [row(1, "0/1", site_filter="."), row(2, "1/1"), row(3, "0/0"), row(4, "1/1")]
    accepted = ancestry.project_vcf(vcf(rows), reference(), ["PROBAND"], assume_ref=False, min_markers=4,
                                   allow_unfiltered=True)[0]
    assert accepted.status == "projected" and accepted.qc["called_unfiltered"] == 1 and accepted.qc["called"] == 3
    refused = ancestry.project_vcf(vcf(rows), reference(), ["PROBAND"], assume_ref=False, min_markers=4)[0]
    assert refused.pcs is None and refused.qc["filtered"] == 1


def test_a_third_allele_at_a_marker_leaves_it_out_and_contig_names_need_no_chr_prefix():
    rows = [row(1, "0/1", alt="G,T"), row(2, "1/1"), row(3, "0/0"), row(4, "1/1")]
    result = ancestry.project_vcf(vcf(rows), reference(), ["PROBAND"], assume_ref=False, min_markers=3,
                                  min_call_rate=0.75)[0]
    assert result.qc["allele_mismatch"] == 1 and result.qc["called"] == 3
    bare = StringIO(vcf([row(i, "0/1") for i in range(1, 5)]).read().replace("chr1\t", "1\t"))
    assert ancestry.project_vcf(bare, reference(), ["PROBAND"], assume_ref=False, min_markers=4)[0].qc["called"] == 4


def test_another_declared_build_is_refused():
    stream = StringIO(vcf([row(1, "0/1")]).read().replace("##reference=GRCh38", "##reference=GRCh37"))
    with pytest.raises(WgsInputError, match="another genome build"):
        ancestry.project_vcf(stream, reference(), ["PROBAND"], assume_ref=False)


def test_a_gzip_source_is_read_as_its_text(tmp_path):
    path = tmp_path / "synthetic.vcf.gz"
    with gzip.open(path, "wt") as stream:
        stream.write(vcf([row(i, "0/1") for i in range(1, 5)]).read())
    result = ancestry.project_file(path, sha256_file(path), reference(), ["PROBAND"], assume_ref=False)[0]
    assert result.qc["called"] == 4


def test_the_bundled_reference_is_pinned_and_its_axes_are_centred():
    model = ancestry.load_reference()
    assert model.public.sha256 == ancestry.DEFAULT_REFERENCE_SHA256
    coordinates = np.array([point.pcs for point in model.public.points])
    # Reference-only PCA: the reference individuals' coordinates are centred and uncorrelated.
    assert np.allclose(coordinates.mean(axis=0), 0, atol=1e-6)
    covariance = np.cov(coordinates.T)
    assert np.allclose(covariance - np.diag(np.diag(covariance)), 0, atol=1e-6)
    assert np.allclose(model.scales, np.sqrt(model.means * (1 - model.means / 2)))
