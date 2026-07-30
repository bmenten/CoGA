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


def test_module_list_includes_unknown_keys_verbatim() -> None:
    # Unknown keys used to be title-cased, which renamed the tools it was recording
    # ("nf-core/lrsvar" -> "Nf-Core/Lrsvar", "xz" -> "Xz"). A provenance record must say
    # exactly what produced a result, so an unrecognised key is shown as-is.
    out = ams._module_list({"custom_tool": "9"}, {})
    module = next(m for m in out if m["key"] == "custom_tool")
    assert module["label"] == "custom_tool" and module["version"] == "9" and module["layer"] == "pipeline"


def test_refresh_modules_records_and_accumulates_per_modality() -> None:
    # Issue #294: each import records its version under by_modality[modality], and a
    # later modality citing a *different* release of the same database accumulates
    # rather than clobbering — the divergence is preserved.
    snv = ams._refresh_modules({}, {"gencode": {"version": "49"}}, modality="snv")
    assert snv["gencode"]["by_modality"] == {"snv": "49"}
    both = ams._refresh_modules(snv, {"gencode": {"version": "45"}}, modality="sv")
    assert both["gencode"]["by_modality"] == {"snv": "49", "sv": "45"}
    assert both["gencode"]["version"] == "45"  # flat representative = latest write


def test_module_list_exposes_by_modality() -> None:
    out = ams._module_list(
        {"gencode": {"version": "45", "by_modality": {"snv": "49", "sv": "45"}}}, {}
    )
    assert next(m for m in out if m["key"] == "gencode")["by_modality"] == {"snv": "49", "sv": "45"}
    # Platform/reference modules carry no per-modality breakdown.
    platform = ams._module_list({}, {"assembly": {"version": "GRCh38"}})
    assert next(m for m in platform if m["key"] == "assembly")["by_modality"] is None


def test_refresh_modules_new_version_wins_and_preserves_untouched() -> None:
    # Re-import: freshly parsed versions overwrite stale ones, while modules the
    # new input does not mention (and untouched fields) are preserved.
    current = {"vep": {"version": "110", "cache": "110_GRCh38"}, "clinvar": {"version": "202301"}}
    incoming = {"vep": {"version": "112"}, "sniffles": {"version": "2.2"}}
    out = ams._refresh_modules(current, incoming)
    assert out["vep"]["version"] == "112"  # newly parsed wins
    assert out["vep"]["cache"] == "110_GRCh38"  # untouched field preserved
    assert out["clinvar"]["version"] == "202301"  # untouched module preserved
    assert out["sniffles"]["version"] == "2.2"  # new module added


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


def test_unknown_module_labels_are_not_renamed_by_title_casing() -> None:
    """A traceability record must not rename the tool it records.

    Title-casing an unrecognised key turned "nf-core/lrsvar" into "Nf-Core/Lrsvar" and
    "xz" into "Xz". Tool names carry their own casing, so only keys that look like plain
    words are prettified.
    """
    from backend.app.services.annotation_manifest_service import _fallback_module_label

    for key in ("nf-core/lrsvar", "minimap2", "perl-math-cdf", "GATK4", "xz", "samtools"):
        assert _fallback_module_label(key) == key


def test_known_modules_keep_their_curated_label() -> None:
    from backend.app.services.annotation_manifest_service import _MODULE_LABELS

    # The tools this pipeline reports all have a curated label, so none of them falls
    # through to the verbatim path with awkward casing.
    assert _MODULE_LABELS["hificnv"] == "HiFiCNV"
    assert _MODULE_LABELS["glnexus"] == "GLnexus"
    assert _MODULE_LABELS["ensemblvep"] == "Ensembl VEP"
    assert _MODULE_LABELS["nf-core/lrsvar"] == "nf-core/lrsvar"
