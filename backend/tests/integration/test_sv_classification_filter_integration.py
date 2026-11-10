"""The SV search's classification filter keeps an SV by the class its review shows (CLIN-77).

As for small variants (CLIN-75), the structural-variant
search's classification filter keeps an SV when the class its review shows on the SV page equals
a requested class: never drops one shown with that class, never keeps one shown with another. The
SV table and cards show a review's class from its text and its tags (``normalizeReviewClassification``;
the server's ``review_class_label``): a bare class word in the text, else the most severe class tag,
else the text. A CNV (ClinGen) scoring counts through the class label the CNV dialog writes as the
text; its stored class (``cnv_class``) alone is not shown, so it is not read.

Here the reviews are real Postgres rows (``structural_variant_reviews``) of one family, one per SV,
and the SVs real ClickHouse rows: the SV list (``get_family_structural_variants_page``), unranked
and ranked, and its CSV export (``export_family_structural_variants``) each keep, for each filter,
the SVs whose review shows the class asked for, and each SV they keep is shown with it.

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke``
job sets it.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import fresh_family, proband_context, run_closing_stores

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"

# One SV per review: variant id, classification text, tags, CNV class (the CNV dialog writes its
# class label as the text, and the class key in cnv_class), and the class the SV page shows.
SV_REVIEWS: tuple[tuple[str, str | None, list[str], str | None, str], ...] = (
    ("sv-tag", None, ["acmg_class_4", "report"], None, "Likely pathogenic - class 4"),
    ("sv-word", "VUS", [], None, "VUS - class 3"),
    ("sv-text-tag", "VUS - class 3", ["acmg_class_4"], None, "Likely pathogenic - class 4"),
    ("sv-word-tag", "VUS", ["acmg_class_4"], None, "VUS - class 3"),
    ("sv-cnv", "Likely pathogenic - class 4", [], "cnv_class_4", "Likely pathogenic - class 4"),
    ("sv-cnv-tag", "VUS - class 3", ["acmg_class_5"], "cnv_class_3", "Pathogenic - class 5"),
    ("sv-cnv-only", None, [], "cnv_class_4", ""),
    ("sv-free", "Some free text", [], None, "Some free text"),
    ("sv-free-tag", "Some free text", ["acmg_class_1"], None, "Benign - class 1"),
)
# An SV without a review: no class filter keeps it.
_UNREVIEWED = "sv-unreviewed"

# The filter values of the owner's table: each class label, a bare class word and free text.
FILTER_VALUES = (
    "Pathogenic - class 5",
    "Likely pathogenic - class 4",
    "VUS - class 3",
    "Benign - class 1",
    "VUS",
    "some free TEXT",
)

# What each filter keeps: the SVs whose review shows that class.
_KEPT = {
    "Pathogenic - class 5": {"sv-cnv-tag"},
    "Likely pathogenic - class 4": {"sv-tag", "sv-text-tag", "sv-cnv"},
    "VUS - class 3": {"sv-word", "sv-word-tag"},
    "Benign - class 1": {"sv-free-tag"},
    "VUS": {"sv-word", "sv-word-tag"},
    "some free TEXT": {"sv-free"},
}


def _sv(variant_id: str, start: int) -> Any:
    from backend.app.services.clickhouse_variant_records import StructuralVariantCall, StructuralVariantRecord

    return StructuralVariantRecord(
        variant_key=None, variant_id=variant_id, chr="1", start=start, end=start + 50_000, sv_type="DEL",
        source="hificnv", remote_chr=None, remote_start=None, remote_end=None, sv_len=-50_000,
        filters=["PASS"], gene_symbols=[], annotations=[{}],
        calls=[StructuralVariantCall(sample="PROBAND", gt="0/1", qual=50.0, read_support=None, filter="PASS")],
    )


async def seed_sv_reviews(session_factory: Any, label: str) -> tuple[Any, list[str]]:
    """A family of one affected proband with the SVs of ``SV_REVIEWS`` and an unreviewed one in
    ClickHouse and their reviews in Postgres; returns its context and every SV id."""
    from sqlalchemy import text

    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_structural_variant_records,
    )

    async with session_factory() as session:
        family_uuid = await fresh_family(session, f"{label}-{uuid4().hex[:12]}")
        for variant_id, classification, tags, cnv_class, _shown in SV_REVIEWS:
            cnv_acmg = (
                {"kind": "loss", "criteria": [], "point_total": None, "classification": classification}
                if cnv_class
                else None
            )
            await session.execute(
                text(
                    "INSERT INTO structural_variant_reviews (family_id, variant_id, classification, tags, "
                    "cnv_acmg, cnv_class, updated_by) VALUES (CAST(:f AS uuid), :v, :c, CAST(:t AS jsonb), "
                    "CAST(:a AS jsonb), :k, 'reviewer')"
                ),
                {
                    "f": family_uuid,
                    "v": variant_id,
                    "c": classification,
                    "t": json.dumps(tags),
                    "a": json.dumps(cnv_acmg) if cnv_acmg else None,
                    "k": cnv_class,
                },
            )
        await session.commit()
    project = str(uuid4())
    variant_ids = [*(review[0] for review in SV_REVIEWS), _UNREVIEWED]
    await ensure_clickhouse_variant_tables(_ASSEMBLY)
    await insert_structural_variant_records(
        _ASSEMBLY,
        family_uuid,
        [project],
        [_sv(variant_id, 1_000_000 + 100_000 * index) for index, variant_id in enumerate(variant_ids)],
    )
    return proband_context(family_uuid, f"{label}-family", project), variant_ids


async def kept_by_sv_search(session_factory: Any, context: Any, wanted: str) -> dict[str, tuple[set[str], set[str]]]:
    """The SVs the SV list, unranked and ranked, and its CSV export keep for the classification
    filter ``wanted``, each with the classes their reviews show."""
    from backend.app.services.clickhouse_family_variants import (
        export_family_structural_variants,
        get_family_structural_variants_page,
    )
    from backend.app.services.family_variant_filters import StructuralVariantQueryFilters
    from backend.app.services.review_pg_utils import review_class_label

    def _kept(variants: Any) -> tuple[set[str], set[str]]:
        return (
            {str(variant.id) for variant in variants},
            {
                review_class_label(variant.review.classification, variant.review.tags) or ""
                for variant in variants
                if variant.review is not None
            },
        )

    out: dict[str, tuple[set[str], set[str]]] = {}
    async with session_factory() as session:
        for path in ("list", "ranked", "export"):
            filters = StructuralVariantQueryFilters(page=1, page_size=100, review_classifications=[wanted])
            if path == "export":
                variants = (await export_family_structural_variants(session, context=context, filters=filters)).rows
            else:
                variants = (
                    await get_family_structural_variants_page(
                        session, context=context, page=1, page_size=100, filters=filters, prioritize=path == "ranked"
                    )
                ).variants
            out[path] = _kept(variants)
    return out


def test_the_sv_classification_filter_keeps_an_sv_by_the_class_its_review_shows() -> None:
    # Kept by the class shown, not by the text alone: a class tag (sv-tag, sv-text-tag) or a
    # bare class word in the text counts, as the SV page reads them.
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clickhouse_variant_storage import delete_family_structural_variants

    async def _scenario() -> dict[str, dict[str, tuple[set[str], set[str]]]]:
        session_factory = get_postgres_sessionmaker()
        context, _ids = await seed_sv_reviews(session_factory, "sv-shown-class")
        try:
            return {wanted: await kept_by_sv_search(session_factory, context, wanted) for wanted in FILTER_VALUES}
        finally:
            await delete_family_structural_variants(_ASSEMBLY, context.family_uuid)

    answers = run_closing_stores(_scenario, init_schema=True)
    shown = {review[0]: review[4] for review in SV_REVIEWS}
    for wanted, expected in _KEPT.items():
        # Every filter keeps exactly the SVs whose review shows the class asked for.
        assert expected == {variant_id for variant_id, label in shown.items() if label and _asks_for(wanted, label)}
        for path, (kept, classes) in answers[wanted].items():
            assert kept == expected, (wanted, path)
            # Each SV a class filter keeps is shown with that class.
            assert classes == {shown[variant_id] for variant_id in expected}, (wanted, path)
            assert len(classes) == 1, (wanted, path)
    # The SV scored by the CNV dialog and then cleared of its text shows no class: no filter keeps it.
    assert not any("sv-cnv-only" in kept for result in answers.values() for kept, _classes in result.values())


def _asks_for(wanted: str, label: str) -> bool:
    from backend.app.services.review_pg_utils import review_class_filter

    return label.lower() in review_class_filter([wanted])
