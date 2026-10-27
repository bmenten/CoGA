"""Integration test: every status a TRGT call can get is stored.

``repeat_expansions.status`` has a check constraint. A male with two different FMR1 alleles
(TRGT run without ``--karyotype XY``, size mosaicism, a recorded sex that does not match)
gets ``review`` (#757), as does a contraction locus outside every stated range (#535); an
FMR1 premutation gets ``premutation``. The constraint allowed neither, so the insert was
refused and the whole repeat dataset with it. These run the real TRGT ingest against
Postgres, on a family of its own, deleted afterwards. Only Postgres is needed. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.

The built-in FMR1 row once called 200 repeats a full mutation (CLIN-2). A database seeded
then gets the corrected row (201) from the next start's seed, and a call imported after it
stores a male's 200-repeat allele as a premutation.

Fifteen built-in rows once started the intermediate or the pathogenic range at another count
than their STRchive row (CLIN-22). The next start gives each built-in row its STRchive row's
thresholds (BEAN1's intermediate threshold becomes NULL); a call imported under an old row
keeps its stored status until it is imported again, while the family table reads it with the
catalogue row's thresholds at once.

A call at a locus without thresholds (a TRID no catalogue row names, or a row that states no
threshold) gets ``not_assessed`` (CLIN-26), which the constraint must store too; a call stored
as ``normal`` there before reads not assessed in the family table at once.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import bindparam, text

pytestmark = pytest.mark.integration

_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##trgtVersion=5.0.0\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
)


def _fmr1_vcf(column: str, sample_field: str, *, trid: str = "FXS_FMR1") -> str:
    return _HEADER.format(column=column) + (
        f"chrX\t147912049\t.\tCGG\t<TR>\t.\t.\tTRID={trid};END=147912111;MOTIFS=CGG;STRUC=<TR>"
        f"\tGT:AL:MC\t{sample_field}\n"
    )


def test_review_and_premutation_calls_are_stored() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.family_metadata_context import SampleMetadataContext
    from backend.app.services.repeat_expansion_pg import ingest_trgt_text, seed_builtin_repeat_catalog

    label = f"itest-repeat-status-{uuid4()}"

    async def _run() -> dict[str, str]:
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        try:
            async with sessionmaker() as session:
                await seed_builtin_repeat_catalog(session)
                family_uuid = (
                    await session.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": label}
                    )
                ).scalar_one()
                contexts = {}
                for name, sex in (("MAN", "male"), ("WOMAN", "female")):
                    sample_id = f"{label}-{name}"
                    sample_uuid = (
                        await session.execute(
                            text(
                                "INSERT INTO samples (sample_id, family_id, sex) "
                                "VALUES (:s, CAST(:f AS uuid), :sex) RETURNING id::text"
                            ),
                            {"s": sample_id, "f": family_uuid, "sex": sex},
                        )
                    ).scalar_one()
                    contexts[name] = SampleMetadataContext(
                        sample_uuid=str(sample_uuid),
                        sample_id=sample_id,
                        family_uuid=str(family_uuid),
                        family_id=label,
                        sex=sex,
                        project_ids=[],
                        assembly_id=None,
                        assembly_name="GRCh38",
                    )
                await session.commit()
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


def test_the_next_start_corrects_an_fmr1_threshold_seeded_before_and_imports_classify_with_it() -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services.family_metadata_context import SampleMetadataContext
    from backend.app.services.repeat_expansion_pg import ingest_trgt_text, seed_builtin_repeat_catalog

    label = f"itest-fmr1-threshold-{uuid4()}"

    async def _run() -> tuple[dict[str, int], dict[str, tuple[str, int]]]:
        await init_postgres_schema()
        sessionmaker = get_postgres_sessionmaker()
        try:
            async with sessionmaker() as session:
                await seed_builtin_repeat_catalog(session)
                # What a start before the fix left: the built-in row's full mutation at 200.
                await session.execute(text("UPDATE repeat_loci SET pathogenic_min = 200 WHERE locus_id = 'FMR1'"))
                await session.commit()
                await seed_builtin_repeat_catalog(session)  # the next start
                thresholds = await session.execute(
                    text("SELECT locus_id, pathogenic_min FROM repeat_loci WHERE locus_id IN ('FMR1', 'FXS_FMR1')")
                )
                catalogue = {str(locus_id): int(pathogenic_min) for locus_id, pathogenic_min in thresholds.all()}

                family_uuid = (
                    await session.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": label}
                    )
                ).scalar_one()
                contexts = {}
                for name in ("MAN200", "MAN201"):
                    sample_id = f"{label}-{name}"
                    sample_uuid = (
                        await session.execute(
                            text(
                                "INSERT INTO samples (sample_id, family_id, sex) "
                                "VALUES (:s, CAST(:f AS uuid), 'male') RETURNING id::text"
                            ),
                            {"s": sample_id, "f": family_uuid},
                        )
                    ).scalar_one()
                    contexts[name] = SampleMetadataContext(
                        sample_uuid=str(sample_uuid),
                        sample_id=sample_id,
                        family_uuid=str(family_uuid),
                        family_id=label,
                        sex="male",
                        project_ids=[],
                        assembly_id=None,
                        assembly_name="GRCh38",
                    )
                await session.commit()
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
                # Leave the catalogue as a start leaves it, also when a step above failed.
                await seed_builtin_repeat_catalog(session)
                await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                await session.commit()
            await close_postgres_engine()

    catalogue, stored = asyncio.run(_run())

    assert catalogue == {"FMR1": 201, "FXS_FMR1": 201}
    assert stored == {"MAN200": ("premutation", 201), "MAN201": ("pathogenic", 201)}


# The built-in rows' thresholds (warning_min, pathogenic_min) before CLIN-22, where they
# differed from their STRchive row's.
_OLD_BUILT_IN_THRESHOLDS = {
    "ATXN1": (39, 45),
    "ATXN2": (32, 34),
    "CACNA1A": (19, 20),
    "ATXN7": (20, 36),
    "TBP": (42, 49),
    "ATXN8OS": (50, 80),
    "CNBP": (55, 75),
    "FXN": (34, 66),
    "C9orf72": (24, 30),
    "ATXN10": (280, 800),
    "BEAN1": (300, 500),
    "PPP2R2B": (44, 51),
    "NOP56": (31, 650),
    "JPH3": (36, 41),
    "PABPN1": (11, 13),
}

# A woman's calls by gene-name TRID: (chrom, pos, motif, repeat count).
_GENE_NAME_CALLS = {
    "ATXN1": ("chr6", 16327633, "CTG", 39),  # intermediate under (39, 45), pathogenic under (36, 39)
    "ATXN2": ("chr12", 111598949, "CTG", 34),  # pathogenic under (32, 34), intermediate under (29, 35)
    "TBP": ("chr6", 170561906, "CAG", 41),  # normal under (42, 49), intermediate under (41, 49)
    "BEAN1": ("chr16", 66490396, "TGGAA", 300),  # intermediate under (300, 500), pathogenic under (None, 110)
}


def _gene_name_vcf(column: str) -> str:
    records = "".join(
        f"{chrom}\t{pos}\t.\t{motif}\t<TR>\t.\t.\tTRID={trid};END={pos + len(motif) * count};MOTIFS={motif};STRUC=<TR>"
        f"\tGT:AL:MC\t1/2:{5 * len(motif)},{count * len(motif)}:5,{count}\n"
        for trid, (chrom, pos, motif, count) in _GENE_NAME_CALLS.items()
    )
    return _HEADER.format(column=column) + records


def test_the_next_start_gives_each_built_in_row_strchives_thresholds_and_the_table_reads_them(monkeypatch) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import repeat_expansion_pg
    from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
    from backend.app.services.repeat_expansion_catalog import BUILTIN_REPEAT_LOCI
    from backend.app.services.repeat_expansion_pg import (
        clear_sample_repeat_expansions,
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
                # What a start before the fix left: the built-in rows' old thresholds.
                for locus_id, (warning_min, pathogenic_min) in _OLD_BUILT_IN_THRESHOLDS.items():
                    await session.execute(
                        text("UPDATE repeat_loci SET warning_min = :w, pathogenic_min = :p WHERE locus_id = :l"),
                        {"w": warning_min, "p": pathogenic_min, "l": locus_id},
                    )
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
                sample_context = SampleMetadataContext(
                    sample_uuid=sample_uuid,
                    sample_id=sample_id,
                    family_uuid=family_uuid,
                    family_id=label,
                    sex="female",
                    project_ids=[],
                    assembly_id=None,
                    assembly_name="GRCh38",
                )
                # Imported under the old rows.
                await ingest_trgt_text(
                    session,
                    sample_context=sample_context,
                    text_value=_gene_name_vcf(sample_id),
                    metadata={"source": "trgt", "filename": "woman.vcf"},
                )

                await seed_builtin_repeat_catalog(session)  # the next start
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
                stored_before = {
                    str(row.locus_id): (str(row.status), row.warning_min, row.pathogenic_min)
                    for row in (await session.execute(stored_query, {"f": family_uuid})).all()
                }
                table = await get_family_repeat_expansion_table_response(
                    session,
                    context=FamilyMetadataContext(
                        family_uuid=family_uuid,
                        family_id=label,
                        project_ids=[],
                        sample_rows=[
                            {"sample_uuid": sample_uuid, "sample_id": sample_id, "role": "proband", "affected": False, "sex": "female"}
                        ],
                        sample_uuid_to_name={sample_uuid: sample_id},
                        sample_name_to_uuid={sample_id: sample_uuid},
                        affected_sample_names=[],
                        assembly_id=None,
                        assembly_name="GRCh38",
                    ),
                )
                read_by_table = {
                    str(row.locus_id): (row.calls[sample_id].status, row.warning_min, row.pathogenic_min)
                    for row in table.loci
                }

                # Imported again, after the next start.
                await clear_sample_repeat_expansions(session, sample_uuid=sample_uuid)
                await ingest_trgt_text(
                    session,
                    sample_context=sample_context,
                    text_value=_gene_name_vcf(sample_id),
                    metadata={"source": "trgt", "filename": "woman.vcf"},
                )
                stored_after = {
                    str(row.locus_id): (str(row.status), row.warning_min, row.pathogenic_min)
                    for row in (await session.execute(stored_query, {"f": family_uuid})).all()
                }
                return catalogue, stored_before, read_by_table, stored_after
        finally:
            async with sessionmaker() as session:
                # Leave the catalogue as a start leaves it, also when a step above failed.
                await seed_builtin_repeat_catalog(session)
                await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                await session.commit()
            await close_postgres_engine()

    catalogue, stored_before, read_by_table, stored_after = asyncio.run(_run())

    # Every built-in row has its STRchive row's thresholds, BEAN1's missing intermediate one included.
    assert sorted(catalogue) == sorted(locus["locus_id"] for locus in BUILTIN_REPEAT_LOCI)
    assert {locus_id: pair for locus_id, pair in catalogue.items() if pair[0] != pair[1]} == {}
    assert catalogue["BEAN1"][0] == (None, 110)
    # Stored at import under the old rows: the genome tracks show these until the calls are
    # imported again.
    assert stored_before == {
        "ATXN1": ("intermediate", 39, 45),
        "ATXN2": ("pathogenic", 32, 34),
        "TBP": ("normal", 42, 49),
        "BEAN1": ("intermediate", 300, 500),
    }
    # The family table reads them with the catalogue row's thresholds at once, as a new import
    # stores them.
    now = {
        "ATXN1": ("pathogenic", 36, 39),
        "ATXN2": ("intermediate", 29, 35),
        "TBP": ("intermediate", 41, 49),
        "BEAN1": ("pathogenic", None, 110),
    }
    assert read_by_table == now
    assert stored_after == now


# A woman's calls (CLIN-26): TRID -> (chrom, pos, motif, repeat count of her second allele).
_NOT_ASSESSED_CALLS = {
    "chr2_1000000_1000030_CAG": ("chr2", 1000000, "CAG", 300),  # no catalogue row names it
    "ADTKD_MUC1": ("chr1", 155188000, "CCCCACCCCACCCCACCCCA", 60),  # a row without thresholds
    "HD_HTT": ("chr4", 3074876, "CAG", 40),  # catalogued: pathogenic from 36, as before
}


def _not_assessed_vcf(column: str) -> str:
    records = "".join(
        f"{chrom}\t{pos}\t.\t{motif}\t<TR>\t.\t.\tTRID={trid};END={pos + len(motif) * count};MOTIFS={motif};STRUC=<TR>"
        f"\tGT:AL:MC\t1/2:{12 * len(motif)},{count * len(motif)}:12,{count}\n"
        for trid, (chrom, pos, motif, count) in _NOT_ASSESSED_CALLS.items()
    )
    return _HEADER.format(column=column) + records


def test_a_call_at_a_locus_without_thresholds_is_stored_not_assessed_and_the_table_reads_older_calls_so(
    monkeypatch,
) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import repeat_expansion_pg
    from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
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

                # What an import before CLIN-26 stored for the uncatalogued call: normal.
                await session.execute(
                    text(
                        "UPDATE repeat_expansions SET status = 'normal', alleles = CAST(:alleles AS jsonb) "
                        "WHERE family_id = CAST(:f AS uuid) AND locus_id = :locus"
                    ),
                    {
                        "alleles": json.dumps(
                            [{"repeat_count": 12, "status": "normal"}, {"repeat_count": 300, "status": "normal"}]
                        ),
                        "f": family_uuid,
                        "locus": uncatalogued,
                    },
                )
                await session.commit()
                table = await get_family_repeat_expansion_table_response(
                    session,
                    context=FamilyMetadataContext(
                        family_uuid=family_uuid,
                        family_id=label,
                        project_ids=[],
                        sample_rows=[
                            {"sample_uuid": sample_uuid, "sample_id": sample_id, "role": "proband", "affected": False, "sex": "female"}
                        ],
                        sample_uuid_to_name={sample_uuid: sample_id},
                        sample_name_to_uuid={sample_id: sample_uuid},
                        affected_sample_names=[],
                        assembly_id=None,
                        assembly_name="GRCh38",
                    ),
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

    # Stored as such: the constraint refused not_assessed before, and with it the whole dataset.
    assert stored == {uncatalogued: "not_assessed", "ADTKD_MUC1": "not_assessed", "HD_HTT": "pathogenic"}
    # The family table reads the call stored as normal before as not assessed at once.
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
    return _HEADER.format(column=column) + records


def test_a_polg_count_inside_its_benign_range_is_stored_normal_and_the_table_reads_older_calls_so(
    monkeypatch,
) -> None:
    from backend.app.core.postgres import close_postgres_engine, get_postgres_sessionmaker, init_postgres_schema
    from backend.app.services import repeat_expansion_pg
    from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
    from backend.app.services.repeat_expansion_pg import (
        clear_sample_repeat_expansions,
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
                family_uuid = str(
                    (
                        await session.execute(
                            text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"), {"f": label}
                        )
                    ).scalar_one()
                )
                contexts: dict[str, SampleMetadataContext] = {}
                for name, sex in (("WOMAN", "female"), ("MAN", "male")):
                    sample_id = f"{label}-{name}"
                    sample_uuid = str(
                        (
                            await session.execute(
                                text(
                                    "INSERT INTO samples (sample_id, family_id, sex) "
                                    "VALUES (:s, CAST(:f AS uuid), :sex) RETURNING id::text"
                                ),
                                {"s": sample_id, "f": family_uuid, "sex": sex},
                            )
                        ).scalar_one()
                    )
                    contexts[name] = SampleMetadataContext(
                        sample_uuid=sample_uuid,
                        sample_id=sample_id,
                        family_uuid=family_uuid,
                        family_id=label,
                        sex=sex,
                        project_ids=[],
                        assembly_id=None,
                        assembly_name="GRCh38",
                    )
                await session.commit()

                async def _import(name: str) -> None:
                    await ingest_trgt_text(
                        session,
                        sample_context=contexts[name],
                        text_value=_benign_range_vcf(contexts[name].sample_id, _BENIGN_RANGE_CALLS[name]),
                        metadata={"source": "trgt", "filename": f"{name}.vcf"},
                    )

                async def _stored() -> dict[tuple[str, str], str]:
                    rows = await session.execute(stored_query, {"f": family_uuid})
                    return {
                        (str(sample_id).rsplit("-", 1)[-1], str(locus_id)): str(status)
                        for sample_id, locus_id, status in rows.all()
                    }

                for name in _BENIGN_RANGE_CALLS:
                    await _import(name)
                stored = await _stored()

                # What an import from CLIN-26 to CLIN-36 stored for the woman's POLG call.
                await session.execute(
                    text(
                        "UPDATE repeat_expansions SET status = 'not_assessed', alleles = CAST(:alleles AS jsonb) "
                        "WHERE sample_id = CAST(:s AS uuid) AND locus_id = 'CPEO_POLG'"
                    ),
                    {
                        "alleles": json.dumps(
                            [{"repeat_count": 10, "status": "not_assessed"}, {"repeat_count": 10, "status": "not_assessed"}]
                        ),
                        "s": contexts["WOMAN"].sample_uuid,
                    },
                )
                await session.commit()
                context = FamilyMetadataContext(
                    family_uuid=family_uuid,
                    family_id=label,
                    project_ids=[],
                    sample_rows=[
                        {
                            "sample_uuid": contexts[name].sample_uuid,
                            "sample_id": contexts[name].sample_id,
                            "role": role,
                            "affected": False,
                            "sex": contexts[name].sex,
                        }
                        for name, role in (("WOMAN", "mother"), ("MAN", "father"))
                    ],
                    sample_uuid_to_name={ctx.sample_uuid: ctx.sample_id for ctx in contexts.values()},
                    sample_name_to_uuid={ctx.sample_id: ctx.sample_uuid for ctx in contexts.values()},
                    affected_sample_names=[],
                    assembly_id=None,
                    assembly_name="GRCh38",
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

                async def _track_status() -> str:
                    track = await get_sample_repeat_expansion_track_response(
                        session, context=context, sample_name=contexts["WOMAN"].sample_id, chromosomes=["15"]
                    )
                    [item] = track.items
                    return item.status

                track_before = await _track_status()
                # Imported again: the stored status follows the rule, and the track with it.
                await clear_sample_repeat_expansions(session, sample_uuid=contexts["WOMAN"].sample_uuid)
                await _import("WOMAN")
                stored_after = await _stored()
                track_after = await _track_status()
                return stored, read_by_table, track_before, stored_after, track_after
        finally:
            async with sessionmaker() as session:
                await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": label})
                await session.commit()
            await close_postgres_engine()

    stored, read_by_table, track_before, stored_after, track_after = asyncio.run(_run())

    expected_stored = {
        ("WOMAN", "CPEO_POLG"): "normal",
        ("WOMAN", "ADTKD_MUC1"): "not_assessed",
        ("MAN", "CPEO_POLG"): "not_assessed",
    }
    assert stored == expected_stored
    # The family table reads the call stored as not assessed before as normal at once; the
    # locus reads its most severe call, the man's allele of 13.
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
    # The genome track reads the call as the table does, at once (CLIN-14): it showed the
    # status stored at import ("not_assessed") until the call was imported again.
    assert (track_before, track_after) == ("normal", "normal")
    assert stored_after == expected_stored
