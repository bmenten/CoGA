"""The NIPT variant list and its CSV export read one search.

``GET /families/{family_id}/nipt/variants`` and ``…/nipt/variants/export`` read their search
through one dependency, ``_nipt_variant_search``: a filter added to one route alone would make
the downloaded file differ from the list on screen. The tests pin the declaration (the same
parameters on both routes, none declared by a route itself but its paging and project), the
wiring (a request that sets every parameter hands the analysis the same search from both, the
export asking for every classified variant), the file (the small-variant export's columns but
the genotypes and the members without calls, then the NIPT classification), the ClinVar (CoGA)
the analysis read on the page and in the file, and how a list cut at the classification limit
is named. The family and its variants are synthetic.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.routers import families_nipt as nipt_router
from backend.app.routers.families_small_variants import _FAMILY_EXPORT_COLUMNS
from backend.app.schemas import ClinvarCogaOut, GenotypeOut, SmallVariantReviewOut, VariantOut
from backend.app.services.access_control import CurrentUser
from backend.app.services.clickhouse_family_variants import CfdnaRecurrence
from backend.app.services.nipt import NiptTrio, multiple_pregnancy_note
from backend.app.services.nipt_analysis import FetalFractionEstimate, NiptClassification, NiptSiteObservation
from backend.app.services.nipt_service import (
    NIPT_VARIANT_FETCH_LIMIT,
    NiptClassifiedVariant,
    NiptRecessiveGene,
    NiptRecurrence,
    NiptVariantsResult,
)
from backend.app.services.nipt_triage import DeNovoTriage, RecessiveAllele, RecessiveGeneRisk

_LIST = "/api/families/FAM1/nipt/variants"
_EXPORT = "/api/families/FAM1/nipt/variants/export"
_USER = CurrentUser(
    id="u1",
    username="reviewer",
    email="reviewer@example.org",
    role="viewer",
    created_at=datetime(2026, 11, 21, tzinfo=timezone.utc),
)

# A value for every search parameter, as a request sends it.
_SEARCH: list[tuple[str, str]] = [
    # The pregnancy whose monogenic NIPT the list reads: a family holds one per pregnancy.
    ("analysis", "monogenic_nipt:ZW1"),
    ("category", "3"),
    ("category", "7"),
    ("min_confidence", "0.6"),
    ("inheritance", "de_novo"),
    ("include_not_inherited", "true"),
    ("de_novo_priority", "medium"),
    ("gene", "GENEA"),
    ("exclude_gene", "GENEB"),
    ("panel_id", "panel-1"),
    ("chr", "1"),
    ("start", "100"),
    ("end", "900"),
    ("intervals", "1:100-900"),
    ("exclude_intervals", "1:500-510"),
    ("ps", "12"),
    ("type", "SNV"),
    ("source", "nipt"),
    ("transcript", "ENST00000000001"),
    ("rsid", "rs1"),
    ("hgvsc", "c.1A>G"),
    ("hgvsp", "p.Met1?"),
    ("impact", "HIGH"),
    ("effect", "missense_variant"),
    ("clinvar", "pathogenic"),
    ("exclude_clinvar", "benign"),
    ("clinvar_pathogenic_override", "true"),
    ("sift", "deleterious"),
    ("polyphen", "probably_damaging"),
    ("max_gnomad_af", "0.01"),
    ("max_gnomad_exomes_af", "0.02"),
    ("max_gnomad_genomes_af", "0.03"),
    ("max_gnomad_popmax_af", "0.04"),
    ("max_topmed_af", "0.05"),
    ("max_gnomad_ac", "6"),
    ("max_gnomad_hom_count", "7"),
    ("max_gnomad_hemi_count", "8"),
    ("min_cadd", "20"),
    ("min_revel", "0.5"),
    ("min_spliceai", "0.2"),
    ("canonical_only", "true"),
    ("mane_only", "true"),
    ("lof_only", "true"),
    ("review_tag", "report"),
    ("exclude_review_tag", "excluded"),
    ("max_unexplained_recurrence", "2"),
    ("exclude_cohort_common", "true"),
]


def _fetal_fraction() -> FetalFractionEstimate:
    return FetalFractionEstimate(
        ff=0.112,
        ff_computed=0.112,
        ff_median=0.11,
        ci_low=0.1,
        ci_high=0.12,
        n_sites=40,
        method="category7_pooled",
        low_confidence=False,
    )


def _classified(variant_id: str, *, gene: str = "GENEA", de_novo: bool = False) -> NiptClassifiedVariant:
    chrom, pos, ref, alt = variant_id.split("-")
    return NiptClassifiedVariant(
        record=SimpleNamespace(variant_id=variant_id),  # type: ignore[arg-type]
        clinvar_coga=None,
        classification=NiptClassification(
            variant_id=variant_id,
            category=4,
            category_label="maternal het, inherited (hom fetus)",
            maternal_state="het",
            fetal_inheritance="maternal_inherited_hom",
            expected_vaf=0.561,
            observed_vaf=0.541,
            confidence=0.6,
            runner_up_category=3,
            runner_up_confidence=0.3,
            flags=["ambiguous", "maternal_inference_indel"],
            maternal_allele_probability=0.801,
            fetal_hom_alt_probability=0.603,
            fetal_genotype_posterior={"het": 0.397, "hom_alt": 0.603},
            quality_failures=["quality:low_alt_reads"],
        ),
        variant_out=VariantOut(
            id=variant_id,
            chr=chrom,
            start=int(pos),
            end=int(pos),
            length=1,
            type="SNV",
            ref=ref,
            alt=alt,
            gene=gene,
            genotypes=[GenotypeOut(sample="MOTHER", gt="0/1"), GenotypeOut(sample="FATHER1", gt="0/1")],
            review=SmallVariantReviewOut(variant_id=variant_id, classification="VUS", tags=["report"]),
        ),
        site=NiptSiteObservation(
            variant_id=variant_id,
            chrom=chrom,
            pos=int(pos),
            is_autosomal=True,
            cf_present=True,
            cf_dp=2086,
            cf_alt_reads=1129,
            cf_vaf=0.541,
            cf_qual=60.0,
            cf_coverage_dp=2086,
            father_state="het",
            father_dp=299,
            father_vaf=0.488,
            father_alt_reads=146,
            # The father's VCF writes this allele another way: his call is read off that record.
            representation="father_call_other_representation",
            represented_by="9-101-G-A",
        ),
        de_novo=DeNovoTriage(window="strict", score=7, label="high", reasons=["rare", "LoF"]) if de_novo else None,
        recurrence=NiptRecurrence(
            other_samples=17,
            counts=CfdnaRecurrence(carriers=15, maternal=12, paternal_low_vaf=1, unexplained_low_vaf=2),
            protection="clinvar",
            plasma_calls=True,
            father_carries=True,
        ),
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, list[dict[str, Any]]]]:
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True
    calls: list[dict[str, Any]] = []
    result: dict[str, NiptVariantsResult] = {}

    async def session():
        yield SimpleNamespace()

    async def user():
        return _USER

    async def fake_variants(_session, **kwargs):
        calls.append(kwargs)
        return result["next"]

    result["next"] = NiptVariantsResult(
        fetal_fraction=_fetal_fraction(),
        total=2,
        variants=[_classified("1-100-A-G", de_novo=True), _classified("2-200-C-T", gene="=HYPERLINK(1)")],
        trio=NiptTrio(father_sample_id="FATHER1", cfdna_sample_id="MOTHER", fetus_sample_ids=("EMB1",)),
    )
    monkeypatch.setattr(nipt_router, "get_family_nipt_variants", fake_variants)
    app.dependency_overrides[get_postgres_session] = session
    app.dependency_overrides[get_current_user] = user
    with TestClient(app) as test_client:
        test_client.result = result  # type: ignore[attr-defined]
        yield test_client, calls
    app.dependency_overrides = original_overrides


def _route(path: str) -> APIRoute:
    return next(route for route in nipt_router.router.routes if isinstance(route, APIRoute) and route.path == path)


def _rows(response) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(response.text)))


def test_both_routes_read_the_search_from_one_dependency() -> None:
    # Each route declares only its paging and project itself; every filter comes from
    # _nipt_variant_search, so the export cannot read a search the list does not.
    own = {
        path: {param.alias for param in _route(path).dependant.query_params}
        for path in ("/{family_id}/nipt/variants", "/{family_id}/nipt/variants/export")
    }
    assert own == {
        "/{family_id}/nipt/variants": {"page", "page_size", "project_id"},
        "/{family_id}/nipt/variants/export": {"project_id"},
    }
    paths = app.openapi()["paths"]

    def parameters(path: str) -> dict[str, dict]:
        return {
            parameter["name"]: parameter
            for parameter in paths[f"/api/families{path}"]["get"]["parameters"]
            if parameter["name"] not in {"page", "page_size"}
        }

    listed = parameters("/{family_id}/nipt/variants")
    exported = parameters("/{family_id}/nipt/variants/export")
    assert listed == exported
    assert {name for name, _value in _SEARCH} | {"family_id", "project_id"} == set(listed)


def test_the_export_hands_the_analysis_the_search_the_list_hands_it(client) -> None:
    test_client, calls = client

    assert test_client.get(_LIST, params=[*_SEARCH, ("page", "2"), ("page_size", "50")]).status_code == 200
    assert test_client.get(_EXPORT, params=_SEARCH).status_code == 200

    listed, exported = calls
    assert (listed.pop("page"), listed.pop("page_size")) == (2, 50)
    # The export asks for every classified variant, and is named apart on the audit trail.
    assert (exported.pop("page"), exported.pop("page_size"), exported.pop("panel_search")) == (
        1,
        None,
        "nipt_variants_export",
    )
    assert listed == exported
    assert exported["query_filters"]["panel_id"] == "panel-1"
    assert exported["categories"] == [3, 7]
    assert (exported["review_tags"], exported["exclude_review_tags"]) == (["report"], ["excluded"])


def test_the_file_holds_the_small_variant_columns_then_the_nipt_classification(client) -> None:
    test_client, _calls = client

    response = test_client.get(_EXPORT)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    header = next(csv.reader(io.StringIO(response.text)))
    small = [label for _field, label in _FAMILY_EXPORT_COLUMNS]
    # No genotypes: the plasma's GT is no genotype and the fetus has no sample of its own.
    assert "Genotypes" not in header and "Members without calls" not in header
    assert header[: len(small) - 2] == [label for label in small if label not in {"Genotypes", "Members without calls"}]
    assert header == [label for _field, label in nipt_router.NIPT_EXPORT_COLUMNS]
    assert len(header) == len(set(header))

    first, second = _rows(response)
    assert (first["Chromosome"], first["Start"], first["Gene"]) == ("1", "100", "GENEA")
    assert (first["Classification text"], first["Tags"]) == ("VUS", "report")
    assert {key: first[key] for key in header[len(small) - 2 :]} == {
        "Fetal fraction": "0.112",
        "Fetal fraction low confidence": "no",
        "Multiple pregnancy note": "",
        "NIPT category": "4",
        "NIPT category label": "maternal het, inherited (hom fetus)",
        "Maternal state": "het",
        "Fetal inheritance": "maternal_inherited_hom",
        "NIPT confidence": "0.6",
        "Runner-up category": "3",
        "Runner-up confidence": "0.3",
        "Observed VAF": "0.541",
        "Expected VAF": "0.561",
        "Plasma alt reads": "1129",
        "Plasma depth": "2086",
        "Plasma depth estimated": "no",
        "Plasma call read from": "",
        "Father state": "het",
        "Father VAF": "0.488",
        "Father depth": "299",
        "Father alt reads": "146",
        "Father call read from": "9-101-G-A",
        "P(paternal allele inherited)": "",
        "P(maternal allele inherited)": "0.801",
        "P(fetus homozygous)": "0.603",
        "Fetal genotype posterior": "het=0.397; hom_alt=0.603",
        "NIPT flags": "ambiguous; maternal_inference_indel",
        "Quality failures": "quality:low_alt_reads",
        "De novo priority": "high",
        "De novo score": "7",
        "De novo window": "strict",
        "De novo reasons": "rare; LoF",
        "Other cfDNA samples": "17",
        "Recurrence carriers": "15",
        "Recurrence maternal": "12",
        "Recurrence paternal low-VAF": "1",
        "Recurrence unexplained low-VAF": "2",
        "Recurrence unexplained median VAF": "",
        "Recurrence protection": "clinvar",
    }
    # Outside the de novo view a variant has no triage; the fetal fraction is on every row,
    # and a value a spreadsheet would read as a formula is written as text.
    assert [second[key] for key in ("De novo priority", "De novo score", "De novo window", "De novo reasons")] == [""] * 4
    assert second["Fetal fraction"] == "0.112"
    assert second["Gene"] == "'=HYPERLINK(1)"


def test_the_list_of_a_twin_pregnancy_carries_its_note_on_every_row(client) -> None:
    # A result of a multiple pregnancy may be in one fetus or more: the file says so, as the
    # page, the report and the signed record do.
    test_client, _calls = client
    test_client.result["next"] = NiptVariantsResult(  # type: ignore[attr-defined]
        fetal_fraction=_fetal_fraction(),
        total=2,
        variants=[_classified("1-100-A-G"), _classified("2-200-C-T")],
        trio=NiptTrio(father_sample_id="FATHER1", cfdna_sample_id="MOTHER", fetus_sample_ids=("EMB1", "EMB2")),
    )

    rows = _rows(test_client.get(_EXPORT))

    assert {row["Multiple pregnancy note"] for row in rows} == {multiple_pregnancy_note(2)}
    assert len(rows) == 2


def test_the_list_and_the_file_show_the_clinvar_coga_the_analysis_read(client) -> None:
    # Each variant's ClinVar in CoGA's release the analysis read, per ALT, and the two releases
    # the page names (design B7): what the page's chip and mark, its ACMG evaluation and the
    # file's ClinVar (CoGA) column show.
    test_client, _calls = client
    release = date(2026, 11, 22)
    item = _classified("1-100-A-G")
    assert item.variant_out is not None
    item.variant_out.clinvar_coga = [
        ClinvarCogaOut(alt="G", status="in_release", release=release, classification="Likely_pathogenic", stars=1)
    ]
    test_client.result["next"] = NiptVariantsResult(  # type: ignore[attr-defined]
        fetal_fraction=_fetal_fraction(),
        total=1,
        variants=[item],
        trio=NiptTrio(father_sample_id="FATHER1", cfdna_sample_id="MOTHER", fetus_sample_ids=("EMB1",)),
        clinvar_coga_release=release,
        clinvar_annotation_release=date(2026, 5, 1),
    )

    page = test_client.get(_LIST).json()
    [row] = _rows(test_client.get(_EXPORT))

    assert (page["clinvar_coga_release"], page["clinvar_annotation_release"]) == ("2026-11-22", "2026-05-01")
    [value] = page["variants"][0]["clinvar_coga"]
    assert (value["alt"], value["status"], value["classification"], value["stars"]) == (
        "G",
        "in_release",
        "Likely_pathogenic",
        1,
    )
    assert row["ClinVar (CoGA)"] == "G: Likely_pathogenic (2026-11-22)"


def test_the_recessive_view_lists_its_genes_each_with_its_variants(client) -> None:
    # The recessive view's results are its genes: the page holds them as groups, each with its
    # carrier variants and its fetal status, and no variant on its own; the file holds their
    # variants, gene by gene.
    test_client, _calls = client
    maternal, paternal = _classified("1-100-A-G"), _classified("1-300-C-T")
    test_client.result["next"] = NiptVariantsResult(  # type: ignore[attr-defined]
        fetal_fraction=_fetal_fraction(),
        total=1,
        variants=[],
        trio=NiptTrio(father_sample_id="FATHER1", cfdna_sample_id="MOTHER"),
        recessive_genes=[
            NiptRecessiveGene(
                risk=RecessiveGeneRisk(
                    gene="GENEA",
                    maternal=[RecessiveAllele(variant_id="1-100-A-G", inherited_probability=0.45, category=2)],
                    paternal=[RecessiveAllele(variant_id="1-300-C-T", inherited_probability=0.01, category=8)],
                    risk=0.0045,
                    maternal_variant_id="1-100-A-G",
                    paternal_variant_id="1-300-C-T",
                    status="carrier_or_unaffected",
                    mode="autosomal",
                    carrier_probability=0.45,
                    unaffected_probability=0.5455,
                ),
                gene_id="ENSG0A",
                variants=[maternal, paternal],
            )
        ],
        gene_total=1,
        variant_total=2,
    )
    page = test_client.get(_LIST, params={"inheritance": "recessive_at_risk"}).json()
    assert (page["variants"], page["total"], page["gene_total"], page["variant_total"]) == ([], 1, 1, 2)
    [group] = page["variant_groups"]
    assert (group["group_type"], group["group_key"], group["gene"], group["gene_id"]) == (
        "nipt_recessive_gene", "nipt-recessive-gene:ENSG0A:autosomal", "GENEA", "ENSG0A",
    )
    assert (group["status"], group["mode"], group["maternal_variant_id"]) == ("carrier_or_unaffected", "autosomal", "1-100-A-G")
    assert [variant["_id"] for variant in group["variants"]] == ["1-100-A-G", "1-300-C-T"]
    assert group["variants"][0]["nipt"]["category"] == 4
    exported = test_client.get(_EXPORT, params={"inheritance": "recessive_at_risk"})
    assert [(row["Chromosome"], row["Start"]) for row in _rows(exported)] == [("1", "100"), ("1", "300")]


def test_a_complete_list_is_saved_under_its_own_name(client) -> None:
    test_client, _calls = client

    response = test_client.get(_EXPORT)

    assert response.headers["content-disposition"] == 'attachment; filename="family-FAM1-nipt-variants.csv"'
    assert response.headers["x-coga-export-rows"] == "2"
    assert response.headers["x-coga-export-truncated"] == "false"
    assert response.headers["x-coga-export-limit"] == str(NIPT_VARIANT_FETCH_LIMIT)
    assert response.headers["x-coga-export-truncated-reason"] == ""


def test_a_list_cut_at_the_classification_limit_says_so_in_the_file_name_and_headers(client) -> None:
    # The list classifies the first variants of its search: past the limit a match can be
    # missing anywhere after where it stopped, however few rows the file holds.
    test_client, _calls = client
    test_client.result["next"] = NiptVariantsResult(  # type: ignore[attr-defined]
        fetal_fraction=_fetal_fraction(),
        total=1,
        variants=[_classified("1-100-A-G")],
        trio=NiptTrio(father_sample_id="FATHER1", cfdna_sample_id="MOTHER", fetus_sample_ids=("EMB1",)),
        total_is_estimated=True,
        count_limit=NIPT_VARIANT_FETCH_LIMIT,
    )

    response = test_client.get(_EXPORT)

    assert response.headers["content-disposition"] == (
        'attachment; filename="family-FAM1-nipt-variants-TRUNCATED-partial-search.csv"'
    )
    assert response.headers["x-coga-export-truncated"] == "true"
    assert response.headers["x-coga-export-truncated-reason"] == "candidate-limit"
    assert response.headers["x-coga-export-limit"] == str(NIPT_VARIANT_FETCH_LIMIT)
    assert len(_rows(response)) == 1


def test_the_list_of_a_pregnancy_is_saved_under_its_pregnancy_and_read_for_it(client) -> None:
    # A family holds a monogenic NIPT per pregnancy: the export reads the one the request names,
    # and its file names that pregnancy.
    test_client, calls = client
    test_client.result["next"] = NiptVariantsResult(  # type: ignore[attr-defined]
        fetal_fraction=_fetal_fraction(),
        total=1,
        variants=[_classified("1-100-A-G")],
        trio=NiptTrio(
            father_sample_id="FATHER1",
            cfdna_sample_id="PLASMA2",
            fetus_sample_ids=("FETUS2",),
            analysis="monogenic_nipt:ZW2",
        ),
    )

    response = test_client.get(f"{_EXPORT}?analysis=monogenic_nipt:ZW2")

    assert response.headers["content-disposition"] == 'attachment; filename="family-FAM1-ZW2-nipt-variants.csv"'
    assert calls[-1]["analysis"] == "monogenic_nipt:ZW2"


def test_a_fetal_fraction_that_was_not_estimated_is_an_empty_cell() -> None:
    # Without an estimate the analysis holds ff = 0.0 for its arithmetic: never written as one.
    from backend.app.schemas.nipt import NiptFetalFractionOut

    not_estimated = NiptFetalFractionOut(
        ff=0.0, ff_computed=None, ff_median=0.08, ci_low=0.01, ci_high=0.2, n_sites=3,
        method="category7_pooled", low_confidence=True,
    )
    row = SimpleNamespace()
    assert nipt_router._nipt_export_cell(row, "fetal_fraction", not_estimated, None) == ""  # type: ignore[arg-type]
    estimated = not_estimated.model_copy(update={"ff": 0.112, "ff_computed": 0.112})
    assert nipt_router._nipt_export_cell(row, "fetal_fraction", estimated, None) == "0.112"  # type: ignore[arg-type]
