from __future__ import annotations

import asyncio
import types
import uuid

import pytest
from fastapi import HTTPException

from backend.app.schemas import GenePanelUpdate
from backend.app.services import panel_metadata_service as pms


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
