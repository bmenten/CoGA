from __future__ import annotations

from functools import lru_cache
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class AnnotationHeaderState:
    ann_fields: List[str] | None = None
    csq_fields: List[str] | None = None
    # The INFO keys the header declares (``##INFO=<ID=…``), and the ClinVar file the INFO
    # line of a ClinVar field names (``clinvar_file_annotated``, ``clinvar_file_in_description``).
    info_ids: set[str] = field(default_factory=set)
    clinvar_file_name: str | None = None


# "-" is how VEP's table output writes an empty value: read as one, it became a ClinVar
# classification "-" (and an rsid "-").
_NULL_VALUES = {"", ".", "-", "NA", "N/A", "null", "None"}
_TRUE_VALUES = {"YES", "TRUE", "1"}
# The numbers a value holds (``_numbers``), compiled once.
_FLOAT_PATTERN = re.compile(r"-?\d+(?:\.\d+)?(?:e[+-]?\d+)?", re.IGNORECASE)
# How a field lists several values: VEP joins them with "&" in a VCF's CSQ, a table or a
# plugin with "," or ";".
_LIST_SEPARATORS = re.compile(r"[&,;]")

# A record's ClinVar classification is read from the ClinVar file the pipeline annotated it
# with (VEP --custom's ClinVar_CLNSIG, the CLNSIG of bcftools annotate or vcfanno), and from
# VEP's cache (CLIN_SIG) only where no ClinVar file was annotated. The cache holds an older
# ClinVar release, read for the known variants at the record's position, and joins their
# classifications with "&" (``uncertain_significance&likely_pathogenic``). It was read first,
# so it stood in for the ClinVar file's classification. Where a ClinVar file was annotated,
# the cache's classification is kept apart (``clinvar_vep_cache``) and never read as
# ClinVar's, also for a variant the file has no record of: ClinVar's file holds every
# variant ClinVar classifies at a precise location.
CLINVAR_FILE_ALIASES = ("ClinVar_CLNSIG", "CLNSIG", "CLINVAR")
_CLINVAR_CACHE_KEY = "clinvar_vep_cache"

_ANN_ALIASES = {
    "gene": ["SYMBOL", "Gene_Name", "GENE", "gene"],
    "gene_id": ["Gene", "Gene_ID", "GENEID", "gene_id"],
    "transcript_id": ["Feature", "Feature_ID", "Transcript", "transcript_id"],
    "feature_type": ["Feature_type", "Feature_Type"],
    "transcript_biotype": ["BIOTYPE", "Transcript_BioType"],
    "impact": ["IMPACT", "Annotation_Impact", "annotation_impact"],
    "effect": ["Consequence", "Annotation", "EFFECT"],
    "hgvsc": ["HGVSc", "HGVS.c", "HGVS_C", "HGVS_c"],
    "hgvsp": ["HGVSp", "HGVS.p", "HGVS_P", "HGVS_p"],
    "exon": ["EXON", "Exon_Rank"],
    "intron": ["INTRON"],
    "clinvar": list(CLINVAR_FILE_ALIASES),
    _CLINVAR_CACHE_KEY: ["CLIN_SIG"],
    "rsid": ["Existing_variation", "DBSNP", "RSID", "ID"],
    "lof": ["LoF", "LOF"],
    "lof_filter": ["LoF_filter", "LOF_FILTER"],
    "lof_flags": ["LoF_flags", "LOF_FLAGS"],
    "sift": ["SIFT"],
    "polyphen": ["PolyPhen", "POLYPHEN"],
    "canonical": ["CANONICAL"],
    "mane_select": ["MANE_SELECT"],
    "mane_plus_clinical": ["MANE_PLUS_CLINICAL"],
    "splice_region": ["SpliceRegion"],
    "alpha_missense_class": ["am_class", "AlphaMissense_class"],
    "utr5_annotation": ["5UTR_annotation"],
    "utr5_consequence": ["5UTR_consequence"],
    "existing_inframe_oorfs": ["Existing_InFrame_oORFs"],
    "existing_outofframe_oorfs": ["Existing_OutOfFrame_oORFs"],
    "existing_uorfs": ["Existing_uORFs"],
    # The ClinVar file's review status (``criteria_provided,_multiple_submitters,…``, read as
    # words: ``_clinvar_text``), conditions (CLNDN), allele ID and, from VEP --custom, the
    # matched record's ID: its VariationID (``ClinVar``, digits only).
    "clinvar_review_status": ["ClinVar_CLNREVSTAT", "CLNREVSTAT"],
    "clinvar_conditions": ["ClinVar_CLNDN", "CLNDN"],
    "clinvar_allele_id": ["ClinVar_ALLELEID", "ALLELEID"],
    "clinvar_variation_id": ["ClinVar", "ClinVar_ID", "CLINVAR_ID"],
    # VEP's NMD plugin: ``NMD_escaping_variant`` for a variant predicted to escape
    # nonsense-mediated decay.
    "nmd": ["NMD"],
    # gnomAD's own site filter (PASS, AC0, RF, InbreedingCoeff …), where a --custom gnomAD file
    # was annotated with its FILTER.
    "gnomad_filter": ["gnomAD_FILTER", "GNOMAD_FILTER"],
    "gnomad_exomes_filter": ["gnomADe_FILTER", "GNOMADE_FILTER"],
    "gnomad_genomes_filter": ["gnomADg_FILTER", "GNOMADG_FILTER"],
}

# A population frequency or count is read only from a field whose name states its source
# (gnomAD, gnomADe, gnomADg, TOPMed), or from VEP's MAX_AF and POPMAX_AF, which only a
# population annotation writes. gnomAD's popmax (named grpmax since gnomAD v4) is gnomAD's
# own; MAX_AF is VEP's highest frequency over every population it reads (1000 Genomes and
# gnomAD's), a different value: it was read as gnomAD's popmax. It is kept as ``max_af``, and
# stands in for the popmax only in the stored filter column (``clickhouse_variant_rows``). The aliases are read from the record's INFO as well as from
# each CSQ/ANN entry, and an entry without a value keeps the INFO's. An unprefixed key names
# no source: INFO/AC is the callset's own allele count (a reserved VCF key that joint callers
# write), and read as gnomAD's AC it made a maximum gnomAD AC filter filter on the family's
# own count (CLIN-59); AC_Adj, nhomalt and hemi are not read either. A record without a
# source-named field has no value, and the frequency and count filters keep it, as absent
# from gnomAD (``ifNull(value, 0) <= maximum``).
_POPULATION_ALIASES = {
    "gnomad_af": ["gnomAD_AF", "GNOMAD_AF"],
    "gnomad_exomes_af": ["gnomADe_AF", "GNOMADE_AF"],
    "gnomad_genomes_af": ["gnomADg_AF", "GNOMADG_AF"],
    "gnomad_popmax_af": [
        "gnomAD_AF_POPMAX",
        "gnomAD_popmax_AF",
        "gnomAD_AF_popmax",
        "gnomAD_AF_grpmax",
        "gnomAD_grpmax_AF",
        "GNOMAD_AF_GRPMAX",
    ],
    # As VEP names a --custom gnomAD exome or genome file's fields: their highest is gnomAD's
    # popmax where the record has no combined one, as for the allele frequency.
    "gnomad_exomes_popmax_af": ["gnomADe_AF_grpmax", "gnomADe_AF_popmax", "GNOMADE_AF_GRPMAX", "GNOMADE_AF_POPMAX"],
    "gnomad_genomes_popmax_af": ["gnomADg_AF_grpmax", "gnomADg_AF_popmax", "GNOMADG_AF_GRPMAX", "GNOMADG_AF_POPMAX"],
    "max_af": ["MAX_AF", "POPMAX_AF"],
    "topmed_af": ["TOPMed_AF", "TOPMED_AF"],
    # gnomAD's filtering allele frequency (95 % confidence, the highest over its genetic
    # ancestry groups), which BA1 and BS1 read where it is annotated; the higher of the exome
    # and genome ones where there is no combined one.
    "gnomad_faf95_af": [
        "gnomAD_fafmax_faf95_max",
        "gnomAD_fafmax_faf95_max_joint",
        "gnomAD_faf95_popmax",
        "GNOMAD_FAFMAX_FAF95_MAX",
    ],
    "gnomad_exomes_faf95_af": ["gnomADe_fafmax_faf95_max", "gnomADe_faf95_popmax", "GNOMADE_FAFMAX_FAF95_MAX"],
    "gnomad_genomes_faf95_af": ["gnomADg_fafmax_faf95_max", "gnomADg_faf95_popmax", "GNOMADG_FAFMAX_FAF95_MAX"],
    # gnomAD v3.1's mtDNA frequencies: of homoplasmic calls (heteroplasmy of 95 % or more) and
    # of heteroplasmic ones (10 % up to 95 %), apart. A population frequency, not the
    # heteroplasmy of the person tested.
    "gnomad_mt_af_hom": ["GNOMAD_MT31_AF_hom", "gnomAD_MT_AF_hom", "gnomADmt_AF_hom"],
    "gnomad_mt_af_het": ["GNOMAD_MT31_AF_het", "gnomAD_MT_AF_het", "gnomADmt_AF_het"],
}

# gnomAD's counts are read as gnomAD's total (CLIN-67): its combined count where the record
# has one, else its exome and genome counts added (``gnomADe_…`` and ``gnomADg_…``, as VEP
# names a --custom gnomAD file's fields), either alone when the other is missing. That is
# the total gnomAD's browser shows, the owner's choice of 2026-10-27. Each count lists its
# combined, exome and genome aliases. Before, ``gnomADe_nhomalt`` and ``gnomADg_nhomalt``
# were not read at all, and the allele count took the exome count alone.
_COUNT_ALIASES = {
    "gnomad_hom_count": (
        ["gnomAD_Hom", "GNOMAD_HOM", "gnomAD_hom", "gnomad_hom", "gnomAD_nhomalt", "GNOMAD_NHOMALT"],
        ["gnomADe_nhomalt", "GNOMADE_NHOMALT", "gnomADe_Hom", "GNOMADE_HOM"],
        ["gnomADg_nhomalt", "GNOMADG_NHOMALT", "gnomADg_Hom", "GNOMADG_HOM"],
    ),
}

_EXTRA_COUNT_ALIASES = {
    "gnomad_ac": (
        ["gnomAD_AC", "GNOMAD_AC"],
        ["gnomADe_AC", "GNOMADE_AC"],
        ["gnomADg_AC", "GNOMADG_AC"],
    ),
    "gnomad_hemi_count": (
        ["gnomAD_Hemi", "GNOMAD_HEMI", "gnomAD_hemi", "gnomad_hemi"],
        ["gnomADe_Hemi", "GNOMADE_HEMI", "gnomADe_hemi"],
        ["gnomADg_Hemi", "GNOMADG_HEMI", "gnomADg_hemi"],
    ),
    # The allele number beside the allele count: how many alleles gnomAD called at the site.
    "gnomad_an": (
        ["gnomAD_AN", "GNOMAD_AN"],
        ["gnomADe_AN", "GNOMADE_AN"],
        ["gnomADg_AN", "GNOMADG_AN"],
    ),
    "gnomad_mt_an": (["GNOMAD_MT31_AN", "gnomAD_MT_AN"], [], []),
}

_SPLICE_ALIASES = {
    "spliceai_ds_ag": ["SpliceAI_pred_DS_AG", "DS_AG", "SPLICEAI_DS_AG"],
    "spliceai_ds_al": ["SpliceAI_pred_DS_AL", "DS_AL", "SPLICEAI_DS_AL"],
    "spliceai_ds_dg": ["SpliceAI_pred_DS_DG", "DS_DG", "SPLICEAI_DS_DG"],
    "spliceai_ds_dl": ["SpliceAI_pred_DS_DL", "DS_DL", "SPLICEAI_DS_DL"],
}

# Where SpliceAI places each delta score's change, relative to the variant (bases; negative
# upstream): the position the score describes.
_POSITION_ALIASES = {
    "spliceai_dp_ag": ["SpliceAI_pred_DP_AG", "DP_AG", "SPLICEAI_DP_AG"],
    "spliceai_dp_al": ["SpliceAI_pred_DP_AL", "DP_AL", "SPLICEAI_DP_AL"],
    "spliceai_dp_dg": ["SpliceAI_pred_DP_DG", "DP_DG", "SPLICEAI_DP_DG"],
    "spliceai_dp_dl": ["SpliceAI_pred_DP_DL", "DP_DL", "SPLICEAI_DP_DL"],
}

_SCORE_ALIASES = {
    "cadd_raw": ["CADD_RAW", "CADD_RAW_HG38"],
    "cadd_phred": ["CADD_PHRED", "CADD_PHRED_HG38"],
    "revel": ["REVEL"],
    "gene_pli": ["PLI", "pLI", "GENE_PLI", "LOF_PLI"],
    "gene_missense_z": ["MISSENSE_Z", "missense_Z", "MIS_Z", "GENE_MISSENSE_Z"],
    "alpha_missense_pathogenicity": ["am_pathogenicity", "AlphaMissense_pathogenicity"],
    # gnomAD-MT's highest heteroplasmy level observed in any of its samples.
    "gnomad_mt_max_hl": ["GNOMAD_MT31_max_hl", "gnomAD_MT_max_hl"],
}

# Each value of _ANN_ALIASES an entry sets only when it has one, except these flags, which
# an entry without one clears from what the record's INFO set (_normalize_annotation_entry).
_FLAG_KEYS = frozenset({"canonical", "mane_select", "mane_plus_clinical"})

_Aliases = Sequence[str]
_CountAliases = tuple[_Aliases, _Aliases, _Aliases]


@dataclass(frozen=True)
class _AliasTables:
    """The alias tables an annotation is read by, field name to the keys it may be under.

    ``_ALL_ALIASES`` reads any mapping (a record's INFO, a VEP TSV row). The entries of one
    CSQ or ANN header are read by its own tables (``_header_alias_tables``): each narrowed
    to the keys that header declares, as no entry holds any other. An entry reads the same
    by either, faster by its own: a whole-genome VCF has millions of entries, and most
    aliases name a field its header does not declare."""

    ann: tuple[tuple[str, _Aliases], ...]
    score: tuple[tuple[str, _Aliases], ...]
    count: tuple[tuple[str, _CountAliases], ...]
    extra_count: tuple[tuple[str, _CountAliases], ...]
    splice: tuple[tuple[str, _Aliases], ...]
    population: tuple[tuple[str, _Aliases], ...]
    position: tuple[tuple[str, _Aliases], ...]


_ALL_ALIASES = _AliasTables(
    ann=tuple(_ANN_ALIASES.items()),
    score=tuple(_SCORE_ALIASES.items()),
    count=tuple(_COUNT_ALIASES.items()),
    extra_count=tuple(_EXTRA_COUNT_ALIASES.items()),
    splice=tuple(_SPLICE_ALIASES.items()),
    population=tuple(_POPULATION_ALIASES.items()),
    position=tuple(_POSITION_ALIASES.items()),
)
# Every key an annotation is read from, by its aliases or by name (SpliceAI_pred).
_ANNOTATION_KEYS = frozenset(
    [alias for _key, aliases in _ALL_ALIASES.ann for alias in aliases]
    + [alias for _key, aliases in _ALL_ALIASES.score for alias in aliases]
    + [alias for _key, lists in _ALL_ALIASES.count for aliases in lists for alias in aliases]
    + [alias for _key, lists in _ALL_ALIASES.extra_count for aliases in lists for alias in aliases]
    + [alias for _key, aliases in _ALL_ALIASES.splice for alias in aliases]
    + [alias for _key, aliases in _ALL_ALIASES.population for alias in aliases]
    + [alias for _key, aliases in _ALL_ALIASES.position for alias in aliases]
    + ["SpliceAI_pred"]
)


def _declared(aliases: _Aliases, fields: frozenset[str]) -> tuple[str, ...]:
    return tuple(alias for alias in aliases if alias in fields)


def _narrowed(
    table: tuple[tuple[str, _Aliases], ...], fields: frozenset[str], *, keep: frozenset[str] = frozenset()
) -> tuple[tuple[str, _Aliases], ...]:
    """``table`` with each key's aliases among ``fields``, and only the keys with one, or in
    ``keep``."""
    narrowed = ((key, _declared(aliases, fields)) for key, aliases in table)
    return tuple((key, aliases) for key, aliases in narrowed if aliases or key in keep)


def _narrowed_counts(
    table: tuple[tuple[str, _CountAliases], ...], fields: frozenset[str]
) -> tuple[tuple[str, _CountAliases], ...]:
    result: list[tuple[str, _CountAliases]] = []
    for key, (combined, exome, genome) in table:
        aliases = (_declared(combined, fields), _declared(exome, fields), _declared(genome, fields))
        if any(aliases):
            result.append((key, aliases))
    return tuple(result)


@lru_cache(maxsize=16)
def _header_alias_tables(fields: tuple[str, ...]) -> _AliasTables:
    """The alias tables of the entries of a CSQ or ANN header declaring ``fields``: each
    field's aliases that the header declares, in their order, and only the fields with one
    -- and the flags, which an entry without one clears."""
    declared = frozenset(fields)
    return _AliasTables(
        ann=_narrowed(_ALL_ALIASES.ann, declared, keep=_FLAG_KEYS),
        score=_narrowed(_ALL_ALIASES.score, declared),
        count=_narrowed_counts(_ALL_ALIASES.count, declared),
        extra_count=_narrowed_counts(_ALL_ALIASES.extra_count, declared),
        splice=_narrowed(_ALL_ALIASES.splice, declared),
        population=_narrowed(_ALL_ALIASES.population, declared),
        position=_narrowed(_ALL_ALIASES.position, declared),
    )


def _clean_value(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if value in _NULL_VALUES:
            return None
    return value


def _parse_bool(value: Any) -> Optional[bool]:
    value = _clean_value(value)
    if value is None:
        return None
    if isinstance(value, str):
        return value.upper() in _TRUE_VALUES
    if isinstance(value, bool):
        return value
    return None


def _parse_presence_bool(value: Any) -> Optional[bool]:
    value = _clean_value(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return True if value else None
    if isinstance(value, (int, float)):
        return True if value else None
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in {"", ".", "-", "NO", "FALSE", "0"}:
            return None
    return True


def _numbers(value: Any) -> list[float]:
    """The different numbers a value holds, in the order it gives them."""
    value = _clean_value(value)
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    found: list[float] = []
    for text in _FLOAT_PATTERN.findall(str(value)):
        try:
            number = float(text)
        except ValueError:
            continue
        if number not in found:
            found.append(number)
    return found


def _several_numbers(value: Any) -> bool:
    """Whether a value lists several different numbers: one per transcript (dbNSFP's
    ``0.12&0.91``), per co-located record or per allele, of which none is the field's."""
    return len(_numbers(value)) > 1


def _parse_float(value: Any) -> Optional[float]:
    """The number a value holds; None without one, and with several different ones
    (``_several_numbers``). Their highest was read before: the score of another transcript or
    allele, shown and filtered as this one's. The annotation keeps such a value's text
    (``…_values``) to be shown."""
    numbers = _numbers(value)
    return numbers[0] if len(numbers) == 1 else None


def _parse_int(value: Any) -> Optional[int]:
    """A count: the whole number a value holds, as ``_parse_float`` reads it (a list of
    different numbers is none; its first was read before)."""
    number = _parse_float(value)
    return int(number) if number is not None else None


def _spliceai_delta(value: Optional[float]) -> Optional[float]:
    """SpliceAI delta scores are probabilities in [0, 1]. Values outside that range are
    not delta scores (a DP position field leaked in via a field-offset) and are dropped."""
    if value is None or not (0.0 <= value <= 1.0):
        return None
    return value


def _parse_spliceai_pred(value: Any) -> Dict[str, float]:
    """SpliceAI's own ``ALLELE|SYMBOL|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|DP_AL|DP_DG|DP_DL``: the
    four delta scores and, where written, the four positions."""
    value = _clean_value(value)
    if not isinstance(value, str):
        return {}
    parts = value.split("|")
    if len(parts) < 6:
        return {}
    result: Dict[str, float] = {}
    for key, index in (
        ("spliceai_ds_ag", 2),
        ("spliceai_ds_al", 3),
        ("spliceai_ds_dg", 4),
        ("spliceai_ds_dl", 5),
    ):
        parsed = _spliceai_delta(_parse_float(parts[index]))
        if parsed is not None:
            result[key] = parsed
    # The positions only beside four delta scores in their slots: a value shifted out of
    # place (a missing SYMBOL) puts a position in a score's slot, and its positions are not
    # where they are read either.
    if len(parts) >= 10 and len(result) == 4:
        for key, index in (
            ("spliceai_dp_ag", 6),
            ("spliceai_dp_al", 7),
            ("spliceai_dp_dg", 8),
            ("spliceai_dp_dl", 9),
        ):
            position = _parse_int(parts[index])
            if position is not None:
                result[key] = position
    return result


def _first_value(mapping: Dict[str, Any], aliases: Sequence[str]) -> Any:
    # _clean_value of each alias's value, written out: this runs for every alias of every
    # field of every annotation entry, and most aliases are absent or empty.
    for alias in aliases:
        value = mapping.get(alias)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if value in _NULL_VALUES:
                continue
        return value
    return None


def _gnomad_count(mapping: Dict[str, Any], aliases: _CountAliases) -> Optional[int]:
    """gnomAD's count in ``mapping``: its combined count, else its exome and genome counts
    added, either alone when the other is missing; None when it holds none (CLIN-67)."""
    combined_aliases, exome_aliases, genome_aliases = aliases
    combined = _parse_int(_first_value(mapping, combined_aliases))
    if combined is not None:
        return combined
    parts = [
        count
        for count in (
            _parse_int(_first_value(mapping, exome_aliases)),
            _parse_int(_first_value(mapping, genome_aliases)),
        )
        if count is not None
    ]
    return sum(parts) if parts else None


def _extract_format_fields(line: str, *, info_id: str) -> List[str] | None:
    if not line.startswith(f"##INFO=<ID={info_id},"):
        return None

    match = re.search(r"Format:\s*([^\">]+)", line)
    if match:
        return [field.strip() for field in match.group(1).strip().strip("'").split("|")]

    match = re.search(r"Functional annotations:\s*'([^']+)'", line)
    if match:
        return [field.strip() for field in match.group(1).split("|")]

    match = re.search(r"'([^']+\|[^']+)'", line)
    if match:
        return [field.strip() for field in match.group(1).split("|")]

    return None


# An INFO line's key and description.
_INFO_ID = re.compile(r"^##INFO=<ID=([^,>]+)")
_INFO_DESCRIPTION = re.compile(r'Description="((?:[^"\\]|\\.)*)"')
# The file a VEP --custom field comes from, as VEP describes it: ``… CLNSIG field from
# [PATH]/clinvar_20261101.vcf.gz``, the folder replaced by ``[PATH]``.
_FIELD_FROM_FILE = re.compile(r"field from\s+(\S+)\s*$")


def clinvar_file_in_description(description: str) -> str | None:
    """The name of the file a field's description says it comes from (VEP --custom), without
    its folder; None when the description names none."""
    match = _FIELD_FROM_FILE.search(description.strip())
    if not match:
        return None
    return match.group(1).rsplit("/", 1)[-1].strip() or None


def update_annotation_header_state(state: AnnotationHeaderState, line: str) -> None:
    ann_fields = _extract_format_fields(line, info_id="ANN")
    if ann_fields:
        state.ann_fields = ann_fields
    csq_fields = _extract_format_fields(line, info_id="CSQ")
    if csq_fields:
        state.csq_fields = csq_fields
    info_id = _INFO_ID.match(line)
    if info_id is None:
        return
    key = info_id.group(1)
    state.info_ids.add(key)
    if key in CLINVAR_FILE_ALIASES and state.clinvar_file_name is None:
        description = _INFO_DESCRIPTION.search(line)
        if description:
            state.clinvar_file_name = clinvar_file_in_description(description.group(1))


def clinvar_file_annotated(state: AnnotationHeaderState) -> bool:
    """Whether the header declares a ClinVar file's classification, as an INFO key or a CSQ or
    ANN field: then the records' ClinVar classification is that file's, and the VEP cache's
    is kept apart."""
    declared = state.info_ids.union(state.csq_fields or (), state.ann_fields or ())
    return not declared.isdisjoint(CLINVAR_FILE_ALIASES)


def _annotation_entries(
    info: Dict[str, str],
    state: AnnotationHeaderState,
) -> tuple[List[str], List[Dict[str, str]]]:
    """The record's ``CSQ`` entries (VEP), else its ``ANN`` entries (snpEff), each as a
    field -> value map by the field list its header declares, with that list; none without
    either."""
    for fields, key in ((state.csq_fields, "CSQ"), (state.ann_fields, "ANN")):
        if fields and info.get(key):
            return fields, [
                {field: value for field, value in zip(fields, entry.split("|"))}
                for entry in info[key].split(",")
                if entry
            ]
    return [], []


# dbNSFP lists a score per transcript, in the order of its Ensembl_transcriptid column. An
# entry that carries that column reads the score of its own transcript (``Feature``); any
# other list is ``_several_numbers``.
_DBNSFP_TRANSCRIPTS = "Ensembl_transcriptid"


def _unversioned(transcript_id: str) -> str:
    return transcript_id.strip().split(".", 1)[0]


def _transcript_value(source: Dict[str, Any], value: Any) -> Any:
    """``value`` as the entry's own transcript's, where it lists one per transcript in the
    order of the entry's dbNSFP transcript list; else ``value`` as it is."""
    transcripts = source.get(_DBNSFP_TRANSCRIPTS)
    feature = source.get("Feature")
    if not isinstance(value, str) or not isinstance(transcripts, str) or not isinstance(feature, str):
        return value
    values = _LIST_SEPARATORS.split(value)
    transcript_ids = _LIST_SEPARATORS.split(transcripts)
    if len(values) < 2 or len(values) != len(transcript_ids):
        return value
    own = _unversioned(feature)
    matches = [item for item, transcript_id in zip(values, transcript_ids) if _unversioned(transcript_id) == own]
    return matches[0] if len(matches) == 1 else value


def _apply_numeric_and_population_aliases(
    source: Dict[str, str],
    annotation: Dict[str, Any],
    *,
    extra_seed: Dict[str, Any],
    population_seed: Dict[str, float],
    tables: _AliasTables = _ALL_ALIASES,
) -> None:
    """Apply the score/count/extra-count/splice/population alias loops, the
    gnomad_af derivation, and the spliceai_max computation to `annotation`
    (mutated in place). Shared by _base_info_annotation (empty seeds) and
    _normalize_annotation_entry (seeds carry the base annotation's extra and
    population_frequencies). The per-caller _ANN_ALIASES handling and final
    pruning stay with each caller."""
    extra = dict(extra_seed)

    def _read(key: str, raw: Any) -> Optional[float]:
        # A value listing several different numbers is none of them; its text is kept for
        # the card (``revel_values``), never summarised.
        if _several_numbers(raw):
            extra[f"{key}_values"] = str(raw).strip()
            return None
        return _parse_float(raw)

    for key, aliases in tables.score:
        value = _read(key, _transcript_value(source, _first_value(source, aliases)))
        if value is not None:
            annotation[key] = value

    for key, count_aliases in tables.count:
        value = _gnomad_count(source, count_aliases)
        if value is not None:
            annotation[key] = value

    for key, count_aliases in tables.extra_count:
        value = _gnomad_count(source, count_aliases)
        if value is not None:
            extra[key] = value

    for key, aliases in tables.splice:
        value = _spliceai_delta(_read(key, _transcript_value(source, _first_value(source, aliases))))
        if value is not None:
            annotation[key] = value
    for key, aliases in tables.position:
        raw = _first_value(source, aliases)
        position = None if _several_numbers(raw) else _parse_int(raw)
        if position is not None:
            annotation[key] = position
    annotation.update(_parse_spliceai_pred(source.get("SpliceAI_pred")))

    population_frequencies = dict(population_seed)
    for key, aliases in tables.population:
        value = _read(key, _first_value(source, aliases))
        if value is not None:
            population_frequencies[key] = value
    for combined, parts in (
        ("gnomad_af", ("gnomad_exomes_af", "gnomad_genomes_af")),
        ("gnomad_popmax_af", ("gnomad_exomes_popmax_af", "gnomad_genomes_popmax_af")),
        ("gnomad_faf95_af", ("gnomad_exomes_faf95_af", "gnomad_genomes_faf95_af")),
    ):
        if combined in population_frequencies:
            continue
        derived = max(
            [population_frequencies[field] for field in parts if field in population_frequencies],
            default=None,
        )
        if derived is not None:
            population_frequencies[combined] = derived
    if extra:
        annotation["extra"] = extra
    if population_frequencies:
        annotation["population_frequencies"] = population_frequencies
        annotation["gnomad_af"] = population_frequencies.get("gnomad_af")

    splice_values = [
        annotation.get("spliceai_ds_ag"),
        annotation.get("spliceai_ds_al"),
        annotation.get("spliceai_ds_dg"),
        annotation.get("spliceai_ds_dl"),
    ]
    present = [value for value in splice_values if value is not None]
    if present:
        annotation["spliceai_max"] = max(present)


def _base_info_annotation(info: Dict[str, str]) -> Dict[str, Any]:
    annotation: Dict[str, Any] = {}
    if info.keys().isdisjoint(_ANNOTATION_KEYS):
        # No key it is read from (most records: INFO holds CSQ alone), so nothing is set.
        return annotation

    for key, aliases in _ALL_ALIASES.ann:
        value = _first_value(info, aliases)
        if key == "canonical":
            parsed = _parse_bool(value)
            if parsed is not None:
                annotation[key] = parsed
        elif key in {"mane_select", "mane_plus_clinical"}:
            parsed = _parse_presence_bool(value)
            if parsed is not None:
                annotation[key] = parsed
        elif value is not None:
            annotation[key] = value

    _apply_numeric_and_population_aliases(info, annotation, extra_seed={}, population_seed={})
    _tidy_clinvar(annotation)
    return annotation


_CLINVAR_VARIATION_ID = re.compile(r"\d+")


def _tidy_clinvar(annotation: Dict[str, Any]) -> None:
    """The ClinVar file's review status read as words (ClinVar writes
    ``criteria_provided,_multiple_submitters,_no_conflicts``, VEP its commas as ``&``), and a
    VariationID kept only as one: VEP --custom's ID field holds the matched record's ID."""
    status = annotation.get("clinvar_review_status")
    if isinstance(status, str):
        words = re.sub(r"\s+", " ", status.replace("&", ",").replace("_", " "))
        annotation["clinvar_review_status"] = re.sub(r"\s*,\s*", ", ", words).strip(" ,")
    variation_id = annotation.get("clinvar_variation_id")
    if variation_id is not None and not _CLINVAR_VARIATION_ID.fullmatch(str(variation_id).strip()):
        annotation.pop("clinvar_variation_id")


def _settle_clinvar(annotation: Dict[str, Any], *, clinvar_file_annotated: bool) -> None:
    """Without a ClinVar file annotated, the VEP cache's classification is the record's ClinVar
    classification, the only one there is; with one, it stays apart (``clinvar_vep_cache``)."""
    if clinvar_file_annotated or "clinvar" in annotation:
        return
    cached = annotation.pop(_CLINVAR_CACHE_KEY, None)
    if cached is not None:
        annotation["clinvar"] = cached


def _normalize_annotation_entry(
    raw_entry: Dict[str, str],
    base_annotation: Dict[str, Any],
    tables: _AliasTables = _ALL_ALIASES,
    *,
    clinvar_file_annotated: bool = False,
) -> Dict[str, Any]:
    annotation = dict(base_annotation)

    for key, aliases in tables.ann:
        value = _first_value(raw_entry, aliases)
        if key == "canonical":
            parsed = _parse_bool(value)
            if parsed is not None:
                annotation[key] = parsed
            else:
                annotation.pop(key, None)
        elif key in {"mane_select", "mane_plus_clinical"}:
            parsed = _parse_presence_bool(value)
            if parsed is not None:
                annotation[key] = parsed
            else:
                annotation.pop(key, None)
        elif value is not None:
            annotation[key] = value

    _apply_numeric_and_population_aliases(
        raw_entry,
        annotation,
        extra_seed=annotation.get("extra", {}),
        population_seed=base_annotation.get("population_frequencies", {}),
        tables=tables,
    )
    _settle_clinvar(
        annotation,
        clinvar_file_annotated=clinvar_file_annotated
        or not raw_entry.keys().isdisjoint(CLINVAR_FILE_ALIASES),
    )
    _tidy_clinvar(annotation)

    return {key: value for key, value in annotation.items() if value not in (None, {}, [])}


# --- A multi-allelic record's entries ------------------------------------------------- #
# A record with several ALT alleles (``G,T``) is stored as one variant, not split, and its CSQ
# holds the entries of every allele. Each entry names its allele, and each annotation of such
# a record says which ALT it is of (``alt_allele``): the card, the transcript list, the ACMG
# dialog and the frozen evidence name it. Before, the entries were read as the record's own,
# so its primary transcript, consequence and scores could be another allele's, unnamed.
ALT_ALLELE_KEY = "alt_allele"


def vep_allele_forms(ref: str, alts: Sequence[str]) -> list[str]:
    """Each ALT as VEP writes it in an entry's ``Allele`` for a VCF record (VEP's VCF parser):
    for an indel, the first base every allele (a ``*`` aside) shares is trimmed, and an allele
    left empty is ``-``; otherwise each as written."""
    is_indel = any(alt != "*" and len(alt) != len(ref) for alt in alts)
    first_bases = {allele[0] for allele in (ref, *alts) if allele and allele != "*"}
    if not is_indel or len(first_bases) != 1:
        return list(alts)
    return [alt if alt == "*" else (alt[1:] or "-") for alt in alts]


def _minimal_allele(ref: str, alt: str) -> str:
    """An ALT as VEP's --minimal writes it: the bases it shares with REF at either end
    trimmed, ``-`` when none is left."""
    if alt == "*":
        return alt
    while ref and alt and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    while ref and alt and ref[0] == alt[0]:
        ref, alt = ref[1:], alt[1:]
    return alt or "-"


def entry_alt_allele(
    entry: Dict[str, Any], ref: str, alts: Sequence[str], *, vep: bool = True
) -> str | None:
    """The ALT allele a CSQ (``vep``) or ANN entry of a multi-allelic record is of: by VEP's
    ``ALLELE_NUM`` (``--allele_number``) where it has one, else by its ``Allele``, as VEP writes
    the record's alleles, as ``--minimal`` writes them, or as written (snpEff writes the ALT).
    None when it names none of them, or several."""
    number = _parse_int(entry.get("ALLELE_NUM"))
    if number is not None and 1 <= number <= len(alts):
        return alts[number - 1]
    allele = str(entry.get("Allele") or "").strip()
    if not allele:
        return None
    written = list(alts)
    minimal = [_minimal_allele(ref, alt) for alt in alts]
    for forms in ((vep_allele_forms(ref, alts), minimal, written) if vep else (written, minimal)):
        matches = [alt for alt, form in zip(alts, forms) if form == allele]
        if len(matches) == 1:
            return matches[0]
        if matches:
            return None
    return None


def extract_small_variant_annotations(
    info: Dict[str, str],
    state: AnnotationHeaderState,
    *,
    ref: str | None = None,
    alt: str | None = None,
) -> List[Dict[str, Any]]:
    """The record's annotations, one per CSQ or ANN entry (else one from its INFO). With the
    record's ``ref`` and ``alt``, each entry of a multi-allelic record names its ALT
    (``alt_allele``)."""
    base_annotation = _base_info_annotation(info)
    # A ClinVar file declared in the header (VEP --custom describes its fields as INFO keys),
    # or a ClinVar file's key in the record's INFO; an entry may name one of its own.
    clinvar_file_in_info = not state.info_ids.isdisjoint(
        CLINVAR_FILE_ALIASES
    ) or not info.keys().isdisjoint(CLINVAR_FILE_ALIASES)
    fields, raw_entries = _annotation_entries(info, state)

    if raw_entries:
        tables = _header_alias_tables(tuple(fields))
        alts = alt.split(",") if ref and alt and "," in alt else None
        vep = state.csq_fields is not None and fields == state.csq_fields
        annotations = []
        for raw_entry in raw_entries:
            annotation = _normalize_annotation_entry(
                raw_entry, base_annotation, tables, clinvar_file_annotated=clinvar_file_in_info
            )
            if annotation and alts is not None and ref:
                allele = entry_alt_allele(raw_entry, ref, alts, vep=vep)
                if allele is not None:
                    annotation[ALT_ALLELE_KEY] = allele
            if annotation:
                annotations.append(annotation)
        return annotations

    _settle_clinvar(base_annotation, clinvar_file_annotated=clinvar_file_in_info)
    return [base_annotation] if base_annotation else []


def normalize_small_variant_annotation_entry(
    raw_entry: Dict[str, str], *, clinvar_file_annotated: bool = False
) -> Dict[str, Any]:
    """A VEP table row's annotation. ``clinvar_file_annotated``: the table declares a ClinVar
    file's field, as a column or an Extra key, which a row without a value leaves out."""
    return _normalize_annotation_entry(raw_entry, {}, clinvar_file_annotated=clinvar_file_annotated)
