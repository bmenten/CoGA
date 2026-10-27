"""Integration test: the repeat catalogue is the shipped STRchive release, and a gene with
several STRchive loci is read against the tract each call lies on (CLIN-31).

These run the real seed, TRGT ingest and family-table query against Postgres, on a family of
their own, deleted afterwards. Only Postgres is needed. Skipped unless ``RUN_INTEGRATION=1``
(see conftest.py); the CI ``smoke`` job sets it.

- A STRchive row the shipped release does not list, as a start on another release left it, is
  gone after the next start; the built-in rows and the release's 75 loci stay.
- TRID ARX at PRTS_ARX's region is stored and read against PRTS_ARX's thresholds (17 GCN
  repeats normal, pathogenic from 20), at EIEE1_ARX's region against EIEE1_ARX's (17
  pathogenic). All three calls read against EIEE1_ARX's before, the first of the gene's loci by
  locus ID. A call whose region overlaps neither is stored not assessed, with the reason, and
  the family table, whose query matched it to EIEE1_ARX by the gene's name, reads it not
  assessed with the reason.
All samples and values are synthetic.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##trgtVersion=5.0.0\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
)

# TRID ARX: (region start, end) on GRCh38. PRTS_ARX and EIEE1_ARX as STRchive places them, and
# a region between ARX and the next gene that overlaps neither.
_ARX_CALLS = {
    "PRTS_ARX": (25_013_529, 25_013_565),
    "EIEE1_ARX": (25_013_649, 25_013_697),
    "ARX": (25_020_000, 25_020_060),
}


def _arx_vcf(column: str) -> str:
    # A woman with a normal allele of 8 GCN repeats and one of 17.
    records = "".join(
        f"chrX\t{start}\t.\tGCG\t<TR>\t.\t.\tTRID=ARX;END={end};MOTIFS=GCN;STRUC=<TR>\tGT:AL:MC\t1/2:24,51:8,17\n"
        for start, end in _ARX_CALLS.values()
    )
    return _HEADER.format(column=column) + records


def test_the_next_start_removes_strchive_rows_the_shipped_release_does_not_list(monkeypatch) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import repeat_expansion_pg
    from backend.app.services.repeat_expansion_catalog import BUILTIN_REPEAT_LOCI
    from backend.app.services.repeat_expansion_pg import seed_builtin_repeat_catalog

    # The shipped STRchive file, not one an environment variable may point at.
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    other_release_locus = f"ITEST_OTHER_RELEASE_{uuid4().hex[:8].upper()}"
    catalogue_query = text("SELECT locus_id, metadata ->> 'source' AS source FROM repeat_loci")

    async def _catalogue(session) -> dict[str, set[str]]:
        rows = (await session.execute(catalogue_query)).all()
        return {
            "strchive": {str(row.locus_id) for row in rows if row.source == "STRchive"},
            "other": {str(row.locus_id) for row in rows if row.source != "STRchive"},
        }

    async def _run():
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        try:
            async with sessionmaker() as session:
                await seed_builtin_repeat_catalog(session)
                # What a start on another release left: a locus the shipped release lacks.
                await session.execute(
                    text(
                        "INSERT INTO repeat_loci (locus_id, gene, display_name, disease, pathogenic_min, metadata) "
                        "VALUES (:l, 'ARX', 'ARX', 'Another release', 15, CAST(:m AS jsonb))"
                    ),
                    {"l": other_release_locus, "m": json.dumps({"source": "STRchive", "seeded": True})},
                )
                await session.commit()
                before = await _catalogue(session)
                await seed_builtin_repeat_catalog(session)  # the next start
                after = await _catalogue(session)
                return before, after
        finally:
            async with sessionmaker() as session:
                await session.execute(text("DELETE FROM repeat_loci WHERE locus_id = :l"), {"l": other_release_locus})
                await session.commit()
            await close_postgres_engine()

    before, after = asyncio.run(_run())

    assert other_release_locus in before["strchive"]
    assert other_release_locus not in after["strchive"]
    assert len(after["strchive"]) == 75
    assert after["strchive"] == before["strchive"] - {other_release_locus}
    assert {locus["locus_id"] for locus in BUILTIN_REPEAT_LOCI} <= after["other"]


def test_a_gene_name_trid_is_stored_and_read_against_the_tract_its_call_lies_on(monkeypatch) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import repeat_expansion_pg
    from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
    from backend.app.services.repeat_expansion_pg import (
        get_family_repeat_expansion_table_response,
        get_sample_repeat_expansion_track_response,
        ingest_trgt_text,
        seed_builtin_repeat_catalog,
    )

    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    label = f"itest-repeat-multi-locus-{uuid4()}"
    stored_query = text(
        "SELECT start, locus_id, status, pathogenic_min, metadata ->> 'locus_not_resolved' AS reason "
        "FROM repeat_expansions WHERE family_id = CAST(:f AS uuid)"
    )

    async def _run():
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        try:
            async with sessionmaker() as session:
                await seed_builtin_repeat_catalog(session)
                family_uuid = str(
                    (
                        await session.execute(
                            text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": label}
                        )
                    ).scalar_one()
                )
                sample_id = f"{label}-WOMAN"
                sample_uuid = str(
                    (
                        await session.execute(
                            text(
                                "INSERT INTO samples (sample_id, family_id, sex) "
                                "VALUES (:s, CAST(:f AS uuid), 'female') RETURNING id::text"
                            ),
                            {"s": sample_id, "f": family_uuid},
                        )
                    ).scalar_one()
                )
                await session.commit()
                await ingest_trgt_text(
                    session,
                    sample_context=SampleMetadataContext(
                        sample_uuid=sample_uuid,
                        sample_id=sample_id,
                        family_uuid=family_uuid,
                        family_id=label,
                        sex="female",
                        project_ids=[],
                        assembly_id=None,
                        assembly_name="GRCh38",
                    ),
                    text_value=_arx_vcf(sample_id),
                    metadata={"source": "trgt", "filename": "woman.vcf"},
                )
                stored = {
                    int(row.start): (str(row.locus_id), str(row.status), row.pathogenic_min, row.reason)
                    for row in (await session.execute(stored_query, {"f": family_uuid})).all()
                }
                context = FamilyMetadataContext(
                    family_uuid=family_uuid,
                    family_id=label,
                    project_ids=[],
                    sample_rows=[
                        {"sample_uuid": sample_uuid, "sample_id": sample_id, "role": "proband", "affected": True, "sex": "female"}
                    ],
                    sample_uuid_to_name={sample_uuid: sample_id},
                    sample_name_to_uuid={sample_id: sample_uuid},
                    affected_sample_names=[sample_id],
                    assembly_id=None,
                    assembly_name="GRCh38",
                )
                table = await get_family_repeat_expansion_table_response(session, context=context)
                read_by_table = {
                    row.start: (row.locus_id, row.calls[sample_id].status, row.pathogenic_min, row.calls[sample_id].note)
                    for row in table.loci
                }
                # The genome track, through the same catalogue fragment on Postgres (CLIN-14).
                track = await get_sample_repeat_expansion_track_response(
                    session, context=context, sample_name=sample_id, chromosomes=["X"]
                )
                read_by_track = {
                    item.start: (item.locus_id, item.status, item.pathogenic_min) for item in track.items
                }
                return stored, read_by_table, read_by_track
        finally:
            async with sessionmaker() as session:
                await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                await session.commit()
            await close_postgres_engine()

    stored, read_by_table, read_by_track = asyncio.run(_run())

    reason = (
        "TRID ARX names 2 STRchive loci (EIEE1_ARX, PRTS_ARX), and the call's region, "
        "chrX:25020000-25020060, overlaps none of them, so none of their thresholds applies."
    )
    prts, eiee1, between = (start for start, _end in _ARX_CALLS.values())
    # Each call against its own tract: 17 repeats are the grey zone at PRTS_ARX (benign 12,
    # pathogenic from 20, no grey zone stated: CLIN-14), pathogenic at EIEE1_ARX (from 17).
    # Between the tracts: no thresholds, not assessed.
    assert stored == {
        prts: ("PRTS_ARX", "intermediate", 20, None),
        eiee1: ("EIEE1_ARX", "pathogenic", 17, None),
        between: ("ARX", "not_assessed", None, reason),
    }
    # The table's query no longer matches the call between the tracts to EIEE1_ARX by the
    # gene's name (pathogenic at 17 there); it shows why the call is not assessed.
    assert read_by_table == {
        prts: ("PRTS_ARX", "intermediate", 20, None),
        eiee1: ("EIEE1_ARX", "pathogenic", 17, None),
        between: ("ARX", "not_assessed", None, reason),
    }
    # The track reads each call as the table does.
    assert read_by_track == {start: (locus, status, threshold) for start, (locus, status, threshold, _note) in read_by_table.items()}
