"""The variant summary's small-variant statistics (PROD-1) against real ClickHouse.

Writes a synthetic trio (PROBAND, MOTHER, FATHER) whose calls cover every case the
statistics count, and checks each figure on the real server:

* the per-sample genotype, type, transition and transversion counts, each variant once
  although a second callset holds it, and the PASS count by the row's FILTER or the call's;
* the most severe consequence over every transcript (VEP's order), a variant without an
  annotation, and the per-chromosome counts;
* the genotype-quality, depth and allele-balance histograms, with calls lacking a value;
* the trio's Mendelian table: every combination of three biallelic diploid genotypes,
  classed by the SQL exactly as ``mendelian_class`` classes it, per autosome and callset;
* what is left out: chrM, an imputed callset (named in the response), another family's
  rows, a member the family does not show.

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``smoke`` job sets it.
"""

from __future__ import annotations

from itertools import product
from typing import Any
from uuid import uuid4

import pytest

from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.tests.integration._datastores import run_closing_stores, vep_annotations

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_TRIO = ["PROBAND", "MOTHER", "FATHER"]
_CSQ_FORMAT = "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL"
_DOSAGE_GT = {0: "0/0", 1: "0/1", 2: "1/1"}


def _call(sample: str, gt: str, *, gq: float | None = 99.0, dp: int | None = 30, af: list[float] | None = None,
          filters: list[str] | None = None) -> SmallVariantCall:
    return SmallVariantCall(
        sample=sample, gt=gt, gq=gq, dp=dp, af=[0.5] if af is None else af, ad=[15, 15], ps=None,
        filters=filters or [],
    )


def _record(
    chrom: str, pos: int, calls: list[SmallVariantCall], *, ref: str = "A", alt: str = "G",
    source: str = "deepvariant", filters: list[str] | None = None, annotations: list[dict[str, Any]] | None = None,
) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None, variant_id=f"{chrom}-{pos}-{ref}-{alt}", chr=chrom, start=pos, end=pos, ref=ref,
        alt=alt, source=source, rsid=None, filters=["PASS"] if filters is None else filters, gene_symbols=[],
        annotations=annotations or [], calls=calls, qual=50.0,
    )


def _csq(alt: str, *consequences: str) -> list[dict[str, Any]]:
    return vep_annotations(
        _CSQ_FORMAT,
        [f"{alt}|{consequence}|MODERATE|GENE1|ENSG1|Transcript|ENST{i}|" for i, consequence in enumerate(consequences)],
    )


def _proband_only(chrom: str, pos: int, gt: str, **kwargs: Any) -> SmallVariantRecord:
    calls = [_call("PROBAND", gt, **kwargs.pop("call", {})), _call("MOTHER", "0/0"), _call("FATHER", "0/0")]
    return _record(chrom, pos, calls, **kwargs)


def _context(family_uuid: str, project_ids: list[str], *, samples: list[str]) -> FamilyMetadataContext:
    roles = {"PROBAND": ("proband", "female"), "MOTHER": ("mother", "female"), "FATHER": ("father", "male")}
    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=f"FAM-{family_uuid[:8]}",
        project_ids=project_ids,
        sample_rows=[{"sample_id": s, "role": roles[s][0], "sex": roles[s][1]} for s in samples],
        sample_uuid_to_name={s: s for s in samples},
        sample_name_to_uuid={s: s for s in samples},
        affected_sample_names=[],
        assembly_id=str(uuid4()),
        assembly_name=_ASSEMBLY,
        relationship_rows=[
            {"relationship_type": "parent_child", "sample_id_a": parent, "sample_id_b": "PROBAND", "role_a": role}
            for parent, role in (("FATHER", "father"), ("MOTHER", "mother"))
            if parent in samples
        ],
    )


def _mendelian_records() -> list[SmallVariantRecord]:
    """chr2: one site per combination of the trio's dosages but 0/0 in all three (26 sites);
    chr15: 40 sites where the proband is homozygous for an allele the father lacks."""
    records = []
    for index, (c, m, f) in enumerate(product((0, 1, 2), repeat=3)):
        if c == m == f == 0:
            continue
        calls = [_call("PROBAND", _DOSAGE_GT[c]), _call("MOTHER", _DOSAGE_GT[m]), _call("FATHER", _DOSAGE_GT[f])]
        records.append(_record("chr2", 1_000_000 + 1_000 * index, calls))
    for index in range(40):
        calls = [_call("PROBAND", "1/1"), _call("MOTHER", "0/1"), _call("FATHER", "0/0")]
        records.append(_record("chr15", 30_000_000 + 1_000 * index, calls))
    return records


def _feature_records() -> list[SmallVariantRecord]:
    """chr1: the proband's variants of every type, consequence, filter and call value."""
    return [
        # A transition, missense on one transcript and stop gained on another: counted once,
        # as stop gained. A second callset holds it too.
        _proband_only("chr1", 1_000, "0/1", annotations=_csq("G", "intron_variant", "missense_variant"), call={"af": [0.42]}),
        _proband_only("chr1", 2_000, "0/1", annotations=_csq("G", "missense_variant", "stop_gained"), call={"gq": 7.0}),
        _record("chr1", 2_000, [_call("PROBAND", "0/1", gq=55.0)], source="clair3"),
        # A transversion, homozygous, without an annotation, no depth.
        _proband_only("chr1", 3_000, "1/1", alt="C", call={"dp": None, "gq": None}),
        # An insertion and a deletion, both filtered by the site's FILTER.
        _proband_only("chr1", 4_000, "0/1", alt="AT", filters=["LowQual"], annotations=_csq("T", "frameshift_variant")),
        _proband_only("chr1", 5_000, "0/1", ref="AT", alt="A", filters=["LowQual"], call={"dp": 250}),
        # An MNV with an annotation that names no term, and a call in a phased notation.
        _proband_only("chr1", 6_000, "0|1", ref="AC", alt="GT", annotations=_csq("GT", ""), call={"af": [0.97]}),
        # A per-sample callset: the call's own FILTER decides, not the row's.
        _record("chr1", 7_000, [_call("PROBAND", "0/1", filters=["PASS"])], source="clair3", filters=["LowQual"]),
        _record("chr1", 8_000, [_call("PROBAND", "0/1", filters=["RefCall"])], source="clair3"),
        # Not variants of the proband: a reference call and a no-call.
        _record("chr1", 9_000, [_call("PROBAND", "0/0"), _call("MOTHER", "0/1"), _call("FATHER", "./.")]),
        # A term outside VEP's order.
        _proband_only("chr3", 1_000, "0/1", annotations=_csq("G", "made_up_variant")),
    ]


def _left_out_records() -> list[SmallVariantRecord]:
    return [
        _proband_only("chrM", 100, "1/1"),
        _record("chr4", 1_000, [_call("PROBAND", "0|1"), _call("MOTHER", "0|0"), _call("FATHER", "0|0")], source="glimpse2"),
    ]


def test_small_variant_statistics_against_clickhouse() -> None:
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )
    from backend.app.services.family_variant_statistics import (
        get_family_small_variant_statistics,
        mendelian_class,
    )

    async def _run() -> Any:
        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        family, project = str(uuid4()), str(uuid4())
        await insert_small_variant_records(
            _ASSEMBLY, family, [project], [*_mendelian_records(), *_feature_records(), *_left_out_records()]
        )
        # Another family at the same sites: none of its rows is counted.
        await insert_small_variant_records(
            _ASSEMBLY, str(uuid4()), [project],
            [_record("chr1", pos, [_call("PROBAND", "1/1")]) for pos in (1_000, 2_000, 3_000)],
        )
        stats = await get_family_small_variant_statistics(_context(family, [project], samples=_TRIO))
        # Without the father the family shows no trio, and his calls are not read.
        no_father = await get_family_small_variant_statistics(
            _context(family, [project], samples=["PROBAND", "MOTHER"])
        )
        return stats, no_father

    stats, no_father = run_closing_stores(_run, init_schema=True)

    assert stats.sources == ["clair3", "deepvariant"]
    assert stats.imputed_sources == ["glimpse2"]
    assert [sample.sample_id for sample in stats.samples] == _TRIO

    proband = stats.samples[0]
    mendelian_proband = sum(1 for c, m, f in product((0, 1, 2), repeat=3) if c > 0) + 40
    features = 9  # chr1:1,000-8,000 and chr3:1,000: nine variants of the proband
    assert proband.non_ref_count == mendelian_proband + features
    assert proband.hom_alt_count == 9 + 40 + 1
    assert proband.het_count == proband.non_ref_count - proband.hom_alt_count
    assert (proband.insertion_count, proband.deletion_count, proband.other_count) == (1, 1, 1)
    assert proband.snv_count == proband.non_ref_count - 3
    assert proband.transversion_count == 1
    assert proband.transition_count == proband.snv_count - 1
    # Filtered: the insertion and deletion (the row's FILTER) and chr1:8,000 (its own).
    assert proband.passing_count == proband.non_ref_count - 3

    consequences = {row.consequence: row.count for row in proband.consequences}
    assert consequences["stop_gained"] == 1
    assert consequences["missense_variant"] == 1
    assert consequences["frameshift_variant"] == 1
    assert consequences["other"] == 1
    # The MNV's annotation names no term, and the other variants have none.
    assert consequences["none"] == proband.non_ref_count - 4
    assert sum(consequences.values()) == proband.non_ref_count

    chromosomes = {row.chrom: (row.het_count, row.hom_alt_count) for row in proband.chromosomes}
    assert [row.chrom for row in proband.chromosomes] == ["1", "2", "3", "15"]
    assert chromosomes["15"] == (0, 40)
    assert chromosomes["3"] == (1, 0)
    assert "M" not in chromosomes and "4" not in chromosomes

    gq = {row.low: row.count for row in proband.genotype_quality_histogram}
    calls = proband.non_ref_count + 1  # chr1:2,000 is called by two callsets
    assert gq[0.0] == 1 and gq[50.0] == 1 and proband.genotype_quality_missing == 1
    assert gq[90.0] == calls - 3
    depth = {row.low: row.count for row in proband.depth_histogram}
    assert depth[200.0] == 1 and proband.depth_missing == 1 and depth[30.0] == calls - 2
    balance = {row.low: row.count for row in proband.allele_balance_histogram}
    assert balance[0.4] == 1 and balance[0.95] == 1
    assert sum(balance.values()) + proband.allele_balance_missing == proband.het_count + 1

    # The father is called at the mother's chr1:9,000 site with a no-call: not a variant.
    mother = stats.samples[1]
    assert mother.non_ref_count == sum(1 for c, m, f in product((0, 1, 2), repeat=3) if m > 0) + 40 + 1

    assert len(stats.trios) == 1
    trio = stats.trios[0]
    assert (trio.child, trio.father, trio.mother, trio.source) == ("PROBAND", "FATHER", "MOTHER", "deepvariant")
    by_chrom = {row.chrom: row for row in trio.chromosomes}
    assert [row.chrom for row in trio.chromosomes] == ["1", "2", "3", "15"]
    # The proband's own variants of chr1 and chr3, in two parents called 0/0: from neither.
    # chr1:9,000 is not a site (the father's no-call), nor the clair3 rows (the proband's
    # alone), nor chr4's imputed one.
    assert (by_chrom["1"].sites, by_chrom["1"].from_neither) == (6, 6)
    assert (by_chrom["3"].sites, by_chrom["3"].from_neither) == (1, 1)
    expected = {"not_from_father": 0, "not_from_mother": 0, "from_neither": 0}
    for c, m, f in product((0, 1, 2), repeat=3):
        if c == m == f == 0:
            continue
        verdict = mendelian_class(c, f, m)
        if verdict:
            expected[verdict] += 1
    chr2 = by_chrom["2"]
    assert chr2.sites == 26
    assert (chr2.not_from_father, chr2.not_from_mother, chr2.from_neither) == (
        expected["not_from_father"], expected["not_from_mother"], expected["from_neither"]
    )
    chr15 = by_chrom["15"]
    assert (chr15.sites, chr15.not_from_father, chr15.not_from_mother, chr15.from_neither) == (40, 40, 0, 0)

    assert no_father.trios == []
    assert [sample.sample_id for sample in no_father.samples] == ["PROBAND", "MOTHER"]
    assert no_father.total_variants < stats.total_variants
