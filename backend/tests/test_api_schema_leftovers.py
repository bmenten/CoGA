"""The API serves and takes only what the server sets and reads (ENG-4).

- A gene profile's homologs carry no percent identity or coverage: no source set them since
  the Ensembl homology request went (dbNSFP, the one source left, names a mouse and a
  zebrafish gene), so they were always null. A homolog cached with them still loads.
- The UCSC assembly catalogue no longer says that each assembly has cytobands and genes:
  nothing set the two fields, so they read true for every assembly, also one UCSC has no
  cytoband or gene table for.
- One model for a couple in a request (the structure edit's and the manual PED family's), and
  one for an HPO term in a response (a Monarch match's terms, a gene's Monarch phenotypes, a
  variant's phenotype matches).
- The clinical-CNV track's ``end`` is an integer, as ``start`` is: a value that is not one is
  refused with the 422 a bad ``start`` gets. It was text, cut at a colon, and a value that was
  no number was refused with 400.
- A member edit takes no ``clear_existing_genomic_data``: it was accepted and ignored (the
  structure edit, which keeps the field, is the one that clears data). A request that still
  sends it is answered as before.
- The SV review save takes no ``acmg`` or ``compound_het``, a small variant's, which it never
  read (#853). A page loaded before this still sends them, and is answered as before.

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
from backend.app.routers import families as families_router
from backend.app.routers import families_structural_variants as sv_router
from backend.app.schemas import (
    FamilyMemberImpactOut,
    FamilyMemberOut,
    FamilyMemberUpdate,
    FamilyMemberUpdateOut,
    FamilyOut,
    FamilyStructureUpdate,
    GeneHomologOut,
    SmallVariantReviewOut,
    StructuralVariantReviewUpdate,
)
from backend.app.services import gene_info_bulk_sources, reference_source_service
from backend.app.services.access_control import CurrentUser

FAMILY, SAMPLE = "FAM_ENG4", "PROBAND_ENG4"


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

    # A homolog cached by the Ensembl request CoGA once made still loads, without them.
    cached = GeneHomologOut.model_validate(
        {"species_name": "Mus Musculus", "symbol": "Brca1", "percent_id": 81.2, "percent_coverage": 96.4}
    )
    assert cached.symbol == "Brca1"
    assert not {"percent_id", "percent_coverage"} & set(cached.model_dump())


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


def test_a_member_edit_takes_no_request_to_clear_the_data(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "clear_existing_genomic_data" not in _properties("FamilyMemberUpdate")
    # The structure edit keeps it: there it clears the family's imported data.
    assert "clear_existing_genomic_data" in _properties("FamilyStructureUpdate")
    assert "clear_existing_genomic_data" in FamilyStructureUpdate.model_fields

    updates: list[FamilyMemberUpdate] = []

    async def update_member(_session: Any, *, update: FamilyMemberUpdate, **_kwargs: Any) -> FamilyMemberUpdateOut:
        updates.append(update)
        member = FamilyMemberOut(sample_id=SAMPLE, role="proband", affected=True, clinical_status="affected")
        family = {"_id": "fam-uuid", "family_id": FAMILY, "created_at": datetime.now(timezone.utc), "members": [member]}
        return FamilyMemberUpdateOut(
            family=FamilyOut.model_validate(family),
            member=member,
            impact=FamilyMemberImpactOut(sample_id=SAMPLE),
        )

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(families_router, "update_family_member_for_admin", update_member)
    monkeypatch.setattr(families_router, "precompute_family_lineage_safe", nothing)
    monkeypatch.setattr(families_router, "precompute_family_ranking_safe", nothing)

    # A page loaded before this sends the flag: the edit is made as before, and the flag,
    # which never cleared anything here, goes no further.
    response = client.put(
        f"/api/families/{FAMILY}/members/{SAMPLE}",
        json={"clinical_status": "affected", "clear_existing_genomic_data": True},
    )

    assert response.status_code == 200
    (update,) = updates
    assert update.clinical_status == "affected"
    assert "clear_existing_genomic_data" not in update.model_fields_set
    assert not hasattr(update, "clear_existing_genomic_data")


def test_the_sv_review_request_takes_no_small_variant_acmg_or_compound_het() -> None:
    sv_body = _properties(_request_body("/api/families/{family_id}/structural-variants/{variant_id}/review"))
    small_body = _properties(_request_body("/api/families/{family_id}/small-variants/{variant_id}/review"))

    assert not {"acmg", "compound_het"} & set(sv_body)
    assert {"expected_updated_at", "classification", "tags", "note", "cnv_acmg", "source"} <= set(sv_body)
    # The small-variant save keeps both: it reads them.
    assert {"acmg", "compound_het"} <= set(small_body)
    # Fields it does not know are left out, as before, not refused.
    assert StructuralVariantReviewUpdate.model_config.get("extra", "ignore") == "ignore"


def test_an_sv_review_save_that_still_sends_them_is_answered_as_before(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
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
        json={
            "tags": ["report"],
            "note": "Breakpoints checked.",
            "acmg": {"criteria": [{"code": "PVS1", "strength": "very_strong", "accepted": True}]},
            "compound_het": {"partner_variant_id": "1-100-A-G", "tags": []},
        },
    )

    assert response.status_code == 200
    assert response.json()["tags"] == ["report"]
    (payload,) = saved
    assert payload.model_fields_set == {"tags", "note"}
    assert not hasattr(payload, "acmg") and not hasattr(payload, "compound_het")
