"""Monarch's gene -> disease and disease -> phenotype associations (``monarch_ingest``).

The parsing and aggregation of the KG's TSVs, the admin search, the release the KG metadata
states (a refresh of an unknown release loads nothing), downloads that cannot be read
or hold no association, and the refresh of both tables in one transaction under one
release. No network: every download is replaced."""

import pytest

from backend.app.services import bounded_download
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
    # as a fault upstream the refresh router answers 502.
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

    monkeypatch.setattr(bounded_download, "download_bounded_bytes", download)

    # A ValueError from the gunzip or the decode, which the refresh router did not catch: a 500.
    with pytest.raises(monarch.MonarchSourceError):
        await monarch._download_gzip_tsv(url)


@pytest.mark.asyncio
async def test_a_download_larger_than_the_cap_is_a_monarch_source_error(monkeypatch) -> None:
    async def download(_client, url, *, max_bytes=bounded_download.MAX_DOWNLOAD_BYTES, **_kwargs) -> bytes:
        raise ValueError(f"{url} download exceeds {max_bytes} bytes")  # as download_bounded_bytes says it

    monkeypatch.setattr(bounded_download, "download_bounded_bytes", download)

    # Raised past the conversion, it reached the refresh router as a 500, though the class says a
    # download too large is a fault upstream, answered 502 with the loaded release kept.
    with pytest.raises(monarch.MonarchSourceError, match="download exceeds"):
        await monarch._download_gzip_tsv("https://example.org/gene_disease.tsv.gz")


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

    # Refused before any statement, so the refresh does not empty the table.
    with pytest.raises(monarch.MonarchSourceError, match="no .* association"):
        await getattr(monarch, refresh)(session, release_version="2026-06-08")
    assert session.statements == []


# ---- The refresh of both tables (refresh_monarch) ------------------------------------------------
#
# Both tables are replaced in the caller's one transaction, under the one release the KG
# metadata states, and committed once: a failure on the second leaves both on the release they
# held. The phenotype information content, derived from the disease-phenotype table, is
# dropped after the commit, so the next score reads the new release.


class _TransactionSession:
    """Records each statement with its parameters, and each commit."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.executed.append((" ".join(str(statement).split()), params))

    async def commit(self) -> None:
        self.commits += 1

    def statements(self) -> list[str]:
        return [sql.split(" (")[0] for sql, _params in self.executed]

    def rows(self, table: str) -> list[dict]:
        return [row for sql, params in self.executed if f"INSERT INTO {table}" in sql for row in params]


# 5,001 disease-phenotype pairs: written in two chunks of at most 5,000 rows.
_PHENOTYPE_PAIRS = 5_001


def _phenotype_tsv(pairs: int) -> str:
    rows = [_pheno_row("MONDO:1", "d1", f"HP:{index:07d}", f"term {index}", "infores:omim") for index in range(pairs)]
    rows.append(_pheno_row("MONDO:2", "d2", "HP:0000001", "excluded", "infores:omim", negated="True"))
    return _tsv(*rows)


def _monarch_files(monkeypatch, *, phenotypes: str) -> list[str]:
    """The KG states release 2026-06-08; its gene-disease files hold two pairs (one causal), its
    disease-phenotype file ``phenotypes``. The list collects each file downloaded."""
    downloaded: list[str] = []
    files = {
        monarch.MONARCH_GENE_DISEASE_FILES[0]: _tsv(
            _row("HGNC:1100", "BRCA1", "biolink:causes", "MONDO:1", "d1", "infores:omim"),
        ),
        monarch.MONARCH_GENE_DISEASE_FILES[1]: _tsv(
            _row("HGNC:1100", "BRCA1", "biolink:contributes_to", "MONDO:1", "d1", "infores:orphanet"),
            _row("HGNC:11998", "TP53", "biolink:gene_associated_with_condition", "MONDO:2", "d2", "infores:orphanet"),
        ),
        monarch.MONARCH_DISEASE_PHENOTYPE_FILE: phenotypes,
    }

    async def release() -> str:
        return "2026-06-08"

    async def download(url: str) -> str:
        downloaded.append(url)
        return files[url]

    monkeypatch.setattr(monarch, "_fetch_release_version", release)
    monkeypatch.setattr(monarch, "_download_gzip_tsv", download)
    return downloaded


@pytest.fixture()
def _scored_release():
    """The phenotype information content of the release loaded before, cached."""
    from backend.app.services import monarch_phenotype_score

    monarch_phenotype_score._ic_cache.update({"expires_at": float("inf"), "ic": {"HP:0000001": 1.0}, "max_ic": 1.0})
    yield monarch_phenotype_score._ic_cache
    monarch_phenotype_score.reset_information_content_cache()


@pytest.mark.asyncio
async def test_a_refresh_replaces_both_tables_under_one_release_and_commits_once(monkeypatch, _scored_release) -> None:
    downloaded = _monarch_files(monkeypatch, phenotypes=_phenotype_tsv(_PHENOTYPE_PAIRS))
    session = _TransactionSession()

    summary = await monarch.refresh_monarch(session)

    assert downloaded == [*monarch.MONARCH_GENE_DISEASE_FILES, monarch.MONARCH_DISEASE_PHENOTYPE_FILE]
    assert session.statements() == [
        "DELETE FROM monarch_gene_disease",
        "INSERT INTO monarch_gene_disease",
        "DELETE FROM monarch_disease_phenotype",
        "INSERT INTO monarch_disease_phenotype",
        "INSERT INTO monarch_disease_phenotype",
    ]
    assert session.commits == 1
    genes = {row["hgnc_id"]: row for row in session.rows("monarch_gene_disease")}
    assert {row["release_version"] for row in genes.values()} == {"2026-06-08"}
    assert (genes["HGNC:1100"]["predicate"], genes["HGNC:1100"]["causal"]) == ("causes", True)
    assert genes["HGNC:1100"]["predicates"] == '["causes", "contributes_to"]'
    assert genes["HGNC:1100"]["sources"] == '["omim", "orphanet"]'
    assert (genes["HGNC:11998"]["predicate"], genes["HGNC:11998"]["causal"]) == ("gene_associated_with_condition", False)
    phenotypes = session.rows("monarch_disease_phenotype")
    assert [len(params) for sql, params in session.executed if "INSERT INTO monarch_disease_phenotype" in sql] == [
        5_000,
        _PHENOTYPE_PAIRS + 1 - 5_000,
    ]
    assert {row["release_version"] for row in phenotypes} == {"2026-06-08"}
    assert [row["hpo_id"] for row in phenotypes if row["negated"]] == ["HP:0000001"]
    assert {key: summary[key] for key in (
        "release_version",
        "files_loaded",
        "gene_disease_pairs",
        "genes",
        "diseases",
        "causal_pairs",
        "disease_phenotype_pairs",
        "phenotype_diseases",
        "excluded_phenotype_pairs",
    )} == {
        "release_version": "2026-06-08",
        "files_loaded": 3,
        "gene_disease_pairs": 2,
        "genes": 2,
        "diseases": 2,
        "causal_pairs": 1,
        "disease_phenotype_pairs": _PHENOTYPE_PAIRS + 1,
        "phenotype_diseases": 2,
        "excluded_phenotype_pairs": 1,
    }
    # The next phenotype score computes the information content of the new release.
    assert _scored_release["ic"] is None


@pytest.mark.asyncio
async def test_a_refresh_that_fails_on_its_second_table_commits_nothing(monkeypatch, _scored_release) -> None:
    _monarch_files(monkeypatch, phenotypes=_HEADER + "\n")
    session = _TransactionSession()

    with pytest.raises(monarch.MonarchSourceError, match="no disease-phenotype association"):
        await monarch.refresh_monarch(session)

    # The gene-disease swap was staged, never committed: the caller's rollback keeps both tables
    # on the release they held, and the scores on its information content.
    assert session.commits == 0
    assert "DELETE FROM monarch_disease_phenotype" not in session.statements()
    assert _scored_release["ic"] == {"HP:0000001": 1.0}


@pytest.mark.asyncio
async def test_a_refresh_of_an_unknown_release_downloads_and_writes_nothing(monkeypatch) -> None:
    downloaded = _monarch_files(monkeypatch, phenotypes=_phenotype_tsv(1))

    async def unknown() -> None:
        return None

    monkeypatch.setattr(monarch, "_fetch_release_version", unknown)
    session = _TransactionSession()

    with pytest.raises(monarch.MonarchSourceError, match="unknown release version"):
        await monarch.refresh_monarch(session)

    assert (downloaded, session.executed, session.commits) == ([], [], 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "release"),
    [
        ("name: monarch-kg\nversion: '2026-06-08'\nsource: x\n", "2026-06-08"),
        ('  version: "2026-07-01"\n', "2026-07-01"),
        ("name: monarch-kg\n", None),
        ("version: ''\n", None),
    ],
    ids=["single-quoted", "double-quoted", "no-version", "empty-version"],
)
async def test_the_release_is_read_from_the_kg_metadata(monkeypatch, metadata: str, release: str | None) -> None:
    import httpx

    requested: list[str] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=metadata)

    monkeypatch.setattr(
        monarch.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)
    )

    assert await monarch._fetch_release_version() == release
    assert requested == [monarch.MONARCH_METADATA_URL]


# ---- What the family shows of a gene's or a disease's phenotypes ---------------------------------
#
# A gene's or disease's expected phenotype counts as shown by the family when the family has the
# term or a more specific one: the family's present terms with all their ancestors.


class _AnsweringSession:
    """Answers each statement with the next list of rows; ``asked`` records each statement and
    its parameters."""

    def __init__(self, *answers: list[dict]) -> None:
        self._answers = list(answers)
        self.asked: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        from types import SimpleNamespace

        self.asked.append((" ".join(str(statement).split()), dict(params or {})))
        rows = self._answers.pop(0)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))


@pytest.mark.asyncio
async def test_the_familys_observed_terms_are_its_present_terms_and_their_ancestors() -> None:
    session = _AnsweringSession([{"hpo_id": "HP:0001250"}], [{"ancestor_id": "HP:0000118"}])

    observed = await monarch.family_observed_phenotype_closure(session, family_uuid="family-uuid")

    # The exact term counts even without a self row in the closure.
    assert observed == {"HP:0001250", "HP:0000118"}
    (present_sql, present_params), (closure_sql, closure_params) = session.asked
    assert "status = 'present'" in present_sql
    assert present_params == {"family_uuid": "family-uuid"}
    assert closure_params == {"present": ["HP:0001250"]}
    # Without a present term nothing is observed, and the closure is not read.
    nothing = _AnsweringSession([])
    assert await monarch.family_observed_phenotype_closure(nothing, family_uuid="family-uuid") == set()
    assert len(nothing.asked) == 1


@pytest.mark.asyncio
async def test_a_phenotype_closure_holds_the_terms_and_their_ancestors() -> None:
    session = _AnsweringSession([{"ancestor_id": "HP:0000118"}, {"ancestor_id": "HP:0000001"}])

    closure = await monarch.phenotype_closure(session, ["HP:0001250", "", "HP:0001250"])

    assert closure == {"HP:0001250", "HP:0000118", "HP:0000001"}
    assert session.asked[0][1] == {"ids": ["HP:0001250"]}
    empty = _AnsweringSession()
    assert await monarch.phenotype_closure(empty, ["", None]) == set()  # type: ignore[list-item]
    assert empty.asked == []


@pytest.mark.asyncio
async def test_a_genes_phenotypes_are_split_by_what_the_family_shows() -> None:
    session = _AnsweringSession(
        [
            {"gene_symbol": "tg", "hpo_id": "HP:0000821", "phenotype_label": "Hypothyroidism"},
            {"gene_symbol": "TG", "hpo_id": "HP:0000853", "phenotype_label": "goiter"},
            # The same term through a second disease of the gene: listed once.
            {"gene_symbol": "TG", "hpo_id": "HP:0000821", "phenotype_label": "Hypothyroidism"},
            {"gene_symbol": "TG", "hpo_id": "HP:0001250", "phenotype_label": None},
            # A gene not asked about.
            {"gene_symbol": "IYD", "hpo_id": "HP:0000821", "phenotype_label": "Hypothyroidism"},
        ]
    )

    breakdown = await monarch.gene_phenotype_breakdown(
        session, symbols=["tg", "", "SCN1A"], observed_closure={"HP:0000821", "HP:0000853"}
    )

    assert session.asked[0][1] == {"symbols": ["SCN1A", "TG"]}
    assert "dp.negated = FALSE" in session.asked[0][0]
    # Sorted by label, case aside; a term without a label by its id.
    assert breakdown == {
        "TG": {
            "matching": [
                {"hpo_id": "HP:0000853", "label": "goiter"},
                {"hpo_id": "HP:0000821", "label": "Hypothyroidism"},
            ],
            "extra": [{"hpo_id": "HP:0001250", "label": None}],
        },
        "SCN1A": {"matching": [], "extra": []},
    }
    assert await monarch.gene_phenotype_breakdown(_AnsweringSession(), symbols=[], observed_closure=set()) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("observed", "matched"),
    [
        ({"HP:0000821", "HP:0000853"}, ["HP:0000853", "HP:0000821"]),
        (None, []),
    ],
    ids=["family", "no-family"],
)
async def test_a_diseases_summary_counts_its_phenotypes_and_lists_those_the_family_shows(
    observed: set[str] | None, matched: list[str]
) -> None:
    session = _AnsweringSession(
        [
            {"mondo_id": "MONDO:1", "hpo_id": "HP:0000821", "phenotype_label": "Hypothyroidism"},
            {"mondo_id": "MONDO:1", "hpo_id": "HP:0000853", "phenotype_label": "goiter"},
            {"mondo_id": "MONDO:1", "hpo_id": "HP:0001250", "phenotype_label": "Seizure"},
        ]
    )

    summary = await monarch.summarize_disease_phenotypes(
        session, mondo_ids=["MONDO:1", "MONDO:2", ""], observed_closure=observed
    )

    assert "negated = FALSE" in session.asked[0][0]
    assert sorted(session.asked[0][1]["ids"]) == ["MONDO:1", "MONDO:2"]
    assert summary["MONDO:1"]["phenotype_count"] == 3
    assert [term["hpo_id"] for term in summary["MONDO:1"]["matched"]] == matched
    # A disease without a phenotype row: none expected, none matched.
    assert summary["MONDO:2"] == {"phenotype_count": 0, "matched": []}
