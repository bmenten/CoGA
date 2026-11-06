"""The VCF annotation parser: what a record's CSQ, its INFO fields and a VEP table row give a
variant, and the number parsing under it.

The parser reads VEP's CSQ by the header's own field list, falls back to the INFO keys of other
annotators, and reads a VEP table row the same way. Its number helpers read scientific
notation, and keep only SpliceAI's delta scores, which are probabilities in [0, 1]: a DP
position (an offset in bases) that lands in a delta-score slot is dropped. A population count
is read only from its source's own field (CLIN-59, below).
"""

import pytest

from backend.app.services.variant_annotation_parser import (
    AnnotationHeaderState,
    _parse_float,
    _parse_spliceai_pred,
    _spliceai_delta,
    clinvar_file_annotated,
    clinvar_file_in_description,
    extract_small_variant_annotations,
    normalize_small_variant_annotation_entry,
    update_annotation_header_state,
)
from backend.app.services.clickhouse_variant_rows import _annotation_columns
from backend.app.services.clickhouse_variant_rows import _annotation_count


def test_extract_small_variant_annotations_from_csq() -> None:
    state = AnnotationHeaderState()
    update_annotation_header_state(
        state,
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|BIOTYPE|HGVSc|HGVSp|CANONICAL|MANE_SELECT|CLIN_SIG|gnomAD_AF|gnomAD_Hom|PLI|MISSENSE_Z|CADD_PHRED|REVEL|SpliceAI_pred_DS_AG|SpliceAI_pred_DS_AL|SpliceAI_pred_DS_DG|SpliceAI_pred_DS_DL">',
    )

    annotations = extract_small_variant_annotations(
        {
            "CSQ": "A|missense_variant|HIGH|GENE1|ENSG1|Transcript|ENST1|protein_coding|ENST1:c.101A>G|ENSP1:p.Lys34Arg|YES|MANE123|Pathogenic|0.0002|1|0.982|4.12|28.4|0.91|0.01|0.02|0.60|0.10"
        },
        state,
    )

    assert annotations == [
        {
            "gene": "GENE1",
            "gene_id": "ENSG1",
            "transcript_id": "ENST1",
            "feature_type": "Transcript",
            "transcript_biotype": "protein_coding",
            "impact": "HIGH",
            "effect": "missense_variant",
            "canonical": True,
            "mane_select": True,
            "hgvsc": "ENST1:c.101A>G",
            "hgvsp": "ENSP1:p.Lys34Arg",
            "clinvar": "Pathogenic",
            "gnomad_hom_count": 1,
            "gene_pli": 0.982,
            "gene_missense_z": 4.12,
            "cadd_phred": 28.4,
            "revel": 0.91,
            "spliceai_ds_ag": 0.01,
            "spliceai_ds_al": 0.02,
            "spliceai_ds_dg": 0.6,
            "spliceai_ds_dl": 0.1,
            "spliceai_max": 0.6,
            "gnomad_af": 0.0002,
            "population_frequencies": {"gnomad_af": 0.0002},
        }
    ]


def test_extract_small_variant_annotations_from_info_fallback() -> None:
    state = AnnotationHeaderState()

    annotations = extract_small_variant_annotations(
        {
            "CLNSIG": "Likely_pathogenic",
            "CADD_PHRED": "26.1",
            "REVEL": "0.78",
            "DS_AG": "0.02",
            "DS_AL": "0.03",
            "DS_DG": "0.15",
            "DS_DL": "0.01",
            "gnomAD_AF": "0.0005",
            "gnomAD_Hom": "2",
            "gnomAD_AC": "12",
            "gnomAD_Hemi": "1",
            "PLI": "0.997",
            "MISSENSE_Z": "3.7",
            "SYMBOL": "GENE2",
            "HGVSc": "NM_000000.1:c.123A>G",
        },
        state,
    )

    assert annotations == [
        {
            "gene": "GENE2",
            "hgvsc": "NM_000000.1:c.123A>G",
            "clinvar": "Likely_pathogenic",
            "cadd_phred": 26.1,
            "revel": 0.78,
            "spliceai_ds_ag": 0.02,
            "spliceai_ds_al": 0.03,
            "spliceai_ds_dg": 0.15,
            "spliceai_ds_dl": 0.01,
            "spliceai_max": 0.15,
            "gnomad_af": 0.0005,
            "gnomad_hom_count": 2,
            "gene_pli": 0.997,
            "gene_missense_z": 3.7,
            "population_frequencies": {"gnomad_af": 0.0005},
            "extra": {"gnomad_ac": 12, "gnomad_hemi_count": 1},
        }
    ]


def test_normalize_small_variant_annotation_entry_from_vep_tsv() -> None:
    annotation = normalize_small_variant_annotation_entry(
        {
            "Gene": "ENSG000001",
            "SYMBOL": "GENE1",
            "Feature_type": "Transcript",
            "Feature": "ENST000001",
            "Consequence": "missense_variant",
            "IMPACT": "MODERATE",
            "BIOTYPE": "protein_coding",
            "CANONICAL": "YES",
            "MANE_SELECT": "NM_000001.1",
            "HGVSc": "ENST000001:c.101A>G",
            "HGVSp": "ENSP000001:p.Lys34Arg",
            "gnomADe_AF": "0.0002",
            "gnomADg_AF": "0.0003",
            "MAX_AF": "0.0005",
            "CLIN_SIG": "Pathogenic",
            "SpliceAI_pred": "A|GENE1|0.01|0.02|0.60|0.10|-|-|-|-",
            "SpliceRegion": "exonic",
            "am_class": "likely_pathogenic",
            "am_pathogenicity": "0.91",
            "LoF": "HC",
            "5UTR_annotation": "uORF",
            "5UTR_consequence": "uORF_gained",
        }
    )

    assert annotation["gene"] == "GENE1"
    assert annotation["gene_id"] == "ENSG000001"
    assert annotation["transcript_id"] == "ENST000001"
    assert annotation["impact"] == "MODERATE"
    assert annotation["effect"] == "missense_variant"
    assert annotation["canonical"] is True
    assert annotation["mane_select"] is True
    assert annotation["clinvar"] == "Pathogenic"
    assert annotation["lof"] == "HC"
    assert annotation["population_frequencies"] == {
        "gnomad_exomes_af": 0.0002,
        "gnomad_genomes_af": 0.0003,
        "gnomad_popmax_af": 0.0005,
        "gnomad_af": 0.0003,
    }
    assert annotation["alpha_missense_class"] == "likely_pathogenic"
    assert annotation["alpha_missense_pathogenicity"] == 0.91
    assert annotation["spliceai_ds_ag"] == 0.01
    assert annotation["spliceai_ds_al"] == 0.02
    assert annotation["spliceai_ds_dg"] == 0.6
    assert annotation["spliceai_ds_dl"] == 0.1
    assert annotation["spliceai_max"] == 0.6
    assert annotation["splice_region"] == "exonic"
    assert annotation["utr5_annotation"] == "uORF"
    assert annotation["utr5_consequence"] == "uORF_gained"


# --- The number helpers -----------------------------------------------------------------------


def test_parse_float_handles_scientific_notation() -> None:
    assert _parse_float("1.23e-5") == 1.23e-5
    assert _parse_float("6.84E-7") == 6.84e-7
    assert _parse_float("0.001") == 0.001
    assert _parse_float(".") is None


def test_spliceai_delta_rejects_out_of_range() -> None:
    # Delta scores are probabilities in [0, 1]; a leaked DP position (e.g. 22) is dropped.
    assert _spliceai_delta(0.42) == 0.42
    assert _spliceai_delta(0.0) == 0.0
    assert _spliceai_delta(1.0) == 1.0
    assert _spliceai_delta(22.0) is None
    assert _spliceai_delta(-1.0) is None
    assert _spliceai_delta(None) is None


def test_parse_spliceai_pred_drops_position_leak() -> None:
    # Standard format ALLELE|SYMBOL|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|... — delta scores only.
    parsed = _parse_spliceai_pred("A|GENE|0.01|0.00|0.93|0.00|-5|12|-8|22")
    assert parsed == {"spliceai_ds_ag": 0.01, "spliceai_ds_al": 0.0, "spliceai_ds_dg": 0.93, "spliceai_ds_dl": 0.0}

    # An offset format where a DP position lands in a DS slot: the >1 value is dropped.
    offset = _parse_spliceai_pred("A|0.00|0.00|0.00|22|35|-8|22|0|0")
    assert "spliceai_ds_dl" not in offset
    assert all(0.0 <= v <= 1.0 for v in offset.values())


# --- CLIN-59: a population count is read only from its source's own field --------------------
# INFO/AC is the callset's own allele count, a reserved VCF key a joint caller writes (the
# demo quartet's clair3 VCF has AC=4;AN=8). The parser read it as gnomAD's AC wherever the
# CSQ had none, so a maximum gnomAD AC filter filtered on the family's own count. AC_Adj,
# nhomalt and hemi, without a prefix, name no source either. Each key here was read before.

_CSQ_HEADER = (
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP.'
    ' Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|gnomADe_AF|gnomAD_AC'
    '|gnomAD_nhomalt|gnomAD_Hemi">'
)
# A transcript with gnomAD's exome frequency and no gnomAD count.
_CSQ_WITHOUT_COUNTS = "T|missense_variant|MODERATE|GENE1|ENSG1|Transcript|ENST1|0.0001|||"
# Each unprefixed key, and the gnomAD count the parser filled from it.
_UNPREFIXED_KEYS = {
    "AC": "gnomad_ac",
    "AC_Adj": "gnomad_ac",
    "nhomalt": "gnomad_hom_count",
    "NHOMALT": "gnomad_hom_count",
    "hemi": "gnomad_hemi_count",
    "HEMI": "gnomad_hemi_count",
}
# The filter of each gnomAD count.
_MAXIMUM_OF = {
    "gnomad_ac": "max_gnomad_ac",
    "gnomad_hom_count": "max_gnomad_hom_count",
    "gnomad_hemi_count": "max_gnomad_hemi_count",
}


def _csq_state() -> AnnotationHeaderState:
    state = AnnotationHeaderState()
    update_annotation_header_state(state, _CSQ_HEADER)
    return state


@pytest.mark.parametrize("key", list(_UNPREFIXED_KEYS))
def test_an_unprefixed_info_count_fills_no_gnomad_count(key: str) -> None:
    annotations = extract_small_variant_annotations(
        {key: "4", "AN": "8", "CSQ": _CSQ_WITHOUT_COUNTS}, _csq_state()
    )

    assert annotations == [
        {
            "gene": "GENE1",
            "gene_id": "ENSG1",
            "transcript_id": "ENST1",
            "feature_type": "Transcript",
            "impact": "MODERATE",
            "effect": "missense_variant",
            "gnomad_af": 0.0001,
            "population_frequencies": {"gnomad_exomes_af": 0.0001, "gnomad_af": 0.0001},
        }
    ]


def test_a_record_without_csq_gets_no_annotation_from_its_own_allele_count() -> None:
    # Before, [{"extra": {"gnomad_ac": 4}}]: an unannotated variant with a gnomAD AC.
    assert extract_small_variant_annotations({"AC": "4", "AN": "8"}, AnnotationHeaderState()) == []


def test_the_gnomad_counts_come_from_gnomads_own_fields() -> None:
    # The CSQ's gnomAD counts, whatever the callset's keys say; a gnomAD count in INFO, under
    # gnomAD's own name, fills a transcript without one, as before.
    state = _csq_state()
    from_csq = extract_small_variant_annotations(
        {
            "AC": "4",
            "nhomalt": "2",
            "HEMI": "2",
            "CSQ": "T|missense_variant|MODERATE|GENE1|ENSG1|Transcript|ENST1|0.0001|12|0|3",
        },
        state,
    )
    from_info = extract_small_variant_annotations(
        {"AC": "4", "gnomAD_AC": "7", "gnomAD_nhomalt": "1", "CSQ": _CSQ_WITHOUT_COUNTS}, state
    )

    assert [(a["extra"], a["gnomad_hom_count"]) for a in from_csq] == [
        ({"gnomad_ac": 12, "gnomad_hemi_count": 3}, 0)
    ]
    assert [(a["extra"], a["gnomad_hom_count"]) for a in from_info] == [({"gnomad_ac": 7}, 1)]


def test_a_vep_table_row_reads_no_unprefixed_count() -> None:
    annotation = normalize_small_variant_annotation_entry(
        {
            "SYMBOL": "GENE1",
            "Consequence": "missense_variant",
            "gnomADe_AF": "0.0001",
            **{key: "4" for key in _UNPREFIXED_KEYS},
        }
    )

    assert annotation["gene"] == "GENE1"
    assert not {"extra", "gnomad_hom_count"} & set(annotation)


@pytest.mark.parametrize("key", list(_UNPREFIXED_KEYS))
def test_a_maximum_gnomad_count_keeps_a_variant_whose_only_count_is_unprefixed(key: str) -> None:
    # The import stores no count in the column the SQL filter reads (annotations, and the index's maximum over the transcripts), where
    # ifNull(count, 0) <= maximum keeps it too: as absent from gnomAD.
    field = _UNPREFIXED_KEYS[key]
    annotations = extract_small_variant_annotations(
        {key: "4", "AN": "8", "CSQ": _CSQ_WITHOUT_COUNTS}, _csq_state()
    )
    # No count stored in the column the SQL reads: ifNull(count, 0) <= the maximum keeps it.
    assert all(getattr(_annotation_columns(annotation), field) is None for annotation in annotations)
    assert [_annotation_count(annotation, field) for annotation in annotations] == [None]


# --------------------------------------------------------------------------- #
# ClinVar: the ClinVar file the pipeline annotated, before VEP's cache
# --------------------------------------------------------------------------- #

_CLINVAR_CSQ_HEADER = (
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. '
    'Format: Allele|Consequence|SYMBOL|Feature|CLIN_SIG|ClinVar_CLNSIG">'
)
_CLINVAR_FIELD_HEADER = (
    '##INFO=<ID=ClinVar_CLNSIG,Number=.,Type=String,Description="Aggregate germline classification for '
    'this single variant. CLNSIG field from [PATH]/clinvar_20261101.vcf.gz">'
)


def _clinvar_state(*lines: str) -> AnnotationHeaderState:
    state = AnnotationHeaderState()
    for line in lines:
        update_annotation_header_state(state, line)
    return state


def test_the_clinvar_files_classification_wins_over_the_vep_caches() -> None:
    # VEP --custom's ClinVar_CLNSIG beside the cache's CLIN_SIG (an older release, of the known
    # variants at the position, joined with "&"). The cache's was read first.
    state = _clinvar_state(_CLINVAR_CSQ_HEADER, _CLINVAR_FIELD_HEADER)
    [annotation] = extract_small_variant_annotations(
        {"CSQ": "G|missense_variant|GENE1|ENST1|uncertain_significance&pathogenic|Benign"}, state
    )
    assert annotation["clinvar"] == "Benign"
    assert annotation["clinvar_vep_cache"] == "uncertain_significance&pathogenic"


def test_a_variant_the_clinvar_file_has_no_record_of_has_no_clinvar_classification() -> None:
    # The file holds every variant ClinVar classifies at a precise location: the cache's
    # classification of a variant it lacks is kept apart, never read as ClinVar's.
    state = _clinvar_state(_CLINVAR_CSQ_HEADER, _CLINVAR_FIELD_HEADER)
    [annotation] = extract_small_variant_annotations({"CSQ": "G|missense_variant|GENE1|ENST1|pathogenic|"}, state)
    assert "clinvar" not in annotation
    assert annotation["clinvar_vep_cache"] == "pathogenic"


def test_without_a_clinvar_file_the_vep_caches_classification_is_the_clinvar_one() -> None:
    state = _clinvar_state(
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|SYMBOL|Feature|CLIN_SIG">'
    )
    [annotation] = extract_small_variant_annotations({"CSQ": "G|missense_variant|GENE1|ENST1|pathogenic"}, state)
    assert annotation["clinvar"] == "pathogenic"
    assert "clinvar_vep_cache" not in annotation
    assert not clinvar_file_annotated(state)


def test_an_info_clnsig_from_a_clinvar_file_is_not_overwritten_by_the_caches() -> None:
    # bcftools annotate (or vcfanno) writes ClinVar's CLNSIG to INFO; each CSQ entry's CLIN_SIG
    # overwrote it.
    state = _clinvar_state(
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|SYMBOL|Feature|CLIN_SIG">',
        '##INFO=<ID=CLNSIG,Number=.,Type=String,Description="Aggregate germline classification for this single variant">',
    )
    annotations = extract_small_variant_annotations(
        {"CLNSIG": "Likely_benign", "CSQ": "G|missense_variant|GENE1|ENST1|pathogenic,G|missense_variant|GENE1|ENST2|"},
        state,
    )
    assert [annotation["clinvar"] for annotation in annotations] == ["Likely_benign", "Likely_benign"]
    assert annotations[0]["clinvar_vep_cache"] == "pathogenic"
    assert "clinvar_vep_cache" not in annotations[1]
    # A record without the INFO key, in a file whose header declares it: not in ClinVar.
    [bare] = extract_small_variant_annotations({"CSQ": "G|missense_variant|GENE1|ENST1|pathogenic"}, state)
    assert "clinvar" not in bare and bare["clinvar_vep_cache"] == "pathogenic"


def test_the_header_names_the_clinvar_file_vep_annotated() -> None:
    state = _clinvar_state(_CLINVAR_CSQ_HEADER, _CLINVAR_FIELD_HEADER)
    assert clinvar_file_annotated(state)
    assert state.clinvar_file_name == "clinvar_20261101.vcf.gz"
    assert "ClinVar_CLNSIG" in state.info_ids
    # A ClinVar field in the CSQ alone (no INFO line describing it) still counts.
    assert clinvar_file_annotated(_clinvar_state(_CLINVAR_CSQ_HEADER))
    # ClinVar's own CLNSIG description names no file.
    assert clinvar_file_in_description("Aggregate germline classification for this single variant") is None
    assert clinvar_file_in_description("CLNSIG. CLNSIG field from /data/ref/clinvar.vcf.gz") == "clinvar.vcf.gz"


def test_a_vep_table_row_reads_clinvar_by_what_the_table_declares() -> None:
    # A ClinVar file declared in the table: a row without its value has no ClinVar
    # classification, whatever the cache's CLIN_SIG.
    declared = normalize_small_variant_annotation_entry(
        {"SYMBOL": "GENE1", "CLIN_SIG": "pathogenic"}, clinvar_file_annotated=True
    )
    assert "clinvar" not in declared and declared["clinvar_vep_cache"] == "pathogenic"
    cache_only = normalize_small_variant_annotation_entry({"SYMBOL": "GENE1", "CLIN_SIG": "pathogenic"})
    assert cache_only["clinvar"] == "pathogenic" and "clinvar_vep_cache" not in cache_only
    both = normalize_small_variant_annotation_entry(
        {"SYMBOL": "GENE1", "CLIN_SIG": "pathogenic", "ClinVar_CLNSIG": "Benign"}
    )
    assert (both["clinvar"], both["clinvar_vep_cache"]) == ("Benign", "pathogenic")
