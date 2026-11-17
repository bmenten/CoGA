"""The family's phenotype score for one gene (GET /families/{id}/gene-phenotype-score).

The ACMG dialog reads it for PP4 and for the phenotype part of the de novo criteria (PS2, PM6)
of a variant no phenotype-prioritised ranking scored. It is the score the ranking gives the gene
(``_rank_small_variants``): the gene's Monarch phenotypes against the affected members' present
HPO terms, rounded alike, with the patient terms it matched best and their labels.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import clickhouse_family_variants as family_variants
from backend.app.services.monarch_phenotype_score import GenePhenotypeScore

TERMS = (["HP:0001250", "HP:0001263"], {"HP:0001250": "Seizure", "HP:0001263": "Global developmental delay"})
CONTEXT = SimpleNamespace(family_uuid="family-uuid", family_id="FAM1")


def _patch(monkeypatch: pytest.MonkeyPatch, *, terms=TERMS, scores: dict[str, GenePhenotypeScore] | None = None):
    """The affected members' terms and the scorer's answer; ``asked`` records each scoring."""
    asked: list[dict[str, Any]] = []

    async def affected_terms(_session: Any, context: Any):
        assert context is CONTEXT
        return terms

    async def score(_session: Any, *, gene_symbols, patient_hpo_ids):
        asked.append({"genes": list(gene_symbols), "terms": list(patient_hpo_ids)})
        return scores or {}

    monkeypatch.setattr(family_variants, "_affected_present_hpo", affected_terms)
    monkeypatch.setattr(family_variants, "score_genes_for_hpo", score)
    return asked


def _score(symbol: str):
    return asyncio.run(family_variants.family_gene_phenotype_score(None, CONTEXT, symbol=symbol))  # type: ignore[arg-type]


def test_the_score_is_the_rankings_for_the_gene_with_the_terms_it_matched(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _patch(
        monkeypatch,
        scores={"SCN2A": GenePhenotypeScore(score=0.723456, matched=[{"hpo_id": "HP:0001250", "label": None, "ic": 4.2}])},
    )

    scored = _score(" SCN2A ")

    assert asked == [{"genes": ["SCN2A"], "terms": TERMS[0]}]
    assert scored.model_dump() == {
        "symbol": "SCN2A",
        "score": 0.7235,
        "hpo_count": 2,
        "matched": [{"hpo_id": "HP:0001250", "label": "Seizure"}],
    }


def test_no_score_without_present_terms_or_monarch_phenotypes_for_the_gene(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not assessed, never a score of 0: the dialog then leaves the de novo strength to the analyst.
    asked = _patch(monkeypatch, terms=([], {}))
    assert _score("SCN2A").model_dump() == {"symbol": "SCN2A", "score": None, "hpo_count": 0, "matched": []}
    assert asked == []

    _patch(monkeypatch, scores={})
    assert _score("NOPHENO").model_dump() == {"symbol": "NOPHENO", "score": None, "hpo_count": 2, "matched": []}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    """A viewer's client of the app, the family's context and the request audit rows the test's."""
    from fastapi.testclient import TestClient

    from backend.app.core.postgres import get_postgres_session
    from backend.app.dependencies import get_current_user
    from backend.app.main import app
    from backend.app.middleware import request_logging
    from backend.app.routers import families as families_router
    from backend.app.services.access_control import CurrentUser

    viewer = CurrentUser(
        id="00000000-0000-4000-8000-0000000000c1",
        username="viewer@example.org",
        email="viewer@example.org",
        role="viewer",
        created_at=datetime.now(timezone.utc),
    )
    contexts: list[dict[str, Any]] = []

    async def context(_session: Any, **kwargs: Any):
        contexts.append(kwargs)
        return CONTEXT

    async def override_session():
        yield None

    async def override_user():
        return viewer

    async def keep_audit_row(_payload: Any) -> None:
        return None

    monkeypatch.setattr(families_router, "build_family_metadata_context", context)
    monkeypatch.setattr(request_logging, "write_audit_log_event", keep_audit_row)
    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_postgres_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    app.state.skip_startup_tasks = True
    try:
        with TestClient(app) as test_client:
            yield test_client, contexts, viewer
    finally:
        app.dependency_overrides = original_overrides
        app.state.skip_startup_tasks = False


def test_the_route_scores_the_gene_in_the_family_the_caller_may_see(client, monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, contexts, viewer = client
    _patch(monkeypatch, scores={"SCN2A": GenePhenotypeScore(score=0.5, matched=[])})

    response = test_client.get("/api/families/FAM1/gene-phenotype-score", params={"symbol": "SCN2A"})

    assert response.status_code == 200
    assert response.json() == {"symbol": "SCN2A", "score": 0.5, "hpo_count": 2, "matched": []}
    # The family is read through its context, which answers 404 for a family outside the
    # caller's projects.
    assert contexts == [{"family_identifier": "FAM1", "user": viewer}]


def test_the_route_asks_for_a_gene(client) -> None:
    test_client, contexts, _viewer = client

    assert test_client.get("/api/families/FAM1/gene-phenotype-score").status_code == 422
    assert test_client.get("/api/families/FAM1/gene-phenotype-score", params={"symbol": "G" * 65}).status_code == 422
    assert contexts == []
