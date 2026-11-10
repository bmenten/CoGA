"""Integration test: every status a TRGT call can get is stored.

``repeat_expansions.status`` has a check constraint. A male with two different FMR1 alleles
(TRGT run without ``--karyotype XY``, size mosaicism, a recorded sex that does not match)
gets ``review``, as does a contraction locus outside every stated range; an FMR1
premutation gets ``premutation``. A constraint that refused either would refuse the insert,
and the whole repeat dataset with it. These run the real TRGT ingest against
Postgres, on a family of its own, deleted afterwards. Only Postgres is needed. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.

The built-in FMR1 row starts the full mutation at 201 repeats (CLIN-2): a male's 200-repeat
allele is stored as a premutation, one of 201 as a full mutation.

Each built-in row holds its STRchive row's thresholds (CLIN-22), BEAN1's missing intermediate
threshold included; a call is stored, and read by the family table, with them.

A call at a locus without thresholds (a TRID no catalogue row names, or a row that states no
threshold) gets ``not_assessed`` (CLIN-26), which the constraint must store too, and the family
table reads it so. A POLG count inside the benign range STRchive states there is ``normal``
(CLIN-36), on the family table and the genome track alike.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import bindparam, text

from backend.tests.integration._datastores import TRGT_VCF_HEADER, repeat_family_context, trgt_family

pytestmark = pytest.mark.integration


def _fmr1_vcf(column: str, sample_field: str, *, trid: str = "FXS_FMR1") -> str:
    return TRGT_VCF_HEADER.format(column=column) + (
        f"chrX\t147912049\t.\tCGG\t<TR>\t.\t.\tTRID={trid};END=147912111;MOTIFS=CGG;STRUC=<TR>"
        f"\tGT:AL:MC\t{sample_field}\n"
    )


def test_review_and_premutation_calls_are_stored() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.repeat_expansion_pg import ingest_trgt_text, seed_builtin_repeat_catalog

    label = f"itest-repeat-status-{uuid4()}"

    async def _run() -> dict[str, str]:
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        try:
            async with sessionmaker() as session:
                await seed_builtin_repeat_catalog(session)
                family_uuid, contexts = await trgt_family(session, label, {"MAN": "male", "WOMAN": "female"})
                # Two different normal alleles in a man (review), a premutation in a woman.
                calls = {"MAN": "1/2:90,96:30,32", "WOMAN": "1/2:90,180:30,60"}
                for name, sample_field in calls.items():
                    await ingest_trgt_text(
                        session,
                        sample_context=contexts[name],
                        text_value=_fmr1_vcf(contexts[name].sample_id, sample_field),
                        metadata={"source": "trgt", "filename": f"{name}.vcf"},
                    )
                rows = await session.execute(
                    text(
                        "SELECT s.sample_id, r.status FROM repeat_expansions r "
                        "JOIN samples s ON s.id = r.sample_id WHERE r.family_id = CAST(:f AS uuid)"
                    ),
                    {"f": family_uuid},
                )
                return {str(sample_id).rsplit("-", 1)[-1]: str(status) for sample_id, status in rows.all()}
        finally:
            async with sessionmaker() as session:
                await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                await session.commit()
            await close_postgres_engine()

    assert asyncio.run(_run()) == {"MAN": "review", "WOMAN": "premutation"}


def test_the_fmr1_full_mutation_starts_at_201_in_the_catalogue_and_imports_classify_with_it() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.repeat_expansion_pg import ingest_trgt_text, seed_builtin_repeat_catalog

    label = f"itest-fmr1-threshold-{uuid4()}"

    async def _run() -> tuple[dict[str, int], dict[str, tuple[str, int]]]:
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        try:
            async with sessionmaker() as session:
                await seed_builtin_repeat_catalog(session)
                thresholds = await session.execute(
                    text("SELECT locus_id, pathogenic_min FROM repeat_loci WHERE locus_id IN ('FMR1', 'FXS_FMR1')")
                )
                catalogue = {str(locus_id): int(pathogenic_min) for locus_id, pathogenic_min in thresholds.all()}

                family_uuid, contexts = await trgt_family(session, label, {"MAN200": "male", "MAN201": "male"})
                # One chrX allele each, with TRGT's TRID "FMR1": the built-in row classifies it.
                for name, count in (("MAN200", 200), ("MAN201", 201)):
                    await ingest_trgt_text(
                        session,
                        sample_context=contexts[name],
                        text_value=_fmr1_vcf(contexts[name].sample_id, f"1:{3 * count}:{count}", trid="FMR1"),
                        metadata={"source": "trgt", "filename": f"{name}.vcf"},
                    )
                rows = await session.execute(
                    text(
                        "SELECT s.sample_id, r.status, r.pathogenic_min FROM repeat_expansions r "
                        "JOIN samples s ON s.id = r.sample_id WHERE r.family_id = CAST(:f AS uuid)"
                    ),
                    {"f": family_uuid},
                )
                stored = {
                    str(sample_id).rsplit("-", 1)[-1]: (str(status), int(pathogenic_min))
                    for sample_id, status, pathogenic_min in rows.all()
                }
                return catalogue, stored
        finally:
            async with sessionmaker() as session:
                await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                await session.commit()
            await close_postgres_engine()

    catalogue, stored = asyncio.run(_run())

    assert catalogue == {"FMR1": 201, "FXS_FMR1": 201}
    assert stored == {"MAN200": ("premutation", 201), "MAN201": ("pathogenic", 201)}


# A woman's calls by gene-name TRID: (chrom, pos, motif, repeat count), each at or next to one
# of STRchive's thresholds (warning_min, pathogenic_min).
_GENE_NAME_CALLS = {
    "ATXN1": ("chr6", 16327633, "CTG", 39),  # (36, 39): pathogenic
    "ATXN2": ("chr12", 111598949, "CTG", 34),  # (29, 35): intermediate
    "TBP": ("chr6", 170561906, "CAG", 41),  # (41, 49): intermediate
    "BEAN1": ("chr16", 66490396, "TGGAA", 300),  # (None, 110): pathogenic
}


def _gene_name_vcf(column: str) -> str:
    records = "".join(
        f"{chrom}\t{pos}\t.\t{motif}\t<TR>\t.\t.\tTRID={trid};END={pos + len(motif) * count};MOTIFS={motif};STRUC=<TR>"
        f"\tGT:AL:MC\t1/2:{5 * len(motif)},{count * len(motif)}:5,{count}\n"
        for trid, (chrom, pos, motif, count) in _GENE_NAME_CALLS.items()
    )
    return TRGT_VCF_HEADER.format(column=column) + records


def test_each_built_in_row_holds_strchives_thresholds_and_a_call_is_stored_and_read_with_them(monkeypatch) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import repeat_expansion_pg
    from backend.app.services.repeat_expansion_catalog import BUILTIN_REPEAT_LOCI
    from backend.app.services.repeat_expansion_pg import (
        get_family_repeat_expansion_table_response,
        ingest_trgt_text,
        seed_builtin_repeat_catalog,
    )

    # The shipped STRchive file, not one an environment variable may point at.
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    label = f"itest-repeat-thresholds-{uuid4()}"
    stored_query = text(
        "SELECT locus_id, status, warning_min, pathogenic_min FROM repeat_expansions "
        "WHERE family_id = CAST(:f AS uuid)"
    )

    async def _run():
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        try:
            async with sessionmaker() as session:
                await seed_builtin_repeat_catalog(session)
                family_uuid, contexts = await trgt_family(session, label, {"WOMAN": "female"})
                sample_context = contexts["WOMAN"]
                sample_id = sample_context.sample_id
                await ingest_trgt_text(
                    session,
                    sample_context=sample_context,
                    text_value=_gene_name_vcf(sample_id),
                    metadata={"source": "trgt", "filename": "woman.vcf"},
                )

                pairs = await session.execute(
                    text(
                        "SELECT b.locus_id, b.warning_min, b.pathogenic_min, s.warning_min, s.pathogenic_min "
                        "FROM repeat_loci b JOIN repeat_loci s "
                        "ON lower(s.gene) = lower(b.gene) AND s.metadata ->> 'source' = 'STRchive' "
                        "WHERE b.locus_id IN :builtin"
                    ).bindparams(bindparam("builtin", expanding=True)),
                    {"builtin": [locus["locus_id"] for locus in BUILTIN_REPEAT_LOCI]},
                )
                catalogue = {
                    str(locus_id): ((builtin_w, builtin_p), (strchive_w, strchive_p))
                    for locus_id, builtin_w, builtin_p, strchive_w, strchive_p in pairs.all()
                }
                stored = {
                    str(row.locus_id): (str(row.status), row.warning_min, row.pathogenic_min)
                    for row in (await session.execute(stored_query, {"f": family_uuid})).all()
                }
                table = await get_family_repeat_expansion_table_response(
                    session, context=repeat_family_context(family_uuid, label, [(sample_context, "proband", False)])
                )
                read_by_table = {
                    str(row.locus_id): (row.calls[sample_id].status, row.warning_min, row.pathogenic_min)
                    for row in table.loci
                }
                return catalogue, stored, read_by_table
        finally:
            async with sessionmaker() as session:
                await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                await session.commit()
            await close_postgres_engine()

    catalogue, stored, read_by_table = asyncio.run(_run())

    # Every built-in row has its STRchive row's thresholds, BEAN1's missing intermediate one included.
    assert sorted(catalogue) == sorted(locus["locus_id"] for locus in BUILTIN_REPEAT_LOCI)
    assert {locus_id: pair for locus_id, pair in catalogue.items() if pair[0] != pair[1]} == {}
    assert catalogue["BEAN1"][0] == (None, 110)
    # Stored with STRchive's thresholds, and read by the family table as stored.
    expected = {
        "ATXN1": ("pathogenic", 36, 39),
        "ATXN2": ("intermediate", 29, 35),
        "TBP": ("intermediate", 41, 49),
        "BEAN1": ("pathogenic", None, 110),
    }
    assert stored == expected
    assert read_by_table == expected


# A woman's calls (CLIN-26): TRID -> (chrom, pos, motif, repeat count of her second allele).
_NOT_ASSESSED_CALLS = {
    "chr2_1000000_1000030_CAG": ("chr2", 1000000, "CAG", 300),  # no catalogue row names it
    "ADTKD_MUC1": ("chr1", 155188000, "CCCCACCCCACCCCACCCCA", 60),  # a row without thresholds
    "HD_HTT": ("chr4", 3074876, "CAG", 40),  # catalogued: pathogenic from 36
}


def _not_assessed_vcf(column: str) -> str:
    records = "".join(
        f"{chrom}\t{pos}\t.\t{motif}\t<TR>\t.\t.\tTRID={trid};END={pos + len(motif) * count};MOTIFS={motif};STRUC=<TR>"
        f"\tGT:AL:MC\t1/2:{12 * len(motif)},{count * len(motif)}:12,{count}\n"
        for trid, (chrom, pos, motif, count) in _NOT_ASSESSED_CALLS.items()
    )
    return TRGT_VCF_HEADER.format(column=column) + records


def test_a_call_at_a_locus_without_thresholds_is_stored_not_assessed_and_the_table_reads_it_so(
    monkeypatch,
) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import repeat_expansion_pg
    from backend.app.services.repeat_expansion_pg import (
        get_family_repeat_expansion_table_response,
        ingest_trgt_text,
        seed_builtin_repeat_catalog,
    )

    # The shipped STRchive file, not one an environment variable may point at.
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    label = f"itest-repeat-not-assessed-{uuid4()}"
    uncatalogued = "chr2_1000000_1000030_CAG"

    async def _run():
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        try:
            async with sessionmaker() as session:
                await seed_builtin_repeat_catalog(session)
                family_uuid, contexts = await trgt_family(session, label, {"WOMAN": "female"})
                sample_id = contexts["WOMAN"].sample_id
                await ingest_trgt_text(
                    session,
                    sample_context=contexts["WOMAN"],
                    text_value=_not_assessed_vcf(sample_id),
                    metadata={"source": "trgt", "filename": "woman.vcf"},
                )
                stored = {
                    str(locus_id): str(status)
                    for locus_id, status in (
                        await session.execute(
                            text("SELECT locus_id, status FROM repeat_expansions WHERE family_id = CAST(:f AS uuid)"),
                            {"f": family_uuid},
                        )
                    ).all()
                }
                table = await get_family_repeat_expansion_table_response(
                    session, context=repeat_family_context(family_uuid, label, [(contexts["WOMAN"], "proband", False)])
                )
                read_by_table = {
                    str(row.locus_id): (
                        row.status,
                        row.calls[sample_id].status,
                        [allele.status for allele in row.calls[sample_id].alleles],
                        row.warning_min,
                        row.pathogenic_min,
                    )
                    for row in table.loci
                }
                return stored, read_by_table
        finally:
            async with sessionmaker() as session:
                await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                await session.commit()
            await close_postgres_engine()

    stored, read_by_table = asyncio.run(_run())

    # Stored as such: the constraint allows not_assessed, so the dataset is not refused.
    assert stored == {uncatalogued: "not_assessed", "ADTKD_MUC1": "not_assessed", "HD_HTT": "pathogenic"}
    # The family table reads each call so, every allele of the two loci without thresholds too.
    assert read_by_table == {
        uncatalogued: ("not_assessed", "not_assessed", ["not_assessed", "not_assessed"], None, None),
        "ADTKD_MUC1": ("not_assessed", "not_assessed", ["not_assessed", "not_assessed"], None, None),
        "HD_HTT": ("pathogenic", "pathogenic", ["normal", "pathogenic"], 27, 36),
    }


# CLIN-36: a count inside the benign range of a row without thresholds is normal (STRchive's
# CPEO_POLG states exactly 10); any other count there, and every count at ADTKD_MUC1, which
# states nothing, stays not assessed. Each member's calls: TRID -> (chrom, pos, motif, counts).
_BENIGN_RANGE_CALLS = {
    "WOMAN": {
        "CPEO_POLG": ("chr15", 89333579, "GCT", (10, 10)),
        "ADTKD_MUC1": ("chr1", 155188505, "GCC", (10, 60)),
    },
    "MAN": {"CPEO_POLG": ("chr15", 89333579, "GCT", (10, 13))},
}


def _benign_range_vcf(column: str, calls: dict[str, tuple[str, int, str, tuple[int, int]]]) -> str:
    records = "".join(
        f"{chrom}\t{pos}\t.\t{motif}\t<TR>\t.\t.\tTRID={trid};END={pos + 50};MOTIFS={motif};STRUC=<TR>"
        f"\tGT:AL:MC\t0/1:{first * len(motif)},{second * len(motif)}:{first},{second}\n"
        for trid, (chrom, pos, motif, (first, second)) in calls.items()
    )
    return TRGT_VCF_HEADER.format(column=column) + records


def test_a_polg_count_inside_its_benign_range_is_stored_normal_and_the_table_and_track_read_it_so(
    monkeypatch,
) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import repeat_expansion_pg
    from backend.app.services.repeat_expansion_pg import (
        get_family_repeat_expansion_table_response,
        get_sample_repeat_expansion_track_response,
        ingest_trgt_text,
        seed_builtin_repeat_catalog,
    )

    # The shipped STRchive file, not one an environment variable may point at.
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    label = f"itest-repeat-benign-range-{uuid4()}"
    stored_query = text(
        "SELECT s.sample_id, r.locus_id, r.status FROM repeat_expansions r "
        "JOIN samples s ON s.id = r.sample_id WHERE r.family_id = CAST(:f AS uuid)"
    )

    async def _run():
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        try:
            async with sessionmaker() as session:
                await seed_builtin_repeat_catalog(session)
                family_uuid, contexts = await trgt_family(session, label, {"WOMAN": "female", "MAN": "male"})

                for name, calls in _BENIGN_RANGE_CALLS.items():
                    await ingest_trgt_text(
                        session,
                        sample_context=contexts[name],
                        text_value=_benign_range_vcf(contexts[name].sample_id, calls),
                        metadata={"source": "trgt", "filename": f"{name}.vcf"},
                    )
                rows = await session.execute(stored_query, {"f": family_uuid})
                stored = {
                    (str(sample_id).rsplit("-", 1)[-1], str(locus_id)): str(status)
                    for sample_id, locus_id, status in rows.all()
                }
                context = repeat_family_context(
                    family_uuid, label, [(contexts["WOMAN"], "mother", False), (contexts["MAN"], "father", False)]
                )
                table = await get_family_repeat_expansion_table_response(session, context=context)
                read_by_table = {
                    str(row.locus_id): (
                        row.status,
                        (row.benign_min, row.benign_max, row.warning_min, row.pathogenic_min),
                        {
                            sample.rsplit("-", 1)[-1]: (call.status, [allele.status for allele in call.alleles])
                            for sample, call in row.calls.items()
                        },
                    )
                    for row in table.loci
                }

                track = await get_sample_repeat_expansion_track_response(
                    session, context=context, sample_name=contexts["WOMAN"].sample_id, chromosomes=["15"]
                )
                [item] = track.items
                return stored, read_by_table, item.status
        finally:
            async with sessionmaker() as session:
                await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                await session.commit()
            await close_postgres_engine()

    stored, read_by_table, track_status = asyncio.run(_run())

    assert stored == {
        ("WOMAN", "CPEO_POLG"): "normal",
        ("WOMAN", "ADTKD_MUC1"): "not_assessed",
        ("MAN", "CPEO_POLG"): "not_assessed",
    }
    # The family table reads each call so; the locus reads its most severe call, the man's
    # allele of 13.
    assert read_by_table == {
        "CPEO_POLG": (
            "not_assessed",
            (10, 10, None, None),
            {
                "WOMAN": ("normal", ["normal", "normal"]),
                "MAN": ("not_assessed", ["normal", "not_assessed"]),
            },
        ),
        "ADTKD_MUC1": (
            "not_assessed",
            (None, None, None, None),
            {"WOMAN": ("not_assessed", ["not_assessed", "not_assessed"])},
        ),
    }
    # The genome track reads the call as the table does (CLIN-14).
    assert track_status == "normal"
