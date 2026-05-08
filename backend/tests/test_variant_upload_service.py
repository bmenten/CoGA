from backend.app.services.clickhouse_variant_storage import build_small_variant_id
from backend.app.services.variant_upload_service import _haplotype_state_end, _parse_vep_tsv_annotations


def test_haplotype_state_end_uses_next_variant_on_same_chromosome() -> None:
    state = {
        "start": 100,
        "last_pos": 200,
        "chr": "1",
    }

    assert _haplotype_state_end(
        state,
        next_chrom="1",
        next_start=500,
        chromosome_sizes={"1": 1_000},
    ) == 500


def test_haplotype_state_end_uses_previous_chromosome_size_on_chromosome_change() -> None:
    state = {
        "start": 800,
        "last_pos": 950,
        "chr": "1",
    }

    assert _haplotype_state_end(
        state,
        next_chrom="2",
        next_start=10,
        chromosome_sizes={"1": 1_000, "2": 2_000},
    ) == 1_000


def test_haplotype_state_end_falls_back_to_last_variant_without_chromosome_size() -> None:
    state = {
        "start": 800,
        "last_pos": 950,
        "chr": "1",
    }

    assert _haplotype_state_end(
        state,
        next_chrom=None,
        next_start=None,
        chromosome_sizes={},
    ) == 951


def test_parse_vep_tsv_annotations_indexes_by_variant_id_and_locus_allele() -> None:
    lookup = _parse_vep_tsv_annotations(
        "#Uploaded_variation\tLocation\tAllele\tGene\tFeature\tFeature_type\tConsequence\tIMPACT\tSYMBOL\tCANONICAL\n"
        "chr1_101_A/G\tchr1:101\tG\tENSG1\tENST1\tTranscript\tmissense_variant\tMODERATE\tGENE1\tYES\n"
    )

    variant_id = build_small_variant_id("1", 101, "A", "G")
    assert lookup.row_count == 1
    assert lookup.by_variant_id[variant_id][0]["gene"] == "GENE1"
    assert lookup.by_locus_allele[("1", 101, "G")][0]["effect"] == "missense_variant"
