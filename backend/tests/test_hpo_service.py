"""The HPO service (``hpo_service``): the ontology's parsing and closure, the admin summary and
term list, a family's phenotype annotations, the table-availability probe, and the ontology
file's path.

The ontology is read from OBO or from the OBO Graphs JSON HPO also publishes, with the same
terms, synonyms (alternative ids among them), obsolete terms and their replacements, and
parent edges. The table-availability probe is cached once the tables are there, and asked
again while they are not (they may appear after the schema is applied). An annotation write
without the HPO tables is refused (503); a read falls back to the bare annotation. The startup
download and the release a file states are tested in ``test_hpo_download_pin.py`` and
``test_hpo_sync_release_from_file.py``; a family's phenotype import in
``test_hpo_family_annotation_import.py``.
"""

import json
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.tests._kept_manifest import validated
from backend.app.services import hpo_service
from backend.app.services.hpo_service import (
    compute_hpo_closure,
    ensure_hpo_ontology_on_startup,
    get_hpo_admin_summary,
    list_hpo_admin_terms,
    parse_hpo_obo_text,
    parse_hpo_ontology_release_metadata_text,
    parse_hpo_ontology_text,
    parse_hpo_tsv_text,
    parse_manifest_inline_hpo,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FAMILY_UUID = "11111111-1111-1111-1111-111111111111"
ANNOTATION_ID = "22222222-2222-2222-2222-222222222222"
# An annotation as the table holds it, read without the HPO term table: the label is the id.
ANNOTATION_ROW = {
    "id": ANNOTATION_ID,
    "sample_id": "PROBAND",
    "hpo_id": "HP:0001250",
    "label": "HP:0001250",
    "definition": None,
    "status": "present",
    "onset": None,
    "evidence": None,
    "source": "manual",
    "note": None,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}


class _FakeMappings:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def one(self):
        return self._rows[0]

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def mappings(self):
        return _FakeMappings(self._rows)

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        return next(iter(self._rows[0].values()))

    def scalar_one(self):
        return next(iter(self._rows[0].values()))


class _FakeSession:
    def __init__(self, results: list[_FakeResult]):
        self._results = list(results)

    async def execute(self, *args, **kwargs):
        del args, kwargs
        return self._results.pop(0)


class _CapturingSession:
    """Answers the table probe with ``probe`` first, when given, then every statement with
    ``rows``; ``sql`` and ``params`` are the last statement's after the probe."""

    def __init__(self, rows: list[dict], *, probe: dict | None = None) -> None:
        self._rows = rows
        self._probe = probe
        self.sql = ""
        self.params: dict = {}

    async def execute(self, sql, params=None):
        if self._probe is not None:
            probe, self._probe = self._probe, None
            return _FakeResult([probe])
        self.sql = str(sql)
        self.params = dict(params or {})
        return _FakeResult(self._rows)


async def _tables_missing(*_args: object, **_kwargs: object) -> bool:
    return False


async def _tables_available(*_args: object, **_kwargs: object) -> bool:
    return True


def test_parse_hpo_obo_terms_synonyms_and_closure() -> None:
    ontology = parse_hpo_obo_text((FIXTURE_DIR / "hpo-mini.obo").read_text(encoding="utf-8"))

    assert ontology.terms["HP:0001250"].label == "Seizure"
    assert ontology.terms["HP:0001250"].synonyms == ["Epileptic seizure"]
    assert ontology.edges[0].child_id == "HP:0001250"

    closure = set(compute_hpo_closure(ontology.terms, ontology.edges))
    assert ("HP:0001250", "HP:0001250", 0) in closure
    assert ("HP:0001250", "HP:0000118", 1) in closure
    assert ("HP:0004322", "HP:0000118", 1) in closure


def test_parse_hpo_obo_release_metadata() -> None:
    metadata = parse_hpo_ontology_release_metadata_text(
        "\n".join(
            [
                "format-version: 1.2",
                "data-version: hp/releases/2026-02-16",
                'property_value: owl:versionInfo "2026-02-16" xsd:string',
                "",
                "[Term]",
                "id: HP:0000001",
            ]
        )
    )

    assert metadata.release_version == "hp/releases/2026-02-16"
    assert metadata.release_date.isoformat() == "2026-02-16"


# What the OBO file below states, as the OBO Graphs JSON states it too.
_OBO_WITH_OBSOLETE_TERMS = """format-version: 1.2
data-version: hp/releases/2026-02-16

[Term]
id: HP:0000118
name: Phenotypic abnormality

[Term]
id: HP:0001250
name: Seizure
def: "A \\"seizure\\" is an intermittent abnormality." []
alt_id: HP:0002279
synonym: "Epileptic seizure" EXACT []
is_a: HP:0000118 ! Phenotypic abnormality
relationship: part_of HP:0000118 ! Phenotypic abnormality

[Term]
id: HP:0000001
name: Fits
is_obsolete: true
replaced_by: HP:0001250

[Term]
id: HP:0009999
def: "A term without a name is not read." []

[Typedef]
id: HP:0000005
name: A typedef, not a term
"""


def test_parse_hpo_obo_reads_obsolete_terms_alternative_ids_and_relationships() -> None:
    ontology = parse_hpo_obo_text(_OBO_WITH_OBSOLETE_TERMS)

    # A term without a name, and a typedef stanza, are not terms.
    assert set(ontology.terms) == {"HP:0000118", "HP:0001250", "HP:0000001"}
    seizure = ontology.terms["HP:0001250"]
    assert seizure.definition == 'A "seizure" is an intermittent abnormality.'
    # An alternative id is found as a synonym, and kept as what it is.
    assert seizure.synonyms == ["Epileptic seizure", "HP:0002279"]
    assert seizure.metadata == {"alt_ids": ["HP:0002279"]}
    assert (seizure.is_obsolete, seizure.replaced_by) == (False, None)
    obsolete = ontology.terms["HP:0000001"]
    assert (obsolete.is_obsolete, obsolete.replaced_by) == (True, "HP:0001250")
    assert [(edge.child_id, edge.relation, edge.parent_id) for edge in ontology.edges] == [
        ("HP:0001250", "is_a", "HP:0000118"),
        ("HP:0001250", "part_of", "HP:0000118"),
    ]


def _obo_graphs_json() -> str:
    def iri(hpo_id: str) -> str:
        return "http://purl.obolibrary.org/obo/" + hpo_id.replace(":", "_")

    return json.dumps(
        {
            "graphs": [
                {
                    "id": "http://purl.obolibrary.org/obo/hp.json",
                    "meta": {"version": "http://purl.obolibrary.org/obo/hp/releases/2026-02-16/hp.json"},
                    "nodes": [
                        {"id": iri("HP:0000118"), "lbl": "Phenotypic abnormality", "type": "CLASS"},
                        {
                            "id": iri("HP:0001250"),
                            "lbl": "Seizure",
                            "type": "CLASS",
                            "meta": {
                                "definition": {"val": "An intermittent abnormality."},
                                "synonyms": [
                                    {"pred": "hasExactSynonym", "val": "Epileptic seizure"},
                                    {"pred": "hasExactSynonym", "val": "Epileptic seizure"},
                                ],
                            },
                        },
                        {"id": iri("HP:0000001"), "lbl": "Fits", "type": "CLASS", "meta": {"deprecated": True}},
                        # Not an HPO term, and an HPO term without a label: neither is read.
                        {"id": "http://purl.obolibrary.org/obo/UBERON_0000955", "lbl": "brain"},
                        {"id": iri("HP:0009999")},
                    ],
                    "edges": [
                        {"sub": iri("HP:0001250"), "pred": "is_a", "obj": iri("HP:0000118")},
                        {
                            "sub": iri("HP:0001250"),
                            "pred": "http://purl.obolibrary.org/obo/BFO_0000050",
                            "obj": iri("HP:0000118"),
                        },
                        {"sub": iri("HP:0001250"), "pred": "is_a", "obj": "http://purl.obolibrary.org/obo/UBERON_0000955"},
                    ],
                }
            ]
        }
    )


def test_parse_hpo_json_reads_the_terms_and_edges_the_obo_states() -> None:
    ontology = parse_hpo_ontology_text(_obo_graphs_json(), source_format="json")

    assert set(ontology.terms) == {"HP:0000118", "HP:0001250", "HP:0000001"}
    seizure = ontology.terms["HP:0001250"]
    assert (seizure.label, seizure.definition, seizure.synonyms) == (
        "Seizure",
        "An intermittent abnormality.",
        ["Epileptic seizure"],
    )
    assert ontology.terms["HP:0000001"].is_obsolete is True
    assert ontology.terms["HP:0000118"].is_obsolete is False
    # The relation is the predicate's last path segment; an edge to a term outside HPO is not read.
    assert [(edge.child_id, edge.relation, edge.parent_id) for edge in ontology.edges] == [
        ("HP:0001250", "is_a", "HP:0000118"),
        ("HP:0001250", "BFO_0000050", "HP:0000118"),
    ]
    # The same closure as from the OBO file's is_a and part_of edges.
    obo = parse_hpo_obo_text(_OBO_WITH_OBSOLETE_TERMS)
    assert {(a, b) for a, b, _depth in compute_hpo_closure(ontology.terms, ontology.edges)} == {
        (a, b) for a, b, _depth in compute_hpo_closure(obo.terms, obo.edges)
    }


def test_parse_hpo_json_release_metadata_reads_the_graphs_version() -> None:
    metadata = parse_hpo_ontology_release_metadata_text(_obo_graphs_json(), source_format="json")

    assert "hp/releases/2026-02-16" in (metadata.release_version or "")
    assert metadata.release_date == date(2026, 2, 16)
    # A file that is no JSON states no release.
    unreadable = parse_hpo_ontology_release_metadata_text("{not json", source_format="json")
    assert (unreadable.release_version, unreadable.release_date) == (None, None)


# ---- The table-availability probe ----------------------------------------------------------------
#
# Probed once and cached while the tables are there; asked again while they are not, since
# they may appear after the schema is applied. (The root conftest resets the cache between
# tests.)


class _ProbeSession:
    def __init__(self, row: dict) -> None:
        self._row = row
        self.count = 0

    async def execute(self, sql, params=None):
        self.count += 1
        return _FakeResult([self._row])


@pytest.mark.asyncio
async def test_postgres_tables_available_caches_positive() -> None:
    session = _ProbeSession({"hpo_term": True})

    assert await hpo_service._postgres_tables_available(session, ("hpo_term",)) is True
    assert session.count == 1
    # Second call is served from the cache — no second probe.
    assert await hpo_service._postgres_tables_available(session, ("hpo_term",)) is True
    assert session.count == 1


@pytest.mark.asyncio
async def test_postgres_tables_available_reprobes_when_absent() -> None:
    session = _ProbeSession({"hpo_missing": False})

    assert await hpo_service._postgres_tables_available(session, ("hpo_missing",)) is False
    assert session.count == 1
    # Absent tables are not cached (they may appear after schema init) -> re-probe.
    assert await hpo_service._postgres_tables_available(session, ("hpo_missing",)) is False
    assert session.count == 2


# ---- The admin summary and term list -------------------------------------------------------------


@pytest.mark.asyncio
async def test_hpo_admin_summary_reports_not_loaded_when_schema_missing() -> None:
    session = _FakeSession([_FakeResult([{"hpo_term": False}])])

    summary = await get_hpo_admin_summary(session)

    assert summary["total_terms"] == 0
    assert summary["ontology_loaded"] is False


@pytest.mark.asyncio
async def test_hpo_admin_summary_names_the_ontology_file_the_backend_is_configured_to_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The admin page prefills its sync path from this. Its own copy of the path had drifted
    # from the backend's default: /data/ref-data/hpo/hpo.obo against hp.obo.
    from backend.app.core.config import Settings
    from backend.app.schemas.hpo import HpoAdminSummaryOut

    assert Settings.model_fields["hpo_ontology_path"].default == "/data/ref-data/hpo/hp.obo"
    monkeypatch.setattr(hpo_service.settings, "hpo_ontology_path", "/srv/ref-data/hpo/hp.obo")

    monkeypatch.setattr(hpo_service, "_postgres_tables_available", _tables_available)
    loaded = await get_hpo_admin_summary(
        _FakeSession(
            [
                _FakeResult(
                    [
                        {
                            "total_terms": 2,
                            "active_terms": 2,
                            "obsolete_terms": 0,
                            "release_date": None,
                            "last_sync_date": None,
                        }
                    ]
                ),
                _FakeResult([{"release_version": "hp/releases/2026-06-06"}]),
            ]
        )
    )
    assert loaded["ontology_path"] == "/srv/ref-data/hpo/hp.obo"
    # The response model carries it, so the endpoint does not drop it.
    assert HpoAdminSummaryOut(**loaded).ontology_path == "/srv/ref-data/hpo/hp.obo"

    monkeypatch.setattr(hpo_service, "_postgres_tables_available", _tables_missing)
    empty = await get_hpo_admin_summary(_FakeSession([]))
    # Also before any ontology is installed, when the path matters most.
    assert empty["ontology_path"] == "/srv/ref-data/hpo/hp.obo"

    # An empty setting names no file; the page then asks for one.
    monkeypatch.setattr(hpo_service.settings, "hpo_ontology_path", "")
    assert (await get_hpo_admin_summary(_FakeSession([])))["ontology_path"] is None


@pytest.mark.asyncio
async def test_hpo_admin_summary_describes_the_ontology_that_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Release, release date and last sync all describe the latest import, read as the
    # signed record reads it. MAX(release_date) and the latest release that is not empty
    # could name a release left only on terms the latest import no longer lists.
    from datetime import datetime, timezone

    imported_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    reads: list[object] = []

    async def loaded(session: object) -> dict:
        reads.append(session)
        return {"release_version": None, "release_date": None, "imported_at": imported_at}

    monkeypatch.setattr(hpo_service, "_postgres_tables_available", _tables_available)
    monkeypatch.setattr(hpo_service, "get_loaded_hpo_release", loaded)
    session = _FakeSession([_FakeResult([{"total_terms": 2, "active_terms": 2, "obsolete_terms": 0}])])
    summary = await get_hpo_admin_summary(session)

    assert reads == [session]
    assert summary["ontology_loaded"] is True
    # Imported from a file without a release: unknown, not the release before it.
    assert summary["release_version"] is None
    assert summary["release_date"] is None
    assert summary["last_sync_date"] == imported_at


# ---- A family's annotations without the HPO tables -----------------------------------------------

_PAYLOAD = SimpleNamespace(hpo_id="HP:0001250", status="present", onset=None, evidence=None, source=None, note=None)
_ANALYST = SimpleNamespace(id=None, username="analyst")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("write", "target"),
    [
        (hpo_service.create_individual_hpo_annotation, {"sample_id": "PROBAND"}),
        (hpo_service.update_individual_hpo_annotation, {"annotation_id": ANNOTATION_ID}),
    ],
    ids=["create", "update"],
)
async def test_an_annotation_write_returns_service_unavailable_when_hpo_schema_is_missing(
    monkeypatch: pytest.MonkeyPatch, write, target: dict
) -> None:
    from fastapi import HTTPException

    session = _FakeSession([])
    monkeypatch.setattr(hpo_service, "_postgres_tables_available", _tables_missing)

    with pytest.raises(HTTPException) as exc_info:
        await write(
            session,
            family_uuid=FAMILY_UUID,
            family_identifier="FAM1",
            payload=_PAYLOAD,
            user=_ANALYST,
            **target,
        )

    assert exc_info.value.status_code == 503
    assert "HPO database schema is not available" in exc_info.value.detail


@pytest.mark.asyncio
async def test_mark_family_hpo_annotations_stale_uses_typed_json_parameters() -> None:
    session = _CapturingSession([{}])

    await hpo_service.mark_family_hpo_annotations_stale(
        session,
        family_uuid=FAMILY_UUID,
        sample_id="PROBAND",
        reason="hpo_annotation_created",
    )

    assert "reason', CAST(:reason AS text)" in session.sql
    assert "sample_id', CAST(:sample_id AS text)" in session.sql
    assert session.params["reason"] == "hpo_annotation_created"
    assert session.params["sample_id"] == "PROBAND"


@pytest.mark.asyncio
async def test_annotation_by_id_returns_basic_annotation_when_hpo_term_table_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession([_FakeResult([dict(ANNOTATION_ROW)])])
    monkeypatch.setattr(hpo_service, "_postgres_tables_available", _tables_missing)

    annotation = await hpo_service._annotation_by_id(
        session,
        family_uuid=FAMILY_UUID,
        annotation_id=ANNOTATION_ID,
    )

    assert annotation is not None
    assert annotation["label"] == "HP:0001250"
    assert annotation["definition"] is None


@pytest.mark.asyncio
async def test_list_family_hpo_annotations_falls_back_when_hpo_term_table_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession([_FakeResult([dict(ANNOTATION_ROW)])])
    monkeypatch.setattr(hpo_service, "_postgres_tables_available", _tables_missing)

    annotations = await hpo_service.list_family_hpo_annotations(
        session,
        family_uuid=FAMILY_UUID,
    )

    assert annotations == [ANNOTATION_ROW]


# ---- The admin's term list and the term search ---------------------------------------------------

_HPO_TABLES = {"hpo_term": True, "hpo_synonym": True, "hpo_edge": True}
# A term as the admin list's query answers it: its synonyms and relatives as JSON text.
_SEIZURE_ADMIN_ROW = {
    "hpo_id": "HP:0001250",
    "label": "Seizure",
    "definition": "A seizure phenotype.",
    "is_obsolete": False,
    "replaced_by": None,
    "release_version": "test",
    "release_date": None,
    "synonyms": '["Epileptic seizure"]',
    "parents": '[{"hpo_id":"HP:0000118","label":"Phenotypic abnormality","relation":"is_a"}]',
    "children": "[]",
    "parent_count": 1,
    "child_count": 0,
    "match_rank": 0,
}
_ABNORMALITY_ROW = {
    "hpo_id": "HP:0000001",
    "label": "Abnormality",
    "definition": None,
    "is_obsolete": False,
}


@pytest.mark.asyncio
async def test_hpo_admin_terms_normalizes_json_values() -> None:
    session = _FakeSession([_FakeResult([_HPO_TABLES]), _FakeResult([dict(_SEIZURE_ADMIN_ROW)])])

    terms = await list_hpo_admin_terms(session, query="seizure", limit=100)

    assert terms[0]["synonyms"] == ["Epileptic seizure"]
    assert terms[0]["parents"] == [
        {
            "hpo_id": "HP:0000118",
            "label": "Phenotypic abnormality",
            "relation": "is_a",
        }
    ]
    assert "match_rank" not in terms[0]


@pytest.mark.asyncio
async def test_search_hpo_terms_returns_default_order_when_query_blank() -> None:
    session = _CapturingSession([dict(_ABNORMALITY_ROW)])

    result = await hpo_service.search_hpo_terms(session, query="", limit=10)

    assert result == [_ABNORMALITY_ROW]
    assert "CASE" not in session.sql
    assert "ORDER BY t.is_obsolete, t.label" in session.sql
    assert session.params["limit"] == 10


@pytest.mark.asyncio
async def test_hpo_admin_terms_accepts_null_query_parameter() -> None:
    session = _CapturingSession([dict(_ABNORMALITY_ROW)], probe=_HPO_TABLES)

    result = await list_hpo_admin_terms(session, query=None, limit=10)

    assert result[0]["hpo_id"] == "HP:0000001"
    assert result[0]["label"] == "Abnormality"
    assert result[0]["definition"] is None
    assert result[0]["is_obsolete"] is False
    assert "CASE" not in session.sql
    assert "ORDER BY t.is_obsolete, t.label" in session.sql
    assert session.params["limit"] == 10


@pytest.mark.asyncio
async def test_hpo_admin_terms_search_query_uses_ranked_matching() -> None:
    session = _CapturingSession([dict(_SEIZURE_ADMIN_ROW)], probe=_HPO_TABLES)

    terms = await list_hpo_admin_terms(session, query="seizure", limit=10)

    assert terms[0]["hpo_id"] == "HP:0001250"
    assert session.params["query"] == "seizure"
    assert session.params["like_query"] == "%seizure%"
    assert session.params["prefix_query"] == "seizure%"
    assert "CASE" in session.sql
    assert "WHERE lower(t.hpo_id) LIKE :like_query" in session.sql


# ---- The startup import, a family's phenotype files, the ontology file's path --------------------


@pytest.mark.asyncio
async def test_hpo_startup_bootstrap_imports_empty_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Without the download, the startup imports only its backup, the vendored release byte for
    # byte.
    ontology_path = tmp_path / "hpo.obo"
    ontology_path.write_bytes((Path(__file__).resolve().parents[2] / "data" / "ref-data" / "hpo" / "hp.obo").read_bytes())
    session = _FakeSession(
        [
            _FakeResult([{"hpo_term": True, "hpo_synonym": True, "hpo_edge": True, "hpo_closure": True}]),
            _FakeResult([{"count": 0}]),
        ]
    )
    captured = {}

    async def fake_import(*args, **kwargs):
        captured.update(kwargs)
        return {"terms": 1, "synonyms": 0, "edges": 0, "closure_rows": 1}

    monkeypatch.setattr(hpo_service, "import_hpo_ontology", fake_import)

    result = await ensure_hpo_ontology_on_startup(
        session,
        ontology_path=ontology_path,
        download_latest=False,
    )

    assert result is not None
    assert result["release_version"] == "hp/releases/2026-02-16"
    assert result["release_date"].isoformat() == "2026-02-16"
    assert captured["path"] == ontology_path
    assert captured["ontology_text"] == ontology_path.read_text(encoding="utf-8")
    assert captured["release_version"] == "hp/releases/2026-02-16"
    assert captured["release_date"].isoformat() == "2026-02-16"


def test_parse_hpo_tsv_reports_row_level_errors() -> None:
    text_value = "\n".join(
        [
            "family_id\tindividual_id\thpo_id\tlabel\tstatus\tonset\tevidence\tsource\tnote",
            "FAM1\tPROBAND\tHP:0001250\tSeizure\tpresent\t\t\tmanual\t",
            "FAM2\tPROBAND\tHP:0001250\tSeizure\tpresent\t\t\tmanual\t",
            "FAM1\tSIB\tbad\tBad\tpresent\t\t\tmanual\t",
            "FAM1\tSIB\tHP:0004322\tShort stature\tmaybe\t\t\tmanual\t",
        ]
    )

    rows, issues = parse_hpo_tsv_text(text_value, expected_family_id="FAM1")

    assert [row.hpo_id for row in rows] == ["HP:0001250"]
    assert [issue.code for issue in issues] == [
        "phenotype_family_mismatch",
        "phenotype_hpo_invalid",
        "phenotype_status_invalid",
    ]
    assert issues[0].line_no == 3


def test_parse_manifest_inline_hpo_present_and_absent_terms() -> None:
    rows, issues = parse_manifest_inline_hpo(
        {
            "II-3": {
                "hpo": {
                    "present": ["HP:0001250"],
                    "absent": [{"id": "HP:0004322", "label": "Short stature"}],
                }
            }
        },
        family_id="FAM1",
    )

    assert issues == []
    assert [(row.individual_id, row.hpo_id, row.status) for row in rows] == [
        ("II-3", "HP:0001250", "present"),
        ("II-3", "HP:0004322", "absent"),
    ]


def test_family_package_validation_accepts_hpo_phenotype_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Clear the configured import roots (by default /data/families) so the
    # path guard does not reject this temp package.
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "family_import_roots", [])
    package = tmp_path / "FAMHPO"
    package.mkdir()
    (package / "family.ped").write_text(
        "\n".join(
            [
                "FAMHPO FATHER 0 0 1 1",
                "FAMHPO MOTHER 0 0 2 1",
                "FAMHPO PROBAND FATHER MOTHER 2 2",
            ]
        ),
        encoding="utf-8",
    )
    (package / "phenotypes.tsv").write_text(
        (FIXTURE_DIR / "phenotypes.tsv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (package / "manifest.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "family_id: FAMHPO",
                "ped: family.ped",
                "phenotypes:",
                "  file: phenotypes.tsv",
                "  format: hpo_tsv",
                "individuals:",
                "  PROBAND:",
                "    hpo:",
                "      present:",
                "        - HP:0004322",
            ]
        ),
        encoding="utf-8",
    )

    validation = validated(package)
    phenotype_summary = next(
        summary for summary in validation.datasets if summary.dataset_type == "phenotypes"
    )

    assert validation.valid
    assert phenotype_summary.status == "valid"
    assert phenotype_summary.files == ["phenotypes.tsv"]
    assert phenotype_summary.samples == ["MOTHER", "PROBAND"]
    assert phenotype_summary.summary["rows"] == 3


def _authorized_candidate() -> Path:
    # An actual candidate path lives directly in an authorized ontology dir.
    return hpo_service.hpo_ontology_candidate_paths(None)[0]


def test_ensure_authorized_hpo_ontology_path_accepts_file_in_ref_data_dir() -> None:
    candidate = _authorized_candidate()
    assert hpo_service.ensure_authorized_hpo_ontology_path(candidate) == Path(
        os.path.normpath(str(candidate))
    )
    # A sibling file in the same authorized dir is accepted too (existence is not
    # required — this is a containment check, so a not-yet-downloaded name passes).
    sibling = candidate.parent / "custom_release.obo"
    assert hpo_service.ensure_authorized_hpo_ontology_path(sibling) == Path(
        os.path.normpath(str(sibling))
    )


@pytest.mark.parametrize("bad_path", ["/etc/passwd", "/tmp/evil.obo", "relative/hp.obo"])
def test_ensure_authorized_hpo_ontology_path_rejects_paths_outside_ref_data(bad_path: str) -> None:
    with pytest.raises(ValueError):
        hpo_service.ensure_authorized_hpo_ontology_path(bad_path)


def test_ensure_authorized_hpo_ontology_path_rejects_traversal_escape() -> None:
    traversal = _authorized_candidate().parent / ".." / ".." / ".." / "etc" / "passwd"
    with pytest.raises(ValueError):
        hpo_service.ensure_authorized_hpo_ontology_path(traversal)


def test_the_bootstrap_download_falls_back_to_a_writable_directory(monkeypatch, tmp_path) -> None:
    # Cloud Run mounts /data/ref-data read-only; the ontology is only needed until
    # it is imported, so the download lands in a temporary directory instead.
    import hashlib
    import io
    import tempfile

    from backend.app.services import hpo_service as svc

    assert svc.hpo_ontology_candidate_paths()[-1] == Path(tempfile.gettempdir()) / "coga-ref-data" / "hpo" / "hp.obo"

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    payload = b"format-version: 1.2\n\n[Term]\nid: HP:0000001\nname: All\n"
    monkeypatch.setattr(svc, "urlopen", lambda request, timeout=60: _Response(payload))
    read_only = tmp_path / "ref-data"
    read_only.mkdir()
    read_only.chmod(0o500)
    writable = tmp_path / "tmp" / "hp.obo"
    try:
        written = svc._download_hpo_ontology_file(
            "https://purl.obolibrary.org/obo/hp.obo",
            [read_only / "hpo" / "hp.obo", writable],
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
    finally:
        read_only.chmod(0o700)
    assert written == writable and writable.read_bytes() == payload
