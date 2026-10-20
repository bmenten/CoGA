"""What the signed analysis record freezes of what the analysis looked at.

Each section of ``signed_record_sections``: the reported variants' descriptions (gene,
transcript, HGVS c. and p., consequence and each sample's genotype; an SV's type, locus,
genes and copy number), the reference-data releases, the repeat-expansion calls and the
Paraphase results with their status and the catalogue behind it; a section that cannot be
read frozen as an explicit marker, which ``snapshot_gaps`` names.
"""

from __future__ import annotations

import asyncio
import types
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.schemas import (
    FamilyParaphaseTableOut,
    FamilyRepeatExpansionTableOut,
    GenotypeOut,
    ParaphaseGeneResultOut,
    ParaphaseMetricOut,
    ParaphaseSampleResultOut,
    RepeatExpansionRowOut,
    RepeatExpansionSampleCallOut,
    VariantOut,
    VariantPage,
)
from backend.app.services import report_signout_service as rss
from backend.app.services import signed_record_sections as srs


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Session:
    """Answers each statement by a fragment of its SQL; counts the savepoints."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.answers = answers or {}
        self.executed: list[tuple[str, Any]] = []
        self.savepoints = 0

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.executed.append((sql, params))
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return _Result(rows)
        return _Result([])

    @asynccontextmanager
    async def _nested(self):
        self.savepoints += 1
        yield

    def begin_nested(self):
        return self._nested()


def _context() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        family_uuid="11111111-1111-1111-1111-111111111111",
        family_id="FAM1",
        assembly_id="22222222-2222-2222-2222-222222222222",
        assembly_name="GRCh38",
    )


def _small(variant_id: str, *, source: str = "clair3", **fields: Any) -> VariantOut:
    return VariantOut.model_validate(
        {
            "_id": variant_id,
            "chr": "1",
            "start": 2000,
            "end": 2001,
            "length": 1,
            "type": "snv",
            "source": source,
            "ref": "C",
            "alt": "T",
            "gene": "GENE_PATH",
            "gene_id": "ENSG01",
            "transcript_id": "ENST01",
            "hgvsc": "c.10C>T",
            "hgvsp": "p.(Arg4Trp)",
            "effect": "missense_variant",
            "impact": "MODERATE",
            "mane_select": True,
            "genotypes": [
                GenotypeOut(sample="PROBAND", gt="0/1", dp=30, ad=[15, 15], af=[0.5]),
                GenotypeOut(sample="MOTHER", gt="0/0", dp=28, gq=60),
            ],
            **fields,
        }
    )


def _sv(variant_id: str, *, source: str = "needlr") -> VariantOut:
    return VariantOut.model_validate(
        {
            "_id": variant_id,
            "chr": "1",
            "start": 7000,
            "end": 12000,
            "length": 5000,
            "type": "DEL",
            "source": source,
            "gene": "BRCA2",
            "gene_symbols": ["BRCA2", "N4BP2L1"],
            "copy_change": "loss",
            "annotation_extra": {"cytoband": "13q13.1"},
            "genotypes": [
                GenotypeOut(sample="PROBAND", gt="0/1", cn=1, read_support=18, qual=60.0),
                GenotypeOut(sample="FATHER", gt="0/1", cn=1),
            ],
        }
    )


# --- Variant descriptions ---------------------------------------------------------


def test_a_reported_variant_is_described_as_the_variant_list_serves_it(monkeypatch) -> None:
    asked: list[dict[str, Any]] = []

    async def _small_page(session, *, context, page, page_size, review_tags):
        asked.append({"kind": "small", "page_size": page_size, "review_tags": review_tags})
        # A reported variant called in two callsets, and one the review list does not report.
        return VariantPage(
            total=3,
            variants=[_small("1-2000-C-T", source="glimpse2"), _small("1-2000-C-T"), _small("9-9-A-G")],
        )

    async def _sv_page(session, *, context, page, page_size, filters):
        asked.append({"kind": "sv", "page_size": page_size, "review_tags": filters.review_tags})
        return VariantPage(total=1, variants=[_sv("1-7000-12000-DEL---")])

    monkeypatch.setattr(srs, "get_family_small_variants_page", _small_page)
    monkeypatch.setattr(srs, "get_family_structural_variants_page", _sv_page)
    out = asyncio.run(
        srs.variant_descriptions(
            _Session(),
            _context(),
            small_ids=["1-2000-C-T"],
            structural_ids=["1-7000-12000-DEL---"],
        )
    )

    # The report's own list: every reported variant, up to the page maximum.
    assert asked == [
        {"kind": "small", "page_size": srs.MAX_VARIANT_PAGE_SIZE, "review_tags": ["report"]},
        {"kind": "sv", "page_size": srs.MAX_VARIANT_PAGE_SIZE, "review_tags": ["report"]},
    ]
    # One entry per callset, in a fixed order; nothing the record does not report.
    assert [(entry["variant_id"], entry["source"]) for entry in out["small"]] == [
        ("1-2000-C-T", "clair3"),
        ("1-2000-C-T", "glimpse2"),
    ]
    first = out["small"][0]
    assert {
        key: first[key]
        for key in ("gene", "transcript", "hgvsc", "hgvsp", "consequence", "impact", "mane_select")
    } == {
        "gene": "GENE_PATH",
        "transcript": "ENST01",
        "hgvsc": "c.10C>T",
        "hgvsp": "p.(Arg4Trp)",
        "consequence": "missense_variant",
        "impact": "MODERATE",
        "mane_select": True,
    }
    assert (first["chrom"], first["start"], first["ref"], first["alt"]) == ("1", 2000, "C", "T")
    # Each sample's genotype as reviewed, samples in order.
    assert first["genotypes"] == [
        {"sample": "MOTHER", "gt": "0/0", "dp": 28, "gq": 60, "ad": None, "af": None},
        {"sample": "PROBAND", "gt": "0/1", "dp": 30, "gq": None, "ad": [15, 15], "af": [0.5]},
    ]
    [sv] = out["structural"]
    assert (sv["sv_type"], sv["chrom"], sv["start"], sv["end"], sv["length"]) == ("DEL", "1", 7000, 12000, 5000)
    assert sv["copy_change"] == "loss"
    assert sv["genes"] == ["BRCA2", "N4BP2L1"]
    assert sv["cytoband"] == "13q13.1"
    assert sv["genotypes"] == [
        {"sample": "FATHER", "gt": "0/1", "cn": 1, "read_support": None, "qual": None, "filter": None},
        {"sample": "PROBAND", "gt": "0/1", "cn": 1, "read_support": 18, "qual": 60.0, "filter": None},
    ]
    assert srs.described_genes(out) == ["BRCA2", "GENE_PATH", "N4BP2L1"]


def test_a_family_without_reported_variants_reads_no_variant_list(monkeypatch) -> None:
    async def _must_not_read(*args, **kwargs):
        raise AssertionError("no reported variant, no list to read")

    monkeypatch.setattr(srs, "get_family_small_variants_page", _must_not_read)
    monkeypatch.setattr(srs, "get_family_structural_variants_page", _must_not_read)
    out = asyncio.run(srs.variant_descriptions(_Session(), _context(), small_ids=[], structural_ids=[]))
    assert out == {"small": [], "structural": []}


# --- Reference data --------------------------------------------------------------


def test_the_gene_reference_names_the_release_each_source_answered_with() -> None:
    session = _Session(
        {
            "FROM gene_info": [
                {"hgnc_symbol": "BRCA2", "source": "hgnc", "status": "success", "release": "2026-09-01", "checksum": "aa"},
                {"hgnc_symbol": "GENE_PATH", "source": "hgnc", "status": "success", "release": "2026-09-01", "checksum": "aa"},
                {"hgnc_symbol": "BRCA2", "source": "dbnsfp_gene", "status": "success", "release": "dbNSFP 5.4", "checksum": "bb"},
                # A partial refresh: this gene was answered by another ClinGen file.
                {"hgnc_symbol": "GENE_PATH", "source": "clingen", "status": "missing", "release": "2026-08-30", "checksum": "cc"},
                {"hgnc_symbol": "BRCA2", "source": "clingen", "status": "success", "release": "2026-09-30", "checksum": "dd"},
            ]
        }
    )
    out = asyncio.run(
        srs.gene_reference_releases(session, "asm", ["GENE_PATH", "BRCA2", "NOT_LOADED"])
    )
    assert out["genes"] == ["BRCA2", "GENE_PATH", "NOT_LOADED"]
    assert out["not_in_reference"] == ["NOT_LOADED"]
    assert out["sources"] == [
        {"source": "clingen", "status": "missing", "release": "2026-08-30", "checksum": "cc", "genes": ["GENE_PATH"]},
        {"source": "clingen", "status": "success", "release": "2026-09-30", "checksum": "dd", "genes": ["BRCA2"]},
        {"source": "dbnsfp_gene", "status": "success", "release": "dbNSFP 5.4", "checksum": "bb", "genes": ["BRCA2"]},
        {"source": "hgnc", "status": "success", "release": "2026-09-01", "checksum": "aa", "genes": ["BRCA2", "GENE_PATH"]},
    ]
    sql, params = session.executed[0]
    assert params == {"assembly_id": "asm", "symbols": ["BRCA2", "GENE_PATH", "NOT_LOADED"]}
    # Bound as one array, never spliced into the statement.
    assert "ANY(CAST(:symbols AS text[]))" in sql


def test_no_reported_gene_reads_no_gene_reference() -> None:
    session = _Session()
    out = asyncio.run(srs.gene_reference_releases(session, "asm", []))
    assert out == {"genes": [], "sources": [], "not_in_reference": []}
    assert session.executed == []


def test_the_cnv_knowledgebase_is_named_by_its_latest_load() -> None:
    loaded = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    session = _Session(
        {
            "FROM reference_dataset_imports": [
                {
                    "source": "clinical-cnv-kb",
                    "source_version": "not stated",
                    "source_release_date": None,
                    "source_url": None,
                    "performed_at": loaded,
                    "inserted": 1234,
                }
            ]
        }
    )
    assert asyncio.run(srs.clinical_cnv_release(session, "asm")) == {
        "source": "clinical-cnv-kb",
        "release": "not stated",
        "release_date": None,
        "source_url": None,
        "loaded_at": loaded.isoformat(),
        "records": 1234,
    }
    assert "dataset_type = 'clinical_cnvs'" in session.executed[0][0]
    # None loaded: said as none, not as an empty release.
    assert asyncio.run(srs.clinical_cnv_release(_Session(), "asm")) is None


# --- Repeat expansions and Paraphase ----------------------------------------------


_CATALOGUE_ROWS = [
    {
        "locus_id": "HD_HTT",
        "gene": "HTT",
        "warning_min": 27,
        "pathogenic_min": 36,
        "benign_min": None,
        "benign_max": None,
        "pathogenic_max": None,
        "premutation_min": None,
        "source": "STRchive",
    },
    {
        "locus_id": "HTT",
        "gene": "HTT",
        "warning_min": 27,
        "pathogenic_min": 36,
        "benign_min": None,
        "benign_max": None,
        "pathogenic_max": None,
        "premutation_min": None,
        "source": None,
    },
]


def _repeat_table() -> FamilyRepeatExpansionTableOut:
    return FamilyRepeatExpansionTableOut(
        loci=[
            RepeatExpansionRowOut(
                locus_id="HD_HTT",
                gene="HTT",
                display_name="HTT",
                disease="Huntington disease",
                inheritance="AD",
                chr="chr4",
                start=3074876,
                end=3074933,
                motif="CAG",
                warning_min=27,
                pathogenic_min=36,
                status="pathogenic",
                calls={
                    "PROBAND": RepeatExpansionSampleCallOut(
                        sample="PROBAND",
                        genotype="0/1",
                        alleles=[
                            {"repeat_count": 18, "bp_length": 54, "status": "normal"},
                            {"repeat_count": 40, "bp_length": 120, "support_reads": 12, "status": "pathogenic"},
                        ],
                        status="pathogenic",
                    ),
                    "FATHER": RepeatExpansionSampleCallOut(
                        sample="FATHER",
                        genotype="0/0",
                        alleles=[{"repeat_count": 18, "bp_length": 54, "status": "normal"}],
                        status="normal",
                    ),
                },
            )
        ]
    )


def test_each_repeat_call_is_frozen_with_its_status_and_the_thresholds_behind_it(monkeypatch) -> None:
    async def _table(session, *, context):
        return _repeat_table()

    monkeypatch.setattr(srs, "get_family_repeat_expansion_table_response", _table)
    out = asyncio.run(srs.repeat_expansion_calls(_Session({"FROM repeat_loci": _CATALOGUE_ROWS}), _context()))

    [locus] = out["loci"]
    assert (locus["locus_id"], locus["gene"], locus["status"]) == ("HD_HTT", "HTT", "pathogenic")
    assert locus["thresholds"] == {
        "intermediate_min": 27,
        "premutation_min": None,
        "pathogenic_min": 36,
        "benign_min": None,
        "benign_max": None,
        "pathogenic_max": None,
    }
    assert [(call["sample"], call["status"]) for call in locus["calls"]] == [
        ("FATHER", "normal"),
        ("PROBAND", "pathogenic"),
    ]
    assert locus["calls"][1]["alleles"] == [
        {"repeat_count": 18, "bp_length": 54, "support_reads": None, "interruption_label": None, "status": "normal"},
        {"repeat_count": 40, "bp_length": 120, "support_reads": 12, "interruption_label": None, "status": "pathogenic"},
    ]
    catalogue = out["catalogue"]
    assert catalogue["loci"] == 2
    assert catalogue["sources"] == ["CoGA built-in", "STRchive"]
    assert len(catalogue["digest"]) == 64


def test_the_repeat_catalogue_digest_moves_with_a_threshold() -> None:
    same = asyncio.run(srs.repeat_catalogue_identity(_Session({"FROM repeat_loci": _CATALOGUE_ROWS})))
    again = asyncio.run(srs.repeat_catalogue_identity(_Session({"FROM repeat_loci": _CATALOGUE_ROWS})))
    moved_rows = [dict(_CATALOGUE_ROWS[0], pathogenic_min=40), _CATALOGUE_ROWS[1]]
    moved = asyncio.run(srs.repeat_catalogue_identity(_Session({"FROM repeat_loci": moved_rows})))
    assert same == again
    assert moved["digest"] != same["digest"]


def _paraphase_table() -> FamilyParaphaseTableOut:
    return FamilyParaphaseTableOut(
        genes=[
            ParaphaseGeneResultOut(
                gene_symbol="SMN1",
                is_medically_relevant=True,
                samples={
                    "PROBAND": ParaphaseSampleResultOut(
                        sample="PROBAND",
                        clinical_status="pathogenic",
                        total_cn=2,
                        gene_cn=0,
                        copy_number_signal=True,
                        copy_number_metrics=[ParaphaseMetricOut(key="smn1_cn", label="SMN1 CN", value=0.0)],
                        uploaded_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
                    )
                },
            ),
            ParaphaseGeneResultOut(gene_symbol="GBA", is_medically_relevant=True, samples={}),
        ]
    )


def test_each_paraphase_result_is_frozen_with_its_status_and_the_rule_behind_it(monkeypatch) -> None:
    async def _table(session, *, context):
        return _paraphase_table()

    monkeypatch.setattr(srs, "get_family_paraphase_table_response", _table)
    out = asyncio.run(srs.paraphase_results(_Session(), _context()))

    assert [gene["gene"] for gene in out["genes"]] == ["GBA", "SMN1"]
    smn = out["genes"][1]
    assert smn["region_id"] == "smn1_smn2"
    # The catalogue's rule the status was read with, and the copy-number norm.
    assert smn["status_rule"]["field"] == "smn1_cn"
    assert smn["expected_copy_number"]["smn1_cn"] == [1.0, 1.0]
    [result] = smn["samples"]
    assert (result["sample"], result["status"], result["copy_number_signal"]) == ("PROBAND", "pathogenic", True)
    assert result["copy_number"] == [{"key": "smn1_cn", "label": "SMN1 CN", "value": 0.0}]
    assert result["loaded_at"] == "2026-10-02T00:00:00+00:00"
    assert out["catalogue"]["regions"] > 0 and len(out["catalogue"]["digest"]) == 64


# --- The sections together, and what could not be read ------------------------------


def test_a_section_that_cannot_be_read_is_frozen_as_unavailable_and_named(monkeypatch) -> None:
    async def _fails(*args, **kwargs):
        raise RuntimeError("SELECT ... WHERE family = 'secret'")

    async def _table(session, *, context):
        return _repeat_table()

    monkeypatch.setattr(srs, "get_family_repeat_expansion_table_response", _table)
    monkeypatch.setattr(srs, "get_family_paraphase_table_response", _fails)
    monkeypatch.setattr(srs, "get_family_small_variants_page", _fails)
    session = _Session({"FROM repeat_loci": _CATALOGUE_ROWS})
    sections = asyncio.run(
        srs.build_record_sections(
            session,
            context=_context(),
            reported=[{"variant_id": "1-2000-C-T"}],
            reported_structural=[],
        )
    )

    assert set(sections) == {section.key for section in srs.RECORD_SECTIONS}
    # A fixed sentence, never the error's text, which may quote what it read.
    assert sections["variant_descriptions"] == {"unavailable": srs.VARIANT_DESCRIPTIONS.unavailable}
    assert sections["paraphase"] == {"unavailable": srs.PARAPHASE.unavailable}
    assert "secret" not in str(sections)
    # The others are read all the same, each in a savepoint of its own.
    assert sections["repeat_expansions"]["loci"][0]["locus_id"] == "HD_HTT"
    assert sections["gene_panels"] == {"applied": []}
    assert session.savepoints == 6
    # The reference data names no gene when no variant could be described.
    assert sections["reference_data"]["gene_reference"]["genes"] == []

    gaps = rss.snapshot_gaps({**sections, "modules": [], "reference_modules": ["assembly", "gene_loci", "monarch", "hpo"]})
    assert gaps == [
        {"section": "variant_descriptions", "item": "Variant descriptions", "reason": srs.VARIANT_DESCRIPTIONS.unavailable},
        {"section": "paraphase", "item": "Paraphase results", "reason": srs.PARAPHASE.unavailable},
    ]


def test_a_reference_data_part_that_cannot_be_read_is_named_on_its_own() -> None:
    snapshot = {
        "reference_data": {
            "gene_reference": {"unavailable": srs.REFERENCE_DATA_PARTS["gene_reference"][1]},
            "clinical_cnvs": None,
        }
    }
    assert srs.record_section_gaps(snapshot) == [
        {
            "section": "reference_data.gene_reference",
            "item": "Gene reference",
            "reason": "The gene reference's releases could not be read.",
        }
    ]


def test_what_a_section_cannot_use_is_left_out_not_guessed() -> None:
    # No descriptions, no genes; a gene-reference row for a gene not looked up is not counted;
    # no assembly, no knowledgebase; a count that is not one is none.
    assert srs.described_genes({"unavailable": "The reported variants' descriptions could not be read."}) == []
    assert srs.described_genes(None) == []
    session = _Session(
        {"FROM gene_info": [{"hgnc_symbol": "OTHER", "source": "hgnc", "status": "success", "release": "r", "checksum": "c"}]}
    )
    out = asyncio.run(srs.gene_reference_releases(session, "asm", ["GENE_PATH"]))
    assert out["sources"] == [] and out["not_in_reference"] == ["GENE_PATH"]
    assert asyncio.run(srs.clinical_cnv_release(_Session(), None)) is None
    assert srs._int_or_none("not a number") is None and srs._int_or_none(True) is None
    # A region without a copy-number norm has none to freeze.
    assert srs._expected_copy_number({"expected_copy_number": {}}) is None


@pytest.mark.parametrize("absent", [section.key for section in srs.RECORD_SECTIONS])
def test_a_section_a_record_predates_is_no_gap(absent: str) -> None:
    # Said by the signed view and listed by the check as not compared, not as not captured.
    snapshot = {section.key: {} for section in srs.RECORD_SECTIONS if section.key != absent}
    assert srs.record_section_gaps(snapshot) == []
