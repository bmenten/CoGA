"""Expanded carrier screening: the couple-level at-risk rule (REQ-CARR-002).

A couple is at risk for a recessive condition when both partners carry a variant in the
same gene. The search keeps, for such a gene, every variant either partner carries; a gene
only one partner carries in is left out. For an X-linked condition the female partner's
carrier status alone puts a son at risk, so on chrX outside the pseudo-autosomal regions a
variant she carries is kept on its own. These tests pin the pure rule
(``_filter_expanded_carrier_screening``) and how the couple is found
(``_carrier_partner_names``); the search applies the rule after its SQL prefilter
(``test_clickhouse_family_variants.py::test_fetch_small_variant_rows_prefilters_expanded_carrier_candidates``).
"""

from __future__ import annotations

from backend.app.services.clickhouse_variant_queries import (
    _carrier_partner_names,
    _filter_expanded_carrier_screening,
)
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

_COUPLE = [
    {"sample_id": "WOMAN", "role": "mother", "sex": "female"},
    {"sample_id": "MAN", "role": "father", "sex": "male"},
]


def _call(sample: str, gt: str) -> SmallVariantCall:
    return SmallVariantCall(sample=sample, gt=gt, gq=None, dp=None, af=[], ad=[], ps=None)


def _variant(
    variant_id: str,
    gene: str | None,
    *,
    woman: str = "0/0",
    man: str = "0/0",
    gene_id: str | None = None,
    chr: str = "1",
    start: int = 100,
) -> SmallVariantRecord:
    annotation: dict = {}
    if gene:
        annotation["gene"] = gene
    if gene_id:
        annotation["gene_id"] = gene_id
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chr,
        start=start,
        end=start,
        ref="A",
        alt="G",
        source="clair3",
        rsid=None,
        filters=[],
        gene_symbols=[gene] if gene else [],
        annotations=[annotation] if annotation else [],
        calls=[_call("WOMAN", woman), _call("MAN", man)],
    )


def _kept(records, sample_rows=_COUPLE, relationship_rows=None, *, assembly_name=None) -> list[str]:
    return [
        record.variant_id
        for record in _filter_expanded_carrier_screening(
            records, sample_rows, relationship_rows, assembly_name=assembly_name
        )
    ]


class TestCoupleRule:
    def test_a_gene_both_partners_carry_in_keeps_each_partners_variant(self) -> None:
        records = [
            _variant("cftr-1", "CFTR", woman="0/1"),
            _variant("cftr-2", "CFTR", man="0/1", start=200),
        ]
        assert _kept(records) == ["cftr-1", "cftr-2"]

    def test_one_variant_both_partners_carry_qualifies(self) -> None:
        assert _kept([_variant("smn1-1", "SMN1", woman="0/1", man="0/1")]) == ["smn1-1"]

    def test_a_gene_only_one_partner_carries_in_is_left_out(self) -> None:
        records = [
            _variant("gaa-1", "GAA", woman="0/1"),
            _variant("gaa-2", "GAA", woman="1/1", start=200),
        ]
        assert _kept(records) == []

    def test_genes_are_matched_by_gene_id_when_the_symbols_differ(self) -> None:
        # A renamed gene: one annotation still carries the old symbol.
        records = [
            _variant("v-old", "OLDNAME", woman="0/1", gene_id="ENSG0001"),
            _variant("v-new", "NEWNAME", man="0/1", gene_id="ENSG0001", start=200),
        ]
        assert _kept(records) == ["v-old", "v-new"]

    def test_reference_and_missing_calls_are_not_carriers(self) -> None:
        records = [
            _variant("hbb-1", "HBB", woman="0/1", man="0/0"),
            _variant("hbb-2", "HBB", man="./.", start=200),
        ]
        assert _kept(records) == []

    def test_a_variant_without_a_gene_is_never_a_finding(self) -> None:
        assert _kept([_variant("intergenic", None, woman="0/1", man="0/1")]) == []

    def test_the_order_of_the_records_is_kept(self) -> None:
        records = [
            _variant("b", "CFTR", man="0/1", start=300),
            _variant("a", "CFTR", woman="0/1", start=100),
        ]
        assert _kept(records) == ["b", "a"]


# GRCh38: PAR1 is chrX:10,001-2,781,479; DMD lies far outside it.
_DMD = 31_000_000
_PAR1 = 1_500_000


class TestXLinked:
    def test_a_female_partners_x_linked_variant_is_kept_on_its_own(self) -> None:
        records = [_variant("dmd-1", "DMD", woman="0/1", chr="X", start=_DMD)]
        assert _kept(records, assembly_name="GRCh38") == ["dmd-1"]

    def test_the_chromosome_is_recognised_under_its_other_names(self) -> None:
        records = [
            _variant("dmd-chr", "DMD", woman="0/1", chr="chrX", start=_DMD),
            _variant("dmd-23", "DMD", woman="0/1", chr="23", start=_DMD + 10),
        ]
        assert _kept(records, assembly_name="GRCh38") == ["dmd-chr", "dmd-23"]

    def test_a_male_partners_x_linked_variant_alone_is_not_a_finding(self) -> None:
        # He has one X: he does not carry an X-linked condition for his sons.
        records = [_variant("dmd-1", "DMD", man="1", chr="X", start=_DMD)]
        assert _kept(records, assembly_name="GRCh38") == []

    def test_a_partner_without_a_recorded_sex_counts_as_female(self) -> None:
        rows = [
            {"sample_id": "WOMAN", "role": "mother", "sex": ""},
            {"sample_id": "MAN", "role": "father", "sex": "male"},
        ]
        records = [_variant("otc-1", "OTC", woman="0/1", chr="X", start=38_400_000)]
        assert _kept(records, rows, assembly_name="GRCh38") == ["otc-1"]

    def test_each_female_partner_of_a_couple_counts(self) -> None:
        rows = [
            {"sample_id": "WOMAN", "role": "other", "sex": "female"},
            {"sample_id": "MAN", "role": "other", "sex": "F"},
        ]
        records = [_variant("dmd-1", "DMD", man="0/1", chr="X", start=_DMD)]
        assert _kept(records, rows, assembly_name="GRCh38") == ["dmd-1"]

    def test_the_pseudo_autosomal_region_follows_the_both_partners_rule(self) -> None:
        # In PAR1 X and Y pair like an autosome: one partner's variant is not enough.
        alone = [_variant("shox-1", "SHOX", woman="0/1", chr="X", start=_PAR1)]
        assert _kept(alone, assembly_name="GRCh38") == []
        both = [*alone, _variant("shox-2", "SHOX", man="0/1", chr="X", start=_PAR1 + 10)]
        assert _kept(both, assembly_name="GRCh38") == ["shox-1", "shox-2"]

    def test_an_assembly_without_known_pars_keeps_the_both_partners_rule(self) -> None:
        records = [_variant("dmd-1", "DMD", woman="0/1", chr="X", start=_DMD)]
        assert _kept(records, assembly_name="T2T-CHM13v2.0") == []
        assert _kept(records) == []

    def test_grch37_reads_its_own_boundaries(self) -> None:
        # GRCh37's PAR1 ends at 2,699,520: 2,700,000 lies outside it there, though inside
        # GRCh38's (which ends at 2,781,479).
        records = [_variant("x-1", "GENEX", woman="0/1", chr="X", start=2_700_000)]
        assert _kept(records, assembly_name="GRCh37") == ["x-1"]
        assert _kept(records, assembly_name="GRCh38") == []

    def test_a_y_variant_is_never_kept_on_its_own(self) -> None:
        records = [_variant("y-1", "GENEY", man="1", chr="Y", start=10_000_000)]
        assert _kept(records, assembly_name="GRCh38") == []

    def test_an_x_linked_variant_without_a_gene_is_not_a_finding(self) -> None:
        records = [_variant("x-intergenic", None, woman="0/1", chr="X", start=_DMD)]
        assert _kept(records, assembly_name="GRCh38") == []

    def test_both_rules_together_keep_each_variant_once_in_order(self) -> None:
        records = [
            _variant("cftr-1", "CFTR", man="0/1", chr="7", start=117_500_000),
            _variant("dmd-1", "DMD", woman="0/1", chr="X", start=_DMD),
            _variant("cftr-2", "CFTR", woman="0/1", chr="7", start=117_600_000),
            _variant("dmd-2", "DMD", woman="0/1", man="1", chr="X", start=_DMD + 100),
            _variant("gaa-1", "GAA", woman="0/1", chr="17", start=80_100_000),
        ]
        assert _kept(records, assembly_name="GRCh38") == ["cftr-1", "dmd-1", "cftr-2", "dmd-2"]


class TestWhoTheCoupleIs:
    def test_a_couple_relationship_decides(self) -> None:
        rows = [
            {"sample_id": "MOTHER", "role": "mother"},
            {"sample_id": "FATHER", "role": "father"},
            {"sample_id": "P1", "role": "proband"},
            {"sample_id": "P2", "role": "other"},
        ]
        relationships = [{"relationship_type": "couple", "sample_id_a": "P1", "sample_id_b": "P2"}]
        assert _carrier_partner_names(rows, relationships) == ("P1", "P2")

    def test_a_relationship_naming_an_unknown_sample_is_ignored(self) -> None:
        relationships = [{"relationship_type": "couple", "sample_id_a": "WOMAN", "sample_id_b": "GHOST"}]
        assert _carrier_partner_names(_COUPLE, relationships) == ("WOMAN", "MAN")

    def test_mother_and_father_are_the_couple_without_a_relationship(self) -> None:
        rows = [*_COUPLE, {"sample_id": "CHILD", "role": "proband"}]
        assert _carrier_partner_names(rows) == ("WOMAN", "MAN")

    def test_a_two_member_family_is_the_couple(self) -> None:
        rows = [{"sample_id": "A", "role": "other"}, {"sample_id": "B", "role": "other"}]
        assert _carrier_partner_names(rows) == ("A", "B")

    def test_no_couple_means_no_finding(self) -> None:
        rows = [{"sample_id": "A", "role": "proband"}, {"sample_id": "B", "role": "sibling"}, {"sample_id": "C"}]
        assert _carrier_partner_names(rows) is None
        record = SmallVariantRecord(
            variant_key=None,
            variant_id="v",
            chr="1",
            start=1,
            end=1,
            ref="A",
            alt="G",
            source="clair3",
            rsid=None,
            filters=[],
            gene_symbols=["CFTR"],
            annotations=[{"gene": "CFTR"}],
            calls=[_call("A", "0/1"), _call("B", "0/1"), _call("C", "0/1")],
        )
        assert _filter_expanded_carrier_screening([record], rows) == []
