from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import json
import logging
import types

from fastapi import HTTPException
import pytest

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
    # A provenance record must say exactly what produced a result, so an unrecognised key is
    # shown as-is, never title-cased ("nf-core/lrsvar" -> "Nf-Core/Lrsvar", "xz" -> "Xz").
    out = ams._module_list({"custom_tool": "9"}, {})
    module = next(m for m in out if m["key"] == "custom_tool")
    assert module["label"] == "custom_tool" and module["version"] == "9" and module["layer"] == "pipeline"


def test_refresh_modules_records_and_accumulates_per_modality() -> None:
    # Each import records its version under by_modality[modality], and a
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


def test_a_clinvar_module_replaces_the_stored_release_and_source_together() -> None:
    # An earlier import's VEP-cache release must not stay beside a ClinVar file named without
    # a date, where it read as that file's release; nor a file's name beside a later cache
    # release. Other modules still merge field by field.
    current = {"clinvar": {"version": "202504", "by_modality": {"snv": "202504", "sv": "20250101"}}}
    undated = ams._refresh_modules(current, {"clinvar": {"detail": "ClinVar file clinvar.vcf.gz"}}, modality="snv")
    assert undated["clinvar"] == {"detail": "ClinVar file clinvar.vcf.gz", "by_modality": {"sv": "20250101"}}

    dated = {"clinvar": {"version": "20261101", "detail": "ClinVar file clinvar_20261101.vcf.gz"}}
    stored = ams._refresh_modules(current, dated, modality="snv")
    cache_again = ams._refresh_modules(stored, {"clinvar": {"version": "202510"}}, modality="snv")
    assert cache_again["clinvar"] == {"version": "202510", "by_modality": {"snv": "202510", "sv": "20250101"}}


def test_a_reimported_header_with_only_a_command_line_keeps_the_recorded_version() -> None:
    # A re-import whose TRGT header has a command line but no version line states no
    # version, so the recorded one stays: the command's first word ("trgt") taken for a
    # version would replace it, and the next sign-out would freeze it.
    from backend.app.services.vcf_header_provenance import extract_header_provenance

    current = {"trgt": {"version": "0.7.0", "by_modality": {"repeats": "0.7.0"}}}
    header = ["##fileformat=VCFv4.2", "##trgtCommand=trgt genotype --genome ref.fa --karyotype XX"]
    incoming = extract_header_provenance(header, modality="repeats").as_modules()
    out = ams._refresh_modules(current, incoming, modality="repeats")
    assert out["trgt"] == {"version": "0.7.0", "by_modality": {"repeats": "0.7.0"}}


def test_a_shapeit5_header_adds_no_version_beside_the_pipeline_record() -> None:
    # A PGT package's pipeline record names SHAPEIT5 5.1.1, and its phased VCF says
    # ``##source=SHAPEIT5 phase_common 1.1``: the 5 is part of the name and the 1.1 is
    # phase_common's own number. The header tags the caller and states no version, so the
    # manifest keeps one SHAPEIT5 at 5.1.1, not a second "shapeit" at version "5".
    from backend.app.services.vcf_header_provenance import extract_header_provenance

    current = {"shapeit5": {"version": "5.1.1", "by_modality": {"pipeline": "5.1.1"}}}
    header = ["##fileformat=VCFv4.2", "##source=SHAPEIT5 phase_common 1.1"]
    incoming = extract_header_provenance(header, modality="snv").as_modules()
    out = ams._refresh_modules(current, incoming, modality="snv")
    assert out == {
        "shapeit5": {"version": "5.1.1", "by_modality": {"pipeline": "5.1.1"}, "detail": "snv caller"}
    }


def _patch_manifest_read(monkeypatch, *, rows, metadata=None) -> None:
    async def _context(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(
            family_uuid="u1", family_id="FAM1", assembly_id=None, assembly_name="GRCh38"
        )

    async def _rows(session, family_uuid):
        return list(rows)

    async def _metadata(session, family_uuid):
        return dict(metadata or {})

    async def _no_platform(session, assembly_id):
        return {}

    monkeypatch.setattr(ams, "build_family_metadata_context", _context)
    monkeypatch.setattr(ams, "_family_manifest_rows", _rows)
    monkeypatch.setattr(ams, "_family_metadata", _metadata)
    monkeypatch.setattr(ams, "_platform_modules", _no_platform)


def test_a_family_without_a_recorded_manifest_states_no_pipeline_layer(monkeypatch) -> None:
    # The pipeline layer is the family_annotation_manifest rows and nothing else.
    _patch_manifest_read(monkeypatch, rows=[])

    out = asyncio.run(
        ams.get_family_annotation_manifest(session=None, family_id="FAM1", user=None)
    )
    assert out["family_id"] == "FAM1" and out["assembly"] == "GRCh38"
    # A family that holds no analysis has one manifest, of no analysis.
    [manifest] = out["analyses"]
    assert manifest == {
        "analysis_type": None,
        "source": None,
        "recorded_at": None,
        "recorded_by": None,
        "modules": [],
    }


@pytest.mark.parametrize(
    ("stored", "source"),
    [
        # A driver without the jsonb codec hands the recorded modules back as JSON text.
        (json.dumps({"clinvar": {"version": "2026-09"}}), "pipeline"),
        # Text that is no JSON object holds nothing recorded: the family states no pipeline layer.
        ("{not json", None),
        (json.dumps(["clinvar"]), None),
        ("  ", None),
    ],
    ids=["json-text", "not-json", "json-not-an-object", "blank"],
)
def test_a_recorded_manifest_read_back_as_text_is_read_as_the_object_it_is(monkeypatch, stored, source) -> None:
    _patch_manifest_read(
        monkeypatch,
        rows=[
            {
                "analysis_type": "unspecified",
                "modules": stored,
                "source": "pipeline",
                "recorded_at": None,
                "recorded_by": "importer",
            }
        ],
    )

    out = asyncio.run(ams.get_family_annotation_manifest(session=None, family_id="FAM1", user=None))

    [manifest] = out["analyses"]
    assert manifest["source"] == source
    versions = {module["key"]: module["version"] for module in manifest["modules"]}
    assert versions == ({"clinvar": "2026-09"} if source == "pipeline" else {})


# --- one manifest per analysis --------------------------------------------------------------
# A family sequenced short-read and then long-read keeps each analysis's versions apart: the
# long-read import never replaces the short-read analysis's VEP, ClinVar or caller, and the
# short-read analysis's record never cites the long-read ones.


def _row(analysis: str, modules: dict, source: str = "vcf_header", recorded_by: str | None = None) -> dict:
    return {
        "analysis_type": analysis,
        "modules": modules,
        "source": source,
        "recorded_at": None,
        "recorded_by": recorded_by or f"import of {analysis}",
    }


_SHORT_READ = _row(
    "short_read_wgs",
    {
        "vep": {"version": "110", "by_modality": {"snv": "110"}},
        "clinvar": {"version": "20240101", "by_modality": {"snv": "20240101"}},
        "gatk": {"version": "4.5.0.0", "detail": "snv caller", "by_modality": {"snv": "4.5.0.0"}},
    },
)
_LONG_READ = _row(
    "long_read_wgs",
    {
        "vep": {"version": "112", "by_modality": {"snv": "112"}},
        "deepvariant": {"version": "1.6.1", "detail": "snv caller", "by_modality": {"snv": "1.6.1"}},
        "trgt": {"version": "1.2.0", "by_modality": {"repeats": "1.2.0"}},
    },
)
_HELD = frozenset({"long_read_wgs", "short_read_wgs"})


def _versions(manifest: dict) -> dict:
    return {module["key"]: module["version"] for module in manifest["modules"]}


def test_each_analysis_states_its_own_versions_and_not_the_others() -> None:
    rows = [_LONG_READ, _SHORT_READ]
    short_read = ams.analysis_manifest(rows, analysis="short_read_wgs", platform_modules={})
    long_read = ams.analysis_manifest(rows, analysis="long_read_wgs", platform_modules={})
    assert _versions(short_read) == {"vep": "110", "clinvar": "20240101", "gatk": "4.5.0.0"}
    assert _versions(long_read) == {"vep": "112", "deepvariant": "1.6.1", "trgt": "1.2.0"}
    assert (short_read["analysis_type"], short_read["recorded_by"]) == ("short_read_wgs", "import of short_read_wgs")
    # The platform's reference layer is every analysis's.
    platform = {"assembly": {"version": "GRCh38"}}
    for analysis in _HELD:
        manifest = ams.analysis_manifest(rows, analysis=analysis, platform_modules=platform)
        assert _versions(manifest)["assembly"] == "GRCh38"


def test_an_uploads_versions_are_every_analysis_s_beneath_its_own() -> None:
    # A single-file SV upload's versions (the row of no analysis): every analysis states them,
    # its own release of a database winning, the versions per modality joined.
    upload = _row(
        "unspecified",
        {
            "clinvar": {"version": "20230601", "by_modality": {"sv": "20230601"}},
            "sniffles": {"version": "2.2", "detail": "sv caller", "by_modality": {"sv": "2.2"}},
        },
    )
    rows = [_LONG_READ, _SHORT_READ, upload]
    short_read = ams.analysis_manifest(rows, analysis="short_read_wgs", platform_modules={})
    by_key = {module["key"]: module for module in short_read["modules"]}
    assert by_key["clinvar"]["version"] == "20240101"
    assert by_key["clinvar"]["by_modality"] == {"sv": "20230601", "snv": "20240101"}
    assert by_key["sniffles"]["version"] == "2.2"
    assert "deepvariant" not in by_key
    # Where the versions came from: the analysis's own row's.
    assert short_read["recorded_by"] == "import of short_read_wgs"


def test_an_administrators_replacement_is_the_analysis_s_whole_manifest() -> None:
    upload = _row("unspecified", {"sniffles": {"version": "2.2"}})
    manual = _row("short_read_wgs", {"vep": {"version": "111"}}, source="manual", recorded_by="admin@example.org")
    rows = [_LONG_READ, manual, upload]
    short_read = ams.analysis_manifest(rows, analysis="short_read_wgs", platform_modules={})
    assert _versions(short_read) == {"vep": "111"}
    assert short_read["source"] == "manual"
    # Another analysis still states the upload's beneath its own.
    long_read = ams.analysis_manifest(rows, analysis="long_read_wgs", platform_modules={})
    assert _versions(long_read)["sniffles"] == "2.2"


def test_a_family_of_no_analysis_states_the_rows_of_none() -> None:
    upload = _row("unspecified", {"vep": {"version": "110"}})
    # The row of an analysis the family no longer holds (its import put back, its data gone)
    # states nothing: neither for a family of no analysis nor beneath another analysis's own.
    stale = _row("long_read_wgs", {"trgt": {"version": "1.2.0"}})
    manifest = ams.analysis_manifest([stale, upload], analysis=None, platform_modules={})
    assert manifest["analysis_type"] is None
    assert _versions(manifest) == {"vep": "110"}
    beneath = ams.analysis_manifest(
        [stale, _SHORT_READ], analysis="short_read_wgs", platform_modules={}
    )
    assert "trgt" not in _versions(beneath)
    # A family of one analysis: its own row over the rows of none.
    single = ams.analysis_manifest(
        [_SHORT_READ, upload], analysis="short_read_wgs", platform_modules={}
    )
    assert _versions(single) == {"vep": "110", "clinvar": "20240101", "gatk": "4.5.0.0"}


def test_the_family_manifest_holds_one_manifest_per_analysis_in_order(monkeypatch) -> None:
    _patch_manifest_read(
        monkeypatch,
        rows=[_LONG_READ, _SHORT_READ],
        metadata={"analysis_types": ["short_read_wgs", "long_read_wgs"]},
    )
    out = asyncio.run(ams.get_family_annotation_manifest(session=None, family_id="FAM1", user=None))
    assert [manifest["analysis_type"] for manifest in out["analyses"]] == ["long_read_wgs", "short_read_wgs"]
    assert _versions(out["analyses"][1]) == {"vep": "110", "clinvar": "20240101", "gatk": "4.5.0.0"}


def test_a_sign_out_freezes_the_manifest_of_the_analysis_it_is_of(monkeypatch) -> None:
    _patch_manifest_read(
        monkeypatch,
        rows=[_LONG_READ, _SHORT_READ],
        metadata={"analysis_types": ["long_read_wgs", "short_read_wgs"]},
    )
    context = types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_id=None, assembly_name="GRCh38")
    out = asyncio.run(ams.analysis_annotation_manifest(None, context, analysis="short_read_wgs"))  # type: ignore[arg-type]
    assert out["assembly"] == "GRCh38" and out["analysis_type"] == "short_read_wgs"
    assert _versions(out) == {"vep": "110", "clinvar": "20240101", "gatk": "4.5.0.0"}


def test_a_calls_manifest_is_that_of_the_analysis_whose_package_stored_it(monkeypatch) -> None:
    def _record(datasets, snv_callset=None):
        from backend.app.services.family_package_records import package_sources

        return {"datasets": {name: {} for name in datasets}, "sources": package_sources(datasets, snv_callset)}

    _patch_manifest_read(
        monkeypatch,
        rows=[_LONG_READ, _SHORT_READ, _row("unspecified", {"sniffles": {"version": "2.2"}})],
        metadata={
            "analysis_types": ["long_read_wgs", "short_read_wgs"],
            "package_imports": {
                "long_read_wgs": _record(["snv", "sv_needlr"], "deepvariant"),
                "short_read_wgs": _record(["snv"], "haplotypecaller"),
            },
        },
    )
    context = types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_id=None, assembly_name="GRCh38")

    def _of(stored_under):
        return _versions(
            asyncio.run(
                ams.call_annotation_manifest(None, context, kind="structural_variants", stored_under=stored_under)  # type: ignore[arg-type]
            )
        )

    assert _of("needlr") == {"vep": "112", "deepvariant": "1.6.1", "trgt": "1.2.0", "sniffles": "2.2"}
    # An uploaded SV's (a source no package lists): the versions of no analysis.
    assert _of("web") == {"sniffles": "2.2"}


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


# The HPO ontology as an import writes it: every term carries the release it came from and
# the SHA-256 of its file.
_HPO_SHA256 = "ab" * 32
_HPO_RELEASE_ROW = {
    "release_version": "hp/releases/2026-06-06",
    "release_date": date(2026, 6, 6),
    "release_sha256": _HPO_SHA256,
}


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
        # could not be fetched.
        "gene_loci": {"version": "ucsc ncbiRefSeq", "detail": "imported 2026-09-01"},
        "monarch": {"version": "2026-03-01"},
        # The HPO release phenotype matching and HPO-driven ranking ran on, with its date and
        # the SHA-256 of its file.
        "hpo": {"version": "hp/releases/2026-06-06", "detail": f"2026-06-06; SHA-256 {_HPO_SHA256}"},
    }


def test_the_hpo_release_is_read_for_a_family_without_an_assembly() -> None:
    # The ontology is not per assembly: like the Monarch release, it is always looked up.
    modules = asyncio.run(ams._platform_modules(_ProvenanceSession(fail=False), None))
    assert modules == {
        "monarch": {"version": "2026-03-01"},
        "hpo": {"version": "hp/releases/2026-06-06", "detail": f"2026-06-06; SHA-256 {_HPO_SHA256}"},
    }


def test_an_hpo_release_without_a_date_names_its_files_digest_alone() -> None:
    session = _ProvenanceSession(
        fail=False, hpo_row={"release_version": "v2026-06", "release_date": None, "release_sha256": _HPO_SHA256}
    )
    modules = asyncio.run(ams._platform_modules(session, None))
    assert modules["hpo"] == {"version": "v2026-06", "detail": f"SHA-256 {_HPO_SHA256}"}


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
    # These lookups run inside sign-out. A bare `except: pass` would leave the module
    # out of the frozen record without a trace, and could hide a total failure to write the
    # manifest.
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


# --- replacing a family's manifest: admin-only, and it leaves a clinical audit event ---
# Every later sign-out freezes this manifest into the signed report, and the row is
# overwritten in place, so the replacement itself must be on the family's chain.


class _ManifestWriteSession:
    """Records the statements a manifest replacement runs, and its commits."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.statements.append(" ".join(str(statement).split()))

    async def commit(self) -> None:
        self.commits += 1


def _manifest_user(role: str):
    return types.SimpleNamespace(
        id="00000000-0000-0000-0000-0000000000aa",
        username=f"{role}-user",
        email=f"{role}@example.org",
        role=role,
    )


def _patch_replacement(monkeypatch, *, stored_row=None, rows=None, metadata=None):
    session = _ManifestWriteSession()
    events: list[dict] = []

    async def _context(session_, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(
            family_uuid="u1", family_id="FAM1", assembly_id=None, assembly_name="GRCh38"
        )

    async def _rows(session_, family_uuid):
        if rows is not None:
            return list(rows)
        return [{"analysis_type": "unspecified", **stored_row}] if stored_row else []

    async def _metadata(session_, family_uuid):
        return dict(metadata or {})

    async def _event(session_, **kwargs):
        # Snapshot what the transaction had done when the event was written.
        events.append({**kwargs, "_statements": list(session.statements), "_commits": session.commits})

    async def _manifest(session_, *, family_id, user, project_id=None):
        return {"family_id": "FAM1", "modules": []}

    monkeypatch.setattr(ams, "build_family_metadata_context", _context)
    monkeypatch.setattr(ams, "_family_manifest_rows", _rows)
    monkeypatch.setattr(ams, "_family_metadata", _metadata)
    monkeypatch.setattr(ams, "record_clinical_event", _event, raising=False)
    monkeypatch.setattr(ams, "get_family_annotation_manifest", _manifest)
    return session, events


def test_a_viewer_cannot_replace_the_manifest_even_through_the_service(monkeypatch) -> None:
    # Defence in depth behind the route's admin dependency: the service is the one place
    # that writes curated provenance.
    session, events = _patch_replacement(monkeypatch)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            ams.set_family_annotation_manifest(
                session, family_id="FAM1", user=_manifest_user("viewer"), modules={"vep": "112"}
            )
        )
    assert excinfo.value.status_code == 403
    assert session.statements == [] and session.commits == 0 and events == []


@pytest.mark.parametrize("role", ["admin"])
def test_replacing_the_manifest_records_the_prior_and_new_modules(monkeypatch, role) -> None:
    stored = {
        "modules": {"vep": {"version": "110", "cache": "110_GRCh38"}, "snpeff": "5.1"},
        "source": "vcf_header",
        "recorded_by": "import (vcf_header)",
        "recorded_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }
    session, events = _patch_replacement(monkeypatch, stored_row=stored)
    user = _manifest_user(role)
    new_modules = {"vep": {"version": "112"}, "clinvar": "2026-09"}

    asyncio.run(
        ams.set_family_annotation_manifest(
            session, family_id="FAM1", user=user, modules=new_modules
        )
    )

    assert len(events) == 1
    event = events[0]
    assert event["action"] == "annotation_manifest"
    assert (event["family_uuid"], event["family_identifier"], event["variant_id"]) == ("u1", "FAM1", None)
    assert (event["actor"], event["actor_id"]) == (user.username, user.id)
    assert event["before"] == {
        "analysis_type": None,
        "source": "vcf_header",
        "recorded_by": "import (vcf_header)",
        "recorded_at": "2026-09-01T00:00:00+00:00",
        "modules": {"vep": {"version": "110", "cache": "110_GRCh38"}, "snpeff": {"version": "5.1"}},
    }
    assert event["after"] == {"analysis_type": None, "source": "manual", "modules": new_modules}
    assert event["summary"] == (
        "Annotation manifest replaced (was vcf_header, now manual): "
        "VEP 110 → 112; SnpEff removed; ClinVar added"
    )
    # One transaction: the event is written after the overwrite and before the commit,
    # so neither can persist without the other.
    assert any("INSERT INTO family_annotation_manifest" in s for s in event["_statements"])
    assert event["_commits"] == 0
    assert session.commits == 1
    # Concurrent replacements are serialised, so each event's "before" is the true prior.
    assert "pg_advisory_xact_lock" in session.statements[0]


def test_a_first_manifest_is_recorded_with_no_prior(monkeypatch) -> None:
    session, events = _patch_replacement(monkeypatch)
    asyncio.run(
        ams.set_family_annotation_manifest(
            session, family_id="FAM1", user=_manifest_user("admin"), modules={"vep": "112"}
        )
    )
    assert events[0]["before"] == {
        "analysis_type": None,
        "source": None,
        "recorded_by": None,
        "recorded_at": None,
        "modules": {},
    }
    assert events[0]["summary"] == "Annotation manifest recorded (manual): VEP added"


def test_a_replacement_in_a_family_of_several_analyses_names_the_analysis(monkeypatch) -> None:
    metadata = {"analysis_types": ["long_read_wgs", "short_read_wgs"]}
    session, events = _patch_replacement(monkeypatch, rows=[_LONG_READ, _SHORT_READ], metadata=metadata)
    admin = _manifest_user("admin")
    # Which analysis must be said, and must be one the family holds.
    for asked in (None, "pgt"):
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(
                ams.set_family_annotation_manifest(
                    session, family_id="FAM1", user=admin, modules={"vep": "111"}, analysis_type=asked
                )
            )
        assert excinfo.value.status_code == 422
    assert session.statements == [] and events == []

    asyncio.run(
        ams.set_family_annotation_manifest(
            session, family_id="FAM1", user=admin, modules={"vep": "111"}, analysis_type="short_read_wgs"
        )
    )
    [event] = events
    assert event["summary"] == (
        "Annotation manifest of the short-read WGS replaced (was vcf_header, now manual): "
        "GATK removed; VEP 110 → 111; ClinVar removed"
    )
    assert event["before"]["analysis_type"] == event["after"]["analysis_type"] == "short_read_wgs"
    assert event["before"]["modules"] == _SHORT_READ["modules"]


def test_a_replacement_writes_the_row_of_its_analysis_alone(monkeypatch) -> None:
    session = _MergeSession()
    metadata = {"analysis_types": ["long_read_wgs", "short_read_wgs"]}

    async def _context(session_, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_id=None, assembly_name="GRCh38")

    async def _rows(session_, family_uuid):
        return [_LONG_READ, _SHORT_READ]

    async def _metadata(session_, family_uuid):
        return metadata

    async def _nothing(*args, **kwargs):
        return {}

    session.commit = _nothing  # type: ignore[attr-defined]
    monkeypatch.setattr(ams, "build_family_metadata_context", _context)
    monkeypatch.setattr(ams, "_family_manifest_rows", _rows)
    monkeypatch.setattr(ams, "_family_metadata", _metadata)
    monkeypatch.setattr(ams, "record_clinical_event", _nothing, raising=False)
    monkeypatch.setattr(ams, "get_family_annotation_manifest", _nothing)
    asyncio.run(
        ams.set_family_annotation_manifest(
            session, family_id="FAM1", user=_manifest_user("admin"), modules={"vep": "111"}, analysis_type="long_read_wgs"
        )
    )
    [(sql, params)] = [(sql, params) for sql, params in session.executed if "INSERT INTO" in sql]
    assert "ON CONFLICT (family_id, analysis_type)" in sql
    assert params["analysis"] == "long_read_wgs" and params["source"] == "manual"


def test_the_replacement_summary_lists_at_most_six_changes() -> None:
    before = {f"tool{i}": "1" for i in range(9)}
    after = {f"tool{i}": "2" for i in range(9)}
    summary = ams._manifest_replacement_summary(
        prior_source="manual", source="manual", before=before, after=after
    )
    assert summary.endswith("tool5 1 → 2; and 3 more")
    assert ams._manifest_replacement_summary(
        prior_source="manual", source="manual", before={"vep": "110"}, after={"vep": {"version": "110"}}
    ) == "Annotation manifest replaced (was manual, now manual): no module changed"
    assert ams._manifest_replacement_summary(
        prior_source="manual", source="manual",
        before={"vep": {"version": "110"}}, after={"vep": {"version": "110", "cache": "x"}},
    ) == "Annotation manifest replaced (was manual, now manual): VEP details changed"


# --- an import serialises with a replacement on the same per-family lock ---
# Without it, a replacement could land between an import's read and its write, and the
# import would overwrite the manual manifest it never saw.


class _MergeSession:
    """Records each statement with its parameters; savepoints are pass-through."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    def begin_nested(self):
        return _Savepoint()

    async def execute(self, statement, params=None):
        self.executed.append((" ".join(str(statement).split()), dict(params or {})))


def _patch_stored_row(monkeypatch, session: _MergeSession, row, read: list | None = None):
    statements_before_read: list[list[str]] = []

    async def _row(session_, family_uuid, analysis):
        statements_before_read.append([sql for sql, _ in session.executed])
        if read is not None:
            read.append(analysis)
        return row

    monkeypatch.setattr(ams, "_family_manifest_row", _row)
    return statements_before_read


def test_an_import_takes_the_manifest_lock_before_it_reads_and_writes(monkeypatch) -> None:
    session = _MergeSession()
    before_read = _patch_stored_row(
        monkeypatch, session, {"modules": {"vep": {"version": "110"}}, "source": "vcf_header"}
    )
    asyncio.run(
        ams.merge_vcf_header_provenance(
            session, family_uuid="u1", assembly_id=None, modules={"vep": {"version": "111"}}, analysis="unspecified"
        )
    )
    lock_sql, lock_params = session.executed[0]
    assert "pg_advisory_xact_lock" in lock_sql
    # The same key an admin's replacement takes for this family.
    assert lock_params == {"k": "fam-manifest:u1"}
    assert before_read == [[lock_sql]]  # locked before the read
    assert "INSERT INTO family_annotation_manifest" in session.executed[1][0]


def test_an_import_still_leaves_a_manual_manifest_alone(monkeypatch) -> None:
    session = _MergeSession()
    _patch_stored_row(monkeypatch, session, {"modules": {"vep": {"version": "112"}}, "source": "manual"})
    asyncio.run(
        ams.merge_vcf_header_provenance(
            session, family_uuid="u1", assembly_id=None, modules={"vep": {"version": "111"}}, analysis="unspecified"
        )
    )
    assert [sql for sql, _ in session.executed] == [session.executed[0][0]]
    assert "pg_advisory_xact_lock" in session.executed[0][0]  # the lock, and no write


def test_a_replacement_takes_the_same_manifest_lock(monkeypatch) -> None:
    session, _ = _patch_replacement(monkeypatch)
    recorded: list[dict] = []
    original_execute = session.execute

    async def _execute(statement, params=None):
        recorded.append(dict(params or {}))
        return await original_execute(statement, params)

    session.execute = _execute
    asyncio.run(
        ams.set_family_annotation_manifest(
            session, family_id="FAM1", user=_manifest_user("admin"), modules={"vep": "112"}
        )
    )
    assert recorded[0] == {"k": "fam-manifest:u1"}


def test_an_import_writes_the_row_of_its_own_analysis(monkeypatch) -> None:
    # The long-read import reads and writes the long-read analysis's row only: the short-read
    # analysis's versions stay as its own import recorded them.
    session = _MergeSession()
    read: list = []
    _patch_stored_row(monkeypatch, session, {"modules": {"vep": {"version": "112"}}, "source": "vcf_header"}, read)
    asyncio.run(
        ams.merge_vcf_header_provenance(
            session,
            family_uuid="u1",
            assembly_id=None,
            modules={"deepvariant": {"version": "1.6.1"}},
            analysis="long_read_wgs",
            modality="snv",
        )
    )
    assert read == ["long_read_wgs"]
    insert_sql, params = session.executed[1]
    assert "ON CONFLICT (family_id, analysis_type)" in insert_sql
    assert params["analysis"] == "long_read_wgs"
    assert json.loads(params["modules"]) == {
        "vep": {"version": "112"},
        "deepvariant": {"version": "1.6.1", "by_modality": {"snv": "1.6.1"}},
    }


def test_an_upload_writes_the_row_of_no_analysis(monkeypatch) -> None:
    session = _MergeSession()
    read: list = []
    _patch_stored_row(monkeypatch, session, None, read)
    asyncio.run(
        ams.merge_vcf_header_provenance(
            session, family_uuid="u1", assembly_id=None, modules={"sniffles": {"version": "2.2"}}, analysis=""
        )
    )
    assert read == ["unspecified"] and session.executed[1][1]["analysis"] == "unspecified"
