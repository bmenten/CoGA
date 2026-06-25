from __future__ import annotations

import asyncio
import types

from backend.app.services import annotation_manifest_service as ams


def test_as_module_accepts_string_or_dict() -> None:
    assert ams._as_module("110") == {"version": "110"}
    assert ams._as_module({"version": "110", "cache": "x"}) == {"version": "110", "cache": "x"}
    assert ams._as_module(None) == {}


def test_module_list_merges_pipeline_over_platform_in_canonical_order() -> None:
    platform = {
        "assembly": {"version": "GRCh38", "detail": "2013-12-01"},
        "monarch": {"version": "2026-06"},
    }
    pipeline = {
        "clinvar": "2026-05",
        "vep": {"version": "110", "cache": "110_GRCh38"},
        "monarch": "override",  # pipeline value wins for the same key
    }
    out = ams._module_list(pipeline, platform)
    by_key = {m["key"]: m for m in out}

    # Canonical display order (assembly, vep, clinvar, …, monarch).
    assert [m["key"] for m in out][:3] == ["assembly", "vep", "clinvar"]
    assert by_key["assembly"]["layer"] == "reference"
    assert by_key["assembly"]["version"] == "GRCh38" and by_key["assembly"]["detail"] == "2013-12-01"
    assert by_key["vep"]["layer"] == "pipeline" and by_key["vep"]["version"] == "110"
    assert by_key["vep"]["detail"] == "110_GRCh38"  # cache surfaced as detail
    assert by_key["clinvar"]["version"] == "2026-05"
    assert by_key["monarch"]["layer"] == "pipeline" and by_key["monarch"]["version"] == "override"


def test_module_list_includes_unknown_keys_with_titled_label() -> None:
    out = ams._module_list({"custom_tool": "9"}, {})
    module = next(m for m in out if m["key"] == "custom_tool")
    assert module["label"] == "Custom Tool" and module["version"] == "9" and module["layer"] == "pipeline"


def test_get_family_manifest_falls_back_to_family_metadata(monkeypatch) -> None:
    # No explicit family_annotation_manifest row -> read the import-captured manifest
    # from family.metadata.annotation_manifest.
    async def _context(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(
            family_uuid="u1", family_id="FAM1", assembly_id=None, assembly_name="GRCh38"
        )

    async def _family(session, family_id, user):
        return types.SimpleNamespace(metadata={"annotation_manifest": {"clinvar": "2026-05"}})

    async def _no_row(session, family_uuid):
        return None

    async def _no_platform(session, assembly_id):
        return {}

    monkeypatch.setattr(ams, "build_family_metadata_context", _context)
    monkeypatch.setattr(ams, "get_family_record", _family)
    monkeypatch.setattr(ams, "_family_manifest_row", _no_row)
    monkeypatch.setattr(ams, "_platform_modules", _no_platform)

    out = asyncio.run(
        ams.get_family_annotation_manifest(session=None, family_id="FAM1", user=None)
    )
    assert out["family_id"] == "FAM1" and out["assembly"] == "GRCh38"
    assert out["source"] == "manifest"
    assert any(m["key"] == "clinvar" and m["version"] == "2026-05" for m in out["modules"])
