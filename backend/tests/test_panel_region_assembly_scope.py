"""Gene-panel coordinates are resolved and stored per assembly (#515, TF-06 H12).

A panel is a gene list; its coordinates depend on the assembly. Before, one region per gene
was kept across all assemblies (GRCh38 first, else T2T) with no assembly recorded, and every
family's panel filter used them. The real-database behaviour (storage, the family filter and
the schema upgrade) is covered by ``integration/test_panel_regions_per_assembly.py``.
"""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

from backend.app.schemas import GeneLocation, PanelAppImportRequest
from backend.app.services import panel_metadata_service as pms
from backend.app.services.panelapp_service import PanelAppImportContent

GRCH38 = "11111111-1111-1111-1111-111111111111"
T2T = "22222222-2222-2222-2222-222222222222"
PANEL = "33333333-3333-3333-3333-333333333333"


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]

    def scalar_one_or_none(self):
        return self._scalar


def _gene_row(symbol, assembly_id, assembly_name, chr_, start, end):
    return {
        "symbol_key": symbol.upper(),
        "assembly_id": assembly_id,
        "assembly_name": assembly_name,
        "chr": chr_,
        "start": start,
        "end": end,
    }


_GENE_ROWS = [
    # Deliberately T2T first: the result must still list GRCh38 first.
    _gene_row("BRCA1", T2T, "T2T-CHM13v2.0", "17", 44_000_000, 44_100_000),
    _gene_row("BRCA1", GRCH38, "GRCh38", "17", 43_044_295, 43_125_483),
    _gene_row("TTN", GRCH38, "GRCh38", "2", 178_525_989, 178_830_802),
]


class _PanelSession:
    """Answers the SQL a panel create/import issues; records the region inserts."""

    def __init__(self, *, loaded_assemblies=None) -> None:
        self.loaded = loaded_assemblies or {"GRCh38": GRCH38, "T2T-CHM13v2.0": T2T}
        self.inserted_regions: list[dict] = []
        self.panel_row = {
            "id": PANEL,
            "name": "PanelApp 245",
            "version": 1,
            "created_by": "44444444-4444-4444-4444-444444444444",
            "created_at": datetime(2026, 9, 28, tzinfo=timezone.utc),
            "description": "Imported",
            "source": "panelapp",
            "external_id": "245",
            "external_version": "3.1",
            "external_url": "https://panelapp.example/245",
            "source_updated_at": None,
            "source_metadata": {},
            "created_by_email": "admin@example.org",
        }

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM assemblies WHERE assembly_name" in sql:
            return _Result(scalar=self.loaded.get(params["name"]))
        if "FROM genes g" in sql:
            wanted = set(params["symbols"])
            return _Result(rows=[row for row in _GENE_ROWS if row["symbol_key"] in wanted])
        if "INSERT INTO gene_panel_regions" in sql:
            self.inserted_regions.extend(params)
            return _Result()
        if "RETURNING" in sql or "FROM gene_panels gp" in sql:
            return _Result(rows=[self.panel_row])
        return _Result()

    async def commit(self) -> None:
        return None


def test_a_gene_is_resolved_in_every_assembly_that_has_it() -> None:
    regions, missing = asyncio.run(pms._resolve_gene_regions(_PanelSession(), ["BRCA1", "TTN", "NOPE"]))

    assert missing == ["NOPE"]
    assert [(r.gene, r.assembly, r.chr, r.start) for r in regions] == [
        ("BRCA1", "GRCh38", "17", 43_044_295),
        ("BRCA1", "T2T-CHM13v2.0", "17", 44_000_000),
        ("TTN", "GRCh38", "2", 178_525_989),
    ]
    assert all(region.assembly_id for region in regions)
    # Counts speak of genes, not of per-assembly rows.
    assert pms._resolved_gene_count(regions) == 2


def test_regions_are_stored_with_their_assembly() -> None:
    session = _PanelSession()
    regions, _ = asyncio.run(pms._resolve_gene_regions(session, ["BRCA1"]))
    unscoped = GeneLocation(gene="LOOSE", chr="1", start=1, end=2)

    asyncio.run(
        pms._replace_panel_members(session, panel_id=PANEL, genes=["BRCA1"], regions=[*regions, unscoped])
    )

    assert {(row["gene"], row["assembly_id"]) for row in session.inserted_regions} == {
        ("BRCA1", GRCH38),
        ("BRCA1", T2T),
    }, "a region without an assembly cannot be scoped, so it is not stored"


def _import(monkeypatch, session, *, assembly="GRCh38"):
    content = PanelAppImportContent(
        genes=["BRCA1", "TTN"],
        regions=[
            # PanelApp's own coordinates for a gene, and a region entity with no gene record.
            GeneLocation(gene="BRCA1", chr="17", start=43_000_000, end=43_200_000),
            GeneLocation(gene="ISCA-37404-Loss", chr="22", start=18_900_000, end=21_500_000),
        ],
        metadata={},
    )

    async def _fetch(panelapp_id, version=None):
        return {"id": 245, "name": "Hereditary cancer", "version": "3.1"}

    monkeypatch.setattr(pms, "fetch_panelapp_panel", _fetch)
    monkeypatch.setattr(pms, "extract_panelapp_import_content", lambda payload, **kwargs: content)
    request = PanelAppImportRequest(panelapp_id=245, assembly=assembly)
    user = types.SimpleNamespace(id="44444444-4444-4444-4444-444444444444", email="a@x.org", role="admin")
    return asyncio.run(pms.import_panelapp_panel_data(session, request, user))


def test_panelapp_coordinates_are_kept_for_the_requested_assembly_only(monkeypatch) -> None:
    session = _PanelSession()
    out = _import(monkeypatch, session)

    stored = {(row["gene"], row["assembly_id"], row["start"]) for row in session.inserted_regions}
    assert stored == {
        # PanelApp's GRCh38 coordinates win for GRCh38 ...
        ("BRCA1", GRCH38, 43_000_000),
        ("ISCA-37404-Loss", GRCH38, 18_900_000),
        # ... every other gene/assembly pair comes from the gene reference.
        ("BRCA1", T2T, 44_000_000),
        ("TTN", GRCH38, 178_525_989),
    }
    assert out.missing_genes == []
    assert "not kept" not in out.message


def test_panelapp_coordinates_for_an_unloaded_assembly_are_not_kept(monkeypatch) -> None:
    # GRCh37 coordinates apply to no family here; stored assembly-less they used to be
    # applied to every family's filter.
    session = _PanelSession()
    out = _import(monkeypatch, session, assembly="GRCh37")

    stored = {(row["gene"], row["assembly_id"]) for row in session.inserted_regions}
    assert stored == {("BRCA1", GRCH38), ("BRCA1", T2T), ("TTN", GRCH38)}
    assert "GRCh37 coordinates were not kept" in out.message
