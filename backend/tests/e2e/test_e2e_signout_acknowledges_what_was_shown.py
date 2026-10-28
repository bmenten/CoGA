"""A sign-out's acknowledgement covers the concerns its dialog showed, end to end (TRACE-19).

"Sign out anyway" sent a gate's acknowledgement and its reason, and the sign-out read the gate
again: a concern that arose after the dialog listed them was signed out over, as
acknowledged, unseen. Each refusal now carries the fingerprint of the concerns it lists, the
dialog sends it back, and a sign-out whose concerns are no longer those is refused with them
as they are now. Over the golden trio, through the API, this:

* tags a variant for the report, unclassified: the drift refusal lists it and gives its
  fingerprint;
* tags a second one while the drift dialog is open: the acknowledgement of what the dialog
  showed is refused (409) with both listed and a new fingerprint, and nothing is written;
* refuses an acknowledgement without a fingerprint (422) and one that is no SHA-256 (422);
* flags the family import-incomplete, as a failed import does, and while the import dialog
  is open flags a second failed dataset: refused again, with both datasets;
* signs out with the fingerprints of what was shown last, and reads the audit event back
  through the API: it records each fingerprint, and the signed record holds the concerns
  each covers.

The module leaves nothing tagged and no flag behind. Skipped unless ``RUN_INTEGRATION=1``
(see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
# Two golden small variants no other module reviews: each, tagged for the report without a
# classification, is a classification whose evidence cannot be verified (no frozen snapshot).
FIRST = "1-6000-C-G"
SECOND = "1-7000-AG-A"
_DRIFT = {"acknowledge_drift": True, "drift_acknowledgement_reason": "e2e: the reported variants hold no ACMG classification"}
_QC = {"acknowledge_qc": True, "qc_acknowledgement_reason": "e2e: synthetic genotypes"}
# The golden trio's abnormal repeat and Paraphase calls are not reviewed here (REQ-TRACE-016).
_UNREVIEWED = {
    "acknowledge_unreviewed_findings": True,
    "unreviewed_findings_acknowledgement_reason": "e2e: the repeat and Paraphase calls are not reviewed",
}
_IMPORT = {
    "acknowledge_import_incomplete": True,
    "import_incomplete_acknowledgement_reason": "e2e: the SV calls are not part of this referral",
}
_JOB_1 = "11111111-2222-4333-8444-555555555555"
_JOB_2 = "66666666-7777-4888-8999-aaaaaaaaaaaa"


def _flag(failed: list[str], jobs: dict[str, str], job_id: str) -> dict[str, Any]:
    """The import_incomplete flag a failed package import leaves on the family."""
    return {
        "at": "2026-10-27T10:14:00+00:00",
        "failed_datasets": failed,
        "imported_datasets": ["coverage"],
        "job_id": job_id,
        "failed_jobs": jobs,
    }


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:  # pragma: no cover
        out["json"] = None
    return out


def _fingerprint(captured: dict) -> str | None:
    detail = (captured.get("json") or {}).get("detail")
    return detail.get("concerns_fingerprint") if isinstance(detail, dict) else None


async def _written(family_uuid: str) -> dict[str, int]:
    """What a sign-out writes for the family: its records and its sign-out audit events."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        signouts = (
            await session.execute(
                text("SELECT count(*) FROM report_signouts WHERE family_id = CAST(:f AS uuid)"),
                {"f": family_uuid},
            )
        ).scalar_one()
        events = (
            await session.execute(
                text(
                    "SELECT count(*) FROM clinical_audit_events "
                    "WHERE family_id = CAST(:f AS uuid) AND action = 'sign_out'"
                ),
                {"f": family_uuid},
            )
        ).scalar_one()
    return {"report_signouts": int(signouts), "sign_out_events": int(events)}


async def _set_import_flag(family_uuid: str, flag: dict[str, Any] | None) -> None:
    """Write the family's import_incomplete flag as a failed import does (None: remove it)."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        if flag is None:
            statement = text(
                "UPDATE families SET metadata = COALESCE(metadata, '{}'::jsonb) - 'import_incomplete' "
                "WHERE id = CAST(:f AS uuid)"
            )
            await session.execute(statement, {"f": family_uuid})
        else:
            statement = text(
                "UPDATE families SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), "
                "'{import_incomplete}', CAST(:flag AS jsonb)) WHERE id = CAST(:f AS uuid)"
            )
            await session.execute(statement, {"f": family_uuid, "flag": json.dumps(flag)})
        await session.commit()


async def _collect(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    out: dict = {"facts": facts, "responses": {}, "written": {}}
    r, written = out["responses"], out["written"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    family = f"/api/families/{FAMILY}"
    sign_out = f"{family}/report/sign-out"

    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
        try:
            # The drift dialog: one reported variant without a classification.
            r["tag_first"] = _cap(await ac.put(f"{family}/small-variants/{FIRST}/review", json={"tags": ["report"]}))
            r["drift_shown"] = _cap(await ac.post(sign_out, json={}))

            # A second variant is tagged for the report while the dialog is open.
            r["tag_second"] = _cap(await ac.put(f"{family}/small-variants/{SECOND}/review", json={"tags": ["report"]}))
            written["before"] = await _written(family_uuid)
            r["drift_changed"] = _cap(
                await ac.post(sign_out, json={**_DRIFT, "drift_concerns_fingerprint": _fingerprint(r["drift_shown"])})
            )
            r["no_fingerprint"] = _cap(await ac.post(sign_out, json=_DRIFT))
            r["malformed_fingerprint"] = _cap(
                await ac.post(sign_out, json={**_DRIFT, "drift_concerns_fingerprint": "not-a-fingerprint"})
            )
            written["after_refusals"] = await _written(family_uuid)

            # The drift acknowledged as it is now; the Sample QC, when it gates, as it lists it.
            drift = {**_DRIFT, "drift_concerns_fingerprint": _fingerprint(r["drift_changed"])}
            await _set_import_flag(family_uuid, _flag(["snv"], {"snv": _JOB_1}, _JOB_1))
            r["after_drift"] = _cap(await ac.post(sign_out, json=drift))
            acknowledged = dict(drift)
            if ((r["after_drift"]["json"] or {}).get("detail") or {}).get("gate") == "sample_qc":
                acknowledged.update(_QC, qc_concerns_fingerprint=_fingerprint(r["after_drift"]))
                r["import_shown"] = _cap(await ac.post(sign_out, json=acknowledged))
            else:
                r["import_shown"] = r["after_drift"]

            # Another import fails, for the SV calls, while the import dialog is open.
            await _set_import_flag(family_uuid, _flag(["snv", "sv_needlr"], {"snv": _JOB_1, "sv_needlr": _JOB_2}, _JOB_2))
            written["before_import_refusal"] = await _written(family_uuid)
            r["import_changed"] = _cap(
                await ac.post(
                    sign_out,
                    json={
                        **acknowledged,
                        **_IMPORT,
                        "import_incomplete_concerns_fingerprint": _fingerprint(r["import_shown"]),
                    },
                )
            )
            written["after_import_refusal"] = await _written(family_uuid)

            # Every concern acknowledged as shown last: signed out, past the findings without a
            # review, acknowledged as the dialog lists them.
            final = {
                **acknowledged,
                **_IMPORT,
                "import_incomplete_concerns_fingerprint": _fingerprint(r["import_changed"]),
            }
            r["signout"] = _cap(await ac.post(sign_out, json=final))
            if ((r["signout"]["json"] or {}).get("detail") or {}).get("gate") == "unreviewed_findings":
                r["unreviewed_shown"] = r["signout"]
                r["signout"] = _cap(
                    await ac.post(
                        sign_out,
                        json={
                            **final,
                            **_UNREVIEWED,
                            "unreviewed_findings_concerns_fingerprint": _fingerprint(r["unreviewed_shown"]),
                        },
                    )
                )
            written["after_signout"] = await _written(family_uuid)
            version = (r["signout"]["json"] or {}).get("version")
            r["detail"] = _cap(await ac.get(f"{family}/report/sign-outs/{version}"))
            r["audit"] = _cap(await ac.get(f"{family}/clinical-audit", params={"limit": 50}))
        finally:
            # Nothing tagged and no flag left for the modules that run after this one.
            await _set_import_flag(family_uuid, None)
            for variant in (FIRST, SECOND):
                r[f"untag_{variant}"] = _cap(
                    await ac.put(f"{family}/small-variants/{variant}/review", json={"tags": [], "note": None})
                )
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    root = tmp_path_factory.mktemp("golden_acknowledged") / FAMILY
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    out = _harness.run_async(lambda: _collect(root))
    assert out["facts"]["completed"] is True, out["facts"]
    return out


def _ok(run: dict, key: str) -> dict:
    resp = run["responses"][key]
    assert resp["status"] == 200, (key, resp["status"], resp["text"][:2000])
    return resp["json"]


def _refusal(run: dict, key: str, status: int = 409) -> Any:
    resp = run["responses"][key]
    assert resp["status"] == status, (key, resp["status"], resp["text"][:2000])
    return resp["json"]["detail"]


def _listed(detail: dict) -> dict[str, str]:
    return {entry["variant_id"]: entry["status"] for entry in detail["drifted"]}


# ------------------------------------------------------------------------- the drift


def test_the_drift_refusal_lists_the_classifications_it_counts_and_their_fingerprint(run) -> None:
    _ok(run, "tag_first")
    shown = _refusal(run, "drift_shown")
    assert shown["gate"] == "evidence_drift" and shown["concerns_changed"] is False
    assert _listed(shown)[FIRST] == "no_snapshot"
    assert SECOND not in _listed(shown)
    assert len(shown["concerns_fingerprint"]) == 64


def test_a_classification_that_drifts_after_the_dialog_is_refused_with_the_new_concerns(run) -> None:
    _ok(run, "tag_second")
    shown, changed = _refusal(run, "drift_shown"), _refusal(run, "drift_changed")
    assert changed["gate"] == "evidence_drift" and changed["concerns_changed"] is True
    assert {FIRST: "no_snapshot", SECOND: "no_snapshot"}.items() <= _listed(changed).items()
    assert changed["concerns_fingerprint"] != shown["concerns_fingerprint"]


def test_an_acknowledgement_without_a_fingerprint_or_with_a_malformed_one_is_refused(run) -> None:
    assert "drift_concerns_fingerprint" in str(_refusal(run, "no_fingerprint", 422))
    malformed = _refusal(run, "malformed_fingerprint", 422)
    assert [error["loc"][-1] for error in malformed] == ["drift_concerns_fingerprint"]


def test_a_refused_acknowledgement_writes_nothing(run) -> None:
    written = run["written"]
    assert written["after_refusals"] == written["before"], written
    assert written["after_import_refusal"] == written["before_import_refusal"], written


# ------------------------------------------------------------------- the import gate


def test_an_import_failure_after_the_dialog_is_refused_with_the_new_concerns(run) -> None:
    shown, changed = _refusal(run, "import_shown"), _refusal(run, "import_changed")
    assert shown["gate"] == "import_incomplete" and shown["concerns_changed"] is False
    assert shown["import_incomplete"]["failed_datasets"] == ["snv"]
    assert changed["gate"] == "import_incomplete" and changed["concerns_changed"] is True
    assert changed["import_incomplete"]["failed_datasets"] == ["snv", "sv_needlr"]
    assert changed["concerns_fingerprint"] != shown["concerns_fingerprint"]


# ------------------------------------------------- the sign-out that covers what it showed


def test_the_fingerprints_shown_last_sign_out_and_the_audit_event_records_them(run) -> None:
    from backend.app.services import report_signout_service as rss

    signed = _ok(run, "signout")
    written = run["written"]
    assert written["after_signout"]["report_signouts"] == written["before"]["report_signouts"] + 1
    snapshot = _ok(run, "detail")["snapshot"]
    assert (snapshot["acknowledged_drift"], snapshot["acknowledged_import_incomplete"]) == (True, True)
    event = next(event for event in _ok(run, "audit")["events"] if event["action"] == "sign_out")
    after = event["after"]
    assert after["version"] == signed["version"]
    drift, imported = _refusal(run, "drift_changed"), _refusal(run, "import_changed")
    assert after["drift_concerns_fingerprint"] == drift["concerns_fingerprint"]
    assert after["import_incomplete_concerns_fingerprint"] == imported["concerns_fingerprint"]
    after_drift = run["responses"]["after_drift"]["json"]["detail"]
    if after_drift.get("gate") == "sample_qc":
        assert after["qc_concerns_fingerprint"] == after_drift["concerns_fingerprint"]
    else:
        assert after["qc_concerns_fingerprint"] is None
    # The record holds the concerns each fingerprint covers, read back through JSONB.
    recorded_drift = {
        "drifted": [
            {key: value for key, value in entry.items() if key not in ("classified_by", "classified_at")}
            for entry in snapshot["drift"]["drifted"]
        ],
        "structural_drifted": [
            {key: value for key, value in entry.items() if key not in ("classified_by", "classified_at")}
            for entry in snapshot["structural_drift"]["drifted"]
        ],
        "finding_drifted": snapshot["finding_review_drift"]["drifted"],
    }
    assert rss.concerns_fingerprint("evidence_drift", recorded_drift) == after["drift_concerns_fingerprint"]
    recorded_import = {
        "import_incomplete": {key: value for key, value in snapshot["import_incomplete"].items() if key != "at"},
        "import_unfinished": {
            key: {field: value for field, value in entry.items() if field != "at"}
            for key, entry in snapshot["import_unfinished"].items()
        },
    }
    assert (
        rss.concerns_fingerprint("import_incomplete", recorded_import)
        == after["import_incomplete_concerns_fingerprint"]
    )


def test_the_module_leaves_nothing_tagged_for_the_report(run) -> None:
    for variant in (FIRST, SECOND):
        assert run["responses"][f"untag_{variant}"]["status"] == 200, run["responses"][f"untag_{variant}"]
