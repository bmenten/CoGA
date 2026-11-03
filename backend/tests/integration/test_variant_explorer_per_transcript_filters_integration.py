"""The Variant Explorer keeps and drops what the family search does (CLIN-29, CLIN-5), on real ClickHouse.

The family search needs one transcript of a variant to meet every annotation filter
together: impact, consequence, ClinVar, frequency, the in-silico scores and *Canonical*,
*MANE* and *LoF only*. The explorer tested each filter against the annotation index, which
pools a variant's transcripts, so with *MANE only* and impact HIGH it kept a variant whose
HIGH transcript is not its MANE transcript, which the family page drops; and it required
every in-silico minimum, where the family page takes any one.

*MANE only* keeps a variant with a MANE Select or a MANE Plus Clinical transcript (CLIN-5).
The explorer read ``has_mane_select`` alone, so a variant whose only MANE transcript is MANE
Plus Clinical was missing from its results and its CSV export: one such variant (its canonical
transcript not MANE) is kept by *MANE only*, beside the MANE Select ones, and a variant with
neither is dropped.

Eight variants go in once, through the import's write path (``insert_small_variant_records``),
annotated as the import parses VEP's CSQ, most with transcripts that disagree. For each
filter set the test runs, on the same rows:

* the family search as the family page runs it (``get_family_small_variants_page``, where
  ClickHouse filters and pages), with its count;
* the family search's Python matcher (``_small_record_matches``), which its ranked,
  inheritance and carrier-screening views apply to the rows they read;
* the explorer's search, with its count, the same search narrowed to the variants' locus,
  and its CSV export;

and asserts that each keeps exactly the expected variants. The family page's hydration
(reviews, cohort counts, constraint, SV badges) is stubbed: it decorates the rows it is
given and keeps or drops none.

``resolve_scope`` is monkeypatched to a fresh project, so only these rows are counted, and the
family is a fresh one. The variants sit on chromosome 9, where no other test writes, because
the annotation tables are keyed by variant, not by project or family.

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``smoke`` job sets it.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import proband_context, run_closing_stores, small_variant, vep_annotations

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_SAMPLE = "CL29_S1"
_LOCUS = {"chr": "9", "start": 136_700_000, "end": 136_701_000}

_CSQ_FORMAT = (
    "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|MANE_SELECT"
    "|MANE_PLUS_CLINICAL|LoF|REVEL|CADD_PHRED|gnomADe_AF|CLIN_SIG"
)


def _csq(
    consequence: str,
    impact: str,
    gene: str,
    transcript: str,
    *,
    mane: str = "",
    mane_plus_clinical: str = "",
    canonical: bool | None = None,
    lof: str = "",
    revel: str = "",
    cadd: str = "",
    af: str = "0.00001",
    clinvar: str = "",
) -> str:
    """One VEP CSQ entry. A MANE Select transcript is also the canonical one, unless said otherwise."""
    canonical_flag = "YES" if (bool(mane) if canonical is None else canonical) else ""
    return "|".join(
        [
            "T", consequence, impact, gene, f"ENSG_{gene}", "Transcript", transcript,
            canonical_flag, mane, mane_plus_clinical, lof, revel, cadd, af, clinvar,
        ]
    )


# HIGH on a transcript that is neither MANE nor canonical; MODIFIER on the MANE one.
_HIGH_OFF_MANE = "9-136700101-C-T"
# One transcript: MANE, HIGH and LoF.
_MANE_HIGH = "9-136700201-C-T"
# Missense on the MANE transcript, a stop gained (LoF) on another.
_LOF_OFF_MANE = "9-136700301-C-T"
# Synonymous on the MANE transcript, a damaging missense (REVEL 0.9) on another.
_REVEL_OFF_MANE = "9-136700401-C-T"
# A missense with CADD 30 and no REVEL score.
_CADD_ONLY = "9-136700501-C-T"
# A common missense (gnomAD 8%) that ClinVar calls likely pathogenic.
_COMMON_LIKELY_PATHOGENIC = "9-136700601-C-T"
# A missense whose only MANE transcript is MANE Plus Clinical; its canonical transcript is not MANE.
_MANE_PLUS_CLINICAL_ONLY = "9-136700701-C-T"
# A missense on its canonical transcript, which is not MANE: no MANE transcript at all.
_NO_MANE = "9-136700801-C-T"

_VARIANTS: dict[str, list[str]] = {
    _HIGH_OFF_MANE: [
        _csq("stop_gained", "HIGH", "SYN29A", "ENST_SYN29A_1", cadd="35"),
        _csq("intron_variant", "MODIFIER", "SYN29A", "ENST_SYN29A_2", mane="NM_SYN29A.1", cadd="35"),
    ],
    _MANE_HIGH: [
        _csq("stop_gained", "HIGH", "SYN29B", "ENST_SYN29B_1", mane="NM_SYN29B.1", lof="HC", cadd="40"),
    ],
    _LOF_OFF_MANE: [
        _csq("missense_variant", "MODERATE", "SYN29C", "ENST_SYN29C_1", mane="NM_SYN29C.1", revel="0.3", cadd="24"),
        _csq("stop_gained", "HIGH", "SYN29C", "ENST_SYN29C_2", lof="HC", cadd="24"),
    ],
    _REVEL_OFF_MANE: [
        _csq("synonymous_variant", "LOW", "SYN29D", "ENST_SYN29D_1", mane="NM_SYN29D.1", cadd="12"),
        _csq("missense_variant", "MODERATE", "SYN29D", "ENST_SYN29D_2", revel="0.9", cadd="12"),
    ],
    _CADD_ONLY: [
        _csq("missense_variant", "MODERATE", "SYN29E", "ENST_SYN29E_1", mane="NM_SYN29E.1", cadd="30"),
    ],
    _COMMON_LIKELY_PATHOGENIC: [
        _csq(
            "missense_variant", "MODERATE", "SYN29F", "ENST_SYN29F_1", mane="NM_SYN29F.1",
            revel="0.7", cadd="28", af="0.08", clinvar="likely_pathogenic",
        ),
        _csq(
            "missense_variant", "MODERATE", "SYN29F", "ENST_SYN29F_2",
            revel="0.6", cadd="28", af="0.08", clinvar="likely_pathogenic",
        ),
    ],
    _MANE_PLUS_CLINICAL_ONLY: [
        _csq("missense_variant", "MODERATE", "SYN29G", "ENST_SYN29G_1", mane_plus_clinical="NM_SYN29G.1", cadd="26"),
        _csq("intron_variant", "MODIFIER", "SYN29G", "ENST_SYN29G_2", canonical=True, cadd="26"),
    ],
    _NO_MANE: [
        _csq("missense_variant", "MODERATE", "SYN29H", "ENST_SYN29H_1", canonical=True, cadd="26"),
    ],
}
_ALL = set(_VARIANTS)

# The family search's filters (the family endpoint's names) and the variants it keeps.
_CASES = [
    pytest.param({}, _ALL, id="no-filter"),
    pytest.param({"impact": ["HIGH"]}, {_HIGH_OFF_MANE, _MANE_HIGH, _LOF_OFF_MANE}, id="high"),
    # MANE Select or MANE Plus Clinical, on any transcript of the variant (CLIN-5).
    pytest.param({"mane_only": True}, _ALL - {_NO_MANE}, id="mane-only"),
    pytest.param({"mane_only": True, "impact": ["HIGH"]}, {_MANE_HIGH}, id="mane-only-and-high"),
    pytest.param({"canonical_only": True, "impact": ["HIGH"]}, {_MANE_HIGH}, id="canonical-only-and-high"),
    pytest.param({"lof_only": True, "impact": ["HIGH"]}, {_MANE_HIGH, _LOF_OFF_MANE}, id="lof-only-and-high"),
    pytest.param({"lof_only": True, "impact": ["MODERATE"]}, set(), id="lof-only-and-moderate"),
    pytest.param({"mane_only": True, "min_revel": 0.5}, {_COMMON_LIKELY_PATHOGENIC}, id="mane-only-and-revel"),
    pytest.param(
        {"min_cadd": 25.0, "min_revel": 0.5},
        _ALL - {_LOF_OFF_MANE},
        id="any-in-silico-predictor",
    ),
    pytest.param({"max_gnomad_af": 0.01}, _ALL - {_COMMON_LIKELY_PATHOGENIC}, id="frequency"),
    pytest.param(
        {"max_gnomad_af": 0.01, "clinvar_overrides_frequency": True}, _ALL, id="frequency-clinvar-rescue"
    ),
    pytest.param(
        {"mane_only": True, "clinvar": ["Likely_pathogenic"]},
        {_COMMON_LIKELY_PATHOGENIC},
        id="mane-only-and-clinvar",
    ),
]


@pytest.fixture(scope="module")
def stored_family() -> dict[str, str]:
    """Write the eight variants once, for a fresh family in a fresh project."""
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    family_uuid, project = str(uuid4()), str(uuid4())
    records = [
        small_variant(variant_id, vep_annotations(_CSQ_FORMAT, entries), sample=_SAMPLE)
        for variant_id, entries in _VARIANTS.items()
    ]

    async def _write() -> None:
        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        await insert_small_variant_records(_ASSEMBLY, family_uuid, [project], records)

    # The schema and tables are made here, so the test does not depend on an earlier test (or
    # the app's startup) having made them.
    run_closing_stores(_write, init_schema=True)
    return {"family_uuid": family_uuid, "project": project}


@pytest.mark.parametrize(("family_filters", "expected"), _CASES)
def test_explorer_keeps_what_the_family_search_keeps(
    monkeypatch: pytest.MonkeyPatch,
    stored_family: dict[str, str],
    family_filters: dict[str, Any],
    expected: set[str],
) -> None:
    import backend.app.services.clickhouse_family_variants as family_variants
    import backend.app.services.variant_explorer_service as explorer
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clickhouse_variant_queries import _small_record_matches
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    family_uuid = stored_family["family_uuid"]
    context = proband_context(
        family_uuid, f"CL29-{family_uuid[:8]}", stored_family["project"], sample=_SAMPLE,
        assembly_id=str(uuid4()), assembly_name=_ASSEMBLY,
    )
    scope = explorer.ExplorerScope(
        assembly_id=str(uuid4()), assembly_name=_ASSEMBLY, project_ids=[stored_family["project"]]
    )

    async def _fake_scope(session, user, assembly_id):
        return scope

    async def _no_hydration(session, *, context, variants):
        return None

    monkeypatch.setattr(explorer, "resolve_scope", _fake_scope)
    monkeypatch.setattr(family_variants, "_hydrate_small_variant_outs", _no_hydration)

    # The explorer names the impact and consequence lists in the plural.
    explorer_filters = {
        {"impact": "impacts", "effect": "effects"}.get(name, name): value
        for name, value in family_filters.items()
    }
    locus = {"chromosome": _LOCUS["chr"], "start": _LOCUS["start"], "end": _LOCUS["end"]}

    async def _run() -> dict[str, Any]:
        async with get_postgres_sessionmaker()() as session:
            family_page = await family_variants.get_family_small_variants_page(
                session, context=context, page=1, page_size=100, **family_filters
            )
            stored = await family_variants._fetch_small_variant_rows(
                context, SmallVariantQueryFilters(page=1, page_size=100)
            )
            python_filters = SmallVariantQueryFilters(page=1, page_size=100, **family_filters)
            explorer_page = await explorer.search_global_small_variants(
                session,
                user=None,
                filters=explorer.GlobalVariantFilters(**explorer_filters),
                assembly_id=scope.assembly_id,
            )
            explorer_locus_page = await explorer.search_global_small_variants(
                session,
                user=None,
                filters=explorer.GlobalVariantFilters(**explorer_filters, **locus),
                assembly_id=scope.assembly_id,
            )
            explorer_export = await explorer.export_global_small_variants(
                session,
                user=None,
                filters=explorer.GlobalVariantFilters(**explorer_filters),
                assembly_id=scope.assembly_id,
            )
        return {
            "family page": (family_page.total, {variant.id for variant in family_page.variants}),
            "family Python matcher": {
                record.variant_id
                for record in stored
                if _small_record_matches(record, python_filters, [], [], [])
            },
            "explorer": (explorer_page.total, {row.variant_id for row in explorer_page.variants}),
            "explorer at the locus": (
                explorer_locus_page.total,
                {row.variant_id for row in explorer_locus_page.variants},
            ),
            "explorer export": {row.variant_id for row in explorer_export.rows},
        }

    results = run_closing_stores(_run)
    assert results == {
        "family page": (len(expected), expected),
        "family Python matcher": expected,
        "explorer": (len(expected), expected),
        "explorer at the locus": (len(expected), expected),
        "explorer export": expected,
    }
