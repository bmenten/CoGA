from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

import httpx
from fastapi import HTTPException

from ..schemas import (
    GeneLocation,
    PanelAppConfidenceCounts,
    PanelAppPanelCountsOut,
    PanelAppPanelSummaryOut,
)

PANELAPP_API_ROOT = "https://panelapp.genomicsengland.co.uk/api/v1"
PANELAPP_WEB_ROOT = "https://panelapp.genomicsengland.co.uk"

# PanelApp's confidence levels by name, highest first. Level 0 (no rating, or removed by an
# expert review) is none of them: it is counted, never imported.
PANELAPP_CONFIDENCE_NAMES: dict[str, str] = {"3": "green", "2": "amber", "1": "red"}


@dataclass(frozen=True)
class PanelAppImportContent:
    genes: list[str]
    regions: list[GeneLocation]
    metadata: dict[str, Any]


def panelapp_panel_url(panelapp_id: int) -> str:
    return f"{PANELAPP_WEB_ROOT}/panels/{panelapp_id}/"


def panelapp_summary(payload: dict[str, Any]) -> PanelAppPanelSummaryOut:
    stated_stats = payload.get("stats")
    stats = stated_stats if isinstance(stated_stats, dict) else {}
    stated_id: Any = payload.get("id")
    panelapp_id = int(stated_id)
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


async def _get_panelapp_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(f"{PANELAPP_API_ROOT}{path}", params=params)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="PanelApp panel not found") from exc
        raise HTTPException(status_code=502, detail="Failed to retrieve PanelApp data") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        # A 200 whose body is no JSON (a proxy's HTML error page, say): a fault upstream like
        # a failed request, not a 500.
        raise HTTPException(status_code=502, detail="Unexpected PanelApp response") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Unexpected PanelApp response")
    return payload


async def search_panelapp_panels(query: str, *, page_size: int = 20) -> tuple[list[PanelAppPanelSummaryOut], int]:
    params: dict[str, Any] = {"page_size": max(1, min(page_size, 50))}
    cleaned_query = query.strip()
    if cleaned_query:
        params["name"] = cleaned_query
    payload = await _get_panelapp_json("/panels/", params=params)
    stated_results = payload.get("results")
    results = stated_results if isinstance(stated_results, list) else []
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


def panelapp_selection(confidence_levels: Sequence[str]) -> str:
    """The confidence levels an import takes, as the panel's ``external_selection`` keeps
    them: each once, lowest first ("3", "2,3", "1,2,3"). Each selection of one PanelApp panel
    is a panel of its own."""
    return ",".join(sorted({str(level) for level in confidence_levels}))


def panelapp_selection_label(confidence_levels: Sequence[str]) -> str:
    """The confidence levels by name, highest first: "green", "green + amber",
    "green + amber + red"."""
    selected = {str(level) for level in confidence_levels}
    return " + ".join(name for level, name in PANELAPP_CONFIDENCE_NAMES.items() if level in selected)


def _confidence_counts(entities: Any) -> PanelAppConfidenceCounts:
    counts = {"green": 0, "amber": 0, "red": 0, "unrated": 0}
    for entity in entities if isinstance(entities, list) else []:
        if not isinstance(entity, dict):
            continue
        counts[PANELAPP_CONFIDENCE_NAMES.get(_entity_confidence(entity), "unrated")] += 1
    return PanelAppConfidenceCounts(total=sum(counts.values()), **counts)


def panelapp_confidence_counts(payload: dict[str, Any]) -> PanelAppPanelCountsOut:
    """How many of the panel's genes, STRs and regions hold each confidence level, as the
    panel's entities give them (the panel list's ``stats`` holds only the totals)."""
    summary = panelapp_summary(payload)
    return PanelAppPanelCountsOut(
        panelapp_id=summary.panelapp_id,
        version=summary.version,
        genes=_confidence_counts(payload.get("genes")),
        strs=_confidence_counts(payload.get("strs")),
        regions=_confidence_counts(payload.get("regions")),
    )


def _entity_symbol(entity: dict[str, Any]) -> str | None:
    stated_gene_data = entity.get("gene_data")
    gene_data = stated_gene_data if isinstance(stated_gene_data, dict) else {}
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
    stated_gene_data = entity.get("gene_data")
    gene_data = stated_gene_data if isinstance(stated_gene_data, dict) else {}
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


def panelapp_default_panel_name(payload: dict[str, Any], confidence_levels: Sequence[str]) -> str:
    """The panel's name unless the import gives one, with the confidence levels it takes:
    each selection of one PanelApp panel is a panel of its own, and a panel's name is unique."""
    summary = panelapp_summary(payload)
    return f"PanelApp {summary.panelapp_id}: {summary.name} ({panelapp_selection_label(confidence_levels)})"
