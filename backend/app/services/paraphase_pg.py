from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.sql import uuid_list_bindparam, uuid_values
from ..schemas import (
    FamilyParaphaseTableOut,
    ParaphaseGeneResultOut,
    ParaphaseExtraFieldOut,
    ParaphaseHaplotypeGroupOut,
    ParaphaseMetricOut,
    ParaphaseSampleResultOut,
)
from .family_metadata_context import FamilyMetadataContext

REPO_PARAPHASE_MEDICAL_REGIONS_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "ref-data" / "paraphase-medical-regions.json"
)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _count_items(value: Any) -> int:
    if isinstance(value, (dict, list, tuple, set)):
        return len(value)
    return 0


def _optional_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return len(value)
    return 1


def _max_optional(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


PARAPHASE_FIELD_LABELS = {
    "total_cn": "Total CN",
    "gene_cn": "Gene CN",
    "highest_total_cn": "Highest total CN",
    "smn1_cn": "SMN1 CN",
    "smn2_cn": "SMN2 CN",
    "smn_del78_cn": "SMNΔ7-8 CN",
    "smn2_del78_cn": "SMN2Δ7-8 CN",
    "smn1_read_number": "SMN1 reads c.840C",
    "smn2_read_number": "SMN2 reads c.840T",
    "smn_del78_read_number": "SMNΔ7-8 reads",
    "smn2_del78_read_number": "SMN2Δ7-8 reads",
    "smn1_haplotypes": "SMN1 haplotypes",
    "smn2_haplotypes": "SMN2 haplotypes",
    "smn_del78_haplotypes": "SMNΔ7-8 haplotypes",
    "smn2_del78_haplotypes": "SMN2Δ7-8 haplotypes",
    "final_haplotypes": "Final haplotypes",
    "assembled_haplotypes": "Assembled haplotypes",
    "two_copy_haplotypes": "Two-copy haplotypes",
}


def _candidate_paraphase_region_paths() -> list[Path]:
    paths: list[Path] = []
    if settings.paraphase_medical_regions_path:
        paths.append(Path(settings.paraphase_medical_regions_path))
    paths.append(REPO_PARAPHASE_MEDICAL_REGIONS_PATH)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


@lru_cache(maxsize=1)
def load_paraphase_medical_regions() -> list[dict[str, Any]]:
    for path in _candidate_paraphase_region_paths():
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw_regions = payload.get("regions") if isinstance(payload, dict) else payload
        if not isinstance(raw_regions, list):
            continue
        regions: list[dict[str, Any]] = []
        for raw_region in raw_regions:
            if not isinstance(raw_region, dict):
                continue
            region_id = str(raw_region.get("region_id") or raw_region.get("id") or "").strip()
            display_name = str(raw_region.get("display_name") or region_id).strip()
            if not region_id or not display_name:
                continue
            regions.append(
                {
                    "region_id": region_id,
                    "display_name": display_name,
                    "genes": [str(item).upper() for item in raw_region.get("genes") or [] if item],
                    "aliases": [str(item).lower() for item in raw_region.get("aliases") or [] if item],
                    "summary": raw_region.get("summary"),
                    "clinical_priority": int(raw_region.get("clinical_priority") or 999),
                    "key_copy_number_fields": [
                        str(item) for item in raw_region.get("key_copy_number_fields") or [] if item
                    ],
                    "key_read_fields": [
                        str(item) for item in raw_region.get("key_read_fields") or [] if item
                    ],
                    "key_haplotype_fields": [
                        str(item) for item in raw_region.get("key_haplotype_fields") or [] if item
                    ],
                    "key_extra_fields": [
                        str(item) for item in raw_region.get("key_extra_fields") or [] if item
                    ],
                    "field_descriptions": {
                        str(key): str(value)
                        for key, value in (raw_region.get("field_descriptions") or {}).items()
                        if key and value
                    },
                    "notes": [str(item) for item in raw_region.get("notes") or [] if item],
                    "disorders": [
                        {
                            "name": str(disorder.get("name") or ""),
                            "omim_url": disorder.get("omim_url"),
                        }
                        for disorder in raw_region.get("disorders") or []
                        if isinstance(disorder, dict) and disorder.get("name")
                    ],
                }
            )
        return regions
    return []


def _paraphase_region_for_gene(gene_symbol: str) -> dict[str, Any] | None:
    gene_key = str(gene_symbol or "").strip().lower()
    if not gene_key:
        return None
    for region in load_paraphase_medical_regions():
        aliases = set(region.get("aliases") or [])
        genes = {str(gene).lower() for gene in region.get("genes") or []}
        if gene_key in aliases or gene_key in genes:
            return region
    return None


def _region_out_payload(region: dict[str, Any]) -> dict[str, Any]:
    return {
        "region_id": region["region_id"],
        "display_name": region["display_name"],
        "genes": region.get("genes") or [],
        "summary": region.get("summary"),
        "clinical_priority": region.get("clinical_priority") or 999,
        "key_copy_number_fields": region.get("key_copy_number_fields") or [],
        "key_read_fields": region.get("key_read_fields") or [],
        "key_haplotype_fields": region.get("key_haplotype_fields") or [],
        "key_extra_fields": region.get("key_extra_fields") or [],
        "field_descriptions": region.get("field_descriptions") or {},
        "notes": region.get("notes") or [],
        "disorders": region.get("disorders") or [],
    }


def _humanize_key(key: str) -> str:
    if key in PARAPHASE_FIELD_LABELS:
        return PARAPHASE_FIELD_LABELS[key]
    return key.replace("_", " ").replace(" cn", " CN").title()


def _numeric_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _metric(key: str, value: Any) -> ParaphaseMetricOut:
    return ParaphaseMetricOut(key=key, label=_humanize_key(key), value=_numeric_value(value))


def _copy_number_expected_value(key: str) -> float:
    if key in {"smn_del78_cn", "smn2_del78_cn"}:
        return 0
    return 2


def _copy_number_is_signal(metric: ParaphaseMetricOut) -> bool:
    if metric.value is None:
        return True
    return metric.value != _copy_number_expected_value(metric.key)


def _extract_copy_number_metrics(payload: dict[str, Any], row: dict[str, Any]) -> list[ParaphaseMetricOut]:
    metrics: list[ParaphaseMetricOut] = []
    seen: set[str] = set()
    for key in ("total_cn", "gene_cn", "highest_total_cn"):
        value = row.get(key)
        if value is None and key not in payload:
            continue
        metrics.append(_metric(key, value if value is not None else payload.get(key)))
        seen.add(key)
    for key, value in payload.items():
        if key in seen:
            continue
        if key.endswith("_cn"):
            metrics.append(_metric(key, value))
    return metrics


def _extract_read_metrics(payload: dict[str, Any]) -> list[ParaphaseMetricOut]:
    return [
        _metric(key, value)
        for key, value in payload.items()
        if key.endswith("_read_number")
    ]


def _haplotype_names(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(item) for item in value.values()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return []


def _extract_haplotype_groups(payload: dict[str, Any]) -> list[ParaphaseHaplotypeGroupOut]:
    groups: list[ParaphaseHaplotypeGroupOut] = []
    for key, value in payload.items():
        if not key.endswith("_haplotypes") and key not in {"final_haplotypes", "assembled_haplotypes"}:
            continue
        haplotypes = _haplotype_names(value)
        if not haplotypes:
            continue
        groups.append(
            ParaphaseHaplotypeGroupOut(
                key=key,
                label=_humanize_key(key),
                count=len(haplotypes),
                haplotypes=haplotypes,
            )
        )
    return groups


_PARAPHASE_EXTRA_EXCLUDED_KEYS = {
    "total_cn",
    "gene_cn",
    "highest_total_cn",
    "sample_sex",
    "phase_region",
    "region_depth",
    "genome_depth",
    "final_haplotypes",
    "assembled_haplotypes",
    "two_copy_haplotypes",
    "sites_for_phasing",
    "heterozygous_sites",
    "fusions_called",
}


def _json_safe_extra_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe_extra_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_extra_value(item) for item in value]
    return value


def _extract_extra_fields(
    payload: dict[str, Any],
    region_info: dict[str, Any] | None,
) -> list[ParaphaseExtraFieldOut]:
    if region_info is None:
        return []
    field_descriptions = region_info.get("field_descriptions") or {}
    configured_keys = [str(key) for key in region_info.get("key_extra_fields") or [] if key]
    payload_keys = [
        key
        for key in payload
        if key not in _PARAPHASE_EXTRA_EXCLUDED_KEYS
        and not key.endswith("_cn")
        and not key.endswith("_read_number")
        and not key.endswith("_haplotypes")
    ]
    ordered_keys = [
        *[key for key in configured_keys if key in payload],
        *sorted(key for key in payload_keys if key not in set(configured_keys)),
    ]
    fields: list[ParaphaseExtraFieldOut] = []
    for key in ordered_keys:
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        fields.append(
            ParaphaseExtraFieldOut(
                key=key,
                label=_humanize_key(key),
                value=_json_safe_extra_value(value),
                description=field_descriptions.get(key),
            )
        )
    return fields


async def get_family_paraphase_table_response(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
) -> FamilyParaphaseTableOut:
    sample_ids = list(context.sample_uuid_to_name)
    samples = [
        {
            "sample_id": row["sample_id"],
            "role": row.get("role", "sibling"),
            "affected": bool(row.get("affected", False)),
            "sex": row.get("sex", "und"),
        }
        for row in context.sample_rows
    ]
    if not sample_ids:
        return FamilyParaphaseTableOut(samples=samples, genes=[])

    result = await session.execute(
        text(
            """
            SELECT
                sample_id::text AS sample_uuid,
                gene_symbol,
                total_cn,
                gene_cn,
                highest_total_cn,
                sample_sex,
                phase_region,
                region_depth,
                genome_depth,
                payload,
                uploaded_at
            FROM sample_paraphase_results
            WHERE family_id = CAST(:family_id AS uuid)
              AND sample_id IN :sample_ids
            ORDER BY lower(gene_symbol), sample_id
            """
        ).bindparams(uuid_list_bindparam("sample_ids")),
        {
            "family_id": context.family_uuid,
            "sample_ids": uuid_values(sample_ids),
        },
    )
    rows = [dict(row) for row in result.mappings().all()]
    member_meta = {
        row["sample_uuid"]: {
            "role": row.get("role"),
            "affected": row.get("affected"),
            "sex": row.get("sex"),
        }
        for row in context.sample_rows
    }

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        gene_symbol = str(row.get("gene_symbol") or "Unknown")
        region_info = _paraphase_region_for_gene(gene_symbol)
        gene = grouped.setdefault(
            gene_symbol,
            {
                "gene_symbol": gene_symbol,
                "is_medically_relevant": region_info is not None,
                "region_info": _region_out_payload(region_info) if region_info else None,
                "max_total_cn": None,
                "max_gene_cn": None,
                "max_highest_total_cn": None,
                "has_copy_number_signal": False,
                "samples": {},
            },
        )
        payload = _json_object(row.get("payload"))
        region_depth = _json_object(row.get("region_depth"))
        sample_uuid = row["sample_uuid"]
        sample_name = context.sample_uuid_to_name.get(sample_uuid, sample_uuid)
        meta = member_meta.get(sample_uuid, {})
        copy_number_metrics = _extract_copy_number_metrics(payload, row)
        read_metrics = _extract_read_metrics(payload)
        haplotype_groups = _extract_haplotype_groups(payload)
        extra_fields = _extract_extra_fields(payload, region_info)
        copy_number_signal = any(_copy_number_is_signal(metric) for metric in copy_number_metrics)
        sample_result = ParaphaseSampleResultOut(
            sample=sample_name,
            role=meta.get("role"),
            affected=meta.get("affected"),
            sex=meta.get("sex"),
            total_cn=row.get("total_cn"),
            gene_cn=row.get("gene_cn"),
            highest_total_cn=row.get("highest_total_cn"),
            sample_sex=row.get("sample_sex"),
            phase_region=row.get("phase_region"),
            region_depth=region_depth,
            genome_depth=row.get("genome_depth"),
            final_haplotype_count=_count_items(payload.get("final_haplotypes")),
            assembled_haplotype_count=_count_items(payload.get("assembled_haplotypes")),
            variant_site_count=_count_items(payload.get("sites_for_phasing")),
            heterozygous_site_count=_count_items(payload.get("heterozygous_sites")),
            fusion_count=_optional_count(payload.get("fusions_called")),
            copy_number_signal=copy_number_signal,
            copy_number_metrics=copy_number_metrics,
            read_metrics=read_metrics,
            haplotype_groups=haplotype_groups,
            extra_fields=extra_fields,
            uploaded_at=row.get("uploaded_at"),
        )
        gene["samples"][sample_name] = sample_result
        gene["max_total_cn"] = _max_optional([gene["max_total_cn"], sample_result.total_cn])
        gene["max_gene_cn"] = _max_optional([gene["max_gene_cn"], sample_result.gene_cn])
        gene["max_highest_total_cn"] = _max_optional(
            [gene["max_highest_total_cn"], sample_result.highest_total_cn]
        )
        gene["has_copy_number_signal"] = bool(
            gene["has_copy_number_signal"] or sample_result.copy_number_signal
        )

    genes = [ParaphaseGeneResultOut(**row) for row in grouped.values()]
    genes.sort(
        key=lambda row: (
            not row.is_medically_relevant,
            row.region_info.clinical_priority if row.region_info else 999,
            row.max_total_cn is None and row.max_gene_cn is None,
            row.gene_symbol.lower(),
        )
    )
    return FamilyParaphaseTableOut(samples=samples, genes=genes)
