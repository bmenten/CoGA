"""The TRGT import of repeat expansions (``repeat_expansion_pg``), and the family table's note on a
man's two chrX alleles.

The per-sample import stores a call with its interruption details, and the family import the call
of each header sample the family holds; a STRchive file is read into catalogue rows; a per-sample upload reads the target's own column of a family
VCF, or a single placeholder column, and refuses a file without one column for the target before
any write (a package entry's ``vcf_sample`` confirms a column named after another sample). A man's
chrX call keeps both alleles when they differ (a mosaic full mutation), flags two normal ones for
review, and collapses one written twice; a PAR call and a woman's stay diploid. All samples and
values are synthetic.
"""

import json

import pytest

from backend.app.services.family_metadata_context import SampleMetadataContext
from backend.app.services import repeat_expansion_pg
from backend.tests._repeat_fakes import Result, RowsSession, family_context, trgt_insert_params


class _RecordingSession:
    def __init__(self, repeat_loci_rows=None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.committed = False
        self.repeat_loci_rows = repeat_loci_rows or []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "FROM repeat_loci" in sql:
            return Result(self.repeat_loci_rows)
        return Result([])

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_ingest_trgt_text_uses_sqlalchemy_text_helper_without_shadowing() -> None:
    session = _RecordingSession()
    sample_context = SampleMetadataContext(
        sample_uuid="sample-uuid",
        sample_id="sample",
        family_uuid="family-uuid",
        family_id="demo_family",
        sex="female",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )

    result = await repeat_expansion_pg.ingest_trgt_text(
        session,
        sample_context=sample_context,
        text_value=(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample\n"
            "chr1\t100\t.\tA\t<STR>\t.\tPASS\tEND=105;TRID=RFC1;MOTIFS=AAA\tGT:MC\t0/1:10_20\n"
        ),
        metadata={"filename": "sample.trgt.vcf", "source": "trgt"},
    )

    insert_calls = [sql for sql, _params in session.calls if "INSERT INTO repeat_expansions" in sql]
    update_calls = [sql for sql, _params in session.calls if "UPDATE samples" in sql]

    assert len(insert_calls) == 1
    assert len(update_calls) == 1
    assert session.committed is True
    assert result == {"processed": 1, "inserted": 1, "source_format": "trgt"}


@pytest.mark.asyncio
async def test_ingest_trgt_text_stores_triplet_interruption_details() -> None:
    session = _RecordingSession(
        repeat_loci_rows=[
            {
                "locus_id": "HD_HTT",
                "gene": "HTT",
                "display_name": "HTT",
                "disease": "Huntington disease",
                "inheritance": "AD",
                "motif": "CAG",
                "motif_index": 0,
                "warning_min": 27,
                "pathogenic_min": 36,
                "aliases": ["HD"],
                "notes": None,
                "metadata": {"interruption_motifs": ["CAA"]},
            }
        ]
    )
    sample_context = SampleMetadataContext(
        sample_uuid="sample-uuid",
        sample_id="sample",
        family_uuid="family-uuid",
        family_id="demo_family",
        sex="female",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )

    await repeat_expansion_pg.ingest_trgt_text(
        session,
        sample_context=sample_context,
        text_value=(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample\n"
            "chr4\t3074876\t.\tA\t<STR>\t.\tPASS\tEND=3074933;TRID=HD_HTT;MOTIFS=CAG,CAA\tGT:MC:MS\t0/1:18_0,40_2:0(0-54),0(0-120)_1(120-126)\n"
        ),
        metadata={"filename": "sample.trgt.vcf", "source": "trgt"},
    )

    insert_calls = [
        params
        for sql, params in session.calls
        if "INSERT INTO repeat_expansions" in sql
    ]
    assert len(insert_calls) == 1
    batched_records = insert_calls[0]
    assert len(batched_records) == 1
    alleles = json.loads(batched_records[0]["alleles_json"])

    assert alleles[0]["interrupted"] is False
    assert alleles[1]["interrupted"] is True
    assert alleles[1]["motif_counts"] == [
        {"motif": "CAG", "count": 40},
        {"motif": "CAA", "count": 2},
    ]
    assert alleles[1]["motif_spans"] == "0(0-120)_1(120-126)"
    assert alleles[1]["interruption_label"] == "CAG 40 + CAA 2"


@pytest.mark.parametrize(
    "payload",
    [
        # STRchive's list, an object keyed by locus, and either with entries no row can name.
        [{"id": "HD_HTT", "gene": "HTT"}, {"id": "", "gene": "X"}, {"gene": "ATN1"}, "not an entry"],
        {"HD_HTT": {"id": "HD_HTT", "gene": "HTT"}, "broken": {"id": "NO_GENE"}, "text": "not an entry"},
    ],
    ids=["list", "object"],
)
def test_a_strchive_file_yields_the_loci_it_names_with_a_gene(payload) -> None:
    # As a start reads the file it checked (_load_configured_strchive_repeat_loci).
    loci = repeat_expansion_pg._strchive_repeat_loci_from_payload(json.loads(json.dumps(payload)))

    assert [(entry["locus_id"], entry["gene"]) for entry in loci] == [("HD_HTT", "HTT")]


def test_a_strchive_file_that_is_no_catalogue_yields_no_locus() -> None:
    # A start refuses a file it reads no locus from (test_strchive_release_pin.py).
    assert repeat_expansion_pg._strchive_repeat_loci_from_payload("a string") == []


@pytest.mark.asyncio
async def test_the_trid_lookup_reads_aliases_and_tracts_from_jsonb_text(monkeypatch) -> None:
    """A driver without a jsonb codec returns the catalogue's aliases and metadata as text: the
    TRID lookup names the same rows, and finds a gene's STRchive tracts, as from a list and a
    mapping."""
    from backend.tests._repeat_fakes import catalogue_after_a_start

    rows = list((await catalogue_after_a_start(monkeypatch)).values())
    as_text = [{**row, "aliases": json.dumps(row["aliases"]), "metadata": json.dumps(row["metadata"])} for row in rows]
    # An alias list that is no JSON is one alias; a metadata text that is no JSON is none.
    as_text.append({**rows[0], "locus_id": "SYN_LOCUS", "gene": "SYN1", "aliases": "SYN-ALIAS", "metadata": "{"})

    parsed = await repeat_expansion_pg._build_repeat_locus_lookup(RowsSession(rows))  # type: ignore[arg-type]
    from_text = await repeat_expansion_pg._build_repeat_locus_lookup(RowsSession(as_text))  # type: ignore[arg-type]

    assert set(from_text) - set(parsed) == {"syn_locus", "syn1", "syn-alias"}
    assert {key: row["locus_id"] for key, row in parsed.items()} == {
        key: row["locus_id"] for key, row in from_text.items() if key not in {"syn_locus", "syn1", "syn-alias"}
    }
    assert {gene: [row["locus_id"] for row in tracts] for gene, tracts in from_text.tracts.items()} == {
        gene: [row["locus_id"] for row in tracts] for gene, tracts in parsed.tracts.items()
    }
    assert [row["locus_id"] for row in from_text.tracts["arx"]] == ["EIEE1_ARX", "PRTS_ARX"]


def test_a_strchive_entry_is_normalized_into_a_catalogue_row() -> None:
    entries = repeat_expansion_pg._strchive_repeat_loci_from_payload(
        [
            {
                "id": "HD_HTT",
                "disease_id": "HD",
                "gene": "HTT",
                "disease": "Huntington disease",
                "inheritance": ["AD"],
                "reference_motif_reference_orientation": ["CAG"],
                "pathogenic_motif_reference_orientation": ["CAG"],
                "interruption_reference_orientation": ["CAA"],
                "intermediate_min": 27,
                "pathogenic_min": 36,
                "hpo_terms": ["HP:0001250 Chorea"],
            }
        ]
    )

    assert entries == [
        {
            "locus_id": "HD_HTT",
            "gene": "HTT",
            "display_name": "HTT",
            "disease": "Huntington disease",
            "inheritance": "AD",
            "motif": "CAG",
            "motif_index": 0,
            "warning_min": 27,
            "pathogenic_min": 36,
            "x_linked": False,
            "aliases": ["HD_HTT", "HTT", "HD"],
            "notes": None,
            "metadata": {
                "source": "STRchive",
                "reference_motifs": ["CAG"],
                "pathogenic_motifs": ["CAG"],
                "interruption_motifs": ["CAA"],
                "benign_min": None,
                "benign_max": None,
                "intermediate_max": None,
                "pathogenic_max": None,
                "motif_len": None,
                "chrom": None,
                "start_hg38": None,
                "stop_hg38": None,
                "hpo_terms": ["HP:0001250 Chorea"],
                "evidence": [],
                "references": [],
                "raw": {
                    "id": "HD_HTT",
                    "disease_id": "HD",
                    "gene": "HTT",
                    "disease": "Huntington disease",
                    "inheritance": ["AD"],
                    "reference_motif_reference_orientation": ["CAG"],
                    "pathogenic_motif_reference_orientation": ["CAG"],
                    "interruption_reference_orientation": ["CAA"],
                    "intermediate_min": 27,
                    "pathogenic_min": 36,
                    "hpo_terms": ["HP:0001250 Chorea"],
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_ingest_family_trgt_text_imports_all_matching_header_samples() -> None:
    session = _RecordingSession()
    sample_contexts = {
        sample_id: SampleMetadataContext(
            sample_uuid=f"{sample_id}-uuid",
            sample_id=sample_id,
            family_uuid="family-uuid",
            family_id="demo_family",
            sex="female",
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for sample_id in ["S1", "S2"]
    }

    result = await repeat_expansion_pg.ingest_family_trgt_text(
        session,
        sample_contexts=sample_contexts,
        text_value=(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\tUNKNOWN\n"
            "chr1\t100\t.\tA\t<STR>\t.\tPASS\tEND=105;TRID=RFC1;MOTIFS=AAA\tGT:MC\t0/1:10_20\t0/0:9_9\t0/0:1_1\n"
        ),
        metadata={"filename": "family.trgt.vcf", "source": "trgt_family"},
    )

    delete_calls = [sql for sql, _params in session.calls if "DELETE FROM repeat_expansions" in sql]
    insert_calls = [params for sql, params in session.calls if "INSERT INTO repeat_expansions" in sql]
    update_calls = [sql for sql, _params in session.calls if "UPDATE samples" in sql]

    assert len(delete_calls) == 2
    assert len(insert_calls) == 1
    assert len(insert_calls[0]) == 2
    assert len(update_calls) == 2
    assert session.committed is True
    assert result == {
        "processed": 1,
        "inserted": 2,
        "samples": 2,
        "source_format": "trgt_family",
    }


# ---- per-sample upload: which sample column is read --------------------------------------


class _UploadSession(_RecordingSession):
    """Also answers the per-sample upload's lookup of the sample ids a column may name."""

    def __init__(self, known_samples: list[str]) -> None:
        super().__init__()
        self.known_samples = known_samples

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "OR sample_id IN" in sql:
            self.calls.append((sql, params))
            return Result([{"sample_id": sample} for sample in self.known_samples])
        return await super().execute(statement, params)


def _child_context() -> SampleMetadataContext:
    return SampleMetadataContext(
        sample_uuid="child-uuid",
        sample_id="CHILD",
        family_uuid="00000000-0000-4000-8000-0000000000f1",
        family_id="TRIO",
        sex="female",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _trgt_vcf(columns: list[str], counts: list[int]) -> str:
    calls = "\t".join(f"0/0:{count}_0" for count in counts)
    return (
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(columns) + "\n"
        f"chr4\t3074876\t.\tA\t<STR>\t.\tPASS\tEND=3074933;TRID=HD_HTT;MOTIFS=CAG,CAA\tGT:MC\t{calls}\n"
    )


def _stored_counts(session: _RecordingSession) -> list[tuple[list[int], str]]:
    inserts = [params for sql, params in session.calls if "INSERT INTO repeat_expansions" in sql]
    return [
        (
            [allele["repeat_count"] for allele in json.loads(record["alleles_json"])],
            json.loads(record["metadata_json"])["vcf_sample"],
        )
        for batch in inserts
        for record in batch
    ]


@pytest.mark.asyncio
async def test_per_sample_upload_reads_the_targets_column_of_a_family_vcf() -> None:
    """A family TRGT VCF uploaded for the child: the child's own column is stored as the
    child's repeat sizes, not the first column (the mother's)."""
    session = _UploadSession(["MOTHER", "CHILD", "FATHER"])

    await repeat_expansion_pg.ingest_trgt_text(
        session,
        sample_context=_child_context(),
        text_value=_trgt_vcf(["MOTHER_sort", "CHILD_sort", "FATHER_sort"], [17, 45, 20]),
        metadata={"filename": "family.trgt.vcf", "source": "trgt"},
    )

    assert _stored_counts(session) == [([45], "CHILD_sort")]
    assert session.committed is True


@pytest.mark.asyncio
async def test_per_sample_upload_binds_a_single_placeholder_column_to_the_target() -> None:
    session = _UploadSession(["MOTHER", "CHILD", "FATHER"])

    await repeat_expansion_pg.ingest_trgt_text(
        session,
        sample_context=_child_context(),
        text_value=_trgt_vcf(["Sample0"], [19]),
        metadata={"filename": "child.trgt.vcf", "source": "trgt"},
    )

    assert _stored_counts(session) == [([19], "Sample0")]


@pytest.mark.parametrize(
    ("columns", "known_samples", "message"),
    [
        # The mother's own file, uploaded for the child.
        (["MOTHER_sort"], ["MOTHER", "CHILD", "FATHER"], "'MOTHER_sort' is MOTHER"),
        # Another family's sample: ids are unique across CoGA, so it is recognised too.
        (["OTHER_PATIENT_sort"], ["MOTHER", "CHILD", "FATHER", "OTHER_PATIENT"], "'OTHER_PATIENT_sort' is OTHER_PATIENT"),
        # Two columns would both be the child.
        (["CHILD", "CHILD_sort"], ["MOTHER", "CHILD", "FATHER"], "more than one sample column for CHILD"),
        # A multi-sample file none of whose columns is a known sample.
        (["Sample0", "Sample1"], ["MOTHER", "CHILD", "FATHER"], "name no known sample"),
    ],
)
@pytest.mark.asyncio
async def test_per_sample_upload_refuses_a_file_without_one_column_for_the_target(
    columns: list[str], known_samples: list[str], message: str
) -> None:
    from fastapi import HTTPException

    session = _UploadSession(known_samples)

    with pytest.raises(HTTPException) as raised:
        await repeat_expansion_pg.ingest_trgt_text(
            session,
            sample_context=_child_context(),
            text_value=_trgt_vcf(columns, [30] * len(columns)),
            metadata={"filename": "wrong.trgt.vcf", "source": "trgt"},
        )

    assert raised.value.status_code == 400
    assert message in str(raised.value.detail)
    # Refused before any write.
    assert _stored_counts(session) == []
    assert session.committed is False


def test_vcf_sample_name_candidates_strip_known_tool_suffixes() -> None:
    from backend.app.services.family_package_common import vcf_sample_name_candidates

    assert vcf_sample_name_candidates("HG002_sort") == ["HG002_sort", "HG002"]
    candidates = vcf_sample_name_candidates("S1_sv_phased")
    assert candidates[0] == "S1_sv_phased"
    assert set(candidates[1:]) == {"S1", "S1_sv"}
    assert vcf_sample_name_candidates("Sample0") == ["Sample0"]
    assert vcf_sample_name_candidates("  ") == []


@pytest.mark.parametrize("declared", ["OTHER_TUBE_sort", "CHILD"])
@pytest.mark.asyncio
async def test_a_package_entrys_vcf_sample_confirms_a_column_named_after_another_sample(declared: str) -> None:
    """A lab that verified the tubes maps a sample to a file named after another tube; the
    entry's vcf_sample (the column, or the sample itself) is its recorded word for it."""
    session = _UploadSession(["MOTHER", "CHILD", "FATHER", "OTHER_TUBE"])

    await repeat_expansion_pg.ingest_trgt_text(
        session,
        sample_context=_child_context(),
        text_value=_trgt_vcf(["OTHER_TUBE_sort"], [33]),
        metadata={"filename": "other_tube.trgt.vcf", "source": "trgt"},
        declared=declared,
    )

    assert _stored_counts(session) == [([33], "OTHER_TUBE_sort")]


@pytest.mark.asyncio
async def test_a_vcf_sample_naming_neither_a_column_nor_the_sample_is_refused() -> None:
    from fastapi import HTTPException

    session = _UploadSession(["MOTHER", "CHILD", "FATHER"])

    with pytest.raises(HTTPException) as raised:
        await repeat_expansion_pg.ingest_trgt_text(
            session,
            sample_context=_child_context(),
            text_value=_trgt_vcf(["CHILD_sort"], [45]),
            metadata={"filename": "child.trgt.vcf", "source": "trgt"},
            declared="CHLD",
        )

    assert raised.value.status_code == 400
    assert "vcf_sample 'CHLD'" in str(raised.value.detail)
    assert _stored_counts(session) == []


_FMR1_LOCUS = {
    "locus_id": "FXS_FMR1",
    "gene": "FMR1",
    "display_name": "FMR1",
    "disease": "Fragile X syndrome",
    "inheritance": "XL",
    "motif": "CGG",
    "motif_index": 0,
    "warning_min": 55,
    "pathogenic_min": 201,
    "metadata": {},
}


def _fmr1_insert_params(*, sex: str, sample_field: str, chrom: str = "chrX", pos: str = "147912051") -> dict:
    return trgt_insert_params(
        {"fxs_fmr1": _FMR1_LOCUS},
        info_field="TRID=FXS_FMR1;END=147912110;MOTIFS=CGG",
        sample_field=sample_field,
        chrom=chrom,
        pos=pos,
        ref="CGG",
        sex=sex,
        family_id="demo_family",
    )


def test_male_chrx_two_different_alleles_keep_the_expansion() -> None:
    """TRGT orders alleles shortest first. A male's chrX call with two sizes (TRGT run
    without --karyotype XY on a mosaic full mutation) keeps both, so the full mutation
    stays and FMR1 reads pathogenic."""
    params = _fmr1_insert_params(sex="male", sample_field="1/2:90,900:30,300")

    alleles = json.loads(params["alleles_json"])
    assert [allele["repeat_count"] for allele in alleles] == [30, 300]
    assert params["allele_count"] == 2
    assert params["status"] == "pathogenic"


def test_male_chrx_two_different_normal_alleles_are_flagged_for_review() -> None:
    params = _fmr1_insert_params(sex="male", sample_field="1/2:90,96:30,32")

    assert params["allele_count"] == 2
    assert params["status"] == "review"


def test_male_chrx_duplicated_allele_collapses_to_one() -> None:
    """TRGT genotyped as XX writes a male's single allele twice: that pair is one allele."""
    params = _fmr1_insert_params(sex="male", sample_field="1/1:90,90:30,30")

    assert params["allele_count"] == 1
    assert params["status"] == "normal"


def test_male_chrx_par_locus_and_females_stay_diploid() -> None:
    par = _fmr1_insert_params(sex="male", sample_field="1/2:90,96:30,32", pos="1500000")
    female = _fmr1_insert_params(sex="female", sample_field="1/2:90,180:30,60")

    assert par["allele_count"] == 2
    assert par["status"] == "normal"
    assert female["allele_count"] == 2
    assert female["status"] == "intermediate"


@pytest.mark.asyncio
async def test_repeat_table_notes_a_male_with_two_chrx_alleles() -> None:
    stored = {
        "sample_uuid": "00000000-0000-4000-8000-000000000001",
        "locus_id": "FXS_FMR1",
        "gene": "FMR1",
        "display_name": "FMR1",
        "disease": "Fragile X syndrome",
        "inheritance": "XL",
        "chr": "X",
        "start": 147912051,
        "end": 147912110,
        "motif": "CGG",
        "genotype": "1/2",
        "allele_count": 2,
        "alleles": [
            {"repeat_count": 30, "status": "normal"},
            {"repeat_count": 32, "status": "normal"},
        ],
        "warning_min": 55,
        "pathogenic_min": 201,
        "status": "normal",
    }
    context = family_context(
        ("00000000-0000-4000-8000-000000000001", "SON", "proband", True, "male"), family_id="demo_family"
    )

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(RowsSession([stored]), context=context)

    call = table.loci[0].calls["SON"]
    assert call.status == "review"
    assert call.note == repeat_expansion_pg.MALE_X_DISTINCT_ALLELES_NOTE
    assert table.loci[0].status == "review"
