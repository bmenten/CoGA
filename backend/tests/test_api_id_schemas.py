from datetime import date, datetime, timezone
from uuid import uuid4

from backend.app.schemas import (
    AssemblyOut,
    FamilyOut,
    FamilyRegionOfInterestOut,
    GeneLocation,
    GenePanelOut,
    ProjectOut,
    UserRead,
    VariantOut,
)


def test_user_read_accepts_uuid_id() -> None:
    user_id = str(uuid4())
    project_id = str(uuid4())

    payload = UserRead(
        id=user_id,
        username="viewer@example.com",
        email="viewer@example.com",
        is_active=True,
        role="viewer",
        projects=[project_id],
        created_at=datetime.now(timezone.utc),
    )

    assert str(payload.id) == user_id
    assert payload.projects == [project_id]


def test_project_and_family_schemas_accept_uuid_identifiers() -> None:
    species_id = str(uuid4())
    family_id = str(uuid4())
    metadata_project_id = str(uuid4())
    metadata_assembly_id = str(uuid4())

    project = ProjectOut(
        id=metadata_project_id,
        name="Casework",
        species_id=species_id,
        assembly_id=metadata_assembly_id,
        user_ids=[metadata_project_id],
        metadata={},
    )
    family = FamilyOut(
        id=family_id,
        family_id="FAM1",
        members=[],
        projects=[metadata_project_id, str(uuid4())],
        roi=FamilyRegionOfInterestOut(
            query="chr1:10-20",
            label="chr1:10-20",
            source="region",
            assembly_id=metadata_assembly_id,
            chr="chr1",
            start=10,
            end=20,
        ),
        metadata={},
    )
    assembly = AssemblyOut(
        id=metadata_assembly_id,
        species_id=species_id,
        assembly_name="GRCh38",
        version="v1",
        release_date=date(2024, 1, 1),
    )

    assert project.id == metadata_project_id
    assert project.species_id == species_id
    assert family.id == family_id
    assert family.projects[0] == metadata_project_id
    assert family.roi is not None and family.roi.assembly_id == metadata_assembly_id
    assert assembly.species_id == species_id


def test_gene_panel_schema_accepts_structured_regions() -> None:
    payload = GenePanelOut(
        _id=str(uuid4()),
        name="Neuro",
        genes=["SCN1A"],
        regions=[GeneLocation(gene="SCN1A", chr="chr2", start=166845671, end=166985752)],
        created_by=str(uuid4()),
        created_at=datetime.now(timezone.utc),
    )

    assert payload.regions[0].gene == "SCN1A"
    assert payload.regions[0].chr == "chr2"


def test_variant_schema_accepts_clickhouse_string_id() -> None:
    payload = VariantOut(
        _id="1-12345-A-T",
        chr="1",
        start=12345,
        end=12345,
        length=0,
        type="SNV",
        source="wgs",
        ref="A",
        alt="T",
        genotypes=[],
    )

    assert payload.id == "1-12345-A-T"
