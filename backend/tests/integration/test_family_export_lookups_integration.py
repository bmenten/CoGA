"""The family CSV exports past 32,767 rows, on real Postgres and ClickHouse (DATA-3).

A family export reads up to 50,001 rows, its 50,000-row cap and one more to know whether more
matched (#512), and looks each row up in Postgres: its review, its gene's constraint metrics
and the structural variants on its genes (the SV second-hit badge). The lookups bound one
parameter per id, and asyncpg refuses a statement with more than 32,767 parameters, so every
export of more than about 32,766 rows answered 500 and its truncation signal never fired.
They now bind their ids as one text[] parameter.

On real Postgres, each lookup is run with 50,001 ids and finds exactly the rows seeded for
them, one of them past the 32,767th id and none for an id it was not given. An empty id list
reads no rows, where an expanding IN over no ids was refused (text = integer).

On real ClickHouse and Postgres, one family holds 50,001 small variants and 50,001 SVs, and
both are exported through their routes: each file holds the first 50,000 rows, is named
...-TRUNCATED-first-50000.csv and says so in its X-CoGA-Export-* headers, and its rows carry
their reviews, the row past the 32,767th too. The small variants exported carry their gene
constraint and their SV second hit as well.

The family context is built by hand (one sample); the family, its reviews, the gene
constraint rows and the SV gene index are real Postgres rows, the variants real ClickHouse
rows (the family's rows are deleted afterwards). Synthetic data only. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_SAMPLE = "PROBAND"
# What an export reads: its 50,000-row cap and one row more (#512).
_EXPORT_READ = 50_001
_EXPORT_CAP = 50_000
# A row past the 32,767 parameters asyncpg sends in one statement.
_PAST_THE_LIMIT = 40_000
# The rows the reviews are seeded for: the first, one past the limit, and the one row the
# export reads past its cap.
_REVIEWED = (0, _PAST_THE_LIMIT, _EXPORT_CAP)
# Positions no other real-engine test writes, so no shared variant row is touched.
_FIRST_POS = 50_000_001


def _small_variant_id(index: int) -> str:
    return f"13-{_FIRST_POS + index}-A-G"


def _sv_id(index: int) -> str:
    return f"data3_DEL_13_{_FIRST_POS + 10 * index}"


def _gene(index: int) -> str:
    return f"DATA3G{index:05d}"


def _gene_id(index: int) -> str:
    return f"ENSGDATA3{index:05d}"


def _run(coroutine_factory) -> None:
    """Run under its own loop, then close the module-global ClickHouse client and Postgres
    engine, which bind to the loop (the next asyncio.run would otherwise hit a closed one)."""
    import asyncio

    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.core.postgres import close_postgres_engine

    async def _wrapped() -> None:
        try:
            await coroutine_factory()
        finally:
            await close_clickhouse_client()
            await close_postgres_engine()

    asyncio.run(_wrapped())


async def _seed_postgres(session_factory) -> dict[str, str]:
    """A family with the reviews of ``_REVIEWED`` (small variants and SVs) and of one
    variant no lookup asks for, and an assembly whose gene_info holds the constraint (pLI)
    of the genes of rows 0 and ``_PAST_THE_LIMIT`` (found by symbol), of row 1's gene under
    another symbol (found by its gene id only) and of one gene no lookup asks for."""
    from sqlalchemy import text

    tag = uuid4().hex[:12]
    async with session_factory() as session:
        species = (
            await session.execute(
                text(
                    "INSERT INTO species (name, common_name, tax_id) "
                    "VALUES (:n, 'test', :t) RETURNING id::text"
                ),
                {"n": f"family-export {tag}", "t": uuid4().int % 2_000_000_000},
            )
        ).scalar_one()
        assembly = (
            await session.execute(
                text(
                    "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                    "VALUES (CAST(:s AS uuid), :a, 'v1', '2020-01-01') RETURNING id::text"
                ),
                {"s": species, "a": f"FAMILYEXPORT{tag}"},
            )
        ).scalar_one()
        label = f"family-export-{tag}"
        family = (
            await session.execute(
                text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                {"f": label},
            )
        ).scalar_one()
        for table, variant_ids in (
            ("small_variant_reviews", [_small_variant_id(i) for i in _REVIEWED] + ["13-1-A-G"]),
            ("structural_variant_reviews", [_sv_id(i) for i in _REVIEWED] + ["data3_DEL_13_1"]),
        ):
            for variant_id in variant_ids:
                await session.execute(
                    text(
                        f"INSERT INTO {table} (family_id, variant_id, classification, tags, updated_by) "
                        "VALUES (CAST(:f AS uuid), :v, 'VUS - class 3', CAST(:t AS jsonb), 'reviewer')"
                    ),
                    {"f": family, "v": variant_id, "t": json.dumps(["report"])},
                )
        for symbol, gene_id, pli in (
            (_gene(0), None, 0.11),
            ("DATA3RENAMED", _gene_id(1), 0.22),
            (_gene(_PAST_THE_LIMIT), None, 0.97),
            ("DATA3UNASKED", "ENSGDATA3UNASKED", 0.5),
        ):
            await session.execute(
                text(
                    "INSERT INTO gene_info (assembly_id, hgnc_symbol, gene_id, extra) "
                    "VALUES (CAST(:a AS uuid), :s, :g, CAST(:e AS jsonb))"
                ),
                {"a": assembly, "s": symbol, "g": gene_id, "e": json.dumps({"pLI": pli})},
            )
        await session.commit()
    return {"family": family, "label": label}


def test_the_lookups_take_more_ids_than_a_statement_has_parameters() -> None:
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.clickhouse_family_variants import _fetch_gene_constraint_metric_map
    from backend.app.services.small_variant_review_pg import get_small_variant_review_map
    from backend.app.services.structural_variant_review_pg import get_structural_variant_review_map
    from backend.app.services.sv_gene_index_service import get_sv_second_hits

    small_ids = [_small_variant_id(i) for i in range(_EXPORT_READ)]
    sv_ids = [_sv_id(i) for i in range(_EXPORT_READ)]
    genes = [_gene(i) for i in range(_EXPORT_READ)]
    # Each row with its gene's symbol and id: two lists of 50,001.
    variants = [
        SimpleNamespace(id=small_ids[i], gene=genes[i], gene_id=_gene_id(i)) for i in range(_EXPORT_READ)
    ]

    async def _scenario() -> None:
        await init_postgres_schema()
        session_factory = get_postgres_sessionmaker()
        seeded = await _seed_postgres(session_factory)
        family = seeded["family"]
        async with session_factory() as session:
            # The SV gene index of the second-hit badge, as the page builds it.
            for gene in (_gene(_PAST_THE_LIMIT), "DATA3UNASKED"):
                await session.execute(
                    text(
                        "INSERT INTO family_sv_gene_index (family_id, gene_symbol, sv_count, svs) "
                        "VALUES (CAST(:f AS uuid), :g, 1, CAST(:s AS jsonb))"
                    ),
                    {"f": family, "g": gene, "s": json.dumps([{"sv_id": "sv-1", "sv_type": "DEL"}])},
                )
            await session.commit()

        async with session_factory() as session:
            reviews = await get_small_variant_review_map(session, family_uuid=family, variant_ids=small_ids)
            sv_reviews = await get_structural_variant_review_map(session, family_uuid=family, variant_ids=sv_ids)
            metrics = await _fetch_gene_constraint_metric_map(session, variants)
            second_hits = await get_sv_second_hits(session, family_uuid=family, gene_symbols=set(genes))

        # Exactly the rows seeded for the ids asked for, one past the 32,767th among them.
        assert sorted(reviews) == sorted(small_ids[i] for i in _REVIEWED)
        assert all(review.tags == ["report"] for review in reviews.values())
        assert sorted(sv_reviews) == sorted(sv_ids[i] for i in _REVIEWED)
        assert all(review.classification == "VUS - class 3" for review in sv_reviews.values())
        # Found by symbol, or by gene id where the symbol differs (row 1).
        assert metrics == {
            small_ids[0]: {"gene_pli": 0.11, "gene_missense_z": None},
            small_ids[1]: {"gene_pli": 0.22, "gene_missense_z": None},
            small_ids[_PAST_THE_LIMIT]: {"gene_pli": 0.97, "gene_missense_z": None},
        }
        assert list(second_hits) == [_gene(_PAST_THE_LIMIT)]
        assert second_hits[_gene(_PAST_THE_LIMIT)]["sv_count"] == 1

    _run(_scenario)


def test_no_ids_read_no_rows() -> None:
    # The array parameter is typed in the SQL, so an empty list is an empty text[]. An
    # expanding IN over no ids rendered IN (SELECT CAST(NULL AS INTEGER) WHERE 1!=1), which
    # Postgres refuses on the text column (text = integer). The review map never sends an
    # empty list (it answers {} first); the lookup under it now takes one as well.
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.small_variant_review_repository import _fetch_review_rows_for_variants

    async def _scenario() -> None:
        await init_postgres_schema()
        session_factory = get_postgres_sessionmaker()
        seeded = await _seed_postgres(session_factory)
        async with session_factory() as session:
            rows = await _fetch_review_rows_for_variants(
                session, family_uuid=seeded["family"], variant_ids=[]
            )
        assert rows == []

    _run(_scenario)


def _small_record(index: int):
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    pos = _FIRST_POS + index
    return SmallVariantRecord(
        variant_key=None,
        variant_id=_small_variant_id(index),
        chr="13",
        start=pos,
        end=pos,
        ref="A",
        alt="G",
        source="test",
        rsid=None,
        filters=["PASS"],
        gene_symbols=[_gene(index)],
        annotations=[],
        calls=[SmallVariantCall(sample=_SAMPLE, gt="0/1", gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)],
        qual=100.0,
    )


def _sv_record(index: int):
    from backend.app.services.clickhouse_variant_records import (
        StructuralVariantCall,
        StructuralVariantRecord,
    )

    start = _FIRST_POS + 10 * index
    return StructuralVariantRecord(
        variant_key=None,
        variant_id=_sv_id(index),
        chr="13",
        start=start,
        end=start + 5,
        sv_type="DEL",
        source="test",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=-5,
        filters=["PASS"],
        # The first SV lies on the gene of the small variant past the limit: its second hit.
        gene_symbols=[_gene(_PAST_THE_LIMIT)] if index == 0 else [],
        annotations=[],
        calls=[StructuralVariantCall(sample=_SAMPLE, gt="0/1", qual=60.0, read_support=12, filter="PASS")],
    )


def _context(family_uuid: str, family_id: str, project: str):
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=family_id,
        project_ids=[project],
        sample_rows=[{"sample_id": _SAMPLE}],
        sample_uuid_to_name={_SAMPLE: _SAMPLE},
        sample_name_to_uuid={_SAMPLE: _SAMPLE},
        affected_sample_names=[_SAMPLE],
        assembly_id=None,
        assembly_name=_ASSEMBLY,
    )


async def _csv_rows(response) -> list[dict[str, str]]:
    chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(chunk if isinstance(chunk, str) else chunk.decode() for chunk in chunks)
    return list(csv.DictReader(io.StringIO(body)))


def _capture(monkeypatch, route, name: str) -> dict:
    """Have ``route`` call its real export service, keeping what the service returned."""
    seen: dict = {}
    real = getattr(route, name)

    async def _export(*args, **kwargs):
        seen["export"] = await real(*args, **kwargs)
        return seen["export"]

    monkeypatch.setattr(route, name, _export)
    return seen


def _assert_cut_at_the_cap(response, stem: str) -> None:
    assert response.headers["x-coga-export-truncated"] == "true"
    assert response.headers["x-coga-export-truncated-reason"] == "row-limit"
    assert response.headers["x-coga-export-rows"] == str(_EXPORT_CAP)
    assert response.headers["x-coga-export-limit"] == str(_EXPORT_CAP)
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{stem}-TRUNCATED-first-{_EXPORT_CAP}.csv"'
    )


def test_a_family_export_past_its_cap_is_cut_at_the_cap_and_says_so(monkeypatch) -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.routers import families_small_variants as small_route
    from backend.app.routers import families_structural_variants as sv_route
    from backend.app.services.clickhouse_variant_storage import (
        delete_family_small_variants,
        delete_family_structural_variants,
        insert_small_variant_records,
        insert_structural_variant_records,
    )

    async def _scenario() -> None:
        await init_postgres_schema()
        session_factory = get_postgres_sessionmaker()
        seeded = await _seed_postgres(session_factory)
        family, label, project = seeded["family"], seeded["label"], str(uuid4())
        context = _context(family, label, project)

        async def _family_context(*_args, **_kwargs):
            return context

        monkeypatch.setattr(small_route, "build_family_metadata_context", _family_context)
        monkeypatch.setattr(sv_route, "build_family_metadata_context", _family_context)
        small_seen = _capture(monkeypatch, small_route, "export_family_small_variants")
        sv_seen = _capture(monkeypatch, sv_route, "export_family_structural_variants")
        try:
            await insert_small_variant_records(
                _ASSEMBLY, family, [project], [_small_record(i) for i in range(_EXPORT_READ)]
            )
            await insert_structural_variant_records(
                _ASSEMBLY, family, [project], [_sv_record(i) for i in range(_EXPORT_READ)]
            )

            async with session_factory() as session:
                small = await small_route.export_family_small_variants_csv(
                    label, filters={}, session=session, user=None
                )
                small_rows = await _csv_rows(small)
            async with session_factory() as session:
                svs = await sv_route.export_family_structural_variants_csv(
                    label,
                    sample_filters=[],
                    samples=[],
                    region_flags=[],
                    classifications=[],
                    review_tags=[],
                    exclude_review_tags=[],
                    session=session,
                    user=None,
                )
                sv_rows = await _csv_rows(svs)
        finally:
            await delete_family_small_variants(_ASSEMBLY, family)
            await delete_family_structural_variants(_ASSEMBLY, family)

        # The small-variant file: the first 50,000 rows, cut at the cap and named so.
        _assert_cut_at_the_cap(small, f"family-{label}-small-variants")
        assert [int(row["Start"]) for row in small_rows] == [_FIRST_POS + i for i in range(_EXPORT_CAP)]
        tagged = [i for i, row in enumerate(small_rows) if row["Tags"] == "report"]
        assert tagged == [0, _PAST_THE_LIMIT]
        assert small_rows[_PAST_THE_LIMIT]["Classification"] == "VUS - class 3"
        # Each row read was looked up: the gene constraint and the SV second hit reach the
        # row past the limit too (a failed second-hit lookup is only logged: no badge).
        rows = small_seen["export"].rows
        assert (rows[0].gene_pli, rows[_PAST_THE_LIMIT].gene_pli) == (0.11, 0.97)
        assert [i for i, row in enumerate(rows) if row.sv_second_hit] == [_PAST_THE_LIMIT]
        assert rows[_PAST_THE_LIMIT].sv_second_hit.gene == _gene(_PAST_THE_LIMIT)

        # The SV file: the same.
        _assert_cut_at_the_cap(svs, f"family-{label}-structural-variants")
        assert [int(row["Start"]) for row in sv_rows] == [_FIRST_POS + 10 * i for i in range(_EXPORT_CAP)]
        assert [i for i, row in enumerate(sv_rows) if row["Tags"] == "report"] == [0, _PAST_THE_LIMIT]
        assert len(sv_seen["export"].rows) == _EXPORT_CAP

    _run(_scenario)
