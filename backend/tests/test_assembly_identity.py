"""An assembly is its name: a re-import of hg38 never makes a second GRCh38.

The ClickHouse variant tables and every lookup by name read the assembly's name alone, so
``assemblies.assembly_name`` is unique. An import from UCSC keeps the assembly of that name
and writes the version (the GRC patch, such as p14) and release date it states; Admin → Add
assembly refuses a name that exists. The import runs against an in-memory ``assemblies``
table that answers the upsert as Postgres does.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.schemas import ReferenceImportSourceAssemblyOut
from backend.app.services import metadata_service
from backend.app.services import reference_source_service as rss

REPO_ROOT = Path(__file__).resolve().parents[2]
HUMAN = "22222222-2222-2222-2222-222222222222"
OTHER_SPECIES = "33333333-3333-3333-3333-333333333333"


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict[str, Any]:
        assert len(self._rows) == 1
        return self._rows[0]


class _AssembliesTable:
    """The species and assemblies tables, as far as an import from UCSC and Add assembly
    reach them; ``assembly_name`` is unique, as in the baseline."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = [dict(row) for row in rows]

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        params = params or {}
        if "JOIN assemblies a ON a.species_id = s.id" in sql:
            # _find_human_grch38_assembly: the human GRCh38, if there is one.
            return _Result(
                [
                    {**row, "assembly_id": row["id"], "species_name": "Homo sapiens"}
                    for row in self.named("GRCh38")
                    if row["species_id"] == HUMAN
                ]
            )
        if "FROM species" in sql:
            return _Result([{"id": HUMAN, "name": "Homo sapiens", "common_name": "human", "tax_id": 9606}])
        if sql.startswith("INSERT INTO assemblies"):
            held = next((row for row in self.rows if row["assembly_name"] == params["assembly_name"]), None)
            if held is None:
                row = {**params, "id": f"assembly-{len(self.rows) + 1}"}
                self.rows.append(row)
                return _Result([{**row, "created": True}])
            if "DO UPDATE" in sql and held["species_id"] == params["species_id"]:
                held.update(version=params["version"], release_date=params["release_date"])
                return _Result([{"id": held["id"], "created": False}])
            return _Result([])
        raise AssertionError(f"Unexpected SQL: {sql}")

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def named(self, assembly_name: str) -> list[dict[str, Any]]:
        return [row for row in self.rows if row["assembly_name"] == assembly_name]


def _patch_ucsc(monkeypatch: pytest.MonkeyPatch, *, patch_release: str) -> None:
    """UCSC's catalogue and genome record for hg38, describing it as GRCh38.<patch_release>;
    no file is downloaded and no dataset is written."""

    async def catalogue(*, tax_id: int) -> list[ReferenceImportSourceAssemblyOut]:
        return [
            ReferenceImportSourceAssemblyOut(
                scientific_name="Homo sapiens",
                common_name="human",
                tax_id=tax_id,
                ucsc_genome="hg38",
                assembly_name="GRCh38",
                assembly_version="hg38",
                release_date=date(2013, 12, 1),
                description="Dec. 2013 (GRCh38/hg38)",
                source_name="Genome Reference Consortium",
                gene_source="UCSC gene tables",
            )
        ]

    async def genome_record(_client: Any, *, ucsc_genome: str) -> dict[str, str]:
        return {"description": f"GRCh38 Genome Reference Consortium GCF_000001405.40_GRCh38.{patch_release}"}

    async def no_file(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def no_gene_table(*_args: Any, **_kwargs: Any) -> None:
        raise rss.HTTPException(status_code=404, detail="no gene table in this test")

    async def cytobands(_client: Any, *, ucsc_genome: str) -> rss.CytobandFile:
        return rss.CytobandFile("chr1\t0\t100\tp36.33\tgneg\n", "https://example.org/cytoBandIdeo.txt.gz")

    async def apply(*_args: Any, **_kwargs: Any) -> Any:
        return type("Result", (), {"inserted": 1, "replaced": True})()

    monkeypatch.setattr(rss, "list_reference_source_assemblies", catalogue)
    monkeypatch.setattr(rss, "_resolve_find_genome_record", genome_record)
    monkeypatch.setattr(rss, "_download_cytobands", cytobands)
    monkeypatch.setattr(rss, "_download_gencode_genes", no_file)
    monkeypatch.setattr(rss, "_download_genes", no_gene_table)
    monkeypatch.setattr(rss, "apply_reference_dataset_text", apply)


def _grch38(version: str, *, species_id: str = HUMAN) -> dict[str, Any]:
    return {
        "id": "assembly-1",
        "species_id": species_id,
        "assembly_name": "GRCh38",
        "version": version,
        "release_date": date(2013, 12, 1),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("held_version", "why"),
    [
        ("p14", "UCSC describes hg38 by a later patch than the one loaded"),
        ("hg38", "the first start could not reach UCSC and made the GRCh38 shell"),
    ],
)
async def test_a_reimport_of_hg38_keeps_the_one_grch38_and_takes_the_patch_ucsc_states(
    monkeypatch: pytest.MonkeyPatch, held_version: str, why: str
) -> None:
    _patch_ucsc(monkeypatch, patch_release="p15")
    table = _AssembliesTable([_grch38(held_version)])

    result = await rss.import_reference_from_ucsc(
        table,  # type: ignore[arg-type]
        tax_id=9606,
        ucsc_genome="hg38",
        overwrite=True,
    )

    assert result.created_assembly is False, why
    assert result.assembly_id == "assembly-1"
    assert result.assembly_version == "p15"
    assert [(row["id"], row["version"]) for row in table.named("GRCh38")] == [("assembly-1", "p15")]


@pytest.mark.asyncio
async def test_a_start_completes_the_grch38_shell_and_takes_the_patch_ucsc_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ucsc(monkeypatch, patch_release="p14")

    async def nothing_loaded(*_args: Any, **_kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(rss, "_assembly_dataset_count", nothing_loaded)
    table = _AssembliesTable([_grch38("hg38")])

    result = await rss.import_reference_from_ucsc(
        table,  # type: ignore[arg-type]
        tax_id=9606,
        ucsc_genome="hg38",
        overwrite=False,
        missing_only=True,
    )

    assert (result.assembly_id, result.created_assembly, result.assembly_version) == ("assembly-1", False, "p14")
    assert [(row["id"], row["version"]) for row in table.named("GRCh38")] == [("assembly-1", "p14")]


@pytest.mark.asyncio
async def test_a_first_import_of_hg38_creates_grch38_with_the_patch_ucsc_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ucsc(monkeypatch, patch_release="p14")
    table = _AssembliesTable([])

    result = await rss.import_reference_from_ucsc(
        table,  # type: ignore[arg-type]
        tax_id=9606,
        ucsc_genome="hg38",
        overwrite=True,
    )

    assert result.created_assembly is True
    assert [row["version"] for row in table.named("GRCh38")] == ["p14"]


@pytest.mark.asyncio
async def test_an_import_under_a_name_another_species_holds_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ucsc(monkeypatch, patch_release="p15")
    table = _AssembliesTable([_grch38("p14", species_id=OTHER_SPECIES)])

    with pytest.raises(HTTPException) as refused:
        await rss.import_reference_from_ucsc(
            table,  # type: ignore[arg-type]
            tax_id=9606,
            ucsc_genome="hg38",
            overwrite=True,
        )

    assert refused.value.status_code == 409
    assert table.rows == [_grch38("p14", species_id=OTHER_SPECIES)]


@pytest.mark.asyncio
async def test_add_assembly_refuses_a_name_that_exists_under_another_version() -> None:
    table = _AssembliesTable([_grch38("p14")])

    with pytest.raises(HTTPException) as refused:
        await metadata_service.create_assembly_record(
            table,  # type: ignore[arg-type]
            species_id=HUMAN,
            assembly_name="GRCh38",
            version="p15",
            release_date=date(2013, 12, 1),
        )

    assert refused.value.status_code == 409
    assert refused.value.detail == "An assembly named GRCh38 already exists"
    assert table.rows == [_grch38("p14")]


def test_the_baseline_makes_the_assembly_name_unique() -> None:
    baseline = (REPO_ROOT / "backend/db/schema/postgres/01_access.sql").read_text()
    table = re.search(r"CREATE TABLE IF NOT EXISTS assemblies \((.*?)\n\);", baseline, re.S)
    assert table is not None
    assert re.search(r"^\s+assembly_name TEXT NOT NULL UNIQUE,$", table.group(1), re.M)
    assert "UNIQUE (" not in table.group(1)
