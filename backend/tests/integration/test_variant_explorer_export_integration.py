"""The Variant Explorer CSV export against real ClickHouse and Postgres (DATA-2).

The export stopped at 50,000 rows without saying so, searched at most 200,000 ids of its
tag / classification filter without saying so either, and failed outright past 32,767 rows:
the review lookup for its rows bound one parameter per variant, more than asyncpg takes.
Against the real engines this checks that the export reads one row past its cap and says
when it was cut, that the tag / classification filter takes its first ids in variant_id
order and says when more matched, and that the review lookup takes any number of ids. It also
checks that the classification filter finds a review's class label whatever its case (ENG-6), and
that a variant reviewed in two families shows the more severe classification (CLIN-69).

``resolve_scope`` is monkeypatched to one fresh project, so no user is needed; the project,
its family and the reviews are real Postgres rows, the variants real ClickHouse rows. The
caps are lowered so that a handful of variants exceed them.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import fresh_assembly, fresh_species, run_closing_stores

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
# Five variants, by carrier count: 1-100 (3), 1-200 (2), 1-400 (2), 1-300 (1), 1-500 (1).
_CARRIERS = {
    "1-100-A-G": ("S1", "S2", "S3"),
    "1-200-A-G": ("S1", "S2"),
    "1-300-A-G": ("S1",),
    "1-400-A-G": ("S2", "S3"),
    "1-500-A-G": ("S3",),
}
_TAGS = {"1-100-A-G": ["report"], "1-200-A-G": ["report"], "1-300-A-G": ["other"]}


def _record(variant_id: str, samples: tuple[str, ...]):
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    pos = int(variant_id.split("-")[1])
    calls = [
        SmallVariantCall(sample=sample, gt="0/1", gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)
        for sample in samples
    ]
    return SmallVariantRecord(
        variant_key=None, variant_id=variant_id, chr="1", start=pos, end=pos, ref="A", alt="G",
        source="test", rsid=None, filters=["PASS"], gene_symbols=[], annotations=[], calls=calls,
        qual=100.0,
    )


async def _seed(session_factory) -> tuple[str, str]:
    """A project with one family, its reviews (two variants tagged report, one other) and
    its five variants in ClickHouse; returns (project id, assembly id)."""
    from sqlalchemy import text

    from backend.app.services.clickhouse_variant_storage import insert_small_variant_records

    tag = uuid4().hex[:12]
    async with session_factory() as session:
        species = await fresh_species(session, f"explorer-export {tag}")
        assembly_id = await fresh_assembly(session, species, f"EXPLOREREXPORT{tag}")
        project = (
            await session.execute(
                text(
                    "INSERT INTO projects (name, species_id, assembly_id) "
                    "VALUES (:n, CAST(:s AS uuid), CAST(:a AS uuid)) RETURNING id::text"
                ),
                {"n": f"explorer-export {tag}", "s": species, "a": assembly_id},
            )
        ).scalar_one()
        family = (
            await session.execute(
                text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                {"f": f"explorer-export-{tag}"},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO family_projects (family_id, project_id) "
                "VALUES (CAST(:f AS uuid), CAST(:p AS uuid))"
            ),
            {"f": family, "p": project},
        )
        for variant_id, tags in _TAGS.items():
            await session.execute(
                text(
                    "INSERT INTO small_variant_reviews (family_id, variant_id, tags, updated_by) "
                    "VALUES (CAST(:f AS uuid), :v, CAST(:t AS jsonb), 'reviewer')"
                ),
                {"f": family, "v": variant_id, "t": json.dumps(tags)},
            )
        await session.commit()

    await insert_small_variant_records(
        _ASSEMBLY,
        family,
        [project],
        [_record(variant_id, samples) for variant_id, samples in _CARRIERS.items()],
    )
    return project, assembly_id


def _scope_to(monkeypatch, svc, project: str, assembly_id: str) -> None:
    scope = svc.ExplorerScope(assembly_id=assembly_id, assembly_name=_ASSEMBLY, project_ids=[project])

    async def _resolve_scope(session, user, requested_assembly_id):
        return scope

    monkeypatch.setattr(svc, "resolve_scope", _resolve_scope)


def test_explorer_export_says_when_it_was_cut(monkeypatch) -> None:
    import backend.app.services.variant_explorer_service as svc
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema

    async def _scenario() -> None:
        await init_postgres_schema()
        session_factory = get_postgres_sessionmaker()
        project, assembly_id = await _seed(session_factory)
        _scope_to(monkeypatch, svc, project, assembly_id)

        async def _export(limit: int, **filters):
            async with session_factory() as session:
                return await svc.export_global_small_variants(
                    session,
                    user=None,
                    filters=svc.GlobalVariantFilters(**filters),
                    assembly_id=assembly_id,
                    limit=limit,
                )

        # Five variants match: a cap of two or four cuts the file and says so, with the
        # most-carried variants first; a cap of five or more holds them all.
        cut = await _export(2)
        assert [row.variant_id for row in cut.rows] == ["1-100-A-G", "1-200-A-G"]
        assert cut.truncated_reason == "row-limit"
        assert (await _export(4)).truncated_reason == "row-limit"
        for limit in (5, 6):
            complete = await _export(limit)
            assert len(complete.rows) == 5
            assert complete.truncated is False
        # The rows carry their reviews' tags from Postgres.
        assert {row.variant_id: row.tags for row in complete.rows}["1-100-A-G"] == ["report"]

        # Two variants are tagged report. With the filter's cap at one, the search takes the
        # first in variant_id order and the export says it was partial; at two it is whole.
        monkeypatch.setattr(svc, "_MAX_TAG_FILTER_VARIANT_IDS", 1)
        partial = await _export(50, review_tags=["report"])
        assert [row.variant_id for row in partial.rows] == ["1-100-A-G"]
        assert partial.truncated_reason == "review-filter-limit"
        monkeypatch.setattr(svc, "_MAX_TAG_FILTER_VARIANT_IDS", 2)
        whole = await _export(50, review_tags=["report"])
        assert sorted(row.variant_id for row in whole.rows) == ["1-100-A-G", "1-200-A-G"]
        assert whole.truncated is False

        # The review lookup takes more ids than one statement may have parameters (32,767):
        # an export of up to 50,001 rows looks up every one of them.
        async with session_factory() as session:
            reviews = await svc._review_display_map(
                session,
                project_ids=[project],
                variant_ids=["1-100-A-G", *(f"2-{pos}-A-G" for pos in range(40_000))],
            )
        assert list(reviews) == ["1-100-A-G"]
        assert reviews["1-100-A-G"]["tags"] == {"report"}

    run_closing_stores(_scenario)


def test_explorer_finds_and_shows_a_classification_whatever_the_case_of_its_label(monkeypatch) -> None:
    # ENG-6: the class labels are in sentence case. A review saved before holds "Likely
    # Pathogenic - class 4"; the classification filter, lowercased on both sides in Postgres,
    # finds it by either spelling. CLIN-69: a variant reviewed in two families shows the more
    # severe classification, as stored, whichever review Postgres reads first.
    import backend.app.services.variant_explorer_service as svc
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema

    async def _scenario() -> None:
        await init_postgres_schema()
        session_factory = get_postgres_sessionmaker()
        project, assembly_id = await _seed(session_factory)
        _scope_to(monkeypatch, svc, project, assembly_id)
        async with session_factory() as session:
            for variant_id, classification, pair in (
                ("1-400-A-G", "Likely benign - class 2", None),
                ("1-400-A-G", "Likely Pathogenic - class 4", None),
                ("1-500-A-G", None, "likely pathogenic - CLASS 4"),
            ):
                family = (
                    await session.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": f"explorer-classified-{uuid4().hex[:12]}"},
                    )
                ).scalar_one()
                await session.execute(
                    text(
                        "INSERT INTO family_projects (family_id, project_id) "
                        "VALUES (CAST(:f AS uuid), CAST(:p AS uuid))"
                    ),
                    {"f": family, "p": project},
                )
                await session.execute(
                    text(
                        "INSERT INTO small_variant_reviews (family_id, variant_id, classification, "
                        "compound_het_classification, updated_by) VALUES (CAST(:f AS uuid), :v, :c, :p, 'reviewer')"
                    ),
                    {"f": family, "v": variant_id, "c": classification, "p": pair},
                )
            await session.commit()

        async def _export(**filters):
            async with session_factory() as session:
                return await svc.export_global_small_variants(
                    session, user=None, filters=svc.GlobalVariantFilters(**filters), assembly_id=assembly_id, limit=50
                )

        for wanted in ("Likely pathogenic - class 4", "Likely Pathogenic - class 4"):
            found = await _export(classifications=[wanted])
            assert sorted(row.variant_id for row in found.rows) == ["1-400-A-G", "1-500-A-G"], wanted
        shown = {row.variant_id: row.classification for row in (await _export()).rows}
        assert shown["1-400-A-G"] == "Likely Pathogenic - class 4"
        assert shown["1-500-A-G"] == "likely pathogenic - CLASS 4"
        assert (await _export(classifications=["likely benign - class 2"])).rows[0].variant_id == "1-400-A-G"

    run_closing_stores(_scenario)
