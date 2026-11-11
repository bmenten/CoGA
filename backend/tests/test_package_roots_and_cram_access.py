"""Where a family package may be imported from, and who may read its aligned reads.

A package is read only from a folder below FAMILY_IMPORT_ROOTS. The CRAM/BAM endpoints check
the caller's access to the family, and that the sample is one of its members, before any file
is served (REQ-SEC-007, risk H11); the alignment manifest hands out backend-relative URLs.
"""

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


def test_local_package_path_allowed_under_configured_root(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    target = tmp_path / "F1"
    assert family_package_source._ensure_authorized_package_path(target) == target.resolve()


def test_local_package_path_traversal_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])
    with pytest.raises(HTTPException) as exc:
        family_package_source._ensure_authorized_package_path(tmp_path / ".." / "coga-outside")
    assert exc.value.status_code == 403


def test_local_package_path_open_only_when_no_root_is_configured(monkeypatch, tmp_path):
    # Development's empty FAMILY_IMPORT_ROOTS admits any folder; a configured root admits only
    # what lies below it.
    monkeypatch.setattr(settings, "family_import_roots", [])
    assert family_package_source._ensure_authorized_package_path(tmp_path / "F1") == (tmp_path / "F1").resolve()
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path / "imports")])
    with pytest.raises(HTTPException) as exc:
        family_package_source._ensure_authorized_package_path(Path("/etc/coga-attacker"))
    assert exc.value.status_code == 403


def test_cram_manifest_relative_urls(monkeypatch):
    monkeypatch.setattr(
        cram, "_alignment_path", lambda fam, sample, ext, suffix="": Path(f"{sample}.{ext}{suffix}") if ext == "cram" else None
    )
    entry = cram._resolve_alignment_manifest_entry("F1", "S1")
    assert entry is not None
    assert entry.url == "/cram/F1/S1.cram"
    assert entry.index_url == "/cram/F1/S1.cram.crai"


# --- PHI alignment access control (REQ-SEC-007, risk H11) -------------------
# A CRAM/BAM must only be served after the family+sample access check passes.
# get_family_record enforces project scoping; the sample must belong to the family.
# The check must run BEFORE any bytes are served.


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
    # The access failure must short-circuit before the file is served.
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
