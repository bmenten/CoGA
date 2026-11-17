from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, cast
from urllib.parse import quote, urlencode

from fastapi import HTTPException
from sqlalchemy import RowMapping, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import require_uuid, uuid_list_bindparam, uuid_values
from ..schemas import (
    GeneAcmgSecondaryFindingOut,
    GeneAssemblyLocationOut,
    GeneDiseaseLinksOut,
    GeneDiseaseMonarchOut,
    GeneDiseaseOmimOut,
    GeneDosageSensitivityOut,
    GeneExternalLinkOut,
    GeneHomologOut,
    GeneInfoSourceStatusOut,
    GeneInheritanceOut,
    GeneMonarchAssociationOut,
    GenePanelMembershipOut,
    GeneProfileOut,
    GeneSearchResultOut,
    GeneTranscriptOut,
    PhenotypeTermRefOut,
)
from ..schemas.genes import InheritanceMode
from .data_scope import is_primary_chromosome
from .ensembl_gene_ids import ensembl_gene_id
from .gene_info_jobs_pg import _assembly_priority, _get_human_context
from .metadata_service import get_accessible_family_mapping
from .access_control import CurrentUser, RecordNotVisible, user_can_access_metadata_projects
from .monarch_ingest import (
    family_observed_phenotype_closure,
    list_monarch_gene_disease,
    summarize_disease_phenotypes,
)
from .sex_chromosomes import grc_build, is_t2t_chm13
from .stored_values import json_object


def _gene_symbol_candidates(symbol: str) -> list[str]:
    cleaned = symbol.strip()
    if not cleaned:
        return []
    candidates = [cleaned, cleaned.upper(), cleaned.capitalize()]
    unique: list[str] = []
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def _transcript_id_from_doc(doc: dict[str, Any]) -> str:
    extra = doc.get("extra") or {}
    return str(extra.get("transcript_id") or doc.get("gene_id") or doc.get("hgnc_symbol"))


_REFSEQ_TRANSCRIPT_PREFIXES = ("NM_", "NR_", "XM_", "XR_")


def _transcript_refseq_accessions(doc: dict[str, Any]) -> list[str]:
    """RefSeq transcript accessions for this transcript, protein accessions excluded.

    GENCODE's RefSeq mapping lists both sides of the pair — ``NM_007294.4`` alongside
    ``NP_009225.1`` — and a transcript table should offer only the transcript.
    """
    extra = doc.get("extra") or {}
    seen: list[str] = []
    for accession in extra.get("refseq_accessions") or []:
        value = str(accession or "").strip()
        if value.upper().startswith(_REFSEQ_TRANSCRIPT_PREFIXES) and value not in seen:
            seen.append(value)
    return seen


def _transcript_relevance_flags(doc: dict[str, Any]) -> dict[str, bool]:
    """Read MANE / Ensembl-canonical status off the annotation's own tags.

    GENCODE states this per transcript in its tag list (``MANE_Select``,
    ``MANE_Plus_Clinical``, ``Ensembl_canonical``), which the importer keeps on the row
    (``extra.tags``), so it is known for every gene without a network call.
    """
    extra = doc.get("extra") or {}
    tags = {str(tag) for tag in (extra.get("tags") or [])}
    return {
        "mane_select": "MANE_Select" in tags,
        "mane_plus_clinical": "MANE_Plus_Clinical" in tags,
        "ensembl_canonical": "Ensembl_canonical" in tags,
    }


def _transcript_count_from_docs(docs: list[dict[str, Any]]) -> int:
    return len({_transcript_id_from_doc(doc) for doc in docs})


def _pick_primary_gene_doc(docs: list[dict[str, Any]]) -> dict[str, Any]:
    if not docs:
        raise HTTPException(status_code=404, detail="Gene not found")
    # A gene present on both the primary chromosome and an ALT/scaffold contig has
    # one row per locus; always resolve to the effective chromosome first so the
    # chromosome view / search never jumps to an alt contig.
    return max(
        docs,
        key=lambda doc: (
            is_primary_chromosome(str(doc.get("chr", ""))),
            int(doc.get("end", 0)) - int(doc.get("start", 0)),
            len(doc.get("exons", [])),
            _transcript_id_from_doc(doc),
        ),
    )


def _gene_locus(doc: dict[str, Any]) -> str:
    chrom = str(doc.get("chr", ""))
    display = chrom if chrom.startswith("chr") else f"chr{chrom}"
    return f"{display}:{int(doc.get('start', 0)):,}-{int(doc.get('end', 0)):,}"


def _ucsc_db_name(assembly_name: str) -> str | None:
    # GRCh38 and GRCh37 under any name grc_build reads (hg38, GRCh38.p14, hg19, ...), the rule
    # the PARs are looked up by; T2T-CHM13 under any of its names.
    build = grc_build(assembly_name)
    if build == "GRCh38":
        return "hg38"
    if build == "GRCh37":
        return "hg19"
    if assembly_name == "GRCm39":
        return "mm39"
    if assembly_name == "GRCm38":
        return "mm10"
    if is_t2t_chm13(assembly_name):
        return "hs1"
    if assembly_name == "EquCab3.0":
        return "equCab3"
    return None


def _gnomad_dataset_name(assembly_name: str) -> str | None:
    build = grc_build(assembly_name)
    if build == "GRCh38":
        return "gnomad_r4"
    if build == "GRCh37":
        return "gnomad_r2_1"
    return None


def _first_identifier(extra: dict[str, Any], *paths: tuple[str, str]) -> str | None:
    """First non-empty identifier from ``extra[block][key]``, scalar or list.

    dbNSFP and HGNC both carry the same identifiers under their own block, and either
    may be absent for a given gene, so the caller lists the places to look in order of
    preference and takes whatever is actually there.
    """
    for block, key in paths:
        value = ((extra.get(block) or {}) if isinstance(extra.get(block), dict) else {}).get(key)
        if isinstance(value, list):
            value = next((entry for entry in value if str(entry or "").strip()), None)
        text_value = str(value or "").strip()
        if text_value:
            return text_value
    return None


def _build_external_links(
    *,
    symbol: str,
    gene_doc: dict[str, Any],
    assembly_name: str,
    ensembl_gene_id: str | None,
    ncbi_gene_id: str | None,
    hgnc_id: str | None,
    omim_gene_id: str | None,
    extra: dict[str, Any] | None = None,
) -> list[GeneExternalLinkOut]:
    chrom = str(gene_doc.get("chr", ""))
    chrom_label = chrom if chrom.startswith("chr") else f"chr{chrom}"
    locus = f"{chrom_label}:{int(gene_doc.get('start', 0))}-{int(gene_doc.get('end', 0))}"
    ucsc_db = _ucsc_db_name(assembly_name)
    gnomad_dataset = _gnomad_dataset_name(assembly_name)
    pubmed_query = quote(f"{symbol}[Title/Abstract] OR {symbol}[MeSH Terms]")

    # dbNSFP and HGNC both hand us exact accessions for this gene. Where one exists the
    # link resolves to the record itself rather than running a symbol search and hoping
    # the first hit is the right gene.
    extra = extra or {}
    uniprot_accession = _first_identifier(
        extra,
        ("dbnsfp_identifiers", "uniprot_accessions"),
        ("hgnc_identifiers", "uniprot_ids"),
    )
    ccds_id = _first_identifier(
        extra, ("dbnsfp_identifiers", "ccds_ids"), ("hgnc_identifiers", "ccds_id")
    )
    ucsc_id = _first_identifier(
        extra, ("dbnsfp_identifiers", "ucsc_ids"), ("hgnc_identifiers", "ucsc_id")
    )

    links = [
        GeneExternalLinkOut(
            label="Ensembl",
            href=(
                f"https://www.ensembl.org/id/{ensembl_gene_id}"
                if ensembl_gene_id
                else f"https://www.ensembl.org/Multi/Search/Results?q={quote(symbol)}"
            ),
        ),
        GeneExternalLinkOut(
            label="NCBI Gene",
            href=(
                f"https://www.ncbi.nlm.nih.gov/gene/{ncbi_gene_id}"
                if ncbi_gene_id
                else f"https://www.ncbi.nlm.nih.gov/gene/?term={quote(symbol)}%5Bsym%5D"
            ),
        ),
        GeneExternalLinkOut(
            label="OMIM",
            href=(
                f"https://www.omim.org/entry/{omim_gene_id}"
                if omim_gene_id
                else f"https://www.omim.org/search?search={quote(symbol)}"
            ),
        ),
        GeneExternalLinkOut(label="PubMed", href=f"https://pubmed.ncbi.nlm.nih.gov/?term={pubmed_query}"),
        GeneExternalLinkOut(
            label="ClinGen",
            href=(
                f"https://search.clinicalgenome.org/kb/genes/{quote(hgnc_id)}"
                if hgnc_id
                else f"https://search.clinicalgenome.org/kb/genes/{quote(symbol)}"
            ),
        ),
        GeneExternalLinkOut(
            label="GenCC",
            href=(
                f"https://search.thegencc.org/genes/{quote(hgnc_id)}"
                if hgnc_id
                else f"https://search.thegencc.org/search?search={quote(symbol)}"
            ),
        ),
        GeneExternalLinkOut(label="DECIPHER", href=f"https://www.deciphergenomics.org/gene/{quote(symbol)}"),
        GeneExternalLinkOut(label="GeneCards", href=f"https://www.genecards.org/cgi-bin/carddisp.pl?gene={quote(symbol)}"),
        GeneExternalLinkOut(
            label="Open Targets",
            href=(
                f"https://platform.opentargets.org/target/{quote(ensembl_gene_id)}"
                if ensembl_gene_id
                else f"https://platform.opentargets.org/search?query={quote(symbol)}"
            ),
        ),
        GeneExternalLinkOut(label="GTEx", href=f"https://gtexportal.org/home/gene/{quote(symbol)}"),
        GeneExternalLinkOut(label="ClinVar", href=f"https://www.ncbi.nlm.nih.gov/clinvar/?term={quote(symbol)}%5Bgene%5D"),
        GeneExternalLinkOut(
            label="UniProt",
            href=(
                f"https://www.uniprot.org/uniprotkb/{quote(uniprot_accession)}/entry"
                if uniprot_accession
                else f"https://www.uniprot.org/uniprotkb?query=gene:{quote(symbol)}"
            ),
        ),
        GeneExternalLinkOut(
            label="GeneReviews",
            href=f"https://www.ncbi.nlm.nih.gov/books/?term={quote(symbol)}%5Bbook%5D%20AND%20GeneReviews%5Bbook%5D",
        ),
        GeneExternalLinkOut(label="PanelApp", href=f"https://panelapp.genomicsengland.co.uk/entities/{quote(symbol)}"),
    ]
    if ccds_id:
        links.append(
            GeneExternalLinkOut(
                label="CCDS",
                href=(
                    "https://www.ncbi.nlm.nih.gov/CCDS/CcdsBrowse.cgi?"
                    f"{urlencode({'REQUEST': 'CCDS', 'DATA': ccds_id})}"
                ),
            )
        )
    if ucsc_db:
        # The gene-model page when the UCSC id is known, otherwise the locus in the browser.
        links.append(
            GeneExternalLinkOut(
                label="UCSC",
                href=(
                    "https://genome.ucsc.edu/cgi-bin/hgGene?"
                    f"{urlencode({'db': ucsc_db, 'hgg_gene': ucsc_id})}"
                    if ucsc_id
                    else "https://genome.ucsc.edu/cgi-bin/hgTracks?"
                    f"{urlencode({'db': ucsc_db, 'position': locus})}"
                ),
            )
        )
    if ensembl_gene_id and gnomad_dataset:
        links.append(
            GeneExternalLinkOut(
                label="gnomAD",
                href=f"https://gnomad.broadinstitute.org/gene/{quote(ensembl_gene_id)}?dataset={gnomad_dataset}",
            )
        )
    return links


async def _lookup_gene_documents(
    session: AsyncSession,
    *,
    symbol: str,
    assembly_ids: list[str],
) -> list[dict[str, Any]]:
    candidates = [candidate.lower() for candidate in _gene_symbol_candidates(symbol)]
    if not candidates or not assembly_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT
                g.id::text AS id,
                g.assembly_id::text AS assembly_id,
                g.gene_id,
                g.hgnc_symbol,
                g.chr,
                g.start,
                g."end" AS end,
                g.exons,
                g.strand,
                g.biotype,
                g.description,
                g.source,
                g.extra
            FROM genes g
            WHERE g.assembly_id IN :assembly_ids
              AND (
                lower(g.hgnc_symbol) IN :candidates
                OR lower(g.gene_id) IN :candidates
                OR lower(COALESCE(g.extra->>'transcript_id', '')) IN :candidates
                -- GENCODE keys gene_id on a versioned Ensembl transcript accession, so
                -- also match the unversioned transcript, the Ensembl gene, and the
                -- RefSeq accessions the GENCODE import records for the transcript.
                OR lower(COALESCE(g.extra->>'ensembl_transcript_id', '')) IN :candidates
                OR lower(COALESCE(g.extra->>'ensembl_gene_id', '')) IN :candidates
                OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(
                        COALESCE(g.extra->'refseq_accessions', '[]'::jsonb)
                    ) AS accession
                    WHERE lower(accession) IN :candidates
                       OR lower(split_part(accession, '.', 1)) IN :candidates
                )
              )
            """
        ).bindparams(
            uuid_list_bindparam("assembly_ids"),
            bindparam("candidates", expanding=True),
        ),
        {"assembly_ids": uuid_values(assembly_ids), "candidates": candidates},
    )
    return [dict(row) for row in result.mappings().all()]


async def search_genes(
    session: AsyncSession,
    *,
    query: str,
) -> list[GeneSearchResultOut]:
    term = query.strip()
    if len(term) < 2:
        return []
    assemblies = (await _get_human_context(session)).assemblies
    assembly_ids = [assembly["id"] for assembly in assemblies]
    result = await session.execute(
        text(
            """
            SELECT
                g.hgnc_symbol,
                g.gene_id,
                g.chr,
                g.start,
                g."end" AS end,
                g.exons,
                g.extra,
                g.assembly_id::text AS assembly_id
            FROM genes g
            WHERE g.assembly_id IN :assembly_ids
              AND upper(g.hgnc_symbol) LIKE :prefix
            ORDER BY g.hgnc_symbol, g.start, g."end"
            LIMIT 1200
            """
        ).bindparams(uuid_list_bindparam("assembly_ids")),
        {"assembly_ids": uuid_values(assembly_ids), "prefix": f"{term.upper()}%"},
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in result.mappings().all():
        grouped[str(row["hgnc_symbol"])].append(dict(row))

    payload: list[GeneSearchResultOut] = []
    for symbol, docs in sorted(grouped.items())[:20]:
        primary = _pick_primary_gene_doc(docs)
        payload.append(
            GeneSearchResultOut(
                symbol=symbol,
                gene_id=str(primary.get("gene_id")),
                chr=str(primary.get("chr")),
                start=int(primary.get("start", 0)),
                end=int(primary.get("end", 0)),
                transcript_count=_transcript_count_from_docs(docs),
                assembly_count=len({doc["assembly_id"] for doc in docs}),
            )
        )
    return payload


# How many genes one disease-link lookup may ask for: a result page shows at most 100 cards.
MAX_DISEASE_LINK_SYMBOLS = 200

_OMIM_ID_PATTERN = re.compile(r"\d{5,}")


def _omim_disease_entries(value: Any) -> list[GeneDiseaseOmimOut]:
    """The OMIM diseases the gene reference holds, each with a label, in their stored order.

    The link is built from the OMIM number, as the Monarch link is from the MONDO id.
    """
    if not isinstance(value, list):
        return []
    entries: list[GeneDiseaseOmimOut] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "").strip()
        if not label:
            continue
        omim_id = str(entry.get("omim_id") or "").strip() or None
        href = (
            f"https://www.omim.org/entry/{omim_id}"
            if omim_id is not None and _OMIM_ID_PATTERN.fullmatch(omim_id)
            else None
        )
        entries.append(GeneDiseaseOmimOut(label=label, omim_id=omim_id, href=href))
    return entries


# The modes a card names, in the order it names them, with their words.
_INHERITANCE_LABELS: dict[InheritanceMode, str] = {
    "AD": "Autosomal dominant",
    "AR": "Autosomal recessive",
    "SD": "Semidominant",
    "XLD": "X-linked dominant",
    "XLR": "X-linked recessive",
    "XL": "X-linked",
    "MT": "Mitochondrial",
    "YL": "Y-linked",
}

# A gene–disease relationship counts for its mode only at one of these classifications,
# strongest first: a Limited, Disputed, Refuted or animal-model-only relationship, or one
# without a classification, does not.
_COUNTED_CLASSIFICATIONS = ("definitive", "strong", "moderate", "supportive")


def _inheritance_mode(value: Any) -> InheritanceMode | None:
    """The mode a curation's inheritance names: ClinGen writes AD/AR/XL/SD/MT, GenCC words
    ("Autosomal recessive", "X-linked"). An unknown, undetermined or other mode is none."""
    words = " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())
    code = words.upper()
    if code in _INHERITANCE_LABELS:
        return cast(InheritanceMode, code)
    if "semidominant" in words or "semi dominant" in words:
        return "SD"
    for phrase, mode in (
        ("autosomal dominant", "AD"),
        ("autosomal recessive", "AR"),
        ("x linked dominant", "XLD"),
        ("x linked recessive", "XLR"),
        ("x linked", "XL"),
        ("mitochondrial", "MT"),
        ("y linked", "YL"),
    ):
        if phrase in words:
            return cast(InheritanceMode, mode)
    return None


def _inheritance_entries(gencc: Any, clingen_validity: Any) -> list[GeneInheritanceOut]:
    """The modes of inheritance GenCC's and ClinGen's gene–disease curations name for the gene.

    One entry per mode, in the card's order, each naming the curations that state it (source
    and classification, strongest first).
    """
    evidence: dict[InheritanceMode, set[tuple[int, str]]] = defaultdict(set)
    for source, assertions, mode_key, class_key in (
        ("GenCC", gencc, "moi_title", "classification_title"),
        ("ClinGen", clingen_validity, "moi", "classification"),
    ):
        if not isinstance(assertions, list):
            continue
        for assertion in assertions:
            if not isinstance(assertion, dict):
                continue
            mode = _inheritance_mode(assertion.get(mode_key))
            classification = " ".join(str(assertion.get(class_key) or "").split())
            if mode is None or classification.lower() not in _COUNTED_CLASSIFICATIONS:
                continue
            rank = _COUNTED_CLASSIFICATIONS.index(classification.lower())
            evidence[mode].add((rank, f"{source} {classification.capitalize()}"))
    return [
        GeneInheritanceOut(
            mode=mode,
            label=label,
            evidence=[text for _rank, text in sorted(evidence[mode])],
        )
        for mode, label in _INHERITANCE_LABELS.items()
        if evidence.get(mode)
    ]


# ClinGen's dosage scores by the words its curations use, the more specific first.
_DOSAGE_SCORE_WORDS = (
    ("unlikely", 40),
    ("recessive", 30),
    ("no evidence", 0),
    ("little evidence", 1),
    ("emerging evidence", 2),
    ("some evidence", 2),
    ("sufficient evidence", 3),
)
_DOSAGE_SCORES = {0, 1, 2, 3, 30, 40}
_WEB_URL_PATTERN = re.compile(r"https?://\S+")


def _dosage_score(value: Any) -> int | None:
    """ClinGen's score for a curation, written as the score ("3") or in its words."""
    words = " ".join(str(value or "").lower().split())
    if not words:
        return None
    if words.isdigit():
        return int(words) if int(words) in _DOSAGE_SCORES else None
    return next((score for phrase, score in _DOSAGE_SCORE_WORDS if phrase in words), None)


def _clean_text(value: Any) -> str | None:
    return " ".join(str(value or "").split()) or None


def _dosage_entry(value: Any) -> GeneDosageSensitivityOut | None:
    """ClinGen's dosage curation of the gene, from ClinGen's own file where the gene reference
    holds it (both curations, the report page), else dbNSFP's copy of its haploinsufficiency
    score. None when neither states a curation."""
    if not isinstance(value, list):
        return None
    entries = [entry for entry in value if isinstance(entry, dict)]
    clingen_file = [
        entry
        for entry in entries
        if any(key in entry for key in ("hgnc_id", "triplosensitivity", "online_report", "date"))
    ]
    for entry in clingen_file or entries:
        haploinsufficiency = _clean_text(entry.get("haploinsufficiency"))
        triplosensitivity = _clean_text(entry.get("triplosensitivity"))
        hi_score = _dosage_score(haploinsufficiency)
        ts_score = _dosage_score(triplosensitivity)
        # dbNSFP's copy writes the score, with ClinGen's words beside it.
        if haploinsufficiency is not None and haploinsufficiency.isdigit():
            haploinsufficiency = _clean_text(entry.get("description"))
        if hi_score is None and ts_score is None and haploinsufficiency is None and triplosensitivity is None:
            continue
        report = _clean_text(entry.get("online_report"))
        return GeneDosageSensitivityOut(
            haploinsufficiency_score=hi_score,
            haploinsufficiency=haploinsufficiency,
            triplosensitivity_score=ts_score,
            triplosensitivity=triplosensitivity,
            report_url=report if report is not None and _WEB_URL_PATTERN.fullmatch(report) else None,
        )
    return None


def _acmg_secondary_finding_entry(entry: Any, status: Any) -> GeneAcmgSecondaryFindingOut:
    """The gene's place on the ACMG SF list: its entry where the gene reference holds one, else
    what the reference's reading of the list for the gene says (``source_status.acmg_sf``): a
    list read without the gene is ``not_listed``, a list that could not be read ``unread``. A
    missing chip on the card must never stand for a list nobody read."""
    if isinstance(entry, dict):
        source_url = _clean_text(entry.get("source_url"))
        return GeneAcmgSecondaryFindingOut(
            status="listed",
            version=_clean_text(entry.get("version")),
            phenotype=_clean_text(entry.get("phenotype")),
            category=_clean_text(entry.get("category")),
            inheritance=_clean_text(entry.get("inheritance")),
            variants_to_report=_clean_text(entry.get("variants_to_report")),
            source_url=source_url if source_url is not None and _WEB_URL_PATTERN.fullmatch(source_url) else None,
        )
    read = status.get("status") if isinstance(status, dict) else None
    if read == "missing":
        return GeneAcmgSecondaryFindingOut(status="not_listed")
    if read in ("error", "success"):
        # "success" without the entry it writes is a reading the card cannot state either way.
        return GeneAcmgSecondaryFindingOut(status="unread")
    return GeneAcmgSecondaryFindingOut(status="not_consulted")


async def list_gene_disease_links(
    session: AsyncSession,
    *,
    symbols: list[str],
) -> list[GeneDiseaseLinksOut]:
    """The diseases each gene is linked to, in Monarch and in OMIM, for a page of variant cards.

    One entry per symbol asked, in the order asked and matched without regard to case; a
    symbol asked twice gets one entry. A gene with no link has empty lists. Monarch's links are
    read as the gene profile reads them (causal first, then by name). OMIM's come from the gene
    reference of the first human assembly that holds the gene, GRCh38 first, and so do the
    gene's modes of inheritance (GenCC, ClinGen gene–disease validity), its ClinGen dosage
    curation and its place on the ACMG SF list.
    """
    asked: dict[str, str] = {}
    for symbol in symbols:
        cleaned = symbol.strip()
        if cleaned and cleaned.upper() not in asked:
            asked[cleaned.upper()] = cleaned
    if not asked:
        return []
    keys = list(asked)

    monarch_result = await session.execute(
        text(
            """
            SELECT upper(gene_symbol) AS symbol_key, mondo_id, disease_label, sources, causal
            FROM monarch_gene_disease
            WHERE upper(gene_symbol) = ANY(CAST(:symbols AS text[]))
              AND coalesce(disease_label, '') <> ''
            ORDER BY upper(gene_symbol), causal DESC, lower(disease_label), mondo_id
            """
        ),
        {"symbols": keys},
    )
    monarch: dict[str, list[GeneDiseaseMonarchOut]] = defaultdict(list)
    for row in monarch_result.mappings().all():
        monarch[str(row["symbol_key"])].append(
            GeneDiseaseMonarchOut(
                mondo_id=str(row["mondo_id"]),
                disease_label=str(row["disease_label"]),
                causal=bool(row["causal"]),
                sources=[str(source) for source in row.get("sources") or []],
                monarch_url=f"https://monarchinitiative.org/{row['mondo_id']}",
            )
        )

    reference_result = await session.execute(
        text(
            """
            SELECT
                upper(g.hgnc_symbol) AS symbol_key,
                a.assembly_name,
                g.extra->'omim_diseases' AS omim_diseases,
                g.extra->'gencc_assertions' AS gencc_assertions,
                g.extra->'clingen_validity_assertions' AS clingen_validity_assertions,
                g.extra->'clingen_dosage_assertions' AS clingen_dosage_assertions,
                g.extra->'acmg_secondary_finding' AS acmg_secondary_finding,
                g.source_status->'acmg_sf' AS acmg_sf_status
            FROM gene_info g
            JOIN assemblies a ON a.id = g.assembly_id
            WHERE upper(g.hgnc_symbol) = ANY(CAST(:symbols AS text[]))
            """
        ),
        {"symbols": keys},
    )
    reference_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in reference_result.mappings().all():
        reference_rows[str(row["symbol_key"])].append(dict(row))
    reference: dict[str, dict[str, Any]] = {
        key: min(rows, key=lambda row: _assembly_priority(str(row["assembly_name"])))
        for key, rows in reference_rows.items()
    }

    return [
        GeneDiseaseLinksOut(
            symbol=symbol,
            monarch=monarch.get(key, []),
            omim=_omim_disease_entries(reference.get(key, {}).get("omim_diseases")),
            inheritance=_inheritance_entries(
                reference.get(key, {}).get("gencc_assertions"),
                reference.get(key, {}).get("clingen_validity_assertions"),
            ),
            dosage=_dosage_entry(reference.get(key, {}).get("clingen_dosage_assertions")),
            acmg_secondary_finding=_acmg_secondary_finding_entry(
                reference.get(key, {}).get("acmg_secondary_finding"),
                reference.get(key, {}).get("acmg_sf_status"),
            ),
        )
        for key, symbol in asked.items()
    ]


async def build_gene_profile(
    session: AsyncSession,
    *,
    symbol: str,
    assembly_id: str | None,
    family_id: str | None,
    project_id: str | None,
    user: CurrentUser,
) -> GeneProfileOut:
    requested_assembly_id = None if assembly_id is None else require_uuid(assembly_id, "Assembly id is invalid")
    requested_project_id = None if project_id is None else require_uuid(project_id, "Project id is invalid")
    family_row: dict[str, Any] | None = None
    if family_id:
        family_row = await get_accessible_family_mapping(session, family_id, user)

    project_row: RowMapping | None = None
    if requested_project_id is not None:
        project_result = await session.execute(
            text(
                """
                SELECT id::text AS id, assembly_id::text AS assembly_id
                FROM projects
                WHERE id = CAST(:project_id AS uuid)
                """
            ),
            {"project_id": requested_project_id},
        )
        project_row = project_result.mappings().first()
        if project_row is None:
            raise HTTPException(status_code=404, detail="Project not found")
        # A project outside the user's projects answers exactly like an unknown one (REQ-SEC-001).
        if not user_can_access_metadata_projects([project_row["id"]], user):
            raise RecordNotVisible("project", "Project not found")
        if family_row is not None and requested_project_id not in (family_row.get("project_ids") or []):
            raise HTTPException(status_code=400, detail="Project is not linked to this family")
    elif family_row is not None:
        # The first of the family's projects the caller may see: another of its projects tells
        # them nothing, not even its assembly.
        visible_project_ids = [
            project
            for project in family_row.get("project_ids") or []
            if user_can_access_metadata_projects([project], user)
        ]
        if visible_project_ids:
            project_result = await session.execute(
                text(
                    """
                    SELECT id::text AS id, assembly_id::text AS assembly_id
                    FROM projects
                    WHERE id = CAST(:project_id AS uuid)
                    """
                ),
                {"project_id": visible_project_ids[0]},
            )
            project_row = project_result.mappings().first()

    preferred_assembly_id = project_row["assembly_id"] if project_row is not None else None
    human = await _get_human_context(session)
    species_row, assemblies = human.species, human.assemblies
    human_assembly_ids = [assembly["id"] for assembly in assemblies]
    if preferred_assembly_id and preferred_assembly_id not in human_assembly_ids:
        preferred_assembly_id = None
    if requested_assembly_id and requested_assembly_id not in human_assembly_ids:
        requested_assembly_id = None

    gene_docs = await _lookup_gene_documents(
        session,
        symbol=symbol,
        assembly_ids=human_assembly_ids,
    )
    if not gene_docs:
        raise HTTPException(status_code=404, detail="Gene not found")

    grouped_docs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in gene_docs:
        grouped_docs[doc["assembly_id"]].append(doc)

    primary_assembly = None
    selected_assembly_id = requested_assembly_id or preferred_assembly_id
    if selected_assembly_id and selected_assembly_id in grouped_docs:
        primary_assembly = next(
            (assembly for assembly in assemblies if assembly["id"] == selected_assembly_id),
            None,
        )
    if primary_assembly is None:
        for assembly in assemblies:
            if assembly["id"] in grouped_docs:
                primary_assembly = assembly
                break
    if primary_assembly is None:
        raise HTTPException(status_code=404, detail="Gene not found")

    primary_docs = grouped_docs[primary_assembly["id"]]
    primary = _pick_primary_gene_doc(primary_docs)

    cached_result = await session.execute(
        text(
            """
            SELECT
                assembly_id::text AS assembly_id,
                hgnc_symbol,
                gene_id,
                display_name,
                summary,
                aliases,
                previous_symbols,
                ensembl_gene_id,
                ncbi_gene_id,
                hgnc_id,
                omim_gene_id,
                gene_type,
                location,
                homologs,
                source_status,
                extra,
                updated_at
            FROM gene_info
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND hgnc_symbol = :symbol
            LIMIT 1
            """
        ),
        {"assembly_id": primary_assembly["id"], "symbol": primary["hgnc_symbol"]},
    )
    cached_info = cached_result.mappings().first()
    if cached_info is None:
        fallback_result = await session.execute(
            text(
                """
                SELECT
                    assembly_id::text AS assembly_id,
                    hgnc_symbol,
                    gene_id,
                    display_name,
                    summary,
                    aliases,
                    previous_symbols,
                    ensembl_gene_id,
                    ncbi_gene_id,
                    hgnc_id,
                    omim_gene_id,
                    gene_type,
                    location,
                    homologs,
                    source_status,
                    extra,
                    updated_at
                FROM gene_info
                WHERE assembly_id IN :assembly_ids
                  AND hgnc_symbol = :symbol
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).bindparams(uuid_list_bindparam("assembly_ids")),
            {"assembly_ids": uuid_values(human_assembly_ids), "symbol": primary["hgnc_symbol"]},
        )
        cached_info = fallback_result.mappings().first()

    # A panel holds the gene by its symbol, or by its Ensembl gene ID (which carries the
    # symbol the gene reference gave it).
    gene_ensembl_ids = sorted(
        {
            gene_id
            for doc in gene_docs
            if (gene_id := ensembl_gene_id(json_object(doc.get("extra")).get("ensembl_gene_id")))
        }
    )
    panel_result = await session.execute(
        text(
            """
            SELECT
                p.id::text AS panel_id,
                p.name,
                COUNT(gpg.gene) AS gene_count
            FROM gene_panels p
            JOIN gene_panel_genes gpg ON gpg.panel_id = p.id
            WHERE upper(gpg.gene) = :symbol
               OR upper(gpg.symbol) = :symbol
               OR gpg.gene = ANY(CAST(:ensembl_gene_ids AS text[]))
            GROUP BY p.id, p.name
            ORDER BY lower(p.name)
            """
        ),
        {
            "symbol": str(primary["hgnc_symbol"]).upper(),
            "ensembl_gene_ids": gene_ensembl_ids,
        },
    )
    panels = [
        GenePanelMembershipOut(
            panel_id=row["panel_id"],
            name=row["name"],
            gene_count=int(row["gene_count"]),
        )
        for row in panel_result.mappings().all()
    ]

    transcripts = [
        GeneTranscriptOut(
            transcript_id=_transcript_id_from_doc(doc),
            start=int(doc.get("start", 0)),
            end=int(doc.get("end", 0)),
            exon_count=len(doc.get("exons", [])),
            strand=int(doc.get("strand", 0)),
            biotype=doc.get("biotype"),
            source=doc.get("source"),
            refseq_accessions=_transcript_refseq_accessions(doc),
            ccds_id=(str((doc.get("extra") or {}).get("ccds_id") or "").strip() or None),
            **_transcript_relevance_flags(doc),
        )
        for doc in sorted(
            primary_docs,
            key=lambda doc: (
                -(int(doc.get("end", 0)) - int(doc.get("start", 0))),
                -len(doc.get("exons", [])),
                _transcript_id_from_doc(doc),
            ),
        )
    ]

    assembly_locations = []
    for assembly in assemblies:
        docs_for_assembly = grouped_docs.get(assembly["id"])
        if not docs_for_assembly:
            continue
        primary_for_assembly = _pick_primary_gene_doc(docs_for_assembly)
        assembly_locations.append(
            GeneAssemblyLocationOut(
                assembly_id=assembly["id"],
                assembly_name=assembly["assembly_name"],
                assembly_version=assembly.get("version"),
                chr=str(primary_for_assembly["chr"]),
                start=int(primary_for_assembly["start"]),
                end=int(primary_for_assembly["end"]),
                transcript_count=_transcript_count_from_docs(docs_for_assembly),
                is_primary=assembly["id"] == primary_assembly["id"],
                is_family_context=preferred_assembly_id == assembly["id"],
            )
        )

    cached_mapping = dict(cached_info) if cached_info is not None else {}
    info_source_status = {
        key: GeneInfoSourceStatusOut(**value)
        for key, value in (cached_mapping.get("source_status") or {}).items()
    }
    external_links = _build_external_links(
        symbol=str(primary["hgnc_symbol"]),
        gene_doc=primary,
        assembly_name=str(primary_assembly["assembly_name"]),
        ensembl_gene_id=cached_mapping.get("ensembl_gene_id"),
        ncbi_gene_id=cached_mapping.get("ncbi_gene_id"),
        hgnc_id=cached_mapping.get("hgnc_id"),
        omim_gene_id=cached_mapping.get("omim_gene_id"),
        extra=dict(cached_mapping.get("extra") or {}),
    )

    monarch_rows = await list_monarch_gene_disease(
        session, symbol=str(primary["hgnc_symbol"])
    )
    observed_closure: set[str] | None = None
    if family_row is not None:
        observed_closure = await family_observed_phenotype_closure(
            session, family_uuid=str(family_row["id"])
        )
    phenotype_summary = await summarize_disease_phenotypes(
        session,
        mondo_ids=[row["mondo_id"] for row in monarch_rows],
        observed_closure=observed_closure,
    )
    monarch_associations = [
        GeneMonarchAssociationOut(
            mondo_id=row["mondo_id"],
            disease_label=row.get("disease_label"),
            predicate=row["predicate"],
            predicates=list(row.get("predicates") or []),
            sources=list(row.get("sources") or []),
            causal=bool(row.get("causal")),
            monarch_url=f"https://monarchinitiative.org/{row['mondo_id']}",
            phenotype_count=phenotype_summary[row["mondo_id"]]["phenotype_count"],
            matched_phenotypes=[
                PhenotypeTermRefOut(hpo_id=match["hpo_id"], label=match["label"])
                for match in phenotype_summary[row["mondo_id"]]["matched"]
            ],
        )
        for row in monarch_rows
    ]

    # The annotation behind the loci and transcripts is recorded per assembly at import
    # time, so it comes from the import log rather than the gene row.
    annotation_result = await session.execute(
        text(
            """
            SELECT source, performed_at
            FROM reference_dataset_imports
            WHERE assembly_id = CAST(:assembly_id AS uuid)
              AND dataset_type = 'genes'
            ORDER BY performed_at DESC
            LIMIT 1
            """
        ),
        {"assembly_id": primary_assembly["id"]},
    )
    annotation_row = annotation_result.mappings().first()

    return GeneProfileOut(
        assembly_id=str(primary_assembly["id"]),
        assembly_name=str(primary_assembly["assembly_name"]),
        assembly_version=primary_assembly.get("version"),
        gene_annotation_source=annotation_row.get("source") if annotation_row is not None else None,
        gene_annotation_imported_at=annotation_row.get("performed_at") if annotation_row is not None else None,
        species_name=str(species_row["name"]),
        symbol=str(primary["hgnc_symbol"]),
        gene_id=str(primary["gene_id"]),
        display_name=cached_mapping.get("display_name") or primary.get("description"),
        summary=cached_mapping.get("summary") or primary.get("description"),
        chr=str(primary["chr"]),
        start=int(primary["start"]),
        end=int(primary["end"]),
        strand=int(primary["strand"]),
        biotype=primary.get("biotype"),
        transcript_count=len(transcripts),
        transcripts=transcripts,
        aliases=list(cached_mapping.get("aliases") or []),
        previous_symbols=list(cached_mapping.get("previous_symbols") or []),
        ensembl_gene_id=cached_mapping.get("ensembl_gene_id"),
        ncbi_gene_id=cached_mapping.get("ncbi_gene_id"),
        hgnc_id=cached_mapping.get("hgnc_id"),
        omim_gene_id=cached_mapping.get("omim_gene_id"),
        gene_type=cached_mapping.get("gene_type") or primary.get("biotype"),
        location=cached_mapping.get("location") or _gene_locus(primary),
        assembly_locations=assembly_locations,
        homologs=[GeneHomologOut(**entry) for entry in (cached_mapping.get("homologs") or [])],
        panels=panels,
        family_counts=None,
        source_status=info_source_status,
        external_links=external_links,
        monarch_associations=monarch_associations,
        extra=dict(cached_mapping.get("extra") or {}),
        updated_at=cached_mapping.get("updated_at"),
    )
