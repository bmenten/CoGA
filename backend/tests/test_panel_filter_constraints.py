from __future__ import annotations

import pytest

from backend.app.services.clickhouse_family_variants import _fetch_panel_constraints


class _MappingResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def scalar_one_or_none(self) -> object | None:
        return self._rows[0] if self._rows else None


class _PanelConstraintSession:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement, params: dict[str, object]):
        sql = str(statement)
        self.statements.append(sql)
        if "FROM gene_panel_genes" in sql:
            return _MappingResult([{"gene_symbol": "GENE1"}])
        if "FROM gene_panel_regions" in sql:
            return _MappingResult([{"gene": "GENE1", "chr": "1", "start": 10, "end": 20}])
        if "FROM genes" in sql:
            return _MappingResult([{"chr": "2", "start": 100, "end": 200}])
        return _MappingResult([])


@pytest.mark.asyncio
async def test_panel_constraints_include_family_assembly_gene_regions() -> None:
    session = _PanelConstraintSession()

    constraints = await _fetch_panel_constraints(
        session,
        "d67e635c-7d98-4495-8b3c-153f5007561b",
        assembly_id="5ddde908-e97d-4f60-95cb-3a6f9f8173d3",
    )

    assert constraints.genes == ("GENE1",)
    assert [(region.chr, region.start, region.end) for region in constraints.regions] == [
        ("1", 10, 20),
        ("2", 100, 200),
    ]
    assert any("FROM genes" in statement for statement in session.statements)
    # #515: stored regions are read for the family's own assembly only.
    stored = next(s for s in session.statements if "FROM gene_panel_regions" in s)
    assert "assembly_id = CAST(:assembly_id AS uuid)" in stored


@pytest.mark.asyncio
async def test_panel_constraints_use_no_coordinates_without_an_assembly() -> None:
    # Without a resolved assembly no stored coordinates can be trusted — they belong to
    # some assembly, not necessarily the family's (#515). The panel narrows by gene alone.
    session = _PanelConstraintSession()

    constraints = await _fetch_panel_constraints(
        session,
        "d67e635c-7d98-4495-8b3c-153f5007561b",
    )

    assert constraints.genes == ("GENE1",)
    assert constraints.regions == ()
    assert not any("FROM gene_panel_regions" in statement for statement in session.statements)
    assert not any("FROM genes" in statement for statement in session.statements)
