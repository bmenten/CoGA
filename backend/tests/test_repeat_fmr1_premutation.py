"""An FMR1 premutation has its own status.

STRchive gives FMR1 one intermediate range, 45-200, which holds both the grey zone (45-54)
and the premutation (55-200). A premutation is what a carrier screen reports for fragile X:
the allele does not cause the disease in its carrier but can expand to a full mutation in a
child. The catalogue adds the premutation threshold (``PREMUTATION_MIN_BY_GENE``), the
classification gives such an allele ``premutation``, which ranks between the grey zone and a
full mutation, and the stored status may hold it (integration/test_repeat_status_storage.py).
All samples and values are synthetic.
"""

from __future__ import annotations

import json

import pytest

from backend.app.services import repeat_expansion_pg
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.repeat_expansion_catalog import BUILTIN_REPEAT_LOCI, PREMUTATION_MIN_BY_GENE
from backend.app.services.repeat_expansion_pg import (
    REPEAT_STATUS_RANK,
    classify_repeat_count,
    summarize_repeat_status,
)

# STRchive's FXS_FMR1 ranges, with the premutation threshold the catalogue adds.
FMR1 = {"warning_min": 45, "pathogenic_min": 201, "benign_min": 5, "benign_max": 44, "pathogenic_max": 2000}


def classify(count: int, *, premutation_min: int | None = 55) -> str:
    return classify_repeat_count(count, **FMR1, premutation_min=premutation_min)


@pytest.mark.parametrize(
    ("count", "status"),
    [(30, "normal"), (44, "normal"), (45, "intermediate"), (54, "intermediate"), (55, "premutation"), (200, "premutation"), (201, "pathogenic")],
)
def test_fmr1_counts_read_as_normal_grey_zone_premutation_or_full_mutation(count: int, status: str) -> None:
    assert classify(count) == status


def test_a_locus_without_a_premutation_range_keeps_its_intermediate_range() -> None:
    assert classify(60, premutation_min=None) == "intermediate"


def test_a_premutation_ranks_between_the_grey_zone_and_a_full_mutation() -> None:
    assert REPEAT_STATUS_RANK["intermediate"] < REPEAT_STATUS_RANK["premutation"] < REPEAT_STATUS_RANK["pathogenic"]
    assert summarize_repeat_status(["normal", "premutation", "intermediate"]) == "premutation"
    assert summarize_repeat_status(["premutation", "pathogenic"]) == "pathogenic"


def test_the_catalogue_sets_the_fmr1_premutation_threshold() -> None:
    assert PREMUTATION_MIN_BY_GENE == {"FMR1": 55}
    assert any(locus["gene"] == "FMR1" for locus in BUILTIN_REPEAT_LOCI)


class _RecordingSession:
    def __init__(self) -> None:
        self.params: list[dict] = []

    async def execute(self, statement, params=None):
        self.params.append(params or {})


@pytest.mark.asyncio
async def test_every_catalogue_entry_of_fmr1_carries_the_threshold_and_no_other_locus_does() -> None:
    session = _RecordingSession()
    strchive_fmr1 = {"locus_id": "FXS_FMR1", "gene": "FMR1", "metadata": {"source": "STRchive", "benign_max": 44}}
    builtin_fmr1 = next(locus for locus in BUILTIN_REPEAT_LOCI if locus["gene"] == "FMR1")
    htt = next(locus for locus in BUILTIN_REPEAT_LOCI if locus["gene"] == "HTT")

    await repeat_expansion_pg._seed_repeat_catalog_entries(session, [builtin_fmr1, strchive_fmr1, htt])  # type: ignore[arg-type]

    metadata = [json.loads(params["metadata_json"]) for params in session.params]
    assert [entry.get("premutation_min") for entry in metadata] == [55, 55, None]
    # The catalogue's own metadata stays.
    assert metadata[1]["source"] == "STRchive" and metadata[1]["benign_max"] == 44


def _insert_status(*, sex: str, sample_field: str) -> str:
    locus = {
        "locus_id": "FXS_FMR1",
        "gene": "FMR1",
        "display_name": "FMR1",
        "disease": "Fragile X syndrome",
        "inheritance": "XD",
        "motif": "CGG",
        "motif_index": 0,
        "warning_min": 45,
        "pathogenic_min": 201,
        "metadata": {"benign_min": 5, "benign_max": 44, "pathogenic_max": 2000, "premutation_min": 55},
    }
    params = repeat_expansion_pg._build_trgt_insert_params(
        sample_context=SampleMetadataContext(
            sample_uuid="sample-uuid",
            sample_id="sample",
            family_uuid="family-uuid",
            family_id="COUPLE1",
            sex=sex,
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        locus_lookup={"fxs_fmr1": locus},
        chrom="chrX",
        pos="147912049",
        ref="CGG",
        info_field="TRID=FXS_FMR1;END=147912111;MOTIFS=CGG",
        format_field="GT:AL:MC",
        sample_field=sample_field,
        header_sample="sample",
        metadata={},
    )
    return str(params["status"])


def test_a_female_partners_premutation_is_stored_as_one() -> None:
    assert _insert_status(sex="female", sample_field="1/2:90,180:30,60") == "premutation"
    assert _insert_status(sex="female", sample_field="1/2:90,150:30,50") == "intermediate"


def test_a_males_premutation_is_one_too() -> None:
    assert _insert_status(sex="male", sample_field="1/1:180,180:60,60") == "premutation"


@pytest.mark.asyncio
async def test_the_family_table_reads_the_premutation_from_the_catalogue() -> None:
    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def all(self):
            return self._rows

    class _TableSession:
        async def execute(self, statement, params=None):
            # Stored before the catalogue knew the premutation: the table reclassifies.
            return _Result(
                [
                    {
                        "sample_uuid": "00000000-0000-4000-8000-000000000001",
                        "locus_id": "FXS_FMR1",
                        "gene": "FMR1",
                        "display_name": "FMR1",
                        "disease": "Fragile X syndrome",
                        "inheritance": "XD",
                        "chr": "X",
                        "start": 147912049,
                        "end": 147912111,
                        "motif": "CGG",
                        "genotype": "1/2",
                        "allele_count": 2,
                        "alleles": [
                            {"repeat_count": 30, "status": "normal"},
                            {"repeat_count": 60, "status": "intermediate"},
                        ],
                        **FMR1,
                        "premutation_min": 55,
                        "status": "intermediate",
                    }
                ]
            )

    context = FamilyMetadataContext(
        family_uuid="00000000-0000-4000-8000-0000000000f1",
        family_id="COUPLE1",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": "00000000-0000-4000-8000-000000000001", "sample_id": "MOTHER1", "role": "mother", "affected": False, "sex": "female"}
        ],
        sample_uuid_to_name={"00000000-0000-4000-8000-000000000001": "MOTHER1"},
        sample_name_to_uuid={"MOTHER1": "00000000-0000-4000-8000-000000000001"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(_TableSession(), context=context)

    [locus] = table.loci
    assert locus.premutation_min == 55
    assert locus.status == "premutation"
    call = locus.calls["MOTHER1"]
    assert [allele.status for allele in call.alleles] == ["normal", "premutation"]
    assert call.status == "premutation"
