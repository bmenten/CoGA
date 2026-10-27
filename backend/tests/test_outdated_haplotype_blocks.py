"""Haplotype blocks built for another pedigree are outdated, and no embryo is called from them (CLIN-66).

The haplotype blocks are built when the phased genotypes are imported, from the index couple, the
couple's children and each member's recorded sex, and the embryo calls read them. A later edit of
the pedigree or of a sex neither rebuilt them nor warned. The loader now keeps, beside each
member's blocks, what they were built for (``pedigree_basis``); the PGT analysis compares it with
the family's pedigree, and blocks built for another, or without the record, are outdated: every
embryo is then uninformative, which the family page and the signed record say, until the phased
genotypes are imported again (the owner's choice of 2026-10-27). An affected status is not part
of it: the calls read the statuses when they are made. A single-file re-upload rewrote the blocks
but not the genome overview's lineage precompute, whose fingerprint left them out; it now covers
a digest of the stored blocks.
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
    kept = basis if basis is None or isinstance(basis, str) else json.dumps(basis)
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
    # Before, the blocks were read as current whatever had changed.
    assert pgt_analysis.blocks_outdated(now, _sources(haplotype_block_basis(_context(), _CALLSET))) is True


@pytest.mark.parametrize("kept", [None, "not json", json.dumps({"father": "FATHER"}), json.dumps(["x"])])
def test_blocks_without_their_record_count_as_outdated(kept: Any) -> None:
    # Loaded before the basis was kept, or with a record that does not say: not trusted.
    assert pgt_analysis.blocks_outdated(_context(), _sources(kept)) is True


def test_a_family_without_blocks_has_none_outdated() -> None:
    assert pgt_analysis.blocks_outdated(_context(), []) is False


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, sources: list[dict[str, Any]]) -> None:
        self.sources = sources

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        if "FROM families" in sql:
            return _Result(
                [{"roi_query": "GENE1", "roi_label": "GENE1", "roi_source": "gene", "roi_chr": "7",
                  "roi_start": 30_500_000, "roi_end": 30_600_000, "pgt": {"inheritance_model": "AD"}}]
            )
        if "FROM sample_interval_track_sources" in sql:
            assert "pedigree_basis" in sql
            return _Result(self.sources)
        return _Result([])


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

    out = asyncio.run(pgt_analysis.family_pgt_analysis(_Session(_sources(built)), _context(_with("E2", sex="female"))))

    assert out.inputs.blocks_outdated is True
    assert [(embryo.sample, embryo.call, embryo.qc_verdict) for embryo in out.embryos] == [
        ("E1", "uninformative", None),
        ("E2", "uninformative", None),
    ]
    assert out.disease_haplotype_resolved is False and out.disease_haplotypes == []
    # The blocks read are still named, as what the record could not use.
    assert out.inputs.blocks_read == 4 and out.inputs.blocks_digest is not None


# ---- A record signed before the check ------------------------------------------------------------


def test_a_record_signed_before_the_check_names_it_as_not_compared() -> None:
    current = {"embryos": [], "inputs": {"blocks_read": 4, "blocks_outdated": False}, "rules": {"qc_thresholds": {}}}
    signed = {"embryos": [], "inputs": {"blocks_read": 4}, "rules": {"qc_thresholds": {}}}

    comparable, not_compared = pgt_analysis.comparable_with_record(signed, current)

    assert comparable["inputs"] == {"blocks_read": 4}
    assert not_compared == [pgt_analysis.BLOCKS_OUTDATED_NOT_COMPARED]


def test_a_record_that_holds_the_check_is_compared_whole() -> None:
    current = {"embryos": [], "inputs": {"blocks_read": 4, "blocks_outdated": True}, "rules": {"qc_thresholds": {}}}
    signed = {"embryos": [], "inputs": {"blocks_read": 4, "blocks_outdated": False}, "rules": {"qc_thresholds": {}}}

    assert pgt_analysis.comparable_with_record(signed, current) == (current, [])


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

    # Before, the fingerprint left the blocks out, and the old precompute was served.
    assert await bed_service._fetch_precomputed_lineage(context) is None
