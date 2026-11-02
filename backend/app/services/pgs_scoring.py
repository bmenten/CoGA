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
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, TextIO

CALCULATION_VERSION = "coga-pgs-gt-v1"
SHORTLIST = (
    "PGS005091", "PGS000013", "PGS000024", "PGS012555", "PGS004930", "PGS000004",
    "PGS000662", "PGS000310", "PGS002804", "PGS003724", "PGS000327", "PGS002790",
)
BASES = frozenset("ACGT")
_BASE_VALUES = {base: base for base in BASES}
_CHROM_VALUES = {str(i): str(i) for i in range(1, 23)}
_LINE_END = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")
GRCH38_LENGTHS = (248956422, 242193529, 198295559, 190214555, 181538259, 170805979,
                 159345973, 145138636, 138394717, 133797422, 135086622, 133275309,
                 114364328, 107043718, 101991189, 90338345, 83257441, 80373285,
                 58617616, 64444167, 46709983, 50818468)


class PgsInputError(ValueError):
    """Fixed, non-identifying message suitable for a job's error record."""


class PgsReferenceError(PgsInputError):
    """A model cannot be prepared until its reference alleles are available."""


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
    if value not in _CHROM_VALUES:
        raise PgsInputError("Only autosomal GRCh38 SNV models are supported.")
    return _CHROM_VALUES[value]


def text_lines(value: str) -> Iterable[str]:
    """Like splitlines(), without allocating a second copy of every model row."""
    start = 0
    for ending in _LINE_END.finditer(value):
        yield value[start:ending.start()]
        start = ending.end()
    if start < len(value):
        yield value[start:]


def sha256_text(value: str) -> str:
    digest = hashlib.sha256()
    for start in range(0, len(value), 1 << 20):
        digest.update(value[start:start + (1 << 20)].encode())
    return digest.hexdigest()


def parse_weights(value: str, pgs_id: str, fasta_path: str | None = None, max_variants: int | None = None) -> tuple[list[Weight], dict[str, str]]:
    """PGS Catalog harmonized or explicit GRCh38 TSV; reject unsupported models in full.

    other_allele is never used to infer REF. Resolved references are saved with the version,
    so historical scoring never depends on a later FASTA or index change.
    """
    comments: dict[str, str] = {}
    for line in text_lines(value):
        if line.startswith("#"):
            key, sep, item = line[1:].partition("=")
            if sep:
                comments[key.strip()] = item.strip()
    if comments.get("pgs_id", pgs_id) != pgs_id:
        raise PgsInputError("The scoring file names another PGS ID.")
    reader = csv.DictReader((line for line in text_lines(value) if line.strip() and not line.startswith("#")), delimiter="\t")
    fields = set(reader.fieldnames or [])
    harmonized = {"hm_chr", "hm_pos"} <= fields
    build = comments.get("HmPOS_build") if harmonized else comments.get("genome_build")
    if build != "GRCh38":
        raise PgsInputError("The scoring file must explicitly declare GRCh38 coordinates.")
    if not {"effect_allele", "effect_weight"} <= fields:
        raise PgsInputError("The scoring file needs effect_allele and additive effect_weight.")
    if fields & {"is_dominant", "is_recessive", "is_haplotype", "is_diplotype", "interaction", "is_interaction",
                 "dosage_0_weight", "dosage_1_weight", "dosage_2_weight"}:
        raise PgsInputError("Non-additive models are not supported.")
    if not harmonized and not {"chr_name", "chr_position"} <= fields:
        raise PgsInputError("The scoring file needs GRCh38 chromosome and position columns.")
    fasta = None
    provenance: dict[str, str] = {"reference": "explicit ref_allele"}
    if fasta_path:
        import pysam

        path = Path(fasta_path)
        if not Path(f"{path}.fai").is_file():
            raise PgsReferenceError("An indexed GRCh38 FASTA is required; its .fai is missing.")
        try:
            fasta = pysam.FastaFile(str(path))
        except OSError as exc:
            raise PgsReferenceError("The configured indexed GRCh38 FASTA is unavailable.") from exc
        provenance = {"reference": "indexed GRCh38 FASTA", "fasta_sha256": sha256_file(path),
                      "fai_sha256": sha256_file(Path(f"{path}.fai"))}
    weights: list[Weight] = []
    seen: set[int] = set()
    try:
        for row in reader:
            if max_variants is not None and len(weights) >= max_variants:
                raise PgsInputError(f"The model exceeds PGS_MAX_VARIANTS={max_variants:,}; no partial weight set is usable.")
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
                raise PgsReferenceError("Every locus needs ref_allele or an indexed GRCh38 FASTA.")
            if effect not in BASES or (other is not None and other not in BASES) or effect == other:
                raise PgsInputError("Only single-base, distinct effect/other alleles are supported.")
            locus = (int(chrom) << 32) | pos
            if locus in seen:
                raise PgsInputError("Duplicate model positions are not supported.")
            seen.add(locus)
            weights.append(Weight(chrom, pos, _BASE_VALUES[ref], _BASE_VALUES[effect],
                                  _BASE_VALUES[other] if other is not None else None, effect_weight))
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


def serialize_weights(weights: Iterable[Weight]) -> tuple[str, str]:
    """Keep the historical canonical checksum without a second full list of dicts.

    Spool JSON to disk: StringIO itself can expand large ASCII input to UTF-32.
    Only the final PostgreSQL parameter string is materialized in memory.
    """
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, allow_nan=False)
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", newline="") as stream:
        def write(part: str) -> None:
            stream.write(part)
            digest.update(part.encode())
        write("[")
        for i, weight in enumerate(weights):
            if i:
                write(", ")
            write(encoder.encode({"chrom": weight.chrom, "pos": weight.pos, "ref": weight.ref,
                                  "effect": weight.effect, "other": weight.other, "weight": weight.weight}))
        write("]")
        stream.seek(0)
        return stream.read(), digest.hexdigest()


def prepare_weights(value: str, pgs_id: str, fasta_path: str | None, max_variants: int) -> tuple[str, str, int, dict[str, str]]:
    weights, reference = parse_weights(value, pgs_id, fasta_path, max_variants)
    payload, checksum = serialize_weights(weights)
    return payload, checksum, len(weights), reference


def load_weights(rows: list[dict[str, Any]], expected_checksum: str) -> list[Weight]:
    """Verify stored rows incrementally, preserving every historical checksum."""
    digest = hashlib.sha256(b"[")
    encoder = json.JSONEncoder(sort_keys=True, allow_nan=False)
    weights = []
    for i, row in enumerate(rows):
        if i:
            digest.update(b", ")
        digest.update(encoder.encode(row).encode())
        weight = Weight(**row)
        weights.append(Weight(_CHROM_VALUES.get(weight.chrom, weight.chrom), weight.pos,
                              _BASE_VALUES.get(weight.ref, weight.ref), _BASE_VALUES.get(weight.effect, weight.effect),
                              _BASE_VALUES.get(weight.other, weight.other) if weight.other is not None else None,
                              weight.weight))
    digest.update(b"]")
    if digest.hexdigest() != expected_checksum:
        raise PgsInputError("The stored model weights failed their checksum check.")
    return weights


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
    """Stream one complete VCF for the supplied versions and samples.

    Retain only model positions; duplicates/split loci abstain. A missing sample in the
    header is never eligible for the absent-record assumption, even at an absent locus.
    """
    positions = {(int(w.chrom) << 32) | w.pos for weights in models.values() for w in weights}
    records: dict[int, list[str] | None] = {}
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
        chrom = parts[0].removeprefix("chr")
        if chrom not in _CHROM_VALUES or not 1 <= pos <= GRCH38_LENGTHS[int(chrom) - 1]:
            continue
        key = (int(chrom) << 32) | pos
        if key in positions:
            for unused in (0, 1, 2, 5, 7):
                parts[unused] = ""
            records[key] = None if key in records else parts
    if columns is None:
        raise PgsInputError("The WGS VCF has no sample header.")
    sample_indexes = {sample: columns.index(sample) for sample in samples if sample in columns}
    results: list[dict[str, Any]] = []
    for version_id, weights in models.items():
        for sample in samples:
            qc: Counter[str] = Counter()
            def contributions(weights: list[Weight] = weights, sample: str = sample,
                              qc: Counter[str] = qc) -> Iterable[float]:
                for weight in weights:
                    key = (int(weight.chrom) << 32) | weight.pos
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
                        yield weight.weight * dosage
            total = math.fsum(contributions())
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
