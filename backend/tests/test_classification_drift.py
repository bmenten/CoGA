from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

from backend.app.services import classification_drift_service as cds
from backend.app.services.small_variant_review_acmg import build_evidence_snapshot

_WHEN = datetime(2026, 6, 25, tzinfo=timezone.utc)


def _record(hash_: str | None, clinvar: str | None = None, version: str = "v1"):
    return types.SimpleNamespace(
        annotation_version=version,
        annotation_set_hash=hash_,
        annotations=[{"clinvar": clinvar}] if clinvar is not None else [],
    )


def test_build_evidence_snapshot_extracts_identity_and_clinvar() -> None:
    snap = build_evidence_snapshot(_record("123", "Pathogenic"), _WHEN)
    assert snap["annotation_set_hash"] == "123"
    assert snap["annotation_version"] == "v1"
    assert snap["clinvar"] == "Pathogenic"
    assert snap["captured_at"].startswith("2026-06-25")


def test_build_evidence_snapshot_treats_placeholder_as_no_clinvar() -> None:
    assert build_evidence_snapshot(_record("123", "-"), _WHEN)["clinvar"] is None
    assert build_evidence_snapshot(_record("123", "."), _WHEN)["clinvar"] is None


def test_diff_unchanged_is_current() -> None:
    snap = {"annotation_set_hash": "h1", "clinvar": "VUS", "annotation_version": "v1"}
    assert cds._diff(snap, dict(snap))["status"] == "current"


def test_diff_changed_hash_is_drifted_with_from_to() -> None:
    snap = {"annotation_set_hash": "h1", "clinvar": "VUS", "annotation_version": "v1"}
    cur = {"annotation_set_hash": "h2", "clinvar": "Pathogenic", "annotation_version": "v2"}
    diff = cds._diff(snap, cur)
    assert diff["status"] == "drifted"
    assert diff["clinvar_from"] == "VUS" and diff["clinvar_to"] == "Pathogenic"
    assert diff["annotation_version_from"] == "v1" and diff["annotation_version_to"] == "v2"


def test_diff_missing_variant() -> None:
    snap = {"annotation_set_hash": "h1", "clinvar": "VUS", "annotation_version": "v1"}
    diff = cds._diff(snap, None)
    assert diff["status"] == "variant_missing" and diff["clinvar_to"] is None


def test_diff_missing_hash_is_unknown_not_false_current() -> None:
    # A missing hash on either side means drift cannot be VERIFIED. It must not read as
    # a false "drifted" — but it must also not read as a false "current" (which would
    # silently clear the sign-out drift gate). Fail safe to "unknown", which the gate
    # counts like a drift (requiring acknowledgement).
    snap = {"annotation_set_hash": None, "clinvar": "VUS", "annotation_version": "v1"}
    cur = {"annotation_set_hash": "h2", "clinvar": "VUS", "annotation_version": "v1"}
    assert cds._diff(snap, cur)["status"] == "unknown"

    # The finding's case: a present current record whose hash field is empty/renamed.
    snap2 = {"annotation_set_hash": "h1", "clinvar": "VUS", "annotation_version": "v1"}
    cur2 = {"annotation_set_hash": None, "clinvar": "VUS", "annotation_version": "v1"}
    assert cds._diff(snap2, cur2)["status"] == "unknown"


def test_evaluate_classification_drift_reports_only_changed(monkeypatch) -> None:
    async def _context(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_name="GRCh38")

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "variant_id": "1-100-A-G",
                    "acmg_class": "acmg_class_4",
                    "acmg_evidence_snapshot": {
                        "annotation_set_hash": "OLD",
                        "clinvar": "Uncertain significance",
                        "annotation_version": "v1",
                    },
                    "updated_by": "alice",
                    "updated_at": _WHEN,
                },
                {
                    "variant_id": "2-200-C-T",  # unchanged -> excluded from the drifted list
                    "acmg_class": "acmg_class_3",
                    "acmg_evidence_snapshot": {
                        "annotation_set_hash": "SAME",
                        "clinvar": None,
                        "annotation_version": "v1",
                    },
                    "updated_by": "bob",
                    "updated_at": _WHEN,
                },
            ]

    class _Session:
        async def execute(self, *args, **kwargs):
            return _Result()

    current_by_variant = {
        "1-100-A-G": _record("NEW", "Pathogenic", version="v2"),
        "2-200-C-T": _record("SAME", None, version="v1"),
    }

    async def _current(*, assembly_name, family_guid, variant_id):
        return current_by_variant[variant_id]

    monkeypatch.setattr(cds, "build_family_metadata_context", _context)
    monkeypatch.setattr(cds, "get_small_variant_family_record", _current)

    out = asyncio.run(
        cds.evaluate_classification_drift(_Session(), family_id="FAM1", user=None)
    )
    assert out["family_id"] == "FAM1"
    assert out["checked"] == 2
    assert out["drifted_count"] == 1
    item = out["drifted"][0]
    assert item["variant_id"] == "1-100-A-G" and item["status"] == "drifted"
    assert item["clinvar_from"] == "Uncertain significance" and item["clinvar_to"] == "Pathogenic"
    assert item["classified_by"] == "alice"


# --- The frequencies and in-silico scores a classification rests on (CLIN-7) -------------

_CSQ_HEADER = (
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP.'
    " Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|MANE_SELECT"
    "|SIFT|PolyPhen|REVEL|CADD_PHRED|am_class|am_pathogenicity|SpliceAI_pred_DS_AG"
    "|SpliceAI_pred_DS_AL|SpliceAI_pred_DS_DG|SpliceAI_pred_DS_DL|gnomADe_AF|gnomADg_AF|MAX_AF"
    '|gnomAD_AC|gnomAD_nhomalt|gnomAD_Hemi|TOPMed_AF">'
)


def _csq(
    *,
    transcript: str,
    consequence: str = "missense_variant",
    impact: str = "MODERATE",
    mane: bool = False,
    revel: str = "0.412",
    cadd: str = "24.3",
    gnomad_exomes: str = "1.2e-05",
    gnomad_genomes: str = "3.1e-05",
    popmax: str = "0.0002",
) -> str:
    # One transcript's CSQ entry, as VEP writes it (an empty field is a missing value).
    return "|".join(
        [
            "G", consequence, impact, "GENE1", "ENSG00000000001", "Transcript", transcript,
            "YES" if mane else "", "NM_000001.1" if mane else "",
            "deleterious(0.01)", "probably_damaging(0.981)", revel, cadd,
            "likely_pathogenic", "0.871", "0.01", "0.02", "0.00", "0.00",
            gnomad_exomes, gnomad_genomes, popmax, "4", "0", "", "2e-05",
        ]
    )


def _annotations(*entries: str) -> list[dict]:
    # Through the import's own parser, so the keys are the ones a stored annotation has.
    from backend.app.services.variant_annotation_parser import (
        AnnotationHeaderState,
        extract_small_variant_annotations,
        update_annotation_header_state,
    )

    state = AnnotationHeaderState()
    update_annotation_header_state(state, _CSQ_HEADER)
    return extract_small_variant_annotations({"CSQ": ",".join(entries)}, state)


def _annotated(hash_: str | None, *entries: str, clinvar: str | None = None):
    annotations = _annotations(*entries)
    if clinvar is not None:
        annotations = [{**annotation, "clinvar": clinvar} for annotation in annotations]
    return types.SimpleNamespace(annotation_version="v1", annotation_set_hash=hash_, annotations=annotations)


# A MANE Select missense transcript and a more severe transcript that is not: the primary
# transcript is the MANE Select one, whose values the ACMG dialog reads.
_MANE = _csq(transcript="ENST00000000001", mane=True)
_OTHER = _csq(
    transcript="ENST00000000002", consequence="stop_gained", impact="HIGH", revel="0.990", cadd="38.0"
)


def _stored(snapshot: dict) -> dict:
    """The snapshot as the JSONB column hands it back."""
    import json

    return json.loads(json.dumps(snapshot, allow_nan=False))


def test_the_snapshot_freezes_the_primary_transcripts_frequencies_and_in_silico_scores() -> None:
    from backend.app.services.small_variant_review_acmg import (
        EVIDENCE_FREQUENCY_FIELDS,
        EVIDENCE_IN_SILICO_FIELDS,
    )

    snap = build_evidence_snapshot(_annotated("123", _OTHER, _MANE), _WHEN)
    assert snap["transcript_id"] == "ENST00000000001"
    assert snap["frequencies"] == {
        # No gnomAD_AF in the file: the higher of the exome and genome frequencies.
        "gnomad_af": 3.1e-05,
        "gnomad_exomes_af": 1.2e-05,
        "gnomad_genomes_af": 3.1e-05,
        "gnomad_popmax_af": 0.0002,
        "topmed_af": 2e-05,
        "gnomad_ac": 4,
        "gnomad_hom_count": 0,
        "gnomad_hemi_count": None,
    }
    assert snap["in_silico"] == {
        "cadd_phred": 24.3,
        "revel": 0.412,
        "spliceai_max": 0.02,
        "sift": "deleterious(0.01)",
        "polyphen": "probably_damaging(0.981)",
        "alpha_missense_class": "likely_pathogenic",
        "alpha_missense_pathogenicity": 0.871,
    }
    # Every field is frozen, in the order the drift check names them, also without a value.
    assert tuple(snap["frequencies"]) == EVIDENCE_FREQUENCY_FIELDS
    assert tuple(snap["in_silico"]) == EVIDENCE_IN_SILICO_FIELDS
    # The identity and the ClinVar significance are kept as before.
    assert (snap["annotation_set_hash"], snap["annotation_version"], snap["clinvar"]) == ("123", "v1", None)


def test_the_frozen_values_are_the_ones_the_acmg_dialog_is_given() -> None:
    # The dialog reads the variant as the API serves it: its primary transcript's fields
    # (_small_variant_out). The snapshot freezes exactly those.
    from backend.app.services.clickhouse_variant_queries import _small_variant_out
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    annotations = _annotations(_OTHER, _MANE)
    served = _small_variant_out(
        SmallVariantRecord(
            variant_key=1, variant_id="1-1000-A-G", chr="1", start=1000, end=1000, ref="A", alt="G",
            source="clair3", rsid=None, filters=["PASS"], gene_symbols=["GENE1"], annotations=annotations,
            calls=[SmallVariantCall(sample="S1", gt="0/1", gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)],
        ),
        assembly_name=None,
        filters=None,
    )
    snap = build_evidence_snapshot(
        types.SimpleNamespace(annotation_version="v1", annotation_set_hash="1", annotations=annotations), _WHEN
    )
    frequencies, in_silico = snap["frequencies"], snap["in_silico"]
    assert snap["transcript_id"] == served.transcript_id
    assert frequencies["gnomad_af"] == served.gnomad_af
    assert frequencies["gnomad_hom_count"] == served.gnomad_hom_count
    for field in ("gnomad_exomes_af", "gnomad_genomes_af", "gnomad_popmax_af", "topmed_af"):
        assert frequencies[field] == served.population_frequencies.get(field), field
    assert frequencies["gnomad_ac"] == served.annotation_extra.get("gnomad_ac")
    assert frequencies["gnomad_hemi_count"] == served.annotation_extra.get("gnomad_hemi_count")
    assert in_silico == {
        "cadd_phred": served.cadd_phred,
        "revel": served.revel,
        "spliceai_max": served.spliceai_max,
        "sift": served.sift,
        "polyphen": served.polyphen,
        "alpha_missense_class": served.alpha_missense_class,
        "alpha_missense_pathogenicity": served.alpha_missense_pathogenicity,
    }


def test_a_variant_without_annotation_freezes_every_value_as_none() -> None:
    snap = build_evidence_snapshot(_record("123"), _WHEN)
    assert snap["transcript_id"] is None
    assert set(snap["frequencies"].values()) == {None}
    assert set(snap["in_silico"].values()) == {None}


def test_a_value_jsonb_cannot_hold_is_frozen_as_none() -> None:
    # JSONB holds no NaN or infinity: such a value would make the save fail.
    record = types.SimpleNamespace(
        annotation_version="v1",
        annotation_set_hash="1",
        annotations=[{"revel": "nan", "cadd_phred": "inf", "gnomad_af": -0.0, "gnomad_hom_count": "inf"}],
    )
    snap = build_evidence_snapshot(record, _WHEN)
    assert (snap["in_silico"]["revel"], snap["in_silico"]["cadd_phred"]) == (None, None)
    assert snap["frequencies"]["gnomad_hom_count"] is None
    assert str(snap["frequencies"]["gnomad_af"]) == "0.0"
    _stored(snap)  # serialises without NaN or infinity


def test_a_changed_revel_reads_as_drift_naming_it() -> None:
    frozen = _stored(build_evidence_snapshot(_annotated("h1", _OTHER, _MANE), _WHEN))
    rescored = _annotated("h2", _OTHER, _csq(transcript="ENST00000000001", mane=True, revel="0.951"))
    diff = cds._diff(frozen, cds._current_evidence(rescored))
    assert diff["status"] == "drifted"
    assert diff["changed"] == ["revel", "annotations"]
    assert (diff["evidence_from"]["revel"], diff["evidence_to"]["revel"]) == (0.412, 0.951)
    # The ClinVar significance did not move, and is reported as before.
    assert diff["clinvar_from"] is None and diff["clinvar_to"] is None


def test_a_value_changed_under_the_same_annotation_set_hash_is_still_drift() -> None:
    # The hash is the cheap key, not the only one: a frozen value that moved is a drift
    # even where the hash did not (an annotation rewritten in place).
    frozen = _stored(build_evidence_snapshot(_annotated("h1", _OTHER, _MANE), _WHEN))
    rewritten = _annotated("h1", _OTHER, _csq(transcript="ENST00000000001", mane=True, revel="0.951"))
    diff = cds._diff(frozen, cds._current_evidence(rewritten))
    assert diff["status"] == "drifted"
    assert diff["changed"] == ["revel"]


def test_a_changed_gnomad_frequency_reads_as_drift_naming_it() -> None:
    frozen = _stored(build_evidence_snapshot(_annotated("h1", _OTHER, _MANE), _WHEN))
    common = _csq(transcript="ENST00000000001", mane=True, gnomad_genomes="0.012", popmax="0.031")
    diff = cds._diff(frozen, cds._current_evidence(_annotated("h2", _OTHER, common)))
    assert diff["status"] == "drifted"
    assert diff["changed"] == ["gnomad_af", "gnomad_genomes_af", "gnomad_popmax_af", "annotations"]
    assert (diff["evidence_from"]["gnomad_af"], diff["evidence_to"]["gnomad_af"]) == (3.1e-05, 0.012)


def test_a_score_that_appears_later_reads_as_drift() -> None:
    # Classified without REVEL (PP3 and BP4 not applicable), REVEL is there now.
    frozen = _stored(
        build_evidence_snapshot(_annotated("h1", _csq(transcript="ENST00000000001", mane=True, revel="")), _WHEN)
    )
    assert frozen["in_silico"]["revel"] is None
    diff = cds._diff(frozen, cds._current_evidence(_annotated("h2", _MANE)))
    assert diff["changed"] == ["revel", "annotations"]
    assert (diff["evidence_from"]["revel"], diff["evidence_to"]["revel"]) == (None, 0.412)


def test_unchanged_evidence_read_back_from_jsonb_is_current() -> None:
    frozen = _stored(build_evidence_snapshot(_annotated("h1", _OTHER, _MANE, clinvar="Pathogenic"), _WHEN))
    diff = cds._diff(frozen, cds._current_evidence(_annotated("h1", _OTHER, _MANE, clinvar="Pathogenic")))
    assert diff["status"] == "current" and diff["changed"] == []


def test_a_number_compares_by_value_whatever_its_encoding() -> None:
    # Postgres JSONB keeps a number's digits, not its Python type: 25.0 may come back as 25.
    assert cds._same(25, 25.0) and cds._same(0.0, -0.0) and cds._same(1e-05, 0.00001)
    assert not cds._same(0.412, 0.4120001)
    assert not cds._same(None, 0.0) and not cds._same(0, False)
    assert cds._same("deleterious(0.01)", "deleterious(0.01)") and cds._same(None, None)


def test_an_old_snapshot_without_the_values_stays_current() -> None:
    # Frozen before the frequencies and in-silico scores were: the annotation version,
    # the annotation-set hash, the ClinVar significance and the time.
    old = {"annotation_version": "v1", "annotation_set_hash": "h1", "clinvar": "Pathogenic", "captured_at": "x"}
    now = cds._current_evidence(_annotated("h1", _OTHER, _MANE, clinvar="Pathogenic"))
    diff = cds._diff(old, now)
    assert diff["status"] == "current"
    assert diff["changed"] == [] and diff["evidence_from"] is None
    # A new annotation set is still a drift, by its hash, as before; nothing else is named.
    moved = cds._diff(old, cds._current_evidence(_annotated("h2", _OTHER, _MANE, clinvar="Pathogenic")))
    assert (moved["status"], moved["changed"], moved["evidence_from"]) == ("drifted", ["annotations"], None)


def test_only_the_values_a_snapshot_froze_are_compared() -> None:
    # A snapshot holding only REVEL is compared on REVEL: a value CoGA freezes later does
    # not turn it into a drift.
    partial = {"annotation_version": "v1", "annotation_set_hash": "h1", "clinvar": None, "in_silico": {"revel": 0.412}}
    diff = cds._diff(partial, cds._current_evidence(_annotated("h1", _OTHER, _MANE)))
    assert diff["status"] == "current"
    assert diff["evidence_from"] == {"revel": 0.412}


def test_a_frozen_group_that_cannot_be_read_is_unknown_not_current() -> None:
    unreadable = {"annotation_version": "v1", "annotation_set_hash": "h1", "clinvar": None, "frequencies": "?"}
    now = cds._current_evidence(_annotated("h1", _OTHER, _MANE))
    assert cds._diff(unreadable, now)["status"] == "unknown"
    # What can be read still names what moved.
    unreadable["in_silico"] = {"revel": 0.2}
    diff = cds._diff(unreadable, now)
    assert (diff["status"], diff["changed"]) == ("drifted", ["revel"])


def test_a_missing_variant_keeps_its_frozen_values() -> None:
    frozen = _stored(build_evidence_snapshot(_annotated("h1", _MANE), _WHEN))
    diff = cds._diff(frozen, None)
    assert diff["status"] == "variant_missing"
    assert diff["evidence_from"]["revel"] == 0.412 and diff["evidence_to"] is None


def test_evaluate_classification_drift_names_the_moved_value(monkeypatch) -> None:
    async def _context(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_name="GRCh38")

    rows = [
        {
            "variant_id": "1-100-A-G",
            "acmg_class": "acmg_class_4",
            "acmg_evidence_snapshot": _stored(build_evidence_snapshot(_annotated("h1", _MANE), _WHEN)),
            "updated_by": "alice",
            "updated_at": _WHEN,
        },
        {
            # Frozen before CLIN-7, its variant unchanged: not listed.
            "variant_id": "2-200-C-T",
            "acmg_class": "acmg_class_3",
            "acmg_evidence_snapshot": {"annotation_set_hash": "h9", "clinvar": None, "annotation_version": "v1"},
            "updated_by": "bob",
            "updated_at": _WHEN,
        },
    ]

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return rows

    class _Session:
        async def execute(self, *args, **kwargs):
            return _Result()

    current_by_variant = {
        "1-100-A-G": _annotated("h2", _csq(transcript="ENST00000000001", mane=True, revel="0.951")),
        "2-200-C-T": _annotated("h9", _MANE),
    }

    async def _current(*, assembly_name, family_guid, variant_id):
        return current_by_variant[variant_id]

    async def _structural(session, context):
        return {"checked": 0, "drifted_count": 0, "drifted": []}

    monkeypatch.setattr(cds, "build_family_metadata_context", _context)
    monkeypatch.setattr(cds, "get_small_variant_family_record", _current)
    monkeypatch.setattr(cds, "evaluate_structural_classification_drift", _structural)

    out = asyncio.run(cds.evaluate_classification_drift(_Session(), family_id="FAM1", user=None))
    assert (out["checked"], out["drifted_count"]) == (2, 1)
    [item] = out["drifted"]
    assert (item["variant_id"], item["status"], item["changed"]) == ("1-100-A-G", "drifted", ["revel", "annotations"])
    assert (item["evidence_from"]["revel"], item["evidence_to"]["revel"]) == (0.412, 0.951)
    # The API serves the item as it is, what moved included.
    from backend.app.schemas.traceability import ClassificationDriftItem

    served = ClassificationDriftItem.model_validate(item)
    assert served.changed == ["revel", "annotations"] and served.evidence_to["revel"] == 0.951
