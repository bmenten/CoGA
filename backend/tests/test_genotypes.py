"""One genotype classification for filters, counts and presence checks.

Genotypes are stored exactly as the VCF writes them, and the filters, inheritance checks
and counts share one classification (``classify_genotype``), so haploid calls (chrM, male
chrX/chrY from callers that emit ploidy-1 calls) and multi-allelic calls match their class.
The SQL side of the classification is checked against real ClickHouse in
``integration/test_genotype_classes_clickhouse.py``.
"""

from __future__ import annotations

import types

import pytest

from backend.app.services import genotypes as g
from backend.app.services.clickhouse_variant_queries import _small_native_inheritance_clauses
from backend.app.services.family_variant_filters import SmallVariantQueryFilters, parse_small_variant_sample_filter


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


def _keeps(entry: str, gt: str) -> bool:
    """Whether a member's per-sample genotype filter ``entry`` keeps the call ``gt``: the
    classes its genotypes name (``parse_small_variant_sample_filter``), which the SQL
    condition looks the call up in (``clickhouse_genotype_condition``; on ClickHouse in
    integration/test_genotype_classes_clickhouse.py)."""
    sample_filter = parse_small_variant_sample_filter(entry)
    assert sample_filter is not None
    return g.classify_genotype(gt) in sample_filter.genotype_classes


def test_the_hom_filter_keeps_a_haploid_alt_call() -> None:
    # The UI's Hom group keeps a chrM "1" (or a male chrX "1").
    hom = "PROBAND:1/1|1|1"
    assert _keeps(hom, "1")
    assert _keeps(hom, "2/2")
    assert not _keeps(hom, "0")


def test_the_het_filter_keeps_a_multi_allelic_call() -> None:
    het = "PROBAND:0/1|1/0|0|1|1|0"
    assert _keeps(het, "1/2")
    assert _keeps(het, "0|2")
    assert not _keeps(het, "2/2")


def test_the_ref_filter_keeps_a_haploid_reference_call() -> None:
    wt = "FATHER:0/0|0|0|./.|absent"
    assert _keeps(wt, "0")
    assert not _keeps(wt, "1")


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
