"""ClinVar's weekly VCF read into the rows of a ClinVar release (``clinvar_release_store``).

NCBI publishes one file per assembly and week, ``clinvar_YYYYMMDD.vcf.gz``: one allele per
record, anchored as a VCF writes it, chromosomes without ``chr`` and mtDNA as ``MT``. The file
names its release twice, in its name and in ``##fileDate``, and its assembly in
``##reference``; a file whose header says otherwise than its name and the assembly it is loaded
for is refused (``ClinvarFileRefused``) before any record is read.

Each record becomes one row keyed by its allele in minimal form (``small_allele_key``), as a
family's rows are, with ClinVar's values as published: the classification (CLNSIG), its terms as
the import stores the annotated ClinVar's (``_status_terms``), the conflicting classifications,
the review status as words, the conditions, the genes, VariationID and AlleleID. A variant
ClinVar classifies only as part of a haplotype or combination has CLNSIGINCL and no CLNSIG: its
row has no classification and is marked ``included_only``. A record that names no allele is
skipped, counted by its reason.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import unquote

from .clickhouse_variant_ids import minimal_allele_id, small_allele_key
from .clickhouse_variant_records import _status_terms
from .clinvar_review_status import clinvar_conditions, clinvar_review_status_words

# NCBI's file name of a weekly release, which names its date.
CLINVAR_FILE_NAME = re.compile(r"clinvar_(\d{8})\.vcf\.gz")

# Why a record is not stored, as the job counts them (``clinvar_load_jobs.records_skipped``).
SKIP_NO_ALT = "no ALT"
SKIP_NOT_AN_ALLELE = "not a sequence of bases"
SKIP_SEVERAL_ALTS = "several ALTs"
SKIP_NO_VARIATION_ID = "no VariationID"

# The columns of a release's rows, in the order ``ClinvarRow.values`` gives them.
CLINVAR_ROW_COLUMNS: tuple[str, ...] = (
    "release",
    "allele_key",
    "variantId",
    "variation_id",
    "allele_id",
    "classification",
    "classification_terms",
    "conflicting",
    "review_status",
    "conditions",
    "genes",
    "included_only",
)


class ClinvarFileRefused(RuntimeError):
    """The file is not the ClinVar release it should be: nothing of it is kept."""


def release_of_file_name(file_name: str) -> date | None:
    """The release a ClinVar file name names (``clinvar_20261122.vcf.gz`` is 2026-11-22), or
    None for a name that is not a weekly release's."""
    match = CLINVAR_FILE_NAME.fullmatch(file_name)
    if match is None:
        return None
    try:
        return date(int(match[1][:4]), int(match[1][4:6]), int(match[1][6:]))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ClinvarRow:
    """One allele of a ClinVar release, as ``clinvar_release_store`` stores it."""

    allele_key: int
    variant_id: str
    variation_id: int
    allele_id: int
    classification: str
    classification_terms: tuple[str, ...]
    conflicting: str
    review_status: str
    conditions: tuple[str, ...]
    genes: tuple[str, ...]
    included_only: bool

    def values(self, release: date) -> tuple[object, ...]:
        """The row in ``CLINVAR_ROW_COLUMNS`` order, in the partition of ``release``."""
        return (
            release,
            self.allele_key,
            self.variant_id,
            self.variation_id,
            self.allele_id,
            self.classification,
            list(self.classification_terms),
            self.conflicting,
            self.review_status,
            list(self.conditions),
            list(self.genes),
            self.included_only,
        )


@dataclass(slots=True)
class ClinvarFileReader:
    """Reads one ClinVar file, line by line, for one assembly and release: the header checked
    as it ends, then one row per record that names an allele. ``records_read`` and ``skipped``
    count what was read."""

    assembly_name: str
    release: date
    file_name: str
    records_read: int = field(default=0, init=False)
    skipped: Counter[str] = field(default_factory=Counter, init=False)
    _file_date: str | None = field(default=None, init=False)
    _reference: str | None = field(default=None, init=False)
    _header_done: bool = field(default=False, init=False)

    def rows(self, lines: Iterable[str]) -> Iterator[ClinvarRow]:
        """The rows of these lines. ``ClinvarFileRefused`` when the header does not name the
        release and the assembly, or a record comes before the header ends."""
        for line in lines:
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("##"):
                self._read_meta(line)
                continue
            if line.startswith("#"):
                self._check_header()
                continue
            if not self._header_done:
                raise ClinvarFileRefused(f"{self.file_name} has a record before its #CHROM header line.")
            self.records_read += 1
            row = self._row(line.split("\t"))
            if isinstance(row, str):
                self.skipped[row] += 1
            else:
                yield row

    def _read_meta(self, line: str) -> None:
        key, _, value = line[2:].partition("=")
        if key == "fileDate":
            self._file_date = value.strip()
        elif key == "reference":
            self._reference = value.strip()

    def _check_header(self) -> None:
        if self._file_date != self.release.isoformat():
            raise ClinvarFileRefused(
                f"{self.file_name} states ##fileDate={self._file_date or '(none)'}, not the "
                f"release its name gives ({self.release.isoformat()})."
            )
        if self._reference != self.assembly_name:
            raise ClinvarFileRefused(
                f"{self.file_name} states ##reference={self._reference or '(none)'}, not the "
                f"assembly it is loaded for ({self.assembly_name})."
            )
        self._header_done = True

    def _row(self, cells: list[str]) -> ClinvarRow | str:
        """The record's row, or the reason it is skipped."""
        if len(cells) < 8:
            raise ClinvarFileRefused(f"{self.file_name} has a record of {len(cells)} columns, not 8.")
        chrom, pos, variation, ref, alt, info = cells[0], cells[1], cells[2], cells[3], cells[4], cells[7]
        if alt in ("", "."):
            return SKIP_NO_ALT
        if "," in alt:
            return SKIP_SEVERAL_ALTS
        if not variation.isdigit():
            return SKIP_NO_VARIATION_ID
        if not pos.isdigit():
            raise ClinvarFileRefused(f"{self.file_name} has a record whose POS is no number.")
        allele = minimal_allele_id(chrom, int(pos), ref, alt)
        if allele is None:
            return SKIP_NOT_AN_ALLELE
        fields = _info(info)
        classification = fields.get("CLNSIG", "")
        allele_id = fields.get("ALLELEID", "")
        return ClinvarRow(
            allele_key=small_allele_key(self.assembly_name, allele),
            variant_id=allele,
            variation_id=int(variation),
            allele_id=int(allele_id) if allele_id.isdigit() else 0,
            classification=classification,
            classification_terms=tuple(sorted(_status_terms(classification))),
            conflicting=fields.get("CLNSIGCONF", ""),
            review_status=clinvar_review_status_words(fields.get("CLNREVSTAT", "")),
            conditions=tuple(clinvar_conditions(fields.get("CLNDN", ""))),
            genes=tuple(_genes(fields.get("GENEINFO", ""))),
            included_only=not classification and bool(fields.get("CLNSIGINCL")),
        )


def _info(info: str) -> dict[str, str]:
    """The INFO column's values by key, each percent-decoded as the VCF encodes ``,;=%``."""
    values: dict[str, str] = {}
    for item in info.split(";"):
        key, _, value = item.partition("=")
        if key:
            values[key] = unquote(value)
    return values


def _genes(geneinfo: str) -> list[str]:
    """The gene symbols of GENEINFO (``BRCA1:672|NBR2:10230``), each once."""
    genes: list[str] = []
    for part in geneinfo.split("|"):
        symbol = part.partition(":")[0].strip()
        if symbol and symbol not in genes:
            genes.append(symbol)
    return genes
