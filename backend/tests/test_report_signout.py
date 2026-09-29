from __future__ import annotations

import asyncio
import json
import types
from datetime import datetime

import pytest
from fastapi import HTTPException

from backend.app.services import report_signout_service as rss
from backend.app.services.sample_integrity_qc import (
    MendelianCheck,
    NiptCategoryQc,
    PaternityCheck,
    RelatednessCheck,
    SampleIntegrityReport,
    SexCheck,
)


def test_canonical_hash_is_stable_and_order_independent() -> None:
    a = rss._canonical_hash({"x": 1, "y": [1, 2], "z": "t"})
    b = rss._canonical_hash({"z": "t", "y": [1, 2], "x": 1})
    assert a == b
    assert len(a) == 64  # sha256 hex
    assert a != rss._canonical_hash({"x": 2, "y": [1, 2], "z": "t"})


def _user():
    return types.SimpleNamespace(username="bjorn", email="b@x.org", id=None)


class _Result:
    def scalar_one(self):
        return 0  # no prior sign-out -> next version 1

    def scalar_one_or_none(self):
        return None  # no prior sign-out -> chain genesis (prev_hash is None)


class _Session:
    def __init__(self) -> None:
        self.executed: list = []

    async def execute(self, *args, **kwargs):
        self.executed.append((args, kwargs))
        return _Result()

    async def commit(self) -> None:
        return None


def _patch_common(
    monkeypatch, *, drifted_count: int, qc_status: str = "pass", import_incomplete=None
):
    async def _ctx(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_name="GRCh38")

    async def _manifest(session, *, family_id, user, project_id=None):
        return {"assembly": "GRCh38", "modules": [{"key": "clinvar", "version": "2026-05"}]}

    async def _drift(session, *, family_id, user, project_id=None):
        return {
            "checked": drifted_count,
            "drifted_count": drifted_count,
            "drifted": [{"variant_id": "x"}] * drifted_count,
        }

    async def _reviews(session, family_uuid):
        return [{"variant_id": "1-1-A-G", "acmg_class": "acmg_class_4", "evidence_snapshot": {"annotation_set_hash": "h"}}]

    async def _audit(*args, **kwargs):
        return None

    async def _qc(session, *, family_id, user, project_id=None):
        # Sample QC is computed inside build_report_snapshot now; stub it with a
        # parameterizable overall_status (default "pass" -> the QC gate stays open).
        return SampleIntegrityReport(overall_status=qc_status)

    async def _import_state(session, family_uuid):
        # The family's import_incomplete flag (default: a complete import).
        assert family_uuid == "u1"
        return import_incomplete

    monkeypatch.setattr(rss, "build_family_metadata_context", _ctx)
    monkeypatch.setattr(rss, "get_family_annotation_manifest", _manifest)
    monkeypatch.setattr(rss, "evaluate_classification_drift", _drift)
    monkeypatch.setattr(rss, "_reported_reviews", _reviews)
    monkeypatch.setattr(rss, "_reported_structural_reviews", _no_structural_reviews)
    monkeypatch.setattr(rss, "record_clinical_event", _audit)
    monkeypatch.setattr(rss, "get_family_sample_integrity_qc", _qc)
    monkeypatch.setattr(rss, "_import_incomplete_state", _import_state, raising=False)


async def _no_structural_reviews(session, family_uuid):
    return []


# ---------------------------------------------------------------------------
# Off-scope assembly gate (TF-06 H12, #515)
# ---------------------------------------------------------------------------


def _patch_off_scope(monkeypatch, assembly_name):
    _patch_common(monkeypatch, drifted_count=0)

    async def _ctx(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_name=assembly_name)

    async def _must_not_build(*args, **kwargs):
        raise AssertionError("an off-scope family must be refused before its snapshot is built")

    monkeypatch.setattr(rss, "build_family_metadata_context", _ctx)
    monkeypatch.setattr(rss, "build_report_snapshot", _must_not_build)


def _sign_out_with_every_override(session):
    # Every other gate acknowledged: the scope gate must not be one more thing to waive.
    return rss.sign_out_report(
        session,
        family_id="FAM1",
        user=_user(),
        acknowledge_drift=True,
        drift_acknowledgement_reason="reviewed",
        acknowledge_qc=True,
        qc_acknowledgement_reason="reviewed",
    )


def test_sign_out_refuses_a_family_on_an_unvalidated_assembly(monkeypatch) -> None:
    _patch_off_scope(monkeypatch, "T2T-CHM13v2.0")
    session = _Session()
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_sign_out_with_every_override(session))

    assert excinfo.value.status_code == 409
    detail = excinfo.value.detail
    # Structured, so the report page shows a refusal rather than the drift override.
    assert detail["gate"] == "assembly_scope"
    assert detail["assembly"] == "T2T-CHM13v2.0"
    assert detail["validated_assemblies"] == ["GRCh38"]
    assert "not validated for clinical use" in detail["message"]
    assert session.executed == [], "nothing may be written for a refused sign-out"


def test_sign_out_refuses_a_family_without_a_resolved_assembly(monkeypatch) -> None:
    _patch_off_scope(monkeypatch, None)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_sign_out_with_every_override(_Session()))
    assert excinfo.value.detail["gate"] == "assembly_scope"
    assert "no reference assembly linked" in excinfo.value.detail["message"]


def test_a_validated_assembly_is_configured_not_hard_coded(monkeypatch) -> None:
    # Extending the scope is a change-controlled configuration step (TF-18), once the
    # assembly has been validated; the gate follows the configuration.
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "validated_assemblies", ["GRCh38", "T2T-CHM13v2.0"])
    _patch_common(monkeypatch, drifted_count=0)

    async def _ctx(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_name="T2T-CHM13v2.0")

    monkeypatch.setattr(rss, "build_family_metadata_context", _ctx)
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert out["version"] == 1


def test_sign_out_blocks_unacknowledged_drift(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=2)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            rss.sign_out_report(
                _Session(), family_id="FAM1", user=_user(), acknowledge_drift=False
            )
        )
    assert excinfo.value.status_code == 409


def test_sign_out_proceeds_when_clean(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    out = asyncio.run(
        rss.sign_out_report(_Session(), family_id="FAM1", user=_user(), acknowledge_drift=False)
    )
    assert out["version"] == 1
    assert len(out["content_hash"]) == 64
    assert out["snapshot"]["modules"] == [{"key": "clinvar", "version": "2026-05"}]
    assert out["snapshot"]["acknowledged_drift"] is False


def test_sign_out_proceeds_with_acknowledged_drift(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=1)
    out = asyncio.run(
        rss.sign_out_report(
            _Session(),
            family_id="FAM1",
            user=_user(),
            acknowledge_drift=True,
            drift_acknowledgement_reason="  ClinVar update reviewed; class unchanged.  ",
        )
    )
    assert out["version"] == 1
    assert out["snapshot"]["acknowledged_drift"] is True
    # The reason is trimmed and frozen into the hashed snapshot and surfaced at top level.
    assert out["snapshot"]["drift_acknowledgement_reason"] == "ClinVar update reviewed; class unchanged."
    assert out["drift_acknowledged"] is True
    assert out["drift_acknowledgement_reason"] == "ClinVar update reviewed; class unchanged."
    assert out["snapshot"]["drift"]["drifted_count"] == 1


def test_reported_variant_without_snapshot_gates_sign_out(monkeypatch) -> None:
    # A reported classification with no frozen evidence snapshot can't be drift-verified,
    # so it must trip the drift gate (#332) rather than sign out unchallenged.
    async def _reviews_no_snapshot(session, family_uuid):
        return [{"variant_id": "9-9-A-G", "acmg_class": "acmg_class_4"}]  # no snapshot

    _patch_common(monkeypatch, drifted_count=0)
    monkeypatch.setattr(rss, "_reported_reviews", _reviews_no_snapshot)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert excinfo.value.status_code == 409
    assert "cannot be verified" in str(excinfo.value.detail)

    # Acknowledging the drift lets it proceed, and the no_snapshot entry is counted.
    _patch_common(monkeypatch, drifted_count=0)
    monkeypatch.setattr(rss, "_reported_reviews", _reviews_no_snapshot)
    out = asyncio.run(
        rss.sign_out_report(
            _Session(),
            family_id="FAM1",
            user=_user(),
            acknowledge_drift=True,
            drift_acknowledgement_reason="Legacy classification re-read.",
        )
    )
    assert out["snapshot"]["drift"]["drifted_count"] == 1
    assert out["snapshot"]["drift"]["drifted"][0]["status"] == "no_snapshot"
    assert out["snapshot"]["acknowledged_drift"] is True


def test_build_report_snapshot_freezes_software_identity(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    monkeypatch.setattr(rss.settings, "app_version", "1.2.3")
    monkeypatch.setattr(rss.settings, "git_sha", "abc1234def5")
    body = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    assert body["software"] == {"version": "1.2.3", "git_sha": "abc1234def5"}


def test_software_identity_is_bound_into_content_hash(monkeypatch) -> None:
    # Changing only the software identity changes the frozen content hash, proving the
    # signed report is cryptographically bound to the exact code that produced it.
    # Hashes the time-invariant snapshot BODY so the only varying input is the version.
    def _body_hash(version: str, sha: str) -> str:
        _patch_common(monkeypatch, drifted_count=0)
        monkeypatch.setattr(rss.settings, "app_version", version)
        monkeypatch.setattr(rss.settings, "git_sha", sha)
        body = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
        return rss._canonical_hash(body)

    assert _body_hash("1.0.0", "aaa") == _body_hash("1.0.0", "aaa")  # deterministic
    assert _body_hash("1.0.0", "aaa") != _body_hash("1.0.1", "aaa")  # bound to version
    assert _body_hash("1.0.0", "aaa") != _body_hash("1.0.0", "bbb")  # bound to git_sha


def test_software_falls_back_to_honest_unknown_sentinels(monkeypatch) -> None:
    # Unstamped build: the snapshot records the "unknown" sentinels (never blank/None),
    # so the hash stays deterministic and the report honestly states the code is unknown.
    _patch_common(monkeypatch, drifted_count=0)
    monkeypatch.setattr(rss.settings, "app_version", "0.0.0+unknown")
    monkeypatch.setattr(rss.settings, "git_sha", "unknown")
    out = asyncio.run(
        rss.sign_out_report(_Session(), family_id="FAM1", user=_user(), acknowledge_drift=False)
    )
    assert out["snapshot"]["software"] == {"version": "0.0.0+unknown", "git_sha": "unknown"}
    # The POST sign-out response surfaces the same frozen identity at top level as the
    # GET list/detail endpoints (which extract it from the JSONB snapshot).
    assert out["software_version"] == "0.0.0+unknown"
    assert out["git_sha"] == "unknown"


def test_serialize_signout_exposes_frozen_software_identity() -> None:
    # The list/footer path surfaces the JSONB-extracted frozen identity as top-level fields.
    row = {
        "version": 1,
        "signed_out_by": "x",
        "signed_out_at": None,
        "content_hash": "h",
        "software_version": "2.0.0",
        "git_sha": "deadbeef",
    }
    serialized = rss._serialize_signout(row)
    assert serialized["software_version"] == "2.0.0"
    assert serialized["git_sha"] == "deadbeef"


def test_build_report_snapshot_hash_is_drift_order_independent(monkeypatch) -> None:
    # P0-3: identical clinical content with drift rows arriving in different orders
    # (Postgres returns equal-updated_at rows arbitrarily) must hash identically. The
    # canonical hash sorts dict keys but not list element order, so the snapshot must
    # order the drift list by the unique variant_id.
    rows = [
        {"variant_id": "2-200-C-T", "acmg_class": "acmg_class_3"},
        {"variant_id": "1-100-A-G", "acmg_class": "acmg_class_4"},
    ]

    def _patch(drift_rows):
        async def _ctx(session, *, family_identifier, user, project_id=None):
            return types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_name="GRCh38")

        async def _manifest(session, *, family_id, user, project_id=None):
            return {
                "assembly": "GRCh38",
                "modules": [{"key": "clinvar", "version": "2026-05"}],
            }

        async def _drift(session, *, family_id, user, project_id=None):
            return {
                "checked": len(drift_rows),
                "drifted_count": len(drift_rows),
                "drifted": list(drift_rows),
            }

        async def _reviews(session, family_uuid):
            return [{"variant_id": "1-1-A-G", "acmg_class": "acmg_class_4", "evidence_snapshot": {"annotation_set_hash": "h"}}]

        async def _qc(session, *, family_id, user, project_id=None):
            return SampleIntegrityReport(overall_status="pass")

        monkeypatch.setattr(rss, "build_family_metadata_context", _ctx)
        monkeypatch.setattr(rss, "get_family_annotation_manifest", _manifest)
        monkeypatch.setattr(rss, "evaluate_classification_drift", _drift)
        monkeypatch.setattr(rss, "_reported_reviews", _reviews)
        monkeypatch.setattr(rss, "_reported_structural_reviews", _no_structural_reviews)
        monkeypatch.setattr(rss, "get_family_sample_integrity_qc", _qc)

    _patch(rows)
    snap_a = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    _patch(list(reversed(rows)))
    snap_b = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))

    assert rss._canonical_hash(snap_a) == rss._canonical_hash(snap_b)
    # The hashed snapshot orders the drift list by variant_id, not by input order.
    assert [d["variant_id"] for d in snap_b["drift"]["drifted"]] == [
        "1-100-A-G",
        "2-200-C-T",
    ]


def test_failing_sample_qc_blocks_sign_out(monkeypatch) -> None:
    # P1-2: a "fail" Sample QC (possible sample/pedigree swap, TF-06 H4 / S5) blocks
    # sign-out with a structured 409 carrying the gate discriminator + a failure summary.
    _patch_common(monkeypatch, drifted_count=0, qc_status="fail")
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert excinfo.value.status_code == 409
    detail = excinfo.value.detail
    assert detail["gate"] == "sample_qc"
    assert detail["qc_summary"]["overall_status"] == "fail"


def test_failing_sample_qc_acknowledged_without_reason_is_422(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0, qc_status="fail")
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            rss.sign_out_report(
                _Session(),
                family_id="FAM1",
                user=_user(),
                acknowledge_qc=True,
                qc_acknowledgement_reason="   ",  # whitespace-only == empty
            )
        )
    assert excinfo.value.status_code == 422


def test_failing_sample_qc_acknowledged_with_reason_proceeds(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0, qc_status="fail")
    out = asyncio.run(
        rss.sign_out_report(
            _Session(),
            family_id="FAM1",
            user=_user(),
            acknowledge_qc=True,
            qc_acknowledgement_reason="  Repeat genotyping confirms the samples.  ",
        )
    )
    assert out["version"] == 1
    assert out["snapshot"]["acknowledged_qc"] is True
    # The reason is stripped and frozen into the (content-hashed) record.
    assert out["snapshot"]["qc_acknowledgement_reason"] == "Repeat genotyping confirms the samples."
    assert out["snapshot"]["sample_qc"]["overall_status"] == "fail"


def test_non_failing_qc_signs_out_without_acknowledgement(monkeypatch) -> None:
    # Per policy only "fail" blocks; pass/warn/skip are frozen + surfaced but do not gate.
    for status in ("pass", "warn", "skip"):
        _patch_common(monkeypatch, drifted_count=0, qc_status=status)
        out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
        assert out["snapshot"]["acknowledged_qc"] is False
        assert out["snapshot"]["qc_acknowledgement_reason"] is None
        assert out["snapshot"]["sample_qc"]["overall_status"] == status


def test_drift_and_qc_gates_are_independent(monkeypatch) -> None:
    # Both failing: the drift gate fires first; acknowledging drift then exposes the QC
    # gate; acknowledging both (with a QC reason) proceeds.
    _patch_common(monkeypatch, drifted_count=2, qc_status="fail")
    with pytest.raises(HTTPException) as drift_exc:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert drift_exc.value.status_code == 409
    assert "classification" in str(drift_exc.value.detail)

    _patch_common(monkeypatch, drifted_count=2, qc_status="fail")
    with pytest.raises(HTTPException) as qc_exc:
        asyncio.run(
            rss.sign_out_report(
                _Session(),
                family_id="FAM1",
                user=_user(),
                acknowledge_drift=True,
                drift_acknowledgement_reason="drift reviewed",
            )
        )
    assert qc_exc.value.status_code == 409
    assert qc_exc.value.detail["gate"] == "sample_qc"

    _patch_common(monkeypatch, drifted_count=2, qc_status="fail")
    out = asyncio.run(
        rss.sign_out_report(
            _Session(),
            family_id="FAM1",
            user=_user(),
            acknowledge_drift=True,
            drift_acknowledgement_reason="drift reviewed",
            acknowledge_qc=True,
            qc_acknowledgement_reason="override reason",
        )
    )
    assert out["snapshot"]["acknowledged_drift"] is True
    assert out["snapshot"]["acknowledged_qc"] is True


def test_sample_qc_is_bound_into_content_hash(monkeypatch) -> None:
    # Changing only the QC verdict changes the frozen content hash, proving the signed
    # report is bound to what QC found. Hash the time-invariant snapshot BODY.
    def _body_hash(status: str) -> str:
        _patch_common(monkeypatch, drifted_count=0, qc_status=status)
        body = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
        assert body["sample_qc"]["overall_status"] == status
        return rss._canonical_hash(body)

    assert _body_hash("pass") == _body_hash("pass")  # deterministic
    assert _body_hash("pass") != _body_hash("fail")  # bound to the QC verdict


def _patch_qc_report(monkeypatch, report: SampleIntegrityReport) -> None:
    async def _qc(session, *, family_id, user, project_id=None):
        return report

    monkeypatch.setattr(rss, "get_family_sample_integrity_qc", _qc)


def _asserted_parent_child(sites: int, status: str = "warn") -> RelatednessCheck:
    return RelatednessCheck(
        "CHILD", "FATHER", "parent-child",
        "indeterminate" if sites < 1000 else "parent-child",
        0.0 if sites < 1000 else 0.25, 0.0, sites, status,
        "Too few shared sites to assess relatedness." if sites < 1000 else "Confirmed parent-child.",
    )


def test_unverifiable_asserted_relatedness_blocks_sign_out(monkeypatch) -> None:
    # #330: a swap that manifests as MISSING data leaves the parent-child relatedness
    # check unable to run (0 informative sites -> warn, not fail). It must still gate.
    _patch_common(monkeypatch, drifted_count=0, qc_status="warn")
    _patch_qc_report(
        monkeypatch,
        SampleIntegrityReport(overall_status="warn", relatedness_checks=[_asserted_parent_child(0)]),
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["gate"] == "sample_qc"
    assert excinfo.value.detail["unverifiable_checks"]  # names the asserted edge


def test_unverifiable_asserted_relatedness_acknowledged_proceeds_and_audits(monkeypatch) -> None:
    captured: dict = {}

    async def _capture(*args, **kwargs):
        captured.update(kwargs)

    _patch_common(monkeypatch, drifted_count=0, qc_status="warn")
    monkeypatch.setattr(rss, "record_clinical_event", _capture)
    _patch_qc_report(
        monkeypatch,
        SampleIntegrityReport(overall_status="warn", relatedness_checks=[_asserted_parent_child(0)]),
    )
    out = asyncio.run(
        rss.sign_out_report(
            _Session(), family_id="FAM1", user=_user(),
            acknowledge_qc=True, qc_acknowledgement_reason="  Repeat genotyping confirms identity.  ",
        )
    )
    assert out["snapshot"]["acknowledged_qc"] is True
    assert out["snapshot"]["qc_acknowledgement_reason"] == "Repeat genotyping confirms identity."
    # The audit distinguishes an unverifiable-check override (qc_status "warn" +
    # qc_unverifiable) from an override of a detected mismatch (qc_status "fail").
    assert captured["after"]["qc_status"] == "warn"
    assert captured["after"]["qc_unverifiable"]


def test_unverifiable_asserted_relatedness_ack_without_reason_is_422(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0, qc_status="warn")
    _patch_qc_report(
        monkeypatch,
        SampleIntegrityReport(overall_status="warn", relatedness_checks=[_asserted_parent_child(0)]),
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            rss.sign_out_report(
                _Session(), family_id="FAM1", user=_user(),
                acknowledge_qc=True, qc_acknowledgement_reason="   ",
            )
        )
    assert excinfo.value.status_code == 422


def test_non_asserted_and_verified_edges_do_not_block(monkeypatch) -> None:
    # A non-asserted ("unrelated") pair with no sites is NOT a swap-relevant assertion,
    # and a fully-verified asserted edge ran fine — neither should require acknowledgement.
    unrelated_no_sites = RelatednessCheck("A", "B", "unrelated", "indeterminate", 0.0, 0.0, 0, "warn", "x")
    for report in (
        SampleIntegrityReport(overall_status="warn", relatedness_checks=[unrelated_no_sites]),
        SampleIntegrityReport(overall_status="pass", relatedness_checks=[_asserted_parent_child(5000, status="pass")]),
    ):
        _patch_common(monkeypatch, drifted_count=0)
        _patch_qc_report(monkeypatch, report)
        out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
        assert out["snapshot"]["acknowledged_qc"] is False


def test_mendelian_unverifiable_blocks_but_elevated_rate_does_not(monkeypatch) -> None:
    # Too few informative sites = could-not-run -> gate. An elevated (but measured) error
    # rate over enough sites is a ran-and-concerning "warn" and must NOT newly gate.
    _patch_common(monkeypatch, drifted_count=0, qc_status="warn")
    _patch_qc_report(
        monkeypatch,
        SampleIntegrityReport(
            overall_status="warn",
            mendelian_checks=[MendelianCheck("CHILD", ["FATHER", "MOTHER"], 10, 0, 0.0, "warn", "Too few informative sites.")],
        ),
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert excinfo.value.status_code == 409

    _patch_common(monkeypatch, drifted_count=0, qc_status="warn")
    _patch_qc_report(
        monkeypatch,
        SampleIntegrityReport(
            overall_status="warn",
            mendelian_checks=[MendelianCheck("CHILD", ["FATHER", "MOTHER"], 5000, 150, 0.03, "warn", "Elevated Mendelian-error rate.")],
        ),
    )
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert out["snapshot"]["acknowledged_qc"] is False


def test_nipt_paternity_and_silent_maternal_pass_through_block(monkeypatch) -> None:
    # NIPT paternity that could not run gates.
    _patch_common(monkeypatch, drifted_count=0, qc_status="warn")
    _patch_qc_report(
        monkeypatch,
        SampleIntegrityReport(
            overall_status="warn",
            paternity_check=PaternityCheck("FATHER", 2, 1, 3, "warn", "Too few paternal-informative sites."),
        ),
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert excinfo.value.status_code == 409

    # The NIPT category QC reports "pass" when maternal transmission is skipped for too
    # few maternal-informative sites — an unverified mother that must now gate despite
    # the overall "pass" verdict.
    _patch_common(monkeypatch, drifted_count=0, qc_status="pass")
    _patch_qc_report(
        monkeypatch,
        SampleIntegrityReport(
            overall_status="pass",
            category_qc_check=NiptCategoryQc(0, 0, 5, 2, 0.4, "pass", "Category distribution within expectation."),
        ),
    )
    with pytest.raises(HTTPException) as excinfo2:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert excinfo2.value.status_code == 409
    assert excinfo2.value.detail["gate"] == "sample_qc"


def test_sex_only_identity_unverifiable_blocks(monkeypatch) -> None:
    # A single-sample / couple / added-relative member whose identity rests solely on the
    # sex check: an indeterminate sex with no anchoring relatedness edge must gate (the
    # fail-open the review found — _unverifiable_swap_checks previously ignored sex).
    _patch_common(monkeypatch, drifted_count=0, qc_status="warn")
    _patch_qc_report(
        monkeypatch,
        SampleIntegrityReport(
            overall_status="warn",
            application="single",
            sex_checks=[SexCheck("PROBAND", "female", "indeterminate", None, 0, "skip", "No chrX genotypes available.")],
        ),
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["gate"] == "sample_qc"


def test_indeterminate_sex_on_anchored_sample_does_not_block(monkeypatch) -> None:
    # The child's sex is indeterminate but a passing parent-child relatedness anchors its
    # identity, so sex is redundant and must NOT newly gate (no over-block on trios).
    _patch_common(monkeypatch, drifted_count=0, qc_status="warn")
    _patch_qc_report(
        monkeypatch,
        SampleIntegrityReport(
            overall_status="warn",
            application="wgs",
            relatedness_checks=[_asserted_parent_child(5000, status="pass")],
            sex_checks=[SexCheck("CHILD", "male", "indeterminate", None, 0, "skip", "No chrX genotypes available.")],
        ),
    )
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert out["snapshot"]["acknowledged_qc"] is False


def test_nipt_unresolved_trio_blocks(monkeypatch) -> None:
    # NIPT family whose trio could not be resolved -> zero cfDNA checks -> would sign out
    # with no integrity evidence at all. Must gate despite overall_status "skip".
    _patch_common(monkeypatch, drifted_count=0, qc_status="skip")
    _patch_qc_report(monkeypatch, SampleIntegrityReport(overall_status="skip", application="nipt"))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["gate"] == "sample_qc"


def test_nipt_with_ran_checks_signs_out(monkeypatch) -> None:
    # A NIPT report whose paternity + category checks ran fine must NOT be gated by the
    # no-evidence branch (no false positive).
    _patch_common(monkeypatch, drifted_count=0, qc_status="pass")
    _patch_qc_report(
        monkeypatch,
        SampleIntegrityReport(
            overall_status="pass",
            application="nipt",
            paternity_check=PaternityCheck("FATHER", 40, 5, 45, "pass", "Paternity supported."),
            category_qc_check=NiptCategoryQc(1, 2, 60, 30, 0.5, "pass", "Within expectation."),
        ),
    )
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert out["snapshot"]["acknowledged_qc"] is False


def test_audit_records_qc_status_and_acknowledgement(monkeypatch) -> None:
    captured: dict = {}

    async def _capture(*args, **kwargs):
        captured.update(kwargs)

    _patch_common(monkeypatch, drifted_count=0, qc_status="fail")
    monkeypatch.setattr(rss, "record_clinical_event", _capture)
    asyncio.run(
        rss.sign_out_report(
            _Session(),
            family_id="FAM1",
            user=_user(),
            acknowledge_qc=True,
            qc_acknowledgement_reason="repeat genotyping confirms identity",
        )
    )
    after = captured["after"]
    assert after["qc_status"] == "fail"
    assert after["acknowledged_qc"] is True
    assert after["qc_acknowledgement_reason"] == "repeat genotyping confirms identity"
    assert ", QC override acknowledged" in captured["summary"]


# ---------------------------------------------------------------------------
# Sequencing QC and the cut-offs it was judged against
# ---------------------------------------------------------------------------


def _qc_context() -> object:
    return types.SimpleNamespace(
        family_uuid="u1",
        family_id="FAM1",
        sample_rows=[
            {
                "sample_id": "HG002",
                # The context query aliases samples.metadata to `sample_metadata`;
                # reading the wrong key here yields an empty map and no error.
                "sample_metadata": {"sequencing_qc": {"depth": {"mean_depth": 18.57}}},
            },
            {"sample_id": "NOQC", "sample_metadata": {}},
        ],
    )


def test_snapshot_freezes_the_cut_offs_the_qc_verdict_was_judged_against(monkeypatch) -> None:
    async def _resolve(session, *, family_uuid):
        return {
            "profile_key": "long_read_wgs",
            "profile_label": "Long-read WGS",
            "thresholds": {
                "depth.mean_depth": {"warn_value": 20.0, "error_value": 10.0},
            },
        }

    from backend.app.services import qc_threshold_service as qts

    monkeypatch.setattr(qts, "resolve_family_qc_thresholds", _resolve)

    frozen = asyncio.run(rss._canonical_sequencing_qc(_Session(), _qc_context()))

    # The chip a reviewer saw said "warning" *relative to limits that can be changed
    # afterwards*. Without these, a signed report cannot say what its own QC display
    # meant at the time.
    assert frozen["profile_key"] == "long_read_wgs"
    assert frozen["thresholds"] == {"depth.mean_depth": {"warn_value": 20.0, "error_value": 10.0}}
    assert "HG002" in frozen["samples"], "the sample's QC verdict must be frozen too"
    # A sample with no recorded QC contributes nothing rather than a fabricated pass.
    assert "NOQC" not in frozen["samples"]
    # Only a failed lookup carries the marker, so an ordinary snapshot hashes as before.
    assert "unavailable" not in frozen


def test_snapshot_sequencing_qc_survives_an_unresolvable_profile(monkeypatch) -> None:
    async def _explode(session, *, family_uuid):
        raise RuntimeError("clickhouse is down")

    from backend.app.services import qc_threshold_service as qts

    monkeypatch.setattr(qts, "resolve_family_qc_thresholds", _explode)

    frozen = asyncio.run(rss._canonical_sequencing_qc(_Session(), _qc_context()))

    # QC display is advisory; it has never been allowed to break sign-out. But the
    # frozen block must say the cut-offs could not be read: empty on its own is the
    # shape of a family imported without QC outputs (#514).
    assert frozen == {
        "profile_key": None,
        "profile_label": None,
        "thresholds": {},
        "samples": {},
        "unavailable": "QC thresholds could not be resolved",
    }


_QC_GAP = {
    "section": "sequencing_qc",
    "item": "Sequencing-QC cut-offs",
    "reason": "QC thresholds could not be resolved",
}


def test_snapshot_gaps_list_what_the_record_could_not_capture() -> None:
    snapshot = {
        "modules": [
            {"key": "clinvar", "label": "ClinVar", "version": "2026-05"},
            {"key": "monarch", "label": "Monarch", "version": "unavailable", "detail": "lookup failed"},
        ],
        "sequencing_qc": {"thresholds": {}, "samples": {}, "unavailable": "QC thresholds could not be resolved"},
    }
    assert rss.snapshot_gaps(snapshot) == [
        _QC_GAP,
        {"section": "modules", "item": "Monarch", "reason": "lookup failed"},
    ]
    complete = {"modules": [{"key": "clinvar", "version": "2026-05"}], "sequencing_qc": {"thresholds": {}, "samples": {}}}
    assert rss.snapshot_gaps(complete) == []
    assert rss.snapshot_gaps(None) == []


def _capture_audit(monkeypatch) -> dict:
    captured: dict = {}

    async def _capture(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(rss, "record_clinical_event", _capture)
    return captured


def _patch_qc_thresholds(monkeypatch, resolver) -> None:
    from backend.app.services import qc_threshold_service as qts

    monkeypatch.setattr(qts, "resolve_family_qc_thresholds", resolver)


def test_sign_out_audit_records_what_the_snapshot_could_not_capture(monkeypatch) -> None:
    async def _explode(session, *, family_uuid):
        raise RuntimeError("clickhouse is down")

    _patch_common(monkeypatch, drifted_count=0)
    _patch_qc_thresholds(monkeypatch, _explode)
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))

    # The sign-out still happens, but the trail says the record is incomplete.
    assert out["snapshot"]["sequencing_qc"]["unavailable"] == "QC thresholds could not be resolved"
    assert captured["after"]["not_captured"] == [_QC_GAP]
    assert "1 part(s) not captured" in captured["summary"]


def test_a_complete_sign_out_records_no_gaps(monkeypatch) -> None:
    async def _complete_qc(session, context):
        return {"profile_key": "wgs", "profile_label": "WGS", "thresholds": {}, "samples": {}}

    _patch_common(monkeypatch, drifted_count=0)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _complete_qc)
    captured = _capture_audit(monkeypatch)
    asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))

    assert captured["after"]["not_captured"] == []
    assert "not captured" not in captured["summary"]


def test_acknowledging_drift_without_a_reason_is_422(monkeypatch) -> None:
    # #508: overriding the evidence-drift gate is attested like the QC override.
    _patch_common(monkeypatch, drifted_count=1)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            rss.sign_out_report(
                _Session(),
                family_id="FAM1",
                user=_user(),
                acknowledge_drift=True,
                drift_acknowledgement_reason="   ",  # whitespace-only == empty
            )
        )
    assert excinfo.value.status_code == 422
    assert "reason" in str(excinfo.value.detail)


def test_no_drift_needs_no_drift_reason(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    out = asyncio.run(
        rss.sign_out_report(_Session(), family_id="FAM1", user=_user(), acknowledge_drift=True)
    )
    assert out["snapshot"]["acknowledged_drift"] is False
    assert out["snapshot"]["drift_acknowledgement_reason"] is None


def test_reported_structural_variants_are_frozen_and_hashed(monkeypatch) -> None:
    # #508: the report page renders structural variants tagged for reporting, so the
    # signed snapshot must hold them — before, a reported CNV was printed but not signed.
    def _body(structural):
        async def _sv_reviews(session, family_uuid):
            return structural

        _patch_common(monkeypatch, drifted_count=0)
        monkeypatch.setattr(rss, "_reported_structural_reviews", _sv_reviews)
        return asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))

    cnv = {
        "variant_id": "DEL-1-1000-2000",
        "variant_key": 7,
        "classification": "pathogenic",
        "cnv_class": "Pathogenic",
        "cnv_point_total": 1.0,
        "cnv_acmg": {"criteria": []},
        "tags": ["report"],
        "note": None,
    }
    with_cnv = _body([cnv])
    without = _body([])
    assert with_cnv["reported_structural_variants"] == [cnv]
    assert without["reported_structural_variants"] == []
    assert rss._canonical_hash(with_cnv) != rss._canonical_hash(without)


class _CheckResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _CheckSession:
    """Answers the latest-sign-out lookup with a fixed row (or none)."""

    def __init__(self, row) -> None:
        self._row = row

    async def execute(self, *args, **kwargs):
        return _CheckResult(self._row)


def _signed_row(snapshot: dict) -> dict:
    import json as _json

    # Stored the way sign_out_report stores it: JSON-encoded, read back from JSONB.
    return {"version": 3, "content_hash": "h" * 64, "snapshot": _json.loads(_json.dumps(snapshot, default=str))}


def _patch_check(monkeypatch, *, reviews):
    _patch_common(monkeypatch, drifted_count=0)

    async def _reviews(session, family_uuid):
        return reviews

    async def _sequencing_qc(session, context):
        return {"profile_key": None, "profile_label": None, "thresholds": {}, "samples": {}}

    monkeypatch.setattr(rss, "_reported_reviews", _reviews)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _sequencing_qc)


_REVIEW = {"variant_id": "1-1-A-G", "acmg_class": "acmg_class_4", "evidence_snapshot": {"annotation_set_hash": "h"}}


def test_signout_check_without_any_signout(monkeypatch) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(None), family_id="FAM1", user=_user())
    )
    assert out["version"] is None and out["matches"] is None
    assert out["changed_sections"] == []
    assert out["not_captured"] == []


def test_signout_check_matches_unchanged_content(monkeypatch) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    # Per-sign-out fields and a newer build identity are not report content.
    signed = {**signed, "version": 3, "signed_out_by": "someone", "generated_at": "2026-09-01T10:00:00+00:00"}
    signed["software"] = {"version": "0.0.9", "git_sha": "old"}
    out = asyncio.run(
        rss.compare_report_with_latest_signout(
            _CheckSession(_signed_row(signed)), family_id="FAM1", user=_user()
        )
    )
    assert out["version"] == 3
    assert out["matches"] is True
    assert out["changed_sections"] == []
    assert out["not_compared"] == []
    assert out["not_captured"] == []


def test_signout_check_names_what_the_signed_record_could_not_capture(monkeypatch) -> None:
    # #514: QC thresholds could not be read at sign-out, so the signed record froze an
    # explicit marker. The report page must say so beside the signed banner.
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    signed["sequencing_qc"] = {**signed["sequencing_qc"], "unavailable": "QC thresholds could not be resolved"}
    out = asyncio.run(
        rss.compare_report_with_latest_signout(
            _CheckSession(_signed_row(signed)), family_id="FAM1", user=_user()
        )
    )
    assert out["not_captured"] == [_QC_GAP]
    # The thresholds resolve now, so the live section differs from the signed one.
    assert out["changed_sections"] == ["sequencing_qc"]


def test_signout_check_flags_a_review_changed_after_signout(monkeypatch) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    # After sign-out, the analyst reclassifies the reported variant.
    _patch_check(monkeypatch, reviews=[{**_REVIEW, "acmg_class": "acmg_class_5"}])
    out = asyncio.run(
        rss.compare_report_with_latest_signout(
            _CheckSession(_signed_row(signed)), family_id="FAM1", user=_user()
        )
    )
    assert out["matches"] is False
    assert out["changed_sections"] == ["reported_variants"]


def test_signout_check_older_snapshot_sections(monkeypatch) -> None:
    # A sign-out made before sections were frozen: absent reported SVs mean none were
    # signed (so a CNV reported since then is a change); other absent sections are not
    # comparable rather than "changed".
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    for key in ("reported_structural_variants", "sequencing_qc"):
        signed.pop(key)
    row = _signed_row(signed)

    out = asyncio.run(rss.compare_report_with_latest_signout(_CheckSession(row), family_id="FAM1", user=_user()))
    assert out["matches"] is True
    assert out["not_compared"] == ["sequencing_qc"]

    async def _one_cnv(session, family_uuid):
        return [{"variant_id": "DEL-1-1000-2000", "tags": ["report"]}]

    monkeypatch.setattr(rss, "_reported_structural_reviews", _one_cnv)
    out = asyncio.run(rss.compare_report_with_latest_signout(_CheckSession(row), family_id="FAM1", user=_user()))
    assert out["matches"] is False
    assert out["changed_sections"] == ["reported_structural_variants"]


# ---------------------------------------------------------------------------
# Incomplete-import gate: a family whose package import partly failed
# ---------------------------------------------------------------------------

# As family_package_registration._flag_family_import_incomplete records it.
_INCOMPLETE = {
    "at": "2026-09-12T10:14:00+00:00",
    "failed_datasets": ["snv", "sv"],
    "imported_datasets": ["coverage"],
}
_IMPORT_ACK_REASON = "SV calls are not part of this referral; SNV re-import is booked."


def _inserts(session: _Session) -> list:
    return [args for args, _ in session.executed if args and "INSERT" in str(args[0])]


def test_incomplete_import_blocks_sign_out(monkeypatch) -> None:
    # A partly failed import leaves the family in place, flagged. Signing it out would
    # release a report that may silently lack datasets, so it is refused like a failing
    # Sample QC: a structured 409 naming what the import left incomplete.
    _patch_common(monkeypatch, drifted_count=0, import_incomplete=_INCOMPLETE)
    captured = _capture_audit(monkeypatch)
    session = _Session()
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(session, family_id="FAM1", user=_user()))

    assert excinfo.value.status_code == 409
    detail = excinfo.value.detail
    assert detail["gate"] == "import_incomplete"
    assert detail["import_incomplete"] == _INCOMPLETE
    assert "snv" in detail["message"] and "sv" in detail["message"]
    assert "coverage" in detail["message"]
    assert _inserts(session) == [], "nothing may be written for a refused sign-out"
    assert captured == {}, "nor audited"


def test_incomplete_import_acknowledged_without_reason_is_422(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0, import_incomplete=_INCOMPLETE)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            rss.sign_out_report(
                _Session(),
                family_id="FAM1",
                user=_user(),
                acknowledge_import_incomplete=True,
                import_incomplete_acknowledgement_reason="   ",  # whitespace-only == empty
            )
        )
    assert excinfo.value.status_code == 422
    assert "reason" in str(excinfo.value.detail)


def test_acknowledged_incomplete_import_is_frozen_and_audited(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0, import_incomplete=_INCOMPLETE)
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(
        rss.sign_out_report(
            _Session(),
            family_id="FAM1",
            user=_user(),
            acknowledge_import_incomplete=True,
            import_incomplete_acknowledgement_reason=f"  {_IMPORT_ACK_REASON}  ",
        )
    )

    snapshot = out["snapshot"]
    # What the import left incomplete, and the attested reason, are inside the hashed
    # record — not added beside it.
    assert snapshot["import_incomplete"] == _INCOMPLETE
    assert snapshot["acknowledged_import_incomplete"] is True
    assert snapshot["import_incomplete_acknowledgement_reason"] == _IMPORT_ACK_REASON
    assert out["content_hash"] == rss._canonical_hash(snapshot)
    # Surfaced at top level, as the list/detail endpoints do.
    assert out["import_incomplete_acknowledged"] is True
    assert out["import_incomplete_acknowledgement_reason"] == _IMPORT_ACK_REASON
    # And in the clinical audit event, as the QC override is.
    after = captured["after"]
    assert after["import_incomplete"] == _INCOMPLETE
    assert after["acknowledged_import_incomplete"] is True
    assert after["import_incomplete_acknowledgement_reason"] == _IMPORT_ACK_REASON
    assert ", incomplete import acknowledged" in captured["summary"]


def test_a_complete_import_needs_no_acknowledgement(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0, import_incomplete=None)
    captured = _capture_audit(monkeypatch)
    # An acknowledgement with nothing to acknowledge records nothing.
    out = asyncio.run(
        rss.sign_out_report(
            _Session(), family_id="FAM1", user=_user(), acknowledge_import_incomplete=True
        )
    )
    assert out["snapshot"]["import_incomplete"] is None
    assert out["snapshot"]["acknowledged_import_incomplete"] is False
    assert out["snapshot"]["import_incomplete_acknowledgement_reason"] is None
    assert out["import_incomplete_acknowledged"] is False
    assert captured["after"]["import_incomplete"] is None
    assert captured["after"]["acknowledged_import_incomplete"] is False
    assert "incomplete import" not in captured["summary"]


def test_the_import_gate_is_acknowledged_independently_of_drift_and_qc(monkeypatch) -> None:
    # All three concerns at once. The gates fire in order — evidence drift, Sample QC,
    # then the incomplete import — and each needs its own acknowledgement with a reason:
    # acknowledging one never waives another.
    drift = {"acknowledge_drift": True, "drift_acknowledgement_reason": "drift reviewed"}
    qc = {"acknowledge_qc": True, "qc_acknowledgement_reason": "identity confirmed"}
    imp = {
        "acknowledge_import_incomplete": True,
        "import_incomplete_acknowledgement_reason": _IMPORT_ACK_REASON,
    }

    def attempt(**acknowledgements):
        _patch_common(
            monkeypatch, drifted_count=1, qc_status="fail", import_incomplete=_INCOMPLETE
        )
        return asyncio.run(
            rss.sign_out_report(_Session(), family_id="FAM1", user=_user(), **acknowledgements)
        )

    with pytest.raises(HTTPException) as only_import:
        attempt(**imp)
    assert "classification" in str(only_import.value.detail)  # the drift gate
    with pytest.raises(HTTPException) as import_and_drift:
        attempt(**imp, **drift)
    assert import_and_drift.value.detail["gate"] == "sample_qc"
    with pytest.raises(HTTPException) as drift_and_qc:
        attempt(**drift, **qc)
    assert drift_and_qc.value.detail["gate"] == "import_incomplete"

    out = attempt(**drift, **qc, **imp)
    assert out["snapshot"]["acknowledged_drift"] is True
    assert out["snapshot"]["acknowledged_qc"] is True
    assert out["snapshot"]["acknowledged_import_incomplete"] is True


def test_the_import_state_is_bound_into_the_content_hash(monkeypatch) -> None:
    def _body_hash(flag) -> str:
        _patch_common(monkeypatch, drifted_count=0, import_incomplete=flag)
        body = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
        assert body["import_incomplete"] == flag
        return rss._canonical_hash(body)

    assert _body_hash(None) == _body_hash(None)  # deterministic
    assert _body_hash(None) != _body_hash(_INCOMPLETE)
    assert _body_hash(_INCOMPLETE) != _body_hash({**_INCOMPLETE, "failed_datasets": ["snv"]})


class _ScalarSession:
    """Answers the one-value lookup of the family's import_incomplete flag."""

    def __init__(self, value) -> None:
        self.value = value
        self.executed: list = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        value = self.value
        return types.SimpleNamespace(scalar_one_or_none=lambda: value)


def test_the_import_flag_is_read_from_the_family_metadata() -> None:
    session = _ScalarSession(dict(_INCOMPLETE))
    assert asyncio.run(rss._import_incomplete_state(session, "u1")) == _INCOMPLETE
    sql, params = session.executed[0]
    assert "import_incomplete" in sql and "families" in sql
    assert params == {"family_uuid": "u1"}  # bound, never interpolated


def test_the_import_flag_reader_is_deterministic_and_fails_safe() -> None:
    def read(value):
        return asyncio.run(rss._import_incomplete_state(_ScalarSession(value), "u1"))

    # No flag: a complete import.
    assert read(None) is None
    # A driver that hands jsonb back as text reads the same.
    assert read(json.dumps(_INCOMPLETE)) == _INCOMPLETE
    # Dataset lists are frozen sorted and de-duplicated, so the hash is stable.
    messy = {"at": _INCOMPLETE["at"], "failed_datasets": ["sv", "snv", "sv"], "imported_datasets": []}
    assert read(messy)["failed_datasets"] == ["snv", "sv"]
    # Set, but not in the shape the import writes: still incomplete — it gates, with
    # nothing to name — rather than being taken for a complete import.
    unknown = {"at": None, "failed_datasets": [], "imported_datasets": []}
    assert read(True) == unknown
    assert read({}) == unknown
    assert read("not json") == unknown


# A sign-out exactly as the writer stored it before the incomplete-import gate existed,
# captured from that writer: its snapshot has none of the gate's keys, and the content
# hash and row hash are the ones it computed then. A signed record is never re-hashed,
# so it must keep verifying as stored — its missing keys are not a tampering finding.
_PRE_GATE_SIGNED_AT = "2026-09-29T17:43:01.402810+00:00"
_PRE_GATE_SNAPSHOT = {
    "acknowledged_drift": False,
    "acknowledged_qc": False,
    "assembly": "GRCh38",
    "drift": {"checked": 1, "drifted": [], "drifted_count": 0},
    "drift_acknowledgement_reason": None,
    "family_id": "FAM1",
    "generated_at": _PRE_GATE_SIGNED_AT,
    "modules": [{"key": "clinvar", "version": "2026-05"}],
    "qc_acknowledgement_reason": None,
    "reported_structural_variants": [],
    "reported_variants": [
        {
            "acmg": None,
            "acmg_class": "acmg_class_4",
            "evidence_snapshot": {"annotation_set_hash": "h"},
            "note": None,
            "tags": ["report"],
            "variant_id": "1-1-A-G",
        }
    ],
    "sample_qc": {
        "application": "unknown",
        "application_label": "",
        "application_summary": "",
        "autosomal_sites": 0,
        "category_qc_check": None,
        "fetal_sex_check": None,
        "genotype_source": None,
        "mendelian_checks": [],
        "notes": [],
        "overall_status": "pass",
        "paternity_check": None,
        "relatedness_checks": [],
        "sex_checks": [],
    },
    "sequencing_qc": {"profile_key": "wgs", "profile_label": "WGS", "samples": {}, "thresholds": {}},
    "signed_out_by": "bjorn",
    "software": {"git_sha": "0123abc", "version": "1.4.0"},
    "version": 1,
}
_PRE_GATE_CONTENT_HASH = "327b79f5eb7a2822cb54a0ad05a92b0898d40046b09c130da2cb2888ba2ea7fc"
_PRE_GATE_ROW_HASH = "368ac39365bd791b1a0d93ad4dd26b680b46566f22a948c7a9c014a2c2bc9493"


class _RowsSession:
    """Answers the sign-out detail and chain reads with fixed rows."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def execute(self, *args, **kwargs):
        rows = self.rows
        return types.SimpleNamespace(
            mappings=lambda: types.SimpleNamespace(
                first=lambda: rows[0] if rows else None, all=lambda: list(rows)
            )
        )


def _pre_gate_row() -> dict:
    return {
        "version": 1,
        "id": "1",
        "signed_out_by": "bjorn",
        "signed_out_at": datetime.fromisoformat(_PRE_GATE_SIGNED_AT),
        "content_hash": _PRE_GATE_CONTENT_HASH,
        "family_identifier": "FAM1",
        # Read back from JSONB the way it was written.
        "snapshot": json.loads(json.dumps(_PRE_GATE_SNAPSHOT)),
        "row_hash": _PRE_GATE_ROW_HASH,
        "prev_hash": None,
        # What Postgres extracts from a snapshot that lacks the keys: NULL.
        "software_version": "1.4.0",
        "git_sha": "0123abc",
        "qc_status": "pass",
        "qc_acknowledged": False,
        "qc_acknowledgement_reason": None,
        "drift_acknowledged": False,
        "drift_acknowledgement_reason": None,
        "import_incomplete_failed_datasets": None,
        "import_incomplete_acknowledged": None,
        "import_incomplete_acknowledgement_reason": None,
    }


def test_a_sign_out_made_before_the_import_gate_still_verifies(monkeypatch) -> None:
    gate_keys = {
        "import_incomplete",
        "acknowledged_import_incomplete",
        "import_incomplete_acknowledgement_reason",
    }
    assert not gate_keys & set(_PRE_GATE_SNAPSHOT)
    # The hash the old writer computed is what the verifier computes over the record as
    # stored: nothing adds the new keys (or a default for them) before hashing.
    assert rss._canonical_hash(_pre_gate_row()["snapshot"]) == _PRE_GATE_CONTENT_HASH

    chain = asyncio.run(rss.verify_report_signout_chain(_RowsSession([_pre_gate_row()]), "FAM1"))
    assert chain.verified and chain.rows_checked == 1, chain

    _patch_common(monkeypatch, drifted_count=0)
    detail = asyncio.run(
        rss.get_report_signout(
            _RowsSession([_pre_gate_row()]), family_id="FAM1", version=1, user=_user()
        )
    )
    assert detail["verified"] is True
    # It predates the gate: no acknowledgement is claimed either way.
    assert detail["import_incomplete_acknowledged"] is None
    assert detail["import_incomplete_acknowledgement_reason"] is None
    assert detail["import_incomplete_failed_datasets"] is None


def test_serialize_signout_exposes_the_frozen_import_acknowledgement() -> None:
    row = {
        "version": 2,
        "signed_out_by": "x",
        "signed_out_at": None,
        "content_hash": "h",
        "import_incomplete_failed_datasets": ["snv", "sv"],
        "import_incomplete_acknowledged": True,
        "import_incomplete_acknowledgement_reason": _IMPORT_ACK_REASON,
    }
    serialized = rss._serialize_signout(row)
    assert serialized["import_incomplete_failed_datasets"] == ["snv", "sv"]
    assert serialized["import_incomplete_acknowledged"] is True
    assert serialized["import_incomplete_acknowledgement_reason"] == _IMPORT_ACK_REASON
    # A driver without the jsonb codec hands the extracted array back as text.
    as_text = rss._serialize_signout({**row, "import_incomplete_failed_datasets": '["snv"]'})
    assert as_text["import_incomplete_failed_datasets"] == ["snv"]


def _patch_import_state(monkeypatch, flag) -> None:
    async def _import_state(session, family_uuid):
        return flag

    monkeypatch.setattr(rss, "_import_incomplete_state", _import_state, raising=False)


def test_signout_check_flags_an_import_completed_after_signout(monkeypatch) -> None:
    # Signed while the import was incomplete (acknowledged); the family has since been
    # re-imported in full. The page no longer shows the data that was signed.
    _patch_check(monkeypatch, reviews=[_REVIEW])
    _patch_import_state(monkeypatch, _INCOMPLETE)
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    _patch_import_state(monkeypatch, None)
    out = asyncio.run(
        rss.compare_report_with_latest_signout(
            _CheckSession(_signed_row(signed)), family_id="FAM1", user=_user()
        )
    )
    assert out["matches"] is False
    assert out["changed_sections"] == ["import_incomplete"]


def test_signout_check_cannot_compare_the_import_state_of_an_older_record(monkeypatch) -> None:
    # A record signed before the gate never froze the import state: that section is not
    # comparable, and does not on its own make the page "changed".
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    signed.pop("import_incomplete")
    out = asyncio.run(
        rss.compare_report_with_latest_signout(
            _CheckSession(_signed_row(signed)), family_id="FAM1", user=_user()
        )
    )
    assert out["matches"] is True
    assert out["not_compared"] == ["import_incomplete"]


def test_the_sign_out_endpoint_passes_the_import_acknowledgement_on(monkeypatch) -> None:
    from backend.app.routers import families_reports
    from backend.app.schemas import ReportSignoutRequest

    captured: dict = {}

    async def _sign_out(session, **kwargs):
        captured.update(kwargs)
        return {
            "version": 1,
            "signed_out_by": "bjorn",
            "signed_out_at": datetime.fromisoformat(_PRE_GATE_SIGNED_AT),
            "content_hash": "h" * 64,
        }

    monkeypatch.setattr(families_reports, "sign_out_report", _sign_out)
    payload = ReportSignoutRequest(
        acknowledge_import_incomplete=True,
        import_incomplete_acknowledgement_reason=_IMPORT_ACK_REASON,
    )
    asyncio.run(
        families_reports.sign_out_family_report_endpoint(
            "FAM1", payload=payload, project_id=None, session=object(), user=_user()
        )
    )
    assert captured["acknowledge_import_incomplete"] is True
    assert captured["import_incomplete_acknowledgement_reason"] == _IMPORT_ACK_REASON
    # A bare request acknowledges nothing.
    asyncio.run(
        families_reports.sign_out_family_report_endpoint(
            "FAM1", payload=None, project_id=None, session=object(), user=_user()
        )
    )
    assert captured["acknowledge_import_incomplete"] is False
    assert captured["import_incomplete_acknowledgement_reason"] is None
