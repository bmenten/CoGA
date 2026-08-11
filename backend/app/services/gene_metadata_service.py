from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import uuid_list_bindparam, uuid_values
from ..schemas import (
    GeneAssemblyLocationOut,
    GeneExternalLinkOut,
    GeneHomologOut,
    GeneInfoSourceStatusOut,
    GeneMonarchAssociationOut,
    GenePanelMembershipOut,
    GeneProfileOut,
    GeneSearchResultOut,
    GeneTranscriptOut,
    MonarchPhenotypeMatchOut,
)
from .data_scope import is_primary_chromosome
from .metadata_service import CurrentUser, get_accessible_family_mapping
from .monarch_ingest import (
    family_observed_phenotype_closure,
    list_monarch_gene_disease,
    summarize_disease_phenotypes,
)


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


def _transcript_relevance_flags(doc: dict[str, Any]) -> dict[str, bool]:
    """Read MANE / Ensembl-canonical status off the annotation's own tags.

    GENCODE states this per transcript (``MANE_Select``, ``MANE_Plus_Clinical``,
    ``Ensembl_canonical``), so it is known for every gene without a network call. The
    ``mane_select`` boolean is written by the importer; the rest are read from the tag
    list, which also covers annotations that carry the tags without the flag.
    """
    extra = doc.get("extra") or {}
    tags = {str(tag) for tag in (extra.get("tags") or [])}
    return {
        "mane_select": bool(extra.get("mane_select")) or "MANE_Select" in tags,
        "mane_plus_clinical": bool(extra.get("mane_plus_clinical"))
        or "MANE_Plus_Clinical" in tags,
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
    if assembly_name == "GRCh38":
        return "hg38"
    if assembly_name in {"GRCh37", "hg19"}:
        return "hg19"
    if assembly_name == "GRCm39":
        return "mm39"
    if assembly_name == "GRCm38":
        return "mm10"
    if assembly_name.startswith("T2T-CHM13"):
        return "hs1"
    if assembly_name == "EquCab3.0":
        return "equCab3"
    return None


def _gnomad_dataset_name(assembly_name: str) -> str | None:
    if assembly_name == "GRCh38":
        return "gnomad_r4"
    if assembly_name in {"GRCh37", "hg19"}:
        return "gnomad_r2_1"
    return None


def _assembly_priority(assembly_name: str) -> tuple[int, str]:
    if assembly_name == "GRCh38":
        return (0, assembly_name)
    if assembly_name in {"T2T-CHM13", "T2T-CHM13v2.0"} or assembly_name.startswith("T2T-CHM13"):
        return (1, assembly_name)
    if assembly_name in {"GRCh37", "hg19"}:
        return (2, assembly_name)
    return (9, assembly_name)


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


def _require_uuid_or_none(value: str | None, detail: str) -> str | None:
    if value is None:
        return None
    try:
        UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detail) from exc
    return value


def _ensure_project_access(project_id: str, user: CurrentUser) -> None:
    if user.role == "admin":
        return
    if project_id not in set(user.metadata_project_ids):
        raise HTTPException(status_code=403, detail="Not authorized")


async def _get_human_context(session: AsyncSession) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    species_result = await session.execute(
        text(
            """
            SELECT id::text AS id, name, common_name
            FROM species
            WHERE name = 'Homo sapiens' OR lower(common_name) = 'human'
            ORDER BY CASE WHEN name = 'Homo sapiens' THEN 0 ELSE 1 END
            LIMIT 1
            """
        )
    )
    species_row = species_result.mappings().first()
    if species_row is None:
        raise HTTPException(status_code=404, detail="Human reference species not found")

    assemblies_result = await session.execute(
        text(
            """
            SELECT id::text AS id, assembly_name, version
            FROM assemblies
            WHERE species_id = CAST(:species_id AS uuid)
            """
        ),
        {"species_id": species_row["id"]},
    )
    assemblies = [dict(row) for row in assemblies_result.mappings().all()]
    if not assemblies:
        raise HTTPException(status_code=404, detail="No human assemblies found")
    assemblies.sort(key=lambda row: _assembly_priority(str(row["assembly_name"])))
    return dict(species_row), assemblies


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
                -- RefSeq accessions the refGene-derived table used to carry.
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
    species_row, assemblies = await _get_human_context(session)
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

    _ = species_row
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


async def build_gene_profile(
    session: AsyncSession,
    *,
    symbol: str,
    assembly_id: str | None,
    family_id: str | None,
    project_id: str | None,
    user: CurrentUser,
) -> GeneProfileOut:
    requested_assembly_id = _require_uuid_or_none(assembly_id, "Assembly id is invalid")
    requested_project_id = _require_uuid_or_none(project_id, "Project id is invalid")
    family_row: dict[str, Any] | None = None
    if family_id:
        family_row = await get_accessible_family_mapping(session, family_id, user)

    project_row: dict[str, Any] | None = None
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
        _ensure_project_access(requested_project_id, user)
        if family_row is not None and requested_project_id not in (family_row.get("project_ids") or []):
            raise HTTPException(status_code=400, detail="Project is not linked to this family")
    elif family_row is not None:
        family_project_ids = family_row.get("project_ids") or []
        if family_project_ids:
            project_result = await session.execute(
                text(
                    """
                    SELECT id::text AS id, assembly_id::text AS assembly_id
                    FROM projects
                    WHERE id = CAST(:project_id AS uuid)
                    """
                ),
                {"project_id": family_project_ids[0]},
            )
            project_row = project_result.mappings().first()

    preferred_assembly_id = project_row["assembly_id"] if project_row is not None else None
    species_row, assemblies = await _get_human_context(session)
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

    panel_result = await session.execute(
        text(
            """
            SELECT
                p.id::text AS panel_id,
                p.name,
                COUNT(gpg.gene_symbol) AS gene_count
            FROM gene_panels p
            JOIN gene_panel_genes gpg ON gpg.panel_id = p.id
            WHERE upper(gpg.gene_symbol) = :symbol
            GROUP BY p.id, p.name
            ORDER BY lower(p.name)
            """
        ),
        {"symbol": str(primary["hgnc_symbol"]).upper()},
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
                MonarchPhenotypeMatchOut(hpo_id=match["hpo_id"], label=match["label"])
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
        gene_annotation_source=(annotation_row or {}).get("source"),
        gene_annotation_imported_at=(annotation_row or {}).get("performed_at"),
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
