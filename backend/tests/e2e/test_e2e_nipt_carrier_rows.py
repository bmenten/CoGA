"""The rows a monogenic NIPT's maternal and recessive views read, chosen in SQL on real
ClickHouse (E2E).

A per-sample cfDNA callset stores every low-level Mutect2 call, so most of a plasma's rows are
calls of neither parent: artefacts, and the fetus's alleles. The maternal view reads the
mother's alleles alone, from a plasma call of 25% or more (``nipt_triage.mother_carries``), and
the recessive view the parents' alleles: hers, and the father's from his own het or hom-alt
call. Their variant load keeps only the rows such an allele can be read from
(``clickhouse_family_variants._carrier_call_condition``), so the low-level calls neither fill
the load's limit before the rest of the genome nor cost the query its time.

The test seeds one family's ``nipt`` callset with the importer's own writer and checks:

- which rows the load keeps: a plasma call at 25% or more over its own allele depths, a call
  without allele depths, or an alt call of the father's; not a low-level plasma call alone,
  nor one beside a reference call of the father's;
- that the maternal and recessive views over those rows are the views over every row: the same
  maternal alleles, genes, probabilities and listed variants. A split record's plasma fraction is
  read over the site's restored depth, never above its own, so the load keeps a sibling the
  views then read below 25%.

The tables are those of an assembly no other test uses, dropped at the end. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "E2ENIPTCARRIERROWS"
_PROJECT = "e2e-nipt-carrier-project"
_FAMILY = "e2e-nipt-carrier-family"
_PLASMA = "CFDNA1"
_FATHER = "FATHER1"

# (variant, gene, [(sample, gt, ad)]): one row per variant in the family's ``nipt`` callset.
_ROWS: list[tuple[str, str, list[tuple[str, str, list[int]]]]] = [
    # The mother's het allele: 47% of the plasma's reads.
    ("7-1000-A-G", "GENEA", [(_PLASMA, "0/1", [106, 94])]),
    # The father's het allele, which the fetus inherited: 10% in the plasma.
    ("7-2000-C-T", "GENEA", [(_PLASMA, "0/1", [180, 20]), (_FATHER, "0/1", [15, 15])]),
    # A low-level artefact at 20%, which the model places in the mother's het band.
    ("7-3000-G-A", "GENEA", [(_PLASMA, "0/1", [160, 40])]),
    # A record split from a multi-allelic one: 37.5% and 28.6% over their own depths, 30%
    # and 20% over the site's 200 reads.
    ("7-4000-A-C", "GENEA", [(_PLASMA, "0/1", [100, 60])]),
    ("7-4000-A-T", "GENEA", [(_PLASMA, "0/1", [100, 40])]),
    # A plasma call without allele depths.
    ("7-5000-T-C", "GENEA", [(_PLASMA, "0/1", [])]),
    # A low-level plasma call beside the father's reference call.
    ("7-6000-C-G", "GENEA", [(_PLASMA, "0/1", [190, 10]), (_FATHER, "0/0", [30, 0])]),
    # A low-level artefact in another gene, where the father carries an allele the plasma
    # does not show.
    ("8-1000-G-T", "GENEB", [(_PLASMA, "0/1", [184, 16])]),
    ("8-2000-A-G", "GENEB", [(_FATHER, "0/1", [14, 16])]),
]
_KEPT = {"7-1000-A-G", "7-2000-C-T", "7-4000-A-C", "7-4000-A-T", "7-5000-T-C", "8-2000-A-G"}


def _record(variant_id: str, gene: str, calls: list[tuple[str, str, list[int]]]):
    from backend.app.services.clickhouse_variant_queries import NIPT_SMALL_VARIANT_SOURCE
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=int(pos),
        end=int(pos),
        ref=ref,
        alt=alt,
        source=NIPT_SMALL_VARIANT_SOURCE,
        rsid=None,
        filters=["PASS"],
        gene_symbols=[gene],
        annotations=[],
        calls=[
            SmallVariantCall(
                sample=sample, gt=gt, gq=None, dp=None, af=[], ad=ad, ps=None, metrics={"TLOD": 40.0}
            )
            for sample, gt, ad in calls
        ],
    )


def _context():
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    samples = {_PLASMA: "00000000-0000-4000-8000-000000000001", _FATHER: "00000000-0000-4000-8000-000000000002"}
    return FamilyMetadataContext(
        family_uuid=_FAMILY,
        family_id=_FAMILY,
        project_ids=[_PROJECT],
        sample_rows=[{"sample_id": name, "id": uuid, "affected": False} for name, uuid in samples.items()],
        relationship_rows=[],
        sample_uuid_to_name={uuid: name for name, uuid in samples.items()},
        sample_name_to_uuid=dict(samples),
        affected_sample_names=[],
        assembly_id="e2e-assembly",
        assembly_name=_ASSEMBLY,
    )


def _view(records, family_records) -> dict[str, Any]:
    """The maternal view (every allele, inherited or not) and the recessive view over
    ``records``, as ``get_family_nipt_variants`` computes them."""
    from backend.app.services.nipt import NiptTrio
    from backend.app.services.nipt_analysis import FetalFractionEstimate, NiptQualityThresholds
    from backend.app.services.nipt_service import _classify_candidates, _maternal_view, _recessive_view

    qc = NiptQualityThresholds()
    ff = FetalFractionEstimate(
        ff=0.1, ff_computed=0.1, ff_median=0.1, ci_low=0.09, ci_high=0.11, n_sites=100,
        method="category7_pooled", low_confidence=False,
    )
    trio = NiptTrio(father_sample_id=_FATHER, cfdna_sample_id=_PLASMA)
    classified = _classify_candidates(records, trio, {}, ff, qc, None, family_records=family_records)
    kept, genes = _recessive_view(classified, qc)
    return {
        "maternal": [
            (item.record.variant_id, item.classification.maternal_allele_probability)
            for item in _maternal_view(classified, include_not_inherited=True)
        ],
        "listed": [item.record.variant_id for item in kept],
        "genes": [
            (
                gene.gene,
                [(allele.variant_id, allele.inherited_probability, allele.note) for allele in gene.maternal],
                [(allele.variant_id, allele.inherited_probability, allele.note) for allele in gene.paternal],
                gene.risk,
                gene.status,
            )
            for gene in genes
        ],
    }


async def _exercise() -> dict[str, Any]:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.services import clickhouse_variant_storage as cvs
    from backend.app.services.clickhouse_family_variants import CarrierCallRows, _fetch_small_variant_rows
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters
    from backend.app.services.nipt_analysis import MATERNAL_ALLELE_VAF_BOUNDARY

    await cvs.ensure_clickhouse_variant_tables(_ASSEMBLY)
    database = settings.clickhouse_database
    try:
        await cvs.insert_small_variant_records(
            _ASSEMBLY, _FAMILY, [_PROJECT], [_record(variant, gene, calls) for variant, gene, calls in _ROWS]
        )
        context = _context()
        filters = SmallVariantQueryFilters(page=1, page_size=100)
        every_row = await _fetch_small_variant_rows(context, filters)
        carrier_rows = await _fetch_small_variant_rows(
            context,
            filters,
            carrier_calls=CarrierCallRows(
                plasma_sample_id=_PLASMA, father_sample_id=_FATHER, maternal_vaf=MATERNAL_ALLELE_VAF_BOUNDARY
            ),
        )
        return {
            "every_row": [record.variant_id for record in every_row],
            "carrier_rows": [record.variant_id for record in carrier_rows],
            "view_over_every_row": _view(every_row, every_row),
            "view_over_carrier_rows": _view(carrier_rows, every_row),
        }
    finally:
        for (name,) in await execute_clickhouse(
            "SELECT name FROM system.tables WHERE database = %(db)s AND name LIKE %(prefix)s",
            {"db": database, "prefix": f"{_ASSEMBLY}/%"},
        ):
            await execute_clickhouse(f"DROP TABLE IF EXISTS {database}.`{name}` SYNC")
        cvs._ensured_variant_table_assemblies.discard(_ASSEMBLY)


@pytest.fixture(scope="module")
def loads() -> dict[str, Any]:
    from backend.tests.e2e import _harness

    return _harness.run_async(_exercise)


def test_the_load_keeps_the_rows_a_parents_allele_can_be_read_from(loads) -> None:
    assert len(loads["every_row"]) == len(_ROWS)
    assert set(loads["carrier_rows"]) == _KEPT


def test_the_maternal_and_recessive_views_over_the_kept_rows_are_the_views_over_every_row(loads) -> None:
    view = loads["view_over_carrier_rows"]
    assert view == loads["view_over_every_row"]
    # The mother's alleles: the 47% call and the split record at 30% of the site's reads; not the
    # 20% artefact the model places in her band, nor the split sibling at 20%.
    assert [variant for variant, _p in view["maternal"]] == ["7-1000-A-G", "7-4000-A-C"]
    # GENEA: the mother's 47% allele and the split record at 30% of the site's reads, beside the
    # father's allele; not the 20% artefact, nor the split sibling at 20%. GENEB: the father's
    # allele alone, so no gene at risk.
    [(gene, maternal, paternal, _risk, _status)] = view["genes"]
    assert gene == "GENEA"
    assert [variant for variant, _p, _note in maternal] == ["7-1000-A-G", "7-4000-A-C"]
    assert [variant for variant, _p, _note in paternal] == ["7-2000-C-T"]
    assert view["listed"] == ["7-1000-A-G", "7-2000-C-T", "7-4000-A-C"]
