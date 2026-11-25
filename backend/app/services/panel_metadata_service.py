from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, NamedTuple

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import require_uuid, rows_matched, uuid_list_bindparam, uuid_values
from ..schemas import (
    GeneLocation,
    GenePanelCreate,
    GenePanelCreateResponse,
    GenePanelOut,
    GenePanelUpdate,
    GenePanelVersionDetail,
    GenePanelVersionListOut,
    GenePanelVersionSummary,
    GeneSymbolCheckOut,
    MendeliomeRegenerateResponse,
    PanelAppImportRequest,
    PanelAppImportResponse,
)
from .analysis_gene_panel import PANEL_IN_USE_SQL
from .access_control import CurrentUser, has_superuser_rights
from .ensembl_gene_ids import ensembl_gene_id, panel_gene_name
from .panelapp_service import (
    extract_panelapp_import_content,
    fetch_panelapp_panel,
    panelapp_default_panel_name,
    panelapp_panel_url,
    panelapp_selection,
    panelapp_selection_label,
)
from .sex_chromosomes import assembly_display_rank
from .stored_values import json_object as _jsonb_dict


def _ensure_admin(user: CurrentUser) -> None:
    # Gene panels are a superuser's or an admin's to change.
    if not has_superuser_rights(user):
        raise HTTPException(status_code=403, detail="Not authorized")


def _resolved_gene_count(regions: list[GeneLocation]) -> int:
    """Genes with coordinates in at least one assembly (regions are per gene per assembly)."""
    return len({region.gene.upper() for region in regions})


class _PanelMember(NamedTuple):
    """One of a panel's genes (a ``gene_panel_genes`` row): the gene as the panel names it, an
    HGNC symbol or an Ensembl gene ID, and for an ID the HGNC symbol that gene has."""

    gene: str
    symbol: str | None = None


def _member_symbols(members: Sequence[_PanelMember]) -> dict[str, str]:
    """The symbol of each member that has one (an Ensembl gene ID), by the member's gene."""
    return {member.gene: member.symbol for member in members if member.symbol}


def _panel_out_from_rows(
    panel_row: dict[str, Any],
    members: Sequence[_PanelMember],
    region_rows: list[dict[str, Any]],
) -> GenePanelOut:
    source_metadata = panel_row.get("source_metadata") or {}
    if isinstance(source_metadata, str):
        try:
            source_metadata = json.loads(source_metadata)
        except json.JSONDecodeError:
            source_metadata = {}
    return GenePanelOut(
        _id=panel_row["id"],
        name=panel_row["name"],
        version=int(panel_row.get("version") or 1),
        genes=[member.gene for member in members],
        gene_symbols=_member_symbols(members),
        gene_count=len(members),
        regions=[
            GeneLocation(
                gene=row["gene"],
                chr=row["chr"],
                start=int(row["start"]),
                end=int(row["end"]),
                assembly_id=row.get("assembly_id"),
                assembly=row.get("assembly"),
            )
            for row in region_rows
        ],
        created_by=panel_row["created_by"],
        created_by_email=panel_row.get("created_by_email"),
        created_at=panel_row["created_at"],
        description=panel_row.get("description"),
        source=panel_row.get("source") or "local",
        external_id=panel_row.get("external_id"),
        external_selection=panel_row.get("external_selection"),
        external_version=panel_row.get("external_version"),
        external_url=panel_row.get("external_url"),
        source_updated_at=panel_row.get("source_updated_at"),
        source_metadata=source_metadata if isinstance(source_metadata, dict) else {},
    )


async def _fetch_panel_rows(
    session: AsyncSession,
    panel_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    if panel_ids is None:
        result = await session.execute(
            text(
                """
                SELECT
                    gp.id::text AS id,
                    gp.name,
                    gp.version,
                    gp.created_by::text AS created_by,
                    gp.created_at,
                    gp.description,
                    gp.source,
                    gp.external_id,
                    gp.external_selection,
                    gp.external_version,
                    gp.external_url,
                    gp.source_updated_at,
                    gp.source_metadata,
                    COALESCE(NULLIF(u.email, ''), u.username) AS created_by_email
                FROM gene_panels gp
                LEFT JOIN users u ON u.id = gp.created_by
                ORDER BY lower(name)
                """
            )
        )
        return [dict(row) for row in result.mappings().all()]
    if not panel_ids:
        return []
    result = await session.execute(
        text(
            """
            SELECT
                gp.id::text AS id,
                gp.name,
                gp.version,
                gp.created_by::text AS created_by,
                gp.created_at,
                gp.description,
                gp.source,
                gp.external_id,
                gp.external_selection,
                gp.external_version,
                gp.external_url,
                gp.source_updated_at,
                gp.source_metadata,
                COALESCE(NULLIF(u.email, ''), u.username) AS created_by_email
            FROM gene_panels gp
            LEFT JOIN users u ON u.id = gp.created_by
            WHERE gp.id IN :panel_ids
            ORDER BY lower(name)
            """
        ).bindparams(uuid_list_bindparam("panel_ids")),
        {"panel_ids": uuid_values(panel_ids)},
    )
    return [dict(row) for row in result.mappings().all()]


async def _fetch_panel_members(
    session: AsyncSession,
    panel_ids: list[str],
) -> dict[str, list[_PanelMember]]:
    if not panel_ids:
        return {}
    result = await session.execute(
        text(
            """
            SELECT panel_id::text AS panel_id, gene, symbol
            FROM gene_panel_genes
            WHERE panel_id IN :panel_ids
            ORDER BY gene
            """
        ).bindparams(uuid_list_bindparam("panel_ids")),
        {"panel_ids": uuid_values(panel_ids)},
    )
    grouped: dict[str, list[_PanelMember]] = {panel_id: [] for panel_id in panel_ids}
    for row in result.mappings().all():
        grouped.setdefault(row["panel_id"], []).append(_PanelMember(row["gene"], row["symbol"]))
    return grouped


async def _fetch_panel_regions(
    session: AsyncSession,
    panel_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    if not panel_ids:
        return {}
    result = await session.execute(
        text(
            """
            SELECT r.panel_id::text AS panel_id, r.gene, r.chr, r.start, r."end",
                   r.assembly_id::text AS assembly_id, a.assembly_name AS assembly
            FROM gene_panel_regions r
            JOIN assemblies a ON a.id = r.assembly_id
            WHERE r.panel_id IN :panel_ids
            ORDER BY r.gene, a.assembly_name, r.chr, r.start, r."end"
            """
        ).bindparams(uuid_list_bindparam("panel_ids")),
        {"panel_ids": uuid_values(panel_ids)},
    )
    grouped: dict[str, list[dict[str, Any]]] = {panel_id: [] for panel_id in panel_ids}
    for row in result.mappings().all():
        grouped.setdefault(row["panel_id"], []).append(dict(row))
    return grouped


async def list_panels_data(session: AsyncSession) -> list[GenePanelOut]:
    panel_rows = await _fetch_panel_rows(session)
    panel_ids = [row["id"] for row in panel_rows]
    members = await _fetch_panel_members(session, panel_ids)
    regions = await _fetch_panel_regions(session, panel_ids)
    return [
        _panel_out_from_rows(row, members.get(row["id"], []), regions.get(row["id"], []))
        for row in panel_rows
    ]


async def get_panel_or_404(
    session: AsyncSession,
    panel_id: str,
) -> GenePanelOut:
    panel_id = require_uuid(panel_id, "Invalid panel id")
    panel_rows = await _fetch_panel_rows(session, [panel_id])
    if not panel_rows:
        raise HTTPException(status_code=404, detail="Panel not found")
    members = await _fetch_panel_members(session, [panel_id])
    regions = await _fetch_panel_regions(session, [panel_id])
    return _panel_out_from_rows(panel_rows[0], members.get(panel_id, []), regions.get(panel_id, []))


@dataclass(frozen=True, slots=True)
class _GeneResolution:
    """What the gene reference makes of a panel's gene names.

    ``names``: each name once, as the panel keeps it (``panel_gene_name``), in the order given.
    ``regions``: each name's locus in every loaded assembly that has it, labelled with the name.
    ``missing``: the names no loaded assembly has. ``symbols``: the HGNC symbol of each Ensembl
    gene ID among the names that has one. ``folded_into``: after ``folded``, the Ensembl gene ID
    each symbol it left out names.
    """

    names: list[str]
    regions: list[GeneLocation]
    missing: list[str]
    symbols: dict[str, str]
    folded_into: dict[str, str] = field(default_factory=dict)

    def members(self) -> list[_PanelMember]:
        return [_PanelMember(name, self.symbols.get(name)) for name in self.names]

    def folded(self) -> _GeneResolution:
        """The genes as a panel keeps them: a symbol that names the same gene as one of the
        Ensembl gene IDs (the ID's symbol, in any capitals) is that gene, kept once, by its ID,
        with the ID's regions alone. The symbol's own regions are left out: in a reference that
        gives other symbols (GRCh37's), the symbol may be another gene's. A symbol that two of
        the IDs share stays as it is, as it names neither of them alone."""
        ids_by_symbol: dict[str, list[str]] = {}
        for gene_id, symbol in self.symbols.items():
            ids_by_symbol.setdefault(symbol.upper(), []).append(gene_id)
        into: dict[str, str] = {
            name: ids_by_symbol[name.upper()][0]
            for name in self.names
            if not ensembl_gene_id(name) and len(ids_by_symbol.get(name.upper(), [])) == 1
        }
        if not into:
            return self
        return _GeneResolution(
            names=[name for name in self.names if name not in into],
            regions=[region for region in self.regions if region.gene not in into],
            missing=[name for name in self.missing if name not in into],
            symbols=self.symbols,
            folded_into=into,
        )


def _by_assembly_rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """GRCh38, T2T-CHM13, GRCh37 under any of their names, then the rest by name."""
    return sorted(
        rows,
        key=lambda row: (assembly_display_rank(row["assembly_name"]), str(row["assembly_name"])),
    )


async def _gene_loci_by_symbol(
    session: AsyncSession,
    symbols: Sequence[str],
    *,
    without_ensembl_gene_id: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Each symbol's longest locus in every loaded assembly that has it, by the symbol in
    capitals. ``without_ensembl_gene_id`` reads only the genes the reference gives no Ensembl
    gene ID: those of a RefSeq-derived reference (T2T's), where an ID cannot be looked up."""
    if not symbols:
        return {}
    only_without_id = "AND (g.extra ->> 'ensembl_gene_id') IS NULL" if without_ensembl_gene_id else ""
    # One text[] parameter, not one per gene (an expanding IN): asyncpg takes at most 32,767
    # per statement, and a panel names any number of genes.
    result = await session.execute(
        text(
            f"""
            SELECT DISTINCT ON (upper(g.hgnc_symbol), g.assembly_id)
                upper(g.hgnc_symbol) AS symbol_key,
                g.assembly_id::text AS assembly_id,
                a.assembly_name,
                g.chr,
                g.start,
                g."end" AS "end"
            FROM genes g
            JOIN assemblies a ON a.id = g.assembly_id
            WHERE upper(g.hgnc_symbol) = ANY(CAST(:symbols AS text[]))
              {only_without_id}
            ORDER BY
                upper(g.hgnc_symbol),
                g.assembly_id,
                (g."end" - g.start) DESC,
                g.start,
                g."end"
            """
        ),
        {"symbols": list(dict.fromkeys(symbol.upper() for symbol in symbols))},
    )
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        by_symbol.setdefault(str(row["symbol_key"]), []).append(dict(row))
    return by_symbol


async def _gene_loci_by_ensembl_id(
    session: AsyncSession,
    gene_ids: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    """Each Ensembl gene ID's longest locus, with that gene's symbol, in every loaded assembly
    whose gene reference gives it, by the ID."""
    if not gene_ids:
        return {}
    result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (g.extra ->> 'ensembl_gene_id', g.assembly_id)
                g.extra ->> 'ensembl_gene_id' AS ensembl_gene_id,
                g.hgnc_symbol,
                g.assembly_id::text AS assembly_id,
                a.assembly_name,
                g.chr,
                g.start,
                g."end" AS "end"
            FROM genes g
            JOIN assemblies a ON a.id = g.assembly_id
            WHERE (g.extra ->> 'ensembl_gene_id') = ANY(CAST(:gene_ids AS text[]))
            ORDER BY
                g.extra ->> 'ensembl_gene_id',
                g.assembly_id,
                (g."end" - g.start) DESC,
                g.start,
                g."end"
            """
        ),
        {"gene_ids": list(dict.fromkeys(gene_ids))},
    )
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        by_id.setdefault(str(row["ensembl_gene_id"]), []).append(dict(row))
    return by_id


def _reference_symbol(gene_id: str, rows: list[dict[str, Any]]) -> str | None:
    """The symbol the gene reference gives an Ensembl gene ID: that of its first assembly in
    display order. GENCODE names a gene that has no symbol by its ID, which is no symbol."""
    for row in _by_assembly_rank(rows):
        symbol = str(row.get("hgnc_symbol") or "").strip()
        if symbol and ensembl_gene_id(symbol) != gene_id:
            return symbol
    return None


async def _resolve_gene_regions(
    session: AsyncSession,
    names: Sequence[str],
    *,
    stated_symbols: Mapping[str, str] | None = None,
) -> _GeneResolution:
    """Each gene's locus in every loaded assembly that has it, tagged with that assembly.

    A panel is a gene list; its coordinates depend on the assembly, so each region is kept
    with its assembly and a family's panel filter reads only its own assembly's: a GRCh38
    family is never filtered on T2T coordinates, nor a T2T family on GRCh38 ones. A gene is
    "missing" only when no loaded assembly has it.

    A gene named by its symbol is looked up by its symbol. One named by its Ensembl gene ID is
    looked up by the ID, in every reference that gives its genes Ensembl IDs (GENCODE's), and
    gets the symbol that reference gives it: that of its first assembly in display order, or,
    for an ID none of them knows, the one ``stated_symbols`` gives (the source panel's). In a
    reference that gives its genes no Ensembl ID (a RefSeq-derived one, T2T's), that symbol is
    looked up instead, among those genes only: a gene that has an Ensembl ID is never taken
    for an ID by its symbol.
    """
    names_kept = list(dict.fromkeys(panel_gene_name(name) for name in names if name and str(name).strip()))
    if not names_kept:
        return _GeneResolution(names=[], regions=[], missing=[], symbols={})
    gene_ids = [name for name in names_kept if ensembl_gene_id(name)]
    by_symbol = await _gene_loci_by_symbol(session, [name for name in names_kept if not ensembl_gene_id(name)])
    by_id = await _gene_loci_by_ensembl_id(session, gene_ids)

    symbols: dict[str, str] = {}
    for gene_id in gene_ids:
        symbol = _reference_symbol(gene_id, by_id.get(gene_id, [])) or (stated_symbols or {}).get(gene_id)
        if symbol:
            symbols[gene_id] = symbol
    stand_ins = await _gene_loci_by_symbol(session, list(symbols.values()), without_ensembl_gene_id=True)
    for gene_id, symbol in symbols.items():
        by_id.setdefault(gene_id, []).extend(stand_ins.get(symbol.upper(), []))

    missing_genes: list[str] = []
    regions: list[GeneLocation] = []
    for name in names_kept:
        matches = by_id.get(name) if ensembl_gene_id(name) else by_symbol.get(name.upper())
        if not matches:
            missing_genes.append(name)
            continue
        for match in _by_assembly_rank(matches):
            regions.append(
                GeneLocation(
                    gene=name,
                    chr=match["chr"],
                    start=int(match["start"]),
                    end=int(match["end"]),
                    assembly_id=match["assembly_id"],
                    assembly=match["assembly_name"],
                )
            )
    return _GeneResolution(names=names_kept, regions=regions, missing=missing_genes, symbols=symbols)


async def check_gene_symbols(session: AsyncSession, names: list[str]) -> GeneSymbolCheckOut:
    """Which names a panel would get coordinates for, and which it would not, before an
    admin saves them: a name no loaded assembly knows (a typo, an old symbol, a retired
    Ensembl gene ID) is kept in the panel's gene list but has no region, so a filter on the
    panel finds nothing in it. An Ensembl gene ID comes with the symbol of the gene it names,
    for the admin to see which gene that is."""
    resolution = await _resolve_gene_regions(session, names)
    missing = set(resolution.missing)
    return GeneSymbolCheckOut(
        found=[name for name in resolution.names if name not in missing],
        missing=resolution.missing,
        symbols={gene_id: symbol for gene_id, symbol in resolution.symbols.items() if gene_id not in missing},
    )


async def _assembly_id_by_name(session: AsyncSession, assembly_name: str) -> str | None:
    """The loaded assembly of this name, if any."""
    result = await session.execute(
        text("SELECT id::text FROM assemblies WHERE assembly_name = :name"),
        {"name": assembly_name},
    )
    return result.scalar_one_or_none()


async def _ensure_panel_name_available(
    session: AsyncSession,
    name: str,
    *,
    existing_panel_id: str | None = None,
) -> None:
    if existing_panel_id is None:
        result = await session.execute(
            text(
                """
                SELECT id::text
                FROM gene_panels
                WHERE name = :name
                """
            ),
            {"name": name},
        )
        if result.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="Panel already exists")
        return

    result = await session.execute(
        text(
            """
            SELECT id::text
            FROM gene_panels
            WHERE name = :name
              AND id::text <> :existing_panel_id
            """
        ),
        {"name": name, "existing_panel_id": existing_panel_id},
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Panel already exists")


async def _replace_panel_members(
    session: AsyncSession,
    *,
    panel_id: str,
    members: Sequence[_PanelMember],
    regions: list[GeneLocation],
) -> None:
    await session.execute(
        text("DELETE FROM gene_panel_genes WHERE panel_id = CAST(:panel_id AS uuid)"),
        {"panel_id": panel_id},
    )
    await session.execute(
        text("DELETE FROM gene_panel_regions WHERE panel_id = CAST(:panel_id AS uuid)"),
        {"panel_id": panel_id},
    )
    if members:
        await session.execute(
            text(
                """
                INSERT INTO gene_panel_genes (panel_id, gene, symbol)
                VALUES (CAST(:panel_id AS uuid), :gene, :symbol)
                """
            ),
            [
                {"panel_id": panel_id, "gene": member.gene, "symbol": member.symbol}
                for member in members
            ],
        )
    # Coordinates without an assembly cannot be scoped to one, so they are not stored;
    # the resolvers only produce such regions for an assembly that is not loaded.
    scoped = [region for region in regions if region.assembly_id]
    if scoped:
        await session.execute(
            text(
                """
                INSERT INTO gene_panel_regions (panel_id, assembly_id, gene, chr, start, "end")
                VALUES (CAST(:panel_id AS uuid), CAST(:assembly_id AS uuid), :gene, :chr, :start, :end)
                """
            ),
            [
                {
                    "panel_id": panel_id,
                    "assembly_id": region.assembly_id,
                    "gene": region.gene,
                    "chr": region.chr,
                    "start": region.start,
                    "end": region.end,
                }
                for region in scoped
            ],
        )


async def _panel_members_unchanged(
    session: AsyncSession,
    panel_id: str,
    *,
    members: Sequence[_PanelMember],
    regions: list[GeneLocation],
) -> bool:
    """Whether the panel holds these genes, with these symbols, and these regions already,
    and nothing else.

    The regions compared are the ones ``_replace_panel_members`` stores: those scoped to an
    assembly. The order of either list does not matter.
    """
    current_members = (await _fetch_panel_members(session, [panel_id])).get(panel_id, [])
    if set(current_members) != set(members):
        return False
    current_regions = (await _fetch_panel_regions(session, [panel_id])).get(panel_id, [])
    stored = {
        (str(row["assembly_id"]), str(row["gene"]), str(row["chr"]), int(row["start"]), int(row["end"]))
        for row in current_regions
    }
    wanted = {
        (str(region.assembly_id), region.gene, region.chr, int(region.start), int(region.end))
        for region in regions
        if region.assembly_id
    }
    return stored == wanted


async def _snapshot_panel_version(
    session: AsyncSession,
    *,
    panel_id: str,
    version: int,
    name: str,
    description: str | None,
    source: str | None,
    external_version: str | None,
    members: Sequence[_PanelMember],
    regions: list[GeneLocation],
    source_metadata: dict[str, Any] | None,
    user: CurrentUser,
) -> None:
    """Archive a snapshot of a panel version.

    Nothing in the app rewrites a snapshot, and the (panel, version) key refuses a second
    one under the same number. No trigger makes the table append-only, though, and
    deleting the panel deletes its snapshots (ON DELETE CASCADE).
    """
    await session.execute(
        text(
            """
            INSERT INTO gene_panel_versions
                (panel_id, version, name, description, source, external_version,
                 gene_count, genes, gene_symbols, regions, source_metadata, created_by,
                 created_by_email)
            VALUES
                (CAST(:panel_id AS uuid), :version, :name, :description, :source,
                 :external_version, :gene_count, CAST(:genes AS jsonb),
                 CAST(:gene_symbols AS jsonb), CAST(:regions AS jsonb),
                 CAST(:source_metadata AS jsonb), CAST(:created_by AS uuid), :created_by_email)
            """
        ),
        {
            "panel_id": panel_id,
            "version": version,
            "name": name,
            "description": description,
            "source": source,
            "external_version": external_version,
            "gene_count": len(members),
            "genes": json.dumps([member.gene for member in members]),
            "gene_symbols": json.dumps(_member_symbols(members), sort_keys=True),
            "regions": json.dumps(
                [
                    {
                        "gene": region.gene,
                        "chr": region.chr,
                        "start": region.start,
                        "end": region.end,
                        "assembly": region.assembly,
                    }
                    for region in regions
                ]
            ),
            "source_metadata": json.dumps(source_metadata or {}),
            "created_by": getattr(user, "id", None),
            "created_by_email": getattr(user, "email", None),
        },
    )


async def create_panel_data(
    session: AsyncSession,
    panel: GenePanelCreate,
    user: CurrentUser,
) -> GenePanelCreateResponse:
    _ensure_admin(user)
    await _ensure_panel_name_available(session, panel.name)

    resolution = (await _resolve_gene_regions(session, panel.genes)).folded()
    members = resolution.members()
    regions = resolution.regions
    missing_genes = resolution.missing

    description = panel.description.strip() if panel.description and panel.description.strip() else None

    created = await session.execute(
        text(
            """
            INSERT INTO gene_panels (name, description, created_by, created_at)
            VALUES (:name, :description, CAST(:created_by AS uuid), :created_at)
            RETURNING
                id::text AS id,
                name,
                created_by::text AS created_by,
                created_at,
                description
            """
        ),
        {
            "name": panel.name,
            "description": description,
            "created_by": user.id,
            "created_at": datetime.now(timezone.utc),
        },
    )
    panel_row = dict(created.mappings().one())
    panel_row["created_by_email"] = user.email
    panel_row["source"] = "local"
    panel_row["external_id"] = None
    panel_row["external_version"] = None
    panel_row["external_url"] = None
    panel_row["source_updated_at"] = None
    panel_row["source_metadata"] = {}
    panel_id = panel_row["id"]

    await _replace_panel_members(session, panel_id=panel_id, members=members, regions=regions)
    await _snapshot_panel_version(
        session,
        panel_id=panel_id,
        version=1,
        name=panel.name,
        description=description,
        source="local",
        external_version=None,
        members=members,
        regions=regions,
        source_metadata={},
        user=user,
    )

    await session.commit()
    panel_out = _panel_out_from_rows(
        panel_row,
        members,
        [region.model_dump() for region in regions],
    )
    message = f"Panel created with {_resolved_gene_count(regions)} of {len(members)} genes"
    if missing_genes:
        message += f"; missing genes: {', '.join(missing_genes)}"
    return GenePanelCreateResponse(
        panel=panel_out,
        message=message,
        missing_genes=missing_genes,
    )


async def update_panel_data(
    session: AsyncSession,
    panel_id: str,
    payload: GenePanelUpdate,
    user: CurrentUser,
) -> GenePanelCreateResponse:
    """Edit a manually-curated panel's gene set (add/remove) as a new version.

    The full desired gene list is supplied; the prior version stays archived. Only
    locally-curated panels are editable this way — generated/imported panels
    (Mendeliome, PanelApp) are updated by regenerating or re-importing.
    """
    _ensure_admin(user)
    panel_id = require_uuid(panel_id, "Invalid panel id")
    existing = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, name, version,
                       source, description
                FROM gene_panels WHERE id = CAST(:panel_id AS uuid)
                FOR UPDATE
                """
            ),
            {"panel_id": panel_id},
        )
    ).mappings().first()
    if existing is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    if existing["source"] != "local":
        raise HTTPException(
            status_code=400,
            detail=(
                f"The '{existing['source']}' panel '{existing['name']}' is generated/imported; "
                "update it by regenerating or re-importing rather than editing genes."
            ),
        )
    # Locked until the commit (FOR UPDATE above): a second edit waits, then finds this one's
    # version instead of saving its own list over it.
    if payload.expected_version is not None and payload.expected_version != int(existing["version"]):
        raise HTTPException(
            status_code=409,
            detail=(
                f"The panel has changed since you opened it: it is now at version "
                f"{int(existing['version'])}, not {payload.expected_version}."
            ),
        )

    resolution = (await _resolve_gene_regions(session, payload.genes)).folded()
    members = resolution.members()
    regions = resolution.regions
    missing_genes = resolution.missing
    description = (
        payload.description.strip()
        if payload.description and payload.description.strip()
        else existing["description"]
    )

    # The same genes with the same symbols: an Ensembl gene ID whose symbol the gene reference
    # now gives otherwise is another version, as the panel's filter reads the symbol too.
    current_members = set((await _fetch_panel_members(session, [panel_id])).get(panel_id, []))
    if current_members == set(members) and description == existing["description"]:
        panel_out = await get_panel_or_404(session, panel_id)
        return GenePanelCreateResponse(
            panel=panel_out,
            message=f"No changes — panel already at version {int(existing['version'])}.",
            missing_genes=missing_genes,
        )

    version = int(existing["version"]) + 1
    await session.execute(
        text(
            """
            UPDATE gene_panels
            SET version = :version, description = :description
            WHERE id = CAST(:panel_id AS uuid)
            """
        ),
        {"version": version, "description": description, "panel_id": panel_id},
    )
    await _replace_panel_members(session, panel_id=panel_id, members=members, regions=regions)
    await _snapshot_panel_version(
        session,
        panel_id=panel_id,
        version=version,
        name=existing["name"],
        description=description,
        source="local",
        external_version=None,
        members=members,
        regions=regions,
        source_metadata={},
        user=user,
    )
    await session.commit()
    panel_out = await get_panel_or_404(session, panel_id)
    message = (
        f"Panel updated to version {version}: {_resolved_gene_count(regions)} of "
        f"{len(members)} genes"
    )
    if missing_genes:
        message += f"; unresolved: {', '.join(missing_genes)}"
    return GenePanelCreateResponse(panel=panel_out, message=message, missing_genes=missing_genes)


async def import_panelapp_panel_data(
    session: AsyncSession,
    request: PanelAppImportRequest,
    user: CurrentUser,
) -> PanelAppImportResponse:
    """Import a PanelApp panel, or import again one the catalog already holds.

    A panel is a PanelApp panel with the confidence levels it takes (``external_selection``):
    the same PanelApp panel imported green only, green and amber, or green, amber and red is
    three panels, each with its own name, versions and genes. The first import of a selection
    archives its panel as version 1. An import that gives the panel other genes or regions
    (from another PanelApp release, with other options, or with another assembly loaded)
    makes the next version and archives it; one that leaves both as they are keeps the
    version and archives nothing, but still records PanelApp's version, the options and the
    time of the import. The prioritised-ranking cache keys a panel on its version, so a
    ranking built on the earlier genes is no longer an exact hit. ``external_version`` stays
    PanelApp's own version of the panel.
    """
    _ensure_admin(user)
    if not request.confidence_levels:
        raise HTTPException(status_code=400, detail="At least one PanelApp confidence level is required")

    payload = await fetch_panelapp_panel(request.panelapp_id, version=request.version)
    content = extract_panelapp_import_content(
        payload,
        confidence_levels=request.confidence_levels,
        include_regions=request.include_regions,
        include_strs=request.include_strs,
        assembly=request.assembly,
    )
    # Every gene is resolved in every loaded assembly. A gene PanelApp gives an Ensembl gene ID
    # is kept by its ID, with the gene reference's symbol for it, or PanelApp's where no loaded
    # assembly knows the ID; a symbol of the same gene (an STR's, where PanelApp gives it no
    # ID) is that gene.
    reference = (
        await _resolve_gene_regions(session, content.genes, stated_symbols=content.gene_symbols)
    ).folded()
    # PanelApp's own coordinates are for the requested assembly. They are stored for that
    # assembly when it is loaded; otherwise they apply to no family and are not kept. They win
    # for the genes and the assembly they cover.
    requested_assembly_id = await _assembly_id_by_name(session, request.assembly)
    panelapp_regions: list[GeneLocation] = []
    if requested_assembly_id:
        seen: set[tuple[str, str, int, int]] = set()
        for region in content.regions:
            kept = region.model_copy(
                update={
                    "gene": reference.folded_into.get(region.gene, region.gene),
                    "assembly_id": requested_assembly_id,
                    "assembly": request.assembly,
                }
            )
            key = (kept.gene.upper(), kept.chr, kept.start, kept.end)
            if key not in seen:
                seen.add(key)
                panelapp_regions.append(kept)
    covered = {(region.gene.upper(), region.assembly_id) for region in panelapp_regions}
    regions = panelapp_regions + [
        region
        for region in reference.regions
        if (region.gene.upper(), region.assembly_id) not in covered
    ]
    members = reference.members()
    located = {region.gene for region in regions}
    missing_genes = [member.gene for member in members if member.gene not in located]
    if not members and not regions:
        raise HTTPException(status_code=400, detail="No PanelApp entities matched the selected import options")

    external_id = str(request.panelapp_id)
    external_selection = panelapp_selection(request.confidence_levels)
    # Locked until the commit: the version this import makes, and whether it makes one,
    # are decided against the panel as it is, not as another import is leaving it.
    existing = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, version
                FROM gene_panels
                WHERE source = 'panelapp' AND external_id = :external_id
                  AND external_selection = :external_selection
                FOR UPDATE
                """
            ),
            {"external_id": external_id, "external_selection": external_selection},
        )
    ).mappings().first()
    existing_panel_id = existing["id"] if existing is not None else None
    name = (
        request.name.strip()
        if request.name and request.name.strip()
        else panelapp_default_panel_name(payload, request.confidence_levels)
    )
    await _ensure_panel_name_available(session, name, existing_panel_id=existing_panel_id)

    source_metadata = {
        **content.metadata,
        "missing_coordinate_genes": missing_genes,
    }
    source_metadata_json = json.dumps(source_metadata, sort_keys=True, default=str)
    now = datetime.now(timezone.utc)
    external_version = str(payload.get("version") or request.version or "")
    description = (
        f"Imported from PanelApp {request.panelapp_id}"
        f" version {payload.get('version') or request.version or 'latest'},"
        f" confidence {panelapp_selection_label(request.confidence_levels)}."
    )
    if payload.get("disease_group"):
        description += f" Disease group: {payload['disease_group']}."
    if payload.get("relevant_disorders"):
        description += f" Relevant disorders: {', '.join(str(value) for value in payload['relevant_disorders'])}."

    if existing is not None:
        # Other genes or regions are a new version; the same ones keep the version, as an
        # edit that changes nothing does.
        changed = not await _panel_members_unchanged(
            session, existing["id"], members=members, regions=regions
        )
        version = int(existing["version"]) + 1 if changed else int(existing["version"])
        updated = await session.execute(
            text(
                """
                UPDATE gene_panels
                SET
                    name = :name,
                    description = :description,
                    version = :version,
                    external_version = :external_version,
                    external_url = :external_url,
                    source_updated_at = :source_updated_at,
                    source_metadata = CAST(:source_metadata_json AS jsonb)
                WHERE id = CAST(:panel_id AS uuid)
                RETURNING
                    id::text AS id,
                    name,
                    created_by::text AS created_by,
                    created_at,
                    description,
                    source,
                    external_id,
                    external_selection,
                    external_version,
                    external_url,
                    source_updated_at,
                    source_metadata
                """
            ),
            {
                "panel_id": existing_panel_id,
                "name": name,
                "description": description,
                "version": version,
                "external_version": external_version,
                "external_url": panelapp_panel_url(request.panelapp_id),
                "source_updated_at": now,
                "source_metadata_json": source_metadata_json,
            },
        )
        panel_row = dict(updated.mappings().one())
        panel_id = panel_row["id"]
    else:
        changed = True
        version = 1
        created = await session.execute(
            text(
                """
                INSERT INTO gene_panels (
                    name,
                    description,
                    source,
                    external_id,
                    external_selection,
                    external_version,
                    external_url,
                    source_updated_at,
                    source_metadata,
                    created_by,
                    created_at
                )
                VALUES (
                    :name,
                    :description,
                    'panelapp',
                    :external_id,
                    :external_selection,
                    :external_version,
                    :external_url,
                    :source_updated_at,
                    CAST(:source_metadata_json AS jsonb),
                    CAST(:created_by AS uuid),
                    :created_at
                )
                RETURNING
                    id::text AS id,
                    name,
                    created_by::text AS created_by,
                    created_at,
                    description,
                    source,
                    external_id,
                    external_selection,
                    external_version,
                    external_url,
                    source_updated_at,
                    source_metadata
                """
            ),
            {
                "name": name,
                "description": description,
                "external_id": external_id,
                "external_selection": external_selection,
                "external_version": external_version,
                "external_url": panelapp_panel_url(request.panelapp_id),
                "source_updated_at": now,
                "source_metadata_json": source_metadata_json,
                "created_by": user.id,
                "created_at": now,
            },
        )
        panel_row = dict(created.mappings().one())
        panel_id = panel_row["id"]

    if changed:
        await _replace_panel_members(session, panel_id=panel_id, members=members, regions=regions)
        await _snapshot_panel_version(
            session,
            panel_id=panel_id,
            version=version,
            name=name,
            description=description,
            source="panelapp",
            external_version=external_version,
            members=members,
            regions=regions,
            source_metadata=source_metadata,
            user=user,
        )
    panel_rows = await _fetch_panel_rows(session, [panel_id])
    if panel_rows:
        panel_row = panel_rows[0]
    await session.commit()

    panel_out = _panel_out_from_rows(panel_row, members, [region.model_dump() for region in regions])
    contents = (
        f"{len(members)} genes and {len(regions)} regions"
        f" ({panelapp_selection_label(request.confidence_levels)})"
    )
    if existing is None:
        message = f"PanelApp panel imported as version {version} with {contents}"
    elif changed:
        message = f"PanelApp panel updated to version {version} with {contents}"
    else:
        message = f"PanelApp panel kept at version {version}: the same {contents}"
    if missing_genes:
        message += f"; no coordinates for: {', '.join(missing_genes)}"
    if content.regions and not requested_assembly_id:
        message += (
            f"; PanelApp's {request.assembly} coordinates were not kept, because "
            f"{request.assembly} is not a loaded assembly"
        )
    return PanelAppImportResponse(
        panel=panel_out,
        message=message,
        missing_genes=missing_genes,
        imported_gene_count=len(members),
        imported_region_count=len(regions),
    )


async def delete_panel_data(
    session: AsyncSession,
    panel_id: str,
    user: CurrentUser,
) -> None:
    _ensure_admin(user)
    panel_id = require_uuid(panel_id, "Invalid panel id")
    # A panel an analysis is read within (its diagnostic panel, analysis_gene_panel) stays: the
    # analysis must first be given another one, on its clinical audit trail.
    in_use = (await session.execute(PANEL_IN_USE_SQL, {"panel_id": panel_id})).scalar() or 0
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The panel is the diagnostic panel of {in_use} "
                f"{'analysis' if in_use == 1 else 'analyses'}: give "
                f"{'it' if in_use == 1 else 'them'} another panel first."
            ),
        )
    result = await session.execute(
        text("DELETE FROM gene_panels WHERE id = CAST(:panel_id AS uuid)"),
        {"panel_id": panel_id},
    )
    if rows_matched(result) == 0:
        raise HTTPException(status_code=404, detail="Panel not found")
    await session.commit()


# ---------------------------------------------------------------------------
# Gene-panel version history
# ---------------------------------------------------------------------------

def _jsonb_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []




async def list_panel_versions(session: AsyncSession, panel_id: str) -> GenePanelVersionListOut:
    panel_id = require_uuid(panel_id, "Invalid panel id")
    current = (
        await session.execute(
            text("SELECT version FROM gene_panels WHERE id = CAST(:panel_id AS uuid)"),
            {"panel_id": panel_id},
        )
    ).scalar()
    if current is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    rows = (
        await session.execute(
            text(
                """
                SELECT version, name, source, external_version, gene_count,
                       created_by_email, created_at
                FROM gene_panel_versions
                WHERE panel_id = CAST(:panel_id AS uuid)
                ORDER BY version DESC
                """
            ),
            {"panel_id": panel_id},
        )
    ).mappings().all()
    return GenePanelVersionListOut(
        panel_id=panel_id,
        current_version=int(current),
        versions=[GenePanelVersionSummary(**dict(row)) for row in rows],
    )


async def get_panel_version(
    session: AsyncSession, panel_id: str, version: int
) -> GenePanelVersionDetail:
    panel_id = require_uuid(panel_id, "Invalid panel id")
    row = (
        await session.execute(
            text(
                """
                SELECT version, name, description, source, external_version, gene_count,
                       genes, gene_symbols, regions, source_metadata, created_by_email,
                       created_at
                FROM gene_panel_versions
                WHERE panel_id = CAST(:panel_id AS uuid) AND version = :version
                """
            ),
            {"panel_id": panel_id, "version": version},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Panel version not found")
    data = dict(row)
    return GenePanelVersionDetail(
        version=data["version"],
        name=data["name"],
        description=data.get("description"),
        source=data.get("source"),
        external_version=data.get("external_version"),
        gene_count=data["gene_count"],
        created_by_email=data.get("created_by_email"),
        created_at=data["created_at"],
        genes=[str(gene) for gene in _jsonb_list(data.get("genes"))],
        gene_symbols={
            str(gene): str(symbol) for gene, symbol in _jsonb_dict(data.get("gene_symbols")).items()
        },
        regions=[GeneLocation(**region) for region in _jsonb_list(data.get("regions"))],
        source_metadata=_jsonb_dict(data.get("source_metadata")),
    )


# ---------------------------------------------------------------------------
# Mendeliome — all genes with a Mendelian disease association in Monarch
# ---------------------------------------------------------------------------

MENDELIOME_NAME = "Mendeliome"
MENDELIOME_SOURCE = "mendeliome"
# Disease associations: 'causes' (causal) + 'gene_associated_with_condition'
# (disease-associated). Risk modifiers ('contributes_to', 'increased_likelihood')
# are intentionally excluded.
_MENDELIOME_PREDICATES = ("causes", "gene_associated_with_condition")


async def _select_mendeliome_genes(session: AsyncSession) -> tuple[list[str], str | None]:
    rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT gene_symbol
                FROM monarch_gene_disease
                WHERE predicate = ANY(:predicates)
                  AND coalesce(gene_symbol, '') <> ''
                ORDER BY gene_symbol
                """
            ),
            {"predicates": list(_MENDELIOME_PREDICATES)},
        )
    ).mappings().all()
    genes = [str(row["gene_symbol"]).strip() for row in rows if str(row["gene_symbol"]).strip()]
    release = (
        await session.execute(text("SELECT max(release_version) FROM monarch_gene_disease"))
    ).scalar()
    return genes, (str(release) if release else None)


async def _mendeliome_panel(session: AsyncSession) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, version
                FROM gene_panels
                WHERE source = :source
                ORDER BY created_at
                LIMIT 1
                """
            ),
            {"source": MENDELIOME_SOURCE},
        )
    ).mappings().first()
    return dict(row) if row else None


async def regenerate_mendeliome(
    session: AsyncSession, user: CurrentUser, *, force: bool = False
) -> MendeliomeRegenerateResponse:
    """Build (or re-version) the Mendeliome panel from the loaded Monarch release.

    A new archived version is created only when the gene set actually changes; an
    unchanged refresh just re-stamps the panel with the latest Monarch release.
    """
    _ensure_admin(user)
    genes, release = await _select_mendeliome_genes(session)
    if not genes:
        raise HTTPException(
            status_code=409,
            detail="Monarch gene–disease data is not loaded; cannot build the Mendeliome.",
        )
    resolution = (await _resolve_gene_regions(session, genes)).folded()
    members = resolution.members()
    deduped = resolution.names
    regions = resolution.regions
    missing_genes = resolution.missing
    now = datetime.now(timezone.utc)
    description = (
        "All genes with a Mendelian disease association in the Monarch knowledge graph "
        "(predicates: causes, gene_associated_with_condition; aggregating OMIM, Orphanet "
        f"and ClinGen). Generated from Monarch release {release or 'unknown'}."
    )
    source_metadata = {
        "generated": True,
        "monarch_release": release,
        "predicates": list(_MENDELIOME_PREDICATES),
        "gene_count": len(deduped),
        "resolved_count": _resolved_gene_count(regions),
        "missing_count": len(missing_genes),
    }

    existing = await _mendeliome_panel(session)
    if existing is None:
        created = await session.execute(
            text(
                """
                INSERT INTO gene_panels
                    (name, description, source, external_id, external_version,
                     source_updated_at, source_metadata, version, created_by, created_at)
                VALUES
                    (:name, :description, :source, :external_id, :external_version,
                     :source_updated_at, CAST(:source_metadata AS jsonb), 1,
                     CAST(:created_by AS uuid), :created_at)
                RETURNING id::text AS id
                """
            ),
            {
                "name": MENDELIOME_NAME,
                "description": description,
                "source": MENDELIOME_SOURCE,
                "external_id": MENDELIOME_SOURCE,
                "external_version": release,
                "source_updated_at": now,
                "source_metadata": json.dumps(source_metadata),
                "created_by": user.id,
                "created_at": now,
            },
        )
        panel_id = created.mappings().one()["id"]
        version = 1
        changed = True
        await _replace_panel_members(session, panel_id=panel_id, members=members, regions=regions)
        await _snapshot_panel_version(
            session,
            panel_id=panel_id,
            version=version,
            name=MENDELIOME_NAME,
            description=description,
            source=MENDELIOME_SOURCE,
            external_version=release,
            members=members,
            regions=regions,
            source_metadata=source_metadata,
            user=user,
        )
        message = f"Mendeliome created with {len(deduped)} genes (Monarch {release or 'unknown'})."
    else:
        panel_id = existing["id"]
        current_version = int(existing["version"])
        current_members = set((await _fetch_panel_members(session, [panel_id])).get(panel_id, []))
        if not force and current_members == set(members):
            await session.execute(
                text(
                    """
                    UPDATE gene_panels
                    SET external_version = :release,
                        source_updated_at = :now,
                        source_metadata = CAST(:source_metadata AS jsonb)
                    WHERE id = CAST(:panel_id AS uuid)
                    """
                ),
                {
                    "release": release,
                    "now": now,
                    "source_metadata": json.dumps(source_metadata),
                    "panel_id": panel_id,
                },
            )
            version = current_version
            changed = False
            message = (
                f"Mendeliome already current — no gene changes for Monarch "
                f"{release or 'unknown'} (version {version})."
            )
        else:
            version = current_version + 1
            await session.execute(
                text(
                    """
                    UPDATE gene_panels
                    SET version = :version,
                        description = :description,
                        external_version = :release,
                        source_updated_at = :now,
                        source_metadata = CAST(:source_metadata AS jsonb)
                    WHERE id = CAST(:panel_id AS uuid)
                    """
                ),
                {
                    "version": version,
                    "description": description,
                    "release": release,
                    "now": now,
                    "source_metadata": json.dumps(source_metadata),
                    "panel_id": panel_id,
                },
            )
            await _replace_panel_members(session, panel_id=panel_id, members=members, regions=regions)
            await _snapshot_panel_version(
                session,
                panel_id=panel_id,
                version=version,
                name=MENDELIOME_NAME,
                description=description,
                source=MENDELIOME_SOURCE,
                external_version=release,
                members=members,
                regions=regions,
                source_metadata=source_metadata,
                user=user,
            )
            changed = True
            message = (
                f"Mendeliome updated to version {version}: {len(deduped)} genes "
                f"(Monarch {release or 'unknown'})."
            )

    await session.commit()
    panel_out = await get_panel_or_404(session, panel_id)
    return MendeliomeRegenerateResponse(
        panel=panel_out,
        message=message,
        changed=changed,
        version=version,
        monarch_release=release,
        gene_count=len(deduped),
        missing_genes=missing_genes,
    )
