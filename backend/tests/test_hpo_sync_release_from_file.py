"""The admin HPO sync records the release the ontology file states, and the file's SHA-256 (TRACE-18).

``POST /admin/hpo/sync`` loaded any ontology file in the folder unchecked, and took a release the
admin typed over the one the file states. That release went onto every term and into every
signed record after it, so a record could name a release the file was not, and no record named
the file. The owner chose (2026-10-27) to keep the sync as the route for a new release, checked:
the release is the one the file states (its ``data-version``), an expected release or date the
admin types must be what the file states or nothing is imported, a file that states no release
is refused, and the terms keep the SHA-256 of the bytes imported (``metadata.release_sha256``),
which the loaded release, the admin summary and the signed record's HPO entry name. The startup
import of the pinned file records the pinned digest the same way.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core import config
from app.services import annotation_manifest_service, hpo_service

_VENDORED_HPO = Path(__file__).resolve().parents[2] / "data" / "ref-data" / "hpo" / "hp.obo"

_OBO = """format-version: 1.2
data-version: hp/releases/2026-09-01
ontology: hp

[Term]
id: HP:0000001
name: All

[Term]
id: HP:0000118
name: Phenotypic abnormality
is_a: HP:0000001 ! All
"""


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---- The release the file states -------------------------------------------------------------------


def test_the_release_is_the_one_the_file_states_with_its_sha256() -> None:
    payload = _VENDORED_HPO.read_bytes()

    checked = hpo_service.checked_hpo_file_release(payload, source_format="obo")

    assert checked.release_version == config.HPO_PINNED_RELEASE
    assert checked.release_date == date(2026, 2, 16)
    assert checked.sha256 == config.HPO_PINNED_ONTOLOGY_SHA256 == _digest(payload)
    assert checked.text == payload.decode("utf-8")


@pytest.mark.parametrize("expected", ["hp/releases/2026-09-01", "2026-09-01", " 2026-09-01 "])
def test_an_expected_release_the_file_states_is_accepted(expected: str) -> None:
    checked = hpo_service.checked_hpo_file_release(
        _OBO.encode(), source_format="obo", expected_version=expected, expected_date=date(2026, 9, 1)
    )

    assert (checked.release_version, checked.release_date) == ("hp/releases/2026-09-01", date(2026, 9, 1))


@pytest.mark.parametrize("expected", ["hp/releases/2026-10-01", "2026-10-01", "09-01", "releases/2026"])
def test_an_expected_release_the_file_does_not_state_is_refused(expected: str) -> None:
    # Before, the typed release replaced the file's own, unchecked.
    with pytest.raises(ValueError, match=f"is release hp/releases/2026-09-01, not the expected {expected}"):
        hpo_service.checked_hpo_file_release(_OBO.encode(), source_format="obo", expected_version=expected)


def test_an_expected_date_the_file_does_not_state_is_refused() -> None:
    with pytest.raises(ValueError, match="states 2026-09-01, not the expected release date 2026-10-01"):
        hpo_service.checked_hpo_file_release(_OBO.encode(), source_format="obo", expected_date=date(2026, 10, 1))


def test_a_file_that_states_no_release_is_refused() -> None:
    # Before, it was imported with the typed release, or with none.
    without_release = _OBO.replace("data-version: hp/releases/2026-09-01\n", "")

    with pytest.raises(ValueError, match="states no release"):
        hpo_service.checked_hpo_file_release(without_release.encode(), source_format="obo", expected_version="2026-09-01")


# ---- The sync ----------------------------------------------------------------------------------------


@pytest.fixture
def sync_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    imported: list[dict[str, Any]] = []

    async def preview(_session: Any, *, path: Any) -> dict[str, int]:
        return {"terms": 2}

    async def summary(_session: Any) -> dict[str, Any]:
        return {"total_terms": 0}

    async def tables_available(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def record_import(_session: Any, **kwargs: Any) -> dict[str, int]:
        imported.append(kwargs)
        return {"terms": 2, "synonyms": 0, "edges": 1, "closure_rows": 3}

    monkeypatch.setattr(hpo_service, "preview_hpo_ontology_sync", preview)
    monkeypatch.setattr(hpo_service, "get_hpo_admin_summary", summary)
    monkeypatch.setattr(hpo_service, "_postgres_tables_available", tables_available)
    monkeypatch.setattr(hpo_service, "import_hpo_ontology", record_import)
    return imported


@pytest.mark.asyncio
async def test_a_preview_names_the_files_release_and_sha256(sync_calls: list, tmp_path: Path) -> None:
    ontology = tmp_path / "hp.obo"
    ontology.write_text(_OBO, encoding="utf-8")

    result = await hpo_service.sync_hpo_ontology(object(), path=ontology, preview_only=True)

    assert result["release_version"] == "hp/releases/2026-09-01"
    assert result["release_date"] == date(2026, 9, 1)
    assert result["release_sha256"] == _digest(_OBO.encode())
    assert sync_calls == []


@pytest.mark.asyncio
async def test_the_sync_imports_the_checked_bytes_with_the_files_release_and_sha256(
    sync_calls: list, tmp_path: Path
) -> None:
    ontology = tmp_path / "hp.obo"
    ontology.write_text(_OBO, encoding="utf-8")

    result = await hpo_service.sync_hpo_ontology(
        object(), path=ontology, release_version="2026-09-01", preview_only=False
    )

    [call] = sync_calls
    # The release as the file states it, not as typed; the text of the bytes that were hashed.
    assert call["release_version"] == "hp/releases/2026-09-01"
    assert call["release_date"] == date(2026, 9, 1)
    assert call["release_sha256"] == _digest(_OBO.encode())
    assert call["ontology_text"] == _OBO
    assert result["release_sha256"] == _digest(_OBO.encode())


@pytest.mark.asyncio
@pytest.mark.parametrize("preview_only", [True, False])
async def test_another_release_than_expected_imports_nothing(
    sync_calls: list, tmp_path: Path, preview_only: bool
) -> None:
    ontology = tmp_path / "hp.obo"
    ontology.write_text(_OBO, encoding="utf-8")

    with pytest.raises(ValueError, match="not the expected 2026-10-01"):
        await hpo_service.sync_hpo_ontology(
            object(), path=ontology, release_version="2026-10-01", preview_only=preview_only
        )

    assert sync_calls == []


# ---- What the terms keep -----------------------------------------------------------------------------


class _CapturingSession:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> None:
        self.executed.append((str(statement), params))

    async def commit(self) -> None:
        return None


def _term_rows(session: _CapturingSession) -> list[dict[str, Any]]:
    [(_, rows)] = [(sql, params) for sql, params in session.executed if "INSERT INTO hpo_term" in sql]
    return rows


@pytest.mark.asyncio
async def test_each_term_keeps_the_sha256_of_the_file_it_came_from(tmp_path: Path) -> None:
    session = _CapturingSession()

    await hpo_service.import_hpo_ontology(
        session,
        path=tmp_path / "hp.obo",
        ontology_text=_OBO,
        release_version="hp/releases/2026-09-01",
        release_date=date(2026, 9, 1),
        release_sha256=_digest(_OBO.encode()),
    )

    rows = _term_rows(session)
    assert {row["hpo_id"] for row in rows} == {"HP:0000001", "HP:0000118"}
    assert {json.loads(row["metadata"])["release_sha256"] for row in rows} == {_digest(_OBO.encode())}


@pytest.mark.asyncio
async def test_an_import_without_a_checksum_writes_none(tmp_path: Path) -> None:
    session = _CapturingSession()

    await hpo_service.import_hpo_ontology(session, path=tmp_path / "hp.obo", ontology_text=_OBO)

    assert all("release_sha256" not in json.loads(row["metadata"]) for row in _term_rows(session))


class _ReleaseResult:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def mappings(self) -> "_ReleaseResult":
        return self

    def first(self) -> dict[str, Any]:
        return self.row


class _ReleaseSession:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.sql: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _ReleaseResult:
        self.sql.append(str(statement))
        return _ReleaseResult(self.row)


@pytest.mark.asyncio
async def test_the_loaded_release_names_the_files_sha256() -> None:
    row = {"release_version": "hp/releases/2026-09-01", "release_date": date(2026, 9, 1), "release_sha256": "a" * 64}
    session = _ReleaseSession(row)

    loaded = await hpo_service.get_loaded_hpo_release(session)

    assert loaded == row
    assert "metadata ->> 'release_sha256' AS release_sha256" in session.sql[0]


@pytest.mark.asyncio
async def test_the_signed_records_hpo_entry_names_the_files_sha256(monkeypatch: pytest.MonkeyPatch) -> None:
    async def loaded(_session: Any) -> dict[str, Any]:
        return {"release_version": "hp/releases/2026-09-01", "release_date": date(2026, 9, 1), "release_sha256": "a" * 64}

    modules = await _platform_hpo_module(monkeypatch, loaded)

    assert modules == {"version": "hp/releases/2026-09-01", "detail": f"2026-09-01; SHA-256 {'a' * 64}"}


@pytest.mark.asyncio
async def test_an_import_from_before_the_checksum_keeps_its_date_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    async def loaded(_session: Any) -> dict[str, Any]:
        return {"release_version": "hp/releases/2026-02-16", "release_date": date(2026, 2, 16), "release_sha256": None}

    assert await _platform_hpo_module(monkeypatch, loaded) == {"version": "hp/releases/2026-02-16", "detail": "2026-02-16"}


async def _platform_hpo_module(monkeypatch: pytest.MonkeyPatch, loaded: Any) -> Any:
    """The HPO entry the annotation manifest's reference layer records."""
    monkeypatch.setattr(annotation_manifest_service, "get_loaded_hpo_release", loaded)

    class _Nested:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *exc: object) -> bool:
            return False

    class _Session:
        def begin_nested(self) -> _Nested:
            return _Nested()

        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            # The Monarch lookup: recorded as unavailable, as a failed lookup is.
            raise RuntimeError("only the HPO lookup is under test")

    # Without an assembly the gene-locus lookups are skipped.
    modules = await annotation_manifest_service._platform_modules(_Session(), None)
    return modules.get("hpo")
