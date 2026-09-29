"""The haplotype responses mark each block where a male carries one copy: chrX or chrY
outside the pseudo-autosomal regions of the family's assembly. The PGT embryo call reads a
male as having one X only on such blocks, so inside a PAR, where a son also carries his
father's copy, both lanes are read."""

import pytest

from app.services import bed_service
from app.services.family_metadata_context import FamilyMetadataContext


def _ctx(assembly_name: str | None = "GRCh38") -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="fam-uuid",
        family_id="PGT1",
        project_ids=[],
        sample_rows=[
            {"sample_uuid": "u-dad", "sample_id": "DAD", "role": "father", "clinical_status": "affected"},
            {"sample_uuid": "u-son", "sample_id": "SON", "role": "embryo", "clinical_status": "unknown"},
        ],
        sample_uuid_to_name={"u-dad": "DAD", "u-son": "SON"},
        sample_name_to_uuid={"DAD": "u-dad", "SON": "u-son"},
        affected_sample_names=["DAD"],
        assembly_id="a1",
        assembly_name=assembly_name,
        relationship_rows=[],
    )


def _row(sample_uuid: str, chrom: str, start: int, end: int) -> dict:
    return {"sample_uuid": sample_uuid, "chr": chrom, "start": start, "end": end, "hap1": "0", "hap2": "1", "ps": None}


# GRCh38: one block inside PAR1, one past it, one reaching into PAR2, one on an autosome.
ROWS = [
    _row("u-son", "X", 500_000, 650_000),
    _row("u-son", "X", 3_000_000, 40_000_000),
    _row("u-son", "X", 150_000_000, 156_000_000),
    _row("u-dad", "7", 1, 1_000_000),
]


def _patch(monkeypatch) -> None:
    async def rows(*args, **kwargs):
        return [dict(row) for row in ROWS]

    async def same(context, *, segments_by_uuid, **kwargs):
        return segments_by_uuid

    monkeypatch.setattr(bed_service, "fetch_interval_track_rows", rows)
    monkeypatch.setattr(bed_service, "_apply_haplotype_lineage", same)
    monkeypatch.setattr(bed_service, "_apply_haplotype_lineage_genomewide", same)


def _flags(response) -> dict[tuple[str, str, int], bool]:
    return {
        (sample.sample, segment.chr, segment.start): segment.hemizygous_in_males
        for sample in response.samples
        for segment in sample.segments
    }


EXPECTED = {
    ("SON", "X", 500_000): False,
    ("SON", "X", 3_000_000): True,
    ("SON", "X", 150_000_000): False,
    ("DAD", "7", 1): False,
}


@pytest.mark.asyncio
async def test_the_region_response_marks_where_a_male_has_one_copy(monkeypatch):
    _patch(monkeypatch)
    response = await bed_service.get_family_haplotypes_response(
        None, context=_ctx(), chr="X", start=0, end=156_030_895
    )
    assert _flags(response) == EXPECTED


@pytest.mark.asyncio
async def test_the_genome_response_marks_where_a_male_has_one_copy(monkeypatch):
    _patch(monkeypatch)
    response = await bed_service.get_family_haplotypes_batch_response(
        None, context=_ctx(), chromosomes=["X", "7"]
    )
    assert _flags(response) == EXPECTED


@pytest.mark.asyncio
async def test_no_block_is_one_copy_where_the_assembly_s_pars_are_not_known(monkeypatch):
    # Unknown bounds could put any block in a PAR: a male is read as having two copies.
    _patch(monkeypatch)
    response = await bed_service.get_family_haplotypes_response(
        None, context=_ctx("T2T-CHM13v2.0"), chr="X", start=0, end=156_030_895
    )
    assert set(_flags(response).values()) == {False}
