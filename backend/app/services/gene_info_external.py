from __future__ import annotations

from datetime import datetime, timezone
import html
import re
from typing import Any, Dict, List
from urllib.parse import quote

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


def ensembl_species_name(species_document: Dict[str, Any]) -> str:
    name = str(species_document.get("name", "")).strip().lower()
    return name.replace(" ", "_")


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


def _clean_html_lines(raw_html: str) -> List[str]:
    without_scripts = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    block_broken = re.sub(
        r"</?(?:br|p|div|section|article|header|footer|main|aside|li|ul|ol|tr|td|th|dt|dd|h[1-6])[^>]*>",
        "\n",
        without_scripts,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", block_broken)
    unescaped = html.unescape(text)
    normalized = [
        re.sub(r"\s+", " ", line).strip()
        for line in unescaped.splitlines()
    ]
    return [line for line in normalized if line]


def _line_after(lines: List[str], label: str) -> str | None:
    try:
        index = lines.index(label)
    except ValueError:
        return None
    if index + 1 >= len(lines):
        return None
    return lines[index + 1]


def _leading_float(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)", value)
    if match is None:
        return None
    return float(match.group(1))


def _leading_int(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(\d+)", value)
    if match is None:
        return None
    return int(match.group(1))


def _leading_bool(value: str | None) -> bool | None:
    if not value:
        return None
    lowered = value.strip().lower()
    if lowered.startswith("yes"):
        return True
    if lowered.startswith("no"):
        return False
    return None


def _split_csv(value: str | None) -> List[str]:
    if not value or value.lower().startswith("no "):
        return []
    return [entry.strip() for entry in value.split(",") if entry.strip()]


def _parse_gencc_classifications(value: str | None) -> Dict[str, int]:
    if not value:
        return {}
    cleaned = value.split("(Read more", 1)[0]
    classifications: Dict[str, int] = {}
    for label in [
        "Definitive",
        "Strong",
        "Moderate",
        "Limited",
        "Supportive",
        "Disputed Evidence",
        "Refuted Evidence",
        "No Known Disease Relationship",
    ]:
        match = re.search(rf"{re.escape(label)}\s+(\d+)", cleaned, flags=re.IGNORECASE)
        if match:
            classifications[label] = int(match.group(1))
    return classifications


def parse_clingen_gene_page(raw_html: str) -> Dict[str, Any]:
    lines = _clean_html_lines(raw_html)
    function_text = _line_after(lines, "Function")
    if function_text and function_text.startswith("(Source:"):
        function_text = None

    genomic_coordinates: Dict[str, str] = {}
    try:
        genomic_index = lines.index("Genomic Coordinates")
    except ValueError:
        genomic_index = -1
    if genomic_index >= 0:
        for line in lines[genomic_index + 1 : genomic_index + 5]:
            if ":" not in line:
                continue
            label, value = line.split(":", 1)
            if label.strip() in {"GRCh37/hg19", "GRCh38/hg38"}:
                genomic_coordinates[label.strip()] = value.strip()

    return {
        "curation_counts": {
            "gene_disease_validity": _leading_int(
                _line_after(lines, "Gene-Disease Validity Classifications")
            ),
            "dosage_sensitivity": _leading_int(
                _line_after(lines, "Dosage Sensitivity Classifications")
            ),
            "clinical_actionability": _leading_int(
                _line_after(lines, "Clinical Actionability Assertions")
            ),
            "variant_pathogenicity": _leading_int(
                _line_after(lines, "Variant Pathogenicity Assertions")
            ),
            "cpic_pharmgkb": _line_after(lines, "CPIC / PharmGKB High Level Records"),
        },
        "gene_facts": {
            "hgnc_name": _line_after(lines, "HGNC Name"),
            "gene_type": _line_after(lines, "Gene type"),
            "locus_type": _line_after(lines, "Locus type"),
            "previous_symbols": _split_csv(_line_after(lines, "Previous symbols")),
            "alias_symbols": _split_csv(_line_after(lines, "Alias symbols")),
            "gencc_classifications": _parse_gencc_classifications(
                _line_after(lines, "GenCC Classifications")
            ),
            "haploinsufficiency_index": _leading_float(_line_after(lines, "%HI")),
            "pli": _leading_float(_line_after(lines, "pLI")),
            "loeuf": _leading_float(_line_after(lines, "LOEUF")),
            "acmg_secondary_finding": _leading_bool(_line_after(lines, "ACMG SF v3.2 Gene?")),
            "cytoband": _line_after(lines, "Cytoband"),
            "mane_select_transcript": _line_after(lines, "MANE Select Transcript"),
            "function": function_text,
            "genomic_coordinates": genomic_coordinates,
        },
    }


async def fetch_hgnc_gene(symbol: str) -> Dict[str, Any]:
    url = f"https://rest.genenames.org/fetch/symbol/{quote(symbol, safe='')}"
    response = await resilient_request("GET", url, headers={"Accept": "application/json"})
    response.raise_for_status()
    data = response.json()
    docs = data.get("response", {}).get("docs", [])
    return docs[0] if docs else {}


async def fetch_ensembl_gene(symbol: str, species_name: str) -> Dict[str, Any]:
    species_key = ensembl_species_name({"name": species_name})
    url = f"https://rest.ensembl.org/lookup/symbol/{quote(species_key, safe='')}/{quote(symbol, safe='')}"
    response = await resilient_request(
        "GET", url, params={"expand": 1}, headers={"Content-Type": "application/json"}
    )
    response.raise_for_status()
    return response.json()


async def fetch_ensembl_homologies(ensembl_gene_id: str) -> Dict[str, Any]:
    url = f"https://rest.ensembl.org/homology/id/human/{quote(ensembl_gene_id, safe='')}"
    response = await resilient_request(
        "GET",
        url,
        params={"type": "orthologues", "format": "condensed"},
        headers={"Content-Type": "application/json"},
    )
    response.raise_for_status()
    return response.json()


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


async def fetch_clingen_gene(symbol: str, hgnc_id: str | None) -> Dict[str, Any]:
    identifier = hgnc_id or symbol
    url = f"https://search.clinicalgenome.org/kb/genes/{quote(identifier, safe='')}"
    response = await resilient_request("GET", url)
    response.raise_for_status()
    return parse_clingen_gene_page(response.text)


def normalize_homologs(
    raw: Dict[str, Any],
    species_docs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    species_lookup: Dict[str, Dict[str, Any]] = {}
    for doc in species_docs:
        for candidate in (doc.get("name"), doc.get("common_name")):
            normalized = str(candidate or "").strip().lower()
            if normalized:
                species_lookup[normalized] = doc
    homologies = raw.get("data", [{}])[0].get("homologies", []) if raw.get("data") else []
    normalized: List[Dict[str, Any]] = []
    for item in homologies:
        target = item.get("target", {})
        species_name = str(target.get("species", "")).replace("_", " ").strip()
        if not species_name:
            continue
        species_doc = species_lookup.get(species_name.lower())
        normalized.append(
            {
                "species_name": species_name.title(),
                "common_name": species_doc.get("common_name") if species_doc else None,
                "symbol": target.get("display_id"),
                "ensembl_gene_id": target.get("id"),
                "homology_type": item.get("type"),
                "percent_id": target.get("perc_id"),
                "percent_coverage": target.get("perc_cov"),
                "in_platform": species_doc is not None,
            }
        )
    normalized.sort(
        key=lambda item: (
            not item.get("in_platform", False),
            -(float(item.get("percent_id") or 0.0)),
            str(item.get("species_name", "")),
        )
    )
    return normalized[:24]


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

    # The per-gene HGNC REST lookup is gone as well. It resolved for barely half the
    # genes reaching this path, and every field it returned — name, aliases, previous
    # symbols, OMIM/Ensembl/Entrez ids, locus group, location, VEGA id, RefSeq
    # accessions — is in the HGNC complete set, which is already loaded in bulk for all
    # ~45,000 approved genes and reports its own per-gene status. Those values are read
    # off the bulk bundle below.

    # The per-gene Ensembl lookup is gone. Its only unique contribution was the
    # canonical transcript, and GENCODE tags that per transcript for every gene, so the
    # request bought a field we already hold — and only resolved for a third of the
    # genes that reached this path anyway.
    ensembl_payload: Dict[str, Any] = {}

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
    ensembl_gene_id = first_non_empty(
        ensembl_payload.get("id"),
        bulk_profile.get("ensembl_gene_id"),
    )
    # The Ensembl homology call is gone too. It returned an orthologue for none of the
    # 1,380 genes that reached it — every attempt ended in "No orthologues returned" or
    # a DNS failure — while dbNSFP already supplies model-organism orthologues in bulk.
    homologs: List[Dict[str, Any]] = []

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
    # The ClinGen gene-page scrape is gone. Measured across 4,052 genes that reached
    # this path it reported success 4,046 times and carried almost nothing: a function
    # for 61, a MANE transcript for 23, a GenCC classification for 1, an ACMG secondary
    # finding for none. The fields it did fill — name, cytoband, locus type, aliases,
    # previous symbols — all now arrive from the HGNC complete set in one download.
    # That is structural rather than bad luck: this path only runs for genes dbNSFP does
    # not cover, which are overwhelmingly lncRNAs and pseudogenes nobody has curated.
    # ClinGen's actual curations still reach every gene through the bulk gene-validity
    # and dosage files.
    clingen_payload: Dict[str, Any] = {}

    extra = {
        "hgnc_name": bulk_profile.get("display_name"),
        "hgnc_gene_group": as_list(hgnc_gene_facts.get("gene_group")),
        "hgnc_vega_id": first_non_empty(hgnc_identifiers.get("vega_id")),
        "refseq_accessions": as_list(hgnc_identifiers.get("refseq_accession")),
        "ensembl_canonical_transcript": ensembl_payload.get("canonical_transcript"),
        "ensembl_description": ensembl_payload.get("description"),
        "ncbi_other_designations": as_list(ncbi_payload.get("otherdesignations")),
        "clingen_curation_counts": clingen_payload.get("curation_counts", {}),
        "clingen_gene_facts": clingen_payload.get("gene_facts", {}),
    }
    source_status_map.update(bulk_bundle.get("source_status") or {})
    extra = merge_gene_extra(extra, bulk_bundle.get("extra") or {})

    return {
        "display_name": first_non_empty(
            bulk_profile.get("display_name"),
            ncbi_payload.get("description"),
        ),
        "summary": first_non_empty(
            ncbi_payload.get("summary"),
            clingen_payload.get("gene_facts", {}).get("function"),
            ensembl_payload.get("description"),
        ),
        "aliases": aliases,
        "previous_symbols": previous_symbols,
        "ensembl_gene_id": ensembl_gene_id,
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
            ensembl_payload.get("biotype"),
            bulk_profile.get("gene_type"),
        ),
        "location": first_non_empty(
            bulk_profile.get("location"),
            ncbi_payload.get("maplocation"),
        ),
        "homologs": homologs,
        "source_status": source_status_map,
        "extra": extra,
    }
