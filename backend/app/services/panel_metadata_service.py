from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sql import uuid_list_bindparam, uuid_values
from ..schemas import GeneLocation, GenePanelCreate, GenePanelCreateResponse, GenePanelOut
from .metadata_service import CurrentUser


def _require_panel_uuid(panel_id: str) -> None:
    try:
        UUID(panel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid panel id") from exc


def _ensure_admin(user: CurrentUser) -> None:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")


_ASSEMBLY_PRIORITY_SQL = """
CASE
    WHEN a.assembly_name = 'GRCh38' THEN 0
    WHEN a.assembly_name IN ('T2T-CHM13', 'T2T-CHM13v2.0')
         OR a.assembly_name LIKE 'T2T-CHM13%' THEN 1
    WHEN a.assembly_name IN ('GRCh37', 'hg19') THEN 2
    ELSE 9
END
""".strip()


def _panel_out_from_rows(
    panel_row: dict[str, Any],
    genes: list[str],
    region_rows: list[dict[str, Any]],
) -> GenePanelOut:
    return GenePanelOut(
        _id=panel_row["id"],
        name=panel_row["name"],
        genes=genes,
        gene_count=len(genes),
        regions=[
            GeneLocation(
                gene=row["gene"],
                chr=row["chr"],
                start=int(row["start"]),
                end=int(row["end"]),
            )
            for row in region_rows
        ],
        created_by=panel_row["created_by"],
        created_by_email=panel_row.get("created_by_email"),
        created_at=panel_row["created_at"],
        description=panel_row.get("description"),
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
                    gp.created_by::text AS created_by,
                    gp.created_at,
                    gp.description,
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
                gp.created_by::text AS created_by,
                gp.created_at,
                gp.description,
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


async def _fetch_panel_genes(
    session: AsyncSession,
    panel_ids: list[str],
) -> dict[str, list[str]]:
    if not panel_ids:
        return {}
    result = await session.execute(
        text(
            """
            SELECT panel_id::text AS panel_id, gene_symbol
            FROM gene_panel_genes
            WHERE panel_id IN :panel_ids
            ORDER BY gene_symbol
            """
        ).bindparams(uuid_list_bindparam("panel_ids")),
        {"panel_ids": uuid_values(panel_ids)},
    )
    grouped: dict[str, list[str]] = {panel_id: [] for panel_id in panel_ids}
    for row in result.mappings().all():
        grouped.setdefault(row["panel_id"], []).append(row["gene_symbol"])
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
            SELECT panel_id::text AS panel_id, gene, chr, start, "end"
            FROM gene_panel_regions
            WHERE panel_id IN :panel_ids
            ORDER BY gene, chr, start, "end"
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
    genes = await _fetch_panel_genes(session, panel_ids)
    regions = await _fetch_panel_regions(session, panel_ids)
    return [
        _panel_out_from_rows(row, genes.get(row["id"], []), regions.get(row["id"], []))
        for row in panel_rows
    ]


async def get_panel_or_404(
    session: AsyncSession,
    panel_id: str,
) -> GenePanelOut:
    _require_panel_uuid(panel_id)
    panel_rows = await _fetch_panel_rows(session, [panel_id])
    if not panel_rows:
        raise HTTPException(status_code=404, detail="Panel not found")
    genes = await _fetch_panel_genes(session, [panel_id])
    regions = await _fetch_panel_regions(session, [panel_id])
    return _panel_out_from_rows(panel_rows[0], genes.get(panel_id, []), regions.get(panel_id, []))


async def create_panel_data(
    session: AsyncSession,
    panel: GenePanelCreate,
    user: CurrentUser,
) -> GenePanelCreateResponse:
    _ensure_admin(user)
    existing = await session.execute(
        text("SELECT id FROM gene_panels WHERE name = :name"),
        {"name": panel.name},
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Panel already exists")

    normalized_symbols = [
        symbol.strip() for symbol in panel.genes if symbol and symbol.strip()
    ]
    deduped_symbols = list(dict.fromkeys(normalized_symbols))
    grouped_regions: dict[str, dict[str, Any]] = {}
    if deduped_symbols:
        result = await session.execute(
            text(
                f"""
                SELECT DISTINCT ON (upper(g.hgnc_symbol))
                    upper(g.hgnc_symbol) AS symbol_key,
                    g.hgnc_symbol,
                    g.chr,
                    g.start,
                    g."end" AS "end"
                FROM genes g
                JOIN assemblies a ON a.id = g.assembly_id
                WHERE upper(g.hgnc_symbol) IN :symbols
                ORDER BY
                    upper(g.hgnc_symbol),
                    {_ASSEMBLY_PRIORITY_SQL},
                    (g."end" - g.start) DESC,
                    a.release_date DESC NULLS LAST,
                    a.version DESC NULLS LAST,
                    g.start,
                    g."end"
                """
            ).bindparams(bindparam("symbols", expanding=True)),
            {"symbols": [symbol.upper() for symbol in deduped_symbols]},
        )
        for row in result.mappings().all():
            grouped_regions[str(row["symbol_key"])] = dict(row)

    missing_genes: list[str] = []
    regions: list[GeneLocation] = []
    for symbol in deduped_symbols:
        match = grouped_regions.get(symbol.upper())
        if match is None:
            missing_genes.append(symbol)
            continue
        regions.append(
            GeneLocation(
                gene=symbol,
                chr=match["chr"],
                start=int(match["start"]),
                end=int(match["end"]),
            )
        )

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
    panel_id = panel_row["id"]

    if deduped_symbols:
        await session.execute(
            text(
                """
                INSERT INTO gene_panel_genes (panel_id, gene_symbol)
                VALUES (CAST(:panel_id AS uuid), :gene_symbol)
                """
            ),
            [{"panel_id": panel_id, "gene_symbol": symbol} for symbol in deduped_symbols],
        )
    if regions:
        await session.execute(
            text(
                """
                INSERT INTO gene_panel_regions (panel_id, gene, chr, start, "end")
                VALUES (CAST(:panel_id AS uuid), :gene, :chr, :start, :end)
                """
            ),
            [
                {
                    "panel_id": panel_id,
                    "gene": region.gene,
                    "chr": region.chr,
                    "start": region.start,
                    "end": region.end,
                }
                for region in regions
            ],
        )

    await session.commit()
    panel_out = _panel_out_from_rows(
        panel_row,
        deduped_symbols,
        [
            {
                "gene": region.gene,
                "chr": region.chr,
                "start": region.start,
                "end": region.end,
            }
            for region in regions
        ],
    )
    message = f"Panel created with {len(regions)} of {len(deduped_symbols)} genes"
    if missing_genes:
        message += f"; missing genes: {', '.join(missing_genes)}"
    return GenePanelCreateResponse(
        panel=panel_out,
        message=message,
        missing_genes=missing_genes,
    )


async def delete_panel_data(
    session: AsyncSession,
    panel_id: str,
    user: CurrentUser,
) -> None:
    _ensure_admin(user)
    _require_panel_uuid(panel_id)
    result = await session.execute(
        text("DELETE FROM gene_panels WHERE id = CAST(:panel_id AS uuid)"),
        {"panel_id": panel_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Panel not found")
    await session.commit()
