"""The HPO startup download is pinned to the vendored release (OPS-6).

At startup the backend imports the vendored data/ref-data/hpo/hp.obo. Only when it finds no
ontology file, as on a deployment whose reference data lacks it, does it download one. That
download was the floating hp.obo, unchecked unless HPO_ONTOLOGY_SHA256 was set, so such a
deployment imported whatever HPO release was current on its first start. It now fetches the
vendored file's own release from its versioned PURL, and uses the file only if its SHA-256 is
the vendored file's. Nothing here goes to the network: urlopen is replaced.
"""

from __future__ import annotations

import functools
import hashlib
import io
import logging
from pathlib import Path
from typing import Any

import pytest

from backend.app.core.config import Settings
from backend.app.services import hpo_service

_VENDORED_HPO = Path(__file__).resolve().parents[2] / "data" / "ref-data" / "hpo" / "hp.obo"
# What the floating hp.obo serves once HPO has published a later release.
_LATER_RELEASE = (
    b"format-version: 1.2\ndata-version: hp/releases/2026-09-01\n\n[Term]\nid: HP:0000001\nname: All\n"
)


@functools.cache
def _vendored() -> tuple[bytes, str, str]:
    """The vendored ontology's bytes, its release (the `data-version:` header) and its SHA-256."""
    payload = _VENDORED_HPO.read_bytes()
    header = payload.split(b"[Term]", 1)[0].decode("utf-8")
    release = hpo_service.parse_hpo_ontology_release_metadata_text(header).release_version
    assert release and release.startswith("hp/releases/"), release
    return payload, release, hashlib.sha256(payload).hexdigest()


def _default(field: str) -> Any:
    """What a deployment that does not set the field gets."""
    return Settings.model_fields[field].default


def _serve(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> list[str]:
    """Answer every download with ``payload``; the list collects the URLs requested."""
    requested: list[str] = []

    def fake_urlopen(request: Any, timeout: float = 60) -> io.BytesIO:
        requested.append(request.full_url)
        return io.BytesIO(payload)

    monkeypatch.setattr(hpo_service, "urlopen", fake_urlopen)
    return requested


class _Session:
    async def rollback(self) -> None:
        return None


def _start_without_an_ontology_file(monkeypatch: pytest.MonkeyPatch, target: Path) -> list[Path]:
    """An empty HPO terminology and no ontology file, so the startup downloads one to
    ``target``, checked against the digest a deployment that sets none gets. The list
    collects the files imported."""
    imported: list[Path] = []

    async def no_terms(_session: Any) -> int:
        return 0

    async def record_import(_session: Any, **kwargs: Any) -> dict[str, int]:
        imported.append(kwargs["path"])
        return {"terms": 1, "synonyms": 0, "edges": 0, "closure_rows": 1}

    monkeypatch.setattr(hpo_service, "_hpo_term_count", no_terms)
    monkeypatch.setattr(hpo_service, "hpo_ontology_candidate_paths", lambda _path=None: [target])
    monkeypatch.setattr(hpo_service, "import_hpo_ontology", record_import)
    monkeypatch.setattr(hpo_service.settings, "hpo_ontology_sha256", _default("hpo_ontology_sha256"))
    return imported


def test_the_pin_is_the_vendored_files_release_and_sha256() -> None:
    _payload, release, digest = _vendored()
    url = _default("hpo_ontology_url")
    # The release's own PURL, which never floats, unlike https://purl.obolibrary.org/obo/hp.obo.
    assert url == f"https://purl.obolibrary.org/obo/{release}/hp.obo"
    assert _default("hpo_ontology_sha256") == digest
    # The README that records the vendored file names the same release, digest and URL.
    readme = _VENDORED_HPO.with_name("README.md").read_text(encoding="utf-8")
    for value in (release, digest, url):
        assert value in readme, value


def test_a_blank_setting_is_the_pin() -> None:
    _payload, release, digest = _vendored()
    blank = Settings(_env_file=None, APP_ENV="test", HPO_ONTOLOGY_URL="  ", HPO_ONTOLOGY_SHA256="")
    assert blank.hpo_ontology_url == f"https://purl.obolibrary.org/obo/{release}/hp.obo"
    # Never "no check": a blank digest is the vendored file's.
    assert blank.hpo_ontology_sha256 == digest
    spelled = Settings(_env_file=None, APP_ENV="test", HPO_ONTOLOGY_SHA256=f" {digest.upper()} ")
    assert spelled.hpo_ontology_sha256 == digest


@pytest.mark.asyncio
async def test_without_an_ontology_file_the_startup_downloads_and_imports_the_pinned_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload, release, _digest = _vendored()
    target = tmp_path / "ref-data" / "hpo" / "hp.obo"
    requested = _serve(monkeypatch, payload)
    imported = _start_without_an_ontology_file(monkeypatch, target)

    result = await hpo_service.ensure_hpo_ontology_on_startup(
        _Session(), ontology_path=target, ontology_url=_default("hpo_ontology_url")
    )

    assert requested == [f"https://purl.obolibrary.org/obo/{release}/hp.obo"]
    assert target.read_bytes() == payload
    assert imported == [target]
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

    result = await hpo_service.ensure_hpo_ontology_on_startup(
        _Session(), ontology_path=target, ontology_url=_default("hpo_ontology_url")
    )

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
                _default("hpo_ontology_url"), [target], expected_sha256=not_a_digest
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
        hpo_service._download_hpo_ontology_file(_default("hpo_ontology_url"), [target])

    assert target.read_bytes() == kept
    assert [path.name for path in target.parent.iterdir()] == ["hp.obo"]
