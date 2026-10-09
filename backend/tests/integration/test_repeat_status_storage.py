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
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

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
