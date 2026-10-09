import json

import pytest

from backend.app.services.family_metadata_context import SampleMetadataContext
from backend.app.services import repeat_expansion_pg


class _FakeQueryResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _RecordingSession:
    def __init__(self, repeat_loci_rows=None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.committed = False
        self.repeat_loci_rows = repeat_loci_rows or []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "FROM repeat_loci" in sql:
            return _FakeQueryResult(self.repeat_loci_rows)
        return _FakeQueryResult([])

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


def test_load_strchive_repeat_loci_normalizes_catalog_entries(tmp_path) -> None:
    path = tmp_path / "STRchive-loci.json"
    path.write_text(
        json.dumps(
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
    )

    entries = repeat_expansion_pg.load_strchive_repeat_loci(path)

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
            return _FakeQueryResult([{"sample_id": sample} for sample in self.known_samples])
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
    """A family TRGT VCF uploaded for the child: the first column (the mother's) used to be
    stored as the child's repeat sizes."""
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
    return repeat_expansion_pg._build_trgt_insert_params(
        sample_context=SampleMetadataContext(
            sample_uuid="sample-uuid",
            sample_id="sample",
            family_uuid="family-uuid",
            family_id="demo_family",
            sex=sex,
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        locus_lookup={"fxs_fmr1": _FMR1_LOCUS},
        chrom=chrom,
        pos=pos,
        ref="CGG",
        info_field="TRID=FXS_FMR1;END=147912110;MOTIFS=CGG",
        format_field="GT:AL:MC",
        sample_field=sample_field,
        header_sample="sample",
        metadata={},
    )


def test_male_chrx_two_different_alleles_keep_the_expansion() -> None:
    """TRGT orders alleles shortest first. A male's chrX call with two sizes (TRGT run
    without --karyotype XY on a mosaic full mutation) used to keep only the first, so
    the full mutation was dropped and FMR1 read normal."""
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
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    class _TableSession:
        async def execute(self, statement, params=None):
            return _FakeQueryResult(
                [
                    {
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
                ]
            )

    context = FamilyMetadataContext(
        family_uuid="00000000-0000-4000-8000-0000000000f1",
        family_id="demo_family",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": "00000000-0000-4000-8000-000000000001", "sample_id": "SON", "role": "proband", "affected": True, "sex": "male"}
        ],
        sample_uuid_to_name={"00000000-0000-4000-8000-000000000001": "SON"},
        sample_name_to_uuid={"SON": "00000000-0000-4000-8000-000000000001"},
        affected_sample_names=["SON"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )

    table = await repeat_expansion_pg.get_family_repeat_expansion_table_response(
        _TableSession(), context=context
    )

    call = table.loci[0].calls["SON"]
    assert call.status == "review"
    assert call.note == repeat_expansion_pg.MALE_X_DISTINCT_ALLELES_NOTE
    assert table.loci[0].status == "review"
