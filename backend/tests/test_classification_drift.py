from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

from backend.app.services import classification_drift_service as cds
from backend.app.services.small_variant_review_acmg import build_evidence_snapshot

_WHEN = datetime(2026, 6, 25, tzinfo=timezone.utc)


def _freeze(record, gene_missense_z: float | None = None) -> dict:
    """The snapshot a save freezes, its gene's missense Z as the gene profile gives it."""
    return build_evidence_snapshot(record, _WHEN, gene_missense_z=gene_missense_z)


def _now(record, gene_missense_z: float | None = None) -> dict:
    """The evidence as the drift check reads it now."""
    return cds._current_evidence(record, gene_missense_z=gene_missense_z)


def _record(hash_: str | None, clinvar: str | None = None, version: str = "v1"):
    return types.SimpleNamespace(
        annotation_version=version,
        annotation_set_hash=hash_,
        annotations=[{"clinvar": clinvar}] if clinvar is not None else [],
    )


def test_build_evidence_snapshot_extracts_identity_and_clinvar() -> None:
    snap = _freeze(_record("123", "Pathogenic"))
    assert snap["annotation_set_hash"] == "123"
    assert snap["annotation_version"] == "v1"
    assert snap["clinvar"] == "Pathogenic"
    assert snap["captured_at"].startswith("2026-06-25")


def test_build_evidence_snapshot_treats_placeholder_as_no_clinvar() -> None:
    assert _freeze(_record("123", "-"))["clinvar"] is None
    assert _freeze(_record("123", "."))["clinvar"] is None


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
    '|gnomAD_AC|gnomAD_nhomalt|gnomAD_Hemi|TOPMed_AF|LoF">'
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
    lof: str = "",
    gene: str = "GENE1",
    gene_id: str = "ENSG00000000001",
) -> str:
    # One transcript's CSQ entry, as VEP writes it (an empty field is a missing value).
    return "|".join(
        [
            "G", consequence, impact, gene, gene_id, "Transcript", transcript,
            "YES" if mane else "", "NM_000001.1" if mane else "",
            "deleterious(0.01)", "probably_damaging(0.981)", revel, cadd,
            "likely_pathogenic", "0.871", "0.01", "0.02", "0.00", "0.00",
            gnomad_exomes, gnomad_genomes, popmax, "4", "0", "", "2e-05", lof,
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

    snap = _freeze(_annotated("123", _OTHER, _MANE))
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
    snap = _freeze(
        types.SimpleNamespace(annotation_version="v1", annotation_set_hash="1", annotations=annotations)
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
    snap = _freeze(_record("123"))
    assert snap["transcript_id"] is None
    assert set(snap["consequence"].values()) == {None}
    assert set(snap["frequencies"].values()) == {None}
    assert set(snap["in_silico"].values()) == {None}
    # Without a consequence it is no missense variant: PP2 reads no missense Z for it.
    assert "gene_constraint" not in snap


def test_a_value_jsonb_cannot_hold_is_frozen_as_none() -> None:
    # JSONB holds no NaN or infinity: such a value would make the save fail.
    record = types.SimpleNamespace(
        annotation_version="v1",
        annotation_set_hash="1",
        annotations=[{"revel": "nan", "cadd_phred": "inf", "gnomad_af": -0.0, "gnomad_hom_count": "inf"}],
    )
    snap = _freeze(record)
    assert (snap["in_silico"]["revel"], snap["in_silico"]["cadd_phred"]) == (None, None)
    assert snap["frequencies"]["gnomad_hom_count"] is None
    assert str(snap["frequencies"]["gnomad_af"]) == "0.0"
    _stored(snap)  # serialises without NaN or infinity


def test_a_changed_revel_reads_as_drift_naming_it() -> None:
    frozen = _stored(_freeze(_annotated("h1", _OTHER, _MANE)))
    rescored = _annotated("h2", _OTHER, _csq(transcript="ENST00000000001", mane=True, revel="0.951"))
    diff = cds._diff(frozen, _now(rescored))
    assert diff["status"] == "drifted"
    assert diff["changed"] == ["revel", "annotations"]
    assert (diff["evidence_from"]["revel"], diff["evidence_to"]["revel"]) == (0.412, 0.951)
    # The ClinVar significance did not move, and is reported as before.
    assert diff["clinvar_from"] is None and diff["clinvar_to"] is None


def test_a_value_changed_under_the_same_annotation_set_hash_is_still_drift() -> None:
    # The hash is the cheap key, not the only one: a frozen value that moved is a drift
    # even where the hash did not (an annotation rewritten in place).
    frozen = _stored(_freeze(_annotated("h1", _OTHER, _MANE)))
    rewritten = _annotated("h1", _OTHER, _csq(transcript="ENST00000000001", mane=True, revel="0.951"))
    diff = cds._diff(frozen, _now(rewritten))
    assert diff["status"] == "drifted"
    assert diff["changed"] == ["revel"]


def test_a_changed_gnomad_frequency_reads_as_drift_naming_it() -> None:
    frozen = _stored(_freeze(_annotated("h1", _OTHER, _MANE)))
    common = _csq(transcript="ENST00000000001", mane=True, gnomad_genomes="0.012", popmax="0.031")
    diff = cds._diff(frozen, _now(_annotated("h2", _OTHER, common)))
    assert diff["status"] == "drifted"
    assert diff["changed"] == ["gnomad_af", "gnomad_genomes_af", "gnomad_popmax_af", "annotations"]
    assert (diff["evidence_from"]["gnomad_af"], diff["evidence_to"]["gnomad_af"]) == (3.1e-05, 0.012)


def test_a_score_that_appears_later_reads_as_drift() -> None:
    # Classified without REVEL (PP3 and BP4 not applicable), REVEL is there now.
    frozen = _stored(
        _freeze(_annotated("h1", _csq(transcript="ENST00000000001", mane=True, revel="")))
    )
    assert frozen["in_silico"]["revel"] is None
    diff = cds._diff(frozen, _now(_annotated("h2", _MANE)))
    assert diff["changed"] == ["revel", "annotations"]
    assert (diff["evidence_from"]["revel"], diff["evidence_to"]["revel"]) == (None, 0.412)


def test_unchanged_evidence_read_back_from_jsonb_is_current() -> None:
    frozen = _stored(_freeze(_annotated("h1", _OTHER, _MANE, clinvar="Pathogenic")))
    diff = cds._diff(frozen, _now(_annotated("h1", _OTHER, _MANE, clinvar="Pathogenic")))
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
    now = _now(_annotated("h1", _OTHER, _MANE, clinvar="Pathogenic"))
    diff = cds._diff(old, now)
    assert diff["status"] == "current"
    assert diff["changed"] == [] and diff["evidence_from"] is None
    # A new annotation set is still a drift, by its hash, as before; nothing else is named.
    moved = cds._diff(old, _now(_annotated("h2", _OTHER, _MANE, clinvar="Pathogenic")))
    assert (moved["status"], moved["changed"], moved["evidence_from"]) == ("drifted", ["annotations"], None)


def test_only_the_values_a_snapshot_froze_are_compared() -> None:
    # A snapshot holding only REVEL is compared on REVEL: a value CoGA freezes later does
    # not turn it into a drift.
    partial = {"annotation_version": "v1", "annotation_set_hash": "h1", "clinvar": None, "in_silico": {"revel": 0.412}}
    diff = cds._diff(partial, _now(_annotated("h1", _OTHER, _MANE)))
    assert diff["status"] == "current"
    assert diff["evidence_from"] == {"revel": 0.412}


def test_a_frozen_group_that_cannot_be_read_is_unknown_not_current() -> None:
    unreadable = {"annotation_version": "v1", "annotation_set_hash": "h1", "clinvar": None, "frequencies": "?"}
    now = _now(_annotated("h1", _OTHER, _MANE))
    assert cds._diff(unreadable, now)["status"] == "unknown"
    # What can be read still names what moved.
    unreadable["in_silico"] = {"revel": 0.2}
    diff = cds._diff(unreadable, now)
    assert (diff["status"], diff["changed"]) == ("drifted", ["revel"])


def test_a_missing_variant_keeps_its_frozen_values() -> None:
    frozen = _stored(_freeze(_annotated("h1", _MANE)))
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
            "acmg_evidence_snapshot": _stored(_freeze(_annotated("h1", _MANE))),
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


# --- The consequence, impact and LoF call, and the gene's missense Z (CLIN-7) -------------------
# PVS1 reads the consequence and LOFTEE's call (HC: very strong), PP2 the consequence and the
# gene's gnomAD missense Z, and the consequence rules criteria out. They are frozen too, as
# the dialog reads them, and a change of any of them is drift that names it.

# A MANE Select stop-gained transcript LOFTEE calls high-confidence.
_LOF = _csq(transcript="ENST00000000001", mane=True, consequence="stop_gained", impact="HIGH", lof="HC")
_EARLIER = datetime(2026, 1, 5, tzinfo=timezone.utc)


def _served(annotations: list[dict], gene_symbols: list[str] | None = None):
    """The variant as the API serves it to the ACMG dialog (_small_variant_out)."""
    from backend.app.services.clickhouse_variant_queries import _small_variant_out
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    return _small_variant_out(
        SmallVariantRecord(
            variant_key=1, variant_id="1-1000-A-G", chr="1", start=1000, end=1000, ref="A", alt="G",
            source="clair3", rsid=None, filters=["PASS"],
            gene_symbols=["GENE1"] if gene_symbols is None else gene_symbols, annotations=annotations,
            calls=[SmallVariantCall(sample="S1", gt="0/1", gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)],
        ),
        assembly_name=None,
        filters=None,
    )


def _profile(symbol: str, gene_id: str, missense_z: float, *, when: datetime) -> dict:
    """A gene profile row (gene_info) holding a gnomAD missense Z, as dbNSFP's gene file names it."""
    return {"hgnc_symbol": symbol, "gene_id": gene_id, "extra": {"mis_z": missense_z}, "source_status": {}, "updated_at": when}


class _Rows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _GeneProfiles:
    """A session holding the gene profiles given (newest first, as the query reads them),
    recording each gene profile query; any other query reads ``other``."""

    def __init__(self, profiles: list[dict], other: list[dict] | None = None) -> None:
        self.profiles = profiles
        self.other = other or []
        self.gene_queries: list[dict] = []

    async def execute(self, statement, params=None):
        if "FROM gene_info" in str(statement):
            self.gene_queries.append(dict(params or {}))
            return _Rows(self.profiles)
        return _Rows(self.other)


def test_the_snapshot_freezes_the_primary_transcripts_consequence_and_the_genes_missense_z() -> None:
    from backend.app.services.small_variant_review_acmg import (
        EVIDENCE_CONSEQUENCE_FIELDS,
        EVIDENCE_GENE_CONSTRAINT_FIELDS,
    )

    # The MANE Select transcript's consequence, not the more severe other transcript's.
    snap = _freeze(_annotated("123", _OTHER, _MANE), gene_missense_z=3.52)
    assert snap["consequence"] == {"effect": "missense_variant", "impact": "MODERATE", "lof": None}
    assert snap["gene_constraint"] == {"gene_missense_z": 3.52}
    # A stop gained is no missense variant: PP2 does not read its gene's missense Z, so none
    # is frozen, whatever the gene's profile holds.
    lof = _freeze(_annotated("123", _csq(transcript="ENST00000000002"), _LOF), gene_missense_z=3.52)
    assert lof["consequence"] == {"effect": "stop_gained", "impact": "HIGH", "lof": "HC"}
    assert "gene_constraint" not in lof
    # A missense variant's missense Z is frozen also without a value.
    assert _freeze(_annotated("123", _MANE))["gene_constraint"] == {"gene_missense_z": None}
    # Every field is frozen, in the order the drift check names them, also without a value.
    assert tuple(snap["consequence"]) == EVIDENCE_CONSEQUENCE_FIELDS
    assert tuple(snap["gene_constraint"]) == EVIDENCE_GENE_CONSTRAINT_FIELDS


def test_the_frozen_consequence_is_the_one_the_acmg_dialog_is_given() -> None:
    for transcripts in (_annotations(_OTHER, _MANE), _annotations(_csq(transcript="ENST00000000002"), _LOF)):
        served = _served(transcripts)
        snap = _freeze(types.SimpleNamespace(annotation_version="v1", annotation_set_hash="1", annotations=transcripts))
        assert snap["consequence"] == {"effect": served.effect, "impact": served.impact, "lof": served.lof}
    assert served.lof == "HC"


def test_the_missense_z_is_read_for_the_gene_the_variant_is_served_with() -> None:
    # The variant list looks a variant's constraint up by the gene it serves the variant
    # with: its primary transcript's gene id and symbol, else the variant's first gene.
    from backend.app.services.small_variant_review_acmg import evidence_gene

    cases = (
        (_annotations(_OTHER, _MANE), ["GENE1"]),
        ([{"gene": "GENE2", "impact": "MODERATE"}], ["GENE9"]),
        ([{"impact": "MODERATE"}], ["GENE3", "GENE4"]),
        ([{"impact": "MODERATE"}], []),
    )
    for transcripts, symbols in cases:
        served = _served(transcripts, gene_symbols=symbols)
        record = types.SimpleNamespace(gene_symbols=symbols, annotations=transcripts)
        assert evidence_gene(record) == (served.gene, served.gene_id), symbols


def test_the_frozen_missense_z_is_the_one_the_variant_list_serves() -> None:
    # The PP2 suggestion reads the missense Z the variant list hydrates the variant with
    # (_fetch_gene_constraint_metric_map): the newest profile of its gene. The snapshot reads
    # it through the same lookup, asking for the same gene.
    from backend.app.services import clickhouse_family_variants as cfv
    from backend.app.services.small_variant_review_acmg import evidence_gene_missense_z

    annotations = _annotations(_OTHER, _MANE)
    session = _GeneProfiles(
        [
            _profile("GENE1", "ENSG00000000001", 3.52, when=_WHEN),
            _profile("GENE1", "ENSG00000000001", 1.07, when=_EARLIER),
            _profile("GENE2", "ENSG00000000002", 0.4, when=_WHEN),
        ]
    )
    served = _served(annotations)
    served_z = asyncio.run(cfv._fetch_gene_constraint_metric_map(session, [served]))[served.id]["gene_missense_z"]
    record = types.SimpleNamespace(gene_symbols=["GENE1"], annotations=annotations)
    assert asyncio.run(evidence_gene_missense_z(session, {"v": record})) == {"v": served_z}
    assert served_z == 3.52
    assert session.gene_queries[0] == session.gene_queries[1]
    # A gene without a profile has none.
    assert asyncio.run(evidence_gene_missense_z(_GeneProfiles([]), {"v": record})) == {"v": None}


def test_a_save_freezes_the_missense_z_its_genes_profile_holds() -> None:
    from backend.app.services.small_variant_review_acmg import freeze_evidence_snapshot

    session = _GeneProfiles([_profile("GENE1", "ENSG00000000001", 3.52, when=_WHEN)])
    snap = asyncio.run(freeze_evidence_snapshot(session, _annotated("h1", _MANE), _WHEN))
    assert snap["gene_constraint"] == {"gene_missense_z": 3.52}
    assert snap["consequence"]["effect"] == "missense_variant" and snap["captured_at"].startswith("2026-06-25")
    # A missense variant whose gene has no profile freezes none.
    assert asyncio.run(freeze_evidence_snapshot(_GeneProfiles([]), _annotated("h1", _MANE), _WHEN))[
        "gene_constraint"
    ] == {"gene_missense_z": None}
    # A stop gained, and a variant without annotation, are no missense variants: their gene's
    # profile is not asked for, and no missense Z is frozen.
    nothing = _GeneProfiles([_profile("GENE1", "ENSG00000000001", 3.52, when=_WHEN)])
    for record in (_annotated("h1", _LOF), _record("h1")):
        assert "gene_constraint" not in asyncio.run(freeze_evidence_snapshot(nothing, record, _WHEN))
    assert nothing.gene_queries == []


def test_a_missense_z_jsonb_cannot_hold_is_frozen_as_none() -> None:
    for value in (float("nan"), float("inf")):
        snap = _freeze(_annotated("h1", _MANE), gene_missense_z=value)
        assert snap["gene_constraint"] == {"gene_missense_z": None}
        _stored(snap)


def test_a_changed_consequence_and_impact_read_as_drift_naming_them() -> None:
    # A new VEP release calls the MANE transcript's change a stop gained.
    frozen = _stored(_freeze(_annotated("h1", _OTHER, _MANE)))
    recalled = _csq(transcript="ENST00000000001", mane=True, consequence="stop_gained", impact="HIGH")
    diff = cds._diff(frozen, _now(_annotated("h2", _OTHER, recalled)))
    assert (diff["status"], diff["changed"]) == ("drifted", ["effect", "impact", "annotations"])
    assert (diff["evidence_from"]["effect"], diff["evidence_to"]["effect"]) == ("missense_variant", "stop_gained")
    assert (diff["evidence_from"]["impact"], diff["evidence_to"]["impact"]) == ("MODERATE", "HIGH")
    # A consequence term added beside the frozen one is a change as well.
    spliced = _csq(transcript="ENST00000000001", mane=True, consequence="missense_variant&splice_region_variant")
    assert cds._diff(frozen, _now(_annotated("h2", _OTHER, spliced)))["changed"] == ["effect", "annotations"]


def test_a_lof_call_that_appears_later_reads_as_drift() -> None:
    # Classified while LOFTEE made no call (PVS1 strong at most); it calls it HC now.
    no_call = _csq(transcript="ENST00000000001", mane=True, consequence="stop_gained", impact="HIGH")
    frozen = _stored(_freeze(_annotated("h1", no_call)))
    assert frozen["consequence"]["lof"] is None
    diff = cds._diff(frozen, _now(_annotated("h2", _LOF)))
    assert diff["changed"] == ["lof", "annotations"]
    assert (diff["evidence_from"]["lof"], diff["evidence_to"]["lof"]) == (None, "HC")


def test_a_changed_gene_missense_z_reads_as_drift_under_the_same_annotation_set() -> None:
    # The gene profiles are refreshed and the variant's annotation is not: the annotation-set
    # hash stays, while the value the PP2 suggestion reads moved.
    frozen = _stored(_freeze(_annotated("h1", _MANE), gene_missense_z=2.95))
    diff = cds._diff(frozen, _now(_annotated("h1", _MANE), gene_missense_z=3.52))
    assert (diff["status"], diff["changed"]) == ("drifted", ["gene_missense_z"])
    assert (diff["evidence_from"]["gene_missense_z"], diff["evidence_to"]["gene_missense_z"]) == (2.95, 3.52)
    # One that appears later, or is gone, is a change too; the same value is not.
    appeared = cds._diff(_stored(_freeze(_annotated("h1", _MANE))), _now(_annotated("h1", _MANE), gene_missense_z=3.52))
    assert appeared["changed"] == ["gene_missense_z"]
    assert cds._diff(frozen, _now(_annotated("h1", _MANE)))["changed"] == ["gene_missense_z"]
    assert cds._diff(frozen, _now(_annotated("h1", _MANE), gene_missense_z=2.95))["status"] == "current"


def test_a_gene_profile_refresh_is_drift_for_a_missense_variant_and_not_for_a_stop_gained_one() -> None:
    # The owner's rule (2026-10-20): the gene's missense Z is frozen and compared only for a
    # missense variant, the variant PP2 reads it for. Both genes' profiles move from 2.95 to
    # 3.52 and neither variant's annotation changes.
    missense = _stored(_freeze(_annotated("h1", _MANE), gene_missense_z=2.95))
    moved = cds._diff(missense, _now(_annotated("h1", _MANE), gene_missense_z=3.52))
    assert (moved["status"], moved["changed"]) == ("drifted", ["gene_missense_z"])
    stop_gained = _stored(_freeze(_annotated("h1", _LOF), gene_missense_z=2.95))
    assert "gene_constraint" not in stop_gained
    unmoved = cds._diff(stop_gained, _now(_annotated("h1", _LOF), gene_missense_z=3.52))
    assert (unmoved["status"], unmoved["changed"]) == ("current", [])
    assert "gene_missense_z" not in unmoved["evidence_from"] and "gene_missense_z" not in unmoved["evidence_to"]


def test_a_variant_no_longer_missense_names_its_consequence_and_its_missense_z_as_gone() -> None:
    # Classified as a missense variant; a new release calls its change a stop gained. The
    # current values are read as a classification saved now would freeze them: no missense Z.
    frozen = _stored(_freeze(_annotated("h1", _MANE), gene_missense_z=2.95))
    recalled = _csq(transcript="ENST00000000001", mane=True, consequence="stop_gained", impact="HIGH")
    diff = cds._diff(frozen, _now(_annotated("h2", recalled), gene_missense_z=2.95))
    assert diff["changed"] == ["effect", "impact", "gene_missense_z", "annotations"]
    assert (diff["evidence_from"]["gene_missense_z"], diff["evidence_to"].get("gene_missense_z")) == (2.95, None)
    # The other way round, the missense Z it did not freeze is not compared.
    earlier = _stored(_freeze(_annotated("h1", recalled)))
    became = cds._diff(earlier, _now(_annotated("h2", _MANE), gene_missense_z=3.52))
    assert became["changed"] == ["effect", "impact", "annotations"]


def test_only_a_missense_consequence_reads_the_gene_missense_z() -> None:
    # As the ACMG dialog tells a missense variant for PP2: its consequence includes
    # missense_variant, in any case and beside other terms.
    from backend.app.services.small_variant_review_acmg import reads_gene_missense_z

    def _with(effect: str | None):
        return types.SimpleNamespace(annotations=[{"effect": effect}] if effect is not None else [])

    for effect in ("missense_variant", "missense_variant&splice_region_variant", "MISSENSE_VARIANT"):
        assert reads_gene_missense_z(_with(effect)), effect
    for effect in ("stop_gained", "synonymous_variant", "inframe_deletion", "splice_region_variant", None):
        assert not reads_gene_missense_z(_with(effect)), effect


def test_the_drift_names_what_moved_in_the_order_a_reviewer_reads_it() -> None:
    frozen = _stored(_freeze(_annotated("h1", _MANE), gene_missense_z=2.95))
    # Values chosen to move every frozen field at once (LOFTEE calls no missense variant),
    # the variant still a missense one so that its missense Z is read.
    everything = _csq(
        transcript="ENST00000000002", mane=True, consequence="missense_variant&splice_region_variant",
        impact="HIGH", lof="HC", revel="0.951", gnomad_genomes="0.012",
    )
    diff = cds._diff(frozen, _now(_annotated("h2", everything), gene_missense_z=3.52))
    assert diff["changed"] == [
        "transcript_id", "effect", "impact", "lof", "gnomad_af", "gnomad_genomes_af", "revel",
        "gene_missense_z", "annotations",
    ]
    assert diff["evidence_to"]["gene_missense_z"] == 3.52


def test_a_snapshot_from_before_the_consequence_was_frozen_is_compared_on_what_it_holds() -> None:
    # Frozen with the transcript, frequencies and in-silico scores but without the
    # consequence and the missense Z: those are not compared, so a value there now, or one
    # that changed, is no drift.
    earlier = _stored(_freeze(_annotated("h1", _OTHER, _MANE)))
    del earlier["consequence"], earlier["gene_constraint"]
    diff = cds._diff(earlier, _now(_annotated("h1", _OTHER, _MANE), gene_missense_z=3.52))
    assert (diff["status"], diff["changed"]) == ("current", [])
    assert not {"effect", "impact", "lof", "gene_missense_z"} & set(diff["evidence_from"])
    # What it froze is still compared and named.
    recalled = _csq(transcript="ENST00000000001", mane=True, consequence="stop_gained", impact="HIGH", revel="0.951")
    moved = cds._diff(earlier, _now(_annotated("h1", _OTHER, recalled), gene_missense_z=3.52))
    assert (moved["status"], moved["changed"]) == ("drifted", ["revel"])


def test_a_consequence_or_constraint_group_that_cannot_be_read_is_unknown() -> None:
    now = _now(_annotated("h1", _MANE))
    for group in ("consequence", "gene_constraint"):
        unreadable = {**_stored(_freeze(_annotated("h1", _MANE))), group: "?"}
        assert cds._diff(unreadable, now)["status"] == "unknown", group


def test_evaluate_classification_drift_reads_the_genes_missense_z_in_one_query(monkeypatch) -> None:
    async def _context(session, *, family_identifier, user, project_id=None):
        return types.SimpleNamespace(family_uuid="u1", family_id="FAM1", assembly_name="GRCh38")

    other_gene = _csq(transcript="ENST00000000009", mane=True, gene="GENE2", gene_id="ENSG00000000002")
    stop_gained = _csq(
        transcript="ENST00000000010", mane=True, consequence="stop_gained", impact="HIGH", lof="HC",
        gene="GENE3", gene_id="ENSG00000000003",
    )
    reviews = [
        {
            "variant_id": "1-100-A-G",
            "acmg_class": "acmg_class_4",
            "acmg_evidence_snapshot": _stored(_freeze(_annotated("h1", _MANE), gene_missense_z=2.95)),
            "updated_by": "alice",
            "updated_at": _WHEN,
        },
        {
            # Its gene has no profile, then and now: not listed.
            "variant_id": "2-200-C-T",
            "acmg_class": "acmg_class_3",
            "acmg_evidence_snapshot": _stored(_freeze(_annotated("h2", other_gene))),
            "updated_by": "bob",
            "updated_at": _WHEN,
        },
        {
            # A stop gained, whose gene's profile moved too: no missense Z was frozen and its
            # gene is not looked up, so it is not listed.
            "variant_id": "3-300-G-A",
            "acmg_class": "acmg_class_5",
            "acmg_evidence_snapshot": _stored(_freeze(_annotated("h3", stop_gained), gene_missense_z=2.95)),
            "updated_by": "carol",
            "updated_at": _WHEN,
        },
    ]
    current_by_variant = {
        "1-100-A-G": _annotated("h1", _MANE),
        "2-200-C-T": _annotated("h2", other_gene),
        "3-300-G-A": _annotated("h3", stop_gained),
    }

    async def _current(*, assembly_name, family_guid, variant_id):
        return current_by_variant[variant_id]

    async def _structural(session, context):
        return {"checked": 0, "drifted_count": 0, "drifted": []}

    monkeypatch.setattr(cds, "build_family_metadata_context", _context)
    monkeypatch.setattr(cds, "get_small_variant_family_record", _current)
    monkeypatch.setattr(cds, "evaluate_structural_classification_drift", _structural)

    # The gene profiles were refreshed since: GENE1's and GENE3's missense Z are 3.52 now.
    session = _GeneProfiles(
        [
            _profile("GENE1", "ENSG00000000001", 3.52, when=_WHEN),
            _profile("GENE3", "ENSG00000000003", 3.52, when=_WHEN),
        ],
        other=reviews,
    )
    out = asyncio.run(cds.evaluate_classification_drift(session, family_id="FAM1", user=None))
    assert (out["checked"], out["drifted_count"]) == (3, 1)
    [item] = out["drifted"]
    assert (item["variant_id"], item["status"], item["changed"]) == ("1-100-A-G", "drifted", ["gene_missense_z"])
    assert (item["evidence_from"]["gene_missense_z"], item["evidence_to"]["gene_missense_z"]) == (2.95, 3.52)
    # One query, for the missense variants' genes only.
    assert session.gene_queries == [
        {"gene_symbols": ["GENE1", "GENE2"], "gene_ids": ["ENSG00000000001", "ENSG00000000002"]}
    ]
    from backend.app.schemas.traceability import ClassificationDriftItem

    assert ClassificationDriftItem.model_validate(item).evidence_to["gene_missense_z"] == 3.52
