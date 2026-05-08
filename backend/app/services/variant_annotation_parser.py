from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class AnnotationHeaderState:
    ann_fields: List[str] | None = None
    csq_fields: List[str] | None = None


_NULL_VALUES = {"", ".", "NA", "N/A", "null", "None"}
_TRUE_VALUES = {"YES", "TRUE", "1"}

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
    "clinvar": ["CLIN_SIG", "CLNSIG", "ClinVar_CLNSIG", "CLINVAR"],
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
}

_POPULATION_ALIASES = {
    "gnomad_af": ["gnomAD_AF", "GNOMAD_AF"],
    "gnomad_exomes_af": ["gnomADe_AF", "GNOMADE_AF"],
    "gnomad_genomes_af": ["gnomADg_AF", "GNOMADG_AF"],
    "gnomad_popmax_af": ["gnomAD_AF_POPMAX", "gnomAD_popmax_AF", "MAX_AF", "POPMAX_AF"],
    "topmed_af": ["TOPMed_AF", "TOPMED_AF"],
}

_COUNT_ALIASES = {
    "gnomad_hom_count": [
        "gnomAD_Hom",
        "GNOMAD_HOM",
        "gnomAD_hom",
        "gnomad_hom",
        "nhomalt",
        "NHOMALT",
        "gnomAD_nhomalt",
        "GNOMAD_NHOMALT",
    ],
}

_EXTRA_COUNT_ALIASES = {
    "gnomad_ac": [
        "gnomAD_AC",
        "GNOMAD_AC",
        "AC",
        "AC_Adj",
        "gnomADe_AC",
        "GNOMADE_AC",
        "gnomADg_AC",
        "GNOMADG_AC",
    ],
    "gnomad_hemi_count": [
        "gnomAD_Hemi",
        "GNOMAD_HEMI",
        "gnomAD_hemi",
        "gnomad_hemi",
        "hemi",
        "HEMI",
    ],
}

_SPLICE_ALIASES = {
    "spliceai_ds_ag": ["SpliceAI_pred_DS_AG", "DS_AG", "SPLICEAI_DS_AG"],
    "spliceai_ds_al": ["SpliceAI_pred_DS_AL", "DS_AL", "SPLICEAI_DS_AL"],
    "spliceai_ds_dg": ["SpliceAI_pred_DS_DG", "DS_DG", "SPLICEAI_DS_DG"],
    "spliceai_ds_dl": ["SpliceAI_pred_DS_DL", "DS_DL", "SPLICEAI_DS_DL"],
}

_SCORE_ALIASES = {
    "cadd_raw": ["CADD_RAW", "CADD_RAW_HG38"],
    "cadd_phred": ["CADD_PHRED", "CADD_PHRED_HG38"],
    "revel": ["REVEL"],
    "gene_pli": ["PLI", "pLI", "GENE_PLI", "LOF_PLI"],
    "gene_missense_z": ["MISSENSE_Z", "missense_Z", "MIS_Z", "GENE_MISSENSE_Z"],
    "alpha_missense_pathogenicity": ["am_pathogenicity", "AlphaMissense_pathogenicity"],
}


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
    return True


def _parse_float(value: Any) -> Optional[float]:
    value = _clean_value(value)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    numbers = re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", str(value), flags=re.IGNORECASE)
    if not numbers:
        return None
    try:
        return max(float(number) for number in numbers)
    except ValueError:
        return None


def _parse_int(value: Any) -> Optional[int]:
    value = _clean_value(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    numbers = re.findall(r"-?\d+", str(value))
    if not numbers:
        return None
    try:
        return int(numbers[0])
    except ValueError:
        return None


def _parse_spliceai_pred(value: Any) -> Dict[str, float]:
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
        parsed = _parse_float(parts[index])
        if parsed is not None:
            result[key] = parsed
    return result


def _first_value(mapping: Dict[str, Any], aliases: List[str]) -> Any:
    for alias in aliases:
        value = _clean_value(mapping.get(alias))
        if value is not None:
            return value
    return None


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


def update_annotation_header_state(state: AnnotationHeaderState, line: str) -> None:
    ann_fields = _extract_format_fields(line, info_id="ANN")
    if ann_fields:
        state.ann_fields = ann_fields
    csq_fields = _extract_format_fields(line, info_id="CSQ")
    if csq_fields:
        state.csq_fields = csq_fields


def _annotation_entries(
    info: Dict[str, str],
    state: AnnotationHeaderState,
) -> tuple[List[str], List[Dict[str, str]]]:
    if state.csq_fields and info.get("CSQ"):
        raw_entries = [entry for entry in info["CSQ"].split(",") if entry]
        return state.csq_fields, [
            {field: value for field, value in zip(state.csq_fields, entry.split("|"))}
            for entry in raw_entries
        ]
    if state.ann_fields and info.get("ANN"):
        raw_entries = [entry for entry in info["ANN"].split(",") if entry]
        return state.ann_fields, [
            {field: value for field, value in zip(state.ann_fields, entry.split("|"))}
            for entry in raw_entries
        ]
    return [], []


def _base_info_annotation(info: Dict[str, str]) -> Dict[str, Any]:
    annotation: Dict[str, Any] = {}

    for key, aliases in _ANN_ALIASES.items():
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

    for key, aliases in _SCORE_ALIASES.items():
        value = _parse_float(_first_value(info, aliases))
        if value is not None:
            annotation[key] = value

    for key, aliases in _COUNT_ALIASES.items():
        value = _parse_int(_first_value(info, aliases))
        if value is not None:
            annotation[key] = value

    extra: Dict[str, Any] = {}
    for key, aliases in _EXTRA_COUNT_ALIASES.items():
        value = _parse_int(_first_value(info, aliases))
        if value is not None:
            extra[key] = value
    if extra:
        annotation["extra"] = extra

    for key, aliases in _SPLICE_ALIASES.items():
        value = _parse_float(_first_value(info, aliases))
        if value is not None:
            annotation[key] = value
    annotation.update(_parse_spliceai_pred(info.get("SpliceAI_pred")))

    population_frequencies: Dict[str, float] = {}
    for key, aliases in _POPULATION_ALIASES.items():
        value = _parse_float(_first_value(info, aliases))
        if value is not None:
            population_frequencies[key] = value

    if "gnomad_af" not in population_frequencies:
        derived_gnomad = max(
            [
                population_frequencies[field]
                for field in ("gnomad_exomes_af", "gnomad_genomes_af")
                if field in population_frequencies
            ],
            default=None,
        )
        if derived_gnomad is not None:
            population_frequencies["gnomad_af"] = derived_gnomad

    if population_frequencies:
        annotation["population_frequencies"] = population_frequencies
        annotation["gnomad_af"] = population_frequencies.get("gnomad_af")

    splice_values = [
        annotation.get("spliceai_ds_ag"),
        annotation.get("spliceai_ds_al"),
        annotation.get("spliceai_ds_dg"),
        annotation.get("spliceai_ds_dl"),
    ]
    splice_values = [value for value in splice_values if value is not None]
    if splice_values:
        annotation["spliceai_max"] = max(splice_values)

    if not annotation:
        return {}
    return annotation


def _normalize_annotation_entry(
    raw_entry: Dict[str, str],
    base_annotation: Dict[str, Any],
) -> Dict[str, Any]:
    annotation = dict(base_annotation)

    for key, aliases in _ANN_ALIASES.items():
        value = _first_value(raw_entry, aliases)
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

    for key, aliases in _SCORE_ALIASES.items():
        value = _parse_float(_first_value(raw_entry, aliases))
        if value is not None:
            annotation[key] = value

    for key, aliases in _COUNT_ALIASES.items():
        value = _parse_int(_first_value(raw_entry, aliases))
        if value is not None:
            annotation[key] = value

    extra = dict(annotation.get("extra", {}))
    for key, aliases in _EXTRA_COUNT_ALIASES.items():
        value = _parse_int(_first_value(raw_entry, aliases))
        if value is not None:
            extra[key] = value
    if extra:
        annotation["extra"] = extra

    for key, aliases in _SPLICE_ALIASES.items():
        value = _parse_float(_first_value(raw_entry, aliases))
        if value is not None:
            annotation[key] = value
    annotation.update(_parse_spliceai_pred(raw_entry.get("SpliceAI_pred")))

    population_frequencies = dict(base_annotation.get("population_frequencies", {}))
    for key, aliases in _POPULATION_ALIASES.items():
        value = _parse_float(_first_value(raw_entry, aliases))
        if value is not None:
            population_frequencies[key] = value
    if "gnomad_af" not in population_frequencies:
        derived_gnomad = max(
            [
                population_frequencies[field]
                for field in ("gnomad_exomes_af", "gnomad_genomes_af")
                if field in population_frequencies
            ],
            default=None,
        )
        if derived_gnomad is not None:
            population_frequencies["gnomad_af"] = derived_gnomad
    if population_frequencies:
        annotation["population_frequencies"] = population_frequencies
        annotation["gnomad_af"] = population_frequencies.get("gnomad_af")

    splice_values = [
        annotation.get("spliceai_ds_ag"),
        annotation.get("spliceai_ds_al"),
        annotation.get("spliceai_ds_dg"),
        annotation.get("spliceai_ds_dl"),
    ]
    splice_values = [value for value in splice_values if value is not None]
    if splice_values:
        annotation["spliceai_max"] = max(splice_values)

    return {key: value for key, value in annotation.items() if value not in (None, {}, [])}


def extract_small_variant_annotations(
    info: Dict[str, str],
    state: AnnotationHeaderState,
) -> List[Dict[str, Any]]:
    base_annotation = _base_info_annotation(info)
    _, raw_entries = _annotation_entries(info, state)

    if raw_entries:
        annotations = [
            _normalize_annotation_entry(raw_entry, base_annotation)
            for raw_entry in raw_entries
        ]
        return [annotation for annotation in annotations if annotation]

    return [base_annotation] if base_annotation else []


def normalize_small_variant_annotation_entry(raw_entry: Dict[str, str]) -> Dict[str, Any]:
    return _normalize_annotation_entry(raw_entry, {})
