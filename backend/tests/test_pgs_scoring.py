"""Synthetic GT scoring: no unusable call can take the absent-record REF path."""
from io import StringIO
import gzip
import hashlib
import json
import math
import subprocess
import sys
import textwrap

import pytest

from backend.app.services.pgs_scoring import PgsInputError, Weight, load_weights, parse_weights, prepare_weights, score_file, score_vcf, serialize_weights, sha256_text, text_lines, weight_dicts

MODEL = "#pgs_id=PGS005091\n#genome_build=GRCh38\nchr_name\tchr_position\teffect_allele\tother_allele\tref_allele\teffect_weight\n1\t10\tG\tA\tA\t0.5\n1\t20\tC\tT\tC\t-1\n"
HEADER = "##fileformat=VCFv4.2\n##reference=GRCh38\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\tMOTHER\n"


def calculate(line="", *, samples=None, assume_ref=True, weights=None):
    return score_vcf(StringIO(HEADER + line), {"v1": weights or parse_weights(MODEL, "PGS005091")[0]},
                     samples or ["PROBAND"], assume_ref=assume_ref)


def test_raw_additive_scores_count_ref_effect_and_phase():
    rows = calculate("1\t10\t.\tA\tG\t50\tPASS\t.\tGT:DS\t0|1:0\t1/1:0\n", samples=["PROBAND", "MOTHER"])
    assert [r["raw_score"] for r in rows] == [-1.5, -1]
    assert rows[0]["qc"] == {"total": 2, "called": 1, "assumed_ref": 1}


@pytest.mark.parametrize(("call", "reason"), [
    (".\tA\tG\t50\tPASS\t.\tGT\t./.", "no_call"),
    (".\tA\tG\t50\tPASS\t.\tGT\t0/.", "no_call"),
    (".\tA\tG\t50\tPASS\t.\tDS\t0.8", "no_call"),
    (".\tA\tG\t50\tLowQual\t.\tGT\t0/0", "filtered"),
    (".\tA\tG\t50\t.\t.\tGT\t0/0", "filtered"),
    (".\tA\tG\t50\tPASS\t.\tGT:FT\t0/0:LowQual", "filtered"),
    (".\tC\tG\t50\tPASS\t.\tGT\t0/0", "allele_mismatch"),
    (".\tA\tT\t50\tPASS\t.\tGT\t0/0", "allele_mismatch"),
    (".\tA\tG\t50\tPASS\t.\tGT\t1", "invalid_gt"),
    (".\tA\tG\t50\tPASS\t.\tGT\t0/1/1", "invalid_gt"),
    (".\tA\tG\t50\tPASS\t.\tGT\t0/2", "invalid_gt"),
    (".\tA\tG\t50\tPASS\t.\tGT", "missing_sample"),
])
def test_unusable_records_are_never_assumed_ref(call, reason):
    result = calculate(f"1\t10\t{call}\n")[0]
    assert result["raw_score"] is None
    assert result["status"] == "incomplete"
    assert result["qc"][reason] == 1
    assert result["qc"]["assumed_ref"] == 1  # only the absent locus at 20


def test_absent_header_sample_is_missing_at_present_and_absent_loci():
    result = calculate(samples=["FATHER1"])[0]
    assert result["raw_score"] is None
    assert result["qc"] == {"total": 2, "missing_sample": 2}


def test_no_ref_assumption_means_no_complete_score():
    result = calculate(assume_ref=False)[0]
    assert result["raw_score"] is None
    assert result["qc"]["missing_record"] == 2


def test_negative_zero_weights_have_a_stable_jsonb_checksum_representation():
    weights, _ = parse_weights(MODEL.replace("0.5", "-0.0"), "PGS005091")
    assert repr(weights[0].weight) == "0.0"


@pytest.mark.parametrize("column", ["is_interaction", "dosage_0_weight", "dosage_2_weight"])
def test_catalog_non_additive_columns_are_never_scored_as_simple_gt(column):
    model = MODEL.replace("effect_weight", f"effect_weight\t{column}").replace("0.5\n", "0.5\t1\n").replace("-1\n", "-1\t0\n")
    with pytest.raises(PgsInputError, match="Non-additive"):
        parse_weights(model, "PGS005091")


def test_other_allele_need_not_be_ref_and_multiallelic_gt_is_resolved_by_base():
    weights = [Weight("1", 10, "A", "C", "T", 2)]
    assert calculate("1\t10\t.\tA\tT,C\t50\tPASS\t.\tGT\t1/2\n", weights=weights)[0]["raw_score"] == 2
    assert calculate(weights=weights)[0]["raw_score"] == 0


def test_duplicate_or_split_position_is_ambiguous():
    line = "1\t10\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n"
    result = calculate(line + line)[0]
    assert result["raw_score"] is None
    assert result["qc"]["ambiguous_record"] == 1


@pytest.mark.parametrize("model", [
    MODEL.replace("#genome_build=GRCh38", "#genome_build=GRCh37"),
    MODEL.replace("ref_allele", "not_ref"),
    MODEL.replace("1\t10", "X\t10"),
    MODEL.replace("G\tA\tA", "GG\tA\tA"),
    MODEL.replace("0.5", "nan"),
    MODEL.replace("0.5", "inf"),
    MODEL.replace("1\t20", "1\t10"),
    MODEL.replace("effect_weight", "OR"),
    MODEL.replace("effect_weight", "effect_weight\tis_dominant"),
    "#variants_number=3\n" + MODEL,
    MODEL.replace("PGS005091", "PGS000013"),
    MODEL.replace("1\t10", "1\t0"),
])
def test_unsupported_or_ambiguous_models_are_rejected_in_full(model):
    with pytest.raises(PgsInputError):
        parse_weights(model, "PGS005091")


def test_indexed_fasta_resolves_ref_without_using_other_allele(tmp_path):
    import pysam

    path = tmp_path / "grch38.fa"
    path.write_text(">chr1\n" + "A" * 30 + "\n")
    pysam.faidx(str(path))
    model = "#HmPOS_build=GRCh38\nhm_chr\thm_pos\teffect_allele\tother_allele\teffect_weight\n1\t10\tC\tT\t0.5\n"
    weights, provenance = parse_weights(model, "PGS005091", str(path))
    assert weights[0].ref == "A" and weights[0].other == "T"
    assert provenance["fasta_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PgsInputError, match="disagrees"):
        parse_weights(MODEL, "PGS005091", str(path))
    with pytest.raises(PgsInputError, match="missing"):
        parse_weights(model, "PGS005091", str(tmp_path / "missing.fa"))


def test_checksum_pins_full_source_and_plain_or_gzip_are_equivalent(tmp_path):
    weights = {"v1": parse_weights(MODEL, "PGS005091")[0]}
    for suffix, value in [("vcf", HEADER.encode()), ("vcf.gz", gzip.compress(HEADER.encode()))]:
        path = tmp_path / suffix
        path.write_bytes(value)
        digest = hashlib.sha256(value).hexdigest()
        assert score_file(path, digest, weights, ["PROBAND"], assume_ref=True)[0]["raw_score"] == -2
        path.write_bytes(value + b"changed")
        with pytest.raises(PgsInputError, match="changed"):
            score_file(path, digest, weights, ["PROBAND"], assume_ref=True)


@pytest.mark.parametrize("value", ["1\t10\t.\n", HEADER + "bad\n", HEADER.replace("GRCh38", "GRCh37"), HEADER + HEADER])
def test_malformed_or_wrong_build_vcf_never_yields_results(value):
    with pytest.raises(PgsInputError):
        score_vcf(StringIO(value), {"v1": parse_weights(MODEL, "PGS005091")[0]}, ["PROBAND"], assume_ref=True)


@pytest.mark.parametrize("value", ["", "one", "one\n", "one\r\ntwo\rthree\n\n", "a\v\f\x1c\x1d\x1e\x85\u2028\u2029b"])
def test_model_line_iteration_preserves_splitlines_semantics(value):
    assert list(text_lines(value)) == value.splitlines()


def test_incremental_serialization_preserves_historical_weights_and_checksums():
    weights, _ = parse_weights(MODEL, "PGS005091")
    weights += [Weight("22", 100, "A", "A", None, 1e-50), Weight("2", 1, "C", "T", "C", 0.0)]
    legacy = json.dumps(weight_dicts(weights), sort_keys=True)
    value, digest = serialize_weights(iter(weights))
    assert value == legacy
    assert digest == hashlib.sha256(legacy.encode()).hexdigest()
    assert load_weights(json.loads(value), digest) == weights
    assert serialize_weights([]) == ("[]", hashlib.sha256(b"[]").hexdigest())
    with pytest.raises(PgsInputError, match="checksum"):
        load_weights(json.loads(value.replace("0.5", "0.6")), digest)
    unicode_source = "\u00e9\U0001f600" * (1 << 20)
    assert sha256_text(unicode_source) == hashlib.sha256(unicode_source.encode()).hexdigest()


def test_preparation_limit_rejects_the_whole_model_and_accepts_the_exact_limit():
    with pytest.raises(PgsInputError, match="PGS_MAX_VARIANTS=1"):
        prepare_weights(MODEL, "PGS005091", None, 1)
    value, digest, count, reference = prepare_weights(MODEL, "PGS005091", None, 2)
    assert count == 2 and len(load_weights(json.loads(value), digest)) == 2
    assert reference == {"reference": "explicit ref_allele"}


def test_irrelevant_annotations_do_not_change_gt_scores_or_qc():
    plain = "1\t10\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n"
    annotated = plain.replace("\t.\tGT", "\tCSQ=" + "synthetic|" * 100_000 + "\tGT")
    assert calculate(annotated) == calculate(plain)


@pytest.mark.parametrize("line", [
    f"1\t{(1 << 32) + 10}\t.\tA\tG\t50\tPASS\t.\tGT\t1/1\n",
    "0\t10\t.\tA\tG\t50\tPASS\t.\tGT\t1/1\n",
])
def test_out_of_range_vcf_coordinates_cannot_alias_a_model_locus(line):
    assert calculate(line) == calculate()


def test_streaming_contributions_keep_compensated_sum_precision():
    weights = [Weight("1", i, "A", "A", "C", w) for i, w in enumerate([1e16, 0.5, -1e16], 1)]
    assert calculate(weights=weights)[0]["raw_score"] == math.fsum(w.weight * 2 for w in weights) == 1


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is enforced in the Linux test runner")
def test_large_synthetic_models_and_annotations_fit_a_bounded_worker():
    # Run outside pytest's imports/coverage memory. This catches both full splitlines /
    # asdict copies during preparation and retention of unrelated WGS INFO annotations.
    code = textwrap.dedent("""
        import gc
        import resource
        from backend.app.services.pgs_scoring import Weight, prepare_weights, score_vcf, sha256_text
        budget = 256 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (budget, budget))
        source = '#genome_build=GRCh38\\nchr_name\\tchr_position\\tref_allele\\teffect_allele\\tother_allele\\teffect_weight\\n'
        source += ''.join(f'1\\t{i}\\tA\\tG\\tA\\t0.5\\n' for i in range(1, 400_001))
        payload, digest, count, _ = prepare_weights(source, 'PGS900001', None, 400_000)
        assert count == 400_000 and sha256_text(payload) == digest
        del source, payload
        gc.collect()
        weights = [Weight('1', i, 'A', 'G', 'A', 0.5) for i in range(1, 4001)]
        def lines():
            yield '#CHROM\\tPOS\\tID\\tREF\\tALT\\tQUAL\\tFILTER\\tINFO\\tFORMAT\\tPROBAND\\n'
            for i in range(1, 4001):
                yield f'1\\t{i}\\t.\\tA\\tG\\t50\\tPASS\\tCSQ=' + 'synthetic' * 8192 + '\\tGT\\t0/1\\n'
        result = score_vcf(lines(), {'v1': weights}, ['PROBAND'], assume_ref=True)[0]
        assert result['raw_score'] == 2000 and result['qc'] == {'total': 4000, 'called': 4000}
    """)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
