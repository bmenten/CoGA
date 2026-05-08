from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from ..core.clickhouse import execute_clickhouse
from ..core.config import settings

_VALID_CLICKHOUSE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_HET_GT_VALUES = {"0/1", "1/0", "0|1", "1|0", "HET"}


def _validate_segment(value: str) -> str:
    if not _VALID_CLICKHOUSE_SEGMENT.fullmatch(value):
        raise HTTPException(status_code=400, detail="Assembly name is invalid")
    return value


def _table_name(assembly_name: str, suffix: str) -> str:
    dataset = _validate_segment(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SNV_INDEL/{suffix}`"


@dataclass(slots=True)
class SmallVariantFamilyRecord:
    variant_key: int | None
    variant_id: str
    gene_symbols: list[str]
    sample_calls: dict[str, str]
    annotations: list[dict[str, Any]]


def _decode_json_payload(raw_value: Any) -> Any:
    if raw_value in (None, "", b""):
        return None
    if isinstance(raw_value, (dict, list)):
        return raw_value
    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode()
    try:
        return json.loads(str(raw_value))
    except json.JSONDecodeError:
        return None


def _collect_annotations(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (
        "annotations",
        "sortedTranscriptConsequences",
        "transcriptConsequences",
        "transcripts",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    for value in payload.values():
        annotations = _collect_annotations(value)
        if annotations:
            return annotations
    return []


def _collect_gene_ids(annotations: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for annotation in annotations:
        for key in ("gene_id", "geneId", "geneID", "ensembl_gene_id", "geneIdVersion"):
            raw_value = annotation.get(key)
            if raw_value not in (None, ""):
                values.add(str(raw_value))
    return values


def _rows_to_variant_record(rows: list[tuple[Any, ...]]) -> SmallVariantFamilyRecord | None:
    if not rows:
        return None
    variant_key, variant_id, gene_symbols, sample_ids, sample_gts, annotations_json = rows[0]
    sample_calls = {
        str(sample_id): str(gt)
        for sample_id, gt in zip(sample_ids or [], sample_gts or [])
        if sample_id not in (None, "")
    }
    annotations = _collect_annotations(_decode_json_payload(annotations_json))
    normalized_gene_symbols = [
        str(symbol).strip()
        for symbol in (gene_symbols or [])
        if str(symbol).strip()
    ]
    return SmallVariantFamilyRecord(
        variant_key=int(variant_key) if variant_key is not None else None,
        variant_id=str(variant_id),
        gene_symbols=normalized_gene_symbols,
        sample_calls=sample_calls,
        annotations=annotations,
    )


async def get_small_variant_family_record(
    *,
    assembly_name: str,
    family_guid: str,
    variant_id: str,
) -> SmallVariantFamilyRecord | None:
    entries_table = _table_name(assembly_name, "entries")
    details_table = _table_name(assembly_name, "variants/details")
    query = f"""
        SELECT
            e.key,
            e.variantId,
            e.gene_symbols,
            e.calls.sampleId,
            e.calls.gt,
            d.annotationsJson
        FROM {entries_table} AS e
        LEFT JOIN {details_table} AS d ON d.key = e.key
        WHERE e.family_guid = %(family_guid)s
          AND e.variantId = %(variant_id)s
          AND e.sign = 1
        LIMIT 1
    """
    rows = await execute_clickhouse(
        query,
        {"family_guid": family_guid, "variant_id": variant_id},
    )
    return _rows_to_variant_record(rows)


def variants_share_gene(
    left: SmallVariantFamilyRecord,
    right: SmallVariantFamilyRecord,
) -> bool:
    left_genes = {gene.lower() for gene in left.gene_symbols if gene}
    right_genes = {gene.lower() for gene in right.gene_symbols if gene}
    if left_genes and right_genes and left_genes.intersection(right_genes):
        return True
    return bool(_collect_gene_ids(left.annotations).intersection(_collect_gene_ids(right.annotations)))


def has_affected_het_call(
    variant: SmallVariantFamilyRecord,
    affected_sample_names: list[str],
) -> bool:
    if not affected_sample_names:
        return True
    return any(
        str(variant.sample_calls.get(sample_name, "")).strip() in _HET_GT_VALUES
        for sample_name in affected_sample_names
    )
