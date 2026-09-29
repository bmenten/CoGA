"""Haplotype blocks from a glimpse2 upload, pinned against a recorded output (#528).

The block builder moved out of ``upload_family_small_variant_file``. These seeded families
must give exactly the blocks, in the same order, that the in-line builder gave. They are a
quartet with segregation haplotypes, the same at the unit-test switch thresholds, a family
without both parents (per-sample phase-set blocks), and a quartet with chromosome sizes
known. Regenerate the fixture only for an intended change to the blocks:

    COGA_REGENERATE_GOLDEN=1 python -m pytest backend/tests/test_variant_upload_haplotype_golden.py
"""

from __future__ import annotations

import json
import os
import random
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import UploadFile

from backend.app.services import haplotype_block_builder, variant_upload_service
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext

GOLDEN = Path(__file__).parent / "fixtures" / "haplotype_blocks_golden.json"
CHROMOSOMES = ["1", "2", "X"]
SITES_PER_CHROMOSOME = 400
SITE_SPACING = 3_000


def _members(with_father: bool) -> list[tuple[str, str, str, bool, str]]:
    members = [
        ("mother-uuid", "MOTHER", "mother", False, "female"),
        ("affected-uuid", "AFFECTED", "proband", True, "female"),
        ("embryo1-uuid", "EMBRYO1", "embryo", False, "und"),
        ("embryo2-uuid", "EMBRYO2", "embryo", False, "und"),
    ]
    if with_father:
        members.insert(0, ("father-uuid", "FATHER", "father", False, "male"))
    return members


def _contexts(members: list[tuple[str, str, str, bool, str]]):
    context = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": uuid, "sample_id": sample_id, "role": role, "affected": affected, "sex": sex}
            for uuid, sample_id, role, affected, sex in members
        ],
        sample_uuid_to_name={uuid: sample_id for uuid, sample_id, *_ in members},
        sample_name_to_uuid={sample_id: uuid for uuid, sample_id, *_ in members},
        affected_sample_names=[sample_id for _uuid, sample_id, _role, affected, _sex in members if affected],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
    sample_contexts = {
        sample_id: SampleMetadataContext(
            sample_uuid=uuid,
            sample_id=sample_id,
            family_uuid="family-uuid",
            family_id="FAM001",
            sex=sex,
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for uuid, sample_id, _role, _affected, sex in members
    }
    return context, sample_contexts


def _simulated_vcf(
    seed: int,
    members: list[tuple[str, str, str, bool, str]],
    *,
    spacing: int = SITE_SPACING,
    switch_errors: bool = False,
) -> str:
    """A phased family: the parents' homologs at random, each child inheriting one of each
    parent's with one crossover per chromosome. A few calls are missing or unphased, and on
    some chromosomes the calls carry a phase set per 120 sites. With ``switch_errors``, each
    child also has a run of sites that looks inherited from the other homolog (a phasing
    switch error), long enough on some chromosomes to pass the switch thresholds."""
    rng = random.Random(seed)
    names = [sample_id for _uuid, sample_id, *_ in members]
    lines = [
        "##fileformat=VCFv4.2",
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Phased genotype">',
        '##FORMAT=<ID=GP,Number=G,Type=Float,Description="Genotype probabilities">',
        '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(names),
    ]
    for chrom in CHROMOSOMES:
        with_phase_sets = rng.random() < 0.5
        father = [[rng.randint(0, 1) for _ in range(SITES_PER_CHROMOSOME)] for _ in range(2)]
        mother = [[rng.randint(0, 1) for _ in range(SITES_PER_CHROMOSOME)] for _ in range(2)]
        children: dict[str, tuple[int, int, int, int]] = {}
        for name in names:
            if name in {"FATHER", "MOTHER"}:
                continue
            # (paternal homolog, maternal homolog, crossover site, which parent crosses)
            children[name] = (
                rng.randint(0, 1),
                rng.randint(0, 1),
                rng.randint(SITES_PER_CHROMOSOME // 5, SITES_PER_CHROMOSOME - 50),
                rng.randint(0, 1),
            )
        bursts: dict[str, tuple[int, int, int]] = {}
        if switch_errors:
            for name in children:
                # (first site, length, which parent's homolog looks switched)
                bursts[name] = (rng.randint(10, 150), rng.randint(40, 160), rng.randint(0, 1))
        for site in range(SITES_PER_CHROMOSOME):
            pos = 10_000 + site * spacing
            calls = []
            for name in names:
                if name == "FATHER":
                    alleles = (father[0][site], father[1][site])
                elif name == "MOTHER":
                    alleles = (mother[0][site], mother[1][site])
                else:
                    pat, mat, crossover, crossing = children[name]
                    if site >= crossover:
                        if crossing == 0:
                            pat = 1 - pat
                        else:
                            mat = 1 - mat
                    if name in bursts:
                        first, length, side = bursts[name]
                        if first <= site < first + length:
                            if side == 0:
                                pat = 1 - pat
                            else:
                                mat = 1 - mat
                    alleles = (father[pat][site], mother[mat][site])
                roll = rng.random()
                if roll < 0.004:
                    gt = "./."
                elif roll < 0.008:
                    gt = f"{min(alleles)}/{max(alleles)}"
                else:
                    gt = f"{alleles[0]}|{alleles[1]}"
                ps = str(1 + site // 120) if with_phase_sets else "."
                calls.append(f"{gt}:0.01,0.98,0.01:{ps}")
            lines.append(f"{chrom}\t{pos}\t.\tA\tG\t.\tPASS\t.\tGT:GP:PS\t" + "\t".join(calls))
    return "\n".join(lines) + "\n"


class _FakeSession:
    async def commit(self) -> None:
        return None


async def _blocks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    seed: int,
    with_father: bool,
    unit_thresholds: bool,
    chromosome_sizes: dict[str, int],
    spacing: int = SITE_SPACING,
    switch_errors: bool = False,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []

    async def nothing(*_args: Any, **_kwargs: Any) -> Any:
        return None

    async def zero(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def no_tracks(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    async def capture(_assembly_name: str, inserted: list[dict[str, Any]]) -> None:
        rows.extend(inserted)

    async def sizes(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        return dict(chromosome_sizes)

    monkeypatch.setattr(variant_upload_service, "count_family_small_variants", zero)
    monkeypatch.setattr(variant_upload_service, "get_track_presence_by_sample", no_tracks)
    monkeypatch.setattr(variant_upload_service, "insert_small_variant_records", nothing)
    monkeypatch.setattr(variant_upload_service, "refresh_family_small_variant_summaries", nothing)
    monkeypatch.setattr(variant_upload_service, "insert_interval_track_rows", capture)
    monkeypatch.setattr(variant_upload_service, "upsert_interval_track_source", nothing)
    monkeypatch.setattr(variant_upload_service, "_fetch_chromosome_sizes", sizes)
    monkeypatch.setattr(
        "backend.app.services.annotation_manifest_service.merge_vcf_header_provenance", nothing
    )
    if unit_thresholds:
        monkeypatch.setattr(haplotype_block_builder, "SEGREGATION_HAPLOTYPE_SWITCH_MIN_MARKERS", 1)
        monkeypatch.setattr(haplotype_block_builder, "SEGREGATION_HAPLOTYPE_SWITCH_MIN_SPAN", 0)

    members = _members(with_father)
    context, sample_contexts = _contexts(members)
    vcf = _simulated_vcf(seed, members, spacing=spacing, switch_errors=switch_errors)
    upload = UploadFile(file=BytesIO(vcf.encode()), filename="family.vcf")
    result = await variant_upload_service.upload_family_small_variant_file(
        _FakeSession(),  # type: ignore[arg-type]
        context=context,
        sample_contexts=sample_contexts,
        file=upload,
        overwrite=False,
        format_hint="glimpse2",
    )
    # The metadata carries the upload time; everything else must match exactly.
    metadata = {json.dumps({k: v for k, v in json.loads(row["metadata_json"]).items() if k != "uploaded_at"}) for row in rows}
    assert len(metadata) <= 1
    return {
        "inserted": result["inserted"],
        "haplotypes_inserted": result["haplotypes_inserted"],
        "metadata": sorted(metadata),
        "blocks": [
            [row["sample_id"], row["chr"], row["start"], row["end"], row["hap1"], row["hap2"], row["ps"]]
            for row in rows
        ],
    }


def _dump(observed: dict[str, Any]) -> str:
    parts = []
    for name, scenario in observed.items():
        header = {key: value for key, value in scenario.items() if key != "blocks"}
        blocks = ",\n".join("   " + json.dumps(block) for block in scenario["blocks"])
        parts.append(f" {json.dumps(name)}: {{\n  " + json.dumps(header)[1:-1] + ',\n  "blocks": [\n' + blocks + "\n  ]\n }")
    return "{\n" + ",\n".join(parts) + "\n}\n"


SCENARIOS = {
    "quartet_segregation": dict(seed=528, with_father=True, unit_thresholds=False, chromosome_sizes={}),
    "quartet_segregation_unit_thresholds": dict(
        seed=529, with_father=True, unit_thresholds=True, chromosome_sizes={}
    ),
    "no_father_phase_set_blocks": dict(seed=530, with_father=False, unit_thresholds=False, chromosome_sizes={}),
    # Sparse sites: 500 kb is ~42 sites, so the 50-marker rule decides which bursts switch.
    "sparse_quartet_switch_errors": dict(
        seed=532,
        with_father=True,
        unit_thresholds=False,
        chromosome_sizes={},
        spacing=12_000,
        switch_errors=True,
    ),
    "quartet_with_chromosome_sizes": dict(
        seed=531,
        with_father=True,
        unit_thresholds=False,
        chromosome_sizes={"1": 248_956_422, "2": 242_193_529, "X": 156_040_895},
    ),
}


@pytest.mark.asyncio
async def test_haplotype_blocks_match_the_recorded_output(monkeypatch: pytest.MonkeyPatch) -> None:
    observed = {}
    for name, scenario in SCENARIOS.items():
        with monkeypatch.context() as patch:
            observed[name] = await _blocks(patch, **scenario)  # type: ignore[arg-type]
    if os.environ.get("COGA_REGENERATE_GOLDEN"):
        # One block per line keeps the fixture small and its diffs readable.
        GOLDEN.write_text(_dump(observed), encoding="utf-8")
    recorded = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(observed) == set(recorded)
    for name in SCENARIOS:
        assert observed[name]["inserted"] == recorded[name]["inserted"], name
        assert observed[name]["blocks"] == recorded[name]["blocks"], name
        assert observed[name]["haplotypes_inserted"] == recorded[name]["haplotypes_inserted"], name
        assert observed[name]["metadata"] == recorded[name]["metadata"], name


def test_the_scenarios_exercise_both_block_builders() -> None:
    """The fixture is only a check if it holds real blocks from both paths."""
    recorded = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name, scenario in SCENARIOS.items():
        blocks = recorded[name]["blocks"]
        samples = {block[0] for block in blocks}
        assert len(blocks) > 20, name
        assert "embryo1-uuid" in samples and "embryo2-uuid" in samples, name
        if scenario["with_father"]:
            # Segregation haplotypes: parents carry 0/1 labels; children switch homolog.
            assert {"father-uuid", "mother-uuid"} <= samples, name
        else:
            # Per-sample phase-set blocks: a block per phase set.
            assert {block[6] for block in blocks} - {None}, name
