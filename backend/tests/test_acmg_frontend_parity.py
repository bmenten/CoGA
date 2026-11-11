"""The server's ACMG and CNV criterion tables match the classification dialogs'.

The small-variant dialog offers each ACMG criterion only the strengths of its
``allowedStrengths``, and the CNV dialog each ClinGen criterion only the points of its range.
The server keeps its own copy of both tables: it refuses a strength a criterion does not take
(``acmg_points.ALLOWED_STRENGTHS``) and keeps a point value within the criterion's range
(``cnv_acmg_points``). These tests read the TypeScript catalogues and fail when the two
differ: a strength the dialog offers that the server refused would make a classification
impossible to save, and one the server took that the dialog does not offer could be stored
only by a client calling the API.

The missense Z the dialog's PP2 suggestion reads is frozen by the server with the
classification: the dialog's sources and their order are the server's.

The class labels the server stores with a classification are the ones the dialogs show, and
the built-in classification and VUS-tier tags carry them; all are in sentence case.

The server reads the class a review shows from its text and its tags as the family page does
(``review_class_label`` and ``normalizeReviewClassification``): both read one table of cases.
The classification filters keep a review by that class; the SV page reads an
SV review's class by the same rule, and the SV search's filter keeps an SV by it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.schemas import (
    AcmgClassificationPayload,
    AcmgCriterionSelection,
    CnvAcmgClassificationPayload,
    CnvAcmgCriterion,
)
from backend.app.services import acmg_points, cnv_acmg_points
from backend.app.services.small_variant_review_acmg import (
    MISSENSE_Z_FROM_GENE_PROFILE,
    MISSENSE_Z_FROM_VCF,
    _normalize_acmg_payload,
)
from backend.app.services.structural_variant_review_pg import _normalize_cnv_acmg_payload

FRONTEND_LIB = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib"
ACMG_CRITERIA_TS = FRONTEND_LIB / "acmg" / "criteria.ts"
ACMG_EVALUATE_TS = FRONTEND_LIB / "acmg" / "evaluate.ts"
CNV_CRITERIA_TS = FRONTEND_LIB / "cnvAcmg" / "criteria.ts"

_QUOTED = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""
_NUMBER = r"-?\d+(?:\.\d+)?"


def _array_body(source: str, declaration: str) -> str:
    """The text of the array ``declaration`` opens, up to the ``];`` that closes it."""
    head = re.search(re.escape(declaration) + r"\s*=\s*\[", source)
    assert head, f"{declaration} not found"
    end = source.find("\n];", head.end())
    assert end > 0, f"the end of {declaration} not found"
    return source[head.end() : end]


def _strengths(literal: str) -> frozenset[str]:
    return frozenset(re.findall(r"'(\w+)'", literal))


def _field(criterion: str, name: str, value: str = r"'(\w+)'") -> str:
    """The value of the field ``name`` in the text of one criterion object."""
    match = re.search(rf"^\s*{name}:\s*{value}", criterion, re.M)
    assert match, f"{name} of a criterion in criteria.ts could not be read:\n{criterion}"
    return match.group(1)


def _dialog_acmg_criteria() -> dict[str, tuple[str, str, frozenset[str]]]:
    """code → (direction, default strength, allowed strengths), as criteria.ts defines them."""
    source = ACMG_CRITERIA_TS.read_text(encoding="utf-8")
    # The shared strength lists (PATHOGENIC_STRENGTHS, BENIGN_STRENGTHS) a criterion may name.
    named = {
        name: _strengths(body) for name, body in re.findall(r"const (\w+): AcmgStrength\[\] = \[([^\]]*)\]", source)
    }
    body = _array_body(source, "export const ACMG_CRITERIA: AcmgCriterionDef[]")
    # One chunk per criterion object, from its ``code:`` to the next criterion's.
    starts = [match.start() for match in re.finditer(r"^\s*code:", body, re.M)]
    criteria: dict[str, tuple[str, str, frozenset[str]]] = {}
    for start, end in zip(starts, starts[1:] + [len(body)]):
        chunk = body[start:end]
        code = _field(chunk, "code")
        allowed = _field(chunk, "allowedStrengths", r"(\[[^\]]*\]|\w+)")
        assert code not in criteria, f"{code} is defined twice in criteria.ts"
        criteria[code] = (
            _field(chunk, "direction"),
            _field(chunk, "defaultStrength"),
            _strengths(allowed) if allowed.startswith("[") else named[allowed],
        )
    return criteria


def _dialog_strength_points() -> dict[str, int]:
    source = ACMG_CRITERIA_TS.read_text(encoding="utf-8")
    body = re.search(r"export const STRENGTH_POINTS: Record<AcmgStrength, number> = \{([^}]*)\}", source)
    assert body, "STRENGTH_POINTS not found in criteria.ts"
    return {name: int(points) for name, points in re.findall(rf"(\w+):\s*({_NUMBER})", body.group(1))}


def _dialog_cnv_criteria(kind: str) -> dict[str, tuple[str, float, float, float]]:
    """code → (section, default, min, max) of the CNV loss or gain catalogue in criteria.ts."""
    source = CNV_CRITERIA_TS.read_text(encoding="utf-8")
    body = _array_body(source, f"export const CNV_{kind.upper()}_CRITERIA: CnvCriterionDef[]")
    calls = re.findall(
        rf"\bc\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(?:{_QUOTED})\s*,"
        rf"\s*({_NUMBER})\s*,\s*({_NUMBER})\s*,\s*({_NUMBER})\s*,?\s*\)",
        body,
    )
    assert len(calls) == len(re.findall(r"\bc\(", body)), f"a {kind} criterion in criteria.ts could not be read"
    criteria = {code: (section, float(default), float(low), float(high)) for code, section, default, low, high in calls}
    assert len(criteria) == len(calls), f"a {kind} criterion is defined twice in criteria.ts"
    return criteria


# --- small variants (ACMG/AMP 2015) -----------------------------------------------------------


def test_the_server_knows_the_dialog_s_criteria_with_their_direction_and_points() -> None:
    dialog = _dialog_acmg_criteria()
    assert len(dialog) == 28
    assert {code: direction for code, (direction, _, _) in dialog.items()} == acmg_points.CRITERION_DIRECTION
    assert set(acmg_points.ALLOWED_STRENGTHS) == acmg_points.VALID_CODES
    assert _dialog_strength_points() == acmg_points.STRENGTH_POINTS


def test_the_server_allows_each_criterion_the_strengths_the_dialog_offers() -> None:
    differences = [
        f"{code}: dialog {sorted(allowed)}, server {sorted(acmg_points.ALLOWED_STRENGTHS.get(code, ()))}"
        for code, (_direction, _default, allowed) in sorted(_dialog_acmg_criteria().items())
        if allowed != acmg_points.ALLOWED_STRENGTHS.get(code)
    ]
    assert differences == []


def test_a_save_takes_every_strength_the_dialog_offers_and_refuses_any_other() -> None:
    # The dialog sends a criterion at one of its allowed strengths, or at its default when the
    # analyst ticks it without choosing one.
    wrong: list[str] = []
    for code, (_direction, default, allowed) in sorted(_dialog_acmg_criteria().items()):
        offered = allowed | {default}
        for strength in sorted(acmg_points.VALID_STRENGTHS):
            payload = AcmgClassificationPayload(
                criteria=[AcmgCriterionSelection(code=code, strength=strength, accepted=True)]
            )
            try:
                blob, _points, _class = _normalize_acmg_payload(payload)
            except HTTPException as refused:
                assert refused.status_code == 400
                taken = False
            else:
                assert blob is not None and blob["criteria"][0]["strength"] == strength
                taken = True
            if taken != (strength in offered):
                wrong.append(f"{code} at {strength}: {'taken' if taken else 'refused'}")
    assert wrong == []


def test_the_missense_z_pp2_reads_comes_from_the_sources_the_server_freezes_in_its_order() -> None:
    # The dialog's PP2 reads the gene profile's missense Z, else the VCF's own MISSENSE_Z
    # (pp2MissenseZ); an ACMG save freezes the value with its source the same way
    # (small_variant_review_acmg.pp2_gene_missense_z). The source names are the server's, and
    # the gene profile's value is read first on both sides.
    source = ACMG_EVALUATE_TS.read_text(encoding="utf-8")
    sources = re.search(r"export type MissenseZSource = ([^;]+);", source)
    assert sources, "MissenseZSource not found in evaluate.ts"
    assert re.findall(r"'(\w+)'", sources.group(1)) == [MISSENSE_Z_FROM_GENE_PROFILE, MISSENSE_Z_FROM_VCF]
    head = source.find("export function pp2MissenseZ(")
    assert head >= 0, "pp2MissenseZ not found in evaluate.ts"
    body = source[head : source.index("\n}\n", head)]
    assert body.index("variant.gene_missense_z") < body.index("variant.annotation_extra?.gene_missense_z")


# --- copy-number variants (ClinGen 2019) ------------------------------------------------------


def _server_cnv_criteria(kind: str) -> dict[str, tuple[str, float, float, float]]:
    catalogue = cnv_acmg_points.CNV_GAIN_CRITERIA if kind == "gain" else cnv_acmg_points.CNV_LOSS_CRITERIA
    return {code: (spec["section"], spec["default"], spec["min"], spec["max"]) for code, spec in catalogue.items()}


@pytest.mark.parametrize("kind", ["loss", "gain"])
def test_the_cnv_catalogues_match_the_dialog_s(kind: str) -> None:
    assert _dialog_cnv_criteria(kind) == _server_cnv_criteria(kind)


@pytest.mark.parametrize("kind", ["loss", "gain"])
def test_a_save_keeps_every_point_value_the_dialog_offers(kind: str) -> None:
    for code, (_section, default, low, high) in _dialog_cnv_criteria(kind).items():
        for points in sorted({default, low, high}):
            payload = CnvAcmgClassificationPayload(
                kind=kind, criteria=[CnvAcmgCriterion(code=code, points=points, accepted=True)]
            )
            blob, _total, _class = _normalize_cnv_acmg_payload(payload)
            assert blob is not None and blob["criteria"][0]["points"] == points, (kind, code, points)


# --- the class labels and the classification tags ------------------------------------------------

SMALL_VARIANT_SEARCH_TS = FRONTEND_LIB.parent / "pages" / "families" / "smallVariantSearch.ts"


def _ts_record(path: Path, declaration: str) -> dict[str, str]:
    """The ``key: 'label'`` entries of the object ``declaration`` opens in ``path``."""
    source = path.read_text(encoding="utf-8")
    body = re.search(re.escape(declaration) + r"\s*=\s*\{([^}]*)\}", source)
    assert body, f"{declaration} not found in {path.name}"
    return dict(re.findall(r"(\w+):\s*'([^']*)'", body.group(1)))


def _ts_tags(declaration: str) -> dict[str, str]:
    """The ``{ key, label }`` entries of the tag list ``declaration`` opens in smallVariantSearch.ts."""
    source = SMALL_VARIANT_SEARCH_TS.read_text(encoding="utf-8")
    body = re.search(re.escape(declaration) + r"\s*=\s*\[(.*?)\n\]", source, re.S)
    assert body, f"{declaration} not found in {SMALL_VARIANT_SEARCH_TS.name}"
    return dict(re.findall(r"\{\s*key:\s*'(\w+)',\s*label:\s*'([^']*)'\s*\}", body.group(1)))


def _in_sentence_case(label: str) -> bool:
    """The first word capitalised and every other in lowercase; an acronym (VUS, AR) as it is."""
    first, *rest = re.findall(r"[A-Za-z]+", label)
    return (
        first[0].isupper()
        and (first.isupper() or first[1:].islower())
        and all(word.islower() or word.isupper() for word in rest)
    )


def test_the_class_labels_and_the_classification_tags_are_in_sentence_case_as_the_dialogs_write_them() -> None:
    # Sentence case (the owner's decision of 2026-10-28): "Likely pathogenic - class 4", "VUS — hot".
    # The server stores the label its scoring computes, so it is the label the dialogs show.
    assert acmg_points.CLASS_LABELS == _ts_record(
        ACMG_CRITERIA_TS, "export const ACMG_CLASS_LABELS: Record<AcmgClassKey, string>"
    )
    assert cnv_acmg_points.CLASS_LABELS == _ts_record(
        CNV_CRITERIA_TS, "export const CNV_CLASS_LABELS: Record<string, string>"
    )
    # A review's class tag reads as its class: the tag's label is the class label.
    from backend.app.services.small_variant_review_tags import DEFAULT_SMALL_VARIANT_TAGS

    built_in = {entry["key"]: entry["label"] for entry in DEFAULT_SMALL_VARIANT_TAGS}
    classification_tags = _ts_tags("export const ACMG_CLASSIFICATION_TAGS")
    vus_tier_tags = _ts_tags("export const ACMG_VUS_TIER_TAGS")
    assert classification_tags == acmg_points.CLASS_LABELS
    assert {key: built_in[key] for key in classification_tags} == classification_tags
    assert vus_tier_tags == {
        "acmg_vus_hot": "VUS — hot",
        "acmg_vus_warm": "VUS — warm",
        "acmg_vus_cold": "VUS — cold",
    }
    assert {key: built_in[key] for key in vus_tier_tags} == vus_tier_tags
    labels = [*built_in.values(), *acmg_points.CLASS_LABELS.values(), *cnv_acmg_points.CLASS_LABELS.values()]
    assert [label for label in labels if not _in_sentence_case(label)] == []


# --- the class a review shows ---------------------------------------------------------------------

REVIEW_CLASS_CASES = SMALL_VARIANT_SEARCH_TS.parent / "__tests__" / "fixtures" / "review-class-cases.json"


def test_a_review_shows_the_class_the_family_page_reads_from_its_text_and_its_tags() -> None:
    # The family page reads a review's class with normalizeReviewClassification: a class word in
    # the text as its label, else the most severe class tag, else the text. The Variant Explorer
    # read the text alone, so a review classified by its class tag showed no class there.
    # The server's rule is review_class_label; smallVariantSearch.test.ts reads the same cases.
    from backend.app.services.review_pg_utils import CLASS_WORD_TAG_KEYS, review_class_label

    cases = json.loads(REVIEW_CLASS_CASES.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 10
    for case in cases:
        shown = review_class_label(case["classification"], case["tags"])
        assert (shown or "") == case["shown"], case["why"]

    # The tables the rule reads are the frontend's: the class words, and the class tags in the
    # order in which the first one held is taken (the most severe first).
    source = SMALL_VARIANT_SEARCH_TS.read_text(encoding="utf-8")
    body = re.search(r"const CLASS_WORD_TAG_KEYS: Record<string, string> = \{([^}]*)\}", source)
    assert body, f"CLASS_WORD_TAG_KEYS not found in {SMALL_VARIANT_SEARCH_TS.name}"
    assert dict(re.findall(r"'?(\w[\w ]*?)'?:\s*'(\w+)'", body.group(1))) == CLASS_WORD_TAG_KEYS
    assert list(_ts_tags("export const ACMG_CLASSIFICATION_TAGS")) == list(acmg_points.CLASS_LABELS)


def test_a_classification_filter_keeps_each_review_by_the_class_the_family_page_shows() -> None:
    # The owner's decision of 2026-11-10: the classification filters keep a review by the
    # class it shows, so over the shared cases a class filter keeps every review shown with that
    # class and none shown with another. A filter value is read as a review's text is: a bare
    # class word asks for its class, in any case; free text asks for itself.
    from backend.app.services.review_pg_utils import (
        CLASS_WORD_TAG_KEYS,
        review_class_filter,
        review_shows_class,
    )

    cases = json.loads(REVIEW_CLASS_CASES.read_text(encoding="utf-8"))["cases"]
    labels = list(acmg_points.CLASS_LABELS.values())
    for case in cases:
        for label in labels:
            kept = review_shows_class(case["classification"], case["tags"], review_class_filter([label]))
            assert kept == (case["shown"].lower() == label.lower()), (case["why"], label)
        if case["shown"]:
            assert review_shows_class(
                case["classification"], case["tags"], review_class_filter([case["shown"].upper()])
            ), case["why"]
    for word, tag in CLASS_WORD_TAG_KEYS.items():
        assert review_class_filter([f" {word.upper()} "]) == [acmg_points.CLASS_LABELS[tag].lower()]
    assert review_class_filter(["Likely Pathogenic - class 4", "likely pathogenic", " ", "My words"]) == [
        "likely pathogenic - class 4",
        "my words",
    ]
    # Tags that are no list hold no class tag, as in SQL.
    assert not review_shows_class(None, {"acmg_class_4": True}, review_class_filter(labels))
    assert not review_shows_class(None, "acmg_class_4", review_class_filter(labels))


# --- the class an SV review shows -----------------------------------------------------------------

SV_PAGE_VIEWS = [
    SMALL_VARIANT_SEARCH_TS.parent / "StructuralVariantTable.tsx",
    SMALL_VARIANT_SEARCH_TS.parent / "StructuralVariantCards.tsx",
]


def test_the_sv_classification_filter_keeps_each_review_by_the_class_the_sv_page_shows() -> None:
    # The owner's decision of 2026-11-10: the SV table and cards show a review's class as
    # the family page does, by normalizeReviewClassification of its text and its tags (no CNV
    # scoring of their own), and the SV search's classification filter keeps an SV by that class.
    # Over the shared cases a class filter keeps every review shown with that class and none shown
    # with another. Were either view to read the class otherwise (from the CNV class, say), this
    # fails: the filter would then need the same rule.
    import asyncio
    import types

    from backend.app.services.structural_variant_review_pg import list_matching_structural_variant_review_ids

    for view in SV_PAGE_VIEWS:
        source = view.read_text(encoding="utf-8")
        assert re.search(
            r"normalizeReviewClassification\(\s*variant\.review\?\.classification,\s*variant\.review\?\.tags,?\s*\)",
            source,
        ), f"{view.name} no longer shows a review's class from its text and its tags"
        assert len(re.findall(r"normalizeReviewClassification\(", source)) == 1, view.name

    cases = json.loads(REVIEW_CLASS_CASES.read_text(encoding="utf-8"))["cases"]
    rows = [
        {"variant_id": f"sv-{index}", "classification": case["classification"], "tags": case["tags"], "note": None}
        for index, case in enumerate(cases)
    ]

    class _Rows:
        async def execute(self, _statement, _params=None):
            return types.SimpleNamespace(mappings=lambda: types.SimpleNamespace(all=lambda: list(rows)))

    def _kept(wanted: str) -> set[str]:
        return asyncio.run(
            list_matching_structural_variant_review_ids(_Rows(), family_uuid="f1", classifications=[wanted])
        )

    for label in acmg_points.CLASS_LABELS.values():
        assert _kept(label) == {
            f"sv-{index}" for index, case in enumerate(cases) if case["shown"].lower() == label.lower()
        }, label
    for index, case in enumerate(cases):
        if case["shown"]:
            assert f"sv-{index}" in _kept(case["shown"].upper()), case["why"]
