"""Backend hardening (#522): species auth, unsigned anchors, the HPO download, the CNV
knowledgebase subprocess environment, and link tokens in the access log."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.coga_logging import RedactQueryTokenFilter, redact_query_tokens
from backend.app.core.config import settings
from backend.app.services import hpo_service
from backend.app.services import integrity_anchor_service as anchors
from backend.app.services.clinical_cnv_kb_jobs import _build_script_env

_OBO = b"format-version: 1.2\ndata-version: hp/releases/2026-06-01\n\n[Term]\nid: HP:0000001\nname: All\n"


def test_the_species_list_requires_a_signed_in_user() -> None:
    from backend.app.main import app

    app.state.skip_startup_tasks = True
    with TestClient(app) as client:
        assert client.get("/api/species/").status_code == 401


def test_no_unsigned_anchor_is_written_outside_development(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "integrity_anchor_signing_key", "")

    class _NoDatabase:
        async def execute(self, *args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("the anchor must be refused before any database work")

    with pytest.raises(RuntimeError, match="UNSIGNED"):
        asyncio.run(anchors.create_integrity_anchor(_NoDatabase()))


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, payload: bytes) -> None:
    monkeypatch.setattr(hpo_service, "urlopen", lambda request, timeout=60: _Response(payload))


def test_the_hpo_ontology_is_only_fetched_over_https(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        hpo_service._download_hpo_ontology_file("http://purl.obolibrary.org/obo/hp.obo", [tmp_path / "hp.obo"])
    assert hpo_service.DEFAULT_HPO_ONTOLOGY_URL.startswith("https://")
    assert settings.hpo_ontology_url.startswith("https://")


def test_the_hpo_download_is_bounded_checked_and_atomic(monkeypatch, tmp_path) -> None:
    target = tmp_path / "hpo" / "hp.obo"
    url = "https://purl.obolibrary.org/obo/hp.obo"

    _serve(monkeypatch, b"x" * 2048)
    with pytest.raises(RuntimeError, match="exceeds"):
        hpo_service._download_hpo_ontology_file(url, [target], max_bytes=1024)

    _serve(monkeypatch, b"<html>502 Bad Gateway</html>")
    with pytest.raises(RuntimeError, match="not an OBO"):
        hpo_service._download_hpo_ontology_file(url, [target])

    _serve(monkeypatch, _OBO)
    with pytest.raises(RuntimeError, match="SHA-256"):
        hpo_service._download_hpo_ontology_file(url, [target], expected_sha256="0" * 64)
    assert not target.exists(), "a rejected download leaves no file behind"

    written = hpo_service._download_hpo_ontology_file(
        url, [target], expected_sha256=hashlib.sha256(_OBO).hexdigest()
    )
    assert written == target and target.read_bytes() == _OBO
    assert not list(Path(target.parent).glob("*.partial"))


def test_the_knowledgebase_script_gets_only_what_it_needs() -> None:
    env = _build_script_env(
        {
            "PATH": "/usr/bin",
            "HOME": "/home/app",
            "OMIM_API_KEY": "omim",
            "HTTPS_PROXY": "http://proxy:3128",
            "SECRET_KEY": "session-signing-key",
            "POSTGRES_PASSWORD": "pg",
            "CLICKHOUSE_PASSWORD": "ch",
            "INTEGRITY_ANCHOR_SIGNING_KEY": "anchor",
            "GOOGLE_APPLICATION_CREDENTIALS": "/secrets/sa.json",
        }
    )
    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/home/app",
        "OMIM_API_KEY": "omim",
        "HTTPS_PROXY": "http://proxy:3128",
    }


def test_link_tokens_are_masked_in_the_access_log() -> None:
    line = "/api/families/F1/qc-report/S1?token=link-token-value&x=1"
    assert redact_query_tokens(line) == "/api/families/F1/qc-report/S1?token=***&x=1"

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", line, "1.1", 200),
        None,
    )
    RedactQueryTokenFilter().filter(record)
    assert "link-token-value" not in record.getMessage()
    assert "token=***" in record.getMessage()
