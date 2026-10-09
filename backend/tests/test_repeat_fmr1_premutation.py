"""An FMR1 premutation has its own status, and a full mutation starts at 201 repeats.

STRchive gives FMR1 one intermediate range, 45-200, which holds both the grey zone (45-54)
and the premutation (55-200). A premutation is what a carrier screen reports for fragile X:
the allele does not cause the disease in its carrier but can expand to a full mutation in a
child. The catalogue adds the premutation threshold (``PREMUTATION_MIN_BY_GENE``), the
classification gives such an allele ``premutation``, which ranks between the grey zone and a
full mutation, and the stored status may hold it (integration/test_repeat_status_storage.py).

A full mutation is more than 200 repeats. FMR1 has two catalogue rows, the built-in ``FMR1``
and STRchive's ``FXS_FMR1``, and the call's TRID decides which one classifies it. The
built-in row put the pathogenic threshold at 200, so under it a 200-repeat premutation read
as a full mutation (CLIN-2); both rows now start the full mutation at 201.
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
        self.statements: list[tuple[str, dict]] = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.params.append(params or {})
        self.statements.append((str(statement), params or {}))

    async def commit(self) -> None:
        self.committed = True


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


# ---- The full mutation starts at 201, under either catalogue row (CLIN-2) ---------------------
#
# The classifier reads pathogenic_min as the first pathogenic count (">="), as STRchive's
# inclusive ranges do (FXS_FMR1: intermediate 45-200, pathogenic 201-2000). A full mutation is
# "more than 200" repeats, so its first count is 201. These tests run the catalogue a start
# leaves (seed_builtin_repeat_catalog over the built-in loci and the shipped STRchive file)
# through the import's lookup, so the TRID picks the row as it does at import.


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _CatalogueSession:
    """Answers the import's catalogue query with the given ``repeat_loci`` rows."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def execute(self, statement, params=None):
        return _Result(self.rows)


async def _catalogue_after_a_start(monkeypatch) -> list[dict]:
    """The ``repeat_loci`` rows a start leaves, as the import's catalogue query returns them.

    The seed upserts by ``locus_id``, so the last write of a locus is its row.
    """
    # The shipped STRchive file, not one an environment variable may point at.
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    session = _RecordingSession()
    await repeat_expansion_pg.seed_builtin_repeat_catalog(session)
    rows: dict[str, dict] = {}
    for params in session.params:
        rows[params["locus_id"]] = {
            **{
                column: params[column]
                for column in (
                    "locus_id",
                    "gene",
                    "display_name",
                    "disease",
                    "inheritance",
                    "motif",
                    "motif_index",
                    "warning_min",
                    "pathogenic_min",
                    "notes",
                )
            },
            "aliases": json.loads(params["aliases_json"]),
            "metadata": json.loads(params["metadata_json"]),
        }
    return [rows[locus_id] for locus_id in sorted(rows)]


async def _import_lookup(monkeypatch) -> dict[str, dict]:
    rows = await _catalogue_after_a_start(monkeypatch)
    return await repeat_expansion_pg._build_repeat_locus_lookup(_CatalogueSession(rows))


def _fmr1_call(lookup: dict[str, dict], *, trid: str, sex: str, sample_field: str) -> dict:
    return repeat_expansion_pg._build_trgt_insert_params(
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
        locus_lookup=lookup,
        chrom="chrX",
        pos="147912049",
        ref="CGG",
        info_field=f"TRID={trid};END=147912111;MOTIFS=CGG",
        format_field="GT:AL:MC",
        sample_field=sample_field,
        header_sample="sample",
        metadata={},
    )


# The TRIDs of FMR1's two catalogue rows: "FMR1" resolves to the built-in row, "FXS_FMR1"
# (STRchive's id) to the STRchive one.
FMR1_TRIDS = ["FMR1", "FXS_FMR1"]


@pytest.mark.parametrize("trid", FMR1_TRIDS)
@pytest.mark.parametrize(
    ("count", "status"),
    [
        (44, "normal"),
        (45, "intermediate"),
        (54, "intermediate"),
        (55, "premutation"),
        (200, "premutation"),
        (201, "pathogenic"),
    ],
)
@pytest.mark.asyncio
async def test_fmr1_ranges_hold_under_either_catalogue_row(monkeypatch, trid: str, count: int, status: str) -> None:
    lookup = await _import_lookup(monkeypatch)

    params = _fmr1_call(lookup, trid=trid, sex="female", sample_field=f"1/2:90,{3 * count}:30,{count}")

    assert params["locus_id"] == trid  # the row the TRID resolved to
    assert [allele["status"] for allele in json.loads(params["alleles_json"])] == ["normal", status]
    assert params["status"] == status


@pytest.mark.parametrize("trid", FMR1_TRIDS)
@pytest.mark.parametrize(
    ("count", "status"),
    [(200, "premutation"), (201, "pathogenic")],
)
# TRGT run with --karyotype XY writes the allele once; without it, twice.
@pytest.mark.parametrize("ploidy", ["hemizygous", "written twice"])
@pytest.mark.asyncio
async def test_a_males_single_x_allele_is_a_premutation_at_200_and_a_full_mutation_at_201(
    monkeypatch, trid: str, count: int, status: str, ploidy: str
) -> None:
    lookup = await _import_lookup(monkeypatch)
    sample_field = (
        f"1:{3 * count}:{count}" if ploidy == "hemizygous" else f"1/1:{3 * count},{3 * count}:{count},{count}"
    )

    params = _fmr1_call(lookup, trid=trid, sex="male", sample_field=sample_field)

    assert params["allele_count"] == 1
    assert params["status"] == status


def test_the_built_in_fmr1_row_and_strchive_start_the_full_mutation_at_201() -> None:
    builtin = next(locus for locus in BUILTIN_REPEAT_LOCI if locus["locus_id"] == "FMR1")
    strchive = next(
        locus
        for locus in repeat_expansion_pg.load_strchive_repeat_loci(repeat_expansion_pg.REPO_STRCHIVE_LOCI_PATH)
        if locus["locus_id"] == "FXS_FMR1"
    )

    # Inclusive ranges: the full mutation starts right after the intermediate range ends.
    assert strchive["metadata"]["intermediate_max"] == 200
    assert (strchive["warning_min"], strchive["pathogenic_min"]) == (45, 201)
    assert (builtin["warning_min"], builtin["pathogenic_min"]) == (45, 201)


@pytest.mark.asyncio
async def test_each_start_rewrites_the_thresholds_an_earlier_start_stored(monkeypatch) -> None:
    """A database seeded with the old built-in row (200) gets 201 on its next start."""
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    session = _RecordingSession()

    await repeat_expansion_pg.seed_builtin_repeat_catalog(session)

    [(sql, params)] = [(sql, params) for sql, params in session.statements if params.get("locus_id") == "FMR1"]
    upsert = " ".join(sql.split())
    assert "ON CONFLICT (locus_id) DO UPDATE" in upsert
    for column in ("warning_min", "pathogenic_min", "metadata"):
        assert f"{column} = EXCLUDED.{column}" in upsert
    assert params["pathogenic_min"] == 201
    assert session.committed


@pytest.mark.asyncio
async def test_a_call_stored_as_a_full_mutation_at_200_reads_premutation_in_the_family_table(monkeypatch) -> None:
    """The family table classifies a stored call again against the catalogue, so a male's
    200-repeat allele imported under the old built-in row reads as a premutation once a start
    has rewritten the row. (The genome tracks show the status stored at import; importing the
    calls again corrects that.)"""
    builtin = next(row for row in await _catalogue_after_a_start(monkeypatch) if row["locus_id"] == "FMR1")

    class _TableSession:
        async def execute(self, statement, params=None):
            # Stored with the old threshold; the query reads the thresholds from the catalogue.
            return _Result(
                [
                    {
                        "sample_uuid": "00000000-0000-4000-8000-000000000002",
                        "locus_id": "FMR1",
                        "gene": "FMR1",
                        "display_name": "FMR1",
                        "disease": "Fragile X syndrome",
                        "inheritance": "X-linked",
                        "chr": "X",
                        "start": 147912049,
                        "end": 147912111,
                        "motif": "CGG",
                        "genotype": "1",
                        "allele_count": 1,
                        "alleles": [{"repeat_count": 200, "status": "pathogenic"}],
                        "warning_min": builtin["warning_min"],
                        "pathogenic_min": builtin["pathogenic_min"],
                        "benign_min": builtin["metadata"].get("benign_min"),
                        "benign_max": builtin["metadata"].get("benign_max"),
                        "pathogenic_max": builtin["metadata"].get("pathogenic_max"),
                        "premutation_min": builtin["metadata"].get("premutation_min"),
                        "status": "pathogenic",
                    }
                ]
            )

    context = FamilyMetadataContext(
        family_uuid="00000000-0000-4000-8000-0000000000f2",
        family_id="TRIO1",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": "00000000-0000-4000-8000-000000000002", "sample_id": "SON1", "role": "proband", "affected": False, "sex": "male"}
        ],
        sample_uuid_to_name={"00000000-0000-4000-8000-000000000002": "SON1"},
        sample_name_to_uuid={"SON1": "00000000-0000-4000-8000-000000000002"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(_TableSession(), context=context)

    [locus] = table.loci
    assert locus.pathogenic_min == 201
    call = locus.calls["SON1"]
    assert [allele.status for allele in call.alleles] == ["premutation"]
    assert call.status == locus.status == "premutation"
