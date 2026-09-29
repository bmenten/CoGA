"""Auto-seeding the NIPT artifact list counts only the assay's own cfDNA samples and skips
common variants — real Postgres + ClickHouse.

The seed used to list every variant carried by five samples anywhere on the assembly: every
common SNP, so the paternal sites the fetal fraction is read from, and every other assay's
recurrent artifacts, which the NIPT analysis then excluded. This seeds a fixture with one
variant of each kind and checks that each assay lists only its own recurrent, non-common
variant, counting a sample once whether ClickHouse stores its calls under its name or its
UUID.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_auto_seed_lists_only_the_assays_recurrent_non_common_variants() -> None:
    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
    from backend.app.services.clickhouse_variant_storage import insert_small_variant_records
    from backend.app.services.nipt_artifact_pg import auto_seed_nipt_artifacts, list_nipt_artifacts

    tag = uuid4().hex[:8].upper()
    # A fresh ClickHouse dataset of its own, so no other test's calls are counted.
    assembly_name = f"NIPTSEED{tag}"

    def name(label: str) -> str:
        return f"{label}-{tag}"

    default_cfdna = [name(f"CF{i}") for i in range(1, 6)]
    panel_cfdna = [name(f"PX{i}") for i in range(1, 6)]
    wgs = [name(f"W{i}") for i in range(1, 6)]

    def record(variant_id: str, carriers: list[str], *, ref_calls: list[str] | None = None, common: bool = False):
        chrom, pos, ref, alt = variant_id.split("-")
        return SmallVariantRecord(
            variant_key=None,
            variant_id=variant_id,
            chr=chrom,
            start=int(pos),
            end=int(pos),
            ref=ref,
            alt=alt,
            source="test",
            rsid=None,
            filters=["PASS"],
            gene_symbols=[],
            annotations=[{"gnomad_af": 0.31}] if common else [],
            calls=[
                SmallVariantCall(sample=s, gt="0/1", gq=99.0, dp=400, af=[0.05], ad=[380, 20], ps=None)
                for s in carriers
            ]
            + [
                SmallVariantCall(sample=s, gt="0/0", gq=99.0, dp=400, af=[0.0], ad=[400, 0], ps=None)
                for s in ref_calls or []
            ],
            qual=60.0,
        )

    async def _seed_postgres(session) -> tuple[str, dict[str, str]]:
        species = (
            await session.execute(
                text(
                    "INSERT INTO species (name, common_name, tax_id) "
                    "VALUES (:n, 'test', :t) RETURNING id::text"
                ),
                {"n": f"nipt-seed {tag} {uuid4()}", "t": uuid4().int % 2_000_000_000},
            )
        ).scalar_one()
        assembly_id = (
            await session.execute(
                text(
                    "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                    "VALUES (CAST(:s AS uuid), :a, 'v1', '2020-01-01') RETURNING id::text"
                ),
                {"s": species, "a": assembly_name},
            )
        ).scalar_one()
        sample_uuids: dict[str, str] = {}
        groups = [
            (default_cfdna, {"assay": "nipt_cfdna"}),
            (panel_cfdna, {"assay": "nipt_cfdna", "assay_panel": "PANEL_X"}),
            (wgs, {}),
        ]
        for index, (samples, metadata) in enumerate(groups):
            family = (
                await session.execute(
                    text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                    {"f": name(f"FAM{index}")},
                )
            ).scalar_one()
            for sample in samples:
                sample_uuids[sample] = (
                    await session.execute(
                        text(
                            "INSERT INTO samples (sample_id, family_id, sex, metadata) "
                            "VALUES (:s, CAST(:f AS uuid), 'female', CAST(:m AS jsonb)) "
                            "RETURNING id::text"
                        ),
                        {"s": sample, "f": family, "m": json.dumps(metadata)},
                    )
                ).scalar_one()
        await session.commit()
        return assembly_id, sample_uuids

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as session:
                assembly_id, uuids = await _seed_postgres(session)

            cf1, cf2, cf3, cf4, cf5 = default_cfdna
            # The last default-scope sample's calls are stored under its UUID.
            cf5_stored = uuids[cf5]
            await insert_small_variant_records(
                assembly_name,
                str(uuid4()),
                [str(uuid4())],
                [
                    # Recurrent in the assay's cfDNA and not common: the one artifact.
                    record("1-100-A-G", [cf1, cf2, cf3, cf4, cf5_stored]),
                    # As recurrent, but a common SNP (the kind FF is read from).
                    record("1-200-A-G", [cf1, cf2, cf3, cf4, cf5_stored], common=True),
                    # One carrier in this assay; recurrent only in other samples (below).
                    record("1-300-A-G", [cf1]),
                    # Four carriers: below the threshold.
                    record("1-400-A-G", [cf1, cf2, cf3, cf4], ref_calls=[cf5_stored]),
                    # Four carriers, plus the same first sample again under its UUID (below).
                    record("1-500-A-G", [cf1, cf2, cf3, cf4]),
                ],
            )
            await insert_small_variant_records(
                assembly_name,
                str(uuid4()),
                [str(uuid4())],
                [
                    record("1-300-A-G", panel_cfdna + wgs),
                    record("1-500-A-G", [uuids[cf1]]),
                ],
            )

            async with sm() as session:
                default = await auto_seed_nipt_artifacts(
                    session, assembly_id=assembly_id, assay_key="nipt_cfdna", min_carrier_samples=5
                )
                panel = await auto_seed_nipt_artifacts(
                    session, assembly_id=assembly_id, assay_key="PANEL_X", min_carrier_samples=5
                )
                default_rows = await list_nipt_artifacts(
                    session, assembly_id=assembly_id, assay_key="nipt_cfdna"
                )
                panel_rows = await list_nipt_artifacts(
                    session, assembly_id=assembly_id, assay_key="PANEL_X"
                )

            assert default == {"seeded": 1, "min_carrier_samples": 5}
            assert [(r["variant_id"], r["recurrence_count"], r["source"]) for r in default_rows] == [
                ("1-100-A-G", 5, "auto")
            ]
            # The other panel's own recurrence is its artifact; the WGS carriers never count.
            assert panel == {"seeded": 1, "min_carrier_samples": 5}
            assert [(r["variant_id"], r["recurrence_count"]) for r in panel_rows] == [("1-300-A-G", 5)]
        finally:
            await close_postgres_engine()
            await close_clickhouse_client()

    asyncio.run(_run())
