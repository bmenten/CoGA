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
    GenePanelMembershipOut,
    GeneProfileOut,
    GeneSearchResultOut,
    GeneTranscriptOut,
)
from .metadata_service import CurrentUser, get_accessible_family_mapping


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


def _transcript_count_from_docs(docs: list[dict[str, Any]]) -> int:
    return len({_transcript_id_from_doc(doc) for doc in docs})


def _pick_primary_gene_doc(docs: list[dict[str, Any]]) -> dict[str, Any]:
    if not docs:
        raise HTTPException(status_code=404, detail="Gene not found")
    return max(
        docs,
        key=lambda doc: (
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


def _build_external_links(
    *,
    symbol: str,
    gene_doc: dict[str, Any],
    assembly_name: str,
    ensembl_gene_id: str | None,
    ncbi_gene_id: str | None,
    hgnc_id: str | None,
    omim_gene_id: str | None,
) -> list[GeneExternalLinkOut]:
    chrom = str(gene_doc.get("chr", ""))
    chrom_label = chrom if chrom.startswith("chr") else f"chr{chrom}"
    locus = f"{chrom_label}:{int(gene_doc.get('start', 0))}-{int(gene_doc.get('end', 0))}"
    ucsc_db = _ucsc_db_name(assembly_name)
    gnomad_dataset = _gnomad_dataset_name(assembly_name)
    pubmed_query = quote(f"{symbol}[Title/Abstract] OR {symbol}[MeSH Terms]")

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
        GeneExternalLinkOut(label="UniProt", href=f"https://www.uniprot.org/uniprotkb?query=gene:{quote(symbol)}"),
        GeneExternalLinkOut(
            label="GeneReviews",
            href=f"https://www.ncbi.nlm.nih.gov/books/?term={quote(symbol)}%5Bbook%5D%20AND%20GeneReviews%5Bbook%5D",
        ),
        GeneExternalLinkOut(label="PanelApp", href=f"https://panelapp.genomicsengland.co.uk/entities/{quote(symbol)}"),
    ]
    if ucsc_db:
        links.append(
            GeneExternalLinkOut(
                label="UCSC",
                href=f"https://genome.ucsc.edu/cgi-bin/hgTracks?{urlencode({'db': ucsc_db, 'position': locus})}",
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
    )

    return GeneProfileOut(
        assembly_id=str(primary_assembly["id"]),
        assembly_name=str(primary_assembly["assembly_name"]),
        assembly_version=primary_assembly.get("version"),
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
        extra=dict(cached_mapping.get("extra") or {}),
        updated_at=cached_mapping.get("updated_at"),
    )
