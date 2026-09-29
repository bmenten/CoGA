"""The evidence an SV/CNV classification rests on: frozen when the CNV scoring is saved,
compared with the SV as it is now (clinical traceability; TF-09a REQ-TRACE-014).

A CNV (ClinGen) classification is suggested from the SV record alone: its type, the genes
it overlaps and their count, the gene constraint (pLI) and the annotated inheritance. The
snapshot freezes those, the event they describe and a hash of the SV's whole annotation
record; the drift check reports a classification whose evidence moved, and the sign-out
gate counts it (test_report_signout.py).
"""

from __future__ import annotations

import asyncio
import json
import types
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from backend.app.schemas import CnvAcmgClassificationPayload, CnvAcmgCriterion, SmallVariantReviewUpdate
from backend.app.services import classification_drift_service as cds
from backend.app.services import structural_variant_evidence as sve
from backend.app.services import structural_variant_review_pg as svr
from backend.app.services.access_control import CurrentUser
from backend.app.services.clickhouse_variant_queries import _structural_variant_out
from backend.app.services.clickhouse_variant_records import StructuralVariantRecord
from backend.app.services.hash_chain import canonical_hash

_WHEN = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)
_VERSIONS = {"assembly": "GRCh38", "gencode": "45", "gene_loci": "GENCODE"}


def _record(**overrides) -> StructuralVariantRecord:
    info = {"Genes": "TCF4,TXNL1", "pLI": "0.99,0.41", "Inheritance": "de_novo", "OMIM_phenotype": "Pitt-Hopkins"}
    info.update(overrides.pop("info", {}))
    fields = {
        "variant_key": 7,
        "variant_id": "DEL-18-55000000-55400000",
        "chr": "18",
        "start": 55_000_000,
        "end": 55_400_000,
        "sv_type": "DEL",
        "source": "needlr",
        "remote_chr": None,
        "remote_start": None,
        "remote_end": None,
        "sv_len": -400_000,
        "filters": ["PASS"],
        "gene_symbols": ["TCF4", "TXNL1"],
        "annotations": [{"source": "needlr", "ref": "N", "alt": "<DEL>", "info": info}],
        "calls": [],
    }
    fields.update(overrides)
    return StructuralVariantRecord(**fields)


def _stored(snapshot: dict) -> dict:
    """The snapshot as it reads back from the JSONB column."""
    return json.loads(json.dumps(snapshot))


# --- what is frozen ----------------------------------------------------------------------------


def test_the_snapshot_freezes_the_inputs_the_cnv_classification_reads() -> None:
    snapshot = sve.build_structural_evidence_snapshot(_record(), _VERSIONS, captured_at=_WHEN)

    assert snapshot["evidence"] == {
        "source": "needlr",
        "sv_type": "DEL",
        "chrom": "18",
        "start": 55_000_000,
        "end": 55_400_000,
        "sv_len": -400_000,
        "remote_chrom": None,
        "remote_start": None,
        "gene_symbols": ["TCF4", "TXNL1"],
        "gene_count": 2,
        "pli": 0.99,
        "inheritance": "de_novo",
        "annotation_hash": canonical_hash(_record().annotations),
    }
    assert snapshot["evidence_hash"] == canonical_hash(snapshot["evidence"])
    assert snapshot["versions"] == _VERSIONS
    assert snapshot["captured_at"] == "2026-09-30T09:00:00+00:00"
    # Nothing the snapshot holds needs more than JSON (it lives in a JSONB column).
    assert _stored(snapshot) == snapshot


def test_the_frozen_values_are_the_ones_the_sv_page_gives_the_classification_dialog() -> None:
    # CnvAcmgClassificationModal reads type, gene/gene_symbols, gene_count, gene_pli and
    # annotation_extra.inheritance from the SV page's VariantOut.
    record = _record()
    served = _structural_variant_out(record, [])
    evidence = sve.structural_evidence(record)

    assert evidence["sv_type"] == served.type
    assert evidence["gene_symbols"] == served.gene_symbols
    assert evidence["gene_count"] == served.gene_count
    assert evidence["pli"] == served.gene_pli == served.annotation_extra["pli"]
    assert evidence["inheritance"] == served.annotation_extra["inheritance"]
    assert (evidence["chrom"], evidence["start"], evidence["end"]) == (served.chr, served.start, served.end)


def test_an_sv_without_constraint_or_inheritance_freezes_them_as_absent() -> None:
    record = _record(annotations=[{"info": {}}], gene_symbols=[], source="sniffles")
    evidence = sve.structural_evidence(record)
    assert (evidence["pli"], evidence["inheritance"]) == (None, None)
    assert (evidence["gene_symbols"], evidence["gene_count"]) == ([], 0)


def test_a_non_finite_pli_is_frozen_as_absent() -> None:
    # JSONB cannot hold NaN or infinity; an INFO value "nan" is no constraint score.
    evidence = sve.structural_evidence(_record(info={"pLI": "nan"}))
    assert evidence["pli"] is None
    json.dumps(evidence, allow_nan=False)


def test_the_versions_are_those_of_the_sv_callset_and_the_reference_it_uses() -> None:
    modules = [
        {"key": "assembly", "version": "GRCh38", "layer": "reference"},
        {"key": "deepvariant", "version": "1.6", "layer": "pipeline", "by_modality": {"snv": "1.6"}},
        {"key": "gencode", "version": "49", "layer": "pipeline", "by_modality": {"snv": "49", "sv": "45"}},
        {"key": "hificnv", "version": "1.0.1", "layer": "pipeline", "by_modality": {"pipeline": "1.0.1"}},
        {"key": "clinvar", "version": "2026-05", "layer": "pipeline", "by_modality": None},
        {"key": "gene_loci", "version": "GENCODE", "layer": "reference"},
        {"key": "monarch", "version": "2026-06", "layer": "reference"},
        {"key": "hpo", "version": "hp/releases/2026-06-06", "layer": "reference"},
    ]
    # The SV callset's own release wins over the one its SNVs were annotated with; the
    # SNV-only caller, a module no modality claims, Monarch and HPO feed no SV evidence.
    assert sve.structural_evidence_versions(modules) == {
        "assembly": "GRCh38",
        "gencode": "45",
        "hificnv": "1.0.1",
        "gene_loci": "GENCODE",
    }


# --- the comparison ----------------------------------------------------------------------------


def _frozen(record: StructuralVariantRecord | None = None) -> dict:
    return _stored(sve.build_structural_evidence_snapshot(record or _record(), _VERSIONS, captured_at=_WHEN))


def test_unchanged_evidence_is_current() -> None:
    diff = sve.diff_structural_evidence(_frozen(), _record())
    assert (diff["status"], diff["changed"]) == ("current", [])


@pytest.mark.parametrize(
    ("changed_record", "changed"),
    [
        (_record(gene_symbols=["TCF4"]), ["gene_symbols"]),
        (_record(info={"pLI": "0.12"}), ["pli", "annotations"]),
        (_record(info={"Inheritance": "maternal"}), ["inheritance", "annotations"]),
        (_record(info={"OMIM_phenotype": "none"}), ["annotations"]),
        (_record(end=55_600_000), ["locus"]),
        (_record(sv_type="DUP"), ["sv_type"]),
        (_record(source="hificnv"), ["source"]),
    ],
)
def test_changed_evidence_is_drifted_and_says_what_moved(changed_record, changed) -> None:
    diff = sve.diff_structural_evidence(_frozen(), changed_record)
    assert diff["status"] == "drifted"
    assert diff["changed"] == changed
    assert diff["evidence_from"]["gene_symbols"] == ["TCF4", "TXNL1"]
    assert diff["evidence_to"] == sve.structural_evidence(changed_record)


def test_an_sv_no_longer_in_the_data_is_missing() -> None:
    diff = sve.diff_structural_evidence(_frozen(), None)
    assert diff["status"] == "variant_missing"
    assert diff["evidence_from"]["gene_symbols"] == ["TCF4", "TXNL1"]
    assert diff["evidence_to"] is None


@pytest.mark.parametrize(
    "unreadable",
    [
        "{not json",
        ["a", "list"],
        {"evidence_hash": "h"},  # no evidence
        {"evidence": {"sv_type": "DEL"}},  # no hash
        {"evidence": "text", "evidence_hash": "h"},
    ],
)
def test_frozen_evidence_that_cannot_be_read_is_unknown_never_current(unreadable) -> None:
    # Unreadable is not unchanged: the gate must not pass it.
    assert sve.diff_structural_evidence(unreadable, _record())["status"] == "unknown"


def test_the_snapshot_as_the_driver_hands_back_json_text_still_compares() -> None:
    as_text = json.dumps(_frozen())
    assert sve.diff_structural_evidence(as_text, _record())["status"] == "current"


def test_evidence_a_later_version_adds_does_not_drift_an_older_classification() -> None:
    # Only the fields a snapshot froze are compared: an older snapshot without a field a
    # newer CoGA extracts still reads as current while what it froze is unchanged.
    older = _frozen()
    older["evidence"].pop("remote_start")
    older["evidence_hash"] = canonical_hash(older["evidence"])
    assert sve.diff_structural_evidence(older, _record())["status"] == "current"
    assert sve.diff_structural_evidence(older, _record(gene_symbols=["TCF4"]))["status"] == "drifted"


# --- the drift check over a family -----------------------------------------------------------------


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _Session:
    """Answers the structural_variant_reviews read; the small-variant read finds nothing."""

    def __init__(self, sv_rows):
        self.sv_rows = sv_rows

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        return _Rows(self.sv_rows if "FROM structural_variant_reviews" in sql else [])


_CONTEXT = types.SimpleNamespace(
    family_uuid="u1", family_id="FAM1", project_ids=["p1"], assembly_name="GRCh38"
)


def _review(variant_id: str, snapshot) -> dict:
    return {
        "variant_id": variant_id,
        "classification": "Pathogenic - class 5",
        "cnv_class": "cnv_class_5",
        "cnv_evidence_snapshot": snapshot,
        "updated_by": "alice",
        "updated_at": _WHEN,
    }


def _patch_current(monkeypatch, current: dict) -> list:
    looked_up: list = []

    async def _fetch(context, variant_id):
        looked_up.append((context.family_uuid, variant_id))
        return current.get(variant_id)

    monkeypatch.setattr(cds, "fetch_structural_variant_record", _fetch)
    return looked_up


def test_the_drift_check_reports_each_sv_classification_whose_evidence_moved(monkeypatch) -> None:
    rows = [
        _review("sv-unchanged", _frozen()),
        _review("sv-genes", _frozen()),
        _review("sv-gone", _frozen()),
        _review("sv-unreadable", "{not json"),
    ]
    looked_up = _patch_current(
        monkeypatch,
        {"sv-unchanged": _record(), "sv-genes": _record(gene_symbols=["TCF4"]), "sv-unreadable": _record()},
    )
    out = asyncio.run(cds.evaluate_structural_classification_drift(_Session(rows), _CONTEXT))

    assert out["checked"] == 4
    assert out["drifted_count"] == 3
    by_id = {item["variant_id"]: item for item in out["drifted"]}
    assert [item["variant_id"] for item in out["drifted"]] == sorted(by_id)
    assert by_id["sv-genes"]["status"] == "drifted" and by_id["sv-genes"]["changed"] == ["gene_symbols"]
    assert by_id["sv-gone"]["status"] == "variant_missing"
    assert by_id["sv-unreadable"]["status"] == "unknown"
    item = by_id["sv-genes"]
    assert (item["classification"], item["cnv_class"]) == ("Pathogenic - class 5", "cnv_class_5")
    assert (item["classified_by"], item["classified_at"]) == ("alice", _WHEN)
    # Each SV is read as the family's data holds it now.
    assert sorted(looked_up) == [("u1", v) for v in ("sv-genes", "sv-gone", "sv-unchanged", "sv-unreadable")]


def test_the_family_drift_carries_the_sv_classifications_beside_the_small_variants(monkeypatch) -> None:
    async def _ctx(session, *, family_identifier, user, project_id=None):
        return _CONTEXT

    monkeypatch.setattr(cds, "build_family_metadata_context", _ctx)
    _patch_current(monkeypatch, {"sv-genes": _record(gene_symbols=["TCF4"])})
    out = asyncio.run(
        cds.evaluate_classification_drift(_Session([_review("sv-genes", _frozen())]), family_id="FAM1", user=None)
    )
    assert (out["checked"], out["drifted_count"], out["drifted"]) == (0, 0, [])
    assert out["structural"]["drifted_count"] == 1
    assert out["structural"]["drifted"][0]["variant_id"] == "sv-genes"

    from backend.app.schemas import ClassificationDriftOut

    served = ClassificationDriftOut.model_validate(out)
    assert served.structural.drifted[0].changed == ["gene_symbols"]


# --- the save freezes it -------------------------------------------------------------------------

_FAMILY = "00000000-0000-0000-0000-00000000f001"
_SAVE_CONTEXT = types.SimpleNamespace(
    family_uuid=_FAMILY, family_id="FAM1", project_ids=["p1"], assembly_name="GRCh38"
)
_USER = CurrentUser(
    id="u1",
    username="reviewer",
    email="reviewer@example.org",
    role="viewer",
    created_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
)


class _Result:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _ReviewTable:
    """An in-memory structural_variant_reviews table behind the queries the save runs.

    Anything else the save writes (a clinical audit event) is accepted and ignored.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.steps: list[str] = []

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = params or {}
        if "pg_advisory_xact_lock" in sql and "strv:" in str(params.get("k", "strv:")):
            self.steps.append("LOCK")
        if sql.startswith("SELECT id::text AS id"):
            self.steps.append("READ")
            return _Result(self.rows.get(params["variant_id"]))
        if sql.startswith("INSERT INTO structural_variant_reviews"):
            self.steps.append("INSERT")
            self.rows[params["variant_id"]] = self._row(params, review_id=f"r-{len(self.rows) + 1}")
        elif sql.startswith("UPDATE structural_variant_reviews"):
            self.steps.append("UPDATE")
            existing = next(r for r in self.rows.values() if r["id"] == params["review_id"])
            self.rows[existing["variant_id"]] = self._row(params, review_id=existing["id"])
        elif sql.startswith("DELETE FROM structural_variant_reviews"):
            self.steps.append("DELETE")
            self.rows = {k: r for k, r in self.rows.items() if r["id"] != params["review_id"]}
        return _Result()

    @staticmethod
    def _row(params: dict, *, review_id: str) -> dict:
        return {
            "id": review_id,
            "variant_key": None,
            "variant_id": params["variant_id"],
            "classification": params["classification"],
            "tags": json.loads(params["tags_json"]),
            "tag_metadata": json.loads(params["tag_metadata_json"]),
            "note": params["note"],
            "cnv_acmg": json.loads(params["cnv_acmg_json"]) if params["cnv_acmg_json"] else None,
            "cnv_point_total": params["cnv_point_total"],
            "cnv_class": params["cnv_class"],
            "cnv_evidence_snapshot": (
                json.loads(params["cnv_evidence_snapshot_json"])
                if params["cnv_evidence_snapshot_json"]
                else None
            ),
            "updated_by": params["updated_by"],
            "updated_at": params["updated_at"],
        }

    async def commit(self) -> None:
        return None


def _scoring(*criteria: tuple[str, float, bool]) -> CnvAcmgClassificationPayload:
    return CnvAcmgClassificationPayload(
        kind="loss",
        criteria=[CnvAcmgCriterion(code=code, points=points, accepted=accepted) for code, points, accepted in criteria],
    )


def _patch_evidence(monkeypatch, table: _ReviewTable, record: StructuralVariantRecord | None) -> list:
    reads: list = []

    async def _fetch(context, variant_id):
        table.steps.append("EVIDENCE")
        reads.append(variant_id)
        return record

    async def _versions(session, *, context, user):
        return dict(_VERSIONS)

    monkeypatch.setattr(svr, "fetch_structural_variant_record", _fetch)
    monkeypatch.setattr(svr, "read_structural_evidence_versions", _versions)
    return reads


def _save(table: _ReviewTable, payload: SmallVariantReviewUpdate, *, context=_SAVE_CONTEXT, variant_id="sv1"):
    return asyncio.run(
        svr.upsert_structural_variant_review(
            table, context=context, variant_id=variant_id, payload=payload, user=_USER
        )
    )


def test_saving_a_cnv_scoring_freezes_the_evidence_it_rests_on(monkeypatch) -> None:
    table = _ReviewTable()
    reads = _patch_evidence(monkeypatch, table, _record())
    _save(table, SmallVariantReviewUpdate(cnv_acmg=_scoring(("2A", 1.0, True))))

    snapshot = table.rows["sv1"]["cnv_evidence_snapshot"]
    assert reads == ["sv1"]
    assert snapshot["evidence"] == sve.structural_evidence(_record())
    assert snapshot["evidence_hash"] == canonical_hash(sve.structural_evidence(_record()))
    assert snapshot["versions"] == _VERSIONS
    assert datetime.fromisoformat(snapshot["captured_at"]) == table.rows["sv1"]["updated_at"]
    # Read before the per-variant lock, as the small-variant save reads its variant.
    assert table.steps[:3] == ["EVIDENCE", "LOCK", "READ"]


def test_a_save_that_leaves_the_scoring_out_keeps_its_evidence_and_reads_nothing(monkeypatch) -> None:
    table = _ReviewTable()
    reads = _patch_evidence(monkeypatch, table, _record())
    _save(table, SmallVariantReviewUpdate(cnv_acmg=_scoring(("2A", 1.0, True))))
    frozen = table.rows["sv1"]["cnv_evidence_snapshot"]

    # The tag toggle and the review dialog send no cnv_acmg.
    _patch_evidence(monkeypatch, table, _record(gene_symbols=["TCF4"]))
    _save(table, SmallVariantReviewUpdate(note="checked the breakpoints"))

    assert table.rows["sv1"]["cnv_evidence_snapshot"] == frozen
    assert reads == ["sv1"]  # only the scoring save read the SV


def test_saving_the_scoring_again_freezes_the_evidence_as_it_is_now(monkeypatch) -> None:
    # Re-reviewing a drifted classification: saving its scoring again takes a new snapshot.
    table = _ReviewTable()
    _patch_evidence(monkeypatch, table, _record())
    _save(table, SmallVariantReviewUpdate(cnv_acmg=_scoring(("2A", 1.0, True))))
    _patch_evidence(monkeypatch, table, _record(gene_symbols=["TCF4"]))
    _save(table, SmallVariantReviewUpdate(cnv_acmg=_scoring(("2A", 1.0, True))))

    assert table.rows["sv1"]["cnv_evidence_snapshot"]["evidence"]["gene_symbols"] == ["TCF4"]


def test_clearing_the_scoring_clears_its_evidence(monkeypatch) -> None:
    table = _ReviewTable()
    _patch_evidence(monkeypatch, table, _record())
    _save(table, SmallVariantReviewUpdate(note="kept", cnv_acmg=_scoring(("2A", 1.0, True))))
    _save(table, SmallVariantReviewUpdate(note="kept", cnv_acmg=None))

    assert table.rows["sv1"]["cnv_acmg"] is None
    assert table.rows["sv1"]["cnv_evidence_snapshot"] is None


def test_a_scoring_of_an_sv_not_in_the_data_is_refused_and_writes_nothing(monkeypatch) -> None:
    table = _ReviewTable()
    _patch_evidence(monkeypatch, table, None)
    with pytest.raises(HTTPException) as refused:
        _save(table, SmallVariantReviewUpdate(cnv_acmg=_scoring(("2A", 1.0, True))))

    assert refused.value.status_code == 404
    assert table.rows == {}
    assert table.steps == ["EVIDENCE"]


def test_without_sv_storage_the_scoring_is_saved_without_evidence(monkeypatch) -> None:
    # A family with no assembly has no SV storage to read. The scoring is kept, with no
    # snapshot, so a sign-out counts it as unverified evidence.
    table = _ReviewTable()
    reads = _patch_evidence(monkeypatch, table, _record())
    no_assembly = types.SimpleNamespace(family_uuid=_FAMILY, family_id="FAM1", project_ids=["p1"], assembly_name=None)
    _save(table, SmallVariantReviewUpdate(cnv_acmg=_scoring(("2A", 1.0, True))), context=no_assembly)

    assert table.rows["sv1"]["cnv_class"] == "cnv_class_5"
    assert table.rows["sv1"]["cnv_evidence_snapshot"] is None
    assert reads == []
