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


def _pedigree(parents_of: dict[str, set[str]], males: set[str] | None = None):
    # Imported here so the rest of the module still runs where the pedigree type is absent.
    from backend.app.services.compound_het_phase import FamilyPedigree

    return FamilyPedigree(
        parents_of={child: frozenset(parents) for child, parents in parents_of.items()},
        males=frozenset(males or ()),
        assembly_name="GRCh38",
    )


TRIO = {"child": {"mother", "father"}}


def test_phase_trans_with_unaffected_parents() -> None:
    # Affected child het for the SNV and the DEL. The father carries the SNV and not the
    # DEL, the mother the DEL and not the SNV: one hit from each parent.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1", "father": "0/0"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/1"},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "trans"
    assert summary["deletion_unmasked"] is True  # DEL in trans with a het SNV → biallelic


def test_an_unaffected_relative_carrying_neither_hit_is_not_evidence_of_trans() -> None:
    # The defect: an unaffected sibling who carries neither hit says nothing about which
    # copy each hit is on, yet the verdict used to be trans (and the pair "biallelic").
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "sib": "0/0"})],
        ["child"],
        unaffected_samples=["sib"],
        snv_gt_by_sample={"child": "0/1", "sib": "0/0"},
    )
    assert summary["phase"] == "unknown"
    assert summary["phase_evidence"] is None
    assert summary["deletion_unmasked"] is False


def test_unaffected_members_without_a_pedigree_are_not_evidence_of_trans() -> None:
    # The same genotypes as the trio above, but nothing says these two are the child's
    # parents: carrying one hit each is only informative in a parent.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1", "father": "0/0"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/1"},
    )
    assert summary["phase"] == "unknown"
    assert summary["deletion_unmasked"] is False


def test_parents_carrying_neither_hit_leave_the_phase_unknown() -> None:
    # Both hits absent from both parents (de novo, or missed calls): either could sit on
    # either copy. This used to be trans because no unaffected member carried both.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/0", "father": "0/0"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/0"},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "unknown"
    assert summary["deletion_unmasked"] is False


def test_a_de_novo_deletion_is_not_placed_opposite_the_inherited_snv() -> None:
    # The mother passed on the SNV; neither parent carries the DEL, so it arose de novo
    # and may sit on the maternal copy as easily as on the paternal one.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/0", "father": "0/0"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/1", "father": "0/0"},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "unknown"
    assert summary["deletion_unmasked"] is False


def test_one_parent_carrying_exactly_one_hit_places_the_other_on_the_other_copy() -> None:
    # Only the mother is sequenced. She carries the SNV and has a reference call at the
    # DEL, so the child's DEL is on the paternal copy: trans.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/0"})],
        ["child"],
        unaffected_samples=["mother"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/1"},
        pedigree=_pedigree({"child": {"mother"}}),
    )
    assert summary["phase"] == "trans"
    assert summary["phase_evidence"] == "segregation"
    assert summary["deletion_unmasked"] is True


def test_a_parent_without_a_call_at_the_deletion_does_not_count_as_lacking_it() -> None:
    # Per-sample SV callsets: the mother has no call in the child's SV record, which is
    # not the same as a reference call, so nothing places the DEL.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1"})],
        ["child"],
        unaffected_samples=["mother"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/1"},
        pedigree=_pedigree({"child": {"mother"}}),
    )
    assert summary["phase"] == "unknown"


def test_a_shallow_parental_reference_call_is_not_evidence() -> None:
    # The mother's SNV reference call has 3 reads: too few to say she does not carry it.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1"})],
        ["child"],
        unaffected_samples=["mother"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0"},
        snv_dp_by_sample={"child": 30, "mother": 3},
        pedigree=_pedigree({"child": {"mother"}}),
    )
    assert summary["phase"] == "unknown"

    deep = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1"})],
        ["child"],
        unaffected_samples=["mother"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0"},
        snv_dp_by_sample={"child": 30, "mother": 30},
        pedigree=_pedigree({"child": {"mother"}}),
    )
    assert deep["phase"] == "trans"


def test_both_hits_from_one_parent_are_cis_when_both_parents_are_genotyped() -> None:
    # The mother (affection status not recorded) carries both hits and the father
    # neither: the child's two hits are both on the maternal copy.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1", "father": "0/0"})],
        ["child"],
        unaffected_samples=["father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/1", "father": "0/0"},
        pedigree=_pedigree(TRIO),
    )
    assert summary["phase"] == "cis"
    assert summary["phase_evidence"] == "segregation"
    assert summary["deletion_unmasked"] is False


def test_a_male_on_chrx_outside_the_pars_is_not_traced_through_two_parents() -> None:
    # A son has one X, from his mother: a father "carrying" the other hit cannot have
    # passed it to him, so the autosomal trace does not apply.
    sv = _sv("DEL", {"son": "0/1", "mother": "0/1", "father": "0/0"})
    sv.update({"chr": "X", "start": 50_000_000, "end": 50_010_000})
    summary = summarize_second_hit(
        [sv],
        ["son"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"son": "0/1", "mother": "0/0", "father": "0/1"},
        pedigree=_pedigree({"son": {"mother", "father"}}, males={"son", "father"}),
        snv_locus=("X", 50_005_000),
    )
    assert summary["phase"] == "unknown"


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
    # The father has no call in the DEL record. The mother's SNV reference call still
    # puts the child's SNV on the paternal copy, and the DEL is credited to the only
    # parent known to carry it, the mother: trans.
    summary = summarize_second_hit(
        [_sv("DEL", {"child": "0/1", "mother": "0/1"})],
        ["child"],
        unaffected_samples=["mother", "father"],
        snv_gt_by_sample={"child": "0/1", "mother": "0/0", "father": "0/1"},
        pedigree=_pedigree(TRIO),
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


class _Result:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def scalar(self):
        return self._rows[0][0] if self._rows else None

    def first(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return types.SimpleNamespace(all=lambda: [row[0] for row in self._rows])


class _IndexSession:
    """Just enough of an AsyncSession to run sv_gene_index_service's SQL in memory."""

    def __init__(self, built_from: dict[str, str | None] | None = None) -> None:
        # family_id -> the SV data version its index was built from (None: before the
        # column existed). A family absent here has no index.
        self.built_from: dict[str, str | None] = dict(built_from or {})
        self.genes: dict[str, set[str]] = {fid: {"STALE"} for fid in self.built_from}
        self.commits = 0

    async def execute(self, statement, params=None):  # noqa: ANN001
        sql = " ".join(str(statement).split())
        rows = params if isinstance(params, list) else [params or {}]
        fid = rows[0].get("fid") if rows else None
        if "pg_advisory_xact_lock" in sql:
            return _Result([])
        if sql.startswith("SELECT") and "FROM family_sv_gene_index_status" in sql:
            if fid not in self.built_from:
                return _Result([])
            return _Result([(self.built_from[fid],)] if "sv_data_version" in sql else [(1,)])
        if sql.startswith("DELETE FROM family_sv_gene_index_status"):
            self.built_from.pop(fid, None)
        elif sql.startswith("DELETE FROM family_sv_gene_index"):
            self.genes.pop(fid, None)
        elif sql.startswith("INSERT INTO family_sv_gene_index_status"):
            self.built_from[fid] = rows[0].get("sv_data_version")
        elif sql.startswith("INSERT INTO family_sv_gene_index"):
            self.genes.setdefault(fid, set()).update(row["gene_symbol"] for row in rows)
        elif sql.startswith("SELECT gene_symbol FROM family_sv_gene_index"):
            return _Result([(gene,) for gene in sorted(self.genes.get(fid, set()))])
        return _Result([])

    async def commit(self) -> None:
        self.commits += 1


def _ensure_index(monkeypatch, session: _IndexSession, *, current_version: str) -> dict:
    """Run the lazy index build against ``session`` with the SV data at ``current_version``."""
    scans = {"count": 0}

    async def fake_scan(context):  # noqa: ANN001
        scans["count"] += 1
        return {"BRCA2": [{"sv_id": "sv1", "sv_type": "DEL", "gt": {"S1": "0/1"}}]}, 1

    async def fake_version(assembly_name, family_uuid):  # noqa: ANN001
        assert (assembly_name, family_uuid) == ("GRCh38", "u1")
        return current_version

    async def fake_ensure_tables(assembly_name):  # noqa: ANN001
        return None

    monkeypatch.setattr(cfv, "_scan_family_sv_gene_map", fake_scan)
    monkeypatch.setattr(cfv, "ensure_clickhouse_variant_tables", fake_ensure_tables)
    monkeypatch.setattr(
        cfv, "get_family_structural_variant_data_version", fake_version, raising=False
    )
    context = types.SimpleNamespace(assembly_name="GRCh38", family_uuid="u1", family_id="F1")
    asyncio.run(cfv._ensure_family_sv_gene_index(session, context))
    return scans


def test_index_is_rebuilt_when_the_family_svs_changed_since_it_was_built(monkeypatch) -> None:
    # A per-sample SV upload or an admin SV delete moves the storage-level SV data
    # version without clearing the index; the next read must not serve the old one.
    session = _IndexSession(built_from={"u1": "1:111"})
    scans = _ensure_index(monkeypatch, session, current_version="2:333")
    assert scans["count"] == 1
    assert session.built_from["u1"] == "2:333"
    assert session.genes["u1"] == {"BRCA2"}  # the stale gene rows are gone


def test_index_built_from_the_current_svs_is_reused(monkeypatch) -> None:
    session = _IndexSession(built_from={"u1": "2:333"})
    scans = _ensure_index(monkeypatch, session, current_version="2:333")
    assert scans["count"] == 0
    assert session.genes["u1"] == {"STALE"}  # untouched


def test_index_built_before_versions_were_recorded_is_rebuilt(monkeypatch) -> None:
    session = _IndexSession(built_from={"u1": None})
    scans = _ensure_index(monkeypatch, session, current_version="0:0")
    assert scans["count"] == 1
    assert session.built_from["u1"] == "0:0"


def test_missing_index_is_built_and_stamped_with_the_version(monkeypatch) -> None:
    session = _IndexSession()
    scans = _ensure_index(monkeypatch, session, current_version="3:9")
    assert scans["count"] == 1
    assert session.built_from["u1"] == "3:9"
    assert session.genes["u1"] == {"BRCA2"}
