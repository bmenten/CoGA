"""Haplotype blocks built for another pedigree are outdated, and no embryo is called from them (CLIN-66).

The haplotype blocks are built when the phased genotypes are imported, from the index couple, the
couple's children and each member's recorded sex, and the embryo calls read them. A later edit of
the pedigree or of a sex does not rebuild them, so the loader keeps, beside each
member's blocks, what they were built for (``pedigree_basis``); the PGT analysis compares it with
the family's pedigree, and blocks built for another, or whose record cannot be read, are
outdated: every embryo is then uninformative, which the family page and the signed record say,
until the phased genotypes are imported again (the owner's choice of 2026-10-27). An affected status is not part
of it: the calls read the statuses when they are made. A single-file re-upload rewrites the blocks
but not the genome overview's lineage precompute, so the precompute's fingerprint covers a digest
of the stored blocks.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.app.schemas import HaplotypeResponse
from backend.app.services import bed_service, pgt_analysis, variant_upload_service
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.haplotype_block_builder import HaplotypeBlockBuilder, haplotype_block_basis
from backend.tests._pgt_session_fakes import AnsweringSession

_MEMBERS = [
    {"sample_id": "FATHER", "role": "father", "sex": "male", "affected": True, "clinical_status": "affected"},
    {"sample_id": "MOTHER", "role": "mother", "sex": "female", "affected": False, "clinical_status": "unaffected"},
    {"sample_id": "E1", "role": "embryo", "sex": "female", "affected": False, "clinical_status": "unknown"},
    {"sample_id": "E2", "role": "embryo", "sex": "male", "affected": False, "clinical_status": "unknown"},
]
_CALLSET = ["FATHER", "MOTHER", "E1", "E2"]


def _context(members: list[dict[str, Any]] | None = None, children: tuple[str, ...] = ("E1", "E2"), **overrides: Any) -> FamilyMetadataContext:
    rows = members if members is not None else _MEMBERS
    values: dict[str, Any] = {
        "family_uuid": "11111111-1111-1111-1111-111111111111",
        "family_id": "FAM_PGT",
        "project_ids": ["p1"],
        "sample_rows": rows,
        "sample_uuid_to_name": {f"u-{row['sample_id']}": row["sample_id"] for row in rows},
        "sample_name_to_uuid": {row["sample_id"]: f"u-{row['sample_id']}" for row in rows},
        "affected_sample_names": [row["sample_id"] for row in rows if row.get("affected")],
        "assembly_id": "a1",
        "assembly_name": "GRCh38",
        "relationship_rows": [
            {"relationship_type": "parent_child", "sample_id_a": parent, "sample_id_b": child, "role_a": role}
            for child in children
            for parent, role in (("FATHER", "father"), ("MOTHER", "mother"))
        ],
    }
    values.update(overrides)
    return FamilyMetadataContext(**values)


def _sources(basis: Any) -> list[dict[str, Any]]:
    """What Postgres records of each member's blocks, with ``basis`` as the loader kept it."""
    kept = basis if isinstance(basis, str) else json.dumps(basis)
    return [
        {"sample": name, "source": "glimpse2", "filename": "family.vcf.gz", "row_count": 4, "uploaded_at": None,
         "pedigree_basis": kept}
        for name in _CALLSET
    ]


# ---- What the blocks rest on --------------------------------------------------------------------


def test_the_basis_is_the_couple_their_children_and_the_samples_sexes() -> None:
    basis = haplotype_block_basis(_context(), ["MOTHER", "E1", "FATHER", "E2", "E1"])

    assert basis == {
        "father": "FATHER",
        "mother": "MOTHER",
        "children": ["E1", "E2"],
        "sexes": {"E1": "female", "E2": "male", "FATHER": "male", "MOTHER": "female"},
    }


def test_the_builder_keeps_the_basis_of_the_samples_its_file_held() -> None:
    builder = HaplotypeBlockBuilder(context=_context(), sample_contexts={}, chromosome_sizes={}, metadata_json="{}")
    for name in ("FATHER", "MOTHER", "E1"):
        builder.add_sample(name)

    # E2 is a child of the couple but not in the file: not part of what these blocks rest on.
    assert builder.basis() == haplotype_block_basis(_context(), ["FATHER", "MOTHER", "E1"])
    assert builder.basis()["children"] == ["E1"]


@pytest.mark.asyncio
async def test_the_loader_keeps_the_basis_beside_each_members_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    kept: list[dict[str, Any]] = []

    async def insert_rows(assembly_name: str, rows: list[dict[str, Any]]) -> None:
        return None

    async def upsert(session: Any, **kwargs: Any) -> None:
        kept.append(kwargs["metadata"])

    monkeypatch.setattr(variant_upload_service, "insert_interval_track_rows", insert_rows)
    monkeypatch.setattr(variant_upload_service, "upsert_interval_track_source", upsert)

    class _Sample:
        def __init__(self, uuid: str) -> None:
            self.sample_uuid = uuid

    basis = haplotype_block_basis(_context(), _CALLSET)
    await variant_upload_service._insert_haplotype_rows(
        object(),
        assembly_name="GRCh38",
        rows=[{"sample_id": "u-E1"}, {"sample_id": "u-E2"}],
        sample_contexts={"E1": _Sample("u-E1"), "E2": _Sample("u-E2")},
        filename="family.vcf.gz",
        metadata_json=json.dumps({"source": "glimpse2"}),
        basis=basis,
    )

    assert kept == [{"source": "glimpse2", "pedigree_basis": basis}] * 2


# ---- Outdated blocks -----------------------------------------------------------------------------


def _with(member: str, **fields: Any) -> list[dict[str, Any]]:
    return [{**row, **fields} if row["sample_id"] == member else row for row in _MEMBERS]


@pytest.mark.parametrize(
    "now",
    [
        _context(),
        # An affected status the calls read when they are made.
        _context(_with("MOTHER", affected=True, clinical_status="affected")),
        # A member the file did not hold, even a child of the couple.
        _context([*_MEMBERS, {"sample_id": "E3", "role": "embryo", "sex": "male"}], children=("E1", "E2", "E3")),
    ],
    ids=["as built", "an affected status changed", "a member added who is not in the file"],
)
def test_blocks_built_for_the_pedigree_as_it_is_are_current(now: FamilyMetadataContext) -> None:
    assert pgt_analysis.blocks_outdated(now, _sources(haplotype_block_basis(_context(), _CALLSET))) is False


@pytest.mark.parametrize(
    "now",
    [
        _context(_with("E2", sex="female")),
        _context(_with("E1", sex=None)),
        # E2 is no longer a child of the couple.
        _context(children=("E1",)),
    ],
    ids=["a sex corrected", "a sex removed", "a child unlinked"],
)
def test_blocks_built_for_another_pedigree_are_outdated(now: FamilyMetadataContext) -> None:
    assert pgt_analysis.blocks_outdated(now, _sources(haplotype_block_basis(_context(), _CALLSET))) is True


@pytest.mark.parametrize("kept", ["not json", json.dumps({"father": "FATHER"}), json.dumps(["x"])])
def test_blocks_whose_record_cannot_be_read_count_as_outdated(kept: Any) -> None:
    # A record that cannot be read, or that does not say what the blocks were built for: not
    # trusted.
    assert pgt_analysis.blocks_outdated(_context(), _sources(kept)) is True


def test_a_family_without_blocks_has_none_outdated() -> None:
    assert pgt_analysis.blocks_outdated(_context(), []) is False


def _reads_the_basis(session: AnsweringSession) -> bool:
    """Whether the record of the blocks was read with what they were built for."""
    reads = [sql for sql, _params in session.executed if "FROM sample_interval_track_sources" in sql]
    return bool(reads) and all("pedigree_basis" in sql for sql in reads)


def test_no_embryo_is_called_from_outdated_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    block = {"chr": "7", "start": 29_000_000, "end": 32_000_000, "hap1": "1", "hap2": "0", "ps": None,
             "hap1_lineage": "paternal", "hap2_lineage": "maternal", "hemizygous_in_males": False}

    async def haplotypes(session: Any, *, context: Any, chr: str, start: Any = None, end: Any = None) -> HaplotypeResponse:
        return HaplotypeResponse.model_validate(
            {"chr": "7", "start": start, "end": end, "samples": [{"sample": name, "segments": [block]} for name in _CALLSET]}
        )

    async def no_markers(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no embryo is called, so no phasing QC is counted")

    monkeypatch.setattr(pgt_analysis, "get_family_haplotypes_response", haplotypes)
    monkeypatch.setattr(pgt_analysis, "get_family_phased_markers_response", no_markers)
    built = haplotype_block_basis(_context(), _CALLSET)
    session = AnsweringSession(
        {
            "FROM families": [
                {"roi_query": "GENE1", "roi_label": "GENE1", "roi_source": "gene", "roi_chr": "7",
                 "roi_start": 30_500_000, "roi_end": 30_600_000, "pgt": {"inheritance_model": "AD"}}
            ],
            "FROM sample_interval_track_sources": _sources(built),
        }
    )

    out = asyncio.run(pgt_analysis.family_pgt_analysis(session, _context(_with("E2", sex="female"))))

    assert _reads_the_basis(session)
    assert out.inputs.blocks_outdated is True
    assert [(embryo.sample, embryo.call, embryo.qc_verdict) for embryo in out.embryos] == [
        ("E1", "uninformative", None),
        ("E2", "uninformative", None),
    ]
    assert out.disease_haplotype_resolved is False and out.disease_haplotypes == []
    # The blocks read are still named, as what the record could not use.
    assert out.inputs.blocks_read == 4 and out.inputs.blocks_digest is not None


# ---- The lineage precompute ----------------------------------------------------------------------


def test_the_lineage_fingerprint_covers_the_stored_blocks() -> None:
    context = _context()

    assert bed_service._lineage_hash(context, blocks="4:11") != bed_service._lineage_hash(context, blocks="4:12")


@pytest.mark.asyncio
async def test_a_precompute_of_blocks_written_again_is_not_served(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context()
    stored = bed_service._lineage_hash(context, blocks="4:11")

    async def stored_hash(*args: Any, **kwargs: Any) -> str:
        return stored

    async def blocks_now(*args: Any, **kwargs: Any) -> str:
        return "4:12"  # a single-file re-upload rewrote the blocks

    async def must_not_fetch(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a stale precompute's rows are not read")

    monkeypatch.setattr(bed_service, "get_interval_track_lineage_hash", stored_hash)
    monkeypatch.setattr(bed_service, "interval_track_rows_digest", blocks_now)
    monkeypatch.setattr(bed_service, "fetch_interval_track_rows", must_not_fetch)

    # The blocks' digest moved the fingerprint: the stored precompute is not served.
    assert await bed_service._fetch_precomputed_lineage(context) is None


# ---- The haplotype tracks (part 2) ------------------------------------------------------------------


def _no_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    async def rows(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def same(context: Any, *, segments_by_uuid: Any, **kwargs: Any) -> Any:
        return segments_by_uuid

    monkeypatch.setattr(bed_service, "fetch_interval_track_rows", rows)
    monkeypatch.setattr(bed_service, "_apply_haplotype_lineage", same)
    monkeypatch.setattr(bed_service, "_apply_haplotype_lineage_genomewide", same)


@pytest.mark.asyncio
@pytest.mark.parametrize("now, outdated", [(_context(), False), (_context(_with("E2", sex="female")), True)], ids=["current", "outdated"])
async def test_the_haplotype_tracks_are_told_when_the_blocks_are_outdated(
    monkeypatch: pytest.MonkeyPatch, now: FamilyMetadataContext, outdated: bool
) -> None:
    _no_blocks(monkeypatch)
    # The record of each member's blocks, as Postgres keeps it: the only statement the tracks run.
    session = AnsweringSession({"FROM sample_interval_track_sources": _sources(haplotype_block_basis(_context(), _CALLSET))})

    region = await bed_service.get_family_haplotypes_response(session, context=now, chr="7", start=1, end=2)
    genome = await bed_service.get_family_haplotypes_batch_response(session, context=now, chromosomes=["7"])

    assert session.executed and all("FROM sample_interval_track_sources" in sql for sql, _params in session.executed)
    # The tracks say whether the blocks are outdated.
    assert (region.blocks_outdated, genome.blocks_outdated) == (outdated, outdated)
