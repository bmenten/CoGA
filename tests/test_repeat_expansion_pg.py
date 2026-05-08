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

    insert_params = [
        params
        for sql, params in session.calls
        if "INSERT INTO repeat_expansions" in sql
    ][0]
    alleles = json.loads(insert_params["alleles_json"])

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
    insert_calls = [sql for sql, _params in session.calls if "INSERT INTO repeat_expansions" in sql]
    update_calls = [sql for sql, _params in session.calls if "UPDATE samples" in sql]

    assert len(delete_calls) == 2
    assert len(insert_calls) == 2
    assert len(update_calls) == 2
    assert session.committed is True
    assert result == {
        "processed": 1,
        "inserted": 2,
        "samples": 2,
        "source_format": "trgt_family",
    }
