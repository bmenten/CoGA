from __future__ import annotations

import asyncio
import copy
import json
import types
from datetime import datetime

import pytest
from fastapi import HTTPException

from backend.app.schemas import ReportSignoutDetail
from backend.app.services import annotation_manifest_service as ams
from backend.app.services import report_signout_service as rss
from backend.app.services.access_control import RecordNotVisible
from backend.app.services.nipt_quality_checks import PLASMA_PROFILE_VERDICTS
from backend.app.services.report_code import parse_report_code, report_code
from backend.app.services.signed_record_sections import RECORD_SECTIONS
from backend.app.services.sample_integrity_qc import (
    MendelianCheck,
    NiptCategoryQc,
    PaternityCheck,
    PedigreeSpec,
    RelatednessCheck,
    SampleIntegrityReport,
    SexCheck,
    evaluate_fetal_sex,
    evaluate_nipt_category_qc,
    evaluate_paternity,
    evaluate_sample_integrity,
    profile_for,
)


# What the analysis looked at (signed_record_sections): the variant descriptions, the gene
# panels applied, the reference-data releases, the repeat expansions, the Paraphase results
# and the coverage tracks. They read ClickHouse and the audit trail, which the stub sessions
# here do not serve, so every test gets these; the tests of the sections themselves are in
# test_signed_record_sections.py (the coverage tracks' in test_signed_record_coverage_tracks.py),
# and the tests below that need others patch their own.
_RECORD_SECTIONS = {
    "variant_descriptions": {"small": [], "structural": []},
    "gene_panels": {"applied": []},
    "reference_data": {
        "gene_reference": {"genes": [], "sources": [], "not_in_reference": []},
        "clinical_cnvs": None,
    },
    "repeat_expansions": {"catalogue": {"loci": 0, "sources": [], "digest": "0" * 64}, "loci": []},
    "paraphase": {"catalogue": {"regions": 0, "digest": "0" * 64}, "genes": []},
    "coverage_tracks": {"tracks": []},
}


# The real reader, kept before the fixture below stands a stub in for it.
_REPORT_CODE_TAKEN = rss._report_code_taken


@pytest.fixture(autouse=True)
def _record_sections(monkeypatch):
    async def _sections(session, *, context, reported, reported_structural, shared_nipt=None):
        return copy.deepcopy(_RECORD_SECTIONS)

    async def _code_free(session, code):
        return False  # no other record has the new record's report code

    monkeypatch.setattr(rss, "build_record_sections", _sections)
    monkeypatch.setattr(rss, "_report_code_taken", _code_free)


def test_canonical_hash_is_stable_and_order_independent() -> None:
    a = rss._canonical_hash({"x": 1, "y": [1, 2], "z": "t"})
    b = rss._canonical_hash({"z": "t", "y": [1, 2], "x": 1})
    assert a == b
    assert len(a) == 64  # sha256 hex
    assert a != rss._canonical_hash({"x": 2, "y": [1, 2], "z": "t"})


def _user():
    return types.SimpleNamespace(username="lab-signer", email="b@x.org", id=None)


# What evaluate_classification_drift reports for a family with no SV/CNV classification.
_NO_STRUCTURAL_DRIFT = {"checked": 0, "drifted_count": 0, "drifted": []}


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
    monkeypatch,
    *,
    drifted_count: int,
    qc_status: str = "pass",
    import_incomplete=None,
    import_unfinished=None,
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
            "structural": _NO_STRUCTURAL_DRIFT,
        }

    async def _reviews(session, family_uuid):
        return [{"variant_id": "1-1-A-G", "acmg_class": "acmg_class_4", "evidence_snapshot": {"annotation_set_hash": "h"}}]

    async def _audit(*args, **kwargs):
        return None

    async def _qc(session, *, family_id, user, project_id=None, shared_nipt=None):
        # Sample QC is computed inside build_report_snapshot now; stub it with a
        # parameterizable overall_status (default "pass" -> the QC gate stays open).
        return SampleIntegrityReport(overall_status=qc_status)

    async def _import_state(session, family_uuid):
        # The family's import_incomplete flag (default: a complete import).
        assert family_uuid == "u1"
        return import_incomplete

    async def _unfinished_state(session, family_uuid):
        # The imports that stopped part-way (default: none).
        assert family_uuid == "u1"
        return dict(import_unfinished or {})

    monkeypatch.setattr(rss, "build_family_metadata_context", _ctx)
    monkeypatch.setattr(rss, "get_family_annotation_manifest", _manifest)
    monkeypatch.setattr(rss, "evaluate_classification_drift", _drift)
    monkeypatch.setattr(rss, "_reported_reviews", _reviews)
    monkeypatch.setattr(rss, "_reported_structural_reviews", _no_structural_reviews)
    monkeypatch.setattr(rss, "record_clinical_event", _audit)
    monkeypatch.setattr(rss, "get_family_sample_integrity_qc", _qc)
    monkeypatch.setattr(rss, "_import_incomplete_state", _import_state, raising=False)
    monkeypatch.setattr(rss, "_import_unfinished_state", _unfinished_state, raising=False)
    # No import of the family is queued or running, and no writer holds its variants.
    monkeypatch.setattr(rss, "_active_import_job", _no_import_job, raising=False)
    monkeypatch.setattr(rss, "try_share_family_variant_writes", _variant_writes_shared, raising=False)


async def _no_structural_reviews(session, family_uuid):
    return []


async def _no_import_job(session, family_identifier):
    return None


async def _variant_writes_shared(session, family_uuid):
    return True


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


# ---------------------------------------------------------------------------
# A family whose data is being written (TF-06 H16)
# ---------------------------------------------------------------------------
#
# A package import writes a family over minutes and commits as it goes; the
# import-incomplete flag is only set once it has failed. A sign-out while it ran froze a
# half-imported family as a complete signed record.


def _wrote_a_signout(session) -> bool:
    return any("INSERT INTO report_signouts" in str(args[0]) for args, _ in session.executed)


def _with_every_override():
    return {
        "acknowledge_drift": True,
        "drift_acknowledgement_reason": "reviewed",
        "acknowledge_qc": True,
        "qc_acknowledgement_reason": "reviewed",
        "acknowledge_import_incomplete": True,
        "import_incomplete_acknowledgement_reason": "reviewed",
    }


@pytest.mark.parametrize(("status", "said"), [("running", "in progress"), ("validating", "in progress"), ("queued", "queued")])
def test_sign_out_is_refused_while_an_import_of_the_family_is_queued_or_running(
    monkeypatch, status, said
) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    looked_up: list[str] = []

    async def _job(session, family_identifier):
        looked_up.append(family_identifier)
        return {"id": "job-7", "status": status}

    async def _must_not_build(*args, **kwargs):
        raise AssertionError("the snapshot of a family being imported must not be read")

    monkeypatch.setattr(rss, "_active_import_job", _job, raising=False)
    monkeypatch.setattr(rss, "build_report_snapshot", _must_not_build)
    session = _Session()
    with pytest.raises(HTTPException) as excinfo:
        # No acknowledgement opens this gate: the data is not yet what it will be.
        asyncio.run(rss.sign_out_report(session, family_id="FAM1", user=_user(), **_with_every_override()))

    assert excinfo.value.status_code == 409
    detail = excinfo.value.detail
    assert detail["gate"] == "import_in_progress"
    assert detail["import_job"] == {"id": "job-7", "status": status}
    assert f"is {said} (import job job-7)" in detail["message"]
    assert looked_up == ["FAM1"], "looked up by the family's identifier, as the job records it"
    assert not _wrote_a_signout(session)


def test_sign_out_is_refused_while_a_writer_holds_the_familys_variants(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    shared: list[str] = []

    async def _held_by_a_writer(session, family_uuid):
        shared.append(family_uuid)
        return False

    async def _must_not_build(*args, **kwargs):
        raise AssertionError("the snapshot of a family being written must not be read")

    monkeypatch.setattr(rss, "try_share_family_variant_writes", _held_by_a_writer, raising=False)
    monkeypatch.setattr(rss, "build_report_snapshot", _must_not_build)
    session = _Session()
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(session, family_id="FAM1", user=_user(), **_with_every_override()))

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["gate"] == "variant_writes_in_progress"
    assert "being written" in excinfo.value.detail["message"]
    assert shared == ["u1"], "the family's variant-write locks, by its uuid"
    assert not _wrote_a_signout(session)


def test_sign_out_is_refused_when_an_import_starts_while_the_snapshot_is_read(monkeypatch) -> None:
    # The import's job is claimed after the first check and writes the pedigree and samples
    # before it takes the variant-write locks: the snapshot may hold part of its writes.
    _patch_common(monkeypatch, drifted_count=0)
    jobs = iter([None, {"id": "job-8", "status": "running"}])

    async def _job(session, family_identifier):
        return next(jobs)

    audited: list = []

    async def _audit(*args, **kwargs):
        audited.append(kwargs)

    monkeypatch.setattr(rss, "_active_import_job", _job, raising=False)
    monkeypatch.setattr(rss, "record_clinical_event", _audit)
    session = _Session()
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(session, family_id="FAM1", user=_user()))

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["gate"] == "import_in_progress"
    assert excinfo.value.detail["import_job"] == {"id": "job-8", "status": "running"}
    assert not _wrote_a_signout(session)
    assert audited == []


def test_sign_out_keeps_writers_out_from_before_its_snapshot_until_it_commits(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    order: list[str] = []

    async def _job(session, family_identifier):
        order.append("import jobs checked")
        return None

    async def _share(session, family_uuid):
        order.append("variant writes shared")
        return True

    real_build = rss.build_report_snapshot

    async def _build(*args, **kwargs):
        order.append("snapshot read")
        return await real_build(*args, **kwargs)

    class _OrderedSession(_Session):
        async def execute(self, *args, **kwargs):
            sql = str(args[0])
            params = args[1] if len(args) > 1 else {}
            if "pg_advisory_xact_lock" in sql and params.get("k") == "report-code":
                order.append("report codes locked")
            elif "pg_advisory_xact_lock" in sql:
                order.append("sign-out chain locked")
            elif "INSERT INTO report_signouts" in sql:
                order.append("signed")
            return await super().execute(*args, **kwargs)

        async def commit(self) -> None:
            order.append("commit")

    monkeypatch.setattr(rss, "_active_import_job", _job, raising=False)
    monkeypatch.setattr(rss, "try_share_family_variant_writes", _share, raising=False)
    monkeypatch.setattr(rss, "build_report_snapshot", _build)
    out = asyncio.run(rss.sign_out_report(_OrderedSession(), family_id="FAM1", user=_user()))

    assert out["version"] == 1
    # The variant-write locks before the sign-out chain's, as every writer takes them
    # first; the second import check after the snapshot and before anything is written.
    assert order == [
        "import jobs checked",
        "variant writes shared",
        "snapshot read",
        "import jobs checked",
        "sign-out chain locked",
        # Held to the commit, so no other sign-out takes the same report code meanwhile.
        "report codes locked",
        "signed",
        "commit",
    ]


def test_the_active_import_job_query_matches_what_an_import_writes() -> None:
    # A running import names its family in family_id once its validation read the package;
    # a queued one only by the family its request named. A dry run writes nothing; a
    # finished job no longer writes.
    sql = " ".join(str(rss._ACTIVE_IMPORT_JOB).split())
    assert "status IN ('queued', 'validating', 'running')" in sql
    assert "NOT dry_run" in sql
    assert "family_id = :family_identifier" in sql
    assert "metadata ->> 'requested_family_id' = :family_identifier" in sql


def test_the_active_import_job_is_read_as_a_mapping() -> None:
    class _Mappings:
        def __init__(self, rows):
            self._rows = rows

        def first(self):
            return self._rows[0] if self._rows else None

    class _JobResult:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return _Mappings(self._rows)

    class _JobSession:
        def __init__(self, rows):
            self.rows = rows
            self.params: list = []

        async def execute(self, statement, params):
            self.params.append(params)
            return _JobResult(self.rows)

    running = _JobSession([{"id": "job-9", "status": "running"}])
    assert asyncio.run(rss._active_import_job(running, "FAM1")) == {"id": "job-9", "status": "running"}
    assert running.params == [{"family_identifier": "FAM1"}]
    assert asyncio.run(rss._active_import_job(_JobSession([]), "FAM1")) is None


# ---------------------------------------------------------------------------
# A family made again under its ID (TRACE-13)
# ---------------------------------------------------------------------------


class _Value:
    """A result that holds one value."""

    def __init__(self, value) -> None:
        self._value = value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _SignoutTable(_Session):
    """``report_signouts`` in memory. It answers what a sign-out reads and writes of the
    table (the version, the chain head, the new row) and the chain verifier's walk, from its
    rows; every other statement gets what ``_Session`` gives.

    A version read is answered over the rows its WHERE clause names: those of the family ID
    (``family_identifier = :family_identifier``) and those of the family row
    (``family_id = CAST(:family_uuid AS uuid)``), either one."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict] = []

    async def execute(self, *args, **kwargs):
        self.executed.append((args, kwargs))
        sql = " ".join(str(args[0]).split())
        params = args[1] if len(args) > 1 else {}
        if sql.startswith("INSERT INTO report_signouts"):
            self.rows.append({**params, "snapshot": json.loads(params["snapshot"])})
        elif "MAX(version)" in sql and "FROM report_signouts" in sql:
            named = [
                row["version"]
                for row in self.rows
                if (
                    "family_identifier = :family_identifier" in sql
                    and row["family_identifier"] == params.get("family_identifier")
                )
                or ("family_id = CAST(:family_uuid AS uuid)" in sql and row["family_id"] == params.get("family_uuid"))
            ]
            return _Value(max(named, default=0))
        elif sql.startswith("SELECT row_hash FROM report_signouts"):
            # The chain head: the family ID's highest version.
            chain = [row for row in self.rows if row["family_identifier"] == params["fident"]]
            return _Value(max(chain, key=lambda row: row["version"])["row_hash"] if chain else None)
        elif "FROM report_signouts" in sql and "ORDER BY version ASC" in sql:
            # The verifier's walk: the family ID's chained rows in version order (a tie
            # keeps the order the rows were written in).
            chain = sorted(
                (
                    {**row, "id": str(row["version"])}
                    for row in self.rows
                    if row["family_identifier"] == params["fident"] and row["row_hash"] is not None
                ),
                key=lambda row: row["version"],
            )
            return types.SimpleNamespace(mappings=lambda: types.SimpleNamespace(all=lambda: chain))
        return _Result()

    def delete_family(self, family_uuid: str) -> None:
        """What the family's deletion does to its sign-outs: ON DELETE SET NULL."""
        for row in self.rows:
            if row["family_id"] == family_uuid:
                row["family_id"] = None


def _sign_out_two_families(monkeypatch) -> _SignoutTable:
    """Family FAM1 signed out twice, deleted, made again under its ID (another family row)
    and signed out twice more: the case TRACE-13 found."""
    _patch_common(monkeypatch, drifted_count=0)
    family = {"uuid": "u1"}

    async def _ctx(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid=family["uuid"], family_id="FAM1", assembly_name="GRCh38")

    async def _complete_import(session, family_uuid):
        return None

    async def _no_unfinished_import(session, family_uuid):
        return {}

    monkeypatch.setattr(rss, "build_family_metadata_context", _ctx)
    monkeypatch.setattr(rss, "_import_incomplete_state", _complete_import, raising=False)
    monkeypatch.setattr(rss, "_import_unfinished_state", _no_unfinished_import, raising=False)
    table = _SignoutTable()
    for family_uuid in ("u1", "u2"):
        family["uuid"] = family_uuid
        for _ in range(2):
            asyncio.run(rss.sign_out_report(table, family_id="FAM1", user=_user()))
        if family_uuid == "u1":
            table.delete_family("u1")
    return table


def test_a_family_made_again_under_its_id_numbers_its_sign_outs_on_and_both_verify(monkeypatch) -> None:
    table = _sign_out_two_families(monkeypatch)

    # Numbered per family ID: the new family's first sign-out is version 3, not 1 again.
    assert [(row["family_id"], row["version"]) for row in table.rows] == [
        (None, 1),
        (None, 2),
        ("u2", 3),
        ("u2", 4),
    ]
    # One chain, in the order it was written: the new family's first record links to the
    # deleted family's last one.
    assert table.rows[0]["prev_hash"] is None
    assert [row["prev_hash"] for row in table.rows[1:]] == [row["row_hash"] for row in table.rows[:-1]]
    chain = asyncio.run(rss.verify_report_signout_chain(table, "FAM1"))
    assert (chain.verified, chain.rows_checked) == (True, 4), chain
    # The version is the family ID's, and so the report code names one record of either family.
    codes = [report_code("FAM1", row["version"], row["content_hash"]) for row in table.rows]
    assert len(set(codes)) == 4 and None not in codes
    assert [parse_report_code(code).version for code in codes if code] == [1, 2, 3, 4]


def test_a_record_of_either_family_changed_afterwards_still_breaks_the_chain(monkeypatch) -> None:
    table = _sign_out_two_families(monkeypatch)
    rows = copy.deepcopy(table.rows)

    # The deleted family's last record, signed by someone else.
    table.rows[1]["signed_out_by"] = "impostor"
    edited = asyncio.run(rss.verify_report_signout_chain(table, "FAM1"))
    assert (edited.verified, edited.first_bad_row, edited.reason) == (
        False,
        "2",
        "row_hash mismatch (row content tampered)",
    )

    # The new family's first record, its snapshot changed.
    table.rows = copy.deepcopy(rows)
    table.rows[2]["snapshot"]["note"] = "ALTERED"
    altered = asyncio.run(rss.verify_report_signout_chain(table, "FAM1"))
    assert (altered.verified, altered.first_bad_row) == (False, "3"), altered
    assert "content_hash" in (altered.reason or "")

    # The deleted family's last record removed: the new family's first no longer links.
    table.rows = [row for index, row in enumerate(copy.deepcopy(rows)) if index != 1]
    removed = asyncio.run(rss.verify_report_signout_chain(table, "FAM1"))
    assert (removed.verified, removed.first_bad_row) == (False, "3"), removed
    assert "prev_hash" in (removed.reason or "")


def test_the_next_version_counts_every_family_made_under_the_id() -> None:
    table = _SignoutTable()
    table.rows = [
        {"family_id": None, "family_identifier": "FAM1", "version": 1},
        {"family_id": None, "family_identifier": "FAM1", "version": 2},
        {"family_id": None, "family_identifier": "FAM9", "version": 7},
    ]
    # A family made again under FAM1 signs version 3; another family ID counts for nothing.
    assert asyncio.run(rss._next_version(table, "FAM1", "u2")) == 3
    assert asyncio.run(rss._next_version(table, "FAM5", "u5")) == 1
    # A row of the family stored without its ID still counts.
    table.rows.append({"family_id": "u2", "family_identifier": None, "version": 4})
    assert asyncio.run(rss._next_version(table, "FAM1", "u2")) == 5
    # Read with bound parameters, never spliced into the statement.
    sql, params = table.executed[-1][0]
    assert ":family_identifier" in str(sql) and "FAM1" not in str(sql)
    assert params == {"family_identifier": "FAM1", "family_uuid": "u2"}


def test_a_deleted_familys_record_keeps_its_report_code_from_a_new_sign_out() -> None:
    # The uniqueness check reads every record with the code's version and hash prefix, a
    # deleted family's too (its family_id nulled), so no sign-out takes its code again.
    code = report_code("FAM1", 2, _CONTENT_HASH)
    assert code is not None
    assert asyncio.run(_REPORT_CODE_TAKEN(_CodeSession([_code_row(family_uuid=None)]), code)) is True
    assert "family_id" not in " ".join(str(rss._REPORT_CODE_CANDIDATES).split()).split("WHERE")[1]


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
    # Identical clinical content with drift rows arriving in different orders
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
                "structural": _NO_STRUCTURAL_DRIFT,
            }

        async def _reviews(session, family_uuid):
            return [{"variant_id": "1-1-A-G", "acmg_class": "acmg_class_4", "evidence_snapshot": {"annotation_set_hash": "h"}}]

        async def _qc(session, *, family_id, user, project_id=None, shared_nipt=None):
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
    # A "fail" Sample QC (possible sample/pedigree swap, TF-06 H4 / S5) blocks
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
    async def _qc(session, *, family_id, user, project_id=None, shared_nipt=None):
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
            paternity_check=PaternityCheck("FATHER", 2, 1, 0, 0, "warn", "Too few paternal-informative sites."),
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
            paternity_check=PaternityCheck("FATHER", 40, 2, 60, 55, "pass", "Paternity supported."),
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
# Each reason of a Sample-QC refusal names its sample, pair or child (CLIN-8)
# ---------------------------------------------------------------------------

_NO_CHRX = "No chrX genotypes available for sex inference."
_PARTNERS_NAMED = [
    "Sample PARTNER_A: no chrX genotypes available for sex inference.",
    "Sample PARTNER_B: no chrX genotypes available for sex inference.",
]

# The refusal's message: a sentence for each kind of concern the gate holds, then what to do
# (CLIN-58). The concerns themselves are in the refusal's lists, one group each.
_QC_FAILED = "Sample-integrity QC status is 'fail' (possible sample or pedigree swap — TF-06 H4)."
_COULD_NOT_RUN = (
    "A sample-integrity check that confirms the pedigree or a sample's identity could not run. "
    "A sample swap can present as missing data rather than a mismatch."
)
_NIPT_CHECK_FAILED = "A NIPT quality check failed."
_RESOLVE_OR_ACKNOWLEDGE = (
    "Resolve each concern listed, or acknowledge them with a reason to sign out anyway."
)


def _couple_without_chrx() -> SampleIntegrityReport:
    """A carrier couple whose callset holds no chrX site: both sex checks could not run, with
    one message, and no relatedness check anchors either partner."""
    return SampleIntegrityReport(
        overall_status="warn",
        application="couple",
        sex_checks=[
            SexCheck("PARTNER_A", "female", "indeterminate", None, 0, "skip", _NO_CHRX),
            SexCheck("PARTNER_B", "male", "indeterminate", None, 0, "skip", _NO_CHRX),
        ],
    )


def _trio_with_ten_sites() -> SampleIntegrityReport:
    """A trio with ten autosomal sites and no chrX: its parent-child and Mendelian checks
    cannot run, as the QC runs them."""
    genotypes = [(0, 1)] * 10
    return evaluate_sample_integrity(
        {"CHILD": genotypes, "FATHER": genotypes, "MOTHER": genotypes},
        {},
        PedigreeSpec(
            recorded_sex={"CHILD": "male", "FATHER": "male", "MOTHER": "female"},
            parents_of={"CHILD": {"father": "FATHER", "mother": "MOTHER"}},
        ),
        profile=profile_for("wgs"),
    )


def _qc_refusal(monkeypatch, report: SampleIntegrityReport) -> dict:
    _patch_common(monkeypatch, drifted_count=0, qc_status=report.overall_status)
    _patch_qc_report(monkeypatch, report)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["gate"] == "sample_qc"
    return excinfo.value.detail


def test_the_qc_refusal_names_the_sample_of_each_reason(monkeypatch) -> None:
    # Neither partner's sex could be checked: the refusal gave "No chrX genotypes available
    # for sex inference." twice and named neither. Each reason names its sample, in the
    # list the report page shows under the message (CLIN-58: the message names the kind of
    # concern, the lists hold the concerns).
    detail = _qc_refusal(monkeypatch, _couple_without_chrx())

    assert detail["unverifiable_checks"] == _PARTNERS_NAMED
    assert detail["failed_checks"] == []
    assert detail["warning_checks"] == []
    assert detail["message"] == f"{_COULD_NOT_RUN} {_RESOLVE_OR_ACKNOWLEDGE}"


def test_the_qc_refusal_names_the_pair_and_the_child_with_its_parents(monkeypatch) -> None:
    # The relatedness and Mendelian messages say "too few sites" without the pair or the
    # child they read.
    detail = _qc_refusal(monkeypatch, _trio_with_ten_sites())

    pairs = [
        f"Samples {a} and {b}: too few shared sites to assess relatedness (kinship 0.500, IBS0 "
        "0.00%, 10 sites)."
        for a, b in (("CHILD", "FATHER"), ("CHILD", "MOTHER"), ("FATHER", "MOTHER"))
    ]
    child = (
        "Sample CHILD (child of FATHER and MOTHER): too few informative sites for a reliable "
        "estimate (0/10 sites (0.00%))."
    )
    # The parents are an unrelated pair the pedigree does not assert: no reason of the gate.
    assert detail["unverifiable_checks"] == [pairs[0], pairs[1], child]
    # The acknowledgement prompt lists every concerning check, named the same way; the sex
    # checks that could not run are not among them (they are not warnings or fails).
    assert detail["qc_summary"]["messages"] == [*pairs, child]
    # In the groups the dialog shows, each concern is listed once: the parents' pair is a
    # warning, which does not gate on its own; the checks that could not run are not
    # listed again among the warnings (CLIN-58).
    assert detail["warning_checks"] == [pairs[2]]
    assert detail["failed_checks"] == []


def test_the_qc_refusal_names_the_nipt_father(monkeypatch) -> None:
    report = SampleIntegrityReport(
        overall_status="warn",
        application="nipt",
        paternity_check=evaluate_paternity(
            "FATHER",
            hom_alt_transmitted=3,
            hom_alt_not_transmitted=0,
            het_transmitted=0,
            het_not_transmitted=0,
        ),
        fetal_sex_check=evaluate_fetal_sex("indeterminate", 0, 0, 0),
        category_qc_check=evaluate_nipt_category_qc({2: 3, 3: 1}),
    )

    detail = _qc_refusal(monkeypatch, report)

    paternity = (
        "Sample FATHER (father): too few paternal-informative sites (3 homozygous, 0 het) to "
        "assess paternity."
    )
    # The maternal lineage and the fetal sex are read from the cfDNA as a whole: their
    # checks hold no sample, and their sentences stay as they were.
    assert detail["unverifiable_checks"] == [
        paternity,
        "NIPT maternal lineage could not be verified (too few maternal-informative sites) — "
        "possible wrong mother.",
    ]
    assert detail["qc_summary"]["messages"] == [
        paternity,
        "Fetal sex indeterminate — too few informative paternal-X sites (0 paternal-X "
        "transmitted, 0 absent of 0 sites).",
    ]
    # The paternity that could not run is listed once, among the checks that could not run.
    assert detail["warning_checks"] == [
        "Fetal sex indeterminate — too few informative paternal-X sites (0 paternal-X "
        "transmitted, 0 absent of 0 sites).",
    ]


def test_a_failed_qc_refusal_names_each_failing_check(monkeypatch) -> None:
    report = SampleIntegrityReport(
        overall_status="fail",
        application="wgs",
        sex_checks=[
            SexCheck(
                "CHILD", "female", "male", 0.01, 500, "fail",
                "Recorded female but genotypes indicate male (1.0% chrX het over 500 sites) — "
                "possible sample swap or mislabel.",
            ),
        ],
        relatedness_checks=[
            RelatednessCheck(
                "CHILD", "FATHER", "parent-child", "unrelated", 0.01, 0.04, 5000, "fail",
                "Recorded parent-child but genotypes look unrelated (kinship 0.010, IBS0 4.00%, "
                "5000 sites) — possible sample swap or wrong parent.",
            ),
        ],
        mendelian_checks=[
            MendelianCheck(
                "CHILD", ["FATHER"], 5000, 600, 0.12, "fail",
                "High Mendelian-error rate 600/5000 sites (12.00%) — likely swap or wrong parent.",
            ),
        ],
    )

    detail = _qc_refusal(monkeypatch, report)

    assert detail["qc_summary"]["messages"] == [
        "Sample CHILD: recorded female but genotypes indicate male (1.0% chrX het over 500 "
        "sites) — possible sample swap or mislabel.",
        "Samples CHILD and FATHER: recorded parent-child but genotypes look unrelated (kinship "
        "0.010, IBS0 4.00%, 5000 sites) — possible sample swap or wrong parent.",
        "Sample CHILD (child of FATHER): high Mendelian-error rate 600/5000 sites (12.00%) — "
        "likely swap or wrong parent.",
    ]
    # Each is a failure, in the group of the checks that failed.
    assert detail["failed_checks"] == detail["qc_summary"]["messages"]
    assert detail["unverifiable_checks"] == [] and detail["warning_checks"] == []
    assert detail["message"] == f"{_QC_FAILED} {_RESOLVE_OR_ACKNOWLEDGE}"


def test_an_acknowledged_refusal_audits_the_named_reasons_and_freezes_the_checks_as_they_are(
    monkeypatch,
) -> None:
    captured: dict = {}

    async def _capture(*args, **kwargs):
        captured.update(kwargs)

    _patch_common(monkeypatch, drifted_count=0, qc_status="warn")
    monkeypatch.setattr(rss, "record_clinical_event", _capture)
    _patch_qc_report(monkeypatch, _couple_without_chrx())
    out = asyncio.run(
        rss.sign_out_report(
            _Session(), family_id="FAM1", user=_user(),
            acknowledge_qc=True, qc_acknowledgement_reason="Both partners were sexed by karyotype.",
        )
    )

    # The audit event records what was acknowledged, each reason with its sample.
    assert captured["after"]["qc_unverifiable"] == _PARTNERS_NAMED
    # The signed record holds each check as the QC made it, beside its sample: the name is
    # not written into the check's message.
    assert [
        (check["sample_id"], check["message"]) for check in out["snapshot"]["sample_qc"]["sex_checks"]
    ] == [("PARTNER_A", _NO_CHRX), ("PARTNER_B", _NO_CHRX)]


def test_a_record_signed_before_the_reasons_named_their_samples_still_matches(monkeypatch) -> None:
    # The checks are frozen as before, so a version signed before CLIN-8 does not read as
    # changed: only the refusal and the audit event name the samples.
    _patch_check(monkeypatch, reviews=[_REVIEW])
    _patch_qc_report(monkeypatch, _couple_without_chrx())
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))

    def _sex_check(sample_id: str, recorded_sex: str) -> dict:
        return {
            "sample_id": sample_id, "recorded_sex": recorded_sex, "inferred_sex": "indeterminate",
            "x_het_rate": None, "x_sites": 0, "status": "skip", "message": _NO_CHRX,
        }

    # The Sample QC as a sign-out before CLIN-8 froze it.
    signed["sample_qc"] = {
        "overall_status": "warn", "application": "couple", "application_label": "",
        "application_summary": "", "genotype_source": None,
        "sex_checks": [_sex_check("PARTNER_A", "female"), _sex_check("PARTNER_B", "male")],
        "relatedness_checks": [], "mendelian_checks": [], "paternity_check": None,
        "fetal_sex_check": None, "category_qc_check": None, "autosomal_sites": 0, "notes": [],
    }
    out = asyncio.run(
        rss.compare_report_with_latest_signout(
            _CheckSession(_signed_row(signed)), family_id="FAM1", user=_user()
        )
    )
    assert out["matches"] is True
    assert out["changed_sections"] == []


@pytest.mark.parametrize(
    ("message", "reads"),
    [
        ("Too few shared sites.", "Sample X: too few shared sites."),
        ("Sex could not be checked: no calls.", "Sample X: sex could not be checked: no calls."),
        # An abbreviation, a name or a lower-case word keeps its first letter.
        ("NIPT paternity could not be verified.", "Sample X: NIPT paternity could not be verified."),
        ("Mendelian consistency could not be verified.", "Sample X: Mendelian consistency could not be verified."),
        ("X-linked sites are missing.", "Sample X: X-linked sites are missing."),
        ("chrX calls are missing.", "Sample X: chrX calls are missing."),
    ],
)
def test_a_reason_reads_on_from_the_name_it_starts_with(message, reads) -> None:
    assert rss._named("Sample X", message) == reads
    # With nothing to name, the message as it is.
    assert rss._named(None, message) == message


def test_a_check_without_its_message_or_its_samples_still_gives_its_reason() -> None:
    qc = {
        "relatedness_checks": [
            {"sample_a": "CHILD", "sample_b": "FATHER", "expected_relationship": "parent-child",
             "informative_sites": 0},
            {"expected_relationship": "sibling", "informative_sites": 0,
             "message": "Too few shared sites to assess relatedness."},
        ],
        "mendelian_checks": [
            {"child": "CHILD", "parents": ["MOTHER"], "informative_sites": 0},
            {"child": "SIB", "parents": [], "informative_sites": 0,
             "message": "Too few informative sites for a reliable estimate."},
            {"parents": ["MOTHER"], "informative_sites": 0,
             "message": "Too few informative sites for a reliable estimate."},
        ],
        "paternity_check": {"father": "FATHER", "hom_alt_transmitted": 0},
        "sex_checks": [{"sample_id": "AUNT", "inferred_sex": "indeterminate"}],
    }

    assert rss._unverifiable_swap_checks(qc) == [
        "Samples CHILD and FATHER: relatedness (expected parent-child) could not be verified.",
        "Too few shared sites to assess relatedness.",
        "Sample CHILD (child of MOTHER): Mendelian consistency could not be verified.",
        "Sample SIB: too few informative sites for a reliable estimate.",
        "Too few informative sites for a reliable estimate.",
        "Sample FATHER (father): NIPT paternity could not be verified (too few "
        "paternal-informative sites).",
        "Sample AUNT: sex/identity could not be verified, and no relatedness check anchors this "
        "sample.",
    ]
    # The prompt lists only checks with a message; one without its sample reads as it is.
    summary = rss._qc_failure_summary(
        {
            "sex_checks": [{"sample_id": "AUNT", "status": "warn"}],
            "paternity_check": {"status": "warn", "message": "Paternal transmission off its expectation."},
        }
    )
    assert summary["messages"] == ["Paternal transmission off its expectation."]


# ---------------------------------------------------------------------------
# A failure does not hide a check that could not run: each group of concerns is listed,
# and audited, on its own (CLIN-58)
# ---------------------------------------------------------------------------

_WRONG_FATHER = (
    "Samples CHILD and FATHER: recorded parent-child but genotypes look unrelated (kinship "
    "0.010, IBS0 4.00%, 5000 sites) — possible sample swap or wrong parent."
)
_RELATIVE_UNCHECKED = "Sample RELATIVE: no chrX genotypes available for sex inference."
_PARENTS_RELATED = (
    "Samples FATHER and MOTHER: parents look third-degree — possible consanguinity (kinship "
    "0.060, IBS0 1.00%, 5000 sites)."
)


def _failed_trio_beside_an_unchecked_relative() -> SampleIntegrityReport:
    """A trio whose child does not look like the recorded father's, and whose parents look
    related, beside a relative of the family whose callset holds no chrX: her sex, the only
    check of her identity (no relatedness check the pedigree asserts covers her), could not
    run."""
    return SampleIntegrityReport(
        overall_status="fail",
        application="wgs",
        sex_checks=[
            SexCheck(
                "CHILD", "male", "male", 0.01, 500, "pass",
                "Genotype-inferred sex matches the record (1.0% chrX het over 500 sites).",
            ),
            SexCheck("RELATIVE", "female", "indeterminate", None, 0, "skip", _NO_CHRX),
        ],
        relatedness_checks=[
            RelatednessCheck(
                "CHILD", "FATHER", "parent-child", "unrelated", 0.01, 0.04, 5000, "fail",
                "Recorded parent-child but genotypes look unrelated (kinship 0.010, IBS0 4.00%, "
                "5000 sites) — possible sample swap or wrong parent.",
            ),
            RelatednessCheck(
                "CHILD", "MOTHER", "parent-child", "parent-child", 0.25, 0.0, 5000, "pass",
                "Confirmed parent-child (kinship 0.250, IBS0 0.00%, 5000 sites).",
            ),
            RelatednessCheck(
                "FATHER", "MOTHER", "unrelated", "third-degree", 0.06, 0.01, 5000, "warn",
                "Parents look third-degree — possible consanguinity (kinship 0.060, IBS0 1.00%, "
                "5000 sites).",
            ),
        ],
    )


def test_a_failed_qc_refusal_lists_the_check_that_could_not_run_beside_the_failure(
    monkeypatch,
) -> None:
    # The refusal said only that the QC failed, and listed only the failure and the warning:
    # the relative whose sex could not be checked, which gates on its own, reached the
    # refusal's list of checks that could not run but not the dialog, so the reason written
    # there might not cover it. The message now names both kinds of concern, and each
    # concern is in its own group, once.
    detail = _qc_refusal(monkeypatch, _failed_trio_beside_an_unchecked_relative())

    assert detail["message"] == f"{_QC_FAILED} {_COULD_NOT_RUN} {_RESOLVE_OR_ACKNOWLEDGE}"
    assert detail["failed_checks"] == [_WRONG_FATHER]
    assert detail["unverifiable_checks"] == [_RELATIVE_UNCHECKED]
    assert detail["nipt_failed_checks"] == []
    assert detail["warning_checks"] == [_PARENTS_RELATED]
    # The skipped sex check is no warning or failure of its own: the summary never held it.
    assert _RELATIVE_UNCHECKED not in detail["qc_summary"]["messages"]


def test_an_acknowledged_refusal_audits_each_group_its_dialog_listed(monkeypatch) -> None:
    # The audit event records every group the refusal listed, as the dialog showed them, so
    # the trail says what the acknowledgement covered: before, it held the QC status but not
    # which checks failed, nor the warnings beside them.
    detail = _qc_refusal(monkeypatch, _failed_trio_beside_an_unchecked_relative())
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(
        rss.sign_out_report(
            _Session(), family_id="FAM1", user=_user(), acknowledge_qc=True,
            qc_acknowledgement_reason="Father re-sampled; the relative was sexed by karyotype.",
        )
    )

    after = captured["after"]
    assert after["qc_failed"] == detail["failed_checks"] == [_WRONG_FATHER]
    assert after["qc_unverifiable"] == detail["unverifiable_checks"] == [_RELATIVE_UNCHECKED]
    assert after["nipt_failed_checks"] == detail["nipt_failed_checks"] == []
    assert after["qc_warnings"] == detail["warning_checks"] == [_PARENTS_RELATED]
    assert after["qc_status"] == "fail" and after["acknowledged_qc"] is True
    assert out["snapshot"]["qc_acknowledgement_reason"] == (
        "Father re-sampled; the relative was sexed by karyotype."
    )


def test_a_sign_out_the_qc_does_not_gate_audits_its_warnings_and_no_other_concern(
    monkeypatch,
) -> None:
    # Warnings alone do not gate: no dialog, no acknowledgement, and the event says which
    # warnings the signed QC held.
    report = SampleIntegrityReport(
        overall_status="warn",
        application="wgs",
        relatedness_checks=[_failed_trio_beside_an_unchecked_relative().relatedness_checks[2]],
    )
    _patch_common(monkeypatch, drifted_count=0, qc_status="warn")
    _patch_qc_report(monkeypatch, report)
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))

    assert out["snapshot"]["acknowledged_qc"] is False
    after = captured["after"]
    assert (after["qc_failed"], after["qc_unverifiable"], after["nipt_failed_checks"]) == ([], [], [])
    assert after["qc_warnings"] == [_PARENTS_RELATED]


def test_a_failed_check_without_its_message_is_listed_by_what_it_is() -> None:
    # No failure is left out of the list the dialog shows: one without a message of its own
    # is named by what it is. A warning without one has nothing to say, as in the summary.
    qc = {
        "sex_checks": [{"sample_id": "CHILD", "status": "fail"}],
        "relatedness_checks": [
            {"sample_a": "CHILD", "sample_b": "FATHER", "status": "fail", "message": ""}
        ],
        "mendelian_checks": [{"child": "CHILD", "parents": ["FATHER", "MOTHER"], "status": "fail"}],
        "paternity_check": {"father": "FATHER", "status": "fail"},
        "fetal_sex_check": {"status": "warn"},
        "category_qc_check": {"status": "fail"},
    }

    assert rss._failed_qc_checks(qc) == [
        "Sample CHILD: sex check failed.",
        "Samples CHILD and FATHER: relatedness check failed.",
        "Sample CHILD (child of FATHER and MOTHER): Mendelian check failed.",
        "Sample FATHER (father): NIPT paternity check failed.",
        "NIPT category-distribution check failed.",
    ]
    assert rss._warning_qc_checks(qc, []) == []


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
        "reference_modules": list(ams.REFERENCE_MODULE_KEYS),
        "sequencing_qc": {"thresholds": {}, "samples": {}, "unavailable": "QC thresholds could not be resolved"},
    }
    assert rss.snapshot_gaps(snapshot) == [
        _QC_GAP,
        {"section": "modules", "item": "Monarch", "reason": "lookup failed"},
    ]
    complete = {
        "modules": [{"key": "clinvar", "version": "2026-05"}],
        # HPO was looked up and none was loaded: nothing to capture, so no gap.
        "reference_modules": list(ams.REFERENCE_MODULE_KEYS),
        "sequencing_qc": {"thresholds": {}, "samples": {}},
    }
    assert rss.snapshot_gaps(complete) == []
    assert rss.snapshot_gaps(None) == []


_HPO_PREDATES_GAP = {"section": "modules", "item": "HPO", "reason": "signed before CoGA recorded its version"}


def test_snapshot_gaps_name_a_reference_module_added_after_the_record_was_signed() -> None:
    # CoGA started recording the HPO release after this record was signed: its list of the
    # reference modules it looked up does not name HPO, and it holds no HPO version.
    older = {
        "modules": [{"key": "clinvar", "version": "2026-05"}],
        "reference_modules": ["assembly", "gene_loci", "monarch"],
        "sequencing_qc": {"thresholds": {}, "samples": {}},
    }
    assert rss.snapshot_gaps(older) == [_HPO_PREDATES_GAP]
    # One whose pipeline declared an HPO version holds one, so nothing is missing.
    declared = {**older, "modules": [*older["modules"], {"key": "hpo", "label": "HPO", "version": "2025-01"}]}
    assert rss.snapshot_gaps(declared) == []
    # A record that names no looked-up modules has each one it lacks named, never assumed.
    unnamed = {key: value for key, value in older.items() if key != "reference_modules"}
    assert [gap["item"] for gap in rss.snapshot_gaps(unnamed)] == [
        rss.module_label(key) for key in ams.REFERENCE_MODULE_KEYS
    ]


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
    # So does the response, as a read of the new version would.
    assert out["not_captured"] == [_QC_GAP]


def test_a_complete_sign_out_records_no_gaps(monkeypatch) -> None:
    async def _complete_qc(session, context):
        return {"profile_key": "wgs", "profile_label": "WGS", "thresholds": {}, "samples": {}}

    _patch_common(monkeypatch, drifted_count=0)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _complete_qc)
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))

    assert captured["after"]["not_captured"] == []
    assert "not captured" not in captured["summary"]
    assert out["not_captured"] == []


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


def test_signout_check_does_not_compare_a_section_the_record_does_not_hold(monkeypatch) -> None:
    # A section CoGA adds after a record was signed is not in that record: it is not
    # comparable, and does not on its own make the page "changed".
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    for key in ("reported_structural_variants", "sequencing_qc"):
        signed.pop(key)
    row = _signed_row(signed)

    out = asyncio.run(rss.compare_report_with_latest_signout(_CheckSession(row), family_id="FAM1", user=_user()))
    assert out["matches"] is True
    assert out["not_compared"] == ["reported_structural_variants", "sequencing_qc"]

    async def _one_cnv(session, family_uuid):
        return [{"variant_id": "DEL-1-1000-2000", "tags": ["report"]}]

    monkeypatch.setattr(rss, "_reported_structural_reviews", _one_cnv)
    out = asyncio.run(rss.compare_report_with_latest_signout(_CheckSession(row), family_id="FAM1", user=_user()))
    assert out["matches"] is False
    # The CNV was reported without frozen evidence, so the SV/CNV drift the record holds changed.
    assert out["changed_sections"] == ["structural_drift"]


# ---------------------------------------------------------------------------
# Incomplete-import gate: a family whose package import partly failed
# ---------------------------------------------------------------------------

# As family_package_registration._flag_family_import_incomplete records it.
_IMPORT_JOB_ID = "3f6c1a2e-8b4d-4e5f-9a7b-1c2d3e4f5a6b"
_INCOMPLETE = {
    "at": "2026-09-12T10:14:00+00:00",
    "failed_datasets": ["snv", "sv"],
    "imported_datasets": ["coverage"],
    # The import job that holds each dataset's error.
    "job_id": _IMPORT_JOB_ID,
}
# A flag written before the job was recorded: the datasets only.
_OLD_INCOMPLETE = {key: value for key, value in _INCOMPLETE.items() if key != "job_id"}
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
    # It points to the job whose record holds each dataset's error.
    assert f"import job {_IMPORT_JOB_ID}" in detail["message"]
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
    assert out["import_incomplete_job_id"] == _IMPORT_JOB_ID
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


# As the reader freezes a flag that names one job: that job holds each dataset's error.
_INCOMPLETE_READ = {**_INCOMPLETE, "failed_jobs": {"snv": _IMPORT_JOB_ID, "sv": _IMPORT_JOB_ID}}


def test_the_import_flag_is_read_from_the_family_metadata() -> None:
    session = _ScalarSession(dict(_INCOMPLETE))
    assert asyncio.run(rss._import_incomplete_state(session, "u1")) == _INCOMPLETE_READ
    sql, params = session.executed[0]
    assert "import_incomplete" in sql and "families" in sql
    assert params == {"family_uuid": "u1"}  # bound, never interpolated


def test_the_import_flag_reader_is_deterministic_and_fails_safe() -> None:
    def read(value):
        return asyncio.run(rss._import_incomplete_state(_ScalarSession(value), "u1"))

    # No flag: a complete import.
    assert read(None) is None
    # A driver that hands jsonb back as text reads the same.
    assert read(json.dumps(_INCOMPLETE)) == _INCOMPLETE_READ
    # Dataset lists are frozen sorted and de-duplicated, so the hash is stable.
    messy = {"at": _INCOMPLETE["at"], "failed_datasets": ["sv", "snv", "sv"], "imported_datasets": []}
    assert read(messy)["failed_datasets"] == ["snv", "sv"]
    # Each failed dataset's job: the one the flag names for it, else the flag's own.
    kept = {**_INCOMPLETE, "failed_jobs": {"sv": "job-earlier", "snv": 7}}
    assert read(kept)["failed_jobs"] == {"snv": _IMPORT_JOB_ID, "sv": "job-earlier"}
    # Set, but not in the shape the import writes: still incomplete — it gates, with
    # nothing to name — rather than being taken for a complete import.
    unknown = {"at": None, "failed_datasets": [], "imported_datasets": [], "job_id": None, "failed_jobs": {}}
    assert read(True) == unknown
    assert read({}) == unknown
    assert read("not json") == unknown
    # A job id that is not a string names no job.
    assert read({**_INCOMPLETE, "job_id": 7})["job_id"] is None


def test_an_old_flag_without_an_import_job_still_gates(monkeypatch) -> None:
    # Flags written before the job was recorded carry only the datasets. They read and
    # gate the same, with no job to point to.
    state = asyncio.run(rss._import_incomplete_state(_ScalarSession(dict(_OLD_INCOMPLETE)), "u1"))
    assert state == {**_OLD_INCOMPLETE, "job_id": None, "failed_jobs": {"snv": None, "sv": None}}
    assert "import job" not in rss._import_gate_message(state)

    _patch_common(monkeypatch, drifted_count=0, import_incomplete=state)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["gate"] == "import_incomplete"

    _patch_common(monkeypatch, drifted_count=0, import_incomplete=state)
    out = asyncio.run(
        rss.sign_out_report(
            _Session(),
            family_id="FAM1",
            user=_user(),
            acknowledge_import_incomplete=True,
            import_incomplete_acknowledgement_reason=_IMPORT_ACK_REASON,
        )
    )
    assert out["snapshot"]["import_incomplete"]["job_id"] is None
    assert out["import_incomplete_job_id"] is None


# ---------------------------------------------------------------------------
# An import that stopped part-way: its process ended (a restart, a crash, out of memory),
# so none of its fail-clean ran and no import_incomplete flag was set. It left its entry in
# families.metadata.import_unfinished, which gates sign-out the same way.
# ---------------------------------------------------------------------------

# As family_package_registration.ImportMark records it: stopped inside haplotypes.
_STOPPED_JOB_ID = "7a1d2c3b-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
_UNFINISHED = {
    _STOPPED_JOB_ID: {
        "job_id": _STOPPED_JOB_ID,
        "at": "2026-10-02T09:12:00+00:00",
        "datasets": ["apcad", "haplotypes", "qdnaseq"],
        "finished_datasets": ["apcad", "qdnaseq"],
    }
}


def test_an_import_that_stopped_part_way_blocks_sign_out(monkeypatch) -> None:
    # Before: no flag was set, the job had ended, and the family signed out as complete.
    _patch_common(monkeypatch, drifted_count=0, import_unfinished=_UNFINISHED)
    captured = _capture_audit(monkeypatch)
    session = _Session()
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(session, family_id="FAM1", user=_user()))

    assert excinfo.value.status_code == 409
    detail = excinfo.value.detail
    assert detail["gate"] == "import_incomplete"
    assert detail["import_incomplete"] is None
    assert detail["import_unfinished"] == _UNFINISHED
    message = detail["message"]
    assert "stopped before it finished" in message
    assert "haplotypes may be partly written or missing (apcad, qdnaseq had finished)" in message
    assert f"Import job {_STOPPED_JOB_ID} records how far it got" in message
    # An update would skip the half-written dataset: the way out is an overwrite.
    assert "with overwrite" in message
    assert _inserts(session) == [] and captured == {}


def test_an_acknowledged_stopped_import_is_frozen_and_audited(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0, import_unfinished=_UNFINISHED)
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(
        rss.sign_out_report(
            _Session(),
            family_id="FAM1",
            user=_user(),
            acknowledge_import_incomplete=True,
            import_incomplete_acknowledgement_reason=_IMPORT_ACK_REASON,
        )
    )

    snapshot = out["snapshot"]
    assert snapshot["import_unfinished"] == _UNFINISHED
    assert snapshot["import_incomplete"] is None
    assert snapshot["acknowledged_import_incomplete"] is True
    assert snapshot["import_incomplete_acknowledgement_reason"] == _IMPORT_ACK_REASON
    assert out["content_hash"] == rss._canonical_hash(snapshot)
    assert out["import_incomplete_acknowledged"] is True
    assert captured["after"]["import_unfinished"] == _UNFINISHED
    assert captured["after"]["acknowledged_import_incomplete"] is True
    assert ", incomplete import acknowledged" in captured["summary"]


def test_a_complete_family_freezes_no_unfinished_import(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert out["snapshot"]["import_unfinished"] == {}
    assert out["snapshot"]["acknowledged_import_incomplete"] is False
    assert captured["after"]["import_unfinished"] == {}


def test_a_failed_and_a_stopped_import_are_one_gate_with_one_acknowledgement(monkeypatch) -> None:
    _patch_common(
        monkeypatch, drifted_count=0, import_incomplete=_INCOMPLETE, import_unfinished=_UNFINISHED
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    detail = excinfo.value.detail
    assert detail["gate"] == "import_incomplete"
    # The message names both: what failed, and what the stopped import had not finished.
    assert "failed for snv, sv" in detail["message"]
    assert "haplotypes may be partly written or missing" in detail["message"]
    assert f"import job {_IMPORT_JOB_ID}" in detail["message"]

    _patch_common(
        monkeypatch, drifted_count=0, import_incomplete=_INCOMPLETE, import_unfinished=_UNFINISHED
    )
    out = asyncio.run(
        rss.sign_out_report(
            _Session(),
            family_id="FAM1",
            user=_user(),
            acknowledge_import_incomplete=True,
            import_incomplete_acknowledgement_reason=_IMPORT_ACK_REASON,
        )
    )
    assert out["snapshot"]["import_incomplete"] == _INCOMPLETE
    assert out["snapshot"]["import_unfinished"] == _UNFINISHED


def test_the_unfinished_imports_are_bound_into_the_content_hash(monkeypatch) -> None:
    def _body_hash(unfinished) -> str:
        _patch_common(monkeypatch, drifted_count=0, import_unfinished=unfinished)
        body = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
        assert body["import_unfinished"] == (unfinished or {})
        return rss._canonical_hash(body)

    assert _body_hash(None) == _body_hash({})
    assert _body_hash(None) != _body_hash(_UNFINISHED)
    finished_all = {
        _STOPPED_JOB_ID: {**_UNFINISHED[_STOPPED_JOB_ID], "finished_datasets": ["apcad", "haplotypes", "qdnaseq"]}
    }
    assert _body_hash(_UNFINISHED) != _body_hash(finished_all)


def test_the_unfinished_import_reader_is_deterministic_and_fails_safe() -> None:
    def read(value):
        return asyncio.run(rss._import_unfinished_state(_ScalarSession(value), "u1"))

    session = _ScalarSession(dict(_UNFINISHED))
    assert asyncio.run(rss._import_unfinished_state(session, "u1")) == _UNFINISHED
    sql, params = session.executed[0]
    assert "import_unfinished" in sql and "families" in sql
    assert params == {"family_uuid": "u1"}  # bound, never interpolated

    # None recorded: nothing unfinished.
    assert read(None) == {}
    assert read(False) == {}
    # A driver that hands jsonb back as text reads the same.
    assert read(json.dumps(_UNFINISHED)) == _UNFINISHED
    # Lists are frozen sorted and de-duplicated, so the hash is stable.
    messy = {"k": {"job_id": "j", "at": "t", "datasets": ["sv", "snv", "sv"], "finished_datasets": ["sv"]}}
    assert read(messy)["k"]["datasets"] == ["snv", "sv"]
    # Set, but not in the shape an import writes: still unfinished, with nothing to name.
    unknown = {"job_id": None, "at": None, "datasets": [], "finished_datasets": []}
    assert read("not json") == {"unreadable": unknown}
    assert read(["a list"]) == {"unreadable": unknown}
    assert read({"k": "not an entry"}) == {"k": unknown}
    assert read({"k": {**messy["k"], "job_id": 7}})["k"]["job_id"] is None


def test_the_gate_message_names_each_failed_datasets_job() -> None:
    # A later failure keeps an earlier one's failed dataset: each error is in its own job.
    state = {**_INCOMPLETE, "failed_jobs": {"snv": _IMPORT_JOB_ID, "sv": "job-earlier"}}
    message = rss._import_gate_message(state)
    assert (
        f"Each dataset's error is recorded in its import job: snv in import job {_IMPORT_JOB_ID}; "
        "sv in import job job-earlier." in message
    )
    # One job for all: the sentence it always had.
    assert f"Each dataset's error is recorded in import job {_IMPORT_JOB_ID}." in rss._import_gate_message(
        _INCOMPLETE_READ
    )


def test_a_failure_no_import_job_records_is_never_named_under_another_failures_job() -> None:
    # SAFE-18. An upload of sv failed (SAFE-25: no job), then an import failed for snv: the
    # flag's job is the import's, and its failed_jobs lists sv with none. Before: sv was
    # frozen and named under the import's job, whose record holds no error of it.
    flag = {**_INCOMPLETE, "failed_jobs": {"snv": _IMPORT_JOB_ID, "sv": None}}
    state = asyncio.run(rss._import_incomplete_state(_ScalarSession(flag), "u1"))
    assert state["failed_jobs"] == {"snv": _IMPORT_JOB_ID, "sv": None}
    message = rss._import_gate_message(state)
    assert (
        "No import job records the error of sv, which failed in a single-file upload or in an "
        f"import run outside a job. Each other dataset's error is recorded in import job {_IMPORT_JOB_ID}."
    ) in message
    assert "Each dataset's error" not in message
    # With two jobs besides it, each is named.
    state["failed_datasets"] = ["coverage", "snv", "sv"]
    state["failed_jobs"] = {"coverage": "job-earlier", "snv": _IMPORT_JOB_ID, "sv": None}
    assert (
        "Each other dataset's error is recorded in its import job: coverage in import job "
        f"job-earlier; snv in import job {_IMPORT_JOB_ID}."
    ) in rss._import_gate_message(state)
    # A flag that names no job for any dataset (written before jobs were recorded) says nothing
    # of jobs, as before.
    old = asyncio.run(rss._import_incomplete_state(_ScalarSession({**_OLD_INCOMPLETE, "failed_jobs": {}}), "u1"))
    assert "import job" not in rss._import_gate_message(old)


def test_a_restore_that_did_not_finish_is_frozen_and_named_as_one() -> None:
    # SAFE-18. A failed overwrite whose restore did not finish leaves its entry listing the
    # whole family's datasets. Before: frozen like any entry, so the 409, its dialog and the
    # signed record called it an import that "stopped before it finished".
    restore = {
        "job_id": "job-r",
        "at": "2026-10-13T09:00:00+00:00",
        "datasets": ["coverage", "snv"],
        "finished_datasets": [],
        "whole_family": [
            {"dataset": "coverage", "scope": {"source": None, "samples": ["S1"]}},
            {"dataset": "snv", "scope": {"source": "clair3", "samples": None}},
        ],
    }
    upload_mark = {
        "job_id": None,
        "at": "2026-10-14T09:00:00+00:00",
        "datasets": ["sv_sniffles"],
        "finished_datasets": [],
        "whole_family": [{"dataset": "sv_sniffles", "scope": {"source": None, "samples": ["S1"]}}],
    }
    stored = {"job-r": restore, "upload-1": upload_mark, _STOPPED_JOB_ID: _UNFINISHED[_STOPPED_JOB_ID]}
    state = asyncio.run(rss._import_unfinished_state(_ScalarSession(stored), "u1"))

    assert state["job-r"] == {
        "job_id": "job-r",
        "at": "2026-10-13T09:00:00+00:00",
        "datasets": ["coverage", "snv"],
        "finished_datasets": [],
        "whole_family": True,
    }
    # An upload's mark lists its items the same way, but is no restore; a stopped import's
    # entry is frozen as before.
    assert "whole_family" not in state["upload-1"]
    assert state[_STOPPED_JOB_ID] == _UNFINISHED[_STOPPED_JOB_ID]

    message = rss._import_gate_message(None, {"job-r": state["job-r"]})
    assert message == (
        "This family's data is incomplete: an import (2026-10-13T09:00:00+00:00) failed, and "
        "putting this family's data back did not finish, so coverage, snv may be partly written "
        "or missing, whichever import or upload wrote them. Import job job-r records its "
        "failure. Import this family again in full, with overwrite: every dataset named here, "
        "not only those that failed, and upload again with overwrite what a single-file upload "
        "brought (an update keeps the data already there), or acknowledge with a reason to sign "
        "out anyway."
    )
    assert "stopped before it finished" not in message


def test_the_gate_message_of_a_stopped_import_that_recorded_nothing() -> None:
    message = rss._import_gate_message(None, {"unreadable": {"job_id": None, "at": None, "datasets": [], "finished_datasets": []}})
    assert message.startswith("This family's data is incomplete: an import stopped before it finished.")
    assert "import job" not in message.lower()
    # Only a failed import: the message it always had.
    failed_only = rss._import_gate_message(_INCOMPLETE)
    assert failed_only == rss._import_gate_message(_INCOMPLETE, {})
    assert "overwrite" not in failed_only


def test_signout_check_flags_an_import_that_stopped_after_signout(monkeypatch) -> None:
    # Signed complete; an import of the family has since stopped part-way. The page no
    # longer shows the data that was signed.
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    _patch_check(monkeypatch, reviews=[_REVIEW])
    monkeypatch.setattr(
        rss, "_import_unfinished_state", lambda _session, _uuid: asyncio.sleep(0, result=dict(_UNFINISHED))
    )
    out = asyncio.run(
        rss.compare_report_with_latest_signout(
            _CheckSession(_signed_row(signed)), family_id="FAM1", user=_user()
        )
    )
    assert out["matches"] is False
    assert out["changed_sections"] == ["import_unfinished"]


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
    "signed_out_by": "lab-signer",
    "software": {"git_sha": "0123abc", "version": "1.4.0"},
    "version": 1,
}
_PRE_GATE_CONTENT_HASH = "7ccf7e25e4773927e95ec18d35fcb52f565c97e16f3c8d49872a3f950557634b"
_PRE_GATE_ROW_HASH = "e19cb1bbe92d6dfed10002412d70424b36b50502a1696dd1b2063f6877d53c45"


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
        "signed_out_by": "lab-signer",
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
        "import_incomplete_job_id": None,
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
    assert detail["import_incomplete_job_id"] is None


# The report page renders a signed version from its record alone, whichever version it is,
# so reading one says what that record lacks, as the sign-out check does for the latest.


def _detail_of(monkeypatch, row: dict) -> dict:
    _patch_common(monkeypatch, drifted_count=0)
    return asyncio.run(
        rss.get_report_signout(_RowsSession([row]), family_id="FAM1", version=1, user=_user())
    )


def test_a_signed_version_names_what_its_record_could_not_capture(monkeypatch) -> None:
    snapshot = {
        **_PRE_GATE_SNAPSHOT,
        "modules": [
            {"key": "clinvar", "label": "ClinVar", "version": "2026-05"},
            {"key": "monarch", "label": "Monarch", "version": "unavailable", "detail": "lookup failed"},
        ],
        "reference_modules": list(ams.REFERENCE_MODULE_KEYS),
        "sequencing_qc": {"thresholds": {}, "samples": {}, "unavailable": "QC thresholds could not be resolved"},
    }
    row = {**_pre_gate_row(), "snapshot": snapshot, "content_hash": rss._canonical_hash(snapshot)}

    detail = _detail_of(monkeypatch, row)

    assert detail["verified"] is True
    gaps = [_QC_GAP, {"section": "modules", "item": "Monarch", "reason": "lookup failed"}]
    assert detail["not_captured"] == gaps
    # The endpoint's response model carries them.
    assert ReportSignoutDetail.model_validate(detail).model_dump()["not_captured"] == gaps


def test_a_version_that_names_no_looked_up_modules_names_each_one_it_lacks(monkeypatch) -> None:
    # The pinned record holds no list of the reference modules it looked up, and only a
    # pipeline module: every reference module is named as missing, none assumed looked up.
    gaps = _detail_of(monkeypatch, _pre_gate_row())["not_captured"]
    assert [gap["item"] for gap in gaps] == [rss.module_label(key) for key in ams.REFERENCE_MODULE_KEYS]
    assert {gap["reason"] for gap in gaps} == {"signed before CoGA recorded its version"}


def test_a_complete_signed_version_names_nothing_missing(monkeypatch) -> None:
    snapshot = {**_PRE_GATE_SNAPSHOT, "reference_modules": list(ams.REFERENCE_MODULE_KEYS)}
    row = {**_pre_gate_row(), "snapshot": snapshot, "content_hash": rss._canonical_hash(snapshot)}
    assert _detail_of(monkeypatch, row)["not_captured"] == []


def test_a_signed_version_read_back_as_text_is_served_as_the_record(monkeypatch) -> None:
    # A driver without the jsonb codec hands the snapshot back as JSON text. It is verified
    # and served as the object it is, which the page renders and the schema declares.
    row = {**_pre_gate_row(), "snapshot": json.dumps(_PRE_GATE_SNAPSHOT)}

    detail = _detail_of(monkeypatch, row)

    assert detail["verified"] is True
    assert detail["snapshot"] == _PRE_GATE_SNAPSHOT
    assert ReportSignoutDetail.model_validate(detail).snapshot == _PRE_GATE_SNAPSHOT


def test_serialize_signout_exposes_the_frozen_import_acknowledgement() -> None:
    row = {
        "version": 2,
        "signed_out_by": "x",
        "signed_out_at": None,
        "content_hash": "h",
        "import_incomplete_failed_datasets": ["snv", "sv"],
        "import_incomplete_job_id": _IMPORT_JOB_ID,
        "import_incomplete_acknowledged": True,
        "import_incomplete_acknowledgement_reason": _IMPORT_ACK_REASON,
    }
    serialized = rss._serialize_signout(row)
    assert serialized["import_incomplete_failed_datasets"] == ["snv", "sv"]
    assert serialized["import_incomplete_job_id"] == _IMPORT_JOB_ID
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
            "signed_out_by": "lab-signer",
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


# ---------------------------------------------------------------------------
# The HPO release in the signed record
# ---------------------------------------------------------------------------

_CLINVAR = {"clinvar": "2026-05"}
_ASSEMBLY = {"assembly": {"version": "GRCh38", "detail": "p14"}}
_HPO = {"hpo": {"version": "hp/releases/2026-06-06", "detail": "2026-06-06"}}


def _patch_manifest(monkeypatch, platform: dict) -> None:
    """The live manifest: the family's pipeline versions over the given reference layer."""

    async def _manifest(session, *, family_id, user, project_id=None):
        return {"assembly": "GRCh38", "modules": ams._module_list(_CLINVAR, platform)}

    monkeypatch.setattr(rss, "get_family_annotation_manifest", _manifest)


def _signed_before_hpo_was_recorded(monkeypatch) -> dict:
    """A snapshot signed before CoGA recorded the HPO release: no HPO module, and HPO not
    among the reference modules it looked up (a module CoGA added later)."""
    _patch_check(monkeypatch, reviews=[_REVIEW])
    _patch_manifest(monkeypatch, _ASSEMBLY)
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    signed["reference_modules"] = [key for key in signed["reference_modules"] if key != "hpo"]
    return signed


def test_the_snapshot_records_which_reference_modules_it_looked_up(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    snapshot = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    assert snapshot["reference_modules"] == ["assembly", "gene_loci", "monarch", "hpo"]
    # Bound into the content hash like the rest of the record.
    assert rss._canonical_hash(snapshot) != rss._canonical_hash({**snapshot, "reference_modules": []})


def test_a_record_signed_before_the_hpo_release_was_recorded_still_verifies(monkeypatch) -> None:
    # Verification re-hashes the snapshot as stored. It never rebuilds the snapshot or reads
    # the live manifest, so a record without an HPO module verifies as it did when signed.
    import json as _json
    from datetime import datetime, timezone

    signed = _signed_before_hpo_was_recorded(monkeypatch)
    assert "hpo" not in {m["key"] for m in signed["modules"]}
    stored = _json.loads(_json.dumps(signed, default=str))  # the JSONB round-trip
    row = {
        "version": 1,
        "id": "1",
        "signed_out_by": "someone",
        "signed_out_at": datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
        "content_hash": rss._canonical_hash(stored),
        "family_identifier": "FAM1",
        "snapshot": stored,
        "software_version": None,
        "git_sha": None,
        "qc_status": None,
        "qc_acknowledged": None,
        "qc_acknowledgement_reason": None,
        "drift_acknowledged": None,
        "drift_acknowledgement_reason": None,
        "prev_hash": None,
    }
    row["row_hash"] = rss.chain_row_hash(None, rss._signout_chain_payload(row))

    async def _must_not_rebuild(*args, **kwargs):
        raise AssertionError("verification must not rebuild the snapshot from live data")

    monkeypatch.setattr(rss, "get_family_annotation_manifest", _must_not_rebuild)
    monkeypatch.setattr(rss, "build_report_snapshot", _must_not_rebuild)

    class _Rows:
        def mappings(self):
            return self

        def first(self):
            return row

        def all(self):
            return [row]

    class _RowSession:
        async def execute(self, *args, **kwargs):
            return _Rows()

    record = asyncio.run(rss.get_report_signout(_RowSession(), family_id="FAM1", version=1, user=_user()))
    assert record["verified"] is True
    chain = asyncio.run(rss.verify_report_signout_chain(_RowSession(), "FAM1"))
    assert chain.verified is True, chain


def test_signout_check_does_not_call_a_record_signed_before_a_module_was_added_changed(monkeypatch) -> None:
    # The live manifest now carries the HPO release, which a record signed before CoGA
    # recorded it does not hold. Comparing the module lists as they are would report every
    # such record as changed ("annotation and pipeline versions") though nothing it froze
    # changed. Like a snapshot section a record predates (#508), the module is not
    # compared, and the record is said not to hold it.
    signed = _signed_before_hpo_was_recorded(monkeypatch)
    _patch_manifest(monkeypatch, {**_ASSEMBLY, **_HPO})
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is True, out
    assert out["changed_sections"] == []
    assert out["not_compared"] == ["modules.hpo"]
    assert out["not_captured"] == [_HPO_PREDATES_GAP]

    # Any other module still compares: a changed assembly detail is a change.
    _patch_manifest(monkeypatch, {"assembly": {"version": "GRCh38", "detail": "p15"}, **_HPO})
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is False
    assert out["changed_sections"] == ["modules"]


def test_signout_check_flags_an_hpo_version_a_family_pipeline_declared_after_sign_out(monkeypatch) -> None:
    # Only the reference layer's HPO postdates the older record. An HPO version the family's
    # own pipeline now declares came from a re-import after sign-out: a change.
    signed = _signed_before_hpo_was_recorded(monkeypatch)

    async def _manifest(session, *, family_id, user, project_id=None):
        return {"assembly": "GRCh38", "modules": ams._module_list({**_CLINVAR, "hpo": "2025-01"}, _ASSEMBLY)}

    monkeypatch.setattr(rss, "get_family_annotation_manifest", _manifest)
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is False
    assert out["changed_sections"] == ["modules"]
    assert out["not_compared"] == []


def test_signout_check_flags_an_hpo_ontology_loaded_after_sign_out(monkeypatch) -> None:
    # A record signed with this change looked HPO up: without an HPO module, none was
    # loaded then. An ontology loaded since is a change, not an older record.
    _patch_check(monkeypatch, reviews=[_REVIEW])
    _patch_manifest(monkeypatch, _ASSEMBLY)
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    _patch_manifest(monkeypatch, {**_ASSEMBLY, **_HPO})
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is False
    assert out["changed_sections"] == ["modules"]
    assert out["not_compared"] == []
    assert out["not_captured"] == []


def test_signout_check_flags_a_new_hpo_release(monkeypatch) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    _patch_manifest(monkeypatch, {**_ASSEMBLY, **_HPO})
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    assert {"key": "hpo", "version": "hp/releases/2026-06-06"}.items() <= next(
        m for m in signed["modules"] if m["key"] == "hpo"
    ).items()
    # Unchanged, it matches.
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is True and out["not_compared"] == []

    _patch_manifest(monkeypatch, {**_ASSEMBLY, "hpo": {"version": "hp/releases/2026-09-01", "detail": "2026-09-01"}})
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is False
    assert out["changed_sections"] == ["modules"]


# ---------------------------------------------------------------------------
# The evidence of the reported structural variants and CNVs (TF-09a REQ-TRACE-014)
# ---------------------------------------------------------------------------

_SV_ID = "DEL-18-55000000-55400000"
_SV_CNV_ACMG = {
    "kind": "loss",
    "criteria": [{"code": "2A", "points": 1.0, "accepted": True}],
    "point_total": 1.0,
    "classification": "Pathogenic - class 5",
}
_SV_EVIDENCE = {
    "evidence": {
        "source": "needlr",
        "sv_type": "DEL",
        "chrom": "18",
        "start": 55000000,
        "end": 55400000,
        "sv_len": -400000,
        "remote_chrom": None,
        "remote_start": None,
        "gene_symbols": ["TCF4", "TXNL1"],
        "gene_count": 2,
        "pli": 0.99,
        "inheritance": "de_novo",
        "annotation_hash": "a" * 64,
    },
    "evidence_hash": "e" * 64,
    "versions": {"assembly": "GRCh38", "gencode": "45"},
    "captured_at": "2026-09-30T09:00:00+00:00",
}


def _reported_sv(evidence_snapshot=_SV_EVIDENCE, **overrides) -> dict:
    return {
        "variant_id": _SV_ID,
        "variant_key": 7,
        "classification": "Pathogenic - class 5",
        "cnv_class": "cnv_class_5",
        "cnv_point_total": 1.0,
        "cnv_acmg": _SV_CNV_ACMG,
        "tags": ["report"],
        "note": None,
        "evidence_snapshot": evidence_snapshot,
        **overrides,
    }


def _sv_drift_entry(**overrides) -> dict:
    return {
        "variant_id": _SV_ID,
        "classification": "Pathogenic - class 5",
        "cnv_class": "cnv_class_5",
        "classified_by": "alice",
        "classified_at": "2026-09-30T09:00:00+00:00",
        "status": "drifted",
        "changed": ["gene_symbols"],
        "evidence_from": _SV_EVIDENCE["evidence"],
        "evidence_to": {**_SV_EVIDENCE["evidence"], "gene_symbols": ["TCF4"], "gene_count": 1},
        **overrides,
    }


def _patch_sv(monkeypatch, *, reported: list, structural_drifted: list, small_drifted: int = 0) -> None:
    """A family whose reported SVs and SV/CNV drift are given; nothing else drifted."""
    _patch_common(monkeypatch, drifted_count=small_drifted)

    async def _drift(session, *, family_id, user, project_id=None):
        return {
            "checked": small_drifted,
            "drifted_count": small_drifted,
            "drifted": [{"variant_id": "x"}] * small_drifted,
            "structural": {
                "checked": len(reported),
                "drifted_count": len(structural_drifted),
                "drifted": list(structural_drifted),
            },
        }

    async def _sv_reviews(session, family_uuid):
        return [dict(item) for item in reported]

    monkeypatch.setattr(rss, "evaluate_classification_drift", _drift)
    monkeypatch.setattr(rss, "_reported_structural_reviews", _sv_reviews)


def test_a_reported_cnv_whose_evidence_changed_blocks_sign_out(monkeypatch) -> None:
    _patch_sv(monkeypatch, reported=[_reported_sv()], structural_drifted=[_sv_drift_entry()])
    session = _Session()
    with pytest.raises(HTTPException) as refused:
        asyncio.run(rss.sign_out_report(session, family_id="FAM1", user=_user()))
    assert refused.value.status_code == 409
    assert "1 classification(s) (1 of them structural-variant or CNV classifications)" in refused.value.detail
    assert _inserts(session) == [], "nothing may be written for a refused sign-out"

    # Acknowledged like any drift: with a reason.
    with pytest.raises(HTTPException) as no_reason:
        asyncio.run(
            rss.sign_out_report(
                _Session(), family_id="FAM1", user=_user(), acknowledge_drift=True, drift_acknowledgement_reason=" "
            )
        )
    assert no_reason.value.status_code == 422


def test_an_acknowledged_sv_drift_is_frozen_and_audited(monkeypatch) -> None:
    _patch_sv(monkeypatch, reported=[_reported_sv()], structural_drifted=[_sv_drift_entry()])
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(
        rss.sign_out_report(
            _Session(),
            family_id="FAM1",
            user=_user(),
            acknowledge_drift=True,
            drift_acknowledgement_reason="TXNL1 dropped from the gene list; class 5 rests on TCF4.",
        )
    )
    snapshot = out["snapshot"]
    assert snapshot["structural_drift"] == {"checked": 1, "drifted_count": 1, "drifted": [_sv_drift_entry()]}
    assert snapshot["acknowledged_drift"] is True
    assert snapshot["drift_acknowledgement_reason"].startswith("TXNL1 dropped")
    # The small-variant drift section is unchanged in shape.
    assert snapshot["drift"] == {"checked": 0, "drifted_count": 0, "drifted": []}
    assert captured["after"]["drifted_count"] == 1
    assert captured["after"]["structural_drifted_count"] == 1
    assert "drift acknowledged" in captured["summary"]


def test_a_reported_sv_without_frozen_evidence_blocks_sign_out(monkeypatch) -> None:
    # A reported SV whose classification froze no evidence (no CNV scoring saved, or one
    # saved before CoGA froze it) cannot be shown unchanged, like an unsnapshotted small
    # variant (#332).
    _patch_sv(monkeypatch, reported=[_reported_sv(evidence_snapshot=None)], structural_drifted=[])
    with pytest.raises(HTTPException) as refused:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert refused.value.status_code == 409
    assert "cannot be verified" in refused.value.detail

    _patch_sv(monkeypatch, reported=[_reported_sv(evidence_snapshot=None)], structural_drifted=[])
    out = asyncio.run(
        rss.sign_out_report(
            _Session(), family_id="FAM1", user=_user(), acknowledge_drift=True, drift_acknowledgement_reason="Re-read."
        )
    )
    assert out["snapshot"]["structural_drift"]["drifted"] == [
        {"variant_id": _SV_ID, "classification": "Pathogenic - class 5", "cnv_class": "cnv_class_5", "status": "no_snapshot"}
    ]


def test_small_variant_and_sv_drift_are_one_gate_and_one_acknowledgement(monkeypatch) -> None:
    _patch_sv(monkeypatch, reported=[_reported_sv()], structural_drifted=[_sv_drift_entry()], small_drifted=2)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert "3 classification(s) (1 of them structural-variant or CNV classifications)" in refused.value.detail

    _patch_sv(monkeypatch, reported=[_reported_sv()], structural_drifted=[_sv_drift_entry()], small_drifted=2)
    out = asyncio.run(
        rss.sign_out_report(
            _Session(), family_id="FAM1", user=_user(), acknowledge_drift=True, drift_acknowledgement_reason="Reviewed."
        )
    )
    assert out["drift_acknowledged"] is True
    assert (out["snapshot"]["drift"]["drifted_count"], out["snapshot"]["structural_drift"]["drifted_count"]) == (2, 1)


def test_unchanged_sv_evidence_needs_no_acknowledgement(monkeypatch) -> None:
    _patch_sv(monkeypatch, reported=[_reported_sv()], structural_drifted=[])
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert out["snapshot"]["acknowledged_drift"] is False
    assert out["snapshot"]["structural_drift"] == {"checked": 1, "drifted_count": 0, "drifted": []}


def test_the_reported_sv_evidence_is_frozen_into_the_content_hash(monkeypatch) -> None:
    def _body(evidence):
        _patch_sv(monkeypatch, reported=[_reported_sv(evidence_snapshot=evidence)], structural_drifted=[])
        return asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))

    frozen = _body(_SV_EVIDENCE)
    assert frozen["reported_structural_variants"][0]["evidence_snapshot"] == _SV_EVIDENCE
    moved = _body({**_SV_EVIDENCE, "evidence_hash": "f" * 64})
    assert rss._canonical_hash(frozen) != rss._canonical_hash(moved)


# A sign-out exactly as the writer stored it before SV/CNV classifications froze their
# evidence, captured from that writer (origin/main at 0633db8) for a family with one
# reported CNV: no `structural_drift` section and no evidence in the reported SV. The
# stored hashes are the ones it computed; the record is never re-hashed, so it must keep
# verifying as stored.
_PRE_SV_EVIDENCE_SIGNED_AT = "2026-09-29T23:02:28.105813+00:00"
_PRE_SV_EVIDENCE_SNAPSHOT = {
    "acknowledged_drift": False,
    "acknowledged_import_incomplete": False,
    "acknowledged_qc": False,
    "assembly": "GRCh38",
    "drift": {"checked": 0, "drifted": [], "drifted_count": 0},
    "drift_acknowledgement_reason": None,
    "family_id": "FAM1",
    "generated_at": _PRE_SV_EVIDENCE_SIGNED_AT,
    "import_incomplete": None,
    "import_incomplete_acknowledgement_reason": None,
    "modules": [{"key": "clinvar", "version": "2026-05"}],
    "qc_acknowledgement_reason": None,
    "reference_modules": ["assembly", "gene_loci", "monarch", "hpo"],
    "reported_structural_variants": [
        {
            "classification": "Pathogenic - class 5",
            "cnv_acmg": {
                "classification": "Pathogenic - class 5",
                "criteria": [{"accepted": True, "code": "2A", "points": 1.0}],
                "kind": "loss",
                "point_total": 1.0,
            },
            "cnv_class": "cnv_class_5",
            "cnv_point_total": 1.0,
            "note": None,
            "tags": ["report"],
            "variant_id": _SV_ID,
            "variant_key": 7,
        }
    ],
    "reported_variants": [],
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
    "signed_out_by": "lab-signer",
    "software": {"git_sha": "0633db8", "version": "1.5.0"},
    "version": 1,
}
_PRE_SV_EVIDENCE_CONTENT_HASH = "fa6dc209662121586420b9033d34cfe02595d4b58767cf3abeb5ca6dbec04c8a"
_PRE_SV_EVIDENCE_ROW_HASH = "10e45f07187d1b45ea15190d2dbf775ef708e6a95604855da48b6a51a3971c50"


def _pre_sv_evidence_row() -> dict:
    return {
        **_pre_gate_row(),
        "signed_out_at": datetime.fromisoformat(_PRE_SV_EVIDENCE_SIGNED_AT),
        "content_hash": _PRE_SV_EVIDENCE_CONTENT_HASH,
        "snapshot": json.loads(json.dumps(_PRE_SV_EVIDENCE_SNAPSHOT)),
        "row_hash": _PRE_SV_EVIDENCE_ROW_HASH,
        "import_incomplete_acknowledged": False,
    }


def test_a_sign_out_made_before_sv_evidence_was_frozen_still_verifies(monkeypatch) -> None:
    assert "structural_drift" not in _PRE_SV_EVIDENCE_SNAPSHOT
    assert "evidence_snapshot" not in _PRE_SV_EVIDENCE_SNAPSHOT["reported_structural_variants"][0]
    # The verifier hashes the record as stored: nothing adds the new section, or a default
    # for it, before hashing.
    assert rss._canonical_hash(_pre_sv_evidence_row()["snapshot"]) == _PRE_SV_EVIDENCE_CONTENT_HASH

    chain = asyncio.run(rss.verify_report_signout_chain(_RowsSession([_pre_sv_evidence_row()]), "FAM1"))
    assert chain.verified and chain.rows_checked == 1, chain

    _patch_common(monkeypatch, drifted_count=0)
    detail = asyncio.run(
        rss.get_report_signout(_RowsSession([_pre_sv_evidence_row()]), family_id="FAM1", version=1, user=_user())
    )
    assert detail["verified"] is True


def _as_now(monkeypatch, *, reported: list, structural_drifted: list) -> None:
    """The family as it would be signed now, over the same content as the older record."""
    _patch_sv(monkeypatch, reported=reported, structural_drifted=structural_drifted)

    async def _no_small_reviews(session, family_uuid):
        return []

    async def _sequencing_qc(session, context):
        return {"profile_key": "wgs", "profile_label": "WGS", "thresholds": {}, "samples": {}}

    monkeypatch.setattr(rss, "_reported_reviews", _no_small_reviews)
    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _sequencing_qc)


def test_signout_check_compares_the_sv_evidence_a_record_does_not_hold(monkeypatch) -> None:
    # The same reported CNV, now with frozen evidence that has not drifted. The pinned
    # record froze no evidence in its reported SV and holds no SV/CNV drift section: the
    # section is not compared, but the reported SV is, whole, so the record reads as
    # changed rather than as matching (records from before the release candidate get no
    # special reading, #681).
    row = _pre_sv_evidence_row()
    _as_now(monkeypatch, reported=[_reported_sv()], structural_drifted=[])
    out = asyncio.run(rss.compare_report_with_latest_signout(_CheckSession(row), family_id="FAM1", user=_user()))
    assert out["matches"] is False, out
    assert out["changed_sections"] == ["reported_structural_variants"]
    # Nor does it hold the unfinished imports, a section added after it too.
    assert out["not_compared"] == [
        "structural_drift",
        "import_unfinished",
        # Nor what the analysis looked at, which records hold since 2026-10-14.
        "variant_descriptions",
        "gene_panels",
        "reference_data",
        "repeat_expansions",
        "paraphase",
        # Nor what each coverage track holds, which records hold since 2026-10-20.
        "coverage_tracks",
    ]
    assert out["not_captured"] == []


def test_signout_check_flags_sv_evidence_that_drifted_after_sign_out(monkeypatch) -> None:
    _as_now(monkeypatch, reported=[_reported_sv()], structural_drifted=[])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    out = asyncio.run(rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user()))
    assert (out["matches"], out["not_compared"], out["not_captured"]) == (True, [], [])

    # A re-import changed the genes the CNV overlaps.
    _as_now(monkeypatch, reported=[_reported_sv()], structural_drifted=[_sv_drift_entry()])
    out = asyncio.run(rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user()))
    assert out["matches"] is False
    assert out["changed_sections"] == ["structural_drift"]


# ---------------------------------------------------------------------------
# The signed analysis record (owner decisions of 2026-10-14)
# ---------------------------------------------------------------------------
#
# A sign-out is the analytical sign-off: it freezes what the analysis looked at (the reported
# variants' descriptions, the gene panels its searches applied, the reference-data releases,
# the repeat expansions and the Paraphase results), says what it is, and carries a report
# code the EPD report quotes.

_PANEL = {
    "panel_id": "33333333-3333-3333-3333-333333333333",
    "name": "Epilepsy",
    "version": 2,
    "source": "panelapp",
    "gene_count": 120,
    "external_id": "402",
    "external_version": "3.1",
    "external_version_created": "2026-08-01",
    "confidence_levels": ["3"],
    "source_updated_at": "2026-09-01T08:00:00+00:00",
    "first_applied_at": "2026-10-10T09:00:00+00:00",
    "first_applied_by": "analyst",
    "first_search": "small_variants",
}


def _with_sections(monkeypatch, **overrides) -> None:
    async def _sections(session, *, context, reported, reported_structural, shared_nipt=None):
        return {**copy.deepcopy(_RECORD_SECTIONS), **copy.deepcopy(overrides)}

    monkeypatch.setattr(rss, "build_record_sections", _sections)


def test_the_snapshot_freezes_what_the_analysis_looked_at_and_what_the_record_is(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    _with_sections(monkeypatch, gene_panels={"applied": [_PANEL]})
    snapshot = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))

    for section in RECORD_SECTIONS:
        # Each is report content: the sign-out check compares it.
        assert section.key in rss.REPORT_CONTENT_SECTIONS
        # The family here is no PGT family (it has no embryo) and no NIPT family, so it
        # holds every section but those of one application.
        assert (section.key in snapshot) is (section.application is None), section.key
    assert snapshot["gene_panels"] == {"applied": [_PANEL]}
    assert snapshot["record_statement"] == (
        "This record is the analytical sign-off in CoGA, which closes and freezes the analysis; "
        "the medical sign-off of the patient report happens in the EPD, which refers to this "
        "record by its report code."
    )
    # What the record is, not what it reports.
    assert "record_statement" not in rss.REPORT_CONTENT_SECTIONS


@pytest.mark.parametrize(
    "change",
    [
        {"gene_panels": {"applied": [{**_PANEL, "version": 3}]}},
        {
            "variant_descriptions": {
                "small": [{"variant_id": "1-1-A-G", "genotypes": [{"sample": "P", "gt": "1/1"}]}],
                "structural": [],
            }
        },
        {"repeat_expansions": {"catalogue": {"loci": 1, "sources": [], "digest": "1" * 64}, "loci": []}},
        {"paraphase": {"catalogue": {"regions": 1, "digest": "1" * 64}, "genes": []}},
        {
            "reference_data": {
                "gene_reference": {"genes": [], "sources": [], "not_in_reference": []},
                "clinical_cnvs": {"loaded_at": "2026-10-13"},
            }
        },
    ],
)
def test_each_new_section_is_bound_into_the_content_hash_and_compared(monkeypatch, change) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    _with_sections(monkeypatch, **change)
    current = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    assert rss._canonical_hash(current) != rss._canonical_hash(signed)

    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is False
    assert out["changed_sections"] == list(change)


# --- A NIPT family's record: its NIPT results (signed_record_nipt) ---------------------

_MALE_DNA = PLASMA_PROFILE_VERDICTS["male_like_not_maternal_plasma"][1]
_DISCORDANT = (
    "The father's X alleles read female and the plasma's chrY coverage reads male: the two "
    "signals disagree; check the samples."
)
_NO_FF = "Not estimated, from 3 category-7 sites (an estimate needs at least 5)."


def _nipt_section(*, ff: str = "pass", fetal_sex: str = "pass", plasma: str = "pass") -> dict:
    """A NIPT family's ``nipt`` section, each check with the verdict given."""
    return {
        "samples": {"father": "FATHER1", "maternal_plasma": "CFDNA1", "fetus": None},
        "fetal_fraction": {
            "ff": 0.12 if ff != "fail" else 0.0,
            "ff_computed": 0.12 if ff != "fail" else None,
            "n_sites": 40 if ff != "fail" else 3,
            "status": ff,
            "message": _NO_FF if ff == "fail" else "12.0% (95% CI 10.9%–13.1%) from 40 category-7 sites.",
        },
        "checks": {
            "paternity": {"status": "pass", "message": "Paternity supported (61 of 122 (50%) of his het alleles seen (half expected))."},
            "fetal_sex": {
                "call": {"pass": "female", "warn": "indeterminate", "fail": "discordant"}[fetal_sex],
                "status": fetal_sex,
                "message": _DISCORDANT if fetal_sex == "fail" else "Female, read from the father's X alleles.",
            },
            "plasma_sample": {
                "status": plasma,
                "message": _MALE_DNA if plasma == "fail" else "Female plasma with a male fetal chrY signal.",
                "profile": "male_like_not_maternal_plasma" if plasma == "fail" else "female_with_male_fetal_signal",
            },
        },
        "variants": [],
        "not_classified": [],
        "recessive_genes": [],
    }


_NIPT_REFUSED = [
    "NIPT fetal sex: the father's X alleles read female and the plasma's chrY coverage reads male: "
    "the two signals disagree; check the samples.",
    "Sample CFDNA1 (maternal plasma): male DNA (chrY at the autosomal level, chrX low): this sample "
    "cannot be maternal plasma.",
]


def test_a_failed_nipt_quality_check_gates_the_sign_out_like_a_failed_sample_qc(monkeypatch) -> None:
    # The Sample QC passes, but the NIPT page fails male DNA given as the maternal plasma and
    # fetal-sex signals that disagree (REQ-NIPT-015, TF-06 H4): a NIPT family is signed out
    # with its NIPT results, so those gate it too, under the Sample QC's acknowledgement.
    _patch_common(monkeypatch, drifted_count=0)
    _with_sections(monkeypatch, nipt=_nipt_section(fetal_sex="fail", plasma="fail"))
    session = _Session()
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(session, family_id="FAM1", user=_user()))
    assert excinfo.value.status_code == 409
    detail = excinfo.value.detail
    assert detail["gate"] == "sample_qc"
    assert detail["nipt_failed_checks"] == _NIPT_REFUSED
    assert detail["qc_summary"]["messages"] == _NIPT_REFUSED
    assert detail["unverifiable_checks"] == []
    assert detail["failed_checks"] == [] and detail["warning_checks"] == []
    assert detail["message"] == f"{_NIPT_CHECK_FAILED} {_RESOLVE_OR_ACKNOWLEDGE}"
    assert not _inserts(session)


def test_a_fetal_fraction_that_could_not_be_estimated_gates_the_sign_out(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    _with_sections(monkeypatch, nipt=_nipt_section(ff="fail"))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert excinfo.value.detail["nipt_failed_checks"] == [
        "NIPT fetal fraction: not estimated, from 3 category-7 sites (an estimate needs at least 5)."
    ]


def test_a_failed_nipt_check_is_named_beside_a_failed_sample_qc(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0, qc_status="fail")
    _with_sections(monkeypatch, nipt=_nipt_section(plasma="fail"))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    detail = excinfo.value.detail
    assert detail["message"] == f"{_QC_FAILED} {_NIPT_CHECK_FAILED} {_RESOLVE_OR_ACKNOWLEDGE}"
    assert detail["nipt_failed_checks"] == [_NIPT_REFUSED[1]]


def test_a_nipt_refusal_lists_each_group_of_concerns_beside_the_failures(monkeypatch) -> None:
    # A NIPT family whose mother's sex check failed and whose maternal plasma is male DNA,
    # while its paternity and maternal lineage could not run (too few informative sites):
    # the refusal said only that the QC and a NIPT check failed, and its dialog never showed
    # the maternal lineage. The message names each kind of concern, and each concern is in
    # its own group, once (CLIN-58).
    report = SampleIntegrityReport(
        overall_status="fail",
        application="nipt",
        sex_checks=[
            SexCheck(
                "MOTHER1", "female", "male", 0.01, 500, "fail",
                "Recorded female but genotypes indicate male (1.0% chrX het over 500 sites) — "
                "possible sample swap or mislabel.",
            ),
        ],
        paternity_check=evaluate_paternity(
            "FATHER1",
            hom_alt_transmitted=3,
            hom_alt_not_transmitted=0,
            het_transmitted=0,
            het_not_transmitted=0,
        ),
        fetal_sex_check=evaluate_fetal_sex("indeterminate", 0, 0, 0),
        category_qc_check=evaluate_nipt_category_qc({2: 3, 3: 1}),
    )
    _with_sections(monkeypatch, nipt=_nipt_section(plasma="fail"))
    detail = _qc_refusal(monkeypatch, report)

    assert detail["message"] == (
        f"{_QC_FAILED} {_COULD_NOT_RUN} {_NIPT_CHECK_FAILED} {_RESOLVE_OR_ACKNOWLEDGE}"
    )
    assert detail["failed_checks"] == [
        "Sample MOTHER1: recorded female but genotypes indicate male (1.0% chrX het over 500 "
        "sites) — possible sample swap or mislabel."
    ]
    assert detail["unverifiable_checks"] == [
        "Sample FATHER1 (father): too few paternal-informative sites (3 homozygous, 0 het) to "
        "assess paternity.",
        "NIPT maternal lineage could not be verified (too few maternal-informative sites) — "
        "possible wrong mother.",
    ]
    assert detail["nipt_failed_checks"] == [_NIPT_REFUSED[1]]
    assert detail["warning_checks"] == [
        "Fetal sex indeterminate — too few informative paternal-X sites (0 paternal-X "
        "transmitted, 0 absent of 0 sites).",
    ]

    # The acknowledged sign-out's audit event records the same groups.
    captured = _capture_audit(monkeypatch)
    asyncio.run(
        rss.sign_out_report(
            _Session(), family_id="FAM1", user=_user(), acknowledge_qc=True,
            qc_acknowledgement_reason="Samples re-drawn and re-run; identities confirmed.",
        )
    )
    after = captured["after"]
    assert after["qc_failed"] == detail["failed_checks"]
    assert after["qc_unverifiable"] == detail["unverifiable_checks"]
    assert after["nipt_failed_checks"] == detail["nipt_failed_checks"]
    assert after["qc_warnings"] == detail["warning_checks"]


def test_an_acknowledged_nipt_check_is_frozen_and_audited(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    _with_sections(monkeypatch, nipt=_nipt_section(fetal_sex="fail", plasma="fail"))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            rss.sign_out_report(
                _Session(), family_id="FAM1", user=_user(), acknowledge_qc=True, qc_acknowledgement_reason=" "
            )
        )
    assert excinfo.value.status_code == 422
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(
        rss.sign_out_report(
            _Session(),
            family_id="FAM1",
            user=_user(),
            acknowledge_qc=True,
            qc_acknowledgement_reason="Plasma tube re-drawn and re-run; the samples are confirmed.",
        )
    )
    snapshot = out["snapshot"]
    assert snapshot["acknowledged_qc"] is True
    assert snapshot["qc_acknowledgement_reason"] == "Plasma tube re-drawn and re-run; the samples are confirmed."
    # The failed checks themselves are in the record's NIPT section, as they were.
    assert snapshot["nipt"]["checks"]["plasma_sample"]["status"] == "fail"
    assert captured["after"]["nipt_failed_checks"] == _NIPT_REFUSED
    assert ", QC override acknowledged" in captured["summary"]


@pytest.mark.parametrize(
    "section",
    [
        _nipt_section(),
        # A low-confidence estimate, no fetal sex, no sex profile: warnings, as on the page.
        {
            **_nipt_section(ff="warn", fetal_sex="warn"),
            "checks": {
                **_nipt_section(fetal_sex="warn")["checks"],
                "plasma_sample": {"status": "unknown", "message": "No per-target coverage: no sex profile."},
            },
        },
        # Results that could not be read: not captured (the record names them), not a QC fail.
        {"unavailable": "The NIPT results could not be read."},
        # The trio could not be resolved: the Sample QC's own NIPT check gates that.
        {"unavailable": "The NIPT trio could not be resolved."},
    ],
)
def test_nipt_checks_that_did_not_fail_do_not_gate(monkeypatch, section) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    _with_sections(monkeypatch, nipt=section)
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert out["snapshot"]["acknowledged_qc"] is False
    assert out["snapshot"]["nipt"] == section
    assert captured["after"]["nipt_failed_checks"] == []


def test_a_family_that_is_no_nipt_family_is_gated_and_audited_as_before(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    assert "nipt" not in out["snapshot"]
    assert captured["after"]["nipt_failed_checks"] == []


def test_a_nipt_familys_results_are_bound_into_the_content_hash_and_compared(monkeypatch) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    _with_sections(monkeypatch, nipt=_nipt_section())
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert (out["matches"], out["changed_sections"], out["not_compared"]) == (True, [], [])

    # A re-import moved the fetal fraction.
    moved = _nipt_section()
    moved["fetal_fraction"] = {**moved["fetal_fraction"], "ff": 0.13, "ff_computed": 0.13}
    _with_sections(monkeypatch, nipt=moved)
    current = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    assert rss._canonical_hash(current) != rss._canonical_hash(signed)
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is False
    assert out["changed_sections"] == ["nipt"]


def test_a_nipt_record_signed_before_the_section_names_it_not_compared(monkeypatch) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    assert "nipt" not in signed
    # The family is a NIPT family: the current data holds the section the record predates.
    _with_sections(monkeypatch, nipt=_nipt_section())
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is True
    assert out["not_compared"] == ["nipt"]
    assert out["not_captured"] == []


def test_the_check_of_a_family_that_is_no_nipt_family_names_no_nipt_section(monkeypatch) -> None:
    # Neither the record nor the family's data holds it: not compared, and not named.
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert "nipt" in rss.REPORT_CONTENT_SECTIONS
    assert (out["matches"], out["not_compared"], out["changed_sections"]) == (True, [], [])


def test_nipt_results_not_captured_are_named_by_the_check_and_the_audit(monkeypatch) -> None:
    unavailable = {"unavailable": "The NIPT results could not be read."}
    _patch_common(monkeypatch, drifted_count=0)
    _with_sections(monkeypatch, nipt=unavailable)
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))
    gap = {"section": "nipt", "item": "NIPT results", "reason": "The NIPT results could not be read."}
    assert gap in out["not_captured"]
    assert gap in captured["after"]["not_captured"]


# A PGT family's embryo calls as signed_record_sections freezes them (pgt_analysis.py): only a
# family with an embryo holds the section.
_PGT_ANALYSIS = {
    "region_of_interest": {"query": "GENE1", "label": "GENE1", "source": "gene", "chrom": "7", "start": 10, "end": 20},
    "window": {"chrom": "7", "start": 0, "end": 1_000_020},
    "inheritance_model": {"recorded": "AD", "applied": "AD", "mode": "dominant"},
    "members": [],
    "disease_haplotype_resolved": True,
    "disease_haplotypes": [{"origin": "paternal", "homolog": "1", "kind": "dominant"}],
    "phase_corrections": [],
    "embryos": [
        {
            "sample": "E1",
            "sex": "female",
            "call": "affected_or_at_risk",
            "roi_not_covered": False,
            "sex_dependent": None,
            "inherited": [{"origin": "paternal", "homolog": "1", "start": 0, "end": 30}],
            "recombinations": [],
            "qc": {"informative_sites": 40, "mendel_errors": 0, "mendel_rate": 0.0, "paternal_markers": 3, "maternal_markers": 2},
        }
    ],
    "inputs": {"haplotype_sources": [], "blocks_read": 4, "blocks_digest": "d" * 64, "markers_truncated": False},
    "rules": {"window_flank": 1_000_000, "recombination_flank": 250_000, "phase_switch_uncertainty": 2_000_000},
}


def _no_sequencing_qc(monkeypatch) -> None:
    async def _sequencing_qc(session, context):
        return {"profile_key": None, "profile_label": None, "thresholds": {}, "samples": {}}

    monkeypatch.setattr(rss, "_canonical_sequencing_qc", _sequencing_qc)


def test_a_pgt_familys_sign_out_freezes_its_embryo_calls_into_the_hashed_record(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    _no_sequencing_qc(monkeypatch)
    _with_sections(monkeypatch, pgt_analysis=_PGT_ANALYSIS)
    captured = _capture_audit(monkeypatch)
    session = _Session()
    out = asyncio.run(rss.sign_out_report(session, family_id="FAM1", user=_user()))

    [insert] = [args for args, _ in session.executed if "INSERT INTO report_signouts" in str(args[0])]
    stored = json.loads(insert[1]["snapshot"])
    assert stored["pgt_analysis"] == _PGT_ANALYSIS
    assert rss._canonical_hash(stored) == out["content_hash"]
    assert captured["after"]["not_captured"] == []


def test_a_pgt_analysis_not_captured_at_sign_out_is_named_on_the_trail(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    _no_sequencing_qc(monkeypatch)
    unavailable = {"unavailable": "The PGT analysis's embryo results could not be read."}
    _with_sections(monkeypatch, pgt_analysis=unavailable)
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))

    gap = {"section": "pgt_analysis", "item": "PGT embryo results", "reason": unavailable["unavailable"]}
    assert captured["after"]["not_captured"] == [gap]
    assert out["not_captured"] == [gap]
    assert "1 part(s) not captured" in captured["summary"]


def test_signout_check_compares_the_embryo_calls_a_pgt_familys_record_holds(monkeypatch) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    _with_sections(monkeypatch, pgt_analysis=_PGT_ANALYSIS)
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is True and out["not_compared"] == []

    # After sign-out an embryo's call changes (a member's status edited, blocks re-imported).
    changed = copy.deepcopy(_PGT_ANALYSIS)
    changed["embryos"][0]["call"] = "unaffected_non_carrier"
    _with_sections(monkeypatch, pgt_analysis=changed)
    current = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    assert rss._canonical_hash(current) != rss._canonical_hash(signed)
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is False
    assert out["changed_sections"] == ["pgt_analysis"]


def test_signout_check_names_the_embryo_calls_a_pgt_familys_older_record_predates(monkeypatch) -> None:
    # Signed before CoGA froze the embryo calls: the record still matches, the section is
    # not compared, and nothing is "not captured".
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    assert "pgt_analysis" not in signed
    _with_sections(monkeypatch, pgt_analysis=_PGT_ANALYSIS)
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is True
    assert out["not_compared"] == ["pgt_analysis"]
    assert out["not_captured"] == []


def test_signout_check_does_not_name_the_pgt_section_for_a_family_without_embryos(monkeypatch) -> None:
    # Neither the record nor the family's data holds it: it is no section of this family.
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert "pgt_analysis" not in signed
    assert out["matches"] is True and out["not_compared"] == [] and out["changed_sections"] == []


def test_signout_check_says_when_a_family_lost_the_embryo_calls_its_record_holds(monkeypatch) -> None:
    # The record holds them; the family's data no longer gives them (its embryos removed).
    _patch_check(monkeypatch, reviews=[_REVIEW])
    _with_sections(monkeypatch, pgt_analysis=_PGT_ANALYSIS)
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    _with_sections(monkeypatch)
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is False and out["changed_sections"] == ["pgt_analysis"]


# The same analysis as CoGA serves it since 2026-10-20: each embryo's phasing-QC verdict and the
# thresholds it was made against (pgt_embryo_qc). _PGT_ANALYSIS above is a record signed before.
def _with_qc_verdict(analysis: dict, **thresholds: float) -> dict:
    from backend.app.schemas import PgtEmbryoQcOut, PgtQcThresholdsOut
    from backend.app.services import pgt_embryo_qc

    limits = PgtQcThresholdsOut(**{**pgt_embryo_qc.QC_THRESHOLDS.model_dump(), **thresholds})
    out = copy.deepcopy(analysis)
    for embryo in out["embryos"]:
        embryo["qc_verdict"] = pgt_embryo_qc.embryo_qc_verdict(
            PgtEmbryoQcOut(**embryo["qc"]), ("paternal",), limits
        ).model_dump(mode="json")
    out["rules"]["qc_thresholds"] = limits.model_dump(mode="json")
    return out


def test_signout_check_compares_a_pgt_record_signed_before_the_qc_verdict_on_what_it_holds(monkeypatch) -> None:
    # Signed before CoGA checked the embryos' QC against thresholds: the record still matches,
    # the verdict is named as not compared, and nothing is "not captured".
    _patch_check(monkeypatch, reviews=[_REVIEW])
    _with_sections(monkeypatch, pgt_analysis=_PGT_ANALYSIS)
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    current = _with_qc_verdict(_PGT_ANALYSIS)
    assert current["embryos"][0]["qc_verdict"]["status"] == "warning"
    _with_sections(monkeypatch, pgt_analysis=current)
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert out["matches"] is True, out
    assert out["changed_sections"] == []
    assert out["not_compared"] == ["pgt_analysis.qc_verdict"]
    assert out["not_captured"] == []

    # What the record holds is still compared: a call that changed since reads as a change.
    changed = copy.deepcopy(current)
    changed["embryos"][0]["call"] = "unaffected_non_carrier"
    _with_sections(monkeypatch, pgt_analysis=changed)
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert (out["matches"], out["changed_sections"]) == (False, ["pgt_analysis"])


def test_signout_check_reads_a_changed_qc_verdict_or_threshold_as_a_change(monkeypatch) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed_analysis = _with_qc_verdict(_PGT_ANALYSIS)
    _with_sections(monkeypatch, pgt_analysis=signed_analysis)
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert (out["matches"], out["not_compared"]) == (True, [])

    # The laboratory lowers the marker threshold after sign-out: the verdict the record froze
    # is no longer the one CoGA gives, and the check says so.
    relaxed = _with_qc_verdict(_PGT_ANALYSIS, min_markers_per_side=3)
    assert relaxed["embryos"][0]["qc_verdict"] != signed_analysis["embryos"][0]["qc_verdict"]
    assert relaxed["rules"]["qc_thresholds"]["min_markers_per_side"] == 3
    _with_sections(monkeypatch, pgt_analysis=relaxed)
    out = asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )
    assert (out["matches"], out["changed_sections"], out["not_compared"]) == (False, ["pgt_analysis"], [])


# --- The repeat calls' near-boundary warnings and QA's counting-units records ---------------
#
# Since the record froze them (signed_record_sections.repeat_expansion_calls), each allele carries
# its near-boundary warning and each locus QA's record of TRGT's counting units there
# (repeat_count_units). The sections are built in test_signed_record_repeat_count_units.py; here
# the check reads them.

_NOT_VALIDATED = {"validated": False, "reason": None, "updated_by": None, "updated_at": None}
_VALIDATED = {
    "validated": True,
    "reason": "Validation report 13",
    "updated_by": "qa-lead",
    "updated_at": "2026-10-20T09:30:00+00:00",
}
_WITHDRAWN = {**_VALIDATED, "validated": False, "reason": "New TRGT catalogue", "updated_at": "2026-10-21T08:00:00+00:00"}
_WARNING_53 = {"repeat_count": 53, "margin": 3, "boundaries": [{"repeat_count": 55, "status": "premutation"}]}


def _repeat_section(count_units: dict, *, count: int = 53, warning: dict | None = None) -> dict:
    """``repeat_expansions`` with a woman's FMR1 call of 30 and ``count`` (53: grey zone, near the
    premutation at 55), at a locus with QA's record ``count_units``."""
    return {
        "catalogue": {"loci": 1, "sources": ["STRchive"], "digest": "1" * 64},
        "count_unit_margin": 3,
        "loci": [
            {
                "locus_id": "FXS_FMR1",
                "gene": "FMR1",
                "disease": "Fragile X syndrome",
                "inheritance": "XLD",
                "chrom": "chrX",
                "start": 147912049,
                "end": 147912111,
                "motif": "CGG",
                "thresholds": {
                    "intermediate_min": 45,
                    "premutation_min": 55,
                    "pathogenic_min": 201,
                    "benign_min": 5,
                    "benign_max": 44,
                    "pathogenic_max": 2000,
                },
                "status": "intermediate",
                "count_units": count_units,
                "calls": [
                    {
                        "sample": "S1",
                        "genotype": "1/2",
                        "status": "intermediate",
                        "note": None,
                        "alleles": [
                            {"repeat_count": 30, "bp_length": 90, "support_reads": 20, "interruption_label": None, "status": "normal", "count_unit_warning": None},
                            {"repeat_count": count, "bp_length": count * 3, "support_reads": 18, "interruption_label": None, "status": "intermediate", "count_unit_warning": warning},
                        ],
                    }
                ],
            }
        ],
    }


def _before_count_units(section: dict) -> dict:
    """``repeat_expansions`` as a record signed before CoGA froze the warnings holds it."""
    old = copy.deepcopy(section)
    old.pop("count_unit_margin")
    for locus in old["loci"]:
        locus.pop("count_units")
        for call in locus["calls"]:
            for allele in call["alleles"]:
                allele.pop("count_unit_warning")
    return old


def _check_against(monkeypatch, signed_section: dict, current_section: dict) -> dict:
    _with_sections(monkeypatch, repeat_expansions=signed_section)
    signed = asyncio.run(rss.build_report_snapshot(_Session(), family_id="FAM1", user=_user()))
    _with_sections(monkeypatch, repeat_expansions=current_section)
    return asyncio.run(
        rss.compare_report_with_latest_signout(_CheckSession(_signed_row(signed)), family_id="FAM1", user=_user())
    )


def test_signout_check_reads_a_locus_validated_since_sign_out_as_a_change(monkeypatch) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    signed_section = _repeat_section(_NOT_VALIDATED, warning=_WARNING_53)
    out = _check_against(monkeypatch, signed_section, signed_section)
    assert (out["matches"], out["changed_sections"], out["not_compared"]) == (True, [], [])

    # QA validates TRGT's counting units at FMR1 after sign-out: the 53 is no longer marked, and
    # the record held it marked at a locus not validated.
    out = _check_against(monkeypatch, signed_section, _repeat_section(_VALIDATED))
    assert (out["matches"], out["changed_sections"], out["not_compared"]) == (False, ["repeat_expansions"], [])


def test_signout_check_reads_a_withdrawn_validation_as_a_change(monkeypatch) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    out = _check_against(monkeypatch, _repeat_section(_VALIDATED), _repeat_section(_WITHDRAWN, warning=_WARNING_53))
    assert (out["matches"], out["changed_sections"], out["not_compared"]) == (False, ["repeat_expansions"], [])


def test_signout_check_compares_a_repeat_record_signed_before_the_warnings_on_what_it_holds(monkeypatch) -> None:
    # Signed before CoGA froze the warnings: the record still matches, the warnings and QA's
    # records are named as not compared, and nothing is "not captured".
    _patch_check(monkeypatch, reviews=[_REVIEW])
    old = _before_count_units(_repeat_section(_NOT_VALIDATED))
    out = _check_against(monkeypatch, old, _repeat_section(_NOT_VALIDATED, warning=_WARNING_53))
    assert out["matches"] is True, out
    assert out["changed_sections"] == []
    assert out["not_compared"] == ["repeat_expansions.count_units"]
    assert out["not_captured"] == []

    # What it holds is still compared: a count that changed since reads as a change.
    out = _check_against(monkeypatch, old, _repeat_section(_NOT_VALIDATED, count=56, warning=None))
    assert (out["matches"], out["changed_sections"]) == (False, ["repeat_expansions"])
    assert out["not_compared"] == ["repeat_expansions.count_units"]


def test_signout_check_compares_a_repeat_section_not_captured_at_sign_out_whole(monkeypatch) -> None:
    _patch_check(monkeypatch, reviews=[_REVIEW])
    unavailable = {"unavailable": "The repeat-expansion calls could not be read."}
    out = _check_against(monkeypatch, unavailable, _repeat_section(_NOT_VALIDATED, warning=_WARNING_53))
    assert (out["matches"], out["changed_sections"], out["not_compared"]) == (False, ["repeat_expansions"], [])
    assert out["not_captured"] == [
        {"section": "repeat_expansions", "item": "Repeat expansions (TRGT)", "reason": unavailable["unavailable"]}
    ]


def test_a_sign_out_carries_its_report_code(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    captured = _capture_audit(monkeypatch)
    out = asyncio.run(rss.sign_out_report(_Session(), family_id="FAM1", user=_user()))

    code = report_code("FAM1", 1, out["content_hash"])
    assert code is not None and out["report_code"] == code
    assert parse_report_code(code).version == 1
    # On the clinical audit trail with the sign-out, in its summary and its record.
    assert captured["after"]["report_code"] == code
    assert f"report code {code}" in captured["summary"]
    # As a detail read serves it.
    assert ReportSignoutDetail.model_validate(out).report_code == code


def test_a_report_code_another_record_has_is_never_given_again(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    asked: list[str] = []

    async def _first_taken(session, code):
        asked.append(code)
        return len(asked) == 1  # the first code this sign-out makes is another record's

    monkeypatch.setattr(rss, "_report_code_taken", _first_taken)
    session = _Session()
    out = asyncio.run(rss.sign_out_report(session, family_id="FAM1", user=_user()))

    assert len(asked) == 2 and asked[0] != asked[1]
    assert out["report_code"] == asked[1]
    # Signed a microsecond later: its own time, its own content hash, its own code.
    [insert] = [args for args, _ in session.executed if "INSERT INTO report_signouts" in str(args[0])]
    stored = json.loads(insert[1]["snapshot"])
    assert stored["generated_at"] == out["signed_out_at"].isoformat()
    assert rss._canonical_hash(stored) == out["content_hash"] == insert[1]["content_hash"]
    assert report_code("FAM1", 1, out["content_hash"]) == asked[1]
    # The lock every sign-out takes while it makes its code unique.
    locks = [args[1].get("k") for args, _ in session.executed if "pg_advisory_xact_lock" in str(args[0])]
    assert "report-code" in locks


def test_a_listed_version_names_its_report_code() -> None:
    row = {
        "version": 2,
        "signed_out_by": "lab-signer",
        "signed_out_at": datetime(2026, 10, 14, 9, 0),
        "content_hash": "ab" * 32,
        "family_identifier": "FAM1",
    }
    assert rss._serialize_signout(row)["report_code"] == report_code("FAM1", 2, "ab" * 32)
    # A row whose fields cannot make one names none, rather than a wrong one.
    assert rss._serialize_signout({**row, "family_identifier": None})["report_code"] is None


def test_a_record_signed_before_these_sections_still_verifies_and_reads(monkeypatch) -> None:
    row = _pre_gate_row()
    assert not {section.key for section in RECORD_SECTIONS} & set(row["snapshot"])
    assert "record_statement" not in row["snapshot"]
    chain = asyncio.run(rss.verify_report_signout_chain(_RowsSession([row]), "FAM1"))
    assert chain.verified, chain

    detail = _detail_of(monkeypatch, row)
    assert detail["verified"] is True
    # It has a report code: made from fields every record holds.
    assert detail["report_code"] == report_code("FAM1", 1, _PRE_GATE_CONTENT_HASH)
    # The sections it predates are not "not captured": the view says the record does not
    # hold them, and the check does not compare them.
    keys = {section.key for section in RECORD_SECTIONS}
    assert not [gap for gap in detail["not_captured"] if gap["section"] in keys]


# --- Finding a record by its report code -------------------------------------------------


class _CodeSession:
    """Answers the report-code candidates read with fixed rows."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.params: list = []

    async def execute(self, statement, params=None):
        self.params.append(params)
        rows = self.rows
        return types.SimpleNamespace(
            mappings=lambda: types.SimpleNamespace(
                all=lambda: list(rows), first=lambda: rows[0] if rows else None
            )
        )


_FAMILY_UUID = "44444444-4444-4444-4444-444444444444"
_CONTENT_HASH = "5f" * 32


def _code_row(**overrides) -> dict:
    return {
        "family_uuid": _FAMILY_UUID,
        "family_identifier": "FAM1",
        "version": 2,
        "content_hash": _CONTENT_HASH,
        "signed_out_at": datetime(2026, 10, 14, 9, 0),
        "signed_out_by": "lab-signer",
        **overrides,
    }


def _visible_family(monkeypatch, *, family_uuid: str = _FAMILY_UUID, raises: Exception | None = None) -> list:
    asked: list = []

    async def _ctx(session, *, family_identifier, user, project_id=None):
        asked.append(family_identifier)
        if raises is not None:
            raise raises
        return types.SimpleNamespace(family_uuid=family_uuid, family_id=family_identifier)

    monkeypatch.setattr(rss, "build_family_metadata_context", _ctx)
    return asked


def test_a_report_code_finds_its_record(monkeypatch) -> None:
    asked = _visible_family(monkeypatch)
    code = report_code("FAM1", 2, _CONTENT_HASH)
    assert code is not None
    session = _CodeSession([_code_row(), _code_row(family_identifier="FAM9")])
    # Typed back in lower case, with spaces.
    out = asyncio.run(
        rss.find_report_signout_by_code(session, code=code.lower().replace("-", " "), user=_user())
    )
    assert out == {
        "report_code": code,
        "family_id": "FAM1",
        "version": 2,
        "signed_out_at": datetime(2026, 10, 14, 9, 0),
        "signed_out_by": "lab-signer",
        "content_hash": _CONTENT_HASH,
    }
    # Opened through its family, as the user may open it.
    assert asked == ["FAM1"]
    # Read by its version and the content-hash prefix its code spells, never spliced in.
    assert session.params == [{"version": 2, "prefix": _CONTENT_HASH[:3]}]


def test_a_string_that_is_not_a_report_code_is_refused_as_a_typo(monkeypatch) -> None:
    _visible_family(monkeypatch)
    code = report_code("FAM1", 2, _CONTENT_HASH)
    assert code is not None
    position = next(index for index in range(len(code) - 1) if code[index] != code[index + 1] and "-" not in code[index:index + 2])
    swapped = code[:position] + code[position + 1] + code[position] + code[position + 2:]
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rss.find_report_signout_by_code(_CodeSession([_code_row()]), code=swapped, user=_user()))
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["code"] == "report_code_invalid"
    assert "mistyped" in excinfo.value.detail["message"]


def test_an_unknown_and_an_inaccessible_report_code_answer_the_same_404(monkeypatch) -> None:
    code = report_code("FAM1", 2, _CONTENT_HASH)
    assert code is not None

    _visible_family(monkeypatch)
    with pytest.raises(HTTPException) as unknown:
        asyncio.run(rss.find_report_signout_by_code(_CodeSession([]), code=code, user=_user()))

    # The record exists, but its family is outside the caller's projects.
    _visible_family(monkeypatch, raises=RecordNotVisible("family", "Family not found"))
    with pytest.raises(HTTPException) as hidden:
        asyncio.run(rss.find_report_signout_by_code(_CodeSession([_code_row()]), code=code, user=_user()))

    assert unknown.value.status_code == hidden.value.status_code == 404
    assert unknown.value.detail == hidden.value.detail == "No signed record has this report code."
    # Only the audit tells them apart (REQ-SEC-001).
    assert isinstance(hidden.value, RecordNotVisible) and hidden.value.kind == "family"
    assert not isinstance(unknown.value, RecordNotVisible)


def test_the_record_of_a_deleted_family_is_not_opened_by_its_code(monkeypatch) -> None:
    code = report_code("FAM1", 2, _CONTENT_HASH)
    assert code is not None
    asked = _visible_family(monkeypatch)
    with pytest.raises(HTTPException) as deleted:
        asyncio.run(
            rss.find_report_signout_by_code(_CodeSession([_code_row(family_uuid=None)]), code=code, user=_user())
        )
    assert (deleted.value.status_code, deleted.value.detail) == (404, "No signed record has this report code.")
    assert asked == []

    # Nor by a family made since under its identifier.
    _visible_family(monkeypatch, family_uuid="55555555-5555-5555-5555-555555555555")
    with pytest.raises(HTTPException) as recreated:
        asyncio.run(rss.find_report_signout_by_code(_CodeSession([_code_row()]), code=code, user=_user()))
    assert (recreated.value.status_code, recreated.value.detail) == (404, "No signed record has this report code.")


def test_a_report_code_is_taken_only_by_the_record_it_names() -> None:
    code = report_code("FAM1", 2, _CONTENT_HASH)
    assert code is not None
    # The autouse stub stands in for it elsewhere; here the real reader runs on fixed rows.
    taken = _REPORT_CODE_TAKEN
    assert asyncio.run(taken(_CodeSession([_code_row()]), code)) is True
    # Same version and hash prefix, another family: another code.
    assert asyncio.run(taken(_CodeSession([_code_row(family_identifier="FAM2")]), code)) is False
    assert asyncio.run(taken(_CodeSession([]), code)) is False


def test_a_family_that_is_gone_answers_like_an_unknown_code_and_other_errors_pass_through(monkeypatch) -> None:
    code = report_code("FAM1", 2, _CONTENT_HASH)
    assert code is not None
    _visible_family(monkeypatch, raises=HTTPException(status_code=404, detail="Family not found"))
    with pytest.raises(HTTPException) as gone:
        asyncio.run(rss.find_report_signout_by_code(_CodeSession([_code_row()]), code=code, user=_user()))
    assert (gone.value.status_code, gone.value.detail) == (404, "No signed record has this report code.")
    assert not isinstance(gone.value, RecordNotVisible)

    _visible_family(monkeypatch, raises=HTTPException(status_code=400, detail="Bad project"))
    with pytest.raises(HTTPException) as other:
        asyncio.run(rss.find_report_signout_by_code(_CodeSession([_code_row()]), code=code, user=_user()))
    assert (other.value.status_code, other.value.detail) == (400, "Bad project")


def test_the_list_of_signed_versions_names_each_ones_report_code(monkeypatch) -> None:
    _patch_common(monkeypatch, drifted_count=0)
    rows = [
        {
            "version": version,
            "signed_out_by": "lab-signer",
            "signed_out_at": datetime(2026, 10, 14, 9, version),
            "content_hash": content_hash,
            "family_identifier": "FAM1",
        }
        for version, content_hash in ((2, "ab" * 32), (1, "cd" * 32))
    ]
    out = asyncio.run(rss.list_report_signouts(_RowsSession(rows), family_id="FAM1", user=_user()))
    assert [entry["report_code"] for entry in out["signouts"]] == [
        report_code("FAM1", 2, "ab" * 32),
        report_code("FAM1", 1, "cd" * 32),
    ]
    assert out["latest"]["report_code"] == report_code("FAM1", 2, "ab" * 32)


def test_the_report_code_endpoint_serves_the_lookup(monkeypatch) -> None:
    from backend.app.routers import report_codes

    seen: dict = {}

    async def _find(session, *, code, user):
        seen["code"] = code
        return {
            "report_code": "7KQM2X-2-4PHT",
            "family_id": "FAM1",
            "version": 2,
            "signed_out_at": datetime(2026, 10, 14, 9, 0),
            "signed_out_by": "lab-signer",
            "content_hash": _CONTENT_HASH,
        }

    monkeypatch.setattr(report_codes, "find_report_signout_by_code", _find)
    out = asyncio.run(
        report_codes.find_signed_record_by_report_code("7kqm2x-2-4pht", session=object(), user=_user())
    )
    assert seen == {"code": "7kqm2x-2-4pht"}
    assert (out.family_id, out.version, out.report_code) == ("FAM1", 2, "7KQM2X-2-4PHT")
