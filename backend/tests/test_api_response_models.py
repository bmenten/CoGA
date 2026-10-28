"""Every route that answers JSON names its response model (ENG-4).

The BED reads' JSON was declared ``text/plain`` (their text format, removed in ENG-5, was the
default), the shared SV counts were a plain mapping, and a dozen routes answered a bare
``dict``: no model described them, so the frontend read them through hand-written types or
untyped (#1020, #1027). Each now names a model (``backend/app/schemas``), which the frontend
reads through its generated type:

* the BED reads, ``BedTrackOut`` (coverage, segments) or ``BedApcadTrackOut`` (APCAD, PCF:
  each row with its ``origin``), by the track type read; the uploads, ``BedUploadResult`` and
  ``StructuralVariantUploadResult``;
* the shared SV counts, ``SharedStructuralVariantCountsOut``;
* the administrator's QC-profile, QC-cut-off, family-projects, NIPT-artifact and deletion
  answers, and the health probes.

None stays a bare ``dict``: the health probes, the only operations routes among them, answer
fixed keys too. The models describe what each route answered before: a BED read still answers
the JSON its service builds, which this file compares with the model for each track type
through the app (a fake interval store); ``e2e/test_e2e_response_models.py`` compares every
route's real answer. What still has no model is listed below, with why: an answer that is not
JSON (a file, a stream, a page), and a list of strings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient
import pytest

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import bed as bed_router
from backend.app.routers import health as health_router
from backend.app.services import bed_service
from backend.app.services.access_control import CurrentUser
from backend.app.services.family_metadata_context import SampleMetadataContext

# Each operation that had no model, the status of its answer and the models it names.
MODELS: dict[tuple[str, str], tuple[str, set[str]]] = {
    ("get", "/api/bed/{sample_id}/{bed_type}"): ("200", {"BedTrackOut", "BedApcadTrackOut"}),
    ("get", "/api/bed/{sample_id}/{bed_type}/batch"): ("200", {"BedTrackOut", "BedApcadTrackOut"}),
    ("post", "/api/bed/upload/{sample_id}/{bed_type}"): ("200", {"BedUploadResult"}),
    ("post", "/api/structural-variants/upload/{sample_id}"): ("200", {"StructuralVariantUploadResult"}),
    ("get", "/api/families/{family_id}/shared-structural-variant-counts"): (
        "200",
        {"SharedStructuralVariantCountsOut"},
    ),
    ("post", "/api/admin/qc-thresholds/profiles"): ("201", {"QcThresholdProfileSummaryOut"}),
    ("put", "/api/admin/qc-thresholds/{profile_key}"): ("200", {"QcThresholdSetOut"}),
    ("put", "/api/admin/families/{family_id}/projects"): ("200", {"FamilyProjectsUpdateOut"}),
    ("delete", "/api/admin/nipt/artifacts/{artifact_id}"): ("200", {"NiptArtifactDeleteOut"}),
    ("delete", "/api/admin/data/samples/{sample_id}/{data_type}"): ("200", {"DataDeletionOut"}),
    ("delete", "/api/admin/data/families/{family_id}/{data_type}"): ("200", {"DataDeletionOut"}),
    ("delete", "/api/admin/samples/{sample_id}"): ("200", {"SampleDeletionOut"}),
    ("delete", "/api/admin/families/{family_id}"): ("200", {"FamilyDeletionOut"}),
    ("get", "/api/health"): ("200", {"HealthOut"}),
    ("get", "/api/health/ready"): ("200", {"ReadinessOut"}),
}

# Answers that are not JSON, which the schema calls JSON with no schema: a file, a stream or a
# page. A model would not describe them.
NOT_JSON = {
    ("get", "/api/admin/data/files/{file_id}/download"),  # the raw file
    ("get", "/api/admin/families/{family_id}/ped"),  # the PED file
    ("get", "/api/families/{family_id}/qc-report/{sample_id}"),  # the QC report page
    ("get", "/api/families/{family_id}/small-variants/export"),  # CSV
    ("get", "/api/families/{family_id}/structural-variants/export"),  # CSV
    ("get", "/api/variant-explorer/small-variants/export"),  # CSV
    # The alignments, their indexes and header, and the callers' signal files.
    *(
        (method, f"/api/cram/{{family_id}}/{{sample_id}}{suffix}")
        for method in ("get", "head")
        for suffix in (".cram", ".cram.crai", ".bam", ".bam.bai")
    ),
    ("get", "/api/cram/{family_id}/{sample_id}.cram.header"),
    ("get", "/api/signal-tracks/{family_id}/{sample_id}/{source}/{kind}"),
    ("head", "/api/signal-tracks/{family_id}/{sample_id}/{source}/{kind}"),
}
# Typed without a model: the sample IDs, a list of strings.
PLAIN = {("get", "/api/variant-explorer/samples")}


def _names_a_model(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    if "$ref" in schema:
        return True
    if _names_a_model(schema.get("items")) or _names_a_model(schema.get("additionalProperties")):
        return True
    return any(_names_a_model(part) for key in ("anyOf", "oneOf", "allOf") for part in schema.get(key, []))


def _json_answers() -> dict[tuple[str, str], dict[str, Any]]:
    """Each operation's JSON answers that succeed, by status."""
    answers: dict[tuple[str, str], dict[str, Any]] = {}
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            for status, response in operation.get("responses", {}).items():
                schema = (response.get("content") or {}).get("application/json", {}).get("schema")
                if status.startswith("2") and schema is not None:
                    answers.setdefault((method, path), {})[status] = schema
    return answers


def test_each_answer_that_had_no_model_names_its_model() -> None:
    answers = _json_answers()
    named = {}
    for operation, (status, _models) in MODELS.items():
        schema = answers.get(operation, {}).get(status, {})
        refs = [schema] if "$ref" in schema else schema.get("oneOf", [])
        named[operation] = (status, {ref.get("$ref", "").rsplit("/", 1)[-1] for ref in refs})
    assert named == MODELS


def test_every_json_answer_names_a_model_but_those_that_are_not_json() -> None:
    answers = _json_answers()
    without = {
        operation
        for operation, by_status in answers.items()
        if not all(_names_a_model(schema) for schema in by_status.values())
    }
    # A route that answers JSON names its model, so the frontend reads it through the type
    # generated from it (scripts/generate-api-types.py). The exceptions are listed, and stay
    # true: each is still there, still without one.
    assert without == NOT_JSON | PLAIN


# --------------------------------------------------------------------------- the BED reads

SAMPLE = SampleMetadataContext(
    sample_uuid="uuid-proband",
    sample_id="PROBAND",
    family_uuid="family-uuid",
    family_id="FAM_BED",
    sex="female",
    project_ids=["project-uuid"],
    assembly_id="assembly-uuid",
    assembly_name="GRCh38",
)


def _row(chrom: str, start: int, end: int, value: float | None, origin: str | None = None) -> dict[str, Any]:
    return {"chr": chrom, "start": start, "end": end, "value": value, "record_id": None, "origin": origin}


# The interval store: each track type's rows, as ClickHouse answers them. A segment and an
# APCAD site without a value, and a site without an origin, are served as they are stored.
STORE = {
    "coverage": [_row("1", 0, 1000, 400.0), _row("1", 1000, 2000, 380.0), _row("2", 0, 1000, 20.0)],
    "segments": [_row("1", 0, 5000, -0.4), _row("1", 5000, 9000, None)],
    "apcad": [_row("1", 10, 11, 0.98, "paternal"), _row("1", 20, 21, None, None)],
    "apcad_pcf": [_row("1", 0, 9000, 0.5, "maternal"), _row("2", 0, 4000, 0.1, "und")],
}
DOWNSAMPLED = [{"chr": "1", "start": 30, "end": 31, "value": 0.02, "origin": "maternal"}]


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    """An administrator's client; the BED routes find the sample, and the interval store holds
    ``STORE`` (``DOWNSAMPLED`` for a windowed APCAD read). The request audit rows are kept,
    not written."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    admin = CurrentUser(
        id="a1",
        username="admin@example.org",
        email="admin@example.org",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )

    class _FakeSession:
        async def rollback(self) -> None:
            return None

    async def override_get_postgres_session():
        yield _FakeSession()

    async def override_get_current_user():
        return admin

    async def sample_context(_session: Any, *, sample_identifier: str, user: CurrentUser) -> SampleMetadataContext:
        return SAMPLE

    async def sources(_assembly: str, **_kwargs: Any) -> dict[str, list[str]]:
        return {"PROBAND": ["web"]}

    async def rows(_assembly: str, *, track_type: str, chromosomes: list[str], **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(row) for row in STORE[track_type] if row["chr"] in chromosomes]

    async def downsampled(_assembly: str, **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(row) for row in DOWNSAMPLED]

    async def keep_audit_row(_payload: Any) -> None:
        return None

    monkeypatch.setattr(bed_router, "build_sample_metadata_context", sample_context)
    monkeypatch.setattr(bed_service, "get_interval_track_sources_by_sample", sources)
    monkeypatch.setattr(bed_service, "fetch_interval_track_rows", rows)
    monkeypatch.setattr(bed_service, "fetch_apcad_downsampled", downsampled)
    monkeypatch.setattr(request_logging, "write_audit_log_event", keep_audit_row)
    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides = original_overrides
        app.state.skip_startup_tasks = False


def _item(chrom: str, start: int, end: int, value: float | None, **origin: str) -> dict[str, Any]:
    return {"chr": chrom, "start": start, "end": end, "value": value, **origin}


# Each read and what it answers, as it answered before the models: no `origin` on a coverage
# or segments row.
READS = {
    "coverage": (
        "/api/bed/PROBAND/coverage",
        {"chrom": "1"},
        {"bed_type": "coverage", "items": [_item("1", 0, 1000, 400.0), _item("1", 1000, 2000, 380.0)]},
    ),
    "coverage-windowed": (
        "/api/bed/PROBAND/coverage",
        {"chrom": "1", "window": 2000},
        {"bed_type": "coverage", "items": [_item("1", 0, 2000, 390.0)]},
    ),
    "coverage-batch": (
        "/api/bed/PROBAND/coverage/batch",
        {"chrom": ["1", "2"], "window": 2000},
        {"bed_type": "coverage", "items": [_item("1", 0, 2000, 390.0), _item("2", 0, 2000, 20.0)]},
    ),
    "segments": (
        "/api/bed/PROBAND/segments",
        {"chrom": "1"},
        {"bed_type": "segments", "items": [_item("1", 0, 5000, -0.4), _item("1", 5000, 9000, None)]},
    ),
    "apcad": (
        "/api/bed/PROBAND/apcad",
        {"chrom": "1"},
        {
            "bed_type": "apcad",
            "items": [_item("1", 10, 11, 0.98, origin="paternal"), _item("1", 20, 21, None, origin="und")],
        },
    ),
    "apcad-windowed-batch": (
        "/api/bed/PROBAND/apcad/batch",
        {"chrom": ["1"], "window": 1000},
        {"bed_type": "apcad", "items": [_item("1", 30, 31, 0.02, origin="maternal")]},
    ),
    "apcad_pcf-batch": (
        "/api/bed/PROBAND/apcad_pcf/batch",
        {"chrom": ["1", "2"]},
        {
            "bed_type": "apcad_pcf",
            "items": [_item("1", 0, 9000, 0.5, origin="maternal"), _item("2", 0, 4000, 0.1, origin="und")],
        },
    ),
}


@pytest.mark.parametrize("read", list(READS))
def test_a_bed_read_answers_the_json_it_answered(client, read: str) -> None:
    path, params, expected = READS[read]
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    assert response.json() == expected


@pytest.mark.parametrize("read", list(READS))
def test_a_bed_reads_answer_is_what_its_model_describes(client, read: str) -> None:
    from pydantic import TypeAdapter

    from backend.app.schemas import BedApcadTrackOut, BedReadOut, BedTrackOut

    path, params, _expected = READS[read]
    body = client.get(path, params=params).json()
    adapter = TypeAdapter(BedReadOut)
    parsed = adapter.validate_python(body)
    # Validated and written back, the answer is itself: no field renamed, dropped or added.
    assert adapter.dump_python(parsed, mode="json") == body
    assert isinstance(parsed, BedApcadTrackOut if body["bed_type"].startswith("apcad") else BedTrackOut)


# --------------------------------------------------------------------------- the health probes


@pytest.mark.parametrize(
    ("postgres", "clickhouse", "status", "expected"),
    [
        (True, True, 200, {"status": "ok", "postgres": "ok", "clickhouse": "ok"}),
        (True, False, 503, {"status": "degraded", "postgres": "ok", "clickhouse": "down"}),
    ],
    ids=["ready", "degraded"],
)
def test_the_health_probes_answer_what_their_models_describe(
    monkeypatch: pytest.MonkeyPatch, postgres: bool, clickhouse: bool, status: int, expected: dict[str, str]
) -> None:
    from backend.app.schemas import HealthOut, ReadinessOut

    async def postgres_ok() -> bool:
        return postgres

    async def clickhouse_ok() -> bool:
        return clickhouse

    monkeypatch.setattr(health_router, "_postgres_ok", postgres_ok)
    monkeypatch.setattr(health_router, "_clickhouse_ok", clickhouse_ok)
    app.state.skip_startup_tasks = True
    try:
        with TestClient(app) as test_client:
            live = test_client.get("/api/health")
            ready = test_client.get("/api/health/ready")
    finally:
        app.state.skip_startup_tasks = False

    assert (live.status_code, live.json()) == (200, {"status": "ok"})
    assert (ready.status_code, ready.json()) == (status, expected)
    assert HealthOut.model_validate(live.json()).model_dump(mode="json") == live.json()
    assert ReadinessOut.model_validate(ready.json()).model_dump(mode="json") == ready.json()
