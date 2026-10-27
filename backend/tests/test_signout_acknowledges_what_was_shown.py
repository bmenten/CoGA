"""A sign-out's acknowledgement covers exactly the concerns its dialog showed (TRACE-19).

"Sign out anyway" sent the acknowledgement of a gate (evidence drift, Sample QC, an
incomplete import) and its reason, and the sign-out read the gate again: a concern that arose
after the dialog listed them (another check failing, another classification drifting,
another dataset failing to import) was signed out over, as acknowledged, unseen. Each refusal
now carries the fingerprint of the concerns it lists, the dialog sends it back with the
acknowledgement, and a sign-out whose concerns are no longer those is refused with them as
they are now, writing nothing.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import types
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.app.schemas import ReportSignoutRequest
from backend.app.services import report_signout_service as rss
from backend.app.services.hash_chain import canonical_json
from backend.app.services.sample_integrity_qc import SampleIntegrityReport, SexCheck

_SECTIONS = {
    "variant_descriptions": {"small": [], "structural": []},
    "gene_panels": {"applied": []},
    "reference_data": {"gene_reference": {"genes": [], "sources": [], "not_in_reference": []}, "clinical_cnvs": None},
    "repeat_expansions": {"catalogue": {"loci": 0, "sources": [], "digest": "0" * 64}, "loci": []},
    "paraphase": {"catalogue": {"regions": 0, "digest": "0" * 64}, "genes": []},
    "coverage_tracks": {"tracks": []},
}
_REASON = "Reviewed with the laboratory director; the concerns listed do not affect this report."
_DRIFT_REASON = "The evidence that moved does not change the classifications listed."
_IMPORT_REASON = "SV calls are not part of this referral."


def _sex_mismatch(sample: str) -> SexCheck:
    return SexCheck(
        sample, "female", "male", 0.01, 500, "fail",
        "Recorded female but genotypes indicate male (1.0% chrX het over 500 sites) — possible sample swap or mislabel.",
    )


def _drifted(variant_id: str, **change) -> dict:
    """A drifted classification as evaluate_classification_drift reports it."""
    entry = {
        "variant_id": variant_id,
        "acmg_class": "acmg_class_4",
        "classified_by": "analyst-a",
        "classified_at": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        "status": "drifted",
        "annotation_version_from": "vep-112",
        "annotation_version_to": "vep-113",
        "clinvar_from": "uncertain_significance",
        "clinvar_to": "uncertain_significance",
        "changed": ["gnomad_af", "annotations"],
        "evidence_from": {"gnomad_af": 0.0001},
        "evidence_to": {"gnomad_af": 0.012},
    }
    entry.update(change)
    return entry


_STRUCTURAL_DRIFTED = {
    "variant_id": "1-7000-12000-DEL",
    "classification": "likely_pathogenic",
    "cnv_class": "likely_pathogenic",
    "classified_by": "analyst-b",
    "classified_at": datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc),
    "status": "drifted",
    "changed": ["gene_symbols"],
    "evidence_from": {"source": "needlr", "gene_symbols": ["BRCA2"]},
    "evidence_to": {"source": "needlr", "gene_symbols": ["BRCA2", "N4BP2L1"]},
}
_JOB_1 = "3f6c1a2e-8b4d-4e5f-9a7b-1c2d3e4f5a6b"
_JOB_2 = "7a1d2c3b-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


def _flag(failed: list[str], *, at: str = "2026-10-27T10:14:00+00:00") -> dict:
    """An import_incomplete flag as _import_incomplete_state reads it."""
    return {
        "at": at,
        "failed_datasets": failed,
        "imported_datasets": ["coverage"],
        "job_id": _JOB_1,
        "failed_jobs": {name: _JOB_1 for name in failed},
    }


class _Family:
    """What the family's gates read now. A test changes it between a refusal and the
    acknowledgement, as data that changes while the dialog is open."""

    def __init__(self) -> None:
        self.drifted: list[dict] = []
        self.structural_drifted: list[dict] = []
        self.qc = SampleIntegrityReport(overall_status="pass")
        self.import_incomplete: dict | None = None
        self.import_unfinished: dict = {}
        self.audited: list[dict] = []


class _Result:
    def scalar_one(self):
        return 0  # no earlier sign-out: version 1

    def scalar_one_or_none(self):
        return None  # the chain's genesis


class _Session:
    def __init__(self) -> None:
        self.executed: list = []

    async def execute(self, *args, **kwargs):
        self.executed.append((args, kwargs))
        return _Result()

    async def commit(self) -> None:
        return None


@pytest.fixture
def family(monkeypatch) -> _Family:
    state = _Family()

    async def _ctx(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_name="GRCh38")

    async def _manifest(session, *, family_id, user, project_id=None):
        return {"assembly": "GRCh38", "modules": [{"key": "clinvar", "version": "2026-05"}]}

    async def _drift(session, *, family_id, user, project_id=None):
        return {
            "checked": len(state.drifted),
            "drifted_count": len(state.drifted),
            "drifted": copy.deepcopy(state.drifted),
            "structural": {
                "checked": len(state.structural_drifted),
                "drifted_count": len(state.structural_drifted),
                "drifted": copy.deepcopy(state.structural_drifted),
            },
        }

    async def _qc(session, *, family_id, user, project_id=None, shared_nipt=None):
        return copy.deepcopy(state.qc)

    async def _nothing_reported(session, family_uuid):
        return []

    async def _sequencing_qc(session, context):
        return {"profile_key": None, "profile_label": None, "thresholds": {}, "samples": {}}

    async def _import_incomplete(session, family_uuid):
        return copy.deepcopy(state.import_incomplete)

    async def _import_unfinished(session, family_uuid):
        return copy.deepcopy(state.import_unfinished)

    async def _no_import_job(session, family_identifier):
        return None

    async def _writes_shared(session, family_uuid):
        return True

    async def _sections(session, *, context, reported, reported_structural, shared_nipt=None):
        return copy.deepcopy(_SECTIONS)

    async def _code_free(session, code):
        return False

    async def _audit(*args, **kwargs):
        state.audited.append(kwargs)

    monkeypatch.setattr(rss, "build_family_metadata_context", _ctx)
    monkeypatch.setattr(rss, "get_family_annotation_manifest", _manifest)
    monkeypatch.setattr(rss, "evaluate_classification_drift", _drift)
    monkeypatch.setattr(rss, "get_family_sample_integrity_qc", _qc)
    monkeypatch.setattr(rss, "_reported_reviews", _nothing_reported)
    monkeypatch.setattr(rss, "_reported_structural_reviews", _nothing_reported)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _sequencing_qc)
    monkeypatch.setattr(rss, "_import_incomplete_state", _import_incomplete)
    monkeypatch.setattr(rss, "_import_unfinished_state", _import_unfinished)
    monkeypatch.setattr(rss, "_active_import_job", _no_import_job)
    monkeypatch.setattr(rss, "try_share_family_variant_writes", _writes_shared)
    monkeypatch.setattr(rss, "build_record_sections", _sections)
    monkeypatch.setattr(rss, "_report_code_taken", _code_free)
    monkeypatch.setattr(rss, "record_clinical_event", _audit)
    return state


def _user():
    return types.SimpleNamespace(username="lab-signer", email="signer@example.org", id=None)


def _sign_out(session=None, **kwargs):
    # A fingerprint the dialog was not given is not sent, as the dialog leaves it out.
    sent = {key: value for key, value in kwargs.items() if not (key.endswith("_concerns_fingerprint") and value is None)}
    return asyncio.run(rss.sign_out_report(session or _Session(), family_id="FAM1", user=_user(), **sent))


def _refusal(session=None, **kwargs) -> tuple[int, Any]:
    with pytest.raises(HTTPException) as refused:
        _sign_out(session, **kwargs)
    return refused.value.status_code, refused.value.detail


def _shown(detail: object) -> str | None:
    """The fingerprint a refusal gave its dialog. (Before TRACE-19 a refusal gave none, and
    the drift gate's was a plain sentence: the acknowledgement then signed out over whatever
    the gate read when it was confirmed.)"""
    return detail.get("concerns_fingerprint") if isinstance(detail, dict) else None


def _wrote_nothing(session: _Session, family: _Family) -> bool:
    """No signed record and no audit event: nothing a refused sign-out may write."""
    return session.executed == [] and family.audited == []


# --------------------------------------------------------------------------- the refusals


def test_each_refusal_carries_the_fingerprint_of_the_concerns_it_lists(family) -> None:
    family.drifted = [_drifted("1-100-A-G")]
    family.structural_drifted = [_STRUCTURAL_DRIFTED]
    family.qc = SampleIntegrityReport(overall_status="fail", sex_checks=[_sex_mismatch("CHILD")])
    family.import_incomplete = _flag(["snv"])

    status, drift = _refusal()
    # The drift gate's refusal is structured like the others': the classifications it
    # counts, each as the record freezes it but for who last saved its review and when.
    assert status == 409 and drift["gate"] == "evidence_drift"
    assert drift["message"].startswith(
        "2 classification(s) (1 of them structural-variant or CNV classifications) have evidence that changed"
    )
    shown_small = {key: value for key, value in _drifted("1-100-A-G").items() if not key.startswith("classified_")}
    shown_structural = {key: value for key, value in _STRUCTURAL_DRIFTED.items() if not key.startswith("classified_")}
    assert drift["drifted"] == [shown_small]
    assert drift["structural_drifted"] == [shown_structural]
    assert drift["concerns_changed"] is False
    # SHA-256 over the canonical JSON of the gate and the concerns the refusal lists.
    concerns = {"drifted": drift["drifted"], "structural_drifted": drift["structural_drifted"]}
    expected = hashlib.sha256(canonical_json({"gate": "evidence_drift", "concerns": concerns}).encode()).hexdigest()
    assert drift["concerns_fingerprint"] == expected
    assert drift["concerns_fingerprint"] == rss.concerns_fingerprint("evidence_drift", concerns)

    status, qc = _refusal(
        acknowledge_drift=True,
        drift_acknowledgement_reason=_DRIFT_REASON,
        drift_concerns_fingerprint=drift["concerns_fingerprint"],
    )
    assert status == 409 and qc["gate"] == "sample_qc" and qc["concerns_changed"] is False
    groups = ("failed_checks", "unverifiable_checks", "nipt_failed_checks", "warning_checks")
    assert qc["concerns_fingerprint"] == rss.concerns_fingerprint("sample_qc", {key: qc[key] for key in groups})

    status, imported = _refusal(
        acknowledge_drift=True,
        drift_acknowledgement_reason=_DRIFT_REASON,
        drift_concerns_fingerprint=drift["concerns_fingerprint"],
        acknowledge_qc=True,
        qc_acknowledgement_reason=_REASON,
        qc_concerns_fingerprint=qc["concerns_fingerprint"],
    )
    assert status == 409 and imported["gate"] == "import_incomplete"
    # The flag and the unfinished imports as the refusal lists them, without the time each
    # was written: the message names it, and the jobs and datasets say which failure it is.
    without_time = {key: value for key, value in imported["import_incomplete"].items() if key != "at"}
    assert imported["concerns_fingerprint"] == rss.concerns_fingerprint(
        "import_incomplete", {"import_incomplete": without_time, "import_unfinished": {}}
    )
    # Three gates, three fingerprints.
    assert len({drift["concerns_fingerprint"], qc["concerns_fingerprint"], imported["concerns_fingerprint"]}) == 3


def test_the_fingerprint_reads_no_order_and_no_time(family) -> None:
    concerns = {"failed_checks": ["Sample A: x.", "Sample B: y."], "warning_checks": []}
    reordered = {"warning_checks": [], "failed_checks": ["Sample B: y.", "Sample A: x."]}
    assert rss.concerns_fingerprint("sample_qc", concerns) == rss.concerns_fingerprint("sample_qc", reordered)
    assert rss.concerns_fingerprint("sample_qc", concerns) != rss.concerns_fingerprint("evidence_drift", concerns)
    assert rss.concerns_fingerprint("sample_qc", concerns) != rss.concerns_fingerprint(
        "sample_qc", {**concerns, "failed_checks": ["Sample A: x."]}
    )

    # The review of a drifted classification saved again (a note, a tag) by another analyst,
    # and an import flag written again at another time: the concerns did not change.
    family.drifted = [_drifted("1-100-A-G")]
    _status, before = _refusal()
    family.drifted = [_drifted("1-100-A-G", classified_by="analyst-c", classified_at=datetime(2026, 10, 27, tzinfo=timezone.utc))]
    _status, after = _refusal()
    assert after["concerns_fingerprint"] == before["concerns_fingerprint"]

    family.drifted = []
    family.import_incomplete = _flag(["snv"])
    _status, flagged = _refusal()
    family.import_incomplete = _flag(["snv"], at="2026-10-27T11:30:00+00:00")
    _status, again = _refusal()
    assert again["concerns_fingerprint"] == flagged["concerns_fingerprint"]


# ---------------------------------------- a concern that arose after the dialog listed them


def test_a_qc_concern_that_arose_after_the_dialog_is_refused_with_the_new_concerns(family) -> None:
    family.qc = SampleIntegrityReport(overall_status="fail", sex_checks=[_sex_mismatch("CHILD")])
    _status, shown = _refusal()
    assert shown["failed_checks"] == [
        "Sample CHILD: recorded female but genotypes indicate male (1.0% chrX het over 500 sites) — possible sample swap or mislabel."
    ]

    # A second sample's sex check fails while the dialog is open.
    family.qc = SampleIntegrityReport(
        overall_status="fail", sex_checks=[_sex_mismatch("CHILD"), _sex_mismatch("FATHER")]
    )
    session = _Session()
    status, refused = _refusal(
        session,
        acknowledge_qc=True,
        qc_acknowledgement_reason=_REASON,
        qc_concerns_fingerprint=_shown(shown),
    )
    # Refused with the concerns as they are now, for the dialog to show for a fresh
    # acknowledgement; nothing is signed out and nothing audited.
    assert status == 409
    assert refused["gate"] == "sample_qc" and refused["concerns_changed"] is True
    assert [concern.split(":")[0] for concern in refused["failed_checks"]] == ["Sample CHILD", "Sample FATHER"]
    assert refused["concerns_fingerprint"] != shown["concerns_fingerprint"]
    assert refused["message"] == shown["message"]
    assert _wrote_nothing(session, family)

    # Acknowledged as shown now: signed out, and the audit event records what was acknowledged.
    out = _sign_out(
        acknowledge_qc=True,
        qc_acknowledgement_reason=_REASON,
        qc_concerns_fingerprint=refused["concerns_fingerprint"],
    )
    assert out["snapshot"]["acknowledged_qc"] is True
    [event] = family.audited
    assert event["after"]["qc_concerns_fingerprint"] == refused["concerns_fingerprint"]
    assert event["after"]["qc_failed"] == refused["failed_checks"]


def test_a_drift_that_arose_after_the_dialog_is_refused_with_the_new_concerns(family) -> None:
    family.drifted = [_drifted("1-100-A-G")]
    _status, shown = _refusal()

    # Another classification drifts while the dialog is open.
    family.drifted = [_drifted("1-100-A-G"), _drifted("2-200-C-T")]
    session = _Session()
    status, refused = _refusal(
        session,
        acknowledge_drift=True,
        drift_acknowledgement_reason=_DRIFT_REASON,
        drift_concerns_fingerprint=_shown(shown),
    )
    assert status == 409 and refused["gate"] == "evidence_drift" and refused["concerns_changed"] is True
    assert [entry["variant_id"] for entry in refused["drifted"]] == ["1-100-A-G", "2-200-C-T"]
    assert refused["message"].startswith("2 classification(s) have evidence that changed")
    assert _wrote_nothing(session, family)

    # The classification shown drifts further: its ClinVar significance moves too. What moved
    # is part of the concern the dialog listed.
    family.drifted = [_drifted("1-100-A-G", clinvar_to="pathogenic", changed=["clinvar", "gnomad_af", "annotations"])]
    status, moved = _refusal(
        acknowledge_drift=True,
        drift_acknowledgement_reason=_DRIFT_REASON,
        drift_concerns_fingerprint=_shown(shown),
    )
    assert status == 409 and moved["concerns_changed"] is True
    assert moved["drifted"][0]["clinvar_to"] == "pathogenic"

    out = _sign_out(
        acknowledge_drift=True,
        drift_acknowledgement_reason=_DRIFT_REASON,
        drift_concerns_fingerprint=moved["concerns_fingerprint"],
    )
    assert out["snapshot"]["acknowledged_drift"] is True
    assert out["snapshot"]["drift"]["drifted"][0]["clinvar_to"] == "pathogenic"
    assert family.audited[-1]["after"]["drift_concerns_fingerprint"] == moved["concerns_fingerprint"]


def test_an_import_failure_that_arose_after_the_dialog_is_refused_with_the_new_concerns(family) -> None:
    family.import_incomplete = _flag(["snv"])
    _status, shown = _refusal()

    # The SV dataset fails too, in another import, while the dialog is open.
    family.import_incomplete = {
        **_flag(["snv", "sv"]),
        "job_id": _JOB_2,
        "failed_jobs": {"snv": _JOB_1, "sv": _JOB_2},
    }
    session = _Session()
    status, refused = _refusal(
        session,
        acknowledge_import_incomplete=True,
        import_incomplete_acknowledgement_reason=_IMPORT_REASON,
        import_incomplete_concerns_fingerprint=_shown(shown),
    )
    assert status == 409 and refused["gate"] == "import_incomplete" and refused["concerns_changed"] is True
    assert refused["import_incomplete"]["failed_datasets"] == ["snv", "sv"]
    assert _wrote_nothing(session, family)

    # An import that stops part-way is a concern of the same gate.
    family.import_unfinished = {
        _JOB_2: {"job_id": _JOB_2, "at": "2026-10-27T12:00:00+00:00", "datasets": ["coverage"], "finished_datasets": []}
    }
    status, stopped = _refusal(
        acknowledge_import_incomplete=True,
        import_incomplete_acknowledgement_reason=_IMPORT_REASON,
        import_incomplete_concerns_fingerprint=refused["concerns_fingerprint"],
    )
    assert status == 409 and stopped["concerns_changed"] is True
    assert list(stopped["import_unfinished"]) == [_JOB_2]

    _sign_out(
        acknowledge_import_incomplete=True,
        import_incomplete_acknowledgement_reason=_IMPORT_REASON,
        import_incomplete_concerns_fingerprint=stopped["concerns_fingerprint"],
    )
    after = family.audited[-1]["after"]
    assert after["import_incomplete_concerns_fingerprint"] == stopped["concerns_fingerprint"]
    assert after["acknowledged_import_incomplete"] is True


def test_an_earlier_gate_whose_concerns_changed_is_refused_before_a_later_one_is_read(family) -> None:
    # The drift dialog was acknowledged; the QC dialog is open when another classification
    # drifts. Confirming the QC acknowledgement is refused at the drift gate, with the drift
    # as it is now.
    family.drifted = [_drifted("1-100-A-G")]
    family.qc = SampleIntegrityReport(overall_status="fail", sex_checks=[_sex_mismatch("CHILD")])
    _status, drift = _refusal()
    acknowledged_drift = {
        "acknowledge_drift": True,
        "drift_acknowledgement_reason": _DRIFT_REASON,
        "drift_concerns_fingerprint": _shown(drift),
    }
    _status, qc = _refusal(**acknowledged_drift)
    assert qc["gate"] == "sample_qc"

    family.drifted = [_drifted("1-100-A-G"), _drifted("2-200-C-T")]
    session = _Session()
    status, refused = _refusal(
        session,
        **acknowledged_drift,
        acknowledge_qc=True,
        qc_acknowledgement_reason=_REASON,
        qc_concerns_fingerprint=_shown(qc),
    )
    assert status == 409 and refused["gate"] == "evidence_drift" and refused["concerns_changed"] is True
    assert _wrote_nothing(session, family)


# ------------------------------------------------------- the acknowledgement that matches


def test_the_fingerprints_shown_sign_out_and_the_audit_event_records_them(family) -> None:
    family.drifted = [_drifted("1-100-A-G")]
    family.qc = SampleIntegrityReport(overall_status="fail", sex_checks=[_sex_mismatch("CHILD")])
    family.import_incomplete = _flag(["snv"])
    sent: dict = {}
    fingerprints: dict = {}
    for gate, acknowledgement in (
        ("evidence_drift", {"acknowledge_drift": True, "drift_acknowledgement_reason": _DRIFT_REASON}),
        ("sample_qc", {"acknowledge_qc": True, "qc_acknowledgement_reason": _REASON}),
        (
            "import_incomplete",
            {"acknowledge_import_incomplete": True, "import_incomplete_acknowledgement_reason": _IMPORT_REASON},
        ),
    ):
        _status, refusal = _refusal(**sent)
        assert refusal["gate"] == gate
        field = {"evidence_drift": "drift", "sample_qc": "qc", "import_incomplete": "import_incomplete"}[gate]
        fingerprints[f"{field}_concerns_fingerprint"] = refusal["concerns_fingerprint"]
        sent.update(acknowledgement, **{f"{field}_concerns_fingerprint": refusal["concerns_fingerprint"]})

    out = _sign_out(**sent)
    snapshot = out["snapshot"]
    assert (snapshot["acknowledged_drift"], snapshot["acknowledged_qc"], snapshot["acknowledged_import_incomplete"]) == (
        True,
        True,
        True,
    )
    [event] = family.audited
    for key, fingerprint in fingerprints.items():
        assert event["after"][key] == fingerprint
    # The record holds the concerns each fingerprint covers: recomputed from it, they match.
    stored = json.loads(json.dumps(snapshot, default=str))
    drift_concerns = {
        "drifted": [
            {key: value for key, value in entry.items() if not key.startswith("classified_")}
            for entry in stored["drift"]["drifted"]
        ],
        "structural_drifted": [],
    }
    assert rss.concerns_fingerprint("evidence_drift", drift_concerns) == fingerprints["drift_concerns_fingerprint"]
    qc_concerns = {
        "failed_checks": event["after"]["qc_failed"],
        "unverifiable_checks": event["after"]["qc_unverifiable"],
        "nipt_failed_checks": event["after"]["nipt_failed_checks"],
        "warning_checks": event["after"]["qc_warnings"],
    }
    assert rss.concerns_fingerprint("sample_qc", qc_concerns) == fingerprints["qc_concerns_fingerprint"]
    import_concerns = {
        "import_incomplete": {key: value for key, value in stored["import_incomplete"].items() if key != "at"},
        "import_unfinished": stored["import_unfinished"],
    }
    assert (
        rss.concerns_fingerprint("import_incomplete", import_concerns)
        == fingerprints["import_incomplete_concerns_fingerprint"]
    )


def test_a_sign_out_without_concerns_needs_no_fingerprint(family) -> None:
    out = _sign_out()
    assert out["version"] == 1
    after = family.audited[-1]["after"]
    assert after["drift_concerns_fingerprint"] is None
    assert after["qc_concerns_fingerprint"] is None
    assert after["import_incomplete_concerns_fingerprint"] is None


def test_concerns_resolved_after_the_dialog_leave_nothing_to_acknowledge(family) -> None:
    # The failed check was resolved while the dialog was open: no concern is signed out
    # over, so none is acknowledged, whatever the request says it was shown.
    family.qc = SampleIntegrityReport(overall_status="fail", sex_checks=[_sex_mismatch("CHILD")])
    _status, shown = _refusal()
    family.qc = SampleIntegrityReport(overall_status="pass")
    out = _sign_out(
        acknowledge_qc=True,
        qc_acknowledgement_reason=_REASON,
        qc_concerns_fingerprint=shown["concerns_fingerprint"],
    )
    assert out["snapshot"]["acknowledged_qc"] is False
    assert out["snapshot"]["qc_acknowledgement_reason"] is None
    assert family.audited[-1]["after"]["qc_concerns_fingerprint"] is None


# ------------------------------------------------- an acknowledgement without a fingerprint


@pytest.mark.parametrize(
    ("setup", "acknowledgement", "field"),
    [
        (
            lambda family: setattr(family, "drifted", [_drifted("1-100-A-G")]),
            {"acknowledge_drift": True, "drift_acknowledgement_reason": _DRIFT_REASON},
            "drift_concerns_fingerprint",
        ),
        (
            lambda family: setattr(
                family, "qc", SampleIntegrityReport(overall_status="fail", sex_checks=[_sex_mismatch("CHILD")])
            ),
            {"acknowledge_qc": True, "qc_acknowledgement_reason": _REASON},
            "qc_concerns_fingerprint",
        ),
        (
            lambda family: setattr(family, "import_incomplete", _flag(["snv"])),
            {"acknowledge_import_incomplete": True, "import_incomplete_acknowledgement_reason": _IMPORT_REASON},
            "import_incomplete_concerns_fingerprint",
        ),
    ],
    ids=["evidence_drift", "sample_qc", "import_incomplete"],
)
def test_an_acknowledgement_without_its_fingerprint_is_refused(family, setup, acknowledgement, field) -> None:
    # It does not say which concerns it was shown, so it covers none: the SPA, the only
    # client, always sends the fingerprint its dialog was given.
    setup(family)
    session = _Session()
    status, detail = _refusal(session, **acknowledgement)
    assert status == 422
    assert field in str(detail)
    assert _wrote_nothing(session, family)


def test_the_request_takes_a_fingerprint_only_as_sha256_hex() -> None:
    fingerprint = "0123456789abcdef" * 4
    request = ReportSignoutRequest(acknowledge_qc=True, qc_concerns_fingerprint=fingerprint)
    assert request.qc_concerns_fingerprint == fingerprint
    assert ReportSignoutRequest().drift_concerns_fingerprint is None
    for malformed in ("", "not-a-fingerprint", fingerprint.upper(), fingerprint + "0"):
        with pytest.raises(ValidationError):
            ReportSignoutRequest(import_incomplete_concerns_fingerprint=malformed)
