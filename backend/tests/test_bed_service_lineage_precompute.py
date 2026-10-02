"""Unit tests for the upload-time genome-overview lineage precompute in
``bed_service``: the pedigree fingerprint, the origin-packed interval rows, and the
hash-guarded read that serves the precompute (and never serves it stale)."""

import pytest

from app.services import bed_service
from app.services.family_metadata_context import FamilyMetadataContext


def _ctx(**overrides) -> FamilyMetadataContext:
    base = dict(
        family_uuid="fam-uuid",
        family_id="FAM001",
        project_ids=[],
        sample_rows=[
            {"sample_uuid": "u-dad", "sample_id": "DAD", "role": "father", "clinical_status": "unaffected"},
            {"sample_uuid": "u-mom", "sample_id": "MOM", "role": "mother", "clinical_status": "unaffected"},
            {"sample_uuid": "u-kid", "sample_id": "KID", "role": "child", "clinical_status": "affected"},
        ],
        sample_uuid_to_name={"u-dad": "DAD", "u-mom": "MOM", "u-kid": "KID"},
        sample_name_to_uuid={"DAD": "u-dad", "MOM": "u-mom", "KID": "u-kid"},
        affected_sample_names=["KID"],
        assembly_id="a1",
        assembly_name="GRCh38",
        relationship_rows=[
            {
                "relationship_type": "parent",
                "sample_id_a": "DAD",
                "sample_id_b": "KID",
                "role_a": "father",
                "role_b": "child",
            }
        ],
    )
    base.update(overrides)
    return FamilyMetadataContext(**base)


def test_lineage_hash_is_deterministic_and_sensitive():
    baseline = bed_service._lineage_hash(_ctx())
    # Stable across calls for unchanged inputs.
    assert baseline == bed_service._lineage_hash(_ctx())
    # Affected-status change -> different fingerprint.
    assert baseline != bed_service._lineage_hash(_ctx(affected_sample_names=["MOM"]))
    # Role change -> different fingerprint.
    rows = [dict(r) for r in _ctx().sample_rows]
    rows[0]["role"] = "child"
    assert baseline != bed_service._lineage_hash(_ctx(sample_rows=rows))
    # Pedigree-edge change -> different fingerprint.
    assert baseline != bed_service._lineage_hash(_ctx(relationship_rows=[]))


def test_lineage_hash_holds_the_roi_only_for_a_family_with_a_relative_of_unknown_degree():
    # A relative of unknown degree is read at the ROI, so moving the ROI makes its
    # colours stale; a family without one keeps its fingerprint.
    assert bed_service._lineage_hash(_ctx()) == bed_service._lineage_hash(_ctx(roi=("chr7", 1_000, 2_000)))
    relative = {
        "relationship_type": "relative",
        "sample_id_a": "MOM",
        "sample_id_b": "KID",
        "role_a": "relative",
        "role_b": "relative",
    }
    linked = _ctx().relationship_rows + [relative]
    hashes = {
        bed_service._lineage_hash(_ctx(relationship_rows=linked, roi=roi))
        for roi in (None, ("chr7", 1_000, 2_000), ("chr7", 1_000, 3_000))
    }
    assert len(hashes) == 3


def test_lineage_interval_rows_pack_lineage_into_origin():
    ctx = _ctx()
    segments = {
        "DAD": [
            {
                "chr": "1",
                "start": 1,
                "end": 100,
                "hap1": "0",
                "hap2": "1",
                "ps": 5,
                "hap1_lineage": "paternal",
                "hap2_lineage": "untransmitted",
            }
        ],
        # A sample not in the family mapping is dropped.
        "GHOST": [{"chr": "1", "start": 1, "end": 2, "hap1": "0", "hap2": "1"}],
    }
    rows = bed_service._lineage_interval_rows(ctx, segments, lineage_hash="HHH")
    assert len(rows) == 1
    row = rows[0]
    assert row["track_type"] == bed_service.LINEAGE_TRACK_TYPE
    assert row["source"] == "glimpse2_lineage"
    assert row["sample_id"] == "u-dad"
    assert row["family_id"] == "fam-uuid"
    assert row["origin"] == "paternal|untransmitted"
    assert '"lineage_hash": "HHH"' in row["metadata_json"]


@pytest.mark.asyncio
async def test_fetch_precomputed_returns_none_when_absent(monkeypatch):
    async def no_hash(*args, **kwargs):
        return None

    monkeypatch.setattr(bed_service, "get_interval_track_lineage_hash", no_hash)
    assert await bed_service._fetch_precomputed_lineage(_ctx()) is None


@pytest.mark.asyncio
async def test_fetch_precomputed_returns_none_when_stale(monkeypatch):
    """A stale precompute (hash mismatch after a pedigree/status edit) is never
    served, and the (expensive) row fetch is not even attempted."""

    async def wrong_hash(*args, **kwargs):
        return "STALE-DOES-NOT-MATCH"

    async def must_not_fetch(*args, **kwargs):
        raise AssertionError("rows must not be fetched when the hash is stale")

    monkeypatch.setattr(bed_service, "get_interval_track_lineage_hash", wrong_hash)
    monkeypatch.setattr(bed_service, "fetch_interval_track_rows", must_not_fetch)
    assert await bed_service._fetch_precomputed_lineage(_ctx()) is None


@pytest.mark.asyncio
async def test_fetch_precomputed_parses_origin_when_hash_matches(monkeypatch):
    ctx = _ctx()
    matching = bed_service._lineage_hash(ctx)

    async def good_hash(*args, **kwargs):
        return matching

    async def stored_rows(*args, **kwargs):
        return [
            {
                "sample_uuid": "u-dad",
                "chr": "1",
                "start": 1,
                "end": 100,
                "hap1": "0",
                "hap2": "1",
                "ps": 5,
                "origin": "paternal|untransmitted",
            },
            {
                "sample_uuid": "u-mom",
                "chr": "1",
                "start": 1,
                "end": 100,
                "hap1": "0",
                "hap2": "1",
                "ps": None,
                "origin": "maternal|unknown",
            },
        ]

    monkeypatch.setattr(bed_service, "get_interval_track_lineage_hash", good_hash)
    monkeypatch.setattr(bed_service, "fetch_interval_track_rows", stored_rows)

    out = await bed_service._fetch_precomputed_lineage(ctx)
    assert out is not None
    assert out["u-dad"][0]["hap1_lineage"] == "paternal"
    assert out["u-dad"][0]["hap2_lineage"] == "untransmitted"
    assert out["u-mom"][0]["hap1_lineage"] == "maternal"
    assert out["u-mom"][0]["hap2_lineage"] == "unknown"


@pytest.mark.asyncio
async def test_genomewide_read_serves_precompute_when_present(monkeypatch):
    ctx = _ctx()

    async def precomputed(_context):
        return {
            "u-dad": [
                {
                    "chr": "1",
                    "start": 1,
                    "end": 9,
                    "hap1": "0",
                    "hap2": "1",
                    "ps": None,
                    "hap1_lineage": "paternal",
                    "hap2_lineage": "untransmitted",
                }
            ]
        }

    monkeypatch.setattr(bed_service, "_fetch_precomputed_lineage", precomputed)
    out = await bed_service._apply_haplotype_lineage_genomewide(
        ctx, segments_by_uuid={"u-dad": []}, chromosomes=["1"]
    )
    assert out["u-dad"][0]["hap1_lineage"] == "paternal"


@pytest.mark.asyncio
async def test_genomewide_read_falls_back_to_grey_when_no_precompute(monkeypatch):
    """With no precompute, relatives must NOT be coloured (the fast grey fallback)."""
    ctx = _ctx()

    async def absent(_context):
        return None

    monkeypatch.setattr(bed_service, "_fetch_precomputed_lineage", absent)
    # A grandmother (relative, not in the nuclear core) with a stored block.
    ctx.sample_rows.append(
        {"sample_uuid": "u-gma", "sample_id": "GMA", "role": "grandmother", "clinical_status": "unaffected"}
    )
    ctx.sample_uuid_to_name["u-gma"] = "GMA"
    ctx.sample_name_to_uuid["GMA"] = "u-gma"
    out = await bed_service._apply_haplotype_lineage_genomewide(
        ctx,
        segments_by_uuid={"u-gma": [{"chr": "1", "start": 1, "end": 9, "hap1": "0", "hap2": "1"}]},
        chromosomes=["1"],
    )
    for seg in out.get("u-gma", []):
        assert seg.get("hap1_lineage") not in ("paternal", "maternal")
        assert seg.get("hap2_lineage") not in ("paternal", "maternal")
