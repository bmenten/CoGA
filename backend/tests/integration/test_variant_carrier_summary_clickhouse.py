"""The Variant Explorer counts each variant's carriers from the carrier summary exactly as from
the families' ``entries`` rows, on real ClickHouse and Postgres (``variant_carrier_summary``).

The reference is the aggregation the explorer ran over ``entries`` (:func:`_reference`): every
call of a member its family shows with an ALT allele, in the rows of the scope's projects that
the filters keep, each filter the family search's own clause; per variant, the distinct
families and samples, heterozygous and homozygous samples. The summary is brought up to date by
the reconciler (``reconcile_variant_carriers``), as the variant-carrier worker does, and the
explorer's search and export must list the same variants with the same counts, under every
filter and scope below, and again after each kind of change.

The cohort, in two fresh projects P1 and P2:

* A (P1): a trio, one callset.
* B (P1 and P2): a proband alone; its rows are stored under both projects, as every write of a
  family stores them under each project it is linked to.
* C (P2): a duo with two callsets (clair3, deepvariant): one variant annotated alike in both
  (one class), one annotated apart (two classes: carrier rows), and an imputed GLIMPSE2
  callset.
* D (P1): a proband and a sibling taken out of the family, whose calls stay in the rows.
* E (P2): the proband of B shown in a family of its own too (a member of two families).

Synthetic data only, on chromosome 13 at 62.3 Mb, where no other test writes. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import (
    fresh_assembly,
    fresh_species,
    run_closing_stores,
    vep_annotations,
)

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_CSQ = "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|BIOTYPE|HGVSc|HGVSp|CANONICAL|MANE_SELECT"


def _csq(consequence: str, impact: str, transcript: str = "ENST00000600001") -> list[dict[str, Any]]:
    return vep_annotations(
        _CSQ,
        [f"T|{consequence}|{impact}|SYNGENE1|ENSG00000600001|Transcript|{transcript}|protein_coding|||YES|"],
    )


_MISSENSE = "missense_variant"
_SYNONYMOUS = "synonymous_variant"

# Variants: (id, annotations under clair3, under deepvariant).
_SHARED = "13-62300101-C-T"  # A, B, C (both callsets alike), E
_SPLIT = "13-62300201-G-T"  # C: missense under clair3, synonymous under deepvariant
_IMPUTED = "13-62300301-A-T"  # C: imputed only; A: called
_TAKEN_OUT = "13-62300401-T-A"  # D: the taken-out sibling alone
_D_PROBAND = "13-62300501-C-A"  # D: proband and taken-out sibling


def _record(variant_id: str, calls: dict[str, str], *, source: str = "clair3", consequence: str = _MISSENSE) -> Any:
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    chrom, pos, ref, alt = variant_id.split("-")
    impact = "MODERATE" if consequence == _MISSENSE else "LOW"
    return SmallVariantRecord(
        variant_key=None, variant_id=variant_id, chr=chrom, start=int(pos), end=int(pos),
        ref=ref, alt=alt, source=source, rsid=None, filters=["PASS"], gene_symbols=[],
        annotations=_csq(consequence, impact),
        calls=[
            SmallVariantCall(sample=sample, gt=gt, gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)
            for sample, gt in calls.items()
        ],
        qual=100.0,
    )


@pytest.fixture(scope="module")
def cohort() -> dict[str, Any]:
    """The families of the module docstring, in Postgres and ClickHouse, the summary not yet
    brought up to date."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    tag = uuid4().hex[:8]

    async def _write() -> dict[str, Any]:
        async with get_postgres_sessionmaker()() as session:
            species = await fresh_species(session, f"carrier-summary {tag}")
            assembly_id = await fresh_assembly(session, species, _ASSEMBLY)
            projects = {}
            for label in ("P1", "P2"):
                projects[label] = (
                    await session.execute(
                        text(
                            "INSERT INTO projects (name, species_id, assembly_id) "
                            "VALUES (:n, CAST(:s AS uuid), CAST(:a AS uuid)) RETURNING id::text"
                        ),
                        {"n": f"carrier-summary {label} {tag}", "s": species, "a": assembly_id},
                    )
                ).scalar_one()
            layout = {
                "A": (("P1",), (("PROBAND-A", True), ("MOTHER-A", True), ("FATHER1-A", True))),
                "B": (("P1", "P2"), (("PROBAND-B", True),)),
                "C": (("P2",), (("PROBAND-C", True), ("MOTHER-C", True))),
                "D": (("P1",), (("PROBAND-D", True), ("SIBLING-D", False))),
                "E": (("P2",), (("PROBAND-B", True),)),
            }
            families: dict[str, str] = {}
            samples: dict[str, str] = {}
            names: dict[str, str] = {}
            for label, (linked, members) in layout.items():
                families[label] = str(
                    (
                        await session.execute(
                            text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                            {"f": f"CARR-{label}-{tag}"},
                        )
                    ).scalar_one()
                )
                for project in linked:
                    await session.execute(
                        text(
                            "INSERT INTO family_projects (family_id, project_id) "
                            "VALUES (CAST(:f AS uuid), CAST(:p AS uuid))"
                        ),
                        {"f": families[label], "p": projects[project]},
                    )
                for name, active in members:
                    if name not in samples:
                        names[name] = f"{name}-{tag}"
                        samples[name] = str(
                            (
                                await session.execute(
                                    text(
                                        "INSERT INTO samples (sample_id, family_id, sex) "
                                        "VALUES (:s, CAST(:f AS uuid), 'female') RETURNING id::text"
                                    ),
                                    {"s": names[name], "f": families[label]},
                                )
                            ).scalar_one()
                        )
                    await session.execute(
                        text(
                            "INSERT INTO family_members (family_id, sample_id, role, active) "
                            "VALUES (CAST(:f AS uuid), CAST(:s AS uuid), 'proband', :a)"
                        ),
                        {"f": families[label], "s": samples[name], "a": active},
                    )
            # The gene in the assembly's gene reference, which the views show it by: the
            # annotation tables store its symbol casefolded.
            await session.execute(
                text(
                    "INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, \"end\", strand, "
                    "biotype, description, source) VALUES (CAST(:a AS uuid), 'ENSG00000600001', 'SYNGENE1', "
                    "'13', 62300000, 62301000, 1, 'protein_coding', 'synthetic', 'test')"
                ),
                {"a": assembly_id},
            )
            await session.commit()

        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        n = names
        await insert_small_variant_records(
            _ASSEMBLY,
            families["A"],
            [projects["P1"]],
            [
                _record(_SHARED, {n["PROBAND-A"]: "0/1", n["MOTHER-A"]: "1/1", n["FATHER1-A"]: "0/0"}),
                _record(_IMPUTED, {n["PROBAND-A"]: "0/1", n["MOTHER-A"]: "0/0", n["FATHER1-A"]: "0/1"}),
            ],
        )
        await insert_small_variant_records(
            _ASSEMBLY,
            families["B"],
            [projects["P1"], projects["P2"]],
            [_record(_SHARED, {n["PROBAND-B"]: "1/1"})],
        )
        for source in ("clair3", "deepvariant"):
            await insert_small_variant_records(
                _ASSEMBLY,
                families["C"],
                [projects["P2"]],
                [
                    _record(_SHARED, {n["PROBAND-C"]: "0/1", n["MOTHER-C"]: "0/0"}, source=source),
                    _record(
                        _SPLIT,
                        {n["PROBAND-C"]: "0/1", n["MOTHER-C"]: "0/1" if source == "clair3" else "1/1"},
                        source=source,
                        consequence=_MISSENSE if source == "clair3" else _SYNONYMOUS,
                    ),
                ],
            )
        await insert_small_variant_records(
            _ASSEMBLY,
            families["C"],
            [projects["P2"]],
            [
                _record(_IMPUTED, {n["PROBAND-C"]: "0/1", n["MOTHER-C"]: "1/1"}, source="glimpse2"),
                _record(_SHARED, {n["PROBAND-C"]: "0/1", n["MOTHER-C"]: "0/1"}, source="glimpse2"),
            ],
        )
        await insert_small_variant_records(
            _ASSEMBLY,
            families["D"],
            [projects["P1"]],
            [
                _record(_TAKEN_OUT, {n["PROBAND-D"]: "0/0", n["SIBLING-D"]: "0/1"}),
                _record(_D_PROBAND, {n["PROBAND-D"]: "1/1", n["SIBLING-D"]: "0/1"}),
            ],
        )
        await insert_small_variant_records(
            _ASSEMBLY,
            families["E"],
            [projects["P2"]],
            [_record(_SHARED, {n["PROBAND-B"]: "0/1"}), _record(_D_PROBAND, {n["PROBAND-B"]: "0/1"})],
        )
        return {
            "tag": tag,
            "assembly_id": assembly_id,
            "projects": projects,
            "families": families,
            "samples": samples,
            "names": names,
        }

    return run_closing_stores(_write, init_schema=True)


def _scope(cohort: dict[str, Any], *projects: str) -> Any:
    from backend.app.services.variant_explorer_service import ExplorerScope

    return ExplorerScope(
        assembly_id=cohort["assembly_id"],
        assembly_name=_ASSEMBLY,
        project_ids=sorted(cohort["projects"][project] for project in projects),
    )


async def _reference(session: Any, scope: Any, filters: Any) -> dict[str, tuple[int, int, int, int]]:
    """Per variant ID, (families, samples, heterozygous, homozygous): the aggregation over
    ``entries`` the explorer's counts must equal (see the module docstring)."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services import variant_explorer_service as explorer
    from backend.app.services.clickhouse_variant_queries import (
        _small_annotation_filter_clauses,
        _small_row_filter_clauses,
    )
    from backend.app.services.genotypes import ALT_CLASSES, HOM_ALT, clickhouse_genotype_condition

    members = await explorer._shown_members(session, scope.project_ids)
    params: dict[str, Any] = {
        "project_guids": tuple(scope.project_ids),
        "shown_calls": explorer._shown_calls(members) or (("", ""),),
    }
    query_filters = explorer._family_search_filters(filters)
    clauses = ["e.sign = 1", "e.project_guid IN %(project_guids)s"]
    clauses += _small_row_filter_clauses(scope, query_filters, params=params, exclude_imputed=not filters.include_imputed)
    clauses += _small_annotation_filter_clauses(scope, query_filters, params=params)
    carrier = clickhouse_genotype_condition("gt", ALT_CLASSES, param="ref_alt", params=params)
    hom = clickhouse_genotype_condition("gt", {HOM_ALT}, param="ref_hom", params=params)
    rows = await execute_clickhouse(
        f"""
        SELECT any(variantId), uniqExact(family_guid), uniqExact(sample_id),
               uniqExactIf(sample_id, NOT is_hom), uniqExactIf(sample_id, is_hom)
        FROM (
            SELECT e.key AS key, e.variantId AS variantId, e.family_guid AS family_guid, sample_id, ({hom}) AS is_hom
            FROM {explorer._small_table_name(_ASSEMBLY, 'entries')} AS e
            ARRAY JOIN e.`calls.sampleId` AS sample_id, e.`calls.gt` AS gt
            WHERE {' AND '.join(clauses)} AND {carrier} AND (e.family_guid, sample_id) IN %(shown_calls)s
        )
        GROUP BY key
        """,
        params,
    )
    return {str(row[0]): (int(row[1]), int(row[2]), int(row[3]), int(row[4])) for row in rows}


async def _explorer(session: Any, scope: Any, filters: Any) -> dict[str, Any]:
    """The explorer's search (every page) and export, each as {variant ID: (families,
    samples, heterozygous, homozygous)}, and the pending families of the search."""
    from backend.app.services import variant_explorer_service as explorer

    async def resolve(_session: Any, _user: Any, _assembly_id: Any) -> Any:
        return scope

    original = explorer.resolve_scope
    explorer.resolve_scope = resolve  # type: ignore[assignment]
    try:
        rows: dict[str, tuple[int, int, int, int]] = {}
        cursor = None
        pending = None
        while True:
            page = await explorer.search_global_small_variants(
                session, user=None, filters=filters, cursor=cursor, page_size=2
            )
            pending = page.pending_families if pending is None else pending
            for row in page.variants:
                assert row.variant_id not in rows
                rows[row.variant_id] = (row.total_families, row.total_samples, row.het_samples, row.hom_samples)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        export = await explorer.export_global_small_variants(session, user=None, filters=filters)
    finally:
        explorer.resolve_scope = original  # type: ignore[assignment]
    return {
        "rows": rows,
        "total": page.total,
        "export": {
            row.variant_id: (row.total_families, row.total_samples, row.het_samples, row.hom_samples)
            for row in export.rows
        },
        "pending": pending,
    }


def _up_to_date(cohort: dict[str, Any]) -> Any:
    from backend.app.services.variant_carrier_summary import reconcile_variant_carriers

    return reconcile_variant_carriers(
        cohort["assembly_id"], _ASSEMBLY, family_uuids=list(cohort["families"].values())
    )


_FILTERS = [
    pytest.param({}, id="unfiltered"),
    pytest.param({"include_imputed": True}, id="imputed-included"),
    pytest.param({"impacts": ["MODERATE"]}, id="missense-class-only"),
    pytest.param({"impacts": ["LOW"]}, id="synonymous-class-only"),
    pytest.param({"variant_type": "SNV", "chromosome": "chr13", "start": 62_300_150}, id="locus"),
]
_SCOPES = [("P1", "P2"), ("P1",), ("P2",)]


def _compare(cohort: dict[str, Any], filters: dict[str, Any]) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.variant_explorer_service import GlobalVariantFilters

    async def _run() -> list[tuple[Any, ...]]:
        await _up_to_date(cohort)
        outcomes = []
        async with get_postgres_sessionmaker()() as session:
            for projects in _SCOPES:
                scope = _scope(cohort, *projects)
                expected = await _reference(session, scope, GlobalVariantFilters(**filters))
                observed = await _explorer(session, scope, GlobalVariantFilters(**filters))
                outcomes.append((projects, expected, observed))
        return outcomes

    outcomes = run_closing_stores(_run)
    # Some scope keeps a variant (a filter may keep none of one project's).
    assert any(expected for _projects, expected, _observed in outcomes)
    for projects, expected, observed in outcomes:
        assert observed["pending"] == 0, projects
        assert observed["rows"] == expected, projects
        assert observed["export"] == expected, projects
        assert observed["total"] == len(expected), projects


@pytest.mark.parametrize("filters", _FILTERS)
def test_the_summary_counts_what_the_families_rows_count(cohort: dict[str, Any], filters: dict[str, Any]) -> None:
    _compare(cohort, filters)


def test_the_cohort_holds_each_kind_of_row(cohort: dict[str, Any]) -> None:
    # The cases the module docstring names are in the summary: aggregate rows, the carrier
    # rows of a variant of two classes and of a sample two families show, and imputed rows.
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.variant_carrier_summary import CARRIERS_SUFFIX
    from backend.app.services.clickhouse_variant_ids import _small_table_name

    families = cohort["families"]

    async def _run() -> list[tuple[Any, ...]]:
        await _up_to_date(cohort)
        return await execute_clickhouse(
            f"""
            SELECT family_guid, variantId, sample_id, imputed, sum(samples)
            FROM {_small_table_name(_ASSEMBLY, CARRIERS_SUFFIX)}
            WHERE family_guid IN %(families)s AND key != 0
            GROUP BY family_guid, variantId, sample_id, imputed, annotationSetHash
            HAVING sum(samples) != 0
            """,
            {"families": tuple(families.values())},
        )

    rows = run_closing_stores(_run)
    names = {value: key for key, value in cohort["names"].items()}
    by_family = {value: key for key, value in families.items()}
    detail = sorted((by_family[row[0]], row[1], names[row[2]], bool(row[3])) for row in rows)
    # C's split variant: one row per class and carrier (two classes, two carriers each);
    # C's shared variant is called and imputed: carrier rows, both callsets' classes;
    # the proband of B and E carries the shared and D's variants: carrier rows in B and E.
    assert ("C", _SPLIT, "PROBAND-C", False) in detail
    assert sum(1 for row in detail if row[:2] == ("C", _SPLIT)) == 4
    assert ("C", _SHARED, "MOTHER-C", True) in detail
    assert ("B", _SHARED, "PROBAND-B", False) in detail
    assert ("E", _SHARED, "PROBAND-B", False) in detail
    assert ("E", _D_PROBAND, "PROBAND-B", False) in detail


def test_a_change_is_pending_until_the_summary_is_brought_up_to_date(cohort: dict[str, Any]) -> None:
    # Family A's mother is taken out of the family, B's rows are deleted, and D's rows are
    # written again with another call: until the reconciler runs, the explorer names those
    # families pending; then it counts what the rows count again.
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clickhouse_variant_storage import (
        delete_family_small_variants,
        insert_small_variant_records,
    )
    from backend.app.services.family_variant_write_lock import SMALL_VARIANTS, lock_family_variant_writes
    from backend.app.services.variant_explorer_service import GlobalVariantFilters

    families = cohort["families"]
    n = cohort["names"]

    async def _run() -> tuple[int, list[str], int]:
        await _up_to_date(cohort)
        async with get_postgres_sessionmaker()() as session:
            await session.execute(
                text(
                    "UPDATE family_members SET active = false "
                    "WHERE family_id = CAST(:f AS uuid) AND sample_id = CAST(:s AS uuid)"
                ),
                {"f": families["A"], "s": cohort["samples"]["MOTHER-A"]},
            )
            await session.commit()
        for label, write in (
            ("B", lambda: delete_family_small_variants(_ASSEMBLY, families["B"])),
            (
                "D",
                lambda: insert_small_variant_records(
                    _ASSEMBLY,
                    families["D"],
                    [cohort["projects"]["P1"]],
                    [_record("13-62300601-G-C", {n["PROBAND-D"]: "0/1", n["SIBLING-D"]: "1/1"})],
                ),
            ),
        ):
            # A writer holds the family's lock from its first write.
            async with get_postgres_sessionmaker()() as session:
                async with session.begin():
                    await lock_family_variant_writes(session, families[label], [SMALL_VARIANTS])
                    await write()
        async with get_postgres_sessionmaker()() as session:
            observed = await _explorer(session, _scope(cohort, "P1", "P2"), GlobalVariantFilters())
        status = await _up_to_date(cohort)
        return observed["pending"], sorted(status.refreshed), len(status.pending)

    pending, refreshed, left = run_closing_stores(_run)
    assert pending == 3
    by_family = {value: key for key, value in families.items()}
    assert [by_family[family] for family in refreshed] == sorted(["A", "B", "D"], key=lambda label: families[label])
    assert left == 0
    for filters in ({}, {"include_imputed": True}):
        _compare(cohort, filters)


def test_a_writer_takes_the_family_out_before_its_first_write(cohort: dict[str, Any]) -> None:
    # Taken out, the family is counted by no row: its markers sum to nothing, and its
    # contribution's rows cancel. The reconciler adds it again.
    from backend.app.services.variant_carrier_summary import (
        _applied_generations,
        detach_family_variant_carriers,
    )

    family = cohort["families"]["C"]

    async def _run() -> tuple[dict[int, int], dict[int, int]]:
        await _up_to_date(cohort)
        before = (await _applied_generations(_ASSEMBLY, [family])).get(family, {})
        await detach_family_variant_carriers(_ASSEMBLY, family)
        after = (await _applied_generations(_ASSEMBLY, [family])).get(family, {})
        return before, after

    before, after = run_closing_stores(_run)
    assert list(before.values()) == [1]
    assert after == {}
    _compare(cohort, {})


def test_the_views_sum_what_the_variant_view_counts(cohort: dict[str, Any]) -> None:
    # The gene view's row of a gene is the sum of the variant view's rows under that gene;
    # the transcript view's, of the variants on that transcript; the frequency view's cells
    # count every variant once.
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services import variant_explorer_service as explorer
    from backend.app.services import variant_explorer_views as views
    from backend.app.services.variant_explorer_service import GlobalVariantFilters

    scope = _scope(cohort, "P1", "P2")

    async def resolve(_session: Any, _user: Any, _assembly_id: Any) -> Any:
        return scope

    async def _run() -> tuple[Any, Any, Any, dict[str, Any], dict[str, Any]]:
        await _up_to_date(cohort)
        original = explorer.resolve_scope
        explorer.resolve_scope = resolve  # type: ignore[assignment]
        views.resolve_scope = resolve  # type: ignore[assignment]
        try:
            async with get_postgres_sessionmaker()() as session:
                genes = await views.explorer_gene_view(session, user=None, filters=GlobalVariantFilters())
                transcripts = await views.explorer_transcript_view(
                    session, user=None, filters=GlobalVariantFilters()
                )
                frequency = await views.explorer_frequency_view(session, user=None, filters=GlobalVariantFilters())
                by_gene = await _explorer(session, scope, GlobalVariantFilters(gene="SYNGENE1"))
                every = await _explorer(session, scope, GlobalVariantFilters())
        finally:
            explorer.resolve_scope = original  # type: ignore[assignment]
            views.resolve_scope = original  # type: ignore[assignment]
        return genes, transcripts, frequency, by_gene, every

    genes, transcripts, frequency, by_gene, every = run_closing_stores(_run)
    (gene,) = [row for row in genes.genes if row.gene == "SYNGENE1"]
    assert gene.variants == len(by_gene["rows"])
    assert gene.carriers == sum(counts[1] for counts in by_gene["rows"].values())
    assert gene.het == sum(counts[2] for counts in by_gene["rows"].values())
    assert gene.hom == sum(counts[3] for counts in by_gene["rows"].values())
    (transcript,) = [row for row in transcripts.rows if row.transcript_id == "ENST00000600001"]
    assert transcript.variants == len(by_gene["rows"])
    assert transcript.carriers == gene.carriers
    # The split variant is missense under one callset and synonymous under the other: on the
    # transcript its most severe class, missense.
    assert transcript.missense + transcript.synonymous == transcript.variants
    assert sum(cell.variants for cell in frequency.cells) == len(every["rows"])
    assert {cell.gnomad_af_class for cell in frequency.cells if cell.variants} == {"absent"}
