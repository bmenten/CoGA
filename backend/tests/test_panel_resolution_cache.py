"""A panel's resolution is kept while nothing it is read from changes (ENG-14).

A panel filter applies the panel's genes, its regions stored for the family's assembly, and
each gene's loci in that assembly's gene reference (``_fetch_panel_constraints``). For the
Mendeliome that is 5,300 genes and, in a gene reference of one row per transcript, some
40,000 loci, and the digest of them that the ranking cache keys on: read again on every
Mendeliome request, and in the check that serves a sub-panel from the Mendeliome's ranking
(CLIN-27), it cost more than computing a small sub-panel afresh.

A resolution is now kept per panel and assembly, and used only while its token is
unchanged: the panel's version, a SHA-256 of its gene rows and of its region rows in the
assembly, and the gene-reference imports recorded for the assembly. These tests drive
``_fetch_panel_constraints`` over an in-memory stand-in for those tables that answers the
token as Postgres computes it (``test_panel_resolution_cache_integration.py`` runs the real
statement):

* resolved again unchanged, the panel is served from its token alone, the same resolution;
* a panel edit, a change of its gene or region rows without a new version, and a
  gene-reference load each read the panel again;
* a resolution during which a change was committed is not kept;
* each assembly keeps its own resolution, a deleted panel still answers 404, at most
  ``_PANEL_RESOLUTION_CACHE_SIZE`` resolutions are kept, the most recently used;
* the digest of a kept resolution is kept with it, and a kept resolution cannot be changed;
* the gene-reference lookup binds its terms as one array, however many there are (asyncpg
  takes at most 32,767 parameters in one statement).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql.asyncpg import dialect as asyncpg_dialect

from backend.app.services import clickhouse_family_variants as cfv
from backend.app.services import variant_ranking_cache as vrc

PANEL = "0e14a1b2-0000-4000-8000-0000000000e1"
OTHER = "0e14a1b2-0000-4000-8000-0000000000e2"
ASSEMBLY = "0e14a1b2-0000-4000-8000-0000000000a1"
ASSEMBLY_B = "0e14a1b2-0000-4000-8000-0000000000a2"
LOADED = datetime(2026, 10, 1, tzinfo=timezone.utc)

# What each statement of a resolution reads.
TOKEN = "token"
GENES = "panel genes"
REGIONS = "panel regions"
LOCI = "gene loci"
FULL_READ = [GENES, REGIONS, LOCI]


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


def _sha256(rows: list[Any]) -> str | None:
    return hashlib.sha256(repr(sorted(rows)).encode()).hexdigest() if rows else None


class _Tables:
    """gene_panels, gene_panel_genes, gene_panel_regions, genes and reference_dataset_imports,
    answering each statement of a resolution as Postgres would."""

    def __init__(self) -> None:
        # panel id -> its version, genes, and regions per assembly (gene, chr, start, end)
        self.panels: dict[str, dict[str, Any]] = {}
        # assembly id -> the gene reference's rows (symbol, transcript id, chr, start, end)
        self.genes: dict[str, list[tuple[str, str, str, int, int]]] = {
            ASSEMBLY: [
                ("GENE1", "ENST01", "1", 1_000, 2_000),
                ("GENE1", "ENST02", "1", 1_200, 1_800),
                ("GENE2", "ENST03", "1", 3_000, 4_000),
                ("GENE3", "ENST04", "2", 7_000, 8_000),
            ],
            ASSEMBLY_B: [("GENE1", "ENST11", "1", 5_000, 6_000)],
        }
        # assembly id -> when each gene-reference import was recorded
        self.imports: dict[str, list[datetime]] = {ASSEMBLY: [LOADED], ASSEMBLY_B: [LOADED]}
        self.reads: list[str] = []
        # A change committed while a resolution reads the gene reference.
        self.while_reading_loci: Any = None

    # -- what the app does to them ---------------------------------------------------------

    def make_panel(self, panel_id: str, *genes: str) -> None:
        self.panels[panel_id] = {
            "version": 1,
            "genes": list(genes),
            "regions": {ASSEMBLY: [(gene, *self._longest(gene)) for gene in genes if self._longest(gene)]},
        }

    def edit_panel(self, panel_id: str, *genes: str) -> None:
        """An edit through the panel service: the next version, its genes and their regions."""
        version = self.panels[panel_id]["version"] + 1
        self.make_panel(panel_id, *genes)
        self.panels[panel_id]["version"] = version

    def load_gene_reference(self, assembly_id: str, rows: list[tuple[str, str, str, int, int]]) -> None:
        """A gene-reference load: the assembly's rows replaced, and the import recorded."""
        self.genes[assembly_id] = list(rows)
        self.imports[assembly_id].append(self.imports[assembly_id][-1] + timedelta(days=1))

    def _longest(self, gene: str) -> tuple[str, int, int] | None:
        loci = [(c, s, e) for symbol, _tx, c, s, e in self.genes[ASSEMBLY] if symbol == gene]
        return max(loci, key=lambda locus: locus[2] - locus[1]) if loci else None

    # -- the statements ----------------------------------------------------------------------

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = str(statement)
        params = dict(params or {})
        if "AS genes_sha256" in sql:
            self.reads.append(TOKEN)
            return _Rows(self._token(params["panel_id"], params["assembly_id"]))
        if "FROM gene_panel_genes" in sql:
            self.reads.append(GENES)
            panel = self.panels.get(params["panel_id"])
            return _Rows([{"gene_symbol": gene} for gene in sorted(panel["genes"])] if panel else [])
        if "FROM gene_panel_regions" in sql:
            self.reads.append(REGIONS)
            panel = self.panels.get(params["panel_id"])
            rows = sorted(panel["regions"].get(params["assembly_id"], [])) if panel else []
            return _Rows([{"gene": g, "chr": c, "start": s, "end": e} for g, c, s, e in rows])
        if "FROM genes" in sql:
            self.reads.append(LOCI)
            terms = set(params["terms"])
            rows = [
                {"chr": c, "start": s, "end": e}
                for symbol, transcript, c, s, e in self.genes.get(params.get("assembly_id"), [])
                if symbol.upper() in terms or transcript.upper() in terms
            ]
            if self.while_reading_loci is not None:
                change, self.while_reading_loci = self.while_reading_loci, None
                change()
            return _Rows(rows)
        if "FROM gene_panels" in sql:
            self.reads.append("panel")
            return _Rows([1] if params["panel_id"] in self.panels else [])
        raise AssertionError(f"unexpected statement: {sql}")

    def _token(self, panel_id: str, assembly_id: str) -> list[dict[str, Any]]:
        panel = self.panels.get(panel_id)
        if panel is None:
            return []
        imports = self.imports.get(assembly_id, [])
        return [
            {
                "version": panel["version"],
                "genes_sha256": _sha256(panel["genes"]),
                "regions_sha256": _sha256(panel["regions"].get(assembly_id, [])),
                "gene_imports": len(imports),
                "last_gene_import": max(imports) if imports else None,
            }
        ]


@pytest.fixture(autouse=True)
def _no_kept_resolutions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no kept resolution, and leaves none behind."""
    monkeypatch.setattr(cfv, "_panel_resolutions", OrderedDict(), raising=False)


@pytest.fixture
def tables() -> _Tables:
    return _Tables()


def _resolve(tables: _Tables, panel_id: str = PANEL, assembly_id: str | None = ASSEMBLY):
    """Resolve the panel as a family on ``assembly_id`` does, and return the resolution and
    what it read."""
    tables.reads.clear()
    constraints = asyncio.run(cfv._fetch_panel_constraints(tables, panel_id, assembly_id=assembly_id))
    return constraints, list(tables.reads)


def _loci(constraints) -> list[tuple[str, int, int]]:
    return sorted((region.chr, region.start, region.end) for region in constraints.regions)


def test_an_unchanged_panel_is_served_from_its_token_alone(tables) -> None:
    tables.make_panel(PANEL, "GENE1", "GENE2")
    first, first_reads = _resolve(tables)
    assert first.genes == ("GENE1", "GENE2")
    assert _loci(first) == [("1", 1_000, 2_000), ("1", 1_200, 1_800), ("1", 3_000, 4_000)]
    assert [read for read in first_reads if read != TOKEN] == FULL_READ

    # Read again unchanged: the token alone, and the same resolution. Before, every
    # resolution read the panel's genes, its regions and every locus of its genes again.
    again, again_reads = _resolve(tables)
    assert again_reads == [TOKEN]
    assert again is first


def test_a_panel_edit_reads_the_panel_again(tables) -> None:
    tables.make_panel(PANEL, "GENE1")
    first, _ = _resolve(tables)
    tables.edit_panel(PANEL, "GENE1", "GENE3")

    after, reads = _resolve(tables)
    assert set(FULL_READ) <= set(reads)
    assert after.genes == ("GENE1", "GENE3")
    assert ("2", 7_000, 8_000) in _loci(after)
    assert vrc.panel_members_digest(after) != vrc.panel_members_digest(first)


def test_a_change_of_the_panel_rows_without_a_new_version_is_read(tables) -> None:
    # A write that bypasses the panel service leaves the version as it was: the token's
    # digest of the panel's rows still moves.
    tables.make_panel(PANEL, "GENE1")
    _resolve(tables)
    tables.panels[PANEL]["genes"].append("GENE2")

    after, reads = _resolve(tables)
    assert set(FULL_READ) <= set(reads)
    assert after.genes == ("GENE1", "GENE2")
    assert ("1", 3_000, 4_000) in _loci(after)

    tables.panels[PANEL]["regions"][ASSEMBLY].append(("GENE3", "2", 7_000, 8_000))
    with_region, reads = _resolve(tables)
    assert set(FULL_READ) <= set(reads)
    assert with_region.genes == ("GENE1", "GENE2", "GENE3")


def test_a_gene_reference_load_reads_the_loci_again(tables) -> None:
    # A reload that moves GENE1: no panel changes version, but its loci, and the panel's
    # part of the ranking-cache key, move.
    tables.make_panel(PANEL, "GENE1")
    first, _ = _resolve(tables)
    tables.load_gene_reference(ASSEMBLY, [("GENE1", "ENST01", "1", 900, 2_600)])

    after, reads = _resolve(tables)
    assert set(FULL_READ) <= set(reads)
    assert ("1", 900, 2_600) in _loci(after)
    assert ("1", 1_200, 1_800) not in _loci(after)
    assert vrc.panel_members_digest(after) != vrc.panel_members_digest(first)


def test_a_resolution_read_while_the_panel_changed_is_not_kept(tables) -> None:
    # An edit committed while the resolution reads: what it read may be part old, part new,
    # so it serves its own request and is not kept for the next.
    tables.make_panel(PANEL, "GENE1")
    tables.while_reading_loci = lambda: tables.edit_panel(PANEL, "GENE2")
    _resolve(tables)

    after, reads = _resolve(tables)
    assert set(FULL_READ) <= set(reads)
    assert after.genes == ("GENE2",)
    again, reads = _resolve(tables)
    assert reads == [TOKEN]
    assert again is after


def test_the_sub_panel_check_reads_a_kept_panel_from_its_token(tables) -> None:
    # CLIN-27: a broader ranking serves a sub-panel only while its panel resolves to what the
    # ranking recorded. With the panel kept (the Mendeliome resolved for its own view), the
    # check reads its token, not its genes, regions and loci.
    tables.make_panel(PANEL, "GENE1", "GENE2")
    resolved, _ = _resolve(tables)
    candidate = {"panel_id": PANEL, "provenance": {"panel_members": vrc.panel_members_digest(resolved)}}

    tables.reads.clear()
    assert asyncio.run(cfv._superset_panel_if_unchanged(tables, candidate, assembly_id=ASSEMBLY)) is resolved
    assert tables.reads == [TOKEN]

    # The panel gains a gene: read again, it no longer holds what the ranking recorded.
    tables.edit_panel(PANEL, "GENE1", "GENE2", "GENE3")
    assert asyncio.run(cfv._superset_panel_if_unchanged(tables, candidate, assembly_id=ASSEMBLY)) is None


def test_each_assembly_keeps_its_own_resolution(tables) -> None:
    tables.make_panel(PANEL, "GENE1")
    in_a, _ = _resolve(tables, assembly_id=ASSEMBLY)
    in_b, reads = _resolve(tables, assembly_id=ASSEMBLY_B)
    assert set(FULL_READ) <= set(reads)
    assert _loci(in_b) == [("1", 5_000, 6_000)]
    assert ("1", 5_000, 6_000) not in _loci(in_a)

    again_a, reads = _resolve(tables, assembly_id=ASSEMBLY)
    assert reads == [TOKEN]
    assert again_a is in_a


def test_without_an_assembly_the_genes_alone_are_read_and_nothing_is_kept(tables) -> None:
    tables.make_panel(PANEL, "GENE1")
    first, reads = _resolve(tables, assembly_id=None)
    assert reads == [GENES]
    assert first.genes == ("GENE1",) and first.regions == ()
    _again, reads = _resolve(tables, assembly_id=None)
    assert reads == [GENES]


def test_a_deleted_panel_still_answers_404(tables) -> None:
    tables.make_panel(PANEL, "GENE1")
    _resolve(tables)
    del tables.panels[PANEL]

    with pytest.raises(HTTPException) as refused:
        _resolve(tables)
    assert refused.value.status_code == 404


def test_the_most_recently_used_resolutions_are_kept(tables) -> None:
    panels = [f"0e14a1b2-0000-4000-8000-{index:012d}" for index in range(cfv._PANEL_RESOLUTION_CACHE_SIZE + 1)]
    for panel_id in panels:
        tables.make_panel(panel_id, "GENE1")
    first, _ = _resolve(tables, panels[0])
    for panel_id in panels[1:-1]:
        _resolve(tables, panel_id)
    # The first is used again, so the second is the least recently used one.
    assert _resolve(tables, panels[0])[0] is first
    _resolve(tables, panels[-1])

    assert _resolve(tables, panels[0])[1] == [TOKEN]
    assert set(FULL_READ) <= set(_resolve(tables, panels[1])[1])


def test_the_digest_of_a_kept_resolution_is_kept_with_it(tables, monkeypatch) -> None:
    tables.make_panel(PANEL, "GENE1", "GENE2")
    digests: list[object] = []

    def counted(constraints):
        digests.append(constraints)
        return vrc.panel_members_digest(constraints)

    monkeypatch.setattr(cfv, "panel_members_digest", counted)
    first, _ = _resolve(tables)
    assert len(digests) == 1  # taken once, as the resolution is kept

    again, _ = _resolve(tables)
    assert cfv._panel_members(again) == vrc.panel_members_digest(first)
    assert len(digests) == 1
    # A resolution that is not kept (any other) is digested as before.
    other = dataclasses.replace(first, genes=("GENE1",))
    assert cfv._panel_members(other) == vrc.panel_members_digest(other)
    assert len(digests) == 2


def test_a_kept_resolution_cannot_be_changed(tables) -> None:
    # The same resolution serves every request that applies the panel.
    tables.make_panel(PANEL, "GENE1")
    kept, _ = _resolve(tables)
    with pytest.raises(dataclasses.FrozenInstanceError):
        kept.genes = ("GENE9",)  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        kept.regions[0].start = 1  # type: ignore[misc]


# --- the gene-reference lookup binds its terms as one array ----------------------------

# The most parameters asyncpg sends in one statement.
_ASYNCPG_MAX_PARAMETERS = 32_767


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        self.calls.append((statement, dict(params or {})))
        return _Rows([])


def _sent(statement: Any, params: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """The parameters ``statement`` reaches Postgres with, as the asyncpg dialect binds it."""
    expanded = statement.compile(dialect=asyncpg_dialect()).construct_expanded_state(params)
    return list(expanded.positiontup), dict(expanded.parameters)


def test_the_gene_reference_lookup_binds_any_number_of_terms_as_one_parameter() -> None:
    # More genes than asyncpg takes parameters: an expanding IN bound one per term.
    terms = [f"Gene{index}" for index in range(_ASYNCPG_MAX_PARAMETERS + 1_000)]
    session = _RecordingSession()
    asyncio.run(cfv._fetch_gene_regions_for_terms(session, terms=terms, assembly_id=ASSEMBLY))

    [(statement, params)] = session.calls
    names, values = _sent(statement, params)
    assert names == ["terms", "assembly_id"]
    assert values["terms"] == [term.upper() for term in terms]
    assert "upper(hgnc_symbol) = ANY(CAST(:terms AS text[]))" in str(statement)
    assert "upper(gene_id) = ANY(CAST(:terms AS text[]))" in str(statement)


def test_the_gene_reference_lookup_reads_nothing_without_terms() -> None:
    session = _RecordingSession()
    assert asyncio.run(cfv._fetch_gene_regions_for_terms(session, terms=["", "  "], assembly_id=ASSEMBLY)) == []
    assert session.calls == []


def test_the_panel_genes_are_kept_once_each_in_the_order_read(tables) -> None:
    # As before: stripped, empty ones left out, the panel's genes and then its regions'.
    tables.make_panel(PANEL, " GENE2 ", "GENE1", "")
    tables.panels[PANEL]["regions"][ASSEMBLY] = [("GENE3", "2", 7_000, 8_000), ("GENE1 ", "1", 1_000, 2_000)]
    resolved, _ = _resolve(tables)
    assert resolved.genes == ("GENE2", "GENE1", "GENE3")
