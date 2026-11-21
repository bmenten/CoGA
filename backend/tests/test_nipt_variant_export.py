"""The NIPT variant list and its CSV export read one search.

``GET /families/{family_id}/nipt/variants`` and ``…/nipt/variants/export`` read their search
through one dependency, ``_nipt_variant_search``: a filter added to one route alone would make
the downloaded file differ from the list on screen. The tests pin the declaration (the same
parameters on both routes, none declared by a route itself but its paging and project), the
wiring (a request that sets every parameter hands the analysis the same search from both, the
export asking for every classified variant), the file (the small-variant export's columns but
the genotypes and the members without calls, then the NIPT classification) and how a list cut
at the classification limit is named. The family and its variants are synthetic.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
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
from backend.app.schemas import GenotypeOut, SmallVariantReviewOut, VariantOut
from backend.app.services.access_control import CurrentUser
from backend.app.services.clickhouse_family_variants import CfdnaRecurrence
from backend.app.services.nipt import NiptTrio, multiple_pregnancy_note
from backend.app.services.nipt_analysis import FetalFractionEstimate, NiptClassification, NiptSiteObservation
from backend.app.services.nipt_service import (
    NIPT_VARIANT_FETCH_LIMIT,
    NiptClassifiedVariant,
    NiptRecurrence,
    NiptVariantsResult,
)
from backend.app.services.nipt_triage import DeNovoTriage

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
    ("clinvar_overrides_frequency", "true"),
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
