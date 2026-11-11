"""The VCF annotation parser: what a record's CSQ, its INFO fields and a VEP table row give a
variant, and the number parsing under it.

The parser reads VEP's CSQ by the header's own field list, falls back to the INFO keys of other
annotators, and reads a VEP table row the same way. Its number helpers read scientific
notation, and keep only SpliceAI's delta scores, which are probabilities in [0, 1]: a DP
position (an offset in bases) that lands in a delta-score slot is dropped. A population count
is read only from its source's own field (below).
"""

import pytest

from backend.app.services.variant_annotation_parser import (
    AnnotationHeaderState,
    _parse_float,
    _parse_int,
    entry_alt_allele,
    vep_allele_forms,
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
        # VEP's MAX_AF is not gnomAD's popmax: it is kept as itself.
        "max_af": 0.0005,
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
    # Standard format ALLELE|SYMBOL|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|…: the delta scores, and
    # where SpliceAI places each change (bases from the variant).
    parsed = _parse_spliceai_pred("A|GENE|0.01|0.00|0.93|0.00|-5|12|-8|22")
    assert parsed == {
        "spliceai_ds_ag": 0.01,
        "spliceai_ds_al": 0.0,
        "spliceai_ds_dg": 0.93,
        "spliceai_ds_dl": 0.0,
        "spliceai_dp_ag": -5,
        "spliceai_dp_al": 12,
        "spliceai_dp_dg": -8,
        "spliceai_dp_dl": 22,
    }

    # An offset format where a DP position lands in a DS slot: the >1 value is dropped, and
    # no position is read from the shifted slots.
    offset = _parse_spliceai_pred("A|0.00|0.00|0.00|22|35|-8|22|0|0")
    assert "spliceai_ds_dl" not in offset
    assert not any(key.startswith("spliceai_dp_") for key in offset)
    assert all(0.0 <= v <= 1.0 for v in offset.values())


# --- a population count is read only from its source's own field --------------------
# INFO/AC is the callset's own allele count, a reserved VCF key a joint caller writes (the
# demo quartet's clair3 VCF has AC=4;AN=8). Read as gnomAD's AC wherever the CSQ has none, it
# would make a maximum gnomAD AC filter filter on the family's own count. AC_Adj, nhomalt and
# hemi, without a prefix, name no source either.

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
    # Not an unannotated variant with a gnomAD AC.
    assert extract_small_variant_annotations({"AC": "4", "AN": "8"}, AnnotationHeaderState()) == []


def test_the_gnomad_counts_come_from_gnomads_own_fields() -> None:
    # The CSQ's gnomAD counts, whatever the callset's keys say; a gnomAD count in INFO, under
    # gnomAD's own name, fills a transcript without one.
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


# --------------------------------------------------------------------------- #
# A multi-allelic record: each entry names the ALT it is of
# --------------------------------------------------------------------------- #

_ALLELE_CSQ_HEADER = (
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. '
    'Format: Allele|Consequence|SYMBOL|Feature|REVEL">'
)


def test_each_entry_of_a_multi_allelic_record_names_its_alt() -> None:
    # Each allele's entries name their alt, not read as the record's.
    state = _clinvar_state(_ALLELE_CSQ_HEADER)
    annotations = extract_small_variant_annotations(
        {"CSQ": "G|missense_variant|GENE1|ENST1|0.2,T|stop_gained|GENE1|ENST1|"}, state, ref="A", alt="G,T"
    )
    assert [(a["alt_allele"], a["effect"]) for a in annotations] == [("G", "missense_variant"), ("T", "stop_gained")]
    # A record with one ALT names none.
    [single] = extract_small_variant_annotations({"CSQ": "G|missense_variant|GENE1|ENST1|0.2"}, state, ref="A", alt="G")
    assert "alt_allele" not in single


def test_a_multi_allelic_indels_entries_are_read_as_vep_writes_its_alleles() -> None:
    # VEP trims the first base all alleles share from an indel record: AT>A,ATT is "-" and "TT".
    assert vep_allele_forms("AT", ["A", "ATT"]) == ["-", "TT"]
    # Alleles that do not share it, and same-length alleles, are written as they are.
    assert vep_allele_forms("A", ["G", "AT"]) == ["G", "AT"]
    assert vep_allele_forms("A", ["G", "T"]) == ["G", "T"]
    assert entry_alt_allele({"Allele": "-"}, "AT", ["A", "ATT"]) == "A"
    assert entry_alt_allele({"Allele": "TT"}, "AT", ["A", "ATT"]) == "ATT"
    # VEP's allele number decides where the entry has one; --minimal's form is read too.
    assert entry_alt_allele({"Allele": "TT", "ALLELE_NUM": "1"}, "AT", ["A", "ATT"]) == "A"
    assert entry_alt_allele({"Allele": "T"}, "AT", ["A", "ATT"]) == "ATT"
    # VEP's form first: AC>AA,A writes "A" for AA and "-" for A, though "A" is also an ALT.
    assert entry_alt_allele({"Allele": "A"}, "AC", ["AA", "A"]) == "AA"
    # snpEff writes the ALT itself.
    assert entry_alt_allele({"Allele": "A"}, "AC", ["AA", "A"], vep=False) == "A"
    # An allele none of them is names none.
    assert entry_alt_allele({"Allele": "C"}, "A", ["G", "T"]) is None


# --------------------------------------------------------------------------- #
# A field listing several values is none of them
# --------------------------------------------------------------------------- #


def test_a_score_listing_several_values_is_read_as_none_and_kept_as_text() -> None:
    # dbNSFP's REVEL per transcript, aliased without matching the transcript: the highest was
    # read, another transcript's score shown and filtered as this one's.
    [annotation] = extract_small_variant_annotations(
        {"CSQ": "G|missense_variant|GENE1|ENST1|0.12&0.91"}, _clinvar_state(_ALLELE_CSQ_HEADER)
    )
    assert "revel" not in annotation
    assert annotation["extra"]["revel_values"] == "0.12&0.91"
    # The same value repeated, or one beside missing ones, is that value.
    assert _parse_float("0.5&0.5") == 0.5
    assert _parse_float(".&0.5") == 0.5
    assert _parse_float("0.12&0.91") is None
    assert _parse_int("3&5") is None and _parse_int("1.0") == 1
    assert _parse_float("1.2E+05") == 120000.0


def test_a_dbnsfp_score_list_reads_the_entrys_own_transcript() -> None:
    # dbNSFP lists its scores in the order of its Ensembl_transcriptid column.
    state = _clinvar_state(
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|SYMBOL|Feature|'
        'Ensembl_transcriptid|REVEL">'
    )
    annotations = extract_small_variant_annotations(
        {
            "CSQ": "G|missense_variant|GENE1|ENST2.3|ENST1&ENST2&ENST3|0.12&0.91&0.4,"
            "G|missense_variant|GENE1|ENST9|ENST1&ENST2&ENST3|0.12&0.91&0.4"
        },
        state,
    )
    assert annotations[0]["revel"] == 0.91
    # A transcript the list lacks: none of them.
    assert "revel" not in annotations[1] and annotations[1]["extra"]["revel_values"] == "0.12&0.91&0.4"


def test_a_population_frequency_listing_several_values_is_read_as_none() -> None:
    # A --custom file with several records at the position: their highest made a rare variant
    # common.
    annotation = normalize_small_variant_annotation_entry({"SYMBOL": "GENE1", "gnomADg_AF": "0.2&0.00001"})
    assert "gnomad_genomes_af" not in annotation.get("population_frequencies", {})
    assert annotation["extra"]["gnomad_genomes_af_values"] == "0.2&0.00001"


# --------------------------------------------------------------------------- #
# gnomAD's popmax and VEP's MAX_AF
# --------------------------------------------------------------------------- #


def test_vep_max_af_is_kept_apart_from_gnomads_popmax() -> None:
    # MAX_AF is VEP's highest over the 1000 Genomes and gnomAD populations, not gnomAD's popmax.
    annotation = normalize_small_variant_annotation_entry({"SYMBOL": "GENE1", "MAX_AF": "0.012"})
    assert annotation["population_frequencies"] == {"max_af": 0.012}
    # The filter column reads it where gnomAD has no popmax of its own.
    assert _annotation_columns(annotation).gnomad_popmax_af == pytest.approx(0.012)
    both = normalize_small_variant_annotation_entry(
        {"SYMBOL": "GENE1", "MAX_AF": "0.012", "gnomADe_AF_grpmax": "0.0004", "gnomADg_AF_grpmax": "0.0006"}
    )
    # gnomAD v4's grpmax, exome and genome: their highest is gnomAD's popmax, and the column's.
    assert both["population_frequencies"]["gnomad_popmax_af"] == 0.0006
    assert both["population_frequencies"]["max_af"] == 0.012
    assert _annotation_columns(both).gnomad_popmax_af == pytest.approx(0.0006)


# --------------------------------------------------------------------------- #
# The fields the new annotation workflow delivers (the field contract in data-import.md)
# --------------------------------------------------------------------------- #


def test_the_clinvar_files_review_status_conditions_and_ids_are_read() -> None:
    # VEP --custom: the file's INFO fields as ClinVar_*, its commas written as "&", and the
    # matched record's ID (its VariationID) under the short name itself.
    annotation = normalize_small_variant_annotation_entry(
        {
            "SYMBOL": "GENE1",
            "ClinVar_CLNSIG": "Pathogenic",
            "ClinVar_CLNREVSTAT": "criteria_provided&_multiple_submitters&_no_conflicts",
            "ClinVar_CLNDN": "Condition_A&not_provided",
            "ClinVar_ALLELEID": "98765",
            "ClinVar": "12345",
        }
    )
    assert annotation["clinvar_review_status"] == "criteria provided, multiple submitters, no conflicts"
    assert annotation["clinvar_conditions"] == "Condition_A&not_provided"
    assert (annotation["clinvar_variation_id"], annotation["clinvar_allele_id"]) == ("12345", "98765")
    # An ID that is no VariationID is not kept as one.
    assert "clinvar_variation_id" not in normalize_small_variant_annotation_entry({"SYMBOL": "G", "ClinVar": "Pathogenic"})


@pytest.mark.parametrize(
    ("status", "stars"),
    [
        ("practice guideline", 4),
        ("reviewed by expert panel", 3),
        ("criteria provided, multiple submitters, no conflicts", 2),
        ("criteria provided, conflicting classifications", 1),
        ("criteria_provided,_single_submitter", 1),
        ("no assertion criteria provided", 0),
        ("no classification provided", 0),
        ("something else", None),
        (None, None),
    ],
)
def test_clinvar_stars(status, stars) -> None:
    from backend.app.services.clinvar_review_status import clinvar_stars

    assert clinvar_stars(status) == stars


def test_clinvar_conditions_are_listed_once_as_words() -> None:
    from backend.app.services.clinvar_review_status import clinvar_conditions

    assert clinvar_conditions("Condition_A|not_provided&Condition_A") == ["Condition A", "not provided"]
    assert clinvar_conditions(None) == []


def test_gnomads_allele_number_faf95_and_filters_and_vep_nmd_are_read() -> None:
    annotation = normalize_small_variant_annotation_entry(
        {
            "SYMBOL": "GENE1",
            "NMD": "NMD_escaping_variant",
            "gnomADe_AN": "1200",
            "gnomADg_AN": "800",
            "gnomADe_fafmax_faf95_max": "0.004",
            "gnomADg_fafmax_faf95_max": "0.007",
            "gnomADg_FILTER": "AC0",
            "SpliceAI_pred_DS_DG": "0.82",
            "SpliceAI_pred_DP_DG": "-14",
        }
    )
    assert annotation["nmd"] == "NMD_escaping_variant"
    # The allele number as gnomAD's total, as its counts; the FAF95 the higher of the two.
    assert annotation["extra"]["gnomad_an"] == 2000
    assert annotation["population_frequencies"]["gnomad_faf95_af"] == 0.007
    assert annotation["gnomad_genomes_filter"] == "AC0"
    assert (annotation["spliceai_ds_dg"], annotation["spliceai_dp_dg"]) == (0.82, -14)


def test_gnomad_mt_frequencies_are_read_homoplasmic_and_heteroplasmic_apart() -> None:
    annotation = normalize_small_variant_annotation_entry(
        {
            "SYMBOL": "MT-ATP6",
            "GNOMAD_MT31_AF_hom": "0.0012",
            "GNOMAD_MT31_AF_het": "0.0001",
            "GNOMAD_MT31_max_hl": "0.92",
            "GNOMAD_MT31_AN": "56434",
        }
    )
    assert annotation["population_frequencies"] == {"gnomad_mt_af_hom": 0.0012, "gnomad_mt_af_het": 0.0001}
    assert annotation["gnomad_mt_max_hl"] == 0.92
    assert annotation["extra"]["gnomad_mt_an"] == 56434
