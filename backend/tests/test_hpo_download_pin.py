"""The HPO startup download is pinned to the vendored release (OPS-6).

At startup the backend imports the vendored data/ref-data/hpo/hp.obo. Only when it finds no
ontology file, as on a deployment whose reference data lacks it, does it download one. That
download was the floating hp.obo, unchecked unless HPO_ONTOLOGY_SHA256 was set, so such a
deployment imported whatever HPO release was current on its first start. It now fetches the
vendored file's own release from its versioned PURL, and uses the file only if its SHA-256 is
the vendored file's. The URL and the digest are code constants: the HPO_ONTOLOGY_URL and
HPO_ONTOLOGY_SHA256 settings that could replace them were removed (owner decision), so the
release changes only through a reviewed code change, and a deployment that still sets either
variable gets the pin. Nothing here goes to the network: urlopen is replaced.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import io
import logging
from pathlib import Path
from typing import Any

import pytest

from backend.app import main as app_main
from backend.app.core.config import (
    HPO_PINNED_ONTOLOGY_SHA256,
    HPO_PINNED_ONTOLOGY_URL,
    HPO_PINNED_RELEASE,
    Settings,
)
from backend.app.services import hpo_service

_VENDORED_HPO = Path(__file__).resolve().parents[2] / "data" / "ref-data" / "hpo" / "hp.obo"
# What the floating hp.obo serves once HPO has published a later release.
_LATER_RELEASE = (
    b"format-version: 1.2\ndata-version: hp/releases/2026-09-01\n\n[Term]\nid: HP:0000001\nname: All\n"
)
# The two variables that could replace the pin until they were removed.
_REMOVED_VARIABLES = ("HPO_ONTOLOGY_URL", "HPO_ONTOLOGY_SHA256")
# A mirror an operator once configured, serving another release.
_MIRROR_URL = "https://hpo-mirror.example/hp.obo"


@functools.cache
def _vendored() -> tuple[bytes, str, str]:
    """The vendored ontology's bytes, its release (the `data-version:` header) and its SHA-256."""
    payload = _VENDORED_HPO.read_bytes()
    header = payload.split(b"[Term]", 1)[0].decode("utf-8")
    release = hpo_service.parse_hpo_ontology_release_metadata_text(header).release_version
    assert release and release.startswith("hp/releases/"), release
    return payload, release, hashlib.sha256(payload).hexdigest()


def _serve(monkeypatch: pytest.MonkeyPatch, payload: bytes, **by_url: bytes) -> list[str]:
    """Answer a download with ``payload``, or with what ``by_url`` maps its URL to; the list
    collects the URLs requested."""
    requested: list[str] = []

    def fake_urlopen(request: Any, timeout: float = 60) -> io.BytesIO:
        requested.append(request.full_url)
        return io.BytesIO(by_url.get(request.full_url, payload))

    monkeypatch.setattr(hpo_service, "urlopen", fake_urlopen)
    return requested


class _Session:
    async def rollback(self) -> None:
        return None


def _start_without_an_ontology_file(monkeypatch: pytest.MonkeyPatch, target: Path) -> list[dict[str, Any]]:
    """An empty HPO terminology and no ontology file, so the startup downloads one to
    ``target``. The list collects what is imported (the file and its release)."""
    imported: list[dict[str, Any]] = []

    async def no_terms(_session: Any) -> int:
        return 0

    async def record_import(_session: Any, **kwargs: Any) -> dict[str, int]:
        imported.append({"path": kwargs["path"], "release_version": kwargs["release_version"]})
        return {"terms": 1, "synonyms": 0, "edges": 0, "closure_rows": 1}

    monkeypatch.setattr(hpo_service, "_hpo_term_count", no_terms)
    monkeypatch.setattr(hpo_service, "hpo_ontology_candidate_paths", lambda _path=None: [target])
    monkeypatch.setattr(hpo_service, "import_hpo_ontology", record_import)
    return imported


def test_the_pin_is_the_vendored_files_release_and_sha256() -> None:
    _payload, release, digest = _vendored()
    assert HPO_PINNED_RELEASE == release
    # The release's own PURL, which never floats, unlike https://purl.obolibrary.org/obo/hp.obo.
    assert HPO_PINNED_ONTOLOGY_URL == f"https://purl.obolibrary.org/obo/{release}/hp.obo"
    assert HPO_PINNED_ONTOLOGY_SHA256 == digest
    # The README that records the vendored file names the same release, digest and URL.
    readme = _VENDORED_HPO.with_name("README.md").read_text(encoding="utf-8")
    for value in (release, digest, HPO_PINNED_ONTOLOGY_URL):
        assert value in readme, value


def test_no_setting_and_no_argument_moves_the_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    # The settings define neither the URL nor the digest, under any name.
    names = {name.upper() for key, field in Settings.model_fields.items() for name in (key, field.alias) if name}
    assert names.isdisjoint(_REMOVED_VARIABLES)
    # A deployment that still sets them starts: the settings ignore them, like every variable
    # they do not define, and keep neither value.
    monkeypatch.setenv("HPO_ONTOLOGY_URL", _MIRROR_URL)
    monkeypatch.setenv("HPO_ONTOLOGY_SHA256", hashlib.sha256(_LATER_RELEASE).hexdigest())
    loaded = Settings(_env_file=None, APP_ENV="test")
    assert not {"hpo_ontology_url", "hpo_ontology_sha256"} & loaded.model_dump().keys()
    assert _MIRROR_URL not in loaded.model_dump().values()
    # Nor can a caller hand the startup another URL or digest.
    assert set(inspect.signature(hpo_service.ensure_hpo_ontology_on_startup).parameters) == {
        "session",
        "ontology_path",
        "download_if_missing",
        "enabled",
    }


class _App:
    class state:  # FastAPI's app.state, as the lifespan reads it
        skip_startup_tasks = False


# Every step of the lifespan but the HPO bootstrap: replaced, so the startup touches no database.
_OTHER_STARTUP_STEPS = (
    "wait_for_postgres",
    "init_postgres_schema",
    "init_postgres_admin_user",
    "start_audit_log_worker",
    "start_ui_event_worker",
    "seed_builtin_repeat_catalog",
    "ensure_human_grch38_reference_on_startup",
    "ensure_human_t2t_reference_on_startup",
    "seed_builtin_reference_tracks",
    "queue_startup_gene_reference_refresh_if_needed",
    "wait_for_clickhouse",
    "init_clickhouse_schema",
    "verify_clickhouse_variant_storage_identity",
    "drop_orphaned_import_backups",
    "start_clickhouse_integrity_monitor",
    "gene_reference_refresh_worker",
    "family_package_import_worker",
    "stop_gene_reference_worker",
    "stop_family_package_import_worker",
    "stop_clickhouse_integrity_monitor",
    "stop_audit_log_worker",
    "stop_ui_event_worker",
    "close_clickhouse_client",
    "close_postgres_engine",
)


@pytest.mark.asyncio
async def test_a_deployment_that_still_sets_the_removed_variables_imports_the_pinned_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # An operator once pointed the download at a mirror serving a later release and set that
    # release's digest. The variables were removed: the startup, as the backend runs it,
    # downloads the pinned release from its PURL and imports the vendored file's bytes.
    payload, release, _digest = _vendored()
    monkeypatch.setenv("HPO_ONTOLOGY_URL", _MIRROR_URL)
    monkeypatch.setenv("HPO_ONTOLOGY_SHA256", hashlib.sha256(_LATER_RELEASE).hexdigest())
    deployment = Settings(
        _env_file=None,
        APP_ENV="test",
        HPO_BOOTSTRAP_ON_STARTUP=True,
        HPO_DOWNLOAD_IF_MISSING=True,
        FAMILY_IMPORT_WORKER_COUNT=1,
    )
    monkeypatch.setattr(app_main, "settings", deployment)
    monkeypatch.setattr(hpo_service, "settings", deployment)

    async def no_op(*_args: Any, **_kwargs: Any) -> None:
        return None

    class _SessionFactory:
        async def __aenter__(self) -> _Session:
            return _Session()

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

    for name in _OTHER_STARTUP_STEPS:
        monkeypatch.setattr(app_main, name, no_op)
    monkeypatch.setattr(app_main, "get_postgres_sessionmaker", lambda: _SessionFactory)
    target = tmp_path / "ref-data" / "hpo" / "hp.obo"
    requested = _serve(monkeypatch, payload, **{_MIRROR_URL: _LATER_RELEASE})
    imported = _start_without_an_ontology_file(monkeypatch, target)

    async with app_main.lifespan(_App()):
        pass

    assert requested == [HPO_PINNED_ONTOLOGY_URL]
    assert target.read_bytes() == payload
    assert imported == [{"path": target, "release_version": release}]


@pytest.mark.asyncio
async def test_without_an_ontology_file_the_startup_downloads_and_imports_the_pinned_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload, release, _digest = _vendored()
    target = tmp_path / "ref-data" / "hpo" / "hp.obo"
    requested = _serve(monkeypatch, payload)
    imported = _start_without_an_ontology_file(monkeypatch, target)

    result = await hpo_service.ensure_hpo_ontology_on_startup(_Session(), ontology_path=target)

    assert requested == [f"https://purl.obolibrary.org/obo/{release}/hp.obo"]
    assert target.read_bytes() == payload
    assert imported == [{"path": target, "release_version": release}]
    assert result is not None and result["release_version"] == release


@pytest.mark.asyncio
async def test_a_download_that_is_not_the_pinned_file_is_refused_logged_and_not_imported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _payload, _release, digest = _vendored()
    target = tmp_path / "ref-data" / "hpo" / "hp.obo"
    _serve(monkeypatch, _LATER_RELEASE)
    imported = _start_without_an_ontology_file(monkeypatch, target)
    caplog.set_level(logging.INFO, logger=hpo_service.logger.name)

    result = await hpo_service.ensure_hpo_ontology_on_startup(_Session(), ontology_path=target)

    assert result is None
    assert imported == []
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    # Both digests, so the log says what was refused and what was expected.
    assert hashlib.sha256(_LATER_RELEASE).hexdigest() in message
    assert digest in message
    assert "not imported" in message


def test_the_download_is_never_unchecked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Without a SHA-256 to check against, nothing is requested.
    requested = _serve(monkeypatch, _LATER_RELEASE)
    target = tmp_path / "hp.obo"
    for not_a_digest in ("", "   ", "not-a-digest", "sha256:" + "0" * 64):
        with pytest.raises(RuntimeError, match="pinned SHA-256"):
            hpo_service._download_hpo_ontology_file(
                HPO_PINNED_ONTOLOGY_URL, [target], expected_sha256=not_a_digest
            )
    assert requested == []
    assert not target.exists()


def test_a_refused_download_leaves_the_file_on_disk_as_it_was(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # The digest is checked before anything is written, against the pin when the caller
    # names none: a file already at the target stays as it was, and no partial file is left.
    target = tmp_path / "hpo" / "hp.obo"
    target.parent.mkdir()
    kept = b"format-version: 1.2\ndata-version: hp/releases/2026-02-16\n"
    target.write_bytes(kept)
    _serve(monkeypatch, _LATER_RELEASE)

    with pytest.raises(RuntimeError, match="SHA-256"):
        hpo_service._download_hpo_ontology_file(HPO_PINNED_ONTOLOGY_URL, [target])

    assert target.read_bytes() == kept
    assert [path.name for path in target.parent.iterdir()] == ["hp.obo"]


def test_the_download_follows_a_redirect_only_to_another_https_url() -> None:
    from urllib.error import URLError
    from urllib.request import Request

    handler = hpo_service._HttpsOnlyRedirects()
    request = Request(HPO_PINNED_ONTOLOGY_URL)

    # The PURL redirects to the release on GitHub: followed.
    onward = handler.redirect_request(request, None, 302, "Found", {}, "https://github.com/obophenotype/hp.obo")
    assert onward.full_url == "https://github.com/obophenotype/hp.obo"
    # A redirect to plain HTTP was followed too (SEC-32); it is refused before any request.
    with pytest.raises(URLError, match="not HTTPS"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://mirror.example.org/hp.obo")
    # The download's opener is the one with this handler.
    assert any(isinstance(each, hpo_service._HttpsOnlyRedirects) for each in hpo_service.urlopen.__self__.handlers)
