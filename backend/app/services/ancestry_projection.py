"""Fixed-reference, variance-standardized PCA projection on autosomal GRCh38 SNPs.

No family data trains the axes. Missing calls are reference-mean centered only when
the sample passes the explicit marker/call-rate thresholds. No ancestry percentages
or categorical ancestry labels are inferred.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import gzip
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, TextIO

import numpy as np

from ..ancestry_schemas import AncestryReferenceOut, AncestryReferencePoint, AncestrySampleOut
from .wgs_evidence import WgsInputError, sha256_file

CALCULATION_VERSION = "coga-ancestry-pca-v1"
MIN_MARKERS = 1000
MIN_CALL_RATE = 0.95
DEFAULT_REFERENCE = Path(__file__).resolve().parents[1] / "data" / "ancestry" / "1000g-grch38-v1.npz"


@dataclass(frozen=True, slots=True)
class AncestryMarker:
    chrom: str
    pos: int
    ref: str
    alt: str


def _call(record: list[str], sample_index: int, marker: AncestryMarker, *, allow_unfiltered: bool = False) -> tuple[str, int]:
    if len(record) <= 9 + sample_index:
        return "missing_sample", 0
    if record[6] != "PASS" and not (allow_unfiltered and record[6] == "."):
        return "filtered", 0
    keys = record[8].split(":")
    values = record[9 + sample_index].split(":")
    call = dict(zip(keys, values))
    if call.get("FT", "PASS") not in {"PASS", "."}:
        return "filtered", 0
    gt = call.get("GT", ".")
    if "." in gt:
        return "no_call", 0
    if not re.fullmatch(r"\d+[/|]\d+", gt):
        return "invalid_gt", 0
    alleles = [record[3].upper(), *record[4].upper().split(",")]
    allowed = {marker.ref, marker.alt}
    if alleles[0] != marker.ref or any(a not in {"A", "C", "G", "T"} or a not in allowed for a in alleles):
        return "allele_mismatch", 0
    indexes = [int(item) for item in re.split(r"[/|]", gt)]
    if any(index >= len(alleles) for index in indexes):
        return "invalid_gt", 0
    return "called_unfiltered" if record[6] == "." else "called", sum(alleles[index] == marker.alt for index in indexes)


@dataclass(frozen=True)
class AncestryReference:
    public: AncestryReferenceOut
    markers: list[AncestryMarker]
    means: Any
    scales: Any
    loadings: Any


def load_reference(path: Path = DEFAULT_REFERENCE) -> AncestryReference:
    """Read a bounded, checksummed NPZ. Never deserialize Python objects/pickle."""
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("reference size")
        data = path.read_bytes()
        checksum = hashlib.sha256(data).hexdigest()
        manifest = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if manifest["sha256"] != checksum:
            raise ValueError("reference checksum")
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
        public = AncestryReferenceOut(**{k: manifest[k] for k in (
            "sha256", "name", "genome_build", "source_url", "source_revision", "method",
            "variant_count", "sample_count", "explained_variance", "populations")},
            points=[AncestryReferencePoint(id=f"1KG-{i + 1:04d}", population=group, pcs=pcs.tolist())
                    for i, (group, pcs) in enumerate(zip(groups, coordinates))])
        return AncestryReference(public, markers, means, scales, loadings)
    except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
        raise WgsInputError("The ancestry reference is unavailable or failed validation.") from exc


def read_vcf_sites(stream: TextIO, positions: set[tuple[str, int]]) -> tuple[dict[tuple[str, int], list[str] | None], list[str]]:
    """Ancestry GT-only reader: preserve missing calls and duplicate/split-site ambiguity."""
    records: dict[tuple[str, int], list[str] | None] = {}
    columns: list[str] | None = None
    for line in stream:
        if line.startswith("##"):
            if line.startswith("##reference=") and re.search(r"GRCh37|hg19", line, re.I):
                raise WgsInputError("The WGS VCF declares another genome build.")
            continue
        if line.startswith("#CHROM\t"):
            if columns is not None:
                raise WgsInputError("The WGS VCF has repeated headers.")
            columns = line.rstrip("\r\n").split("\t")[9:]
            if len(columns) != len(set(columns)):
                raise WgsInputError("The WGS VCF has duplicate sample columns.")
            continue
        if not line.strip() or line.startswith("#"):
            continue
        if columns is None:
            raise WgsInputError("The WGS VCF has no sample header.")
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) < 8:
            raise WgsInputError("The WGS VCF contains a malformed record.")
        try:
            pos = int(parts[1])
        except ValueError as exc:
            raise WgsInputError("The WGS VCF contains an invalid position.") from exc
        key = (parts[0].removeprefix("chr"), pos)
        if key in positions:
            records[key] = None if key in records else parts
    if columns is None:
        raise WgsInputError("The WGS VCF has no sample header.")
    return records, columns


def project_vcf(stream: TextIO, reference: AncestryReference, samples: list[str], *, assume_ref: bool,
                min_markers: int = MIN_MARKERS, min_call_rate: float = MIN_CALL_RATE,
                allow_unfiltered: bool = False) -> list[AncestrySampleOut]:
    positions = {(w.chrom, w.pos) for w in reference.markers}
    records, columns = read_vcf_sites(stream, positions)
    sample_indexes = {sample: columns.index(sample) for sample in samples if sample in columns}
    results = []
    for sample in samples:
        qc: Counter[str] = Counter()
        # A missing genotype contributes zero *after reference centering*. It is never
        # silently converted to homozygous REF or included in the usable-call count.
        normalized = np.zeros(len(reference.markers), dtype=np.float64)
        for i, marker in enumerate(reference.markers):
            key = (marker.chrom, marker.pos)
            if sample not in sample_indexes:
                status, dosage = "missing_sample", 0
            elif key not in records:
                status, dosage = ("assumed_ref" if assume_ref else "missing_record"), 0
            elif records[key] is None:
                status, dosage = "ambiguous_record", 0
            else:
                record = records[key]
                assert record is not None
                status, dosage = _call(record, sample_indexes[sample], marker, allow_unfiltered=allow_unfiltered)
            qc[status] += 1
            if status in {"called", "called_unfiltered", "assumed_ref"}:
                normalized[i] = (dosage - reference.means[i]) / reference.scales[i]
        usable = qc["called"] + qc.get("called_unfiltered", 0) + qc["assumed_ref"]
        rate = usable / len(reference.markers)
        enough = usable >= min_markers and rate >= min_call_rate
        pcs = (normalized @ reference.loadings).tolist() if enough else None
        results.append(AncestrySampleOut(sample_id=sample, status="projected" if enough else "insufficient_data",
                                        pcs=pcs, call_rate=rate, qc={"total": len(reference.markers),
                                        "called": qc["called"], "assumed_ref": qc["assumed_ref"], **dict(qc)}))
    return results


def project_file(path: Path, expected_sha256: str, reference: AncestryReference, samples: list[str], *,
                 assume_ref: bool, allow_unfiltered: bool = False) -> list[AncestrySampleOut]:
    if sha256_file(path) != expected_sha256:
        raise WgsInputError("The WGS source changed since import; import it again.")
    with path.open("rb") as handle:
        compressed = handle.read(2) == b"\x1f\x8b"
    opener = gzip.open if compressed else open
    with opener(path, "rt", encoding="utf-8") as stream:
        result = project_vcf(stream, reference, samples, assume_ref=assume_ref, allow_unfiltered=allow_unfiltered)
    if sha256_file(path) != expected_sha256:
        raise WgsInputError("The WGS source changed during calculation; no results were saved.")
    return result
