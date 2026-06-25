"""Phase 0 of SNV + SV compound het: the SV→gene index + the badge summary."""

from __future__ import annotations

import asyncio
import types

from backend.app.services import clickhouse_family_variants as cfv
from backend.app.services.sv_gene_index_service import summarize_second_hit


def _sv(sv_type: str, gt: dict[str, str], ps: dict[str, int] | None = None) -> dict:
    return {
        "sv_id": "v",
        "sv_type": sv_type,
        "chr": "1",
        "start": 1,
        "end": 9,
        "gt": gt,
        "ps": ps or {},
    }


def test_deletion_in_affected_is_flagged_and_het() -> None:
    summary = summarize_second_hit([_sv("DEL", {"S1": "0/1"})], ["S1"])
    assert summary["sv_count"] == 1
    assert summary["sv_types"] == ["DEL"]
    assert summary["affected_zygosity"] == "het"
    assert summary["has_deletion"] is True  # the unmasking case


def test_homozygous_sv() -> None:
    summary = summarize_second_hit([_sv("DUP", {"S1": "1/1"})], ["S1"])
    assert summary["affected_zygosity"] == "hom"
    assert summary["has_deletion"] is False


def test_mixed_zygosity_across_svs() -> None:
    summary = summarize_second_hit(
        [_sv("DEL", {"S1": "0/1"}), _sv("INS", {"S1": "1/1"})], ["S1"]
    )
    assert summary["affected_zygosity"] == "mixed"
    assert summary["sv_types"] == ["DEL", "INS"]
    assert summary["has_deletion"] is True


def test_no_affected_genotype_leaves_zygosity_unknown() -> None:
    # SV present in the family but not called in the affected sample.
    summary = summarize_second_hit([_sv("INV", {"S2": "0/1"})], ["S1"])
    assert summary["affected_zygosity"] is None
    assert summary["has_deletion"] is False


def test_phase_trans_with_unaffected_parents() -> None:
    # Affected child het for the SNV; SV carried; neither unaffected parent carries both.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1", "father": "0/0"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/1"},
    )
    assert summary["phase"] == "trans"
    assert summary["deletion_unmasked"] is True  # DEL in trans with a het SNV → biallelic


def test_phase_cis_when_unaffected_carries_both() -> None:
    summary = summarize_second_hit(
        [_sv("DUP", {"child": "0/1", "mother": "0/1"})],
        ["child"],
        unaffected_samples=["mother"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/1"},  # mother carries SNV + SV
    )
    assert summary["phase"] == "cis"
    assert summary["deletion_unmasked"] is False


def test_phase_unknown_for_singleton() -> None:
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1"})],
        ["child"],
        unaffected_samples=[],
        snv_gt_by_sample={"child": "0/1"},
    )
    assert summary["phase"] == "unknown"
    assert summary["deletion_unmasked"] is False


def test_phase_unknown_without_snv_genotype() -> None:
    summary = summarize_second_hit([_sv("DEL", {"S1": "0/1"})], ["S1"])
    assert summary["phase"] == "unknown"


def test_read_phase_trans_from_shared_phase_set() -> None:
    # SNV alt on hap 1 (0|1), SV alt on hap 0 (1|0), same phase set 1000 → trans by reads.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "1|0"}, ps={"child": 1000})],
        ["child"],
        unaffected_samples=[],  # singleton → segregation could not decide, but reads can
        snv_gt_by_sample={"child": "0|1"},
        snv_ps_by_sample={"child": 1000},
    )
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "read"
    assert summary["deletion_unmasked"] is True


def test_read_phase_cis_from_shared_phase_set() -> None:
    # Both alts on hap 1 (0|1) in the same block → cis by reads.
    summary = summarize_second_hit(
        [_sv("DUP", {"child": "0|1"}, ps={"child": 1000})],
        ["child"],
        snv_gt_by_sample={"child": "0|1"},
        snv_ps_by_sample={"child": 1000},
    )
    assert summary["phase"] == "cis"
    assert summary["phase_evidence"] == "read"


def test_read_phase_skipped_for_different_phase_sets() -> None:
    # Different PS blocks can't be compared → fall back to segregation (here unknown).
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "1|0"}, ps={"child": 2000})],
        ["child"],
        unaffected_samples=[],
        snv_gt_by_sample={"child": "0|1"},
        snv_ps_by_sample={"child": 1000},
    )
    assert summary["phase"] == "unknown"
    assert summary["phase_evidence"] is None


def test_segregation_used_when_no_phasing() -> None:
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/1"},
    )
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "segregation"


def test_scan_groups_svs_by_gene(monkeypatch) -> None:
    rows = [
        # variantId, svType, chrom, start, end, gene_symbols, sampleIds, gts, ps
        ("sv1", "DEL", "1", 100, 200, ["BRCA2", "fgr"], ["S1", "S2"], ["0|1", "0/0"], [1000, None]),
        ("sv2", "DUP", "1", 300, 400, ["BRCA2"], ["S1"], ["1/1"], [None]),
    ]

    async def _fake_execute(query, params):  # noqa: ANN001
        assert params["family_guid"] == "u1"
        return rows

    monkeypatch.setattr(cfv, "execute_clickhouse", _fake_execute)
    context = types.SimpleNamespace(assembly_name="GRCh38", family_uuid="u1")
    gene_map, sv_total = asyncio.run(cfv._scan_family_sv_gene_map(context))

    assert sv_total == 2  # two distinct SVs
    assert set(gene_map) == {"BRCA2", "FGR"}  # gene symbols upper-cased
    assert len(gene_map["BRCA2"]) == 2
    assert gene_map["BRCA2"][0]["gt"] == {"S1": "0|1", "S2": "0/0"}
    assert gene_map["BRCA2"][0]["ps"] == {"S1": 1000}  # phase set captured, nulls dropped
    assert gene_map["FGR"][0]["sv_id"] == "sv1"
