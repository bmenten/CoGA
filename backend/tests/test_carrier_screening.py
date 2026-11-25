"""Expanded carrier screening: the couple-level at-risk rule (REQ-CARR-002).

A couple is at risk for a recessive condition when both partners carry a variant in the
same gene. The search keeps, for such a gene, every variant either partner carries; a gene
only one partner carries in is left out, and so is a mitochondrial gene, as mtDNA passes
from the mother alone. On chrX outside the pseudo-autosomal regions a variant either
partner carries is kept on its own: a woman's carrier status alone puts a son at risk of an
X-linked condition, and a man passes his one X to every daughter. These tests pin the pure rule
(``_filter_expanded_carrier_screening``) and how the couple is found
(``_carrier_partners``); the search applies the rule after its SQL prefilter
(``test_clickhouse_family_variants.py::test_fetch_small_variant_rows_prefilters_expanded_carrier_candidates``).
"""

from __future__ import annotations

from backend.app.services.clickhouse_variant_queries import (
    _carrier_partners,
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


def _kept_left_out(records, sample_rows, relationship_rows, *, left_out) -> list[str]:
    return [
        record.variant_id
        for record in _filter_expanded_carrier_screening(records, sample_rows, relationship_rows, left_out=left_out)
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

    def test_a_mitochondrial_gene_both_partners_carry_a_variant_in_is_not_a_finding(self) -> None:
        # mtDNA passes from the mother alone: two partners with a variant in one chrM gene
        # (homoplasmic haplogroup variants, mostly) put no child at risk.
        for chromosome in ("M", "MT", "chrM"):
            records = [
                _variant("mt-1", "MT-CYB", woman="1/1", chr=chromosome, start=15_000),
                _variant("mt-2", "MT-CYB", man="1/1", chr=chromosome, start=15_100),
            ]
            assert _kept(records) == [], chromosome

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

    def test_a_male_partners_x_linked_variant_is_kept_on_its_own(self) -> None:
        # He passes his one X to every daughter, whatever the female partner carries.
        records = [_variant("dmd-1", "DMD", man="1", chr="X", start=_DMD)]
        assert _kept(records, assembly_name="GRCh38") == ["dmd-1"]

    def test_each_partners_x_linked_variant_counts_whatever_the_recorded_sex(self) -> None:
        for sexes in (("", "male"), ("female", "F"), ("unknown", "")):
            rows = [
                {"sample_id": "WOMAN", "role": "other", "sex": sexes[0]},
                {"sample_id": "MAN", "role": "other", "sex": sexes[1]},
            ]
            records = [
                _variant("otc-1", "OTC", woman="0/1", chr="X", start=38_400_000),
                _variant("dmd-1", "DMD", man="0/1", chr="X", start=_DMD),
            ]
            assert _kept(records, rows, assembly_name="GRCh38") == ["otc-1", "dmd-1"], sexes

    def test_the_pseudo_autosomal_region_follows_the_both_partners_rule(self) -> None:
        # In PAR1 X and Y pair like an autosome: one partner's variant is not enough.
        alone = [_variant("shox-1", "SHOX", woman="0/1", chr="X", start=_PAR1)]
        assert _kept(alone, assembly_name="GRCh38") == []
        both = [*alone, _variant("shox-2", "SHOX", man="0/1", chr="X", start=_PAR1 + 10)]
        assert _kept(both, assembly_name="GRCh38") == ["shox-1", "shox-2"]

    def test_an_assembly_without_known_pars_keeps_the_both_partners_rule(self) -> None:
        for partner in ("woman", "man"):
            records = [_variant("dmd-1", "DMD", chr="X", start=_DMD, **{partner: "0/1"})]
            assert _kept(records, assembly_name="T2T-CHM13v1.0") == [], partner
            assert _kept(records) == [], partner

    def test_t2t_reads_its_own_boundaries(self) -> None:
        # T2T-CHM13v2.0's PAR1 ends at 2,394,410. A partner's variant just past it counts
        # alone; one inside it follows the both-partners rule.
        past = [_variant("x-1", "GENEX", woman="0/1", chr="X", start=2_394_411)]
        assert _kept(past, assembly_name="T2T-CHM13v2.0") == ["x-1"]
        inside = [_variant("x-2", "GENEX", woman="0/1", chr="X", start=2_394_410)]
        assert _kept(inside, assembly_name="T2T-CHM13v2.0") == []

    def test_grch37_reads_its_own_boundaries(self) -> None:
        # GRCh37's PAR1 ends at 2,699,520: 2,700,000 lies outside it there, though inside
        # GRCh38's (which ends at 2,781,479).
        records = [_variant("x-1", "GENEX", woman="0/1", chr="X", start=2_700_000)]
        assert _kept(records, assembly_name="GRCh37") == ["x-1"]
        assert _kept(records, assembly_name="GRCh38") == []

    def test_a_y_variant_is_never_kept_on_its_own(self) -> None:
        # chrY follows the both-partners rule: only the male partner has one.
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
            _variant("otc-1", "OTC", man="1", chr="X", start=38_400_000),
        ]
        assert _kept(records, assembly_name="GRCh38") == ["cftr-1", "dmd-1", "cftr-2", "dmd-2", "otc-1"]


class TestWhoTheCoupleIs:
    def test_a_couple_relationship_decides(self) -> None:
        rows = [
            {"sample_id": "MOTHER", "role": "mother"},
            {"sample_id": "FATHER", "role": "father"},
            {"sample_id": "P1", "role": "proband"},
            {"sample_id": "P2", "role": "other"},
        ]
        relationships = [{"relationship_type": "couple", "sample_id_a": "P1", "sample_id_b": "P2"}]
        assert _carrier_partners(rows, relationships) == (("P1",), ("P2",))

    def test_a_relationship_naming_an_unknown_sample_is_ignored(self) -> None:
        relationships = [{"relationship_type": "couple", "sample_id_a": "WOMAN", "sample_id_b": "GHOST"}]
        assert _carrier_partners(_COUPLE, relationships) == (("WOMAN",), ("MAN",))

    def test_mother_and_father_are_the_couple_without_a_relationship(self) -> None:
        rows = [*_COUPLE, {"sample_id": "CHILD", "role": "proband"}]
        assert _carrier_partners(rows) == (("WOMAN",), ("MAN",))

    def test_a_two_member_family_is_the_couple(self) -> None:
        rows = [{"sample_id": "A", "role": "other"}, {"sample_id": "B", "role": "other"}]
        assert _carrier_partners(rows) == (("A",), ("B",))

    def test_two_samples_of_one_person_are_not_a_couple(self) -> None:
        rows = [{"sample_id": "A", "role": "proband"}, {"sample_id": "A2", "role": "relative"}]
        same = [{"relationship_type": "same_individual", "sample_id_a": "A2", "sample_id_b": "A"}]
        assert _carrier_partners(rows, same) is None
        record = SmallVariantRecord(
            variant_key=None,
            variant_id="v",
            chr="7",
            start=117_500_000,
            end=117_500_000,
            ref="A",
            alt="G",
            source="clair3",
            rsid=None,
            filters=[],
            gene_symbols=["CFTR"],
            annotations=[{"gene": "CFTR"}],
            calls=[_call("A", "0/1"), _call("A2", "0/1")],
        )
        assert _filter_expanded_carrier_screening([record], rows, same) == []

    def test_a_couple_with_a_partner_sampled_twice_is_the_couple(self) -> None:
        rows = [{"sample_id": "A", "role": "other"}, {"sample_id": "A2", "role": "other"}, {"sample_id": "B", "role": "other"}]
        same = [{"relationship_type": "same_individual", "sample_id_a": "A", "sample_id_b": "A2"}]
        assert _carrier_partners(rows, same) == (("A", "A2"), ("B",))
        # Without the link, three samples are no couple.
        assert _carrier_partners(rows) is None

    def test_a_partner_sampled_twice_carries_what_either_sample_carries(self) -> None:
        rows = [
            {"sample_id": "WOMAN", "role": "mother", "sex": "female"},
            {"sample_id": "WOMAN2", "role": "other", "sex": "female"},
            {"sample_id": "MAN", "role": "father", "sex": "male"},
        ]
        same = [{"relationship_type": "same_individual", "sample_id_a": "WOMAN2", "sample_id_b": "WOMAN"}]
        only_second = _variant("cftr-1", "CFTR", man="0/1")
        only_second.calls.append(_call("WOMAN2", "0/1"))
        his = _variant("cftr-2", "CFTR", man="0/1", start=200)
        assert _carrier_partners(rows, same) == (("WOMAN", "WOMAN2"), ("MAN",))
        assert _kept([only_second, his], rows, same) == ["cftr-1", "cftr-2"]
        # Without the link the two samples are two people, and three people are no couple
        # under a mother and a father: the mother's sample alone is read.
        assert _kept([only_second, his], rows) == []

    def test_a_sample_only_another_analysis_lists_is_not_read(self) -> None:
        rows = [
            {"sample_id": "WOMAN", "role": "mother", "sex": "female"},
            {"sample_id": "WOMAN2", "role": "other", "sex": "female"},
            {"sample_id": "MAN", "role": "father", "sex": "male"},
        ]
        same = [{"relationship_type": "same_individual", "sample_id_a": "WOMAN", "sample_id_b": "WOMAN2"}]
        assert _carrier_partners(rows, same, left_out={"WOMAN"}) == (("WOMAN2",), ("MAN",))
        record = _variant("cftr-1", "CFTR", woman="0/1", man="0/1")
        assert _kept_left_out([record], rows, same, left_out={"WOMAN2"}) == ["cftr-1"]
        record_of_second = _variant("cftr-2", "CFTR", man="0/1")
        record_of_second.calls.append(_call("WOMAN2", "0/1"))
        assert _kept_left_out([record_of_second], rows, same, left_out={"WOMAN2"}) == []
        # A partner none of whose samples the reading reads leaves no couple to screen.
        assert _carrier_partners(rows, same, left_out={"MAN"}) is None

    def test_an_x_linked_variant_on_either_sample_of_a_partner_counts(self) -> None:
        rows = [
            {"sample_id": "WOMAN", "role": "mother", "sex": "female"},
            {"sample_id": "MAN", "role": "father", "sex": "male"},
            {"sample_id": "MAN2", "role": "other", "sex": "unknown"},
        ]
        same = [{"relationship_type": "same_individual", "sample_id_a": "MAN", "sample_id_b": "MAN2"}]
        his_x = _variant("dmd-1", "DMD", chr="X", start=_DMD)
        his_x.calls.append(_call("MAN2", "1"))
        assert _kept([his_x], rows, same, assembly_name="GRCh38") == ["dmd-1"]
        # Without the link MAN2 is a third person, neither partner: its call is not read.
        assert _kept([his_x], rows, assembly_name="GRCh38") == []

    def test_no_couple_means_no_finding(self) -> None:
        rows = [{"sample_id": "A", "role": "proband"}, {"sample_id": "B", "role": "sibling"}, {"sample_id": "C"}]
        assert _carrier_partners(rows) is None
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
