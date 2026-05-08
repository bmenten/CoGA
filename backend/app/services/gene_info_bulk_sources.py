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

_MAX_ASSOCIATIONS_PER_GENE = 24


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _normalized_row(row: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        normalized[_normalize_header(key)] = str(value or "").strip()
    return normalized


def _row_value(row: dict[str, str], *aliases: str) -> str:
    for alias in aliases:
        value = row.get(_normalize_header(alias), "").strip()
        if value:
            return value
    return ""


def _split_multi_value(value: str) -> list[str]:
    if not value:
        return []
    return [entry.strip() for entry in re.split(r"[|;]", value) if entry.strip()]


def _split_gene_list(value: str) -> list[str]:
    if not value:
        return []
    return [entry.strip() for entry in re.split(r"[|;,]", value) if entry.strip()]


def _leading_float(value: str) -> float | None:
    if not value:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
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


def build_bulk_gene_bundle(
    *,
    symbol: str,
    bulk_context: HumanGeneBulkContext | None,
) -> dict[str, Any]:
    if bulk_context is None:
        return {"extra": {}, "source_status": {}, "omim_gene_id": None}

    normalized_symbol = _normalize_symbol(symbol)
    merged_extra: dict[str, Any] = {}
    source_status_map: dict[str, dict[str, Any]] = {}
    omim_gene_id: str | None = None

    for name, dataset in bulk_context.datasets.items():
        source_status_map[name] = dataset.status_for_symbol(normalized_symbol)
        record = dataset.records_by_symbol.get(normalized_symbol)
        if record is None:
            continue
        merged_extra = merge_gene_extra(merged_extra, record.get("extra") or {})
        candidate_omim_gene_id = str(record.get("omim_gene_id") or "").strip()
        if candidate_omim_gene_id and not omim_gene_id:
            omim_gene_id = candidate_omim_gene_id

    return {
        "extra": merged_extra,
        "source_status": source_status_map,
        "omim_gene_id": omim_gene_id,
    }


async def _download_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    raw = response.content
    if url.endswith(".gz"):
        raw = gzip.decompress(raw)
    return raw.decode(response.encoding or "utf-8")


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
            "omim_gene_id": record.get("omim_gene_id"),
            "status_payload": status_payload,
        }
    return finalized


def parse_clingen_validity_rows(text_value: str) -> dict[str, dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text_value))
    records_by_symbol: dict[str, dict[str, Any]] = {}
    for raw_row in reader:
        row = _normalized_row(raw_row)
        symbol = _normalize_symbol(_row_value(row, "gene_symbol"))
        if not symbol:
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


def parse_clingen_dosage_rows(text_value: str) -> dict[str, dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text_value))
    records_by_symbol: dict[str, dict[str, Any]] = {}
    for raw_row in reader:
        row = _normalized_row(raw_row)
        symbol = _normalize_symbol(_row_value(row, "gene_symbol"))
        if not symbol:
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


def parse_gencc_rows(text_value: str) -> dict[str, dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text_value))
    records_by_symbol: dict[str, dict[str, Any]] = {}
    for raw_row in reader:
        row = _normalized_row(raw_row)
        symbol = _normalize_symbol(_row_value(row, "gene_symbol"))
        if not symbol:
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


def parse_clinvar_gene_condition_rows(text_value: str) -> dict[str, dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text_value), delimiter="\t")
    records_by_symbol: dict[str, dict[str, Any]] = {}
    for raw_row in reader:
        row = _normalized_row(raw_row)
        related_symbols = [
            _normalize_symbol(candidate)
            for candidate in _split_gene_list(_row_value(row, "associatedgenes"))
        ]
        related_symbols = [symbol for symbol in related_symbols if symbol]
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

            record = records_by_symbol.setdefault(
                symbol,
                {
                    "extra": {
                        "omim_diseases": [],
                        "dbnsfp_disease_associations": [],
                        "constraint_metrics": {},
                    },
                    "status_payload": {"record_count": 0},
                    "omim_gene_id": _clean_omim_id(_row_value(row, "mim_id", "omim_id")),
                },
            )

            disease_descriptions = _split_multi_value(
                _row_value(row, "disease_description", "disease", "disease_description_omim")
            )
            if not disease_descriptions:
                disease_descriptions = _split_multi_value(_row_value(row, "disease_description"))
            mim_ids = [
                candidate
                for candidate in (
                    _clean_omim_id(value) for value in _split_gene_list(_row_value(row, "mim_id", "omim_id"))
                )
                if candidate
            ]

            for index, disease_name in enumerate(disease_descriptions):
                association = {
                    "label": disease_name,
                    "source": "dbNSFP",
                    "details": _row_value(row, "gene_full_name"),
                }
                _add_unique_record(record["extra"]["dbnsfp_disease_associations"], association)
                if index < len(mim_ids):
                    omim_id = mim_ids[index]
                    omim_entry = {
                        "label": disease_name,
                        "omim_id": omim_id,
                        "href": f"https://www.omim.org/entry/{omim_id}",
                    }
                    _add_unique_record(record["extra"]["omim_diseases"], omim_entry)

            constraint_metrics = {
                "missense_z": _leading_float(_row_value(row, "mis_z", "missense_z")),
                "shet": _leading_float(_row_value(row, "s_het", "shet")),
                "phaplo": _leading_float(_row_value(row, "phaplo", "p_haplo", "phi", "ghis")),
                "ptriplo": _leading_float(_row_value(row, "ptriplo", "p_triplo")),
            }
            constraint_metrics = {
                key: value for key, value in constraint_metrics.items() if value is not None
            }
            if constraint_metrics:
                record["extra"]["constraint_metrics"] = merge_gene_extra(
                    record["extra"].get("constraint_metrics") or {},
                    constraint_metrics,
                )
            if record["extra"]["dbnsfp_disease_associations"] or record["extra"]["omim_diseases"] or constraint_metrics:
                record["status_payload"]["record_count"] = (
                    len(record["extra"]["dbnsfp_disease_associations"])
                    + len(record["extra"]["omim_diseases"])
                    + len(record["extra"].get("constraint_metrics") or {})
                )
    return _finalize_gene_records(records_by_symbol)


async def _load_csv_dataset(
    *,
    name: str,
    url: str,
    parser,
) -> GeneBulkSourceDataset:
    try:
        text_value = await _download_text(url)
        records_by_symbol = parser(text_value)
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


async def load_human_gene_bulk_context(
    *,
    symbols: Iterable[str] | None = None,
) -> HumanGeneBulkContext:
    clingen_validity, clingen_dosage, gencc, clinvar_gene_condition = await asyncio.gather(
        _load_csv_dataset(
            name="ClinGen gene validity",
            url=settings.gene_reference_clingen_validity_url,
            parser=parse_clingen_validity_rows,
        ),
        _load_csv_dataset(
            name="ClinGen dosage",
            url=settings.gene_reference_clingen_dosage_url,
            parser=parse_clingen_dosage_rows,
        ),
        _load_csv_dataset(
            name="GenCC",
            url=settings.gene_reference_gencc_url,
            parser=parse_gencc_rows,
        ),
        _load_csv_dataset(
            name="ClinVar gene-condition",
            url=settings.gene_reference_clinvar_gene_condition_url,
            parser=parse_clinvar_gene_condition_rows,
        ),
    )
    datasets = {
        "clingen_gene_validity": clingen_validity,
        "clingen_dosage": clingen_dosage,
        "gencc": gencc,
        "clinvar_gene_condition": clinvar_gene_condition,
    }

    dbnsfp_path = str(settings.gene_reference_dbnsfp_gene_path or "").strip()
    if dbnsfp_path:
        try:
            records_by_symbol = parse_dbnsfp_gene_rows(dbnsfp_path, symbols=symbols)
            datasets["dbnsfp_gene"] = GeneBulkSourceDataset(
                name="dbNSFP gene",
                source_url=dbnsfp_path,
                status="success",
                records_by_symbol=records_by_symbol,
                payload={"symbols_with_records": len(records_by_symbol)},
            )
        except Exception as exc:  # pragma: no cover
            datasets["dbnsfp_gene"] = GeneBulkSourceDataset(
                name="dbNSFP gene",
                source_url=dbnsfp_path,
                status="error",
                message=str(exc),
            )
    else:
        datasets["dbnsfp_gene"] = GeneBulkSourceDataset(
            name="dbNSFP gene",
            source_url=None,
            status="missing",
            message="GENE_REFERENCE_DBNSFP_GENE_PATH is not configured",
        )

    return HumanGeneBulkContext(datasets=datasets)
