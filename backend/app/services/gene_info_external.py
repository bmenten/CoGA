from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from ..core.http_resilience import resilient_request
from .gene_info_bulk_sources import HumanGeneBulkContext, build_bulk_gene_bundle, merge_gene_extra


def as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if isinstance(value, tuple):
        return [str(item) for item in value if item not in (None, "")]
    if value == "":
        return []
    return [str(value)]


def first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text_value = str(value).strip()
        if text_value:
            return text_value
    return None


def source_status(
    *,
    status: str,
    source_url: str | None = None,
    payload: Dict[str, Any] | None = None,
    message: str | None = None,
) -> Dict[str, Any]:
    return {
        "status": status,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_url": source_url,
        "message": message,
        "payload": payload or {},
    }


async def fetch_ncbi_gene(symbol: str, species_name: str) -> Dict[str, Any]:
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    search_response = await resilient_request(
        "GET",
        search_url,
        params={
            "db": "gene",
            "term": f"{symbol}[sym] AND {species_name}[orgn]",
            "retmode": "json",
        },
    )
    search_response.raise_for_status()
    ids = search_response.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return {}
    summary_response = await resilient_request(
        "GET", summary_url, params={"db": "gene", "id": ids[0], "retmode": "json"}
    )
    summary_response.raise_for_status()
    summary_data = summary_response.json().get("result", {})
    return summary_data.get(ids[0], {})


async def fetch_external_gene_bundle(
    *,
    symbol: str,
    species_document: Dict[str, Any],
    species_docs: List[Dict[str, Any]],
    bulk_context: HumanGeneBulkContext | None = None,
) -> Dict[str, Any]:
    cleaned_symbol = symbol.strip()
    is_human = str(species_document.get("name", "")).lower() == "homo sapiens"
    bulk_bundle = build_bulk_gene_bundle(symbol=cleaned_symbol, bulk_context=bulk_context)
    if is_human and bulk_bundle.get("primary_source") == "dbnsfp_gene":
        local_profile = bulk_bundle.get("profile") or {}
        local_extra = bulk_bundle.get("extra") or {}
        return {
            "display_name": first_non_empty(
                local_profile.get("display_name"),
                local_extra.get("hgnc_name"),
            ),
            "summary": first_non_empty(
                local_profile.get("summary"),
                local_extra.get("ensembl_description"),
                (local_extra.get("clingen_gene_facts") or {}).get("function"),
            ),
            # HGNC is the register for what a gene is called and what it used to be
            # called, so its symbol lists take precedence over anything a derived
            # source carries in its own profile.
            "aliases": sorted(
                {
                    alias
                    for alias in (
                        as_list(bulk_bundle.get("aliases")) + as_list(local_profile.get("aliases"))
                    )
                    if alias and alias != cleaned_symbol
                }
            ),
            "previous_symbols": sorted(
                set(
                    as_list(bulk_bundle.get("previous_symbols"))
                    + as_list(local_profile.get("previous_symbols"))
                )
            ),
            "ensembl_gene_id": first_non_empty(local_profile.get("ensembl_gene_id")),
            "ncbi_gene_id": first_non_empty(local_profile.get("ncbi_gene_id")),
            "hgnc_id": first_non_empty(local_profile.get("hgnc_id")),
            "omim_gene_id": first_non_empty(bulk_bundle.get("omim_gene_id")),
            "gene_type": first_non_empty(local_profile.get("gene_type")),
            "location": first_non_empty(local_profile.get("location")),
            "homologs": bulk_bundle.get("homologs") or [],
            "source_status": bulk_bundle.get("source_status") or {},
            "extra": local_extra,
        }

    source_status_map: Dict[str, Dict[str, Any]] = {}

    # NCBI is the only per-gene request left. The HGNC, Ensembl, Ensembl-homology and
    # ClinGen-page lookups that used to run here were removed: measured over 4,052 genes
    # they returned fields the bulk HGNC complete set already holds, a canonical
    # transcript GENCODE tags per transcript, no orthologue at all (0 of 1,380), and
    # ClinGen fields that were null for ~99% of them. NCBI stays because it is the only
    # source of a gene summary for genes outside dbNSFP.
    ncbi_payload: Dict[str, Any] = {}
    try:
        ncbi_payload = await fetch_ncbi_gene(cleaned_symbol, str(species_document.get("name")))
        source_status_map["ncbi"] = source_status(
            status="success" if ncbi_payload else "missing",
            source_url="https://www.ncbi.nlm.nih.gov/home/develop/api/",
            payload=ncbi_payload,
            message=None if ncbi_payload else "No NCBI Gene record returned",
        )
    except Exception as error:  # pragma: no cover
        source_status_map["ncbi"] = source_status(
            status="error",
            source_url="https://www.ncbi.nlm.nih.gov/home/develop/api/",
            message=str(error),
        )

    # Everything the per-gene HGNC call used to supply comes off the bulk complete set:
    # the same authority, already downloaded once for the whole job.
    bulk_profile = bulk_bundle.get("profile") or {}
    bulk_extra = bulk_bundle.get("extra") or {}
    hgnc_identifiers = bulk_extra.get("hgnc_identifiers") or {}
    hgnc_gene_facts = bulk_extra.get("hgnc_gene_facts") or {}
    aliases = sorted(
        {
            alias
            for alias in (
                as_list(ncbi_payload.get("otheraliases")) + as_list(bulk_bundle.get("aliases"))
            )
            if alias and alias != cleaned_symbol
        }
    )
    previous_symbols = sorted(set(as_list(bulk_bundle.get("previous_symbols"))))
    omim_ids = as_list(hgnc_identifiers.get("omim_ids"))
    extra = {
        "hgnc_name": bulk_profile.get("display_name"),
        "hgnc_gene_group": as_list(hgnc_gene_facts.get("gene_group")),
        "hgnc_vega_id": first_non_empty(hgnc_identifiers.get("vega_id")),
        "refseq_accessions": as_list(hgnc_identifiers.get("refseq_accession")),
        "ncbi_other_designations": as_list(ncbi_payload.get("otherdesignations")),
    }
    source_status_map.update(bulk_bundle.get("source_status") or {})
    # The bulk bundle carries the ClinGen curation counts, gene facts and GenCC
    # classifications, so those keys still arrive — from the bulk files rather than a
    # per-gene scrape.
    extra = merge_gene_extra(extra, bulk_bundle.get("extra") or {})

    return {
        "display_name": first_non_empty(
            bulk_profile.get("display_name"),
            ncbi_payload.get("description"),
        ),
        "summary": first_non_empty(
            ncbi_payload.get("summary"),
            (bulk_extra.get("clingen_gene_facts") or {}).get("function"),
        ),
        "aliases": aliases,
        "previous_symbols": previous_symbols,
        "ensembl_gene_id": first_non_empty(bulk_profile.get("ensembl_gene_id")),
        "ncbi_gene_id": first_non_empty(
            ncbi_payload.get("uid"),
            bulk_profile.get("ncbi_gene_id"),
        ),
        "hgnc_id": first_non_empty(bulk_profile.get("hgnc_id")),
        "omim_gene_id": first_non_empty(
            omim_ids[0] if omim_ids else None,
            bulk_bundle.get("omim_gene_id"),
        ),
        "gene_type": first_non_empty(
            hgnc_gene_facts.get("locus_group"),
            bulk_profile.get("gene_type"),
        ),
        "location": first_non_empty(
            bulk_profile.get("location"),
            ncbi_payload.get("maplocation"),
        ),
        # dbNSFP supplies model-organism orthologues in bulk; nothing on this path adds
        # to them, so the bundle's own list stands.
        "homologs": bulk_bundle.get("homologs") or [],
        "source_status": source_status_map,
        "extra": extra,
    }
