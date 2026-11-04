"""Independent synthetic PCA arithmetic and fail-closed genotype/reference boundaries."""
import base64
import hashlib
import json
import shutil

import numpy as np
import pytest

from backend.app.ancestry_schemas import AncestryReferenceOut
from backend.app.services import ancestry_projection as ancestry
from backend.app.services.ancestry_projection import AncestryInputError, AncestryMarker


def reference():
    public = AncestryReferenceOut(sha256="0" * 64, name="Synthetic reference", source_url="https://example.org/reference",
        source_revision="synthetic", method="Synthetic orthogonal axes", variant_count=4, sample_count=5,
        explained_variance=[0.4, 0.3, 0.2, 0.1], populations={"SYN": "Synthetic"}, points=[])
    return ancestry.AncestryReference(public, [AncestryMarker("1", i + 1, "A", "G") for i in range(4)],
        np.array([1, 0.5, 1.5, 0.25]), np.array([1, 0.5, 1, 0.25]), np.eye(4))


MARKER = AncestryMarker("1", 1, "A", "G")


@pytest.mark.parametrize("gt,ref,alt,expected", [
    ("0/0", "A", "G", ("called", 0)), ("0|1", "A", "G", ("called", 1)), ("1/1", "A", "G", ("called", 2)),
    ("./.", "A", "G", ("no_call", 0)), ("0/.", "A", "G", ("no_call", 0)), ("", "A", "G", ("no_call", 0)),
    ("1", "A", "G", ("invalid_gt", 0)), ("0/2", "A", "G", ("invalid_gt", 0)), ("het", "A", "G", ("invalid_gt", 0)),
    ("0/1", "C", "G", ("allele_mismatch", 0)), ("0/1", "A", "T", ("allele_mismatch", 0)),
    # A third allele at the marker leaves it out, even where the individual does not carry it.
    ("0/1", "A", "G,T", ("allele_mismatch", 0)),
    ("0/1", "a", "g", ("called", 1)),
])
def test_a_call_is_read_only_as_a_diploid_genotype_of_the_markers_own_alleles(gt, ref, alt, expected):
    assert ancestry.genotype_dosage(gt, ref, alt, MARKER) == expected


def test_any_non_reference_allele_counts_as_sequenced():
    assert ancestry.carries_alt("0/1") and ancestry.carries_alt("2|0") and ancestry.carries_alt("1")
    assert not ancestry.carries_alt("0/0") and not ancestry.carries_alt("./.") and not ancestry.carries_alt("0|.")


def test_with_every_marker_usable_the_fit_is_the_plain_projection():
    model = reference()
    dosages = np.array([1.0, 2.0, 0.0, 2.0])
    pcs = ancestry.project_least_squares(model, dosages, np.ones(4, dtype=bool))
    assert pcs == pytest.approx([0, 3, -1.5, 7])
    standardized = (dosages - model.means) / model.scales
    assert pcs == pytest.approx((standardized @ model.loadings).tolist())


def test_a_missing_marker_is_left_out_rather_than_pulled_to_the_centre():
    rng = np.random.default_rng(7)
    loadings, _ = np.linalg.qr(rng.normal(size=(200, 4)))
    model = ancestry.AncestryReference(reference().public, [AncestryMarker("1", i + 1, "A", "G") for i in range(200)],
                                       np.ones(200), np.ones(200), loadings)
    # An individual exactly on the axes: its standardized dosages are a combination of them.
    truth = np.array([3.0, -2.0, 1.0, 0.5])
    dosages = 1 + loadings @ truth
    usable = np.ones(200, dtype=bool)
    usable[::3] = False
    assert ancestry.project_least_squares(model, dosages, usable) == pytest.approx(truth.tolist())
    # Filling the missing markers with the reference mean instead shrinks the point.
    filled = np.where(usable, dosages - 1, 0) @ loadings
    assert np.linalg.norm(filled) < 0.9 * np.linalg.norm(truth)


def test_too_few_or_degenerate_markers_give_no_coordinates():
    model = reference()
    assert ancestry.project_least_squares(model, np.ones(4), np.array([True, True, True, False])) is None
    flat = ancestry.AncestryReference(model.public, model.markers, model.means, model.scales, np.zeros((4, 4)))
    assert ancestry.project_least_squares(flat, np.ones(4), np.ones(4, dtype=bool)) is None


def test_the_bundled_reference_is_pinned_and_its_axes_are_centred():
    model = ancestry.load_reference()
    assert model.public.sha256 == ancestry.DEFAULT_REFERENCE_SHA256
    assert model.public.sample_count == 2504 and model.public.variant_count >= 1000
    assert {p.population for p in model.public.points} == {"AFR", "AMR", "EAS", "EUR", "SAS"}
    assert np.allclose(model.loadings.T @ model.loadings, np.eye(4))
    coordinates = np.array([point.pcs for point in model.public.points])
    # Reference-only PCA: the reference individuals' coordinates are centred and uncorrelated.
    assert np.allclose(coordinates.mean(axis=0), 0, atol=1e-6)
    covariance = np.cov(coordinates.T)
    assert np.allclose(covariance - np.diag(np.diag(covariance)), 0, atol=1e-6)
    assert np.allclose(model.scales, np.sqrt(model.means * (1 - model.means / 2)))
    # About two in five markers carry the ALT allele in a sequenced genome.
    assert 0.35 < model.expected_carrier.mean() < 0.45


def test_the_bundled_exome_mask_is_pinned_and_covers_the_coding_markers():
    model = ancestry.load_reference()
    assert model.exome is not None and model.exome.sha256 == ancestry.DEFAULT_EXOME_MASK_SHA256
    assert model.public.exome_mask_sha256 == model.exome.sha256
    assert "CDS of protein-coding transcripts" in model.exome.definition
    assert model.exome.covered.shape == (len(model.markers),)
    # The peddy markers were chosen in exome capture regions: most are coding.
    assert 0.8 < model.exome.covered.mean() < 0.95


def _copy_bundled(tmp_path):
    for suffix in (".npz", ".json", ".exome.json"):
        shutil.copy(ancestry.DEFAULT_REFERENCE.with_suffix(suffix), tmp_path / f"model{suffix}")
    return tmp_path / "model.npz"


def test_an_operators_model_is_checked_against_its_own_sidecar_and_mask(tmp_path):
    path = _copy_bundled(tmp_path)
    model = ancestry.load_reference(path)
    assert model.exome is not None and model.exome.covered.sum() == ancestry.load_reference().exome.covered.sum()
    # A model without a mask reads no callset as an exome.
    path.with_suffix(".exome.json").unlink()
    assert ancestry.load_reference(path).exome is None


@pytest.mark.parametrize("change", ["reference", "count", "bits"])
def test_a_mask_that_is_not_the_models_is_refused(tmp_path, change):
    path = _copy_bundled(tmp_path)
    mask_path = path.with_suffix(".exome.json")
    mask = json.loads(mask_path.read_text())
    if change == "reference":
        mask["reference_sha256"] = "f" * 64
    elif change == "count":
        mask["covered_count"] += 1
    else:
        bits = np.unpackbits(np.frombuffer(base64.b64decode(mask["covered"]), dtype=np.uint8))
        bits[-1] = 1  # a bit past the last marker
        mask["covered"] = base64.b64encode(np.packbits(bits).tobytes()).decode()
    mask_path.write_text(json.dumps(mask))
    with pytest.raises(AncestryInputError, match="reference is unavailable or failed validation"):
        ancestry.load_reference(path)


def test_a_changed_bundled_mask_or_reference_is_refused(tmp_path, monkeypatch):
    path = _copy_bundled(tmp_path)
    # Pretend the copy is the bundled model: its pins then apply.
    monkeypatch.setattr(ancestry, "DEFAULT_REFERENCE", path.resolve())
    assert ancestry.load_reference(path).exome is not None
    mask_path = path.with_suffix(".exome.json")
    mask_path.write_text(mask_path.read_text().replace('"definition": "', '"definition": "changed '))
    with pytest.raises(AncestryInputError):
        ancestry.load_reference(path)
    bad = tmp_path / "reference.npz"
    bad.write_bytes(b"changed public reference")
    bad.with_suffix(".json").write_text(json.dumps({"sha256": hashlib.sha256(b"x").hexdigest()}))
    with pytest.raises(AncestryInputError, match="reference is unavailable or failed validation"):
        ancestry.load_reference(bad)
