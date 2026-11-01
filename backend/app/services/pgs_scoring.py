"""Exploratory GT-only PGS. No clinical classification, imputation or strand guessing.

Positions are one based. Only absence of a *record*, in an explicitly declared full-WGS
source, can mean REF. A present but unusable record always has a separate QC outcome.
"""
from __future__ import annotations

from collections import Counter
import csv
from dataclasses import asdict, dataclass
import gzip
import hashlib
import math
from pathlib import Path
import re
from typing import Any, Iterable, TextIO

CALCULATION_VERSION = "coga-pgs-gt-v1"
SHORTLIST = (
    "PGS005091", "PGS000013", "PGS000024", "PGS012555", "PGS004930", "PGS000004",
    "PGS000662", "PGS000310", "PGS002804", "PGS003724", "PGS000327", "PGS002790",
)
BASES = frozenset("ACGT")
GRCH38_LENGTHS = (248956422, 242193529, 198295559, 190214555, 181538259, 170805979,
                 159345973, 145138636, 138394717, 133797422, 135086622, 133275309,
                 114364328, 107043718, 101991189, 90338345, 83257441, 80373285,
                 58617616, 64444167, 46709983, 50818468)


class PgsInputError(ValueError):
    """Fixed, non-identifying message suitable for a job's error record."""


@dataclass(frozen=True, slots=True)
class Weight:
    chrom: str
    pos: int
    ref: str
    effect: str
    other: str | None
    weight: float


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chrom(value: str) -> str:
    value = value.removeprefix("chr")
    if value not in {str(i) for i in range(1, 23)}:
        raise PgsInputError("Only autosomal GRCh38 SNV models are supported.")
    return value


def parse_weights(value: str, pgs_id: str, fasta_path: str | None = None) -> tuple[list[Weight], dict[str, str]]:
    """PGS Catalog harmonized or explicit GRCh38 TSV; reject unsupported models in full.

    other_allele is never used to infer REF. Resolved references are saved with the version,
    so historical scoring never depends on a later FASTA or index change.
    """
    comments: dict[str, str] = {}
    rows = []
    for line in value.splitlines():
        if line.startswith("#"):
            key, sep, item = line[1:].partition("=")
            if sep:
                comments[key.strip()] = item.strip()
        elif line.strip():
            rows.append(line)
    if comments.get("pgs_id", pgs_id) != pgs_id:
        raise PgsInputError("The scoring file names another PGS ID.")
    reader = csv.DictReader(rows, delimiter="\t")
    fields = set(reader.fieldnames or [])
    harmonized = {"hm_chr", "hm_pos"} <= fields
    build = comments.get("HmPOS_build") if harmonized else comments.get("genome_build")
    if build != "GRCh38":
        raise PgsInputError("The scoring file must explicitly declare GRCh38 coordinates.")
    if not {"effect_allele", "effect_weight"} <= fields:
        raise PgsInputError("The scoring file needs effect_allele and additive effect_weight.")
    if fields & {"is_dominant", "is_recessive", "is_haplotype", "is_diplotype", "interaction"}:
        raise PgsInputError("Non-additive models are not supported.")
    if not harmonized and not {"chr_name", "chr_position"} <= fields:
        raise PgsInputError("The scoring file needs GRCh38 chromosome and position columns.")
    fasta = None
    provenance: dict[str, str] = {"reference": "explicit ref_allele"}
    if fasta_path:
        import pysam

        path = Path(fasta_path)
        if not Path(f"{path}.fai").is_file():
            raise PgsInputError("An indexed GRCh38 FASTA is required; its .fai is missing.")
        fasta = pysam.FastaFile(str(path))
        provenance = {"reference": "indexed GRCh38 FASTA", "fasta_sha256": sha256_file(path),
                      "fai_sha256": sha256_file(Path(f"{path}.fai"))}
    weights: list[Weight] = []
    seen: set[tuple[str, int]] = set()
    try:
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise PgsInputError("A scoring-file row has missing or extra columns.")
            chrom = _chrom(row["hm_chr" if harmonized else "chr_name"])
            try:
                pos = int(row["hm_pos" if harmonized else "chr_position"])
                effect_weight = float(row["effect_weight"])
            except (ValueError, TypeError) as exc:
                raise PgsInputError("A model position or effect weight is invalid.") from exc
            if not 1 <= pos <= GRCH38_LENGTHS[int(chrom) - 1] or not math.isfinite(effect_weight):
                raise PgsInputError("Model coordinates and weights must be finite GRCh38 values.")
            # JSONB canonicalizes negative zero. Freeze the same representation before
            # computing the resolved checksum, so a legitimate zero-weight model verifies.
            if effect_weight == 0:
                effect_weight = 0.0
            effect = row["effect_allele"].upper()
            other = (row.get("other_allele") or "").upper() or None
            ref = (row.get("ref_allele") or "").upper()
            if fasta is not None:
                contig = chrom if chrom in fasta.references else f"chr{chrom}"
                try:
                    reference = fasta.fetch(contig, pos - 1, pos).upper()
                except (KeyError, ValueError) as exc:
                    raise PgsInputError("The FASTA does not cover a model position.") from exc
                if ref and ref != reference:
                    raise PgsInputError("ref_allele disagrees with the GRCh38 FASTA.")
                ref = reference
            if ref not in BASES:
                raise PgsInputError("Every locus needs ref_allele or an indexed GRCh38 FASTA.")
            if effect not in BASES or (other is not None and other not in BASES) or effect == other:
                raise PgsInputError("Only single-base, distinct effect/other alleles are supported.")
            if (chrom, pos) in seen:
                raise PgsInputError("Duplicate model positions are not supported.")
            seen.add((chrom, pos))
            weights.append(Weight(chrom, pos, ref, effect, other, effect_weight))
    finally:
        if fasta is not None:
            fasta.close()
    if not weights:
        raise PgsInputError("The scoring file contains no weights.")
    if "variants_number" in comments:
        try:
            matches = int(comments["variants_number"]) == len(weights)
        except ValueError as exc:
            raise PgsInputError("The declared model size is invalid.") from exc
        if not matches:
            raise PgsInputError("The scoring file does not contain its declared number of weights.")
    return weights, provenance


def weight_dicts(weights: Iterable[Weight]) -> list[dict[str, Any]]:
    return [asdict(weight) for weight in weights]


def _call(record: list[str], sample_index: int, weight: Weight) -> tuple[str, int]:
    if len(record) <= 9 + sample_index:
        return "missing_sample", 0
    if record[6] != "PASS":
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
    allowed = {weight.ref, weight.effect, weight.other}
    if alleles[0] != weight.ref or any(a not in BASES or a not in allowed for a in alleles):
        return "allele_mismatch", 0
    indexes = [int(item) for item in re.split(r"[/|]", gt)]
    if any(index >= len(alleles) for index in indexes):
        return "invalid_gt", 0
    return "called", sum(alleles[index] == weight.effect for index in indexes)


def score_vcf(stream: TextIO, models: dict[str, list[Weight]], samples: list[str], *, assume_ref: bool) -> list[dict[str, Any]]:
    """Stream one complete VCF once for every selected version and sample.

    Retain only model positions; duplicates/split loci abstain. A missing sample in the
    header is never eligible for the absent-record assumption, even at an absent locus.
    """
    positions = {(w.chrom, w.pos) for weights in models.values() for w in weights}
    records: dict[tuple[str, int], list[str] | None] = {}
    columns: list[str] | None = None
    for line in stream:
        if line.startswith("##"):
            if line.startswith("##reference=") and re.search(r"GRCh37|hg19", line, re.I):
                raise PgsInputError("The WGS VCF declares another genome build.")
            continue
        if line.startswith("#CHROM\t"):
            if columns is not None:
                raise PgsInputError("The WGS VCF has repeated headers.")
            columns = line.rstrip("\r\n").split("\t")[9:]
            if len(columns) != len(set(columns)):
                raise PgsInputError("The WGS VCF has duplicate sample columns.")
            continue
        if not line.strip() or line.startswith("#"):
            continue
        if columns is None:
            raise PgsInputError("The WGS VCF has no sample header.")
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) < 8:
            raise PgsInputError("The WGS VCF contains a malformed record.")
        try:
            pos = int(parts[1])
        except ValueError as exc:
            raise PgsInputError("The WGS VCF contains an invalid position.") from exc
        key = (parts[0].removeprefix("chr"), pos)
        if key in positions:
            records[key] = None if key in records else parts
    if columns is None:
        raise PgsInputError("The WGS VCF has no sample header.")
    sample_indexes = {sample: columns.index(sample) for sample in samples if sample in columns}
    results: list[dict[str, Any]] = []
    for version_id, weights in models.items():
        for sample in samples:
            qc: Counter[str] = Counter()
            terms: list[float] = []
            for weight in weights:
                key = (weight.chrom, weight.pos)
                if sample not in sample_indexes:
                    status, dosage = "missing_sample", 0
                elif key not in records:
                    status = "assumed_ref" if assume_ref else "missing_record"
                    dosage = 2 if weight.effect == weight.ref else 0
                elif records[key] is None:
                    status, dosage = "ambiguous_record", 0
                else:
                    record = records[key]
                    assert record is not None
                    status, dosage = _call(record, sample_indexes[sample], weight)
                qc[status] += 1
                if status in {"called", "assumed_ref"}:
                    terms.append(weight.weight * dosage)
            total = math.fsum(terms)
            if not math.isfinite(total):
                raise PgsInputError("The score exceeds the supported numeric range.")
            complete = qc["called"] + qc["assumed_ref"] == len(weights)
            results.append({"version_id": version_id, "sample_id": sample,
                            "raw_score": total if complete else None,
                            "status": "complete" if complete else "incomplete",
                            "qc": {"total": len(weights), **dict(qc)}})
    return results


def score_file(path: Path, expected_sha256: str, models: dict[str, list[Weight]], samples: list[str], *, assume_ref: bool) -> list[dict[str, Any]]:
    if sha256_file(path) != expected_sha256:
        raise PgsInputError("The WGS source changed since import; import it again.")
    with path.open("rb") as handle:
        compressed = handle.read(2) == b"\x1f\x8b"
    opener = gzip.open if compressed else open
    with opener(path, "rt", encoding="utf-8") as stream:
        result = score_vcf(stream, models, samples, assume_ref=assume_ref)
    if sha256_file(path) != expected_sha256:
        raise PgsInputError("The WGS source changed during calculation; no results were saved.")
    return result
