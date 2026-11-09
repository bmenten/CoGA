"""The Variant Explorer CSV export against real ClickHouse and Postgres (DATA-2).

The export stopped at 50,000 rows without saying so, searched at most 200,000 ids of its
tag / classification filter without saying so either, and failed outright past 32,767 rows:
the review lookup for its rows bound one parameter per variant, more than asyncpg takes.
Against the real engines this checks that the export reads one row past its cap and says
when it was cut, that the tag / classification filter takes its first ids in variant_id
order and says when more matched, and that the review lookup takes any number of ids. It also
checks that the classification filter finds a review's class label whatever its case (ENG-6), and
that a variant reviewed in two families shows the more severe classification (CLIN-69): each
review's class as the family page reads it, a review classified by its class tag alone too, of one
class twice the review saved last, and the classification filter keeping what the family search's
keeps (CLIN-71).

``resolve_scope`` is monkeypatched to one fresh project, so no user is needed; the project,
its family and the reviews are real Postgres rows, the variants real ClickHouse rows. The
caps are lowered so that a handful of variants exceed them.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import explorer_shows, fresh_assembly, fresh_species, run_closing_stores

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


async def _seed(session_factory) -> tuple[str, str, str]:
    """A project with one family, its reviews (two variants tagged report, one other) and
    its five variants in ClickHouse; returns (project id, assembly id, family UUID)."""
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
    return project, assembly_id, family


def _scope_to(monkeypatch, svc, project: str, assembly_id: str, family: str) -> None:
    """The Explorer searches the project, whose family shows S1, S2 and S3."""
    explorer_shows(monkeypatch, {family: ["S1", "S2", "S3"]})
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
        project, assembly_id, family = await _seed(session_factory)
        _scope_to(monkeypatch, svc, project, assembly_id, family)

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
        project, assembly_id, family = await _seed(session_factory)
        _scope_to(monkeypatch, svc, project, assembly_id, family)
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


def test_explorer_shows_the_class_a_review_holds_by_its_tag_and_filters_as_the_family_search(monkeypatch) -> None:
    # CLIN-71: the Explorer read a review's classification text alone, so a review classified by
    # its class tag (no text) showed no class, though the family page shows one; and with no
    # ORDER BY, of two reviews of one class the first Postgres returned was shown. Each review now
    # shows its class as the family page reads it, the reviews read saved last first, then by
    # family id. The classification filter keeps, family by family, what the family search's keeps.
    import backend.app.services.variant_explorer_service as svc
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.small_variant_review_pg import list_matching_small_variant_review_ids

    async def _scenario() -> None:
        await init_postgres_schema()
        session_factory = get_postgres_sessionmaker()
        project, assembly_id, base_family = await _seed(session_factory)
        _scope_to(monkeypatch, svc, project, assembly_id, base_family)
        families = [base_family]
        same_time: dict[str, str] = {}
        async with session_factory() as session:
            for variant_id, review, saved in (
                # Classified by the class tag alone, beside a milder class in another family.
                ("1-400-A-G", {"tags": ["acmg_class_4"]}, "2026-11-01"),
                ("1-400-A-G", {"classification": "Likely benign - class 2"}, "2026-11-02"),
                # The pair classified by its class tag alone.
                ("1-500-A-G", {"compound_het_tags": ["acmg_class_5"]}, "2026-11-01"),
                # A class word, shown as its class label.
                ("1-300-A-G", {"classification": "VUS"}, "2026-11-01"),
                # One class twice, the later review classified by its tag: the later one shown.
                ("1-100-A-G", {"classification": "acmg_class_4"}, "2026-11-01"),
                ("1-100-A-G", {"tags": ["acmg_class_4"]}, "2026-11-02"),
                # One class twice, saved at the same time: the lower family id's shown.
                ("1-200-A-G", {"classification": "acmg_class_3"}, "2026-11-03"),
                ("1-200-A-G", {"tags": ["acmg_class_3"]}, "2026-11-03"),
            ):
                family = (
                    await session.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": f"explorer-class-tag-{uuid4().hex[:12]}"},
                    )
                ).scalar_one()
                families.append(family)
                await session.execute(
                    text(
                        "INSERT INTO family_projects (family_id, project_id) "
                        "VALUES (CAST(:f AS uuid), CAST(:p AS uuid))"
                    ),
                    {"f": family, "p": project},
                )
                await session.execute(
                    text(
                        "INSERT INTO small_variant_reviews (family_id, variant_id, classification, tags, "
                        "compound_het_tags, updated_by, updated_at) VALUES (CAST(:f AS uuid), :v, :c, "
                        "CAST(:t AS jsonb), CAST(:pt AS jsonb), 'reviewer', :u)"
                    ),
                    {
                        "f": family,
                        "v": variant_id,
                        "c": review.get("classification"),
                        "t": json.dumps(review.get("tags", [])),
                        "pt": json.dumps(review.get("compound_het_tags", [])),
                        "u": datetime.fromisoformat(f"{saved}T09:00:00+00:00"),
                    },
                )
                if variant_id == "1-200-A-G":
                    same_time[family] = (
                        "VUS - class 3" if review.get("tags") else str(review.get("classification"))
                    )
            await session.commit()

        async with session_factory() as session:
            exported = await svc.export_global_small_variants(
                session, user=None, filters=svc.GlobalVariantFilters(), assembly_id=assembly_id, limit=50
            )
        shown = {row.variant_id: row.classification for row in exported.rows}
        assert shown["1-400-A-G"] == "Likely pathogenic - class 4"
        assert shown["1-500-A-G"] == "Pathogenic - class 5"
        assert shown["1-300-A-G"] == "VUS - class 3"
        assert shown["1-100-A-G"] == "Likely pathogenic - class 4"
        assert shown["1-200-A-G"] == same_time[min(same_time)]

        # The classification filter keeps the variants a family search's classification filter
        # keeps in some family: by the classification text, its pair's too, and no tag. A review
        # classified by its class tag alone is kept by the tag filter.
        async with session_factory() as session:
            for wanted in (
                "Pathogenic - class 5",
                "Likely pathogenic - class 4",
                "VUS - class 3",
                "VUS",
                "Likely benign - class 2",
                "acmg_class_4",
            ):
                explorer, _capped = await svc._variant_ids_matching_reviews(
                    session, project_ids=[project], classifications=[wanted], tags=[], limit=100
                )
                family_search: set[str] = set()
                for family in families:
                    family_search |= await list_matching_small_variant_review_ids(
                        session, family_uuid=family, classifications=[wanted]
                    )
                assert set(explorer) == family_search, wanted
            kept, _capped = await svc._variant_ids_matching_reviews(
                session, project_ids=[project], classifications=["Likely pathogenic - class 4"], tags=[], limit=100
            )
            assert "1-400-A-G" not in kept
            tagged, _capped = await svc._variant_ids_matching_reviews(
                session, project_ids=[project], classifications=[], tags=["acmg_class_4"], limit=100
            )
            assert sorted(tagged) == ["1-100-A-G", "1-400-A-G"]

    run_closing_stores(_scenario)
