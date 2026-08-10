"""Outbound gene-lookup request safety (#336).

The original defect: `urllib`'s `quote()` defaults to `safe='/'`, so a `/` or `../` in a
gene symbol passed through unescaped and forged the request PATH on the fixed external
hosts (genenames / ensembl / clinicalgenome). Those four per-gene lookups have since
been removed entirely — the bulk HGNC, GENCODE and ClinGen files supply the same fields
without a request — which retires that whole class of bug rather than guarding it.

NCBI is the one per-gene lookup left, and it is structurally immune: the symbol goes in
a query parameter that the HTTP client encodes, never into the URL path. These tests
hold that line, so a future rewrite that interpolates a symbol into the path fails here.
"""
from __future__ import annotations

import pytest

import backend.app.services.gene_info_external as gene_info_external


class _FakeResponse:
    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload or {"esearchresult": {"idlist": []}}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def captured_requests(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    async def fake_request(method: str, url: str, **kwargs):
        calls.append((url, kwargs.get("params") or {}))
        return _FakeResponse()

    monkeypatch.setattr(gene_info_external, "resilient_request", fake_request)
    return calls


@pytest.mark.asyncio
async def test_ncbi_lookup_keeps_the_symbol_out_of_the_url_path(
    captured_requests: list[tuple[str, dict]],
) -> None:
    await gene_info_external.fetch_ncbi_gene("BRCA1/../admin", "Homo sapiens")

    url, params = captured_requests[-1]
    # The endpoint is fixed and the symbol travels as a parameter, so a '/' in it cannot
    # add a path segment however hostile the symbol is.
    assert url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    assert "BRCA1/../admin" not in url
    assert params["term"] == "BRCA1/../admin[sym] AND Homo sapiens[orgn]"


@pytest.mark.asyncio
async def test_ncbi_lookup_keeps_the_species_out_of_the_url_path(
    captured_requests: list[tuple[str, dict]],
) -> None:
    await gene_info_external.fetch_ncbi_gene("TP53", "Homo sapiens/../x")

    url, params = captured_requests[-1]
    assert url.endswith("/esearch.fcgi")
    assert params["term"].endswith("Homo sapiens/../x[orgn]")


@pytest.mark.asyncio
async def test_ncbi_lookup_stops_when_no_gene_matches(
    captured_requests: list[tuple[str, dict]],
) -> None:
    # An empty id list must not lead to a second request built from a missing id.
    result = await gene_info_external.fetch_ncbi_gene("NOT_A_GENE", "Homo sapiens")

    assert result == {}
    assert len(captured_requests) == 1


@pytest.mark.asyncio
async def test_removed_per_gene_lookups_are_gone(
    captured_requests: list[tuple[str, dict]],
) -> None:
    """The HGNC/Ensembl/ClinGen per-gene lookups must not come back unnoticed.

    They were removed because they contributed almost nothing (see the sync measurements
    in the module comment); reintroducing one would also reintroduce the path-forging
    surface these tests were written for.
    """
    for name in (
        "fetch_hgnc_gene",
        "fetch_ensembl_gene",
        "fetch_ensembl_homologies",
        "fetch_clingen_gene",
        "parse_clingen_gene_page",
        "normalize_homologs",
    ):
        assert not hasattr(gene_info_external, name), f"{name} should have been removed"
