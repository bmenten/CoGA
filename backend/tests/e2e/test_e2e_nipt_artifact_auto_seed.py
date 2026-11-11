"""The NIPT artifact list's auto-seed lists an allele by the mark of a recurrent artefact, not
by how many plasmas carry it, on real Postgres and ClickHouse (E2E).

The artifact list removes its alleles from every analysis of the assay. The auto-seed lists
an allele when it is an unexplained low-VAF call in at least ``min_unexplained_samples``
cfDNA samples of the assay: a call below the maternal boundary (0.25) that its family's
father does not explain, read as the recurrence count on the NIPT list reads it. A count of
every carrier would list a maternal het of a polymorphism, which every few plasmas carry,
and the fetus's paternal alleles, the sites the fetal fraction is read from.

Six synthetic families of one assay panel, each a plasma (``CFDNA1``..``CFDNA6``, tagged
``assay: nipt_cfdna``) and its father (``FATHER1``..``FATHER6``), hold:

- an artefact: a low-VAF call in three plasmas, one with no father's call, one beside a
  low-level paternal call (1%) and one beside a paternal het of low quality (TLOD 5);
- a maternal het (50%) in all six plasmas;
- the fetus's paternal allele: a low-VAF call in four plasmas, each father het;
- an allele maternal in three plasmas and unexplained in two;
- crossed-over reads (2 alt reads) in four plasmas;
- a common allele (gnomAD 31%) and a ClinVar pathogenic one (gnomAD 1%), each an
  unexplained low-VAF call in three plasmas.

Only the artefact is seeded, at the default threshold of 3, with 3 as its recurrence: the
first family is stored under two projects and counts once. The seed's count equals the
list's ``unexplained_low_vaf`` count of every allele, and the candidates are the same after
a forced part merge of ``entries`` (``OPTIMIZE … FINAL``; see
``test_e2e_nipt_cfdna_recurrence.py`` for why).

The rows are written by the importer's own writer, one row per variant and family, as a
monogenic NIPT import stores them, in the tables of an assembly no other test uses, dropped
at the end.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_TAG = uuid4().hex[:8].upper()
_ASSEMBLY = f"E2ENIPTSEED{_TAG}"
_PANEL = f"E2E_SEED_{_TAG}"
_PROJECT = "e2e-nipt-seed-project"
_OTHER_PROJECT = "e2e-nipt-seed-other-project"
_THRESHOLD = 3

_ARTEFACT = "7-1000-A-G"
_MATERNAL = "7-2000-C-T"
_PATERNAL = "7-3000-G-A"
_MOSTLY_MATERNAL = "7-4000-T-C"
_CROSSED_OVER = "7-5000-A-C"
_COMMON = "7-6000-G-T"
_PATHOGENIC = "7-7000-C-G"


def _sample(role: str, index: int) -> str:
    return f"{role}{index}-{_TAG}"


def _plasma(index: int, ad: list[int]) -> tuple[str, list[int], dict[str, float]]:
    return (_sample("CFDNA", index), ad, {})


def _father(index: int, ad: list[int], tlod: float) -> tuple[str, list[int], dict[str, float]]:
    return (_sample("FATHER", index), ad, {"TLOD": tlod})


_FATHER_HET = ([50, 50], 80.0)
_UNEXPLAINED = [980, 20]  # 2%

# variant -> {family index: calls of that family's row}
_ROWS: dict[str, dict[int, list[tuple[str, list[int], dict[str, float]]]]] = {
    _ARTEFACT: {
        1: [_plasma(1, [985, 15])],
        2: [_plasma(2, [980, 20]), _father(2, [990, 10], 30.0)],
        3: [_plasma(3, [970, 30]), _father(3, [50, 50], 5.0)],
    },
    _MATERNAL: {index: [_plasma(index, [500, 500])] for index in range(1, 7)},
    _PATERNAL: {index: [_plasma(index, [950, 50]), _father(index, *_FATHER_HET)] for index in range(1, 5)},
    _MOSTLY_MATERNAL: {
        **{index: [_plasma(index, [550, 450])] for index in range(1, 4)},
        **{index: [_plasma(index, _UNEXPLAINED)] for index in (4, 5)},
    },
    _CROSSED_OVER: {index: [_plasma(index, [998, 2])] for index in range(1, 5)},
    _COMMON: {index: [_plasma(index, _UNEXPLAINED)] for index in range(1, 4)},
    _PATHOGENIC: {index: [_plasma(index, _UNEXPLAINED)] for index in range(1, 4)},
}
_ANNOTATIONS: dict[str, list[dict[str, Any]]] = {
    _COMMON: [{"gnomad_af": 0.31}],
    _PATHOGENIC: [{"gnomad_af": 0.01, "clinvar": "Pathogenic"}],
}


def _record(variant_id: str, calls: list[tuple[str, list[int], dict[str, float]]]):
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
        gene_symbols=[],
        annotations=_ANNOTATIONS.get(variant_id, []),
        calls=[
            SmallVariantCall(sample=sample, gt="0/1", gq=None, dp=None, af=[], ad=ad, ps=None, metrics=metrics)
            for sample, ad, metrics in calls
        ],
    )


async def _seed_postgres() -> str:
    """The assembly, and the six families' plasmas and fathers, the plasmas tagged with the
    assay panel; the assembly's UUID."""
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.tests.integration._datastores import fresh_assembly, fresh_species

    async with get_postgres_sessionmaker()() as session:
        species = await fresh_species(session, f"e2e nipt seed {_TAG}")
        assembly_id = await fresh_assembly(session, species, _ASSEMBLY)
        for index in range(1, 7):
            family = (
                await session.execute(
                    text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                    {"f": f"E2ESEED{index}-{_TAG}"},
                )
            ).scalar_one()
            for sample, sex, metadata in (
                (_sample("CFDNA", index), "female", {"assay": "nipt_cfdna", "assay_panel": _PANEL}),
                (_sample("FATHER", index), "male", {}),
            ):
                await session.execute(
                    text(
                        "INSERT INTO samples (sample_id, family_id, sex, metadata) "
                        "VALUES (:s, CAST(:f AS uuid), :sex, CAST(:m AS jsonb))"
                    ),
                    {"s": sample, "f": family, "sex": sex, "m": json.dumps(metadata)},
                )
        await session.commit()
    return assembly_id


async def _exercise() -> dict[str, Any]:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import clickhouse_variant_storage as cvs
    from backend.app.services.clickhouse_family_variants import (
        count_cfdna_recurrence,
        fetch_recurrent_artifact_candidates,
    )
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.app.services.nipt_analysis import NiptQualityThresholds
    from backend.app.services.nipt_artifact_pg import (
        assay_cfdna_carrier_samples,
        auto_seed_nipt_artifacts,
        list_nipt_artifacts,
        recurrence_rules,
    )
    from backend.tests.e2e._harness import ensure_postgres_schema

    await ensure_postgres_schema()
    assembly_id = await _seed_postgres()
    await cvs.ensure_clickhouse_variant_tables(_ASSEMBLY)
    database = settings.clickhouse_database
    rules = recurrence_rules(NiptQualityThresholds())
    try:
        # One write per family, as each family's import writes its callset.
        for index in range(1, 7):
            records = [_record(variant, rows[index]) for variant, rows in _ROWS.items() if index in rows]
            projects = [_PROJECT, _OTHER_PROJECT] if index == 1 else [_PROJECT]
            await cvs.insert_small_variant_records(_ASSEMBLY, f"e2e-nipt-seed-{index}", projects, records)

        async with get_postgres_sessionmaker()() as session:
            carriers = await assay_cfdna_carrier_samples(session, assay_key=_PANEL)
            seeded = await auto_seed_nipt_artifacts(
                session, assembly_id=assembly_id, assay_key=_PANEL, min_unexplained_samples=_THRESHOLD
            )
            listed = await list_nipt_artifacts(session, assembly_id=assembly_id, assay_key=_PANEL)
        # The list's count, outside a family none of these is in.
        recurrence = await count_cfdna_recurrence(
            _ASSEMBLY, list(_ROWS), carrier_samples=carriers, exclude_family_uuid="none", rules=rules
        )
        await execute_clickhouse(f"OPTIMIZE TABLE {_small_table_name(_ASSEMBLY, 'entries')} FINAL")
        merged = await fetch_recurrent_artifact_candidates(
            _ASSEMBLY, carrier_samples=carriers, rules=rules, min_unexplained_samples=_THRESHOLD
        )
        unprotected = await fetch_recurrent_artifact_candidates(
            _ASSEMBLY, carrier_samples=carriers, rules=rules, min_unexplained_samples=2
        )
        return {
            "carriers": sorted(carriers),
            "seeded": seeded,
            "listed": [(row["variant_id"], row["recurrence_count"], row["source"]) for row in listed],
            "recurrence": recurrence,
            "merged": merged,
            "at_two": unprotected,
        }
    finally:
        for (name,) in await execute_clickhouse(
            "SELECT name FROM system.tables WHERE database = %(db)s AND name LIKE %(prefix)s",
            {"db": database, "prefix": f"{_ASSEMBLY}/%"},
        ):
            await execute_clickhouse(f"DROP TABLE IF EXISTS {database}.`{name}` SYNC")
        cvs._ensured_variant_table_assemblies.discard(_ASSEMBLY)


@pytest.fixture(scope="module")
def run() -> dict[str, Any]:
    from backend.tests.e2e import _harness

    return _harness.run_async(_exercise)


def test_the_seed_reads_the_assays_plasmas_only(run) -> None:
    assert run["carriers"] == sorted(_sample("CFDNA", index) for index in range(1, 7))


def test_only_the_unexplained_low_vaf_artefact_is_seeded(run) -> None:
    # Not the maternal het in six plasmas, the paternal allele in four, the allele maternal
    # in three and unexplained in two, the crossed-over reads, or the protected alleles.
    assert run["seeded"] == {"seeded": 1, "min_unexplained_samples": _THRESHOLD}
    assert run["listed"] == [(_ARTEFACT, 3, "auto")]


def test_a_recurrent_maternal_het_has_carriers_but_no_unexplained_call(run) -> None:
    # A count of every carrier would have listed it at any threshold up to six.
    maternal = run["recurrence"][_MATERNAL]
    assert (maternal.carriers, maternal.maternal, maternal.unexplained_low_vaf) == (6, 6, 0)
    paternal = run["recurrence"][_PATERNAL]
    assert (paternal.carriers, paternal.paternal_low_vaf, paternal.unexplained_low_vaf) == (4, 4, 0)


def test_the_seed_counts_as_the_list_counts(run) -> None:
    # The artefact's three calls are unexplained: no father's call, a low-level one and a
    # low-quality one. The first family, under two projects, counts once.
    unexplained = {variant: counts.unexplained_low_vaf for variant, counts in run["recurrence"].items()}
    assert unexplained == {
        _ARTEFACT: 3,
        _MATERNAL: 0,
        _PATERNAL: 0,
        _MOSTLY_MATERNAL: 2,
        _COMMON: 3,
        _PATHOGENIC: 3,
    }
    assert _CROSSED_OVER not in run["recurrence"]


def test_the_candidates_are_the_same_after_a_part_merge(run) -> None:
    assert run["merged"] == [(_ARTEFACT, 3)]


def test_a_common_or_pathogenic_allele_is_never_a_candidate(run) -> None:
    # At a threshold of two the mostly maternal allele joins; the common and the ClinVar
    # pathogenic allele, at three, still do not.
    assert run["at_two"] == [(_ARTEFACT, 3), (_MOSTLY_MATERNAL, 2)]
