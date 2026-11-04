"""Fixed-reference, variance-standardized PCA projection on autosomal GRCh38 SNPs.

Exploratory and outside CoGA's intended purpose: nothing here is read clinically or enters the
sign-out. No family data trains the axes. An individual's principal components are the least-
squares fit of the reference loadings to the standardized dosages of the markers it has usable
genotypes for, so a marker without one neither counts as reference nor pulls the point to the
centre. No ancestry percentages or categorical ancestry labels are inferred.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any

import numpy as np

from ..ancestry_schemas import AncestryReferenceOut, AncestryReferencePoint

CALCULATION_VERSION = "coga-ancestry-pca-v2"
MIN_MARKERS = 1000
# Of the markers a callset is taken to cover, the share an individual needs a usable genotype at.
MIN_CALL_RATE = 0.95
DEFAULT_REFERENCE = Path(__file__).resolve().parents[1] / "data" / "ancestry" / "1000g-grch38-v1.npz"
# The bundled model and its exome mask, pinned: their JSON files certify only themselves. An
# operator's own model (ANCESTRY_REFERENCE_PATH) is checked against its sidecar alone, and its
# mask, if it has one, against the model's checksum.
DEFAULT_REFERENCE_SHA256 = "802a3590063e52897c7b58d950c003b656e284e88b0ea97d8836e6844d4b4703"
DEFAULT_EXOME_MASK_SHA256 = "73fb3a86242e1d5c9984f01831ea1fb54287c077661ee90274a91ebbd1f4c29b"
_GT = re.compile(r"(\d+)[/|](\d+)")


class AncestryInputError(ValueError):
    """A fixed, non-identifying reason an ancestry calculation cannot go on."""


@dataclass(frozen=True, slots=True)
class AncestryMarker:
    chrom: str
    pos: int
    ref: str
    alt: str


@dataclass(frozen=True)
class ExomeMask:
    """The markers an exome covers (scripts/prepare_ancestry_exome_mask.py)."""

    sha256: str
    definition: str
    covered: Any  # numpy bool array, one per marker


@dataclass(frozen=True)
class AncestryReference:
    public: AncestryReferenceOut
    markers: list[AncestryMarker]
    means: Any
    scales: Any
    loadings: Any
    exome: ExomeMask | None = None

    @property
    def expected_carrier(self) -> Any:
        """Per marker, the share of reference individuals expected to carry the ALT allele
        (Hardy-Weinberg, from the reference allele frequency): about two in five."""
        return 1 - (1 - self.means / 2) ** 2


def _load_exome_mask(path: Path, reference_sha256: str, marker_count: int, *, pinned: bool) -> ExomeMask | None:
    mask_path = path.with_suffix(".exome.json")
    if not mask_path.exists():
        if pinned:
            raise ValueError("bundled exome mask missing")
        return None
    data = mask_path.read_bytes()
    if len(data) > 1024 * 1024:
        raise ValueError("exome mask size")
    checksum = hashlib.sha256(data).hexdigest()
    if pinned and checksum != DEFAULT_EXOME_MASK_SHA256:
        raise ValueError("bundled exome mask checksum")
    manifest = json.loads(data.decode("utf-8"))
    if manifest["reference_sha256"] != reference_sha256 or manifest["marker_count"] != marker_count:
        raise ValueError("exome mask reference")
    bits = np.unpackbits(np.frombuffer(base64.b64decode(manifest["covered"], validate=True), dtype=np.uint8))
    if len(bits) < marker_count or bits[marker_count:].any():
        raise ValueError("exome mask length")
    covered = bits[:marker_count].astype(bool)
    if int(covered.sum()) != manifest["covered_count"] or not 0 < covered.sum() < marker_count:
        raise ValueError("exome mask count")
    return ExomeMask(sha256=checksum, definition=str(manifest["definition"]), covered=covered)


def load_reference(path: Path = DEFAULT_REFERENCE) -> AncestryReference:
    """Read a bounded, checksummed NPZ and its exome mask. Never deserialize Python objects/pickle."""
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("reference size")
        data = path.read_bytes()
        checksum = hashlib.sha256(data).hexdigest()
        manifest = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if manifest["sha256"] != checksum:
            raise ValueError("reference checksum")
        pinned = path.resolve() == DEFAULT_REFERENCE
        if pinned and checksum != DEFAULT_REFERENCE_SHA256:
            raise ValueError("bundled reference checksum")
        with np.load(io.BytesIO(data), allow_pickle=False) as arrays:
            means = arrays["means"].astype(np.float64)
            scales = arrays["scales"].astype(np.float64)
            loadings = arrays["loadings"].astype(np.float64)
            coordinates = arrays["coordinates"].astype(np.float64)
            loci = arrays["loci"].tolist()
            groups = arrays["populations"].tolist()
        n = len(loci)
        if (n < 1 or n > 100000 or means.shape != (n,) or scales.shape != (n,)
                or loadings.shape != (n, 4) or coordinates.shape != (len(groups), 4)
                or not 5 <= len(groups) <= 10000 or manifest["variant_count"] != n
                or manifest["sample_count"] != len(groups)
                or len(manifest["explained_variance"]) != 4):
            raise ValueError("reference dimensions")
        if (not all(np.isfinite(a).all() for a in (means, scales, loadings, coordinates))
                or not ((means > 0) & (means < 2)).all() or not (scales > 0).all()
                or not np.allclose(loadings.T @ loadings, np.eye(4), atol=1e-5)):
            raise ValueError("reference numeric values")
        markers = []
        seen = set()
        for locus in loci:
            chrom, position, ref, alt = str(locus).split(":")
            key = (chrom, int(position))
            if (chrom not in {str(i) for i in range(1, 23)} or key[1] < 1 or key in seen
                    or ref not in "ACGT" or alt not in "ACGT" or len(ref) != 1 or len(alt) != 1 or ref == alt):
                raise ValueError("reference locus")
            seen.add(key)
            markers.append(AncestryMarker(chrom, key[1], ref, alt))
        if not set(groups) <= set(manifest["populations"]):
            raise ValueError("reference labels")
        exome = _load_exome_mask(path, checksum, n, pinned=pinned)
        public = AncestryReferenceOut(**{k: manifest[k] for k in (
            "sha256", "name", "genome_build", "source_url", "source_revision", "method",
            "variant_count", "sample_count", "explained_variance", "populations")},
            exome_mask_sha256=exome.sha256 if exome else None,
            exome_mask_definition=exome.definition if exome else None,
            points=[AncestryReferencePoint(id=f"1KG-{i + 1:04d}", population=group, pcs=pcs.tolist())
                    for i, (group, pcs) in enumerate(zip(groups, coordinates))])
        return AncestryReference(public, markers, means, scales, loadings, exome)
    except (OSError, ValueError, KeyError, TypeError, EOFError, UnicodeError) as exc:
        raise AncestryInputError("The ancestry reference is unavailable or failed validation.") from exc


def genotype_dosage(gt: str, ref: str, alt: str, marker: AncestryMarker) -> tuple[str, int]:
    """The status of one diploid call at a marker and its count of the marker's ALT allele.

    ``called`` only for a diploid GT whose record's REF is the marker's and whose alleles are
    all the marker's two single bases; ``no_call``, ``invalid_gt`` or ``allele_mismatch``
    otherwise. There are no strand flips and no dosage inference.
    """
    if "." in gt or not gt:
        return "no_call", 0
    match = _GT.fullmatch(gt)
    if match is None:
        return "invalid_gt", 0
    alleles = [ref.upper(), *alt.upper().split(",")]
    allowed = {marker.ref, marker.alt}
    if alleles[0] != marker.ref or any(a not in allowed for a in alleles):
        return "allele_mismatch", 0
    indexes = [int(match.group(1)), int(match.group(2))]
    if any(index >= len(alleles) for index in indexes):
        return "invalid_gt", 0
    return "called", sum(alleles[index] == marker.alt for index in indexes)


def carries_alt(gt: str) -> bool:
    """Whether a GT holds any non-reference allele: evidence that the position was sequenced."""
    return any(int(index) > 0 for index in re.findall(r"\d+", gt))


def project_least_squares(reference: AncestryReference, dosages: Any, usable: Any) -> list[float] | None:
    """The four PCs that best fit the usable markers' standardized dosages, or None when the
    usable markers cannot determine them."""
    rows = np.flatnonzero(usable)
    if len(rows) < 4:
        return None
    loadings = reference.loadings[rows]
    gram = loadings.T @ loadings
    if not np.isfinite(gram).all() or np.linalg.cond(gram) > 1e6:
        return None
    standardized = (dosages[rows] - reference.means[rows]) / reference.scales[rows]
    return [float(value) for value in np.linalg.solve(gram, loadings.T @ standardized)]
