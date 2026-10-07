import asyncio
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import cram
from app.core.config import settings
from app.services import family_package_source


def _family_with_samples(*sample_ids):
    return types.SimpleNamespace(
        members=[types.SimpleNamespace(sample_id=s) for s in sample_ids]
    )


def test_s3_source_authorization(monkeypatch):
    monkeypatch.setattr(
        settings, "family_import_roots", ["s3://bucket/families", "/data/families"]
    )
    assert family_package_source._ensure_authorized_s3_source("s3://bucket/families/F1") == "s3://bucket/families/F1"
    assert family_package_source._authorized_s3_roots() == ["s3://bucket/families"]
    with pytest.raises(HTTPException):
        family_package_source._ensure_authorized_s3_source("s3://other-bucket/families/F1")


def test_s3_source_rejected_without_configured_root(monkeypatch):
    monkeypatch.setattr(settings, "family_import_roots", ["/data/families"])
    with pytest.raises(HTTPException):
        family_package_source._ensure_authorized_s3_source("s3://bucket/families/F1")


def test_local_package_path_rejected_when_no_local_root_configured(monkeypatch):
    # S3-only config: no local root is configured. An out-of-staging local path must be
    # rejected — the previous fail-open returned any admin-supplied path unchecked,
    # giving an out-of-allowlist file read/write primitive.
    monkeypatch.setattr(settings, "family_import_roots", ["s3://bucket/families"])
    assert family_package_source._authorized_local_roots() == []
    with pytest.raises(HTTPException) as exc:
        family_package_source._ensure_authorized_package_path(Path("/etc/coga-attacker"))
    assert exc.value.status_code == 403


def test_local_package_path_allowed_under_configured_root(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    target = tmp_path / "F1"
    assert family_package_source._ensure_authorized_package_path(target) == target.resolve()


def test_local_package_path_traversal_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    with pytest.raises(HTTPException) as exc:
        family_package_source._ensure_authorized_package_path(tmp_path / ".." / "coga-outside")
    assert exc.value.status_code == 403


def test_staged_package_source_local_is_passthrough(tmp_path):
    with family_package_source.staged_package_source(str(tmp_path)) as staged:
        assert staged.root == str(tmp_path)
        assert staged.source_uri is None
        assert staged.remote_only_files == frozenset()


def test_cram_manifest_uses_presigned_urls_in_s3_mode(monkeypatch):
    monkeypatch.setattr(cram, "storage_is_remote", lambda: True)
    monkeypatch.setattr(cram, "object_key", lambda *parts: "/".join(parts))
    # Pretend the CRAM + index objects exist (but not the BAM ones).
    monkeypatch.setattr(cram, "object_exists", lambda key: ".cram" in key)
    monkeypatch.setattr(
        cram, "presigned_get_url", lambda key, filename=None: f"https://s3.example/{key}?sig=abc"
    )

    entry = cram._resolve_alignment_manifest_entry("F1", "S1")
    assert entry is not None
    assert entry.format == "cram"
    assert entry.url == "https://s3.example/F1/S1.cram?sig=abc"
    assert entry.index_url == "https://s3.example/F1/S1.cram.crai?sig=abc"


def test_cram_manifest_relative_urls_in_local_mode(monkeypatch):
    monkeypatch.setattr(cram, "storage_is_remote", lambda: False)
    monkeypatch.setattr(cram, "_alignment_exists", lambda fam, sample, ext, suffix="": ext == "cram")
    entry = cram._resolve_alignment_manifest_entry("F1", "S1")
    assert entry is not None
    assert entry.url == "/cram/F1/S1.cram"
    assert entry.index_url == "/cram/F1/S1.cram.crai"


# --- PHI alignment access control (REQ-SEC-007, risk H11) -------------------
# A CRAM/BAM (or its presigned URL) must only be served after the family+sample
# access check passes. get_family_record enforces project scoping; the sample
# must belong to the family. The check must run BEFORE any bytes/URL are issued.


def test_alignment_denied_when_family_not_accessible_before_serving(monkeypatch):
    # The checkpoint's answer for a family outside the user's projects (REQ-SEC-001).
    async def _deny(session, family_id, user):
        raise HTTPException(status_code=404, detail="Family not found")

    served = {"called": False}

    def _serve_spy(*args, **kwargs):
        served["called"] = True
        return "should-not-be-reached"

    monkeypatch.setattr(cram, "get_family_record", _deny)
    monkeypatch.setattr(cram, "_serve_alignment", _serve_spy)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(cram.get_cram("F1", "S1", session=None, user=object()))

    assert exc.value.status_code == 404
    # The access failure must short-circuit before the file/presigned URL is issued.
    assert served["called"] is False


def test_alignment_denied_for_sample_outside_the_family(monkeypatch):
    async def _ok(session, family_id, user):
        return _family_with_samples("S1", "S2")

    monkeypatch.setattr(cram, "get_family_record", _ok)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            cram._ensure_accessible_alignment_sample(None, "F1", "S_FOREIGN", object())
        )

    assert exc.value.status_code == 404


def test_alignment_allowed_for_a_member_sample(monkeypatch):
    async def _ok(session, family_id, user):
        return _family_with_samples("S1", "S2")

    monkeypatch.setattr(cram, "get_family_record", _ok)

    # A sample that belongs to the family passes the gate without raising.
    assert (
        asyncio.run(
            cram._ensure_accessible_alignment_sample(None, "F1", "S1", object())
        )
        is None
    )
