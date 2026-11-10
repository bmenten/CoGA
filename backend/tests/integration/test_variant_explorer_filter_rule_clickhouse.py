"""A Variant Explorer row is the family pages pooled, on real ClickHouse (the roadmap's
*Variant Explorer filters*; the owner's decisions of 2026-11-06).

The Explorer reads each filter it shares with the family search through the family search's
own SQL builders (``clickhouse_variant_queries``): the location, the variant type, the
imputed callsets, the gene, the rsID, the excluded ClinVar classes, the per-transcript
annotation filters and the gene panel. Each ``entries`` row is read with its own annotation,
as on the family page, so a variant that two families carry under two annotations (two VEP
runs) is counted, under an annotation filter, only in the family whose own annotation meets
it. The chromosome is read with or without ``chr``, and chrM by any of its names (M, MT).

A row's own annotation is the annotation set it was imported with (its key and
``annotationSetHash``), whatever its version: family B is written as a VEP TSV import
(``vep_tsv``), so a variant both families carry under the same annotations has it under two
versions, and is read alike in both. The rsID a family's VCF gives a variant is written beside
its annotations: family B's page also finds, by that rsID, a variant its own VCF gives none,
which family A's names with the same annotations.

One reading is the Explorer's own, by design: the sample genotype filter keeps a variant
the sample carries with that genotype in any of its rows, and then counts every carrier of
the cohort (the owner's decision of 2026-11-06). The genotype classes are the family page's.

Two families of one proband each go in once, through the import's write path
(``insert_small_variant_records``), annotated as the import parses VEP's CSQ. For each case
the test opens both family pages (``get_family_small_variants_page``) with the filter, and
the Explorer's search and CSV export; it asserts that each family page keeps what the rule
keeps, and that each Explorer row counts the families and carriers of the family pages that
keep it (or, for the genotype filter, of the variant's rows). The family pages' hydration is
stubbed: it decorates the rows it is given and keeps or drops none. ``resolve_scope`` is
monkeypatched to a fresh project, so only these rows are counted.

The variants sit on chromosome 14 at 74.1 Mb and on chrM at 14,701-14,702, where no other
test writes: the annotation tables are keyed by variant, not by project or family.

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``smoke`` job sets it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import (
    explorer_shows,
    proband_context,
    run_closing_stores,
    small_variant,
    vep_annotations,
)

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_SAMPLE_A, _SAMPLE_B = "EXPF_A1", "EXPF_B1"

_CSQ_FORMAT = (
    "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|MANE_SELECT"
    "|Existing_variation|CLIN_SIG|gnomADe_AF"
)


def _csq(
    consequence: str,
    impact: str,
    gene: str,
    *,
    rsid: str = "",
    clinvar: str = "",
    af: str = "0.00001",
) -> str:
    """One VEP CSQ entry: the MANE Select (and canonical) transcript of ``gene``."""
    return "|".join(
        [
            "T", consequence, impact, gene, f"ENSG_{gene}", "Transcript", f"ENST_{gene}_1",
            "YES", f"NM_{gene}.1", rsid, clinvar, af,
        ]
    )


# Carried by both families, each under its own annotation (two VEP runs): family A's says
# stop gained (HIGH) in EXPF1, rs990001, of uncertain significance and rare; family B's says
# missense (MODERATE) in EXPF1B, benign and at 2 % in gnomAD.
_TWO_RUNS = "14-74100101-C-T"
# Carried by both families under one annotation: heterozygous in A, homozygous in B.
_SHARED = "14-74100201-G-A"
# A deletion that starts one base before 74,100,300 (family A).
_DELETION = "14-74100299-CAT-C"
# Imputed only (GLIMPSE2), in family A.
_IMPUTED = "14-74100401-A-G"
# A two-base substitution (family A).
_MNV = "14-74100501-AC-GT"
# Carried by both families under one annotation; family A's VCF gives it rs990002, B's none.
_RSID_ELSEWHERE = "14-74100601-T-C"
# The mitochondrion, stored as M by family A's VCF (chrM) and as MT by family B's.
_MITO_M = "M-14701-A-G"
_MITO_MT = "MT-14702-T-C"

_RUN_ONE = [_csq("stop_gained", "HIGH", "EXPF1", rsid="rs990001", clinvar="uncertain_significance")]
_RUN_TWO = [_csq("missense_variant", "MODERATE", "EXPF1B", clinvar="benign", af="0.02")]


@dataclass(frozen=True)
class _Stored:
    csq: list[str]
    gt: str = "0/1"
    rsid: str | None = None
    source: str = "clair3"


_FAMILY_A: dict[str, _Stored] = {
    _TWO_RUNS: _Stored(_RUN_ONE, rsid="rs990001"),
    _SHARED: _Stored([_csq("missense_variant", "MODERATE", "EXPF2")]),
    _DELETION: _Stored([_csq("frameshift_variant", "HIGH", "EXPF2")]),
    _IMPUTED: _Stored([_csq("intron_variant", "MODIFIER", "EXPF2")], source="glimpse2"),
    _MNV: _Stored([_csq("missense_variant", "MODERATE", "EXPF2")]),
    _RSID_ELSEWHERE: _Stored([_csq("missense_variant", "MODERATE", "EXPF3")], rsid="rs990002"),
    _MITO_M: _Stored([_csq("missense_variant", "MODERATE", "MT-ND5", af="")], gt="1"),
}
_FAMILY_B: dict[str, _Stored] = {
    _TWO_RUNS: _Stored(_RUN_TWO),
    _SHARED: _Stored([_csq("missense_variant", "MODERATE", "EXPF2")], gt="1/1"),
    _RSID_ELSEWHERE: _Stored([_csq("missense_variant", "MODERATE", "EXPF3")]),
    _MITO_MT: _Stored([_csq("synonymous_variant", "LOW", "MT-ND5", af="")], gt="1"),
}
_STORED = {"A": (_SAMPLE_A, _FAMILY_A), "B": (_SAMPLE_B, _FAMILY_B)}
# The annotation version each family is imported with: B's as a VEP TSV.
_ANNOTATION_VERSION = {"A": "current", "B": "vep_tsv"}

# What each family page lists without a filter: its imputed callsets are hidden.
_ALL_A = set(_FAMILY_A) - {_IMPUTED}
_ALL_B = set(_FAMILY_B)
_CHR14_A = {_TWO_RUNS, _SHARED, _DELETION, _MNV, _RSID_ELSEWHERE}
_CHR14_B = {_TWO_RUNS, _SHARED, _RSID_ELSEWHERE}


@dataclass(frozen=True)
class _Case:
    """One filter as the Explorer names it and as the family page names it, the variants
    each family's page keeps, and, where the Explorer counts more than those pages (the
    genotype filter), the Explorer's rows as variant -> (families, carriers)."""

    explorer: dict[str, Any]
    family: dict[str, Any]
    kept: dict[str, set[str]]
    explorer_rows: dict[str, tuple[int, int]] | None = None
    panel_genes: tuple[str, ...] = field(default_factory=tuple)


def _pooled(kept: dict[str, set[str]]) -> dict[str, tuple[int, int]]:
    """The family pages pooled: each variant with the number of families whose page keeps
    it and their carriers (one proband each)."""
    families = Counter(variant for variants in kept.values() for variant in variants)
    return {variant: (count, count) for variant, count in families.items()}


_CASES = {
    "no filter": _Case({}, {}, {"A": _ALL_A, "B": _ALL_B}),
    # An annotation filter reads each family's own annotation.
    "impact HIGH": _Case(
        {"impacts": ["HIGH"]}, {"impact": ["HIGH"]}, {"A": {_TWO_RUNS, _DELETION}, "B": set()}
    ),
    "impact MODERATE": _Case(
        {"impacts": ["MODERATE"]},
        {"impact": ["MODERATE"]},
        {"A": {_SHARED, _MNV, _RSID_ELSEWHERE, _MITO_M}, "B": {_TWO_RUNS, _SHARED, _RSID_ELSEWHERE}},
    ),
    "gene": _Case({"gene": "EXPF1"}, {"gene": "EXPF1"}, {"A": {_TWO_RUNS}, "B": set()}),
    "gene renamed": _Case({"gene": "expf1b"}, {"gene": "expf1b"}, {"A": set(), "B": {_TWO_RUNS}}),
    "gene by Ensembl ID": _Case(
        {"gene": "ENSG_EXPF1"}, {"gene": "ENSG_EXPF1"}, {"A": {_TWO_RUNS}, "B": set()}
    ),
    "rsID": _Case({"rsid": "RS990001"}, {"rsid": "RS990001"}, {"A": {_TWO_RUNS}, "B": set()}),
    # The rsID family A's VCF gives a variant both carry under one annotation set, which family
    # B imported under another version: B's row finds it through its annotation set.
    "rsID of another import": _Case(
        {"rsid": "rs990002"}, {"rsid": "rs990002"}, {"A": {_RSID_ELSEWHERE}, "B": {_RSID_ELSEWHERE}}
    ),
    "ClinVar": _Case({"clinvar": ["benign"]}, {"clinvar": ["benign"]}, {"A": set(), "B": {_TWO_RUNS}}),
    "excluded ClinVar": _Case(
        {"exclude_clinvar": ["Benign"]},
        {"exclude_clinvar": ["Benign"]},
        {"A": _ALL_A, "B": _ALL_B - {_TWO_RUNS}},
    ),
    "frequency": _Case(
        {"max_gnomad_af": 0.01}, {"max_gnomad_af": 0.01}, {"A": _ALL_A, "B": _ALL_B - {_TWO_RUNS}}
    ),
    "panel": _Case(
        {"panel_id": "panel"},
        {"panel_id": "panel"},
        {"A": {_TWO_RUNS}, "B": set()},
        panel_genes=("EXPF1",),
    ),
    # The chromosome with or without chr; chrM by any of its names.
    "chromosome": _Case({"chromosome": "14"}, {"chr": "14"}, {"A": _CHR14_A, "B": _CHR14_B}),
    "chromosome with chr": _Case({"chromosome": "chr14"}, {"chr": "chr14"}, {"A": _CHR14_A, "B": _CHR14_B}),
    "chrM as M": _Case({"chromosome": "M"}, {"chr": "M"}, {"A": {_MITO_M}, "B": {_MITO_MT}}),
    "chrM as MT": _Case({"chromosome": "MT"}, {"chr": "MT"}, {"A": {_MITO_M}, "B": {_MITO_MT}}),
    "chrM as chrM": _Case({"chromosome": "chrM"}, {"chr": "chrM"}, {"A": {_MITO_M}, "B": {_MITO_MT}}),
    # A location keeps a variant that starts in it: the deletion starts before 74,100,300.
    "location": _Case(
        {"chromosome": "14", "start": 74_100_300, "end": 74_100_600},
        {"chr": "14", "start": 74_100_300, "end": 74_100_600},
        {"A": {_MNV}, "B": set()},
    ),
    "location from a start": _Case(
        {"chromosome": "14", "start": 74_100_201, "end": 74_100_299},
        {"chr": "14", "start": 74_100_201, "end": 74_100_299},
        {"A": {_SHARED, _DELETION}, "B": {_SHARED}},
    ),
    "SNV": _Case(
        {"variant_type": "SNV"},
        {"type": "SNV"},
        {"A": {_TWO_RUNS, _SHARED, _RSID_ELSEWHERE, _MITO_M}, "B": _ALL_B},
    ),
    "INDEL (an MNV too)": _Case(
        {"variant_type": "INDEL"}, {"type": "INDEL"}, {"A": {_DELETION, _MNV}, "B": set()}
    ),
    "MNV": _Case({"variant_type": "mnv"}, {"type": "mnv"}, {"A": {_MNV}, "B": set()}),
    # The genotype filter: the family page's classes (a haploid 1 is homozygous), and the
    # Explorer's own reading, by variant: every carrier of a kept variant is counted.
    "genotype het": _Case(
        {"sample_genotype_filters": [(_SAMPLE_A, "het")]},
        {"sample_filters": [f"{_SAMPLE_A}:het"]},
        {"A": {_TWO_RUNS, _SHARED, _DELETION, _MNV, _RSID_ELSEWHERE}},
        explorer_rows={
            _TWO_RUNS: (2, 2), _SHARED: (2, 2), _DELETION: (1, 1), _MNV: (1, 1), _RSID_ELSEWHERE: (2, 2)
        },
    ),
    "genotype hom (haploid)": _Case(
        {"sample_genotype_filters": [(_SAMPLE_A, "hom")]},
        {"sample_filters": [f"{_SAMPLE_A}:hom"]},
        {"A": {_MITO_M}},
        explorer_rows={_MITO_M: (1, 1)},
    ),
    "genotype hom": _Case(
        {"sample_genotype_filters": [(_SAMPLE_B, "hom")]},
        {"sample_filters": [f"{_SAMPLE_B}:hom"]},
        {"B": {_SHARED, _MITO_MT}},
        explorer_rows={_SHARED: (2, 2), _MITO_MT: (1, 1)},
    ),
    "genotype het or hom": _Case(
        {"sample_genotype_filters": [(_SAMPLE_B, "het_hom")]},
        {"sample_filters": [f"{_SAMPLE_B}:het|hom"]},
        {"B": _ALL_B},
        explorer_rows={_TWO_RUNS: (2, 2), _SHARED: (2, 2), _RSID_ELSEWHERE: (2, 2), _MITO_MT: (1, 1)},
    ),
    # A variant the sample carries is kept, and its rows are then read by the annotation
    # filters: only family A's annotation of the variant B carries says HIGH.
    "genotype and impact": _Case(
        {"sample_genotype_filters": [(_SAMPLE_B, "het_hom")], "impacts": ["HIGH"]},
        {"sample_filters": [f"{_SAMPLE_B}:het|hom"], "impact": ["HIGH"]},
        {"B": set()},
        explorer_rows={_TWO_RUNS: (1, 1)},
    ),
}


@pytest.fixture(scope="module")
def stored_families() -> dict[str, str]:
    """Write both families once, in one fresh project."""
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    project = str(uuid4())
    families = {label: str(uuid4()) for label in _STORED}

    def _records(sample: str, variants: dict[str, _Stored]) -> list[Any]:
        records = []
        for variant_id, stored in variants.items():
            record = small_variant(
                variant_id, vep_annotations(_CSQ_FORMAT, stored.csq), sample=sample, source=stored.source
            )
            call = replace(record.calls[0], gt=stored.gt)
            records.append(replace(record, rsid=stored.rsid, calls=[call]))
        return records

    async def _write() -> None:
        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        for label, (sample, variants) in _STORED.items():
            await insert_small_variant_records(
                _ASSEMBLY,
                families[label],
                [project],
                _records(sample, variants),
                annotation_version=_ANNOTATION_VERSION[label],
            )

    run_closing_stores(_write, init_schema=True)
    return {"project": project, **families}


def _stub_family_page_and_scope(
    monkeypatch: pytest.MonkeyPatch, stored: dict[str, str], panel_genes: tuple[str, ...]
) -> Any:
    """Point the Explorer at the fresh project, stub the family page's hydration and
    ranking cache, and answer the panel with ``panel_genes``; the Explorer's scope."""
    import backend.app.services.clickhouse_family_variants as family_variants
    import backend.app.services.variant_explorer_service as explorer
    from backend.app.services.clickhouse_variant_records import PanelFilterConstraints

    scope = explorer.ExplorerScope(assembly_id=str(uuid4()), assembly_name=_ASSEMBLY, project_ids=[stored["project"]])

    async def _fake_scope(session: Any, user: Any, assembly_id: Any) -> Any:
        return scope

    async def _no_hydration(session: Any, *, context: Any, variants: Any) -> None:
        return None

    async def _panel(session: Any, panel_id: Any, *, assembly_id: Any) -> PanelFilterConstraints:
        return PanelFilterConstraints(genes=panel_genes)

    monkeypatch.setattr(explorer, "resolve_scope", _fake_scope)
    # Each family shows its proband.
    explorer_shows(monkeypatch, {stored[label]: [sample] for label, (sample, _variants) in _STORED.items()})
    monkeypatch.setattr(explorer, "_fetch_panel_constraints", _panel)
    monkeypatch.setattr(family_variants, "_fetch_panel_constraints", _panel)
    monkeypatch.setattr(family_variants, "_hydrate_small_variant_outs", _no_hydration)
    return scope


def _family_context(stored: dict[str, str], label: str) -> Any:
    sample, _variants = _STORED[label]
    family_uuid = stored[label]
    return proband_context(
        family_uuid, f"EXPF-{label}-{family_uuid[:8]}", stored["project"], sample=sample,
        assembly_id=str(uuid4()), assembly_name=_ASSEMBLY,
    )


@pytest.mark.parametrize("case", list(_CASES.values()), ids=list(_CASES))
def test_an_explorer_row_is_the_family_pages_pooled(
    monkeypatch: pytest.MonkeyPatch, stored_families: dict[str, str], case: _Case
) -> None:
    import backend.app.services.clickhouse_family_variants as family_variants
    import backend.app.services.variant_explorer_service as explorer
    from backend.app.core.postgres import get_postgres_sessionmaker

    scope = _stub_family_page_and_scope(monkeypatch, stored_families, case.panel_genes)
    filters = explorer.GlobalVariantFilters(**case.explorer)

    async def _run() -> dict[str, Any]:
        results: dict[str, Any] = {}
        async with get_postgres_sessionmaker()() as session:
            for label in case.kept:
                page = await family_variants.get_family_small_variants_page(
                    session, context=_family_context(stored_families, label), page=1, page_size=100, **case.family
                )
                results[f"family {label}"] = (page.total, {variant.id for variant in page.variants})
            explorer_page = await explorer.search_global_small_variants(
                session, user=None, filters=filters, assembly_id=scope.assembly_id
            )
            export = await explorer.export_global_small_variants(
                session, user=None, filters=filters, assembly_id=scope.assembly_id
            )
        rows = {row.variant_id: (row.total_families, row.total_samples) for row in explorer_page.variants}
        results["explorer"] = (explorer_page.total, rows)
        results["explorer export"] = {row.variant_id: (row.total_families, row.total_samples) for row in export.rows}
        return results

    results = run_closing_stores(_run)
    expected_rows = case.explorer_rows if case.explorer_rows is not None else _pooled(case.kept)
    expected: dict[str, Any] = {f"family {label}": (len(kept), kept) for label, kept in case.kept.items()}
    expected["explorer"] = (len(expected_rows), expected_rows)
    expected["explorer export"] = expected_rows
    assert results == expected


def test_imputed_callsets_are_hidden_as_on_the_family_page(
    monkeypatch: pytest.MonkeyPatch, stored_families: dict[str, str]
) -> None:
    """The imputed callsets (GLIMPSE2, SHAPEIT) are hidden by one clause on both pages: the
    family page shows them when its callset filter names one, the Explorer when asked to
    include them."""
    import backend.app.services.clickhouse_family_variants as family_variants
    import backend.app.services.variant_explorer_service as explorer
    from backend.app.core.postgres import get_postgres_sessionmaker

    scope = _stub_family_page_and_scope(monkeypatch, stored_families, ())
    context = _family_context(stored_families, "A")

    async def _run() -> dict[str, Any]:
        async with get_postgres_sessionmaker()() as session:
            default_page = await family_variants.get_family_small_variants_page(
                session, context=context, page=1, page_size=100
            )
            imputed_page = await family_variants.get_family_small_variants_page(
                session, context=context, page=1, page_size=100, source="glimpse2"
            )
            hidden = await explorer.search_global_small_variants(
                session, user=None, filters=explorer.GlobalVariantFilters(), assembly_id=scope.assembly_id
            )
            included = await explorer.search_global_small_variants(
                session,
                user=None,
                filters=explorer.GlobalVariantFilters(include_imputed=True, sample_genotype_filters=[(_SAMPLE_A, "het")]),
                assembly_id=scope.assembly_id,
            )
        return {
            "family page": {variant.id for variant in default_page.variants},
            "family page, GLIMPSE2": {variant.id for variant in imputed_page.variants},
            "explorer": {row.variant_id for row in hidden.variants},
            "explorer with imputed, sample het": {row.variant_id for row in included.variants},
        }

    assert run_closing_stores(_run) == {
        "family page": _ALL_A,
        "family page, GLIMPSE2": {_IMPUTED},
        "explorer": _ALL_A | _ALL_B,
        "explorer with imputed, sample het": {_TWO_RUNS, _SHARED, _DELETION, _MNV, _RSID_ELSEWHERE, _IMPUTED},
    }
