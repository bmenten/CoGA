from __future__ import annotations

import asyncio
import json
import types
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from backend.app.schemas import GenePanelCreate, GenePanelUpdate, PanelAppImportRequest
from backend.app.services import panel_metadata_service as pms
from backend.app.services.panelapp_service import PanelAppImportContent


def _admin():
    return types.SimpleNamespace(role="admin", id=str(uuid.uuid4()), email="admin@x.org")


def test_jsonb_helpers_parse_list_dict_str_none() -> None:
    assert pms._jsonb_list(["A", "B"]) == ["A", "B"]
    assert pms._jsonb_list('["A","B"]') == ["A", "B"]
    assert pms._jsonb_list(None) == []
    assert pms._jsonb_list("not json") == []
    assert pms._jsonb_dict({"a": 1}) == {"a": 1}
    assert pms._jsonb_dict('{"a": 1}') == {"a": 1}
    assert pms._jsonb_dict(None) == {}


def test_mendeliome_predicates_are_causal_plus_associated() -> None:
    # The clinical choice: disease-causing + disease-associated, excluding risk modifiers.
    assert pms._MENDELIOME_PREDICATES == ("causes", "gene_associated_with_condition")


class _Result:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _Session:
    def __init__(self, row):
        self._row = row

    async def execute(self, *args, **kwargs):
        return _Result(self._row)


def test_update_panel_data_rejects_generated_panels() -> None:
    # Editing genes by hand is only for locally-curated panels.
    row = {
        "id": str(uuid.uuid4()),
        "name": "Mendeliome",
        "version": 3,
        "source": "mendeliome",
        "description": None,
    }
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            pms.update_panel_data(
                _Session(row), row["id"], GenePanelUpdate(genes=["BRCA1"]), _admin()
            )
        )
    assert excinfo.value.status_code == 400
    assert "generated/imported" in excinfo.value.detail


def test_update_panel_data_404_when_missing() -> None:
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            pms.update_panel_data(
                _Session(None), str(uuid.uuid4()), GenePanelUpdate(genes=["BRCA1"]), _admin()
            )
        )
    assert excinfo.value.status_code == 404


def test_regenerate_mendeliome_409_without_monarch(monkeypatch) -> None:
    async def _empty(session):
        return [], None

    monkeypatch.setattr(pms, "_select_mendeliome_genes", _empty)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(pms.regenerate_mendeliome(_Session(None), _admin()))
    assert excinfo.value.status_code == 409


def test_non_admin_cannot_regenerate_or_update() -> None:
    viewer = types.SimpleNamespace(role="viewer", id=str(uuid.uuid4()), email="v@x.org")
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(pms.regenerate_mendeliome(_Session(None), viewer))
    assert excinfo.value.status_code == 403
    with pytest.raises(HTTPException) as excinfo2:
        asyncio.run(
            pms.update_panel_data(
                _Session(None), str(uuid.uuid4()), GenePanelUpdate(genes=[]), viewer
            )
        )
    assert excinfo2.value.status_code == 403


# ---------------------------------------------------------------------------
# The version history (REQ-CARR-003): what is archived, when, and how it reads back.
# The same, on the real schema: integration/test_gene_panel_version_history.py.
# ---------------------------------------------------------------------------

GRCH38 = "11111111-1111-1111-1111-111111111111"
T2T = "22222222-2222-2222-2222-222222222222"
_ARCHIVED_AT = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
_ASSEMBLY_NAMES = {GRCH38: "GRCh38", T2T: "T2T-CHM13v2.0"}

# The gene reference `_resolve_gene_regions` reads: BRCA1 in two assemblies.
_REFERENCE_GENES = [
    ("BRCA1", GRCH38, "17", 43_044_295, 43_125_483),
    ("BRCA1", T2T, "17", 44_000_000, 44_100_000),
    ("TTN", GRCH38, "2", 178_525_989, 178_830_802),
    ("CFTR", GRCH38, "7", 117_480_025, 117_668_665),
]

# A parameter of an UPDATE of gene_panels, and the column it sets.
_UPDATED_COLUMN = {
    "version": "version",
    "description": "description",
    "name": "name",
    "external_version": "external_version",
    "release": "external_version",
    "external_url": "external_url",
    "source_updated_at": "source_updated_at",
    "now": "source_updated_at",
    "source_metadata": "source_metadata",
    "source_metadata_json": "source_metadata",
}


class _Rows:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = [dict(row) for row in rows or []]
        self._scalar = scalar

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict[str, Any]:
        assert len(self._rows) == 1
        return self._rows[0]

    def scalar(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _PanelDb:
    """The panel tables in memory, answering the statements the panel service sends.

    jsonb comes back as text, as asyncpg returns it without a codec, so what a version
    archives is read back through the service's own parsing. A statement this stand-in
    does not know fails the test rather than answering nothing.
    """

    def __init__(self, admin: Any, *, monarch: tuple[list[str], str] | None = None) -> None:
        self.emails = {admin.id: admin.email}
        self.monarch = monarch
        self.panels: dict[str, dict[str, Any]] = {}
        self.genes: dict[str, list[str]] = {}
        self.regions: dict[str, list[dict[str, Any]]] = {}
        self.versions: list[dict[str, Any]] = []
        self.statements: list[str] = []
        self.fail_on: str | None = None

    def writes(self, since: int = 0) -> list[str]:
        """The writes sent (from statement ``since`` on), each as its verb and table, and
        the commits."""
        writes = []
        for sql in self.statements[since:]:
            words = sql.split()
            if words[0] == "SELECT":
                continue
            writes.append(" ".join(words[:2] if words[0] == "UPDATE" else words[:3]))
        return writes

    def add_panel(self, *, name: str, source: str, version: int, genes: list[str]) -> str:
        panel_id = str(uuid.uuid4())
        self.panels[panel_id] = {
            "id": panel_id,
            "name": name,
            "description": None,
            "source": source,
            "external_id": None,
            "external_version": None,
            "external_url": None,
            "source_updated_at": None,
            "source_metadata": "{}",
            "version": version,
            "created_by": next(iter(self.emails)),
            "created_at": _ARCHIVED_AT,
        }
        self.genes[panel_id] = list(genes)
        self.regions[panel_id] = []
        return panel_id

    async def commit(self) -> None:
        self.statements.append("COMMIT")

    async def execute(self, statement: Any, params: Any = None) -> _Rows:
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if self.fail_on and sql.startswith(self.fail_on):
            raise RuntimeError(f"simulated failure: {self.fail_on}")
        return self._read(sql, params) if sql.startswith("SELECT") else self._write(sql, params)

    def _panel_row(self, panel_id: str) -> dict[str, Any]:
        row = dict(self.panels[panel_id])
        row["created_by_email"] = self.emails.get(row["created_by"])
        return row

    def _write(self, sql: str, params: Any) -> _Rows:
        if sql.startswith("INSERT INTO gene_panel_versions"):
            self.versions.append({**params, "created_at": _ARCHIVED_AT})
            return _Rows()
        if sql.startswith("INSERT INTO gene_panels"):
            panel_id = str(uuid.uuid4())
            self.panels[panel_id] = {
                "id": panel_id,
                "name": params["name"],
                "description": params.get("description"),
                "source": params.get("source") or ("panelapp" if "'panelapp'" in sql else "local"),
                "external_id": params.get("external_id"),
                "external_version": params.get("external_version"),
                "external_url": params.get("external_url"),
                "source_updated_at": params.get("source_updated_at"),
                "source_metadata": params.get("source_metadata") or params.get("source_metadata_json") or "{}",
                "version": 1,
                "created_by": params["created_by"],
                "created_at": params["created_at"],
            }
            return _Rows([self._panel_row(panel_id)])
        if sql.startswith("UPDATE gene_panels"):
            panel = self.panels[params["panel_id"]]
            for name, value in params.items():
                if name != "panel_id":
                    panel[_UPDATED_COLUMN[name]] = value
            return _Rows([self._panel_row(params["panel_id"])] if "RETURNING" in sql else [])
        if sql.startswith("DELETE FROM gene_panel_genes"):
            self.genes[params["panel_id"]] = []
            return _Rows()
        if sql.startswith("DELETE FROM gene_panel_regions"):
            self.regions[params["panel_id"]] = []
            return _Rows()
        if sql.startswith("INSERT INTO gene_panel_genes"):
            for row in params:
                self.genes.setdefault(row["panel_id"], []).append(row["gene_symbol"])
            return _Rows()
        if sql.startswith("INSERT INTO gene_panel_regions"):
            for row in params:
                self.regions.setdefault(row["panel_id"], []).append(
                    {key: row[key] for key in ("gene", "chr", "start", "end", "assembly_id")}
                )
            return _Rows()
        raise AssertionError(f"write the stand-in does not know: {sql}")

    def _read(self, sql: str, params: Any) -> _Rows:
        if "FROM gene_panel_versions" in sql:
            rows = [row for row in self.versions if row["panel_id"] == params["panel_id"]]
            if "AND version = :version" in sql:
                return _Rows([row for row in rows if row["version"] == params["version"]])
            if "ORDER BY version DESC" in sql:
                rows.sort(key=lambda row: row["version"], reverse=True)
            columns = ("version", "name", "source", "external_version", "gene_count", "created_by_email", "created_at")
            return _Rows([{column: row[column] for column in columns} for row in rows])
        if sql.startswith("SELECT version FROM gene_panels"):
            panel = self.panels.get(params["panel_id"])
            return _Rows(scalar=panel["version"] if panel else None)
        if "FROM genes g" in sql:
            wanted = set(params["symbols"])
            return _Rows(
                [
                    {
                        "symbol_key": symbol,
                        "assembly_id": assembly_id,
                        "assembly_name": _ASSEMBLY_NAMES[assembly_id],
                        "chr": chr_,
                        "start": start,
                        "end": end,
                    }
                    for symbol, assembly_id, chr_, start, end in _REFERENCE_GENES
                    if symbol in wanted
                ]
            )
        if sql.startswith("SELECT id::text FROM assemblies"):
            return _Rows(scalar=None)
        if sql.startswith("SELECT id::text FROM gene_panels WHERE name = :name"):
            clash = [
                panel_id
                for panel_id, panel in self.panels.items()
                if panel["name"] == params["name"] and panel_id != params.get("existing_panel_id")
            ]
            return _Rows(scalar=clash[0] if clash else None)
        if sql.startswith("SELECT id::text FROM gene_panels WHERE source = 'panelapp'"):
            found = [
                panel_id
                for panel_id, panel in self.panels.items()
                if panel["source"] == "panelapp" and panel["external_id"] == params["external_id"]
            ]
            return _Rows(scalar=found[0] if found else None)
        if sql.startswith("SELECT id::text AS id, version FROM gene_panels WHERE source = :source"):
            found = [panel for panel in self.panels.values() if panel["source"] == params["source"]]
            return _Rows([{"id": found[0]["id"], "version": found[0]["version"]}] if found else [])
        if sql.startswith("SELECT id::text AS id, name, version"):
            panel = self.panels.get(params["panel_id"])
            return _Rows([dict(panel)] if panel else [])
        if "FROM gene_panels gp" in sql:
            wanted_ids = {str(value) for value in params["panel_ids"]}
            return _Rows([self._panel_row(panel_id) for panel_id in self.panels if panel_id in wanted_ids])
        if "FROM gene_panel_genes" in sql:
            wanted_ids = {str(value) for value in params["panel_ids"]}
            return _Rows(
                [
                    {"panel_id": panel_id, "gene_symbol": symbol}
                    for panel_id in wanted_ids
                    for symbol in sorted(self.genes.get(panel_id, []))
                ]
            )
        if "FROM gene_panel_regions r" in sql:
            wanted_ids = {str(value) for value in params["panel_ids"]}
            return _Rows(
                [
                    {**region, "panel_id": panel_id, "assembly": _ASSEMBLY_NAMES[region["assembly_id"]]}
                    for panel_id in wanted_ids
                    for region in self.regions.get(panel_id, [])
                ]
            )
        if sql.startswith("SELECT DISTINCT gene_symbol FROM monarch_gene_disease"):
            genes = self.monarch[0] if self.monarch else []
            return _Rows([{"gene_symbol": symbol} for symbol in genes])
        if sql.startswith("SELECT max(release_version) FROM monarch_gene_disease"):
            return _Rows(scalar=self.monarch[1] if self.monarch else None)
        raise AssertionError(f"query the stand-in does not know: {sql}")


def _create(db: _PanelDb, admin: Any, genes: list[str], description: str | None = None):
    return asyncio.run(
        pms.create_panel_data(db, GenePanelCreate(name="Cardiac", genes=genes, description=description), admin)
    )


def _edit(db: _PanelDb, admin: Any, panel_id: str, genes: list[str], description: str | None = None):
    return asyncio.run(
        pms.update_panel_data(db, panel_id, GenePanelUpdate(genes=genes, description=description), admin)
    )


def test_creating_a_panel_archives_it_as_version_1() -> None:
    admin = _admin()
    db = _PanelDb(admin)

    out = _create(db, admin, [" TTN ", "BRCA1", "TTN", "NOTAGENE1"], description="  Curated  ")

    assert out.panel.version == 1
    [archived] = db.versions
    assert (archived["panel_id"], archived["version"], archived["name"]) == (out.panel.id, 1, "Cardiac")
    assert archived["description"] == "Curated"
    assert (archived["source"], archived["external_version"]) == ("local", None)
    # The genes as the panel holds them: trimmed, once each, in the order given, the one
    # without coordinates included.
    assert json.loads(archived["genes"]) == ["TTN", "BRCA1", "NOTAGENE1"]
    assert archived["gene_count"] == 3
    assert json.loads(archived["regions"]) == [
        {"gene": "TTN", "chr": "2", "start": 178_525_989, "end": 178_830_802, "assembly": "GRCh38"},
        {"gene": "BRCA1", "chr": "17", "start": 43_044_295, "end": 43_125_483, "assembly": "GRCh38"},
        {"gene": "BRCA1", "chr": "17", "start": 44_000_000, "end": 44_100_000, "assembly": "T2T-CHM13v2.0"},
    ]
    assert json.loads(archived["source_metadata"]) == {}
    assert (archived["created_by"], archived["created_by_email"]) == (admin.id, admin.email)
    # Archived in the transaction that creates the panel, before it is committed.
    assert db.writes() == [
        "INSERT INTO gene_panels",
        "DELETE FROM gene_panel_genes",
        "DELETE FROM gene_panel_regions",
        "INSERT INTO gene_panel_genes",
        "INSERT INTO gene_panel_regions",
        "INSERT INTO gene_panel_versions",
        "COMMIT",
    ]


def test_an_edit_archives_the_next_version_and_leaves_the_earlier_one_as_it_was() -> None:
    admin = _admin()
    db = _PanelDb(admin)
    created = _create(db, admin, ["TTN", "BRCA1"], description="Curated")
    first = dict(db.versions[0])
    before_edit = len(db.statements)

    out = _edit(db, admin, created.panel.id, ["TTN", "CFTR"])

    assert out.panel.version == 2
    assert out.message.startswith("Panel updated to version 2")
    assert db.panels[created.panel.id]["version"] == 2
    assert [entry["version"] for entry in db.versions] == [1, 2]
    assert db.versions[0] == first, "an archived version is never rewritten"
    second = db.versions[1]
    assert json.loads(second["genes"]) == ["TTN", "CFTR"]
    assert second["gene_count"] == 2
    # An edit that sends no description keeps the panel's.
    assert second["description"] == "Curated"
    assert db.writes(since=before_edit) == [
        "UPDATE gene_panels",
        "DELETE FROM gene_panel_genes",
        "DELETE FROM gene_panel_regions",
        "INSERT INTO gene_panel_genes",
        "INSERT INTO gene_panel_regions",
        "INSERT INTO gene_panel_versions",
        "COMMIT",
    ]


def test_an_edit_of_the_description_alone_is_a_new_version() -> None:
    admin = _admin()
    db = _PanelDb(admin)
    created = _create(db, admin, ["TTN", "BRCA1"], description="Curated")

    out = _edit(db, admin, created.panel.id, ["BRCA1", "TTN"], description="Curated, reviewed")

    assert out.panel.version == 2
    assert [entry["version"] for entry in db.versions] == [1, 2]
    assert db.versions[1]["description"] == "Curated, reviewed"
    assert json.loads(db.versions[1]["genes"]) == ["BRCA1", "TTN"]


def test_an_edit_that_changes_nothing_archives_nothing() -> None:
    admin = _admin()
    db = _PanelDb(admin)
    created = _create(db, admin, ["TTN", "BRCA1"])
    before_edit = len(db.statements)

    # The same genes, in another order, once more and padded.
    out = _edit(db, admin, created.panel.id, ["BRCA1", " TTN", "BRCA1"])

    assert out.message == "No changes — panel already at version 1."
    assert out.panel.version == 1
    assert len(db.versions) == 1
    assert db.writes(since=before_edit) == []


def test_a_panel_change_whose_version_cannot_be_archived_is_not_committed() -> None:
    admin = _admin()
    db = _PanelDb(admin)
    db.fail_on = "INSERT INTO gene_panel_versions"
    with pytest.raises(RuntimeError):
        _create(db, admin, ["TTN"])
    assert "COMMIT" not in db.writes()

    db = _PanelDb(admin)
    created = _create(db, admin, ["TTN"])
    before_edit = len(db.statements)
    db.fail_on = "INSERT INTO gene_panel_versions"
    with pytest.raises(RuntimeError):
        _edit(db, admin, created.panel.id, ["TTN", "CFTR"])
    assert "COMMIT" not in db.writes(since=before_edit)


def test_the_history_lists_each_version_newest_first_and_each_reads_back() -> None:
    admin = _admin()
    db = _PanelDb(admin)
    panel_id = _create(db, admin, ["TTN", "BRCA1"]).panel.id
    _edit(db, admin, panel_id, ["TTN", "BRCA1", "CFTR"])
    _edit(db, admin, panel_id, ["CFTR"], description="Narrowed")

    # A request may name the panel in braces and capitals: the same panel, its id canonical.
    history = asyncio.run(pms.list_panel_versions(db, "{" + panel_id.upper() + "}"))

    assert history.panel_id == panel_id
    assert history.current_version == 3
    assert [(entry.version, entry.gene_count) for entry in history.versions] == [(3, 1), (2, 3), (1, 2)]
    assert {entry.created_by_email for entry in history.versions} == {admin.email}
    # Each version the history lists reads back as that version, with its own genes.
    genes = {}
    for entry in history.versions:
        detail = asyncio.run(pms.get_panel_version(db, panel_id, entry.version))
        assert (detail.version, detail.name, detail.gene_count, detail.created_at) == (
            entry.version,
            entry.name,
            entry.gene_count,
            entry.created_at,
        )
        genes[detail.version] = detail.genes
    assert genes == {1: ["TTN", "BRCA1"], 2: ["TTN", "BRCA1", "CFTR"], 3: ["CFTR"]}

    first = asyncio.run(pms.get_panel_version(db, panel_id, 1))
    assert [(region.gene, region.assembly, region.chr, region.start, region.end) for region in first.regions] == [
        ("TTN", "GRCh38", "2", 178_525_989, 178_830_802),
        ("BRCA1", "GRCh38", "17", 43_044_295, 43_125_483),
        ("BRCA1", "T2T-CHM13v2.0", "17", 44_000_000, 44_100_000),
    ]
    assert first.description is None
    last = asyncio.run(pms.get_panel_version(db, panel_id, 3))
    assert (last.description, last.source, last.source_metadata) == ("Narrowed", "local", {})


def test_an_unknown_panel_or_version_is_404_and_an_id_that_is_no_uuid_400() -> None:
    admin = _admin()
    db = _PanelDb(admin)
    panel_id = _create(db, admin, ["TTN"]).panel.id

    def _refusal(call) -> tuple[int, str]:
        with pytest.raises(HTTPException) as refused:
            asyncio.run(call())
        return refused.value.status_code, refused.value.detail

    assert _refusal(lambda: pms.get_panel_version(db, panel_id, 2)) == (404, "Panel version not found")
    assert _refusal(lambda: pms.list_panel_versions(db, str(uuid.uuid4()))) == (404, "Panel not found")
    assert _refusal(lambda: pms.list_panel_versions(db, "panel-1")) == (400, "Invalid panel id")
    assert _refusal(lambda: pms.get_panel_version(db, "panel-1", 1)) == (400, "Invalid panel id")


def test_the_mendeliome_is_archived_when_built_and_again_only_when_its_genes_change() -> None:
    admin = _admin()
    db = _PanelDb(admin, monarch=(["BRCA1", "TTN"], "2026-07-01"))

    built = asyncio.run(pms.regenerate_mendeliome(db, admin))

    panel_id = built.panel.id
    assert (built.version, built.changed) == (1, True)
    [first] = db.versions
    assert (first["version"], first["source"], first["external_version"]) == (1, "mendeliome", "2026-07-01")
    assert json.loads(first["genes"]) == ["BRCA1", "TTN"]
    assert json.loads(first["source_metadata"])["monarch_release"] == "2026-07-01"

    # A new Monarch release with the same genes re-stamps the panel and archives nothing.
    db.monarch = (["BRCA1", "TTN"], "2026-09-01")
    same = asyncio.run(pms.regenerate_mendeliome(db, admin))
    assert (same.version, same.changed) == (1, False)
    assert len(db.versions) == 1
    assert (db.panels[panel_id]["version"], db.panels[panel_id]["external_version"]) == (1, "2026-09-01")

    # Forced, the same genes are a new version.
    forced = asyncio.run(pms.regenerate_mendeliome(db, admin, force=True))
    assert (forced.version, forced.changed) == (2, True)

    # Other genes are a new version that holds them.
    db.monarch = (["BRCA1", "CFTR", "TTN"], "2026-10-01")
    grown = asyncio.run(pms.regenerate_mendeliome(db, admin))
    assert (grown.version, grown.changed) == (3, True)
    assert db.panels[panel_id]["version"] == 3
    assert [entry["version"] for entry in db.versions] == [1, 2, 3]
    assert json.loads(db.versions[-1]["genes"]) == ["BRCA1", "CFTR", "TTN"]
    assert db.versions[-1]["external_version"] == "2026-10-01"


@pytest.mark.parametrize("source", ["mendeliome", "panelapp"])
def test_a_generated_or_imported_panel_is_never_edited_into_a_version(source: str) -> None:
    admin = _admin()
    db = _PanelDb(admin)
    panel_id = db.add_panel(name="Generated", source=source, version=4, genes=["BRCA1"])

    with pytest.raises(HTTPException) as refused:
        _edit(db, admin, panel_id, ["BRCA1", "TTN"])

    assert refused.value.status_code == 400
    assert db.versions == []
    assert db.writes() == []
    assert (db.panels[panel_id]["version"], db.genes[panel_id]) == (4, ["BRCA1"])


def test_a_panelapp_import_keeps_panelapps_version_and_archives_none(monkeypatch) -> None:
    """Today's behaviour, reported for QA under REG-1 (REQ-CARR-003).

    A PanelApp panel carries PanelApp's version (``external_version``) only: an import
    writes no ``gene_panel_versions`` row and never raises the panel's own version, so a
    re-import that changes the genes keeps version 1 and the version history stays empty.
    If imports come to be archived, this test changes with that decision.
    """
    admin = _admin()
    db = _PanelDb(admin)
    imported_genes = ["BRCA1", "TTN"]

    async def _fetch(panelapp_id, version=None):
        return {"id": panelapp_id, "name": "Hereditary cancer", "version": "3.1"}

    monkeypatch.setattr(pms, "fetch_panelapp_panel", _fetch)
    monkeypatch.setattr(
        pms,
        "extract_panelapp_import_content",
        lambda payload, **kwargs: PanelAppImportContent(genes=list(imported_genes), regions=[], metadata={}),
    )

    first = asyncio.run(pms.import_panelapp_panel_data(db, PanelAppImportRequest(panelapp_id=245), admin))
    imported_genes.append("CFTR")  # say, amber genes taken in as well
    again = asyncio.run(
        pms.import_panelapp_panel_data(
            db, PanelAppImportRequest(panelapp_id=245, confidence_levels=["3", "2"]), admin
        )
    )

    assert again.panel.id == first.panel.id
    assert (first.panel.genes, again.panel.genes) == (["BRCA1", "TTN"], ["BRCA1", "TTN", "CFTR"])
    assert (first.panel.version, again.panel.version) == (1, 1)
    assert again.panel.external_version == "3.1"
    assert db.versions == []
    history = asyncio.run(pms.list_panel_versions(db, first.panel.id))
    assert (history.current_version, history.versions) == (1, [])
    with pytest.raises(HTTPException) as missing:
        asyncio.run(pms.get_panel_version(db, first.panel.id, 1))
    assert missing.value.status_code == 404
