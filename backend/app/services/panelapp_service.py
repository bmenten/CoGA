from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

import httpx
from fastapi import HTTPException

from ..schemas import GeneLocation, PanelAppPanelSummaryOut

PANELAPP_API_ROOT = "https://panelapp.genomicsengland.co.uk/api/v1"
PANELAPP_WEB_ROOT = "https://panelapp.genomicsengland.co.uk"


@dataclass(frozen=True)
class PanelAppImportContent:
    genes: list[str]
    regions: list[GeneLocation]
    metadata: dict[str, Any]


def panelapp_panel_url(panelapp_id: int) -> str:
    return f"{PANELAPP_WEB_ROOT}/panels/{panelapp_id}/"


def panelapp_summary(payload: dict[str, Any]) -> PanelAppPanelSummaryOut:
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    panelapp_id = int(payload.get("id"))
    return PanelAppPanelSummaryOut(
        panelapp_id=panelapp_id,
        name=str(payload.get("name") or f"PanelApp {panelapp_id}"),
        disease_group=payload.get("disease_group") or None,
        disease_sub_group=payload.get("disease_sub_group") or None,
        status=payload.get("status") or None,
        version=str(payload.get("version")) if payload.get("version") not in (None, "") else None,
        version_created=payload.get("version_created") or None,
        relevant_disorders=[str(value) for value in payload.get("relevant_disorders") or [] if value],
        gene_count=int(stats.get("number_of_genes") or 0),
        str_count=int(stats.get("number_of_strs") or 0),
        region_count=int(stats.get("number_of_regions") or 0),
        types=[
            str(entry.get("name"))
            for entry in payload.get("types") or []
            if isinstance(entry, dict) and entry.get("name")
        ],
        url=panelapp_panel_url(panelapp_id),
    )


def _raise_panelapp_error(exc: Exception) -> HTTPException:
    _ = exc
    return HTTPException(status_code=502, detail="Failed to retrieve PanelApp data")


async def _get_panelapp_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(f"{PANELAPP_API_ROOT}{path}", params=params)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="PanelApp panel not found") from exc
        raise _raise_panelapp_error(exc) from exc
    except httpx.HTTPError as exc:
        raise _raise_panelapp_error(exc) from exc
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Unexpected PanelApp response")
    return payload


async def search_panelapp_panels(query: str, *, page_size: int = 20) -> tuple[list[PanelAppPanelSummaryOut], int]:
    params: dict[str, Any] = {"page_size": max(1, min(page_size, 50))}
    cleaned_query = query.strip()
    if cleaned_query:
        params["name"] = cleaned_query
    payload = await _get_panelapp_json("/panels/", params=params)
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    return [panelapp_summary(row) for row in results if isinstance(row, dict)], int(payload.get("count") or 0)


async def fetch_panelapp_panel(panelapp_id: int, *, version: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"format": "json"}
    if version:
        params["version"] = version
    # panelapp_id is int-typed (Pydantic ge=1) at the router edge; the explicit int()
    # pins that numeric invariant at the URL sink so no '/' or traversal can enter the
    # request path even if a future caller forgets upstream validation (partial-SSRF guard).
    return await _get_panelapp_json(f"/panels/{int(panelapp_id)}/", params=params)


def _entity_confidence(entity: dict[str, Any]) -> str:
    return str(entity.get("confidence_level") or "").strip()


def _entity_symbol(entity: dict[str, Any]) -> str | None:
    gene_data = entity.get("gene_data") if isinstance(entity.get("gene_data"), dict) else {}
    for value in (
        gene_data.get("hgnc_symbol"),
        gene_data.get("gene_symbol"),
        entity.get("entity_name"),
    ):
        text_value = str(value or "").strip()
        if text_value:
            return text_value
    return None


def _assembly_key(assembly: str) -> str:
    return "GRch37" if assembly.upper() in {"GRCH37", "HG19"} else "GRch38"


def _parse_location(location: Any) -> tuple[str, int, int] | None:
    match = re.match(r"^(?:chr)?([^:]+):(\d+)-(\d+)$", str(location or "").strip(), flags=re.IGNORECASE)
    if match is None:
        return None
    start = int(match.group(2))
    end = int(match.group(3))
    if end < start:
        start, end = end, start
    return match.group(1), start, end


def _location_from_gene_data(gene_data: dict[str, Any], assembly: str) -> tuple[str, int, int] | None:
    ensembl_genes = gene_data.get("ensembl_genes")
    if not isinstance(ensembl_genes, dict):
        return None
    assembly_entry = ensembl_genes.get(_assembly_key(assembly))
    if not isinstance(assembly_entry, dict) or not assembly_entry:
        return None
    def release_rank(value: str) -> tuple[int, str]:
        return (int(value), value) if str(value).isdigit() else (-1, str(value))

    for release_key in sorted((str(key) for key in assembly_entry), key=release_rank, reverse=True):
        release_entry = assembly_entry.get(release_key)
        if not isinstance(release_entry, dict):
            continue
        parsed = _parse_location(release_entry.get("location"))
        if parsed is not None:
            return parsed
    return None


def _location_from_coordinates(entity: dict[str, Any], assembly: str) -> tuple[str, int, int] | None:
    key = "grch37_coordinates" if assembly.upper() in {"GRCH37", "HG19"} else "grch38_coordinates"
    coordinates = entity.get(key)
    chrom = str(entity.get("chromosome") or "").strip()
    if not chrom or not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    try:
        start = int(coordinates[0])
        end = int(coordinates[1])
    except (TypeError, ValueError):
        return None
    if end < start:
        start, end = end, start
    return chrom, start, end


def _append_unique(values: list[str], value: str | None) -> None:
    cleaned = str(value or "").strip()
    if cleaned and cleaned not in values:
        values.append(cleaned)


def _append_region(regions: list[GeneLocation], region: GeneLocation) -> None:
    key = (region.gene.upper(), region.chr, region.start, region.end)
    if key not in {(row.gene.upper(), row.chr, row.start, row.end) for row in regions}:
        regions.append(region)


def _region_from_gene_entity(entity: dict[str, Any], assembly: str, symbol: str) -> GeneLocation | None:
    gene_data = entity.get("gene_data") if isinstance(entity.get("gene_data"), dict) else {}
    parsed = _location_from_gene_data(gene_data, assembly)
    if parsed is None:
        return None
    chrom, start, end = parsed
    return GeneLocation(gene=symbol, chr=chrom, start=start, end=end)


def _region_from_coordinate_entity(entity: dict[str, Any], assembly: str, label: str) -> GeneLocation | None:
    parsed = _location_from_coordinates(entity, assembly)
    if parsed is None:
        return None
    chrom, start, end = parsed
    return GeneLocation(gene=label, chr=chrom, start=start, end=end)


def extract_panelapp_import_content(
    payload: dict[str, Any],
    *,
    confidence_levels: Sequence[str],
    include_regions: bool,
    include_strs: bool,
    assembly: str,
) -> PanelAppImportContent:
    selected_levels = {str(level) for level in confidence_levels}
    genes: list[str] = []
    regions: list[GeneLocation] = []
    source_counts = {"genes": 0, "regions": 0, "strs": 0}

    for entity in payload.get("genes") or []:
        if not isinstance(entity, dict) or _entity_confidence(entity) not in selected_levels:
            continue
        symbol = _entity_symbol(entity)
        if not symbol:
            continue
        _append_unique(genes, symbol)
        source_counts["genes"] += 1
        region = _region_from_gene_entity(entity, assembly, symbol)
        if region is not None:
            _append_region(regions, region)

    if include_strs:
        for entity in payload.get("strs") or []:
            if not isinstance(entity, dict) or _entity_confidence(entity) not in selected_levels:
                continue
            symbol = _entity_symbol(entity)
            if symbol:
                _append_unique(genes, symbol)
            label = symbol or str(entity.get("entity_name") or "").strip()
            if not label:
                continue
            source_counts["strs"] += 1
            region = _region_from_coordinate_entity(entity, assembly, label)
            if region is not None:
                _append_region(regions, region)

    if include_regions:
        for entity in payload.get("regions") or []:
            if not isinstance(entity, dict) or _entity_confidence(entity) not in selected_levels:
                continue
            label = str(entity.get("entity_name") or entity.get("verbose_name") or "").strip()
            if not label:
                continue
            source_counts["regions"] += 1
            region = _region_from_coordinate_entity(entity, assembly, label)
            if region is not None:
                _append_region(regions, region)

    summary = panelapp_summary(payload)
    return PanelAppImportContent(
        genes=genes,
        regions=regions,
        metadata={
            "panelapp_id": summary.panelapp_id,
            "panelapp_name": summary.name,
            "version": summary.version,
            "version_created": summary.version_created,
            "disease_group": summary.disease_group,
            "disease_sub_group": summary.disease_sub_group,
            "status": summary.status,
            "relevant_disorders": summary.relevant_disorders,
            "types": summary.types,
            "stats": {
                "number_of_genes": summary.gene_count,
                "number_of_strs": summary.str_count,
                "number_of_regions": summary.region_count,
            },
            "import_options": {
                "confidence_levels": sorted(selected_levels),
                "include_regions": include_regions,
                "include_strs": include_strs,
                "assembly": assembly,
            },
            "selected_counts": source_counts,
            "source_url": panelapp_panel_url(summary.panelapp_id),
        },
    )


def panelapp_default_panel_name(payload: dict[str, Any]) -> str:
    summary = panelapp_summary(payload)
    return f"PanelApp {summary.panelapp_id}: {summary.name}"
