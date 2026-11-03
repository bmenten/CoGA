"""Bounded evidence about a VCF; genome-wide distribution is not callable coverage."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
from typing import Any

from .data_scope import normalize_chromosome

AUTOSOMES = frozenset(str(i) for i in range(1, 23))
_MASK = (1 << 64) - 1


class WgsInputError(ValueError):
    """Fixed non-identifying input error for WGS verification and ancestry jobs."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gt_hash(chrom: str, pos: int, ref: str, alt: str, sample: str, gt: str, filt: str) -> int:
    value = "\t".join((chrom, str(pos), ref, alt, sample, gt, filt or ".")).encode()
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "little")


@dataclass
class WgsEvidence:
    samples: list[str] = field(default_factory=list)
    records: int = 0
    filters: Counter[str] = field(default_factory=Counter)
    chromosomes: Counter[str] = field(default_factory=Counter)
    bins: set[tuple[str, int]] = field(default_factory=set)
    builds: set[str] = field(default_factory=set)
    imputed: bool = False
    gvcf: bool = False
    curated: bool = False
    malformed: int = 0
    has_gt: bool = True
    call_count: int = 0
    call_sum: int = 0
    call_xor: int = 0

    def header(self, line: str) -> None:
        if line.startswith("##CoGA_RecordPolicy="):
            self.curated |= bool(re.search(r"review|exclud|remov", line, re.I))
        if line.startswith("##reference="):
            for build, pattern in [("GRCh37", r"GRCh37|hg19"), ("GRCh38", r"GRCh38|hg38")]:
                if re.search(pattern, line, re.I):
                    self.builds.add(build)
        if line.startswith(("##source=", "##source_version=", "##GLIMPSE", "##Imputation")):
            self.imputed |= bool(re.search(r"glimpse|imput", line, re.I))
        if line.startswith("#CHROM\t"):
            self.samples = line.rstrip("\r\n").split("\t")[9:]

    def observe(self, fields: list[str], *, fingerprint: bool = False) -> None:
        self.records += 1
        if len(fields) != 9 + len(self.samples) or not self.samples:
            self.malformed += 1
            return
        try:
            pos = int(fields[1])
        except ValueError:
            self.malformed += 1
            return
        if not 1 <= pos <= 4_294_967_295:
            self.malformed += 1
            return
        chrom = normalize_chromosome(fields[0])
        self.chromosomes[chrom if chrom in AUTOSOMES | {"X", "Y", "M", "MT"} else "other"] += 1
        filt = fields[6] or "."
        self.filters[filt if filt in self.filters or len(self.filters) < 100 else "other"] += 1
        if chrom in AUTOSOMES:
            self.bins.add((chrom, pos // 1_000_000))
        self.gvcf |= any(a in {"<NON_REF>", "<*>"} for a in fields[4].split(","))
        fmt = fields[8].split(":")
        if "GT" not in fmt:
            self.has_gt = False
            return
        if fingerprint:
            index = fmt.index("GT")
            for sample, raw in zip(self.samples, fields[9:]):
                values = raw.split(":")
                if index >= len(values):
                    self.malformed += 1
                    continue
                h = gt_hash(chrom, pos, fields[3], fields[4], sample, values[index], fields[6])
                self.call_count += 1
                self.call_sum = (self.call_sum + h) & _MASK
                self.call_xor ^= h

    def summary(self) -> dict[str, Any]:
        autosomes = sorted(set(self.chromosomes) & AUTOSOMES, key=int)
        unsupported = self.imputed or self.gvcf or not self.has_gt or bool(self.malformed)
        candidate = not unsupported and len(autosomes) == 22 and self.records >= 1_000_000 and len(self.bins) >= 1000
        warnings = []
        if self.filters.get("."):
            warnings.append("Unfiltered records (FILTER=.) require an explicit acceptance policy; they are not PASS calls.")
        if not candidate:
            warnings.append("Genome-wide coverage cannot be established from this VCF's distribution; confirm its assay and completeness.")
        if self.builds != {"GRCh38"}:
            warnings.append("The VCF does not unambiguously declare GRCh38.")
        if self.imputed:
            warnings.append("Imputed genotypes are not a directly called WGS source.")
        if self.gvcf:
            warnings.append("gVCF reference blocks are not supported by the exploratory genotype reader.")
        if self.curated:
            warnings.append("Preparation held or removed records: an absent site may represent an omitted call rather than a reference genotype.")
        return {"record_count": self.records, "samples": self.samples, "autosomes": autosomes,
                "autosomal_bin_count": len(self.bins), "filter_counts": dict(self.filters),
                "declared_builds": sorted(self.builds), "genome_wide_candidate": candidate,
                "imputed": self.imputed, "gvcf": self.gvcf, "curated_callset": self.curated, "has_gt": self.has_gt,
                "malformed_records": self.malformed, "warnings": warnings}

    def fingerprint(self) -> str:
        return f"wgs-file-gt-v1:{self.call_count}:{self.call_sum}:{self.call_xor}"
