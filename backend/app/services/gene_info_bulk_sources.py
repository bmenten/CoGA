from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import io
import json
from pathlib import Path
import re
from typing import Any, Iterable, TextIO

import httpx

from ..core.config import settings
from .bounded_download import download_bounded_bytes, gunzip_bounded

_MAX_ASSOCIATIONS_PER_GENE = 24
_MAX_TERMS_PER_GENE = 96
_DEFAULT_DBNSFP_GENE_PATH = Path("/data/ref-data/dbNSFP5.4_gene.gz")
_REPO_DBNSFP_GENE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "ref-data" / "dbNSFP5.4_gene.gz"
)
_MISSING_CELL_VALUES = {"", ".", "-", "na", "n/a", "none", "null"}


def _clean_cell(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in _MISSING_CELL_VALUES:
        return ""
    return text


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _normalized_row(row: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        normalized[_normalize_header(key)] = _clean_cell(value)
    return normalized


def _row_value(row: dict[str, str], *aliases: str) -> str:
    for alias in aliases:
        value = _clean_cell(row.get(_normalize_header(alias), ""))
        if value:
            return value
    return ""


def _split_multi_value(value: str) -> list[str]:
    if not value:
        return []
    return [cleaned for entry in re.split(r"[|;]", value) if (cleaned := _clean_cell(entry))]


def _split_gene_list(value: str) -> list[str]:
    if not value:
        return []
    return [cleaned for entry in re.split(r"[|;,]", value) if (cleaned := _clean_cell(entry))]


def _leading_float(value: str) -> float | None:
    value = _clean_cell(value)
    if not value:
        return None
    # Capture an optional scientific-notation exponent — dbNSFP stores constraint
    # metrics like gnomAD_pLI as "9.2157e-29", and dropping the exponent silently
    # corrupts them (9.2157 instead of ~0).
    match = re.search(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value)
    if match is None:
        return None
    return float(match.group(0))


def _json_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def merge_gene_extra(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if value in (None, "", [], {}):
            continue
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_gene_extra(current, value)
            continue
        if isinstance(current, list) and isinstance(value, list):
            seen = {_json_key(item) for item in current}
            combined = list(current)
            for item in value:
                item_key = _json_key(item)
                if item_key in seen:
                    continue
                seen.add(item_key)
                combined.append(item)
            merged[key] = combined
            continue
        merged[key] = value
    return merged


def _source_status(
    *,
    status: str,
    source_url: str | None = None,
    payload: dict[str, Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_url": source_url,
        "message": message,
        "payload": payload or {},
    }


@dataclass(slots=True)
class GeneBulkSourceDataset:
    name: str
    source_url: str | None
    status: str
    message: str | None = None
    records_by_symbol: dict[str, dict[str, Any]] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def status_for_symbol(self, symbol: str) -> dict[str, Any]:
        if self.status == "error":
            return _source_status(
                status="error",
                source_url=self.source_url,
                payload=self.payload,
                message=self.message,
            )
        record = self.records_by_symbol.get(symbol)
        if record is None:
            return _source_status(
                status="missing",
                source_url=self.source_url,
                payload={"record_count": 0, **self.payload},
                message=self.message or f"No {self.name} record for {symbol}",
            )
        status_payload = dict(self.payload)
        status_payload.update(record.get("status_payload") or {})
        return _source_status(
            status="success",
            source_url=self.source_url,
            payload=status_payload,
            message=self.message,
        )


@dataclass(slots=True)
class HumanGeneBulkContext:
    datasets: dict[str, GeneBulkSourceDataset] = field(default_factory=dict)

    def dbnsfp_record_for_symbol(self, symbol: str) -> dict[str, Any] | None:
        dataset = self.datasets.get("dbnsfp_gene")
        if dataset is None or dataset.status != "success":
            return None
        return dataset.records_by_symbol.get(_normalize_symbol(symbol))


def build_bulk_gene_bundle(
    *,
    symbol: str,
    bulk_context: HumanGeneBulkContext | None,
) -> dict[str, Any]:
    if bulk_context is None:
        return {"extra": {}, "source_status": {}, "omim_gene_id": None}

    normalized_symbol = _normalize_symbol(symbol)
    merged_extra: dict[str, Any] = {}
    merged_profile: dict[str, Any] = {}
    merged_homologs: list[dict[str, Any]] = []
    seen_homologs: set[str] = set()
    source_status_map: dict[str, dict[str, Any]] = {}
    omim_gene_id: str | None = None
    primary_source: str | None = None

    for name, dataset in bulk_context.datasets.items():
        source_status_map[name] = dataset.status_for_symbol(normalized_symbol)
        record = dataset.records_by_symbol.get(normalized_symbol)
        if record is None:
            continue
        if name == "dbnsfp_gene":
            primary_source = "dbnsfp_gene"
        merged_extra = merge_gene_extra(merged_extra, record.get("extra") or {})
        merged_profile = merge_gene_extra(merged_profile, record.get("profile") or {})
        for homolog in record.get("homologs") or []:
            homolog_key = _json_key(homolog)
            if homolog_key in seen_homologs:
                continue
            seen_homologs.add(homolog_key)
            merged_homologs.append(homolog)
        candidate_omim_gene_id = str(record.get("omim_gene_id") or "").strip()
        if candidate_omim_gene_id and not omim_gene_id:
            omim_gene_id = candidate_omim_gene_id

    return {
        "extra": merged_extra,
        "profile": merged_profile,
        "homologs": merged_homologs,
        "source_status": source_status_map,
        "omim_gene_id": omim_gene_id,
        "primary_source": primary_source,
    }


async def _download_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        raw = await download_bounded_bytes(client, url, source=url)
    if url.endswith(".gz"):
        raw = gunzip_bounded(raw, source=url)
    return raw.decode("utf-8")


def _add_unique_record(bucket: list[dict[str, Any]], record: dict[str, Any]) -> None:
    if not record:
        return
    if len(bucket) >= _MAX_ASSOCIATIONS_PER_GENE:
        return
    record_key = _json_key(record)
    if any(_json_key(existing) == record_key for existing in bucket):
        return
    bucket.append(record)


def _clean_omim_id(value: str) -> str | None:
    match = re.search(r"(\d{5,})", value or "")
    if match is None:
        return None
    return match.group(1)


def _finalize_gene_records(records_by_symbol: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    finalized: dict[str, dict[str, Any]] = {}
    for symbol, record in records_by_symbol.items():
        extra = dict(record.get("extra") or {})
        status_payload = dict(record.get("status_payload") or {})
        for key in (
            "omim_diseases",
            "dbnsfp_disease_associations",
            "clingen_validity_assertions",
            "clingen_dosage_assertions",
            "gencc_assertions",
            "clinvar_gene_relationships",
        ):
            if key in extra and not extra[key]:
                extra.pop(key, None)
        finalized[symbol] = {
            "extra": extra,
            "profile": record.get("profile") or {},
            "homologs": record.get("homologs") or [],
            "omim_gene_id": record.get("omim_gene_id"),
            "status_payload": status_payload,
        }
    return finalized


def parse_clingen_validity_rows(
    text_value: str, *, symbols: Iterable[str] | None = None
) -> dict[str, dict[str, Any]]:
    symbol_filter = {_normalize_symbol(symbol) for symbol in (symbols or []) if _normalize_symbol(symbol)}
    reader = csv.DictReader(io.StringIO(text_value))
    records_by_symbol: dict[str, dict[str, Any]] = {}
    for raw_row in reader:
        row = _normalized_row(raw_row)
        symbol = _normalize_symbol(_row_value(row, "gene_symbol"))
        if not symbol:
            continue
        if symbol_filter and symbol not in symbol_filter:
            continue
        assertion = {
            "disease_label": _row_value(row, "disease_label"),
            "disease_id": _row_value(row, "disease_id_mondo"),
            "moi": _row_value(row, "moi"),
            "sop": _row_value(row, "sop"),
            "classification": _row_value(row, "classification"),
            "online_report": _row_value(row, "online_report"),
            "classification_date": _row_value(row, "classification_date"),
            "gcep": _row_value(row, "gcep"),
        }
        if not any(assertion.values()):
            continue
        record = records_by_symbol.setdefault(
            symbol,
            {"extra": {"clingen_validity_assertions": []}, "status_payload": {"record_count": 0}},
        )
        _add_unique_record(record["extra"]["clingen_validity_assertions"], assertion)
        record["status_payload"]["record_count"] = len(record["extra"]["clingen_validity_assertions"])

    for record in records_by_symbol.values():
        assertions = record["extra"]["clingen_validity_assertions"]
        record["extra"]["clingen_curation_counts"] = {
            "gene_disease_validity": len(assertions),
        }
    return _finalize_gene_records(records_by_symbol)


def parse_clingen_dosage_rows(
    text_value: str, *, symbols: Iterable[str] | None = None
) -> dict[str, dict[str, Any]]:
    symbol_filter = {_normalize_symbol(symbol) for symbol in (symbols or []) if _normalize_symbol(symbol)}
    reader = csv.DictReader(io.StringIO(text_value))
    records_by_symbol: dict[str, dict[str, Any]] = {}
    for raw_row in reader:
        row = _normalized_row(raw_row)
        symbol = _normalize_symbol(_row_value(row, "gene_symbol"))
        if not symbol:
            continue
        if symbol_filter and symbol not in symbol_filter:
            continue
        assertion = {
            "hgnc_id": _row_value(row, "hgnc_id"),
            "haploinsufficiency": _row_value(row, "haploinsufficiency"),
            "triplosensitivity": _row_value(row, "triplosensitivity"),
            "online_report": _row_value(row, "online_report"),
            "date": _row_value(row, "date"),
        }
        if not any(assertion.values()):
            continue
        record = records_by_symbol.setdefault(
            symbol,
            {"extra": {"clingen_dosage_assertions": []}, "status_payload": {"record_count": 0}},
        )
        _add_unique_record(record["extra"]["clingen_dosage_assertions"], assertion)
        record["status_payload"]["record_count"] = len(record["extra"]["clingen_dosage_assertions"])

    for record in records_by_symbol.values():
        assertions = record["extra"]["clingen_dosage_assertions"]
        record["extra"]["clingen_curation_counts"] = {
            "dosage_sensitivity": len(assertions),
        }
    return _finalize_gene_records(records_by_symbol)


def parse_gencc_rows(
    text_value: str, *, symbols: Iterable[str] | None = None
) -> dict[str, dict[str, Any]]:
    symbol_filter = {_normalize_symbol(symbol) for symbol in (symbols or []) if _normalize_symbol(symbol)}
    reader = csv.DictReader(io.StringIO(text_value))
    records_by_symbol: dict[str, dict[str, Any]] = {}
    for raw_row in reader:
        row = _normalized_row(raw_row)
        symbol = _normalize_symbol(_row_value(row, "gene_symbol"))
        if not symbol:
            continue
        if symbol_filter and symbol not in symbol_filter:
            continue
        classification = _row_value(row, "classification_title")
        assertion = {
            "gene_curie": _row_value(row, "gene_curie"),
            "disease_curie": _row_value(row, "disease_curie"),
            "disease_title": _row_value(row, "disease_title"),
            "classification_title": classification,
            "moi_title": _row_value(row, "moi_title"),
            "submitter_title": _row_value(row, "submitter_title"),
            "report_url": _row_value(row, "submitted_as_public_report_url"),
        }
        if not any(assertion.values()):
            continue
        record = records_by_symbol.setdefault(
            symbol,
            {
                "extra": {
                    "gencc_assertions": [],
                    "clingen_gene_facts": {"gencc_classifications": {}},
                },
                "status_payload": {"record_count": 0},
            },
        )
        before_count = len(record["extra"]["gencc_assertions"])
        _add_unique_record(record["extra"]["gencc_assertions"], assertion)
        record["status_payload"]["record_count"] = len(record["extra"]["gencc_assertions"])
        if classification and len(record["extra"]["gencc_assertions"]) > before_count:
            counts = record["extra"]["clingen_gene_facts"]["gencc_classifications"]
            counts[classification] = int(counts.get(classification) or 0) + 1
    return _finalize_gene_records(records_by_symbol)


def parse_clinvar_gene_condition_rows(
    text_value: str, *, symbols: Iterable[str] | None = None
) -> dict[str, dict[str, Any]]:
    symbol_filter = {_normalize_symbol(symbol) for symbol in (symbols or []) if _normalize_symbol(symbol)}
    reader = csv.DictReader(io.StringIO(text_value), delimiter="\t")
    records_by_symbol: dict[str, dict[str, Any]] = {}
    for raw_row in reader:
        row = _normalized_row(raw_row)
        related_symbols = [
            _normalize_symbol(candidate)
            for candidate in _split_gene_list(_row_value(row, "associatedgenes"))
        ]
        related_symbols = [
            symbol
            for symbol in related_symbols
            if symbol and (not symbol_filter or symbol in symbol_filter)
        ]
        if not related_symbols:
            continue
        disease_name = _row_value(row, "diseasename")
        disease_mim = _clean_omim_id(_row_value(row, "diseasemim"))
        source_name = _row_value(row, "sourcename")
        source_id = _row_value(row, "sourceid")
        for symbol in related_symbols:
            record = records_by_symbol.setdefault(
                symbol,
                {
                    "extra": {
                        "omim_diseases": [],
                        "dbnsfp_disease_associations": [],
                        "clinvar_gene_relationships": [],
                    },
                    "status_payload": {"record_count": 0},
                },
            )
            relationship = {
                "concept_id": _row_value(row, "conceptid"),
                "disease_name": disease_name,
                "source_name": source_name,
                "source_id": source_id,
                "disease_mim": disease_mim,
                "last_updated": _row_value(row, "lastupdated"),
            }
            if any(relationship.values()):
                _add_unique_record(record["extra"]["clinvar_gene_relationships"], relationship)

            if disease_name:
                association = {
                    "label": disease_name,
                    "source": "ClinVar",
                    "details": " · ".join(
                        part for part in [source_name, source_id] if part
                    ) or None,
                }
                _add_unique_record(record["extra"]["dbnsfp_disease_associations"], association)

            if disease_name and disease_mim:
                omim_entry = {
                    "label": disease_name,
                    "omim_id": disease_mim,
                    "href": f"https://www.omim.org/entry/{disease_mim}",
                }
                _add_unique_record(record["extra"]["omim_diseases"], omim_entry)

            relationship_count = len(record["extra"]["clinvar_gene_relationships"])
            record["status_payload"]["record_count"] = relationship_count
    return _finalize_gene_records(records_by_symbol)


def _open_text_handle(path: Path) -> TextIO:
    if path.suffix in {".gz", ".bgz"}:
        return gzip.open(path, "rt")
    return path.open("rt")


def _unique_values(values: Iterable[Any], *, limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = _clean_cell(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if limit is not None and len(result) >= limit:
            break
    return result


def _limited_split(value: str, *, limit: int | None = None) -> list[str]:
    return _unique_values(_split_multi_value(value), limit=limit)


def _clean_prefixed_text(value: str, *prefixes: str) -> str:
    cleaned = _clean_cell(value)
    for prefix in prefixes:
        cleaned = re.sub(rf"^{re.escape(prefix)}:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" ;")


def _numeric_metrics(row: dict[str, str], mapping: dict[str, tuple[str, ...]]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for output_key, aliases in mapping.items():
        value = _leading_float(_row_value(row, *aliases))
        if value is not None:
            metrics[output_key] = value
    return metrics


def _aligned_records(
    row: dict[str, str],
    columns: dict[str, tuple[str, ...]],
    *,
    limit: int = _MAX_ASSOCIATIONS_PER_GENE,
) -> list[dict[str, Any]]:
    values_by_key = {
        output_key: _split_multi_value(_row_value(row, *aliases))
        for output_key, aliases in columns.items()
    }
    max_len = min(max((len(values) for values in values_by_key.values()), default=0), limit)
    records: list[dict[str, Any]] = []
    for index in range(max_len):
        record = {
            output_key: values[index]
            for output_key, values in values_by_key.items()
            if index < len(values) and values[index]
        }
        if record:
            records.append(record)
    return records


def _parse_pubmed_ids(value: str) -> list[str]:
    return _unique_values(re.findall(r"\d{5,}", value), limit=24)


def _parse_mim_disease_entries(row: dict[str, str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw_entry in _split_multi_value(_row_value(row, "MIM_disease")):
        match = re.match(r"\[MIM:(?P<omim>\d+)\]\s*(?P<label>.+)", raw_entry)
        if not match:
            continue
        entries.append(
            {
                "label": match.group("label").strip(),
                "omim_id": match.group("omim"),
                "href": f"https://www.omim.org/entry/{match.group('omim')}",
            }
        )

    if entries:
        return entries[:_MAX_ASSOCIATIONS_PER_GENE]

    disease_text = _row_value(row, "Disease_description", "Disease_description_OMIM")
    if not disease_text:
        return []
    candidate_entries = re.split(r"(?=DISEASE:\s*)", disease_text)
    if len(candidate_entries) <= 1:
        candidate_entries = _split_multi_value(disease_text)
    for raw_entry in candidate_entries:
        cleaned = _clean_prefixed_text(raw_entry, "DISEASE")
        if not cleaned:
            continue
        mim_match = re.search(r"\[MIM:(\d+)\]", cleaned)
        label = re.split(r"\s*\[MIM:\d+\]", cleaned, maxsplit=1)[0].strip(" :;")
        if not label:
            continue
        entry = {"label": label}
        if mim_match:
            entry["omim_id"] = mim_match.group(1)
            entry["href"] = f"https://www.omim.org/entry/{mim_match.group(1)}"
        entries.append(entry)
        if len(entries) >= _MAX_ASSOCIATIONS_PER_GENE:
            break
    return entries


def _dbnsfp_pathways(row: dict[str, str]) -> dict[str, Any]:
    pathways = {
        "uniprot": _limited_split(_row_value(row, "Pathway(Uniprot)"), limit=32),
        "biocarta_short": _limited_split(_row_value(row, "Pathway(BioCarta)_short"), limit=32),
        "biocarta_full": _limited_split(_row_value(row, "Pathway(BioCarta)_full"), limit=32),
        "consensus_path_db": _limited_split(_row_value(row, "Pathway(ConsensusPathDB)"), limit=64),
        "kegg_ids": _limited_split(_row_value(row, "Pathway(KEGG)_id"), limit=32),
        "kegg": _limited_split(_row_value(row, "Pathway(KEGG)_full"), limit=32),
    }
    return {key: value for key, value in pathways.items() if value}


def _dbnsfp_gene_ontology(row: dict[str, str]) -> dict[str, list[str]]:
    ontology = {
        "biological_process": _limited_split(_row_value(row, "GO_biological_process"), limit=64),
        "cellular_component": _limited_split(_row_value(row, "GO_cellular_component"), limit=64),
        "molecular_function": _limited_split(_row_value(row, "GO_molecular_function"), limit=64),
    }
    return {key: value for key, value in ontology.items() if value}


def _dbnsfp_hpo_terms(row: dict[str, str]) -> list[dict[str, str]]:
    hpo_ids = _split_multi_value(_row_value(row, "HPO_id"))
    hpo_names = _split_multi_value(_row_value(row, "HPO_name"))
    terms: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, hpo_id in enumerate(hpo_ids):
        key = hpo_id
        if key in seen:
            continue
        seen.add(key)
        term = {"hpo_id": hpo_id}
        if index < len(hpo_names) and hpo_names[index]:
            term["label"] = hpo_names[index]
        terms.append(term)
        if len(terms) >= _MAX_TERMS_PER_GENE:
            break
    return terms


def _dbnsfp_expression(row: dict[str, str]) -> dict[str, Any]:
    tissue_values: dict[str, float] = {}
    for key, value in row.items():
        if not key.startswith("hpa_consensus_") or key == "hpa_consensus_highly_expressed":
            continue
        numeric_value = _leading_float(value)
        if numeric_value is None:
            continue
        tissue = key.removeprefix("hpa_consensus_")
        tissue_values[tissue] = numeric_value
    expression = {
        "tissue_specificity": _row_value(row, "Tissue_specificity(Uniprot)"),
        "hpa_consensus_tpm": tissue_values,
        "hpa_highly_expressed": _split_gene_list(_row_value(row, "HPA_consensus_highly_expressed")),
    }
    return {key: value for key, value in expression.items() if value}


def _dbnsfp_homologs(row: dict[str, str]) -> list[dict[str, Any]]:
    homologs: list[dict[str, Any]] = []
    mouse_symbol = _row_value(row, "MGI_mouse_gene")
    if mouse_symbol:
        homologs.append(
            {
                "species_name": "Mus Musculus",
                "common_name": "mouse",
                "symbol": mouse_symbol,
                "homology_type": "dbNSFP model organism ortholog",
                "in_platform": False,
            }
        )
    zebrafish_symbol = _row_value(row, "ZFIN_zebrafish_gene")
    if zebrafish_symbol:
        homologs.append(
            {
                "species_name": "Danio Rerio",
                "common_name": "zebrafish",
                "symbol": zebrafish_symbol,
                "homology_type": "dbNSFP model organism ortholog",
                "in_platform": False,
            }
        )
    return homologs


def _unique_homologs(homologs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for homolog in homologs:
        homolog_key = _json_key(homolog)
        if homolog_key in seen:
            continue
        seen.add(homolog_key)
        unique.append(homolog)
    return unique


def _dbnsfp_constraint_metrics(row: dict[str, str]) -> dict[str, float]:
    return _numeric_metrics(
        row,
        {
            "missense_z": ("mis_z", "missense_z"),
            "shet": ("s_het", "shet"),
            "phaplo": ("phaplo", "p_haplo"),
            "ptriplo": ("ptriplo", "p_triplo"),
            "p_hi": ("P(HI)",),
            "hipred_score": ("HIPred_score",),
            "ghis": ("GHIS",),
            "p_rec": ("P(rec)",),
            "rvis_evs": ("RVIS_EVS",),
            "rvis_percentile_evs": ("RVIS_percentile_EVS",),
            "lof_fdr_exac": ("LoF-FDR_ExAC",),
            "rvis_exac": ("RVIS_ExAC",),
            "rvis_percentile_exac": ("RVIS_percentile_ExAC",),
            "exac_pli": ("ExAC_pLI",),
            "exac_prec": ("ExAC_pRec",),
            "exac_pnull": ("ExAC_pNull",),
            "gnomad_pli": ("gnomAD_pLI",),
            "gnomad_prec": ("gnomAD_pRec",),
            "gnomad_pnull": ("gnomAD_pNull",),
            "gnomad_lof_oe": ("gnomAD_lof.oe",),
            "gnomad_mis_oe": ("gnomAD_mis.oe",),
            "gnomad_loeuf": ("gnomAD_LOEUF",),
            "gnomad_moeuf": ("gnomAD_MOEUF",),
            "exac_del_score": ("ExAC_del.score",),
            "exac_dup_score": ("ExAC_dup.score",),
            "exac_cnv_score": ("ExAC_cnv.score",),
            "gdi": ("GDI",),
            "gdi_phred": ("GDI-Phred",),
            "loftool_score": ("LoFtool_score",),
            "gene_indispensability_score": ("Gene_indispensability_score",),
        },
    )


def parse_dbnsfp_gene_rows(
    path: str | Path,
    *,
    symbols: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    source_path = Path(path)
    symbol_filter = {_normalize_symbol(symbol) for symbol in (symbols or []) if _normalize_symbol(symbol)}
    records_by_symbol: dict[str, dict[str, Any]] = {}

    with _open_text_handle(source_path) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for raw_row in reader:
            row = _normalized_row(raw_row)
            symbol = _normalize_symbol(
                _row_value(row, "gene_name", "gene_symbol", "symbol", "genename")
            )
            if not symbol:
                continue
            if symbol_filter and symbol not in symbol_filter:
                continue

            aliases = _unique_values(_split_gene_list(_row_value(row, "Gene_other_names")))
            previous_symbols = _unique_values(_split_gene_list(_row_value(row, "Gene_old_names")))
            ensembl_gene_id = _row_value(row, "Ensembl_gene")
            ncbi_gene_id = _row_value(row, "Entrez_gene_id")
            omim_gene_id = _clean_omim_id(_row_value(row, "MIM_id", "OMIM_id"))
            gene_full_name = _row_value(row, "Gene_full_name")
            function_description = _clean_prefixed_text(
                _row_value(row, "Function_description"),
                "FUNCTION",
            )
            refseq_accessions = _unique_values(_split_gene_list(_row_value(row, "Refseq_id")))
            uniprot_accessions = _unique_values(_split_gene_list(_row_value(row, "Uniprot_acc")))
            uniprot_ids = _unique_values(_split_gene_list(_row_value(row, "Uniprot_id")))
            ccds_ids = _unique_values(_split_gene_list(_row_value(row, "CCDS_id")))
            ucsc_ids = _unique_values(_split_gene_list(_row_value(row, "ucsc_id")))
            constraint_metrics = _dbnsfp_constraint_metrics(row)
            mim_diseases = _parse_mim_disease_entries(row)
            orphanet_assertions = _aligned_records(
                row,
                {
                    "orphanet_id": ("Orphanet_disorder_id",),
                    "disease_title": ("Orphanet_disorder",),
                    "association_type": ("Orphanet_association_type",),
                },
            )
            gencc_assertions = _aligned_records(
                row,
                {
                    "gencc_id": ("GenCC_id",),
                    "disease_title": ("GenCC_disease_title",),
                    "classification_title": ("GenCC_impact_class",),
                    "moi_title": ("GenCC_model_of_inheritance",),
                    "pmids": ("GenCC_pmid",),
                },
            )
            for assertion in gencc_assertions:
                if "pmids" in assertion:
                    assertion["pmids"] = _parse_pubmed_ids(str(assertion["pmids"]))

            clingen_haplo_score = _row_value(row, "ClinGen_Haploinsufficiency_Score")
            clingen_haplo_description = _row_value(row, "ClinGen_Haploinsufficiency_Description")
            clingen_haplo_disease = _row_value(row, "ClinGen_Haploinsufficiency_Disease")
            clingen_haplo_pmids = _parse_pubmed_ids(
                _row_value(row, "ClinGen_Haploinsufficiency_PMID")
            )
            clingen_dosage_assertions = []
            if any(
                [
                    clingen_haplo_score,
                    clingen_haplo_description,
                    clingen_haplo_disease,
                    clingen_haplo_pmids,
                ]
            ):
                clingen_dosage_assertions.append(
                    {
                        "haploinsufficiency": clingen_haplo_score,
                        "description": clingen_haplo_description,
                        "disease": clingen_haplo_disease,
                        "pmids": clingen_haplo_pmids,
                    }
                )

            gencc_classification_counts: dict[str, int] = {}
            for assertion in gencc_assertions:
                classification = str(assertion.get("classification_title") or "").strip()
                if not classification:
                    continue
                gencc_classification_counts[classification] = (
                    gencc_classification_counts.get(classification, 0) + 1
                )

            dbnsfp_identifiers = {
                "ensembl_gene_id": ensembl_gene_id,
                "entrez_gene_id": ncbi_gene_id,
                "mim_id": _row_value(row, "MIM_id"),
                "omim_id": _row_value(row, "OMIM_id"),
                "uniprot_accessions": uniprot_accessions,
                "uniprot_ids": uniprot_ids,
                "ccds_ids": ccds_ids,
                "refseq_accessions": refseq_accessions,
                "ucsc_ids": ucsc_ids,
            }
            dbnsfp_identifiers = {
                key: value for key, value in dbnsfp_identifiers.items() if value
            }

            clingen_gene_facts = {
                "hgnc_name": gene_full_name,
                "previous_symbols": previous_symbols,
                "alias_symbols": aliases,
                "function": function_description,
                "haploinsufficiency_index": (
                    constraint_metrics["p_hi"] * 100 if "p_hi" in constraint_metrics else None
                ),
                "pli": constraint_metrics.get("gnomad_pli"),
                "loeuf": constraint_metrics.get("gnomad_loeuf"),
                "gencc_classifications": gencc_classification_counts,
            }
            clingen_gene_facts = {
                key: value
                for key, value in clingen_gene_facts.items()
                if value not in (None, "", [], {})
            }

            record = records_by_symbol.setdefault(
                symbol,
                {
                    "profile": {},
                    "homologs": [],
                    "extra": {
                        "omim_diseases": [],
                        "dbnsfp_disease_associations": [],
                        "gencc_assertions": [],
                        "clingen_dosage_assertions": [],
                        "constraint_metrics": {},
                        "clingen_gene_facts": {},
                    },
                    "status_payload": {"record_count": 0},
                    "omim_gene_id": omim_gene_id,
                },
            )
            record["profile"] = merge_gene_extra(
                record.get("profile") or {},
                {
                    key: value
                    for key, value in {
                        "display_name": gene_full_name,
                        "summary": function_description,
                        "aliases": aliases,
                        "previous_symbols": previous_symbols,
                        "ensembl_gene_id": ensembl_gene_id,
                        "ncbi_gene_id": ncbi_gene_id,
                    }.items()
                    if value
                },
            )
            if omim_gene_id and not record.get("omim_gene_id"):
                record["omim_gene_id"] = omim_gene_id

            model_organisms = {
                "mouse": {
                    "symbol": _row_value(row, "MGI_mouse_gene"),
                    "phenotypes": _limited_split(_row_value(row, "MGI_mouse_phenotype"), limit=32),
                },
                "zebrafish": {
                    "symbol": _row_value(row, "ZFIN_zebrafish_gene"),
                    "structures": _limited_split(_row_value(row, "ZFIN_zebrafish_structure"), limit=32),
                    "phenotype_quality": _limited_split(
                        _row_value(row, "ZFIN_zebrafish_phenotype_quality"),
                        limit=32,
                    ),
                    "phenotype_tags": _limited_split(
                        _row_value(row, "ZFIN_zebrafish_phenotype_tag"),
                        limit=32,
                    ),
                },
            }
            model_organisms = {
                key: value for key, value in model_organisms.items() if any(value.values())
            }
            curation_counts = {
                "dosage_sensitivity": len(clingen_dosage_assertions) or None,
            }
            curation_counts = {
                key: value for key, value in curation_counts.items() if value is not None
            }
            record["extra"] = merge_gene_extra(
                record.get("extra") or {},
                {
                    "hgnc_name": gene_full_name,
                    "ensembl_description": function_description,
                    "refseq_accessions": refseq_accessions,
                    "dbnsfp_identifiers": dbnsfp_identifiers,
                    "dbnsfp_pathways": _dbnsfp_pathways(row),
                    "gene_ontology": _dbnsfp_gene_ontology(row),
                    "hpo_terms": _dbnsfp_hpo_terms(row),
                    "dbnsfp_orphanet_assertions": orphanet_assertions,
                    "dbnsfp_trait_associations": _limited_split(
                        _row_value(row, "Trait_association(GWAS)"),
                        limit=_MAX_ASSOCIATIONS_PER_GENE,
                    ),
                    "dbnsfp_model_organisms": model_organisms,
                    "dbnsfp_tissue_expression": _dbnsfp_expression(row),
                    "clingen_gene_facts": clingen_gene_facts,
                    "clingen_curation_counts": curation_counts,
                    "constraint_metrics": constraint_metrics,
                },
            )

            for disease in mim_diseases:
                _add_unique_record(record["extra"]["omim_diseases"], disease)
                association = {
                    "label": disease["label"],
                    "source": "dbNSFP",
                    "details": "OMIM" if disease.get("omim_id") else None,
                }
                _add_unique_record(record["extra"]["dbnsfp_disease_associations"], association)

            for orphanet_assertion in orphanet_assertions:
                label = orphanet_assertion.get("disease_title")
                if label:
                    _add_unique_record(
                        record["extra"]["dbnsfp_disease_associations"],
                        {
                            "label": label,
                            "source": "Orphanet",
                            "details": orphanet_assertion.get("association_type"),
                        },
                    )

            for gencc_assertion in gencc_assertions:
                _add_unique_record(record["extra"]["gencc_assertions"], gencc_assertion)
                label = gencc_assertion.get("disease_title")
                if label:
                    _add_unique_record(
                        record["extra"]["dbnsfp_disease_associations"],
                        {
                            "label": label,
                            "source": "GenCC",
                            "significance": gencc_assertion.get("classification_title"),
                            "details": gencc_assertion.get("moi_title"),
                        },
                    )

            for dosage_assertion in clingen_dosage_assertions:
                _add_unique_record(record["extra"]["clingen_dosage_assertions"], dosage_assertion)

            record["homologs"] = _unique_homologs(
                [*(record.get("homologs") or []), *_dbnsfp_homologs(row)]
            )
            record["extra"] = {
                key: value
                for key, value in (record.get("extra") or {}).items()
                if value not in (None, "", [], {})
            }
            record["profile"] = {
                key: value
                for key, value in (record.get("profile") or {}).items()
                if value not in (None, "", [], {})
            }
            record["status_payload"]["record_count"] = (
                len(record["extra"].get("dbnsfp_disease_associations") or [])
                + len(record["extra"].get("omim_diseases") or [])
                + len(record["extra"].get("gencc_assertions") or [])
                + len(record["extra"].get("clingen_dosage_assertions") or [])
                + len(record["extra"].get("constraint_metrics") or {})
            )
            record["status_payload"]["fields"] = sorted(
                key
                for key, value in (record.get("extra") or {}).items()
                if value not in (None, "", [], {})
            )
    return _finalize_gene_records(records_by_symbol)


async def _load_csv_dataset(
    *,
    name: str,
    url: str,
    parser,
    symbols: Iterable[str] | None = None,
) -> GeneBulkSourceDataset:
    try:
        text_value = await _download_text(url)
        records_by_symbol = parser(text_value, symbols=symbols)
        return GeneBulkSourceDataset(
            name=name,
            source_url=url,
            status="success",
            records_by_symbol=records_by_symbol,
            payload={"symbols_with_records": len(records_by_symbol)},
        )
    except Exception as exc:  # pragma: no cover
        return GeneBulkSourceDataset(
            name=name,
            source_url=url,
            status="error",
            message=str(exc),
        )


async def _load_online_gene_bulk_datasets(
    *,
    symbols: Iterable[str] | None = None,
) -> dict[str, GeneBulkSourceDataset]:
    clingen_validity, clingen_dosage, gencc, clinvar_gene_condition = await asyncio.gather(
        _load_csv_dataset(
            name="ClinGen gene validity",
            url=settings.gene_reference_clingen_validity_url,
            parser=parse_clingen_validity_rows,
            symbols=symbols,
        ),
        _load_csv_dataset(
            name="ClinGen dosage",
            url=settings.gene_reference_clingen_dosage_url,
            parser=parse_clingen_dosage_rows,
            symbols=symbols,
        ),
        _load_csv_dataset(
            name="GenCC",
            url=settings.gene_reference_gencc_url,
            parser=parse_gencc_rows,
            symbols=symbols,
        ),
        _load_csv_dataset(
            name="ClinVar gene-condition",
            url=settings.gene_reference_clinvar_gene_condition_url,
            parser=parse_clinvar_gene_condition_rows,
            symbols=symbols,
        ),
    )
    return {
        "clingen_gene_validity": clingen_validity,
        "clingen_dosage": clingen_dosage,
        "gencc": gencc,
        "clinvar_gene_condition": clinvar_gene_condition,
    }


def _dbnsfp_gene_path_candidates() -> list[Path]:
    configured_path = str(settings.gene_reference_dbnsfp_gene_path or "").strip()
    candidates = [Path(configured_path)] if configured_path else []
    candidates.extend([_DEFAULT_DBNSFP_GENE_PATH, _REPO_DBNSFP_GENE_PATH])

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def find_local_dbnsfp_gene_path() -> Path | None:
    return next((candidate for candidate in _dbnsfp_gene_path_candidates() if candidate.exists()), None)


def _load_dbnsfp_gene_dataset(
    *,
    symbols: Iterable[str] | None,
) -> GeneBulkSourceDataset:
    candidates = _dbnsfp_gene_path_candidates()
    existing_path = find_local_dbnsfp_gene_path()
    source_url = str(existing_path or candidates[0]) if candidates else None

    if existing_path is None:
        return GeneBulkSourceDataset(
            name="dbNSFP gene",
            source_url=source_url,
            status="missing",
            message="No local dbNSFP gene file found; checked "
            + ", ".join(str(candidate) for candidate in candidates),
        )

    try:
        records_by_symbol = parse_dbnsfp_gene_rows(existing_path, symbols=symbols)
        return GeneBulkSourceDataset(
            name="dbNSFP gene",
            source_url=str(existing_path),
            status="success",
            records_by_symbol=records_by_symbol,
            payload={"symbols_with_records": len(records_by_symbol)},
        )
    except Exception as exc:  # pragma: no cover
        return GeneBulkSourceDataset(
            name="dbNSFP gene",
            source_url=str(existing_path),
            status="error",
            message=str(exc),
        )


async def load_human_gene_bulk_context(
    *,
    symbols: Iterable[str] | None = None,
) -> HumanGeneBulkContext:
    normalized_symbols = sorted(
        {_normalize_symbol(symbol) for symbol in (symbols or []) if _normalize_symbol(symbol)}
    )
    dbnsfp_gene = _load_dbnsfp_gene_dataset(symbols=normalized_symbols or None)
    datasets: dict[str, GeneBulkSourceDataset] = {
        "dbnsfp_gene": dbnsfp_gene,
    }

    fallback_symbols: list[str] | None
    if dbnsfp_gene.status == "success":
        fallback_symbols = [
            symbol for symbol in normalized_symbols if symbol not in dbnsfp_gene.records_by_symbol
        ]
    else:
        fallback_symbols = normalized_symbols or None

    if dbnsfp_gene.status != "success" or fallback_symbols:
        online_datasets = await _load_online_gene_bulk_datasets(symbols=fallback_symbols)
        datasets.update(online_datasets)

    return HumanGeneBulkContext(datasets=datasets)
