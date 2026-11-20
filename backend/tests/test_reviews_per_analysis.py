"""A review is one analysis's (analysis_reviews.py; the owner's decision of 2026-11-20).

A family may hold several analyses on the same DNA, short-read and then long-read WGS, each signed
out on its own; the report in the EPD refers 1:1 to one analysis's signed record. So a review is
stored under the analysis it is made in, and a reading for analysis A reads A's own review of a
variant, else the shared one (stored under no analysis the family holds). A save in A writes A's
own review, from the review it read and the save's values; the shared one stays as it is. A save
of a family of several analyses with small variants names the analysis, or is refused (422).

The store is a fake kept by the session: the real repository code (the scoped reads, the own
review's insert, update and delete, the compound-het pair clear) runs over it, as does the real
clinical audit diff. The values are synthetic.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.schemas import SmallVariantReviewUpdate, StructuralVariantReviewUpdate
from app.services import (
    clinical_audit_service,
    small_variant_review_pg,
    small_variant_review_repository,
    structural_variant_review_pg,
)
from app.services.analysis_reviews import (
    ScopedReview,
    effective_reviews,
    effective_reviews_with_content,
    keeps_own_review,
    review_in_effect_sql,
    scope_review_clause,
    scoped_review,
)
from app.services.analysis_scope import NO_ANALYSIS, AnalysisNotNamed, AnalysisScope, analysis_scope, review_belongs
from app.services.family_package_records import package_sources

SR, LR = "short_read_wgs", "long_read_wgs"
HELD = frozenset({SR, LR})
A, B = "1-1000-A-G", "1-2000-C-T"
T0 = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def _scope(analysis: str | None) -> AnalysisScope:
    return AnalysisScope(analysis=analysis, held=HELD) if analysis else AnalysisScope()


# --- the scope: which analysis a reading or a review is about --------------------------------


def _record(datasets: list[str], snv_callset: str | None, registered_at: str) -> dict[str, Any]:
    return {
        "folder_path": f"/data/{registered_at}",
        "registered_at": registered_at,
        "datasets": {name: {"enabled": True} for name in datasets},
        "sources": package_sources(datasets, snv_callset),
    }


def _short_and_long_read(**long_read_datasets: Any) -> dict[str, Any]:
    return {
        "analysis_types": [LR, SR],
        "package_imports": {
            SR: _record(["snv"], "haplotypecaller", "2026-11-01"),
            LR: _record(["snv", *long_read_datasets.get("extra", [])], "deepvariant", "2026-11-10"),
        },
    }


def test_a_scope_names_the_family_s_analyses_its_shared_reviews_are_read_against() -> None:
    metadata = _short_and_long_read()
    named = analysis_scope(metadata, LR)
    assert (named.analysis, named.held, named.review_analysis) == (LR, HELD, LR)
    # Not named: it must name one before it reviews.
    unnamed = analysis_scope(metadata)
    assert unnamed.analysis is None and unnamed.unnamed_among == (SR, LR) and unnamed.held == HELD
    with pytest.raises(AnalysisNotNamed):
        unnamed.require_named()
    # A family that holds none stores its reviews under none.
    whole = analysis_scope({})
    assert whole.require_named() is whole and whole.review_analysis == NO_ANALYSIS


def test_the_structural_variants_scope_is_told_by_the_analyses_with_structural_variants() -> None:
    # Only the long-read analysis's record lists an SV caller: its SVs, and their reviews, are
    # the long-read analysis's without naming it; its small variants must still name one.
    metadata = _short_and_long_read(extra=["sv_needlr"])
    assert analysis_scope(metadata, kind="structural_variants").analysis == LR
    assert analysis_scope(metadata).unnamed_among == (SR, LR)
    # A family whose analyses both list an SV caller names the analysis of an SV review too.
    both = _short_and_long_read(extra=["sv_needlr"])
    both["package_imports"][SR] = _record(["snv", "cnv"], "haplotypecaller", "2026-11-01")
    with pytest.raises(AnalysisNotNamed):
        analysis_scope(both, kind="structural_variants").require_named()


# --- the effective review --------------------------------------------------------------------


def _row(variant_id: str, analysis: str, *, updated_at: datetime = T0, **fields: Any) -> dict[str, Any]:
    return {"variant_id": variant_id, "analysis": analysis, "updated_at": updated_at, **fields}


def test_an_analysis_reads_its_own_review_else_the_shared_one_never_another_analysis_s() -> None:
    rows = [
        _row(A, SR, tags=["report"]),
        _row(A, LR, tags=["review"]),
        _row(B, SR, tags=["report"]),
        _row("1-3000-G-A", NO_ANALYSIS, tags=["report"]),
        _row("1-4000-T-C", NO_ANALYSIS, tags=["report"]),
        _row("1-4000-T-C", LR, tags=[]),
    ]
    long_read = effective_reviews(rows, _scope(LR))
    assert {variant_id: review["analysis"] for variant_id, review in long_read.items()} == {
        A: LR,
        "1-3000-G-A": NO_ANALYSIS,
        "1-4000-T-C": LR,
    }
    short_read = effective_reviews(rows, _scope(SR))
    assert {variant_id: review["analysis"] for variant_id, review in short_read.items()} == {
        A: SR,
        B: SR,
        "1-3000-G-A": NO_ANALYSIS,
        "1-4000-T-C": NO_ANALYSIS,
    }
    # The long-read analysis's empty review hides the shared one: it reads as no review.
    with_content = effective_reviews_with_content(rows, _scope(LR), lambda review: bool(review.get("tags")))
    assert sorted(with_content) == [A, "1-3000-G-A"]
    # The order the rows come in does not decide.
    assert effective_reviews(list(reversed(rows)), _scope(LR)) == long_read


def test_a_reading_of_the_whole_family_prefers_the_review_stored_under_no_analysis() -> None:
    rows = [_row(A, LR, updated_at=T0 + timedelta(hours=1)), _row(A, NO_ANALYSIS), _row(B, SR), _row(B, LR, updated_at=T0 + timedelta(hours=1))]
    whole = effective_reviews(rows, AnalysisScope())
    assert (whole[A]["analysis"], whole[B]["analysis"]) == (NO_ANALYSIS, LR)


def test_review_belongs_is_the_analysis_s_own_or_one_of_no_analysis_the_family_holds() -> None:
    assert review_belongs(LR, HELD, LR) and review_belongs(LR, HELD, NO_ANALYSIS)
    assert not review_belongs(LR, HELD, SR)
    assert review_belongs(None, HELD, SR)


def test_a_save_s_own_and_shared_review_and_when_an_empty_own_review_is_kept() -> None:
    reviews = scoped_review([_row(A, NO_ANALYSIS, tags=["report"]), _row(A, SR, tags=["x"])], _scope(LR))
    assert reviews.own is None and reviews.shared is not None and reviews.effective is reviews.shared

    def has_content(review: dict[str, Any]) -> bool:
        return bool(review.get("tags"))

    # Emptied, the analysis's own review is kept while it hides a shared review with content.
    assert keeps_own_review(reviews, has_content, {"tags": []})
    assert not keeps_own_review(ScopedReview(own={"tags": ["x"]}), has_content, {"tags": []})
    assert keeps_own_review(ScopedReview(), has_content, {"tags": ["report"]})


def test_the_sql_condition_binds_the_analysis_and_the_family_s_analyses() -> None:
    params: dict[str, Any] = {}
    assert scope_review_clause(AnalysisScope(), params) == "TRUE" and params == {}
    clause = scope_review_clause(_scope(LR), params, column="member.analysis")
    assert clause == (
        "(member.analysis = CAST(:review_scope_analysis AS text) "
        "OR NOT (member.analysis = ANY(CAST(:review_scope_held AS text[]))))"
    )
    assert params == {"review_scope_analysis": LR, "review_scope_held": sorted(HELD)}


def test_the_explorer_condition_takes_only_its_own_identifiers() -> None:
    sql = review_in_effect_sql("r", "small_variant_reviews")
    assert "r.analysis IN" in sql and "FROM small_variant_reviews own" in sql
    for alias, table in (("r; DROP", "small_variant_reviews"), ("r", "users")):
        with pytest.raises(ValueError):
            review_in_effect_sql(alias, table)


# --- the save: a fake store of review rows, keyed by analysis and variant ----------------------

_COLUMNS = (
    "variant_key",
    "variant_id",
    "classification",
    "tags",
    "tag_metadata",
    "note",
    *small_variant_review_repository._compound_het_clear_payload(),
    "acmg",
    "acmg_point_total",
    "acmg_class",
    "acmg_evidence_snapshot",
    "updated_by",
    "updated_at",
)


def _review(variant_id: str, analysis: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": f"review-{analysis or 'shared'}-{variant_id}",
        "analysis": analysis,
        "variant_key": int(variant_id.split("-")[1]),
        "variant_id": variant_id,
        "classification": None,
        "tags": [],
        "tag_metadata": {},
        "note": None,
        **small_variant_review_repository._compound_het_clear_payload(),
        "acmg": None,
        "acmg_point_total": None,
        "acmg_class": None,
        "acmg_evidence_snapshot": None,
        "updated_by": "analyst-b",
        "updated_at": T0,
        "created_at": T0,
    }
    row.update(fields)
    for tags_key, metadata_key in (("tags", "tag_metadata"), ("compound_het_tags", "compound_het_tag_metadata")):
        if metadata_key not in fields:
            row[metadata_key] = {tag: {"updated_by": "analyst-b", "updated_at": T0} for tag in row[tags_key]}
    return row


class _Store:
    """The family's review rows, written through the statements the repository sends; an UPDATE
    or DELETE matches its row by id, and an INSERT is refused where the unique index would."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {row["id"]: copy.deepcopy(row) for row in rows}
        self.commits = 0
        self.events: list[dict[str, Any]] = []

    def of(self, analysis: str) -> dict[str, dict[str, Any]]:
        return {row["variant_id"]: row for row in self.rows.values() if row["analysis"] == analysis}

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        params = params or {}
        if sql.startswith("UPDATE small_variant_reviews"):
            row = self.rows[params["review_id"]]
            row.update({key: params.get(key) for key in _COLUMNS})
        elif sql.startswith("DELETE FROM small_variant_reviews"):
            self.rows.pop(params["review_id"])
        elif sql.startswith("INSERT INTO small_variant_reviews"):
            assert (params["analysis"], params["variant_id"]) not in {
                (row["analysis"], row["variant_id"]) for row in self.rows.values()
            }, "a second review of one variant in one analysis"
            row_id = f"review-{params['analysis'] or 'shared'}-{params['variant_id']}"
            self.rows[row_id] = {
                "id": row_id,
                "analysis": params["analysis"],
                "created_at": params["created_at"],
                **{key: params.get(key) for key in _COLUMNS},
            }
        return SimpleNamespace(
            rowcount=1,
            mappings=lambda: SimpleNamespace(first=lambda: None, all=lambda: []),
            scalar_one_or_none=lambda: None,
        )

    async def commit(self) -> None:
        self.commits += 1


def _fetch_rows(store: _Store, *, where_clause: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows a scoped read selects (``analysis_reviews.scope_review_clause``)."""
    if "review_scope_analysis" in params:
        analysis, held = params["review_scope_analysis"], frozenset(params["review_scope_held"])
    else:
        analysis, held = None, frozenset()
    rows = [row for row in store.rows.values() if review_belongs(analysis, held, row["analysis"])]
    if "group_id" in params:
        members = {row["variant_id"] for row in rows if row["compound_het_group_id"] == params["group_id"]}
        rows = [row for row in rows if row["variant_id"] in members]
    elif "variant_id" in params:
        rows = [row for row in rows if row["variant_id"] == params["variant_id"]]
    else:
        rows = [row for row in rows if row["variant_id"] in params["variant_ids"]]
    return copy.deepcopy(rows)


def _context(scope: AnalysisScope) -> SimpleNamespace:
    return SimpleNamespace(
        family_uuid="family-uuid",
        family_id="FAM-SRLR",
        assembly_name="GRCh38",
        assembly_id=None,
        project_ids=[],
        affected_sample_names=["PROBAND"],
        scope=scope,
    )


def _save(monkeypatch, store: _Store, payload: SmallVariantReviewUpdate, *, scope: AnalysisScope, variant_id: str = A):
    seen: dict[str, Any] = {}

    async def variant(*, variant_id: str, left_out_sources: Any = (), **_kwargs: Any) -> Any:
        seen.setdefault("left_out_sources", set(left_out_sources))
        return SimpleNamespace(variant_key=int(variant_id.split("-")[1]), gene_symbols=["GENE_CH"])

    async def fetch_rows(session: _Store, *, where_clause: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return _fetch_rows(session, where_clause=where_clause, params=params)

    async def tag_definitions(*_args: Any, **_kwargs: Any) -> list[Any]:
        return [SimpleNamespace(key=key) for key in ("review", "report")]

    async def record(_session: Any, **event: Any) -> None:
        store.events.append(event)

    async def lock(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(small_variant_review_pg, "get_small_variant_family_record", variant)
    monkeypatch.setattr(small_variant_review_pg, "variants_share_gene", lambda *_args: True)
    monkeypatch.setattr(small_variant_review_pg, "has_affected_het_call", lambda *_args: True)
    monkeypatch.setattr(small_variant_review_pg, "list_small_variant_tag_definitions", tag_definitions)
    monkeypatch.setattr(small_variant_review_pg, "_lock_review", lock)
    monkeypatch.setattr(small_variant_review_repository, "_fetch_review_rows", fetch_rows)
    monkeypatch.setattr(clinical_audit_service, "record_clinical_event", record)
    user = SimpleNamespace(id=None, username="analyst-c", email="c@example.org")
    saved = asyncio.run(
        small_variant_review_pg.upsert_small_variant_review(
            store, context=_context(scope), variant_id=variant_id, payload=payload, user=user  # type: ignore[arg-type]
        )
    )
    return saved, seen


def test_a_save_that_names_no_analysis_of_a_family_of_several_is_refused(monkeypatch) -> None:
    store = _Store([])
    unnamed = AnalysisScope(unnamed_among=(SR, LR), held=HELD)
    with pytest.raises(AnalysisNotNamed) as refused:
        _save(monkeypatch, store, SmallVariantReviewUpdate(tags=["report"]), scope=unnamed)
    assert "name the analysis" in str(refused.value)
    assert store.rows == {} and store.events == [] and store.commits == 0


def test_a_save_in_one_analysis_is_that_analysis_s_review_only(monkeypatch) -> None:
    store = _Store([])
    scope = AnalysisScope(analysis=LR, held=HELD, left_out={"small_variants": frozenset({"haplotypecaller"})})
    saved, seen = _save(monkeypatch, store, SmallVariantReviewUpdate(tags=["report"]), scope=scope)

    assert saved.tags == ["report"]
    assert list(store.of(LR)) == [A] and store.of(SR) == {} and store.of(NO_ANALYSIS) == {}
    # The variant is read from the analysis's own callset: the other analysis's is left out.
    assert seen["left_out_sources"] == {"haplotypecaller"}
    # Its audit events name the analysis.
    assert [(event["summary"], event["metadata"]) for event in store.events] == [
        ("Tags added report", {"analysis": LR})
    ]
    # Reviewed in the other analysis with another classification: each keeps its own.
    _save(monkeypatch, store, SmallVariantReviewUpdate(classification="VUS"), scope=_scope(SR))
    _save(
        monkeypatch,
        store,
        SmallVariantReviewUpdate(classification="Likely pathogenic", tags=["report"], expected_updated_at=store.of(LR)[A]["updated_at"]),
        scope=_scope(LR),
    )
    assert (store.of(SR)[A]["classification"], store.of(SR)[A]["tags"]) == ("VUS", [])
    assert (store.of(LR)[A]["classification"], store.of(LR)[A]["tags"]) == ("Likely pathogenic", ["report"])


def test_a_save_over_a_shared_review_writes_the_analysis_s_own_from_it(monkeypatch) -> None:
    acmg = {"criteria": [{"code": "PM2", "strength": "moderate", "accepted": True}], "point_total": 2}
    shared = _review(A, NO_ANALYSIS, tags=["report"], note="seen before the package", acmg=acmg, acmg_class="acmg_class_3")
    store = _Store([shared])
    # The client loaded the shared review, the long-read analysis's effective one: its version.
    payload = SmallVariantReviewUpdate(tags=["report", "review"], note="seen before the package", expected_updated_at=T0)
    saved, _seen = _save(monkeypatch, store, payload, scope=_scope(LR))

    own = store.of(LR)[A]
    assert (own["tags"], own["note"]) == (["report", "review"], "seen before the package")
    # What the save left out is kept from the review it read: its ACMG record and who added each tag.
    assert own["acmg"] == acmg and own["acmg_class"] == "acmg_class_3"
    assert own["tag_metadata"]["report"] == {"updated_by": "analyst-b", "updated_at": T0}
    assert own["tag_metadata"]["review"]["updated_by"] == "analyst-c"
    # The shared review stays as it is: the short-read analysis still reads it.
    assert store.of(NO_ANALYSIS)[A] == shared
    assert saved.tags == ["report", "review"]
    # Audited against the review the analysis read, in the analysis.
    assert [(event["summary"], event["metadata"]) for event in store.events] == [
        ("Tags added review", {"analysis": LR})
    ]


def test_a_save_checked_against_a_shared_review_changed_since_is_refused(monkeypatch) -> None:
    store = _Store([_review(A, NO_ANALYSIS, tags=["report"], updated_at=T0 + timedelta(minutes=5))])
    with pytest.raises(HTTPException) as refused:
        _save(monkeypatch, store, SmallVariantReviewUpdate(tags=[], expected_updated_at=T0), scope=_scope(LR))
    assert refused.value.status_code == 409
    assert store.of(LR) == {} and store.commits == 0


def test_clearing_an_analysis_s_review_keeps_it_empty_to_hide_the_shared_one(monkeypatch) -> None:
    shared = _review(A, NO_ANALYSIS, tags=["report"])
    store = _Store([shared])
    saved, _seen = _save(monkeypatch, store, SmallVariantReviewUpdate(tags=[], expected_updated_at=T0), scope=_scope(LR))

    assert (saved.tags, saved.updated_at) == ([], None)
    own = store.of(LR)[A]
    assert own["tags"] == [] and own["classification"] is None
    assert store.of(NO_ANALYSIS)[A] == shared
    assert [(event["summary"], event["metadata"]) for event in store.events] == [
        ("Tags removed report", {"analysis": LR})
    ]
    # The long-read analysis reads the variant as not reviewed; the short-read one the shared review.
    reviews = asyncio.run(
        small_variant_review_pg.get_small_variant_review_map(
            store, family_uuid="family-uuid", variant_ids=[A], scope=_scope(LR)  # type: ignore[arg-type]
        )
    )
    assert reviews == {}
    reviews = asyncio.run(
        small_variant_review_pg.get_small_variant_review_map(
            store, family_uuid="family-uuid", variant_ids=[A], scope=_scope(SR)  # type: ignore[arg-type]
        )
    )
    assert reviews[A].tags == ["report"]
    # A later save over the empty review, which the client loaded as none, is no conflict.
    _save(monkeypatch, store, SmallVariantReviewUpdate(tags=["review"], expected_updated_at=None), scope=_scope(LR))
    assert store.of(LR)[A]["tags"] == ["review"]


def test_clearing_a_review_with_no_shared_one_deletes_it(monkeypatch) -> None:
    store = _Store([_review(A, LR, tags=["report"])])
    _save(monkeypatch, store, SmallVariantReviewUpdate(tags=[], expected_updated_at=T0), scope=_scope(LR))
    assert store.rows == {}


def test_a_family_that_holds_no_analysis_stores_its_reviews_under_none(monkeypatch) -> None:
    store = _Store([])
    _save(monkeypatch, store, SmallVariantReviewUpdate(tags=["report"]), scope=AnalysisScope())
    assert list(store.of(NO_ANALYSIS)) == [A]
    assert store.events[0]["metadata"] == {"analysis": NO_ANALYSIS}


def test_breaking_up_a_shared_pair_in_one_analysis_leaves_it_in_the_other(monkeypatch) -> None:
    pair = {"compound_het_gene": "GENE_CH", "compound_het_phase_status": "unknown", "compound_het_updated_by": "analyst-b"}
    store = _Store(
        [
            _review(A, NO_ANALYSIS, compound_het_group_id="g1", compound_het_partner_variant_ids=[B], **pair),
            _review(B, NO_ANALYSIS, tags=["report"], compound_het_group_id="g1", compound_het_partner_variant_ids=[A], **pair),
        ]
    )
    _save(monkeypatch, store, SmallVariantReviewUpdate(tags=["review"], compound_het=None), scope=_scope(LR))

    long_read = store.of(LR)
    # The long-read analysis holds its own reviews of both variants, without the pair; the
    # partner's keeps its tag.
    assert (long_read[A]["tags"], long_read[A]["compound_het_group_id"]) == (["review"], None)
    assert (long_read[B]["tags"], long_read[B]["compound_het_group_id"]) == (["report"], None)
    # The shared pair stays: the short-read analysis still reads it.
    shared = store.of(NO_ANALYSIS)
    assert shared[A]["compound_het_group_id"] == shared[B]["compound_het_group_id"] == "g1"
    assert all(event["metadata"]["analysis"] == LR for event in store.events)
    assert f"Compound-het pair with {B} removed" in [event["summary"] for event in store.events]


def test_pairing_in_one_analysis_writes_both_reviews_in_it(monkeypatch) -> None:
    store = _Store([_review(B, SR, tags=["report"])])
    _save(monkeypatch, store, SmallVariantReviewUpdate(compound_het={"partner_variant_id": B, "tags": []}), scope=_scope(LR))
    long_read = store.of(LR)
    assert long_read[A]["compound_het_partner_variant_ids"] == [B]
    assert long_read[B]["compound_het_partner_variant_ids"] == [A]
    # The short-read analysis's review of the partner is not the long-read analysis's: untouched,
    # and the long-read analysis's review of the partner holds nothing of it.
    assert store.of(SR)[B]["compound_het_group_id"] is None and long_read[B]["tags"] == []


# --- the structural-variant review save -------------------------------------------------------


class _SvStore:
    """The family's SV review rows, written through the statements the save sends."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {row["id"]: dict(row) for row in rows}
        self.events: list[dict[str, Any]] = []
        self.commits = 0

    def of(self, analysis: str) -> dict[str, dict[str, Any]]:
        return {row["variant_id"]: row for row in self.rows.values() if row["analysis"] == analysis}

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        params = params or {}
        found: list[dict[str, Any]] = []
        if sql.startswith("SELECT"):
            scope = (
                (params["review_scope_analysis"], frozenset(params["review_scope_held"]))
                if "review_scope_analysis" in params
                else (None, frozenset())
            )
            found = [
                dict(row)
                for row in self.rows.values()
                if row["variant_id"] == params.get("variant_id") and review_belongs(scope[0], scope[1], row["analysis"])
            ]
        elif sql.startswith("UPDATE structural_variant_reviews"):
            self.rows[params["review_id"]].update(
                classification=params["classification"], note=params["note"], updated_at=params["updated_at"],
                tags=json.loads(params["tags_json"]),
            )
        elif sql.startswith("DELETE FROM structural_variant_reviews"):
            self.rows.pop(params["review_id"])
        elif sql.startswith("INSERT INTO structural_variant_reviews"):
            row_id = f"sv-{params['analysis'] or 'shared'}-{params['variant_id']}"
            assert row_id not in self.rows
            self.rows[row_id] = {
                "id": row_id,
                "analysis": params["analysis"],
                "variant_id": params["variant_id"],
                "classification": params["classification"],
                "tags": json.loads(params["tags_json"]),
                "tag_metadata": {},
                "note": params["note"],
                "cnv_acmg": None,
                "cnv_point_total": None,
                "cnv_class": None,
                "cnv_evidence_snapshot": None,
                "updated_by": params["updated_by"],
                "updated_at": params["updated_at"],
            }
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: found, first=lambda: None))

    async def commit(self) -> None:
        self.commits += 1


def _sv_save(monkeypatch, store: _SvStore, payload: StructuralVariantReviewUpdate, *, scope: AnalysisScope):
    async def record(_session: Any, **event: Any) -> None:
        store.events.append(event)

    async def lock(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def tag_definitions(*_args: Any, **_kwargs: Any) -> list[Any]:
        return [SimpleNamespace(key=key) for key in ("review", "report")]

    monkeypatch.setattr(structural_variant_review_pg, "_lock_review", lock)
    monkeypatch.setattr(structural_variant_review_pg, "list_small_variant_tag_definitions", tag_definitions)
    monkeypatch.setattr(clinical_audit_service, "record_clinical_event", record)
    user = SimpleNamespace(id=None, username="analyst-c", email="c@example.org")
    return asyncio.run(
        structural_variant_review_pg.upsert_structural_variant_review(
            store, context=_context(scope), variant_id="1-100-900-DEL", payload=payload, user=user  # type: ignore[arg-type]
        )
    )


def _sv_row(analysis: str, **fields: Any) -> dict[str, Any]:
    # Who added each tag, as every save records it beside the tags.
    fields.setdefault(
        "tag_metadata", {tag: {"updated_by": "analyst-b", "updated_at": T0} for tag in fields.get("tags", [])}
    )
    return {
        "id": f"sv-{analysis or 'shared'}-1-100-900-DEL",
        "analysis": analysis,
        "variant_id": "1-100-900-DEL",
        "classification": None,
        "tags": [],
        "tag_metadata": {},
        "note": None,
        "cnv_acmg": None,
        "cnv_point_total": None,
        "cnv_class": None,
        "cnv_evidence_snapshot": None,
        "updated_by": "analyst-b",
        "updated_at": T0,
        **fields,
    }


def test_an_sv_review_is_one_analysis_s_too(monkeypatch) -> None:
    unnamed = AnalysisScope(unnamed_among=(SR, LR), held=HELD)
    with pytest.raises(AnalysisNotNamed):
        _sv_save(monkeypatch, _SvStore([]), StructuralVariantReviewUpdate(tags=["report"]), scope=unnamed)

    shared = _sv_row(NO_ANALYSIS, tags=["report"], classification="Pathogenic")
    store = _SvStore([shared])
    saved = _sv_save(
        monkeypatch,
        store,
        StructuralVariantReviewUpdate(tags=["report"], classification="Likely pathogenic", expected_updated_at=T0),
        scope=_scope(LR),
    )
    assert saved.classification == "Likely pathogenic"
    assert store.of(LR)["1-100-900-DEL"]["classification"] == "Likely pathogenic"
    assert store.of(NO_ANALYSIS)["1-100-900-DEL"] == shared
    assert {event["metadata"]["analysis"] for event in store.events} == {LR}
    assert {event["metadata"]["modality"] for event in store.events} == {"sv"}

    # Cleared in the short-read analysis: kept empty, hiding the shared review there.
    _sv_save(monkeypatch, store, StructuralVariantReviewUpdate(tags=[], expected_updated_at=T0), scope=_scope(SR))
    assert store.of(SR)["1-100-900-DEL"]["tags"] == [] and store.of(SR)["1-100-900-DEL"]["classification"] is None
    assert store.of(NO_ANALYSIS)["1-100-900-DEL"] == shared
