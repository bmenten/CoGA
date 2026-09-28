"""One genotype classification for filters, counts and presence checks (#511).

Genotypes are stored exactly as the VCF writes them, but the filters, inheritance checks
and counts each used a diploid, biallelic literal list, so haploid calls (chrM, male
chrX/chrY from callers that emit ploidy-1 calls) and multi-allelic calls matched nothing.
The SQL side of the classification is checked against real ClickHouse in
``integration/test_genotype_classes_clickhouse.py``.
"""

from __future__ import annotations

import types

import pytest

from backend.app.services import genotypes as g
from backend.app.services.clickhouse_variant_queries import (
    _record_matches_de_novo_dominant,
    _record_matches_homozygous_recessive,
    _record_matches_x_linked_recessive,
    _small_native_inheritance_clauses,
    _small_record_matches_sample_filters,
)
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.family_variant_filters import SmallVariantQueryFilters


@pytest.mark.parametrize(
    ("gt", "expected"),
    [
        # diploid biallelic, unphased and phased
        ("0/1", "het"), ("1/0", "het"), ("0|1", "het"), ("1|0", "het"),
        ("1/1", "hom_alt"), ("1|1", "hom_alt"), ("0/0", "hom_ref"), ("0|0", "hom_ref"),
        # haploid: chrM, male chrX/chrY
        ("1", "hom_alt"), ("2", "hom_alt"), ("0", "hom_ref"), (".", "no_call"),
        # multi-allelic
        ("1/2", "het"), ("0/2", "het"), ("2|1", "het"), ("2/2", "hom_alt"),
        ("10/12", "het"), ("12/12", "hom_alt"), ("0/10", "het"),
        # half calls: an ALT allele is a carrier; a half reference call is unknown
        ("./1", "het"), ("1|.", "het"), ("./0", "no_call"),
        # no-calls and non-genotypes
        ("./.", "no_call"), (".|.", "no_call"), ("", "no_call"), (None, "no_call"),
        ("HET", "no_call"), ("1/a", "no_call"), (" 1/1 ", "hom_alt"),
    ],
)
def test_every_genotype_falls_in_exactly_one_class(gt, expected) -> None:
    assert g.classify_genotype(gt) == expected


def test_the_short_vocabulary_is_exhaustive_and_partitioned() -> None:
    # Every string of up to three characters that single-digit alleles can produce:
    # empty, 11 haploid symbols, and 11 x 11 pairs with either separator.
    by_class = {cls: set(g.genotype_vocabulary(cls)) for cls in g.GENOTYPE_CLASSES}
    everything = set().union(*by_class.values())
    assert len(everything) == 1 + 11 + 11 * 11 * 2
    assert sum(len(values) for values in by_class.values()) == len(everything), "classes overlap"
    for cls, values in by_class.items():
        assert all(g.classify_genotype(value) == cls for value in values)
    assert {"1", "1/1", "1|1", "2/2"} <= by_class["hom_alt"]
    assert {"0/1", "1/0", "0|1", "1|0", "1/2", "./1"} <= by_class["het"]
    assert {"0", "0/0", "0|0"} <= by_class["hom_ref"]
    assert {"", ".", "./.", ".|.", "./0"} <= by_class["no_call"]


def test_the_sql_condition_is_a_set_lookup_with_an_exact_long_string_fallback() -> None:
    params: dict = {}
    condition = g.clickhouse_genotype_condition("gt", {"het", "hom_alt"}, param="p", params=params)
    assert condition.startswith("(gt IN %(p)s OR (length(gt) > 3 AND (")
    assert params["p"] == g.genotype_vocabulary("het", "hom_alt")
    assert "splitByRegexp('[/|]', gt)" in condition
    assert g.clickhouse_genotype_condition("gt", [], param="q", params=params) == "0"
    # no_call is the complement of the called classes, so no string falls in none.
    no_call = g.clickhouse_genotype_condition("gt", {"no_call"}, param="n", params=params)
    assert no_call.startswith("NOT (gt IN %(n_called)s")
    assert params["n_called"] == g.genotype_vocabulary("hom_alt", "het", "hom_ref")
    with pytest.raises(ValueError):
        g.clickhouse_genotype_condition("gt", {"carrier"}, param="r", params=params)


def _call(sample: str, gt: str) -> SmallVariantCall:
    return SmallVariantCall(sample=sample, gt=gt, gq=None, dp=None, af=[], ad=[], ps=None)


def _record(chr_: str, *calls: SmallVariantCall) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=1, variant_id=f"{chr_}-100-A-G", chr=chr_, start=100, end=100, ref="A", alt="G",
        source=None, rsid=None, filters=[], gene_symbols=["GENE"], annotations=[], calls=list(calls),
    )


def _sample_filters(*entries: str) -> SmallVariantQueryFilters:
    return SmallVariantQueryFilters(page=1, page_size=50, sample_filters=list(entries))


def test_the_hom_filter_keeps_a_haploid_alt_call() -> None:
    # The UI's Hom group: before #511 a chrM "1" (or a male chrX "1") matched nothing.
    hom = _sample_filters("PROBAND:1/1|1|1")
    assert _small_record_matches_sample_filters(_record("MT", _call("PROBAND", "1")), hom)
    assert _small_record_matches_sample_filters(_record("X", _call("PROBAND", "1")), hom)
    assert _small_record_matches_sample_filters(_record("1", _call("PROBAND", "2/2")), hom)
    assert not _small_record_matches_sample_filters(_record("MT", _call("PROBAND", "0")), hom)


def test_the_het_filter_keeps_a_multi_allelic_call() -> None:
    het = _sample_filters("PROBAND:0/1|1/0|0|1|1|0")
    assert _small_record_matches_sample_filters(_record("1", _call("PROBAND", "1/2")), het)
    assert _small_record_matches_sample_filters(_record("1", _call("PROBAND", "0|2")), het)
    assert not _small_record_matches_sample_filters(_record("1", _call("PROBAND", "2/2")), het)


def test_the_ref_filter_keeps_a_haploid_reference_call() -> None:
    wt = _sample_filters("FATHER:0/0|0|0|./.|absent")
    assert _small_record_matches_sample_filters(_record("X", _call("FATHER", "0")), wt)
    assert not _small_record_matches_sample_filters(_record("X", _call("FATHER", "1")), wt)


def test_x_linked_recessive_keeps_a_hemizygous_haploid_son() -> None:
    record = _record("X", _call("SON", "1"), _call("MOTHER", "0/1"), _call("FATHER", "0"))
    assert _record_matches_x_linked_recessive(
        record,
        affected_samples=["SON"],
        unaffected_samples=["MOTHER", "FATHER"],
        sample_sex={"SON": "male", "MOTHER": "female", "FATHER": "male"},
    )
    # An unaffected male carrying the ALT allele (haploid "1") rules the variant out.
    carrier_father = _record("X", _call("SON", "1"), _call("FATHER", "1"))
    assert not _record_matches_x_linked_recessive(
        carrier_father,
        affected_samples=["SON"],
        unaffected_samples=["FATHER"],
        sample_sex={"SON": "male", "FATHER": "male"},
    )


def test_homozygous_recessive_reads_a_multi_allelic_homozygote() -> None:
    record = _record("2", _call("PROBAND", "2/2"), _call("MOTHER", "0/2"), _call("FATHER", "1/2"))
    assert _record_matches_homozygous_recessive(
        record, affected_samples=["PROBAND"], unaffected_samples=["MOTHER", "FATHER"]
    )


def test_an_unaffected_haploid_carrier_rules_out_dominant() -> None:
    record = _record("MT", _call("PROBAND", "0/1"), _call("MOTHER", "1"))
    assert not _record_matches_de_novo_dominant(
        record, affected_samples=["PROBAND"], unaffected_samples=["MOTHER"]
    )


def test_the_x_linked_sql_admits_haploid_alt_calls() -> None:
    context = types.SimpleNamespace(
        affected_sample_names=["SON"],
        sample_rows=[
            {"sample_id": "SON", "clinical_status": "affected", "sex": "male"},
            {"sample_id": "FATHER", "clinical_status": "unaffected", "sex": "male"},
        ],
        relationship_rows=[],
        sample_name_to_uuid={"SON": "u-son", "FATHER": "u-father"},
    )
    filters = SmallVariantQueryFilters(page=1, page_size=50, inheritance="x_linked")
    clauses, params = _small_native_inheritance_clauses(context, filters)

    gts = {key: value for key, value in params.items() if key.endswith("_gts")}
    assert gts, "the X-linked clauses bind genotype vocabularies"
    assert all("1" in value and "1/2" in value for value in gts.values())
    assert any("length(gt) > 3" in clause for clause in clauses)
