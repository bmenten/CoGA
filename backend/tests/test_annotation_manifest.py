from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import logging
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


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False  # a failure inside still propagates, as with a real SAVEPOINT


class _ProvenanceResult:
    def __init__(self, *, row=None, scalar=None):
        self._row, self._scalar = row, scalar

    def mappings(self):
        return self

    def first(self):
        return self._row

    def scalar(self):
        return self._scalar


# The HPO ontology as an import writes it: every term carries the release it came from.
_HPO_RELEASE_ROW = {"release_version": "hp/releases/2026-06-06", "release_date": date(2026, 6, 6)}


class _ProvenanceSession:
    """Answers the four platform-module lookups, or fails them all."""

    def __init__(self, *, fail: bool, hpo_row: dict | None = _HPO_RELEASE_ROW) -> None:
        self.fail = fail
        self.hpo_row = hpo_row  # None: no HPO ontology is loaded
        self.savepoints = 0

    def begin_nested(self):
        self.savepoints += 1
        return _Savepoint()

    async def execute(self, statement, params=None):
        if self.fail:
            raise RuntimeError('relation "monarch_gene_disease" does not exist')
        if "FROM assemblies" in str(statement):
            return _ProvenanceResult(row={"assembly_name": "GRCh38", "version": "p14", "release_date": None})
        if "FROM reference_dataset_imports" in str(statement):
            return _ProvenanceResult(
                row={"source": "ucsc ncbiRefSeq", "performed_at": datetime(2026, 9, 1, tzinfo=timezone.utc)}
            )
        if "FROM hpo_term" in str(statement):
            return _ProvenanceResult(row=self.hpo_row)
        return _ProvenanceResult(scalar="2026-03-01")


_ASSEMBLY_ID = "00000000-0000-0000-0000-000000000001"


def test_platform_modules_read_the_reference_versions() -> None:
    session = _ProvenanceSession(fail=False)
    modules = asyncio.run(ams._platform_modules(session, _ASSEMBLY_ID))
    assert modules == {
        "assembly": {"version": "GRCh38", "detail": "p14"},
        # The source of the gene loci CoGA loaded, e.g. the UCSC table used when GENCODE
        # could not be fetched (#536).
        "gene_loci": {"version": "ucsc ncbiRefSeq", "detail": "imported 2026-09-01"},
        "monarch": {"version": "2026-03-01"},
        # The HPO release phenotype matching and HPO-driven ranking ran on, with its date.
        "hpo": {"version": "hp/releases/2026-06-06", "detail": "2026-06-06"},
    }


def test_the_hpo_release_is_read_for_a_family_without_an_assembly() -> None:
    # The ontology is not per assembly: like the Monarch release, it is always looked up.
    modules = asyncio.run(ams._platform_modules(_ProvenanceSession(fail=False), None))
    assert modules == {
        "monarch": {"version": "2026-03-01"},
        "hpo": {"version": "hp/releases/2026-06-06", "detail": "2026-06-06"},
    }


def test_an_hpo_release_without_a_date_has_no_detail() -> None:
    session = _ProvenanceSession(fail=False, hpo_row={"release_version": "v2026-06", "release_date": None})
    modules = asyncio.run(ams._platform_modules(session, None))
    assert modules["hpo"] == {"version": "v2026-06", "detail": None}


def test_an_hpo_ontology_loaded_without_a_release_is_recorded_as_such() -> None:
    # An ontology file without a data-version header imports with no release. Phenotype
    # matching still runs on it, so a signed record must say its release is unknown, not
    # leave HPO out as if none were loaded (#514).
    for missing in (None, "", "   "):
        session = _ProvenanceSession(fail=False, hpo_row={"release_version": missing, "release_date": None})
        modules = asyncio.run(ams._platform_modules(session, None))
        assert modules["hpo"] == {
            "version": ams.UNAVAILABLE_MODULE_VERSION,
            "detail": "release not recorded",
        }, missing


def test_no_hpo_module_without_a_loaded_ontology() -> None:
    # No terms, nothing phenotype matching could have used: as with Monarch, no module.
    modules = asyncio.run(ams._platform_modules(_ProvenanceSession(fail=False, hpo_row=None), _ASSEMBLY_ID))
    assert "hpo" not in modules
    assert set(modules) == {"assembly", "gene_loci", "monarch"}


def test_the_reference_module_list_names_every_platform_module() -> None:
    # A signed snapshot records this list, so a module missing from its `modules` reads as
    # "not loaded then" rather than "not looked up" (report_signout_service).
    modules = asyncio.run(ams._platform_modules(_ProvenanceSession(fail=True), _ASSEMBLY_ID))
    assert set(modules) == set(ams.REFERENCE_MODULE_KEYS)
    assert ams.REFERENCE_MODULE_KEYS == ("assembly", "gene_loci", "monarch", "hpo")


def test_a_failed_platform_lookup_is_recorded_not_dropped(caplog) -> None:
    # #514: these lookups run inside sign-out. A bare `except: pass` left the module
    # out of the frozen record without a trace — the pattern that once hid a total
    # failure to write the manifest.
    session = _ProvenanceSession(fail=True)
    with caplog.at_level(logging.WARNING, logger=ams.logger.name):
        modules = asyncio.run(ams._platform_modules(session, _ASSEMBLY_ID))

    marker = {"version": ams.UNAVAILABLE_MODULE_VERSION, "detail": "lookup failed"}
    assert modules == {"assembly": marker, "gene_loci": marker, "monarch": marker, "hpo": marker}
    # Each lookup is its own savepoint, so a failed statement cannot abort the
    # sign-out transaction around it.
    assert session.savepoints == 4
    assert "Reference-assembly provenance lookup failed" in caplog.text
    assert "Gene-locus provenance lookup failed" in caplog.text
    assert "Monarch-release provenance lookup failed" in caplog.text
    assert "HPO-release provenance lookup failed" in caplog.text
    # The manifest (and so the report footer and the snapshot) shows them as such.
    listed = {m["key"]: (m["version"], m["detail"]) for m in ams._module_list({}, modules)}
    assert listed == {
        "assembly": ("unavailable", "lookup failed"),
        "gene_loci": ("unavailable", "lookup failed"),
        "monarch": ("unavailable", "lookup failed"),
        "hpo": ("unavailable", "lookup failed"),
    }


def test_the_hpo_module_is_listed_under_its_label_on_the_reference_layer() -> None:
    listed = ams._module_list({}, {"hpo": {"version": "hp/releases/2026-06-06", "detail": "2026-06-06"}})
    assert listed == [
        {
            "key": "hpo",
            "label": "HPO",
            "version": "hp/releases/2026-06-06",
            "detail": "2026-06-06",
            "layer": "reference",
            "by_modality": None,
        }
    ]
    assert ams.module_label("hpo") == "HPO"
    assert ams.module_label("custom_tool") == "custom_tool"
