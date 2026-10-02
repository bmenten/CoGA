"""The sample-integrity genotype sample against real ClickHouse.

Sample-integrity QC reads its genotypes from ``fetch_genotype_site_sample``: the family's
sites with the smallest storage keys, over every autosome, or over chrX outside the
pseudo-autosomal regions. This writes a synthetic family with sites on every chromosome,
on chrX inside and outside both PARs, on chrY and on an unplaced contig, with a second
callset and a second family beside it, and checks on the real server that the sample is
exactly the smallest keys of the family's sites in scope, spreads over every autosome,
repeats identically, and counts a variant stored under two projects once. It then runs
the QC service on those rows: the two embryos, identical over the first quarter of every
chromosome, read as siblings.

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``smoke`` job sets it.
"""

from __future__ import annotations

import random
import types
from uuid import uuid4

import pytest

from backend.app.services.clickhouse_variant_ids import small_variant_key
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.family_metadata_context import FamilyMetadataContext

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_SAMPLES = ["FATHER", "MOTHER", "E1", "E2"]
_SITES_PER_AUTOSOME = 500


def _run_with_ch_cleanup(run) -> None:
    """Run a coroutine factory under its own loop, resetting the module-global ClickHouse
    client afterwards — otherwise the next test's asyncio.run() reuses a client bound to
    this (now-closed) loop and raises 'Event loop is closed'."""
    import asyncio as _asyncio

    from backend.app.core.clickhouse import close_clickhouse_client

    async def _wrapped() -> None:
        try:
            await run()
        finally:
            await close_clickhouse_client()

    _asyncio.run(_wrapped())


def _record(chrom: str, pos: int, samples: list[str], gts: list[str], *, source: str) -> SmallVariantRecord:
    calls = [
        SmallVariantCall(sample=sample, gt=gt, gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)
        for sample, gt in zip(samples, gts)
    ]
    return SmallVariantRecord(
        variant_key=None,
        variant_id=f"{chrom}-{pos}-A-G",
        chr=chrom,
        start=pos,
        end=pos,
        ref="A",
        alt="G",
        source=source,
        rsid=None,
        filters=["PASS"],
        gene_symbols=[],
        annotations=[],
        calls=calls,
        qual=100.0,
    )


def _sibling_records() -> list[SmallVariantRecord]:
    """The family's GLIMPSE2 autosomes: E1 and E2 identical (IBD2) over the first quarter of
    each chromosome, sharing one haplotype (IBD1) over the next half and none (IBD0) over
    the last quarter; every genotype a consistent transmission from the parents."""
    rng = random.Random(41)
    records = []
    for number in range(1, 23):
        n = _SITES_PER_AUTOSOME
        for i in range(n):
            bits = rng.getrandbits(4)
            f = (bits & 1, bits >> 1 & 1)
            m = (bits >> 2 & 1, bits >> 3 & 1)
            e1 = (f[0], m[0])
            e2 = e1 if i < n // 4 else (f[0], m[1]) if i < 3 * n // 4 else (f[1], m[1])
            gts = [f"{a}|{b}" for a, b in (f, m, e1, e2)]
            records.append(_record(f"chr{number}", 1_000_000 + 10_000 * i, _SAMPLES, gts, source="glimpse2"))
    return records


def _x_records() -> list[SmallVariantRecord]:
    """chrX: FATHER and E2 are male, MOTHER and E1 female. 240 sites outside the PARs (two of
    them under the name 23), and sites in PAR1 and PAR2 where the males are heterozygous."""
    rng = random.Random(43)
    records = []
    for i in range(240):
        fx = rng.getrandbits(1)
        m = (rng.getrandbits(1), rng.getrandbits(1))
        gts = [f"{fx}|{fx}", f"{m[0]}|{m[1]}", f"{fx}|{m[0]}", f"{m[1]}|{m[1]}"]
        chrom = "23" if i < 2 else "chrX"
        records.append(_record(chrom, 3_000_000 + 500_000 * i, _SAMPLES, gts, source="glimpse2"))
    par_het = ["0|1", "0|1", "0|1", "0|1"]
    for pos in (10_001, 1_500_000, 2_781_479, 155_701_383, 155_900_000, 156_030_895):
        records.append(_record("chrX", pos, _SAMPLES, par_het, source="glimpse2"))
    records.append(_record("23", 2_000_000, _SAMPLES, par_het, source="glimpse2"))
    return records


def _decoy_records() -> list[SmallVariantRecord]:
    """Sites the sample must never read: chrY, an unplaced contig, another callset."""
    het = ["0|1", "0|1", "0|1", "0|1"]
    return [
        _record("chrY", 7_000_000, _SAMPLES, het, source="glimpse2"),
        _record("chrY", 15_000_000, _SAMPLES, het, source="glimpse2"),
        _record("chr1_KI270706v1_random", 1_000, _SAMPLES, het, source="glimpse2"),
        *[_record("chr1", 200_000_000 + 1_000 * i, _SAMPLES, het, source="deepvariant") for i in range(50)],
    ]


def _context(family_uuid: str, project_ids: list[str], samples: list[str]) -> FamilyMetadataContext:
    roles = {"FATHER": ("father", "male"), "MOTHER": ("mother", "female"), "E1": ("embryo", "female"),
             "E2": ("embryo", "male")}
    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=f"FAM-{family_uuid[:8]}",
        project_ids=project_ids,
        sample_rows=[
            {"sample_id": s, "role": roles.get(s, ("proband", "female"))[0], "sex": roles.get(s, ("proband", "female"))[1]}
            for s in samples
        ],
        sample_uuid_to_name={s: s for s in samples},
        sample_name_to_uuid={s: s for s in samples},
        affected_sample_names=[],
        assembly_id=str(uuid4()),
        assembly_name=_ASSEMBLY,
        relationship_rows=[
            {"relationship_type": "parent_child", "sample_id_a": parent, "sample_id_b": embryo, "role_a": role}
            for embryo in ("E1", "E2")
            if embryo in samples
            for parent, role in (("FATHER", "father"), ("MOTHER", "mother"))
        ],
    )


def _key(record: SmallVariantRecord) -> int:
    return small_variant_key(_ASSEMBLY, record.variant_id)


def test_genotype_site_sample_against_clickhouse(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.services import sample_integrity_service
    from backend.app.services.clickhouse_family_variants import fetch_genotype_site_sample
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    autosomal = _sibling_records()
    x_records = _x_records()

    async def _run() -> None:
        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        family = str(uuid4())
        project = str(uuid4())
        ctx = _context(family, [project], _SAMPLES)
        await insert_small_variant_records(
            _ASSEMBLY, family, [project], [*autosomal, *x_records, *_decoy_records()]
        )
        # Another family, at the same sites, with its own samples.
        other_family = str(uuid4())
        await insert_small_variant_records(
            _ASSEMBLY,
            other_family,
            [project],
            [_record(r.chr, r.start, ["OTHER"], ["1|1"], source="glimpse2") for r in autosomal[:500]],
        )

        # The sample is exactly the family's 1,000 smallest-key autosomal sites of the callset,
        # on every autosome, and the same on a second run.
        sample = await fetch_genotype_site_sample(ctx, scope="autosomes", limit=1_000, source="glimpse2")
        expected = sorted(autosomal, key=_key)[:1_000]
        assert [(chrom, pos) for chrom, pos, *_rest in sample] == [
            (r.chr.removeprefix("chr"), r.start) for r in expected
        ]
        assert {row[0] for row in sample} == {str(n) for n in range(1, 23)}
        assert all(row[4] == _SAMPLES for row in sample)
        by_site = {(r.chr.removeprefix("chr"), r.start): [c.gt for c in r.calls] for r in autosomal}
        assert all(row[5] == by_site[(row[0], row[1])] for row in sample)
        assert await fetch_genotype_site_sample(ctx, scope="autosomes", limit=1_000, source="glimpse2") == sample

        # Above the family's site count, every autosomal site of the callset, each once.
        everything = await fetch_genotype_site_sample(ctx, scope="autosomes", limit=90_000, source="glimpse2")
        assert sorted((row[0], row[1]) for row in everything) == sorted(by_site)

        # chrX: every site outside the PARs, under either name, and none inside them.
        x_sample = await fetch_genotype_site_sample(ctx, scope="chrX", limit=20_000, source="glimpse2")
        outside = sorted((r.chr.removeprefix("chr"), r.start) for r in x_records[:240])
        assert sorted((row[0], row[1]) for row in x_sample) == outside

        # A family linked to two projects stores each variant twice; the sample counts it once.
        # The limit caps the stored rows, so 20 rows hold the 10 smallest keys.
        twice = str(uuid4())
        second_project = str(uuid4())
        await insert_small_variant_records(_ASSEMBLY, twice, [project, second_project], autosomal[:50])
        ctx_twice = _context(twice, [project, second_project], _SAMPLES)
        all_twice = await fetch_genotype_site_sample(ctx_twice, scope="autosomes", limit=1_000, source="glimpse2")
        assert len(all_twice) == 50 and len({(row[0], row[1]) for row in all_twice}) == 50
        capped = await fetch_genotype_site_sample(ctx_twice, scope="autosomes", limit=20, source="glimpse2")
        assert [(row[0], row[1]) for row in capped] == [
            (r.chr.removeprefix("chr"), r.start) for r in sorted(autosomal[:50], key=_key)[:10]
        ]

        # The QC service on these rows (the Postgres side stubbed): the embryos read as siblings.
        async def _fake_context(session, *, family_identifier, user, project_id=None):
            return ctx

        async def _fake_get_family(session, family_id, user):
            return types.SimpleNamespace(metadata={})

        monkeypatch.setattr(sample_integrity_service, "build_family_metadata_context", _fake_context)
        monkeypatch.setattr(sample_integrity_service, "get_family_record", _fake_get_family)
        monkeypatch.setattr(sample_integrity_service, "QC_AUTOSOMAL_SITES", 5_000)
        report = await sample_integrity_service.get_family_sample_integrity_qc(
            session=None, family_id=ctx.family_id, user=None
        )
        assert report.application == "pgt" and report.genotype_source == "glimpse2"
        assert report.autosomal_sites == 5_000
        siblings = next(c for c in report.relatedness_checks if {c.sample_a, c.sample_b} == {"E1", "E2"})
        assert (siblings.inferred_relationship, siblings.status) == ("sibling", "pass"), siblings.message
        assert report.overall_status == "pass", report
        assert {c.sample_id: c.x_sites for c in report.sex_checks} == dict.fromkeys(_SAMPLES, 240)

    _run_with_ch_cleanup(_run)
