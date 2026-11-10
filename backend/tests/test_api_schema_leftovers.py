"""The API serves and takes only what the server sets and reads (ENG-4).

- A gene profile's homologs carry no percent identity or coverage: dbNSFP, the one source of
  homologs, names a mouse and a zebrafish gene and sets neither.
- The UCSC assembly catalogue does not say whether an assembly has cytobands and genes:
  nothing sets the two fields, which would read true for every assembly, also one UCSC has no
  cytoband or gene table for.
- One model for a couple in a request (the structure edit's and the manual PED family's), and
  one for an HPO term in a response (a Monarch match's terms, a gene's Monarch phenotypes, a
  variant's phenotype matches).
- The clinical-CNV track's ``end`` is an integer, as ``start`` is: a value that is not one is
  refused with the 422 a bad ``start`` gets.
- A member edit takes no ``clear_existing_genomic_data`` (the structure edit, which keeps the
  field, is the one that clears data).
- The SV review save takes no ``acmg`` or ``compound_het``, a small variant's. A request that
  sends them, or any field the save does not take, is refused (422) naming the field, as the
  small-variant save's is.
- A gene's homologs carry no Ensembl gene id and no "in the platform" flag: no source sets
  either, and no screen shows homologs.
- The small-variant review save takes no ``cnv_acmg``, a structural variant's CNV
  classification. No page sends it; a request that does is refused (422) naming the field, as
  is any field the save does not take, so that a client is told its value would go nowhere.

All data here are synthetic.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_admin_user, get_current_user
from backend.app.main import app
from backend.app.routers import cnvs as cnvs_router
from backend.app.routers import families_small_variants as small_router
from backend.app.routers import families_structural_variants as sv_router
from backend.app.schemas import (
    FamilyMemberUpdate,
    FamilyStructureUpdate,
    GeneHomologOut,
    SmallVariantReviewOut,
    SmallVariantReviewUpdate,
    StructuralVariantReviewUpdate,
)
from backend.app.services import gene_info_bulk_sources, reference_source_service
from backend.app.services.access_control import CurrentUser

FAMILY = "FAM_ENG4"


def _schemas() -> dict[str, Any]:
    return app.openapi()["components"]["schemas"]


def _properties(model: str) -> dict[str, Any]:
    return _schemas()[model]["properties"]


def _items(model: str, field: str) -> str:
    """The model a list field of ``model`` holds, as the schema names it."""
    return _properties(model)[field]["items"]["$ref"].rsplit("/", 1)[-1]


def _request_body(path: str, method: str = "put") -> str:
    operation = app.openapi()["paths"][path][method]
    return operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]


def _admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


class _Session:
    async def rollback(self) -> None:
        return None


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """A signed-in administrator; every route's service is replaced by the test."""
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    async def session() -> Any:
        yield _Session()

    async def user() -> CurrentUser:
        return _admin()

    app.dependency_overrides[get_postgres_session] = session
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_current_admin_user] = user
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides = original_overrides


# --------------------------------------------------------------------------- #
# Response fields no code set
# --------------------------------------------------------------------------- #


def test_a_gene_homolog_serves_no_percent_identity_or_coverage() -> None:
    assert not {"percent_id", "percent_coverage"} & set(_properties("GeneHomologOut"))

    # The one source of homologs, dbNSFP's mouse and zebrafish genes, writes neither.
    written = gene_info_bulk_sources._dbnsfp_homologs(
        {"mgi_mouse_gene": "Brca1", "zfin_zebrafish_gene": "brca1"}
    )
    assert [entry["symbol"] for entry in written] == ["Brca1", "brca1"]
    assert all(set(entry) <= set(GeneHomologOut.model_fields) for entry in written)


def test_a_gene_homolog_serves_no_ensembl_id_or_platform_flag() -> None:
    assert not {"ensembl_gene_id", "in_platform"} & set(_properties("GeneHomologOut"))

    # dbNSFP, the one source of homologs, names a mouse and a zebrafish gene by symbol only.
    written = gene_info_bulk_sources._dbnsfp_homologs(
        {"mgi_mouse_gene": "Brca1", "zfin_zebrafish_gene": "brca1"}
    )
    assert [set(entry) for entry in written] == [{"species_name", "common_name", "symbol", "homology_type"}] * 2


@pytest.mark.asyncio
async def test_the_ucsc_assembly_catalogue_claims_no_cytobands_or_genes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not {"cytobands_available", "genes_available"} & set(_properties("ReferenceImportSourceAssemblyOut"))

    async def catalogue() -> list[dict[str, Any]]:
        # T2T: UCSC has no cytoband table for it, nor (at hs1) a gene table.
        return [
            {
                "ucsc_genome": "hs1",
                "tax_id": 9606,
                "scientific_name": "Homo sapiens",
                "common_name": "human",
                "description": "Jan. 2022 (T2T CHM13v2.0/hs1)",
                "source_name": "T2T Consortium",
                "release_date": date(2022, 1, 1),
                "assembly_name": "T2T-CHM13",
            }
        ]

    monkeypatch.setattr(reference_source_service, "_load_catalog_entries", catalogue)

    (assembly,) = await reference_source_service.list_reference_source_assemblies(tax_id=9606)

    assert assembly.ucsc_genome == "hs1"
    assert not {"cytobands_available", "genes_available"} & set(assembly.model_dump())


# --------------------------------------------------------------------------- #
# Duplicate models
# --------------------------------------------------------------------------- #


def test_one_model_for_a_couple_in_a_request() -> None:
    assert "ManualPedCoupleCreate" not in _schemas()
    assert _items("FamilyStructureRelationshipsUpdate", "couples") == "FamilyStructureCoupleUpdate"
    assert _items("ManualPedFamilyCreate", "couples") == "FamilyStructureCoupleUpdate"


def test_one_model_for_an_hpo_term_in_a_response() -> None:
    assert "MonarchPhenotypeMatchOut" not in _schemas()
    assert _items("PhenotypeMatchResultOut", "matching_phenotypes") == "PhenotypeTermRefOut"
    assert _items("PhenotypeMatchResultOut", "extra_phenotypes") == "PhenotypeTermRefOut"
    assert _items("GeneMonarchAssociationOut", "matched_phenotypes") == "PhenotypeTermRefOut"
    assert _items("VariantPriorityOut", "phenotype_matches") == "PhenotypeTermRefOut"


# --------------------------------------------------------------------------- #
# The clinical-CNV track's end
# --------------------------------------------------------------------------- #


def _cnv_reads(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []

    async def read(_session: Any, **kwargs: Any) -> list[Any]:
        reads.append(kwargs)
        return []

    monkeypatch.setattr(cnvs_router, "get_clinical_cnvs_data", read)
    return reads


def test_the_cnv_track_reads_an_integer_end_as_it_reads_start(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = _cnv_reads(monkeypatch)

    response = client.get("/api/cnvs/GRCh38/chr1", params={"start": 1000, "end": 250000})

    assert response.status_code == 200
    assert reads == [{"assembly": "GRCh38", "chrom": "chr1", "start": 1000, "end": 250000}]
    assert _cnv_end_parameter()["schema"]["type"] == "integer"


def _cnv_end_parameter() -> dict[str, Any]:
    parameters = app.openapi()["paths"]["/api/cnvs/{assembly}/{chrom}"]["get"]["parameters"]
    return next(parameter for parameter in parameters if parameter["name"] == "end")


@pytest.mark.parametrize("value", ["abc", "1.5", "1000:2000", "-1"])
def test_an_end_that_is_no_position_is_refused_as_a_bad_start_is(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    reads = _cnv_reads(monkeypatch)

    bad_start = client.get("/api/cnvs/GRCh38/chr1", params={"start": value, "end": 250000})
    bad_end = client.get("/api/cnvs/GRCh38/chr1", params={"start": 1000, "end": value})

    assert bad_start.status_code == 422
    assert bad_end.status_code == bad_start.status_code
    assert [error["loc"] for error in bad_end.json()["detail"]] == [["query", "end"]]
    assert reads == []


# --------------------------------------------------------------------------- #
# Request fields no route read
# --------------------------------------------------------------------------- #


def test_a_member_edit_takes_no_request_to_clear_the_data() -> None:
    assert "clear_existing_genomic_data" not in _properties("FamilyMemberUpdate")
    assert "clear_existing_genomic_data" not in FamilyMemberUpdate.model_fields
    # The structure edit keeps it: there it clears the family's imported data.
    assert "clear_existing_genomic_data" in _properties("FamilyStructureUpdate")
    assert "clear_existing_genomic_data" in FamilyStructureUpdate.model_fields


def test_the_sv_review_request_takes_no_small_variant_acmg_or_compound_het() -> None:
    sv_body = _properties(_request_body("/api/families/{family_id}/structural-variants/{variant_id}/review"))
    small_body = _properties(_request_body("/api/families/{family_id}/small-variants/{variant_id}/review"))

    assert not {"acmg", "compound_het"} & set(sv_body)
    assert {"expected_updated_at", "classification", "tags", "note", "cnv_acmg", "source"} <= set(sv_body)
    # The small-variant save keeps both: it reads them.
    assert {"acmg", "compound_het"} <= set(small_body)
    # A field it does not take is refused, not left out.
    assert _schemas()[_request_body("/api/families/{family_id}/structural-variants/{variant_id}/review")].get(
        "additionalProperties"
    ) is False
    assert StructuralVariantReviewUpdate.model_config.get("extra") == "forbid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("acmg", {"criteria": [{"code": "PVS1", "strength": "very_strong", "accepted": True}]}, id="acmg"),
        pytest.param("compound_het", {"partner_variant_id": "1-100-A-G", "tags": []}, id="compound-het"),
    ],
)
def test_an_sv_review_save_with_a_small_variant_field_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    saved: list[StructuralVariantReviewUpdate] = []

    async def context(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(family_uuid="fam-uuid", family_id=FAMILY)

    async def save(_session: Any, *, payload: StructuralVariantReviewUpdate, variant_id: str, **_kwargs: Any) -> SmallVariantReviewOut:
        saved.append(payload)
        return SmallVariantReviewOut(variant_id=variant_id, tags=list(payload.tags), note=payload.note)

    monkeypatch.setattr(sv_router, "build_family_metadata_context", context)
    monkeypatch.setattr(sv_router, "upsert_structural_variant_review_record", save)

    response = client.put(
        f"/api/families/{FAMILY}/structural-variants/sv1/review",
        json={"tags": ["report"], "note": "Breakpoints checked.", field: value},
    )

    # Refused, naming the field, rather than saved with the value dropped without a word.
    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert (error["type"], error["loc"]) == ("extra_forbidden", ["body", field])
    assert saved == []

    # Without it, the same save is made.
    response = client.put(
        f"/api/families/{FAMILY}/structural-variants/sv1/review",
        json={"tags": ["report"], "note": "Breakpoints checked."},
    )
    assert response.status_code == 200
    assert response.json()["tags"] == ["report"]
    (payload,) = saved
    assert payload.model_fields_set == {"tags", "note"}


def test_the_small_variant_review_request_takes_no_cnv_classification() -> None:
    small_body = _request_body("/api/families/{family_id}/small-variants/{variant_id}/review")
    assert small_body == "SmallVariantReviewUpdate"
    assert "cnv_acmg" not in _properties(small_body)
    # What it takes is what the save reads; any other field is refused, not left out.
    assert {"expected_updated_at", "classification", "tags", "note", "compound_het", "acmg"} == set(
        _properties(small_body)
    )
    assert _schemas()[small_body].get("additionalProperties") is False
    assert SmallVariantReviewUpdate.model_config.get("extra") == "forbid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("cnv_acmg", {"kind": "loss", "criteria": [{"code": "2A", "points": 1.0, "accepted": True}]}, id="cnv-acmg"),
        pytest.param("source", "sniffles", id="an-sv-field"),
    ],
)
def test_a_small_variant_review_save_with_a_field_it_does_not_take_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    saved: list[SmallVariantReviewUpdate] = []

    async def context(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(family_uuid="fam-uuid", family_id=FAMILY)

    async def save(_session: Any, *, payload: SmallVariantReviewUpdate, variant_id: str, **_kwargs: Any) -> SmallVariantReviewOut:
        saved.append(payload)
        return SmallVariantReviewOut(variant_id=variant_id, tags=list(payload.tags), note=payload.note)

    monkeypatch.setattr(small_router, "build_family_metadata_context", context)
    monkeypatch.setattr(small_router, "upsert_small_variant_review_record", save)

    response = client.put(
        f"/api/families/{FAMILY}/small-variants/1-100-A-G/review",
        json={"tags": ["report"], "note": "Checked.", field: value},
    )

    # Refused, naming the field, rather than saved with the value dropped without a word.
    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert (error["type"], error["loc"]) == ("extra_forbidden", ["body", field])
    assert saved == []

    # Without it, the same save is made.
    response = client.put(
        f"/api/families/{FAMILY}/small-variants/1-100-A-G/review",
        json={"tags": ["report"], "note": "Checked."},
    )
    assert response.status_code == 200
    (payload,) = saved
    assert payload.model_fields_set == {"tags", "note"}
