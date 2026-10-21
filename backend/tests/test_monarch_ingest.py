"""Tests for Monarch gene -> disease association parsing and aggregation."""

import pytest

from backend.app.services import monarch_ingest as monarch
from backend.app.services.monarch_ingest import (
    _Association,
    _DiseasePhenotype,
    _escape_like,
    _resolve_release_version,
    parse_disease_phenotype_tsv,
    parse_gene_disease_tsv,
    search_monarch_associations,
)

_HEADER = (
    "subject\tsubject_label\tsubject_category\tsubject_taxon\tsubject_taxon_label\t"
    "negated\tpredicate\tobject\tobject_label\tobject_category\tqualifiers\t"
    "publications\thas_evidence\tprimary_knowledge_source\taggregator_knowledge_source"
)


def _row(subject, label, predicate, obj, obj_label, source, negated=""):
    return (
        f"{subject}\t{label}\tbiolink:Gene\tNCBITaxon:9606\tHomo sapiens\t"
        f"{negated}\t{predicate}\t{obj}\t{obj_label}\tbiolink:Disease\t\t\t\t"
        f"{source}\t['infores:monarchinitiative']"
    )


def _tsv(*rows: str) -> str:
    return "\n".join((_HEADER, *rows)) + "\n"


def test_parse_aggregates_sources_and_picks_strongest_predicate() -> None:
    text = _tsv(
        _row("HGNC:1", "GENEA", "biolink:gene_associated_with_condition",
             "MONDO:1", "disease one", "infores:orphanet"),
        _row("HGNC:1", "GENEA", "biolink:causes",
             "MONDO:1", "disease one", "infores:omim"),
    )
    associations: dict[tuple[str, str], _Association] = {}
    parse_gene_disease_tsv(text, associations=associations)

    assert len(associations) == 1
    record = associations[("HGNC:1", "MONDO:1")]
    # Strongest predicate wins; sources and predicates accumulate.
    assert record.predicate == "causes"
    assert record.predicates == {"causes", "gene_associated_with_condition"}
    assert record.sources == {"omim", "orphanet"}
    assert record.causal is True


def test_parse_filters_non_hgnc_subjects_and_negated_rows() -> None:
    text = _tsv(
        _row("MONDO:9", "diseaseX", "biolink:causes", "MONDO:1", "d1", "infores:omim"),
        _row("HGNC:2", "GENEB", "biolink:causes", "MONDO:2", "d2", "infores:omim",
             negated="True"),
        _row("HGNC:3", "GENEC", "biolink:causes", "MONDO:3", "d3", "infores:omim"),
    )
    associations: dict[tuple[str, str], _Association] = {}
    parse_gene_disease_tsv(text, associations=associations)

    assert set(associations) == {("HGNC:3", "MONDO:3")}


def test_noncausal_only_predicate_is_not_marked_causal() -> None:
    text = _tsv(
        _row("HGNC:4", "GENED", "biolink:associated_with_increased_likelihood_of",
             "MONDO:4", "d4", "infores:omim"),
    )
    associations: dict[tuple[str, str], _Association] = {}
    parse_gene_disease_tsv(text, associations=associations)

    record = associations[("HGNC:4", "MONDO:4")]
    assert record.causal is False
    assert record.predicate == "associated_with_increased_likelihood_of"


def _pheno_row(disease, disease_label, hpo, hpo_label, source, negated=""):
    return (
        f"{disease}\t{disease_label}\tbiolink:Disease\t\t\t"
        f"{negated}\tbiolink:has_phenotype\t{hpo}\t{hpo_label}\tbiolink:PhenotypicFeature\t\t\t\t"
        f"{source}\t['infores:monarchinitiative']"
    )


def test_disease_phenotype_aggregates_sources() -> None:
    text = _tsv(
        _pheno_row("MONDO:1", "d1", "HP:1", "seizure", "infores:omim"),
        _pheno_row("MONDO:1", "d1", "HP:1", "seizure", "infores:orphanet"),
    )
    phenotypes: dict[tuple[str, str], _DiseasePhenotype] = {}
    parse_disease_phenotype_tsv(text, phenotypes=phenotypes)

    assert len(phenotypes) == 1
    record = phenotypes[("MONDO:1", "HP:1")]
    assert record.sources == {"omim", "orphanet"}
    assert record.negated is False


def test_disease_phenotype_present_assertion_wins_over_negated() -> None:
    text = _tsv(
        _pheno_row("MONDO:2", "d2", "HP:2", "ataxia", "infores:omim", negated="True"),
        _pheno_row("MONDO:2", "d2", "HP:2", "ataxia", "infores:orphanet"),
    )
    phenotypes: dict[tuple[str, str], _DiseasePhenotype] = {}
    parse_disease_phenotype_tsv(text, phenotypes=phenotypes)

    assert phenotypes[("MONDO:2", "HP:2")].negated is False


def test_disease_phenotype_keeps_pure_exclusions_and_filters_non_hp() -> None:
    text = _tsv(
        _pheno_row("MONDO:3", "d3", "HP:3", "excluded", "infores:omim", negated="True"),
        _pheno_row("MONDO:4", "d4", "MONDO:9", "not-a-phenotype", "infores:omim"),
    )
    phenotypes: dict[tuple[str, str], _DiseasePhenotype] = {}
    parse_disease_phenotype_tsv(text, phenotypes=phenotypes)

    assert set(phenotypes) == {("MONDO:3", "HP:3")}
    assert phenotypes[("MONDO:3", "HP:3")].negated is True


def test_escape_like_neutralizes_wildcards() -> None:
    # Backslash first so the wildcard escapes it introduces are not double-escaped.
    assert _escape_like("type_2 50%") == r"type\_2 50\%"
    assert _escape_like(r"a\b") == r"a\\b"


class _ExplodingSession:
    """Stand-in session that fails if any query is issued."""

    async def execute(self, *args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("an empty query should not touch the database")


@pytest.mark.asyncio
async def test_search_blank_query_returns_empty_shape_without_db() -> None:
    result = await search_monarch_associations(_ExplodingSession(), query="   ")

    assert result == {
        "query": "",
        "total": 0,
        "diseases": [],
        "gene_overview": {"total": 0, "genes": []},
    }


@pytest.mark.asyncio
async def test_resolve_release_version_uses_explicit_and_skips_fetch(monkeypatch) -> None:
    called = False

    async def _fake_fetch() -> str:
        nonlocal called
        called = True
        return "2026-06-08"

    monkeypatch.setattr(monarch, "_fetch_release_version", _fake_fetch)
    # An explicit version is returned as-is with no network lookup.
    assert await _resolve_release_version("2025-01-01") == "2025-01-01"
    assert called is False
    # None resolves from the metadata fetch.
    assert await _resolve_release_version(None) == "2026-06-08"
    assert called is True


@pytest.mark.asyncio
async def test_resolve_release_version_raises_when_unknown(monkeypatch) -> None:
    async def _fake_fetch_none() -> None:
        return None

    monkeypatch.setattr(monarch, "_fetch_release_version", _fake_fetch_none)
    # An unresolvable release version must hard-fail rather than load NULL-version data,
    # as a fault upstream the refresh router answers 502 (DATA-4).
    with pytest.raises(monarch.MonarchSourceError, match="unknown release version"):
        await _resolve_release_version(None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "body"),
    [
        ("https://example.org/gene_disease.tsv.gz", b"not gzip at all"),
        ("https://example.org/gene_disease.tsv.gz", b"\x1f\x8b\x08\x00 cut short"),
        ("https://example.org/gene_disease.tsv", b"subject\t\xff\xfe not UTF-8\n"),
    ],
    ids=["not-gzip", "truncated-gzip", "not-utf8"],
)
async def test_a_download_that_cannot_be_read_is_a_monarch_source_error(monkeypatch, url: str, body: bytes) -> None:
    async def download(_client, _url, **_kwargs) -> bytes:
        return body

    monkeypatch.setattr(monarch, "download_bounded_bytes", download)

    # A ValueError from the gunzip or the decode, which the refresh router did not catch: a 500.
    with pytest.raises(monarch.MonarchSourceError):
        await monarch._download_gzip_tsv(url)


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement, *_args, **_kwargs):
        self.statements.append(str(statement))


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh", ["refresh_monarch_gene_disease", "refresh_monarch_disease_phenotype"])
async def test_a_download_without_one_association_empties_no_table(monkeypatch, refresh: str) -> None:
    async def header_only(_url: str) -> str:
        return _HEADER + "\n"

    monkeypatch.setattr(monarch, "_download_gzip_tsv", header_only)
    session = _RecordingSession()

    # It deleted every row and wrote none: the refresh emptied the table (#859).
    with pytest.raises(monarch.MonarchSourceError, match="no .* association"):
        await getattr(monarch, refresh)(session, release_version="2026-06-08")
    assert session.statements == []
