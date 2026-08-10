"""GENCODE GTF → `genes` rows.

The reference gene table used to be UCSC refGene, which gave transcript spans and
little else: `biotype` was the literal string ``unknown`` on every row, there were no
Ensembl or HGNC identifiers, and the gene set was whatever that one UCSC track carried.
GENCODE is the annotation the rest of the pipeline is already built on — dbNSFP 5.4 is
built on GENCODE 50 / Ensembl 116 — so taking the gene table from the same release
makes the coordinates agree with the annotation, and brings real biotypes, MANE flags,
and the `hgnc_id` that lets gene records join to HGNC by identifier instead of by name.

Row shape is deliberately unchanged: **one row per transcript**, with `gene_id` holding
the transcript identifier, exactly as the refGene import produced. That keeps the
sixteen consumers of the table working on the same shape, and confines the change to
the content of the rows.

The GTF is streamed, never held in memory: the full annotation is ~250k transcripts and
several million exon lines. Features arrive grouped (gene, then its transcripts, then
each transcript's exons), so a transcript is emitted once the next transcript or gene
begins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Iterable, Iterator

from .data_scope import normalize_chromosome

# GENCODE states its own release in the GTF preamble:
#   ##description: evidence-based annotation of the human genome (GRCh38), version 50 (Ensembl 116)
#   ##date: 2026-04-08
_VERSION_RE = re.compile(r"version\s+(\d+\w*)\s*\(Ensembl\s+(\d+)\)", re.IGNORECASE)
_DATE_RE = re.compile(r"^##date:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_ATTR_RE = re.compile(r'(\S+)\s+"([^"]*)"')


@dataclass(slots=True)
class GencodeRelease:
    version: str | None = None
    ensembl_release: str | None = None
    date: str | None = None

    @property
    def label(self) -> str | None:
        if self.version and self.ensembl_release:
            return f"v{self.version} (Ensembl {self.ensembl_release})"
        return f"v{self.version}" if self.version else None


def parse_gencode_release(header_lines: Iterable[str]) -> GencodeRelease:
    release = GencodeRelease()
    for line in header_lines:
        if not line.startswith("#"):
            break
        if release.version is None and (match := _VERSION_RE.search(line)):
            release.version, release.ensembl_release = match.group(1), match.group(2)
        if release.date is None and (match := _DATE_RE.match(line)):
            release.date = match.group(1)
    return release


def _attributes(raw: str) -> dict[str, Any]:
    """Parse a GTF attribute column.

    ``tag`` repeats — a transcript carries ``basic``, ``MANE_Select``, ``CCDS`` and so
    on — so tags are collected into a list while every other key keeps its last value.
    """
    attributes: dict[str, Any] = {}
    tags: list[str] = []
    for key, value in _ATTR_RE.findall(raw):
        if key == "tag":
            tags.append(value)
        else:
            attributes[key] = value
    if tags:
        attributes["tags"] = tags
    return attributes


def _strip_version(identifier: str) -> str:
    """ENSG00000012048.24 → ENSG00000012048. Versions churn; the stem is the join key."""
    return identifier.split(".", 1)[0] if identifier.startswith("ENS") else identifier


@dataclass(slots=True)
class _Transcript:
    gene_attributes: dict[str, Any]
    attributes: dict[str, Any]
    chrom: str
    start: int
    end: int
    strand: int
    exons: list[dict[str, int]] = field(default_factory=list)

    def as_row(self, *, assembly_id: str, refseq_by_transcript: dict[str, list[str]]) -> dict[str, Any]:
        transcript_id = self.attributes.get("transcript_id", "")
        gene_id = self.attributes.get("gene_id", "")
        tags = self.attributes.get("tags") or []
        # Exons arrive in transcript order, which is reversed on the minus strand; number
        # them as GENCODE does (exon_number) rather than by coordinate.
        exons = [
            {"name": f"exon{index}", "start": exon["start"], "end": exon["end"]}
            for index, exon in enumerate(self.exons, start=1)
        ]
        refseq = refseq_by_transcript.get(_strip_version(transcript_id), [])
        return {
            "assembly_id": assembly_id,
            # Same semantics as the refGene import: the row is a transcript, and
            # gene_id carries its accession.
            "gene_id": transcript_id,
            "hgnc_symbol": self.attributes.get("gene_name") or self.gene_attributes.get("gene_name") or "",
            "chr": normalize_chromosome(self.chrom),
            "start": self.start,
            "end": self.end,
            "exons": json.dumps(exons),
            "strand": self.strand,
            "biotype": self.attributes.get("transcript_type") or self.gene_attributes.get("gene_type") or "unknown",
            "description": self.attributes.get("transcript_name") or "",
            "source": "gencode",
            "extra": json.dumps(
                {
                    key: value
                    for key, value in {
                        "transcript_id": transcript_id,
                        "ensembl_transcript_id": _strip_version(transcript_id),
                        "ensembl_gene_id": _strip_version(gene_id),
                        "ensembl_gene_id_versioned": gene_id,
                        "hgnc_id": self.gene_attributes.get("hgnc_id") or self.attributes.get("hgnc_id"),
                        "gene_type": self.gene_attributes.get("gene_type") or self.attributes.get("gene_type"),
                        "transcript_type": self.attributes.get("transcript_type"),
                        "transcript_name": self.attributes.get("transcript_name"),
                        "level": self.attributes.get("level"),
                        "tags": tags,
                        "mane_select": "MANE_Select" in tags,
                        "mane_plus_clinical": "MANE_Plus_Clinical" in tags,
                        "ccds_id": self.attributes.get("ccdsid"),
                        "refseq_accessions": refseq,
                        "exon_count": len(exons),
                    }.items()
                    if value not in (None, "", [], {})
                }
            ),
        }


def iter_gencode_gene_rows(
    lines: Iterable[str],
    *,
    assembly_id: str,
    refseq_by_transcript: dict[str, list[str]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream `genes` rows out of a GENCODE GTF, one per transcript."""
    refseq_by_transcript = refseq_by_transcript or {}
    gene_attributes: dict[str, Any] = {}
    current: _Transcript | None = None

    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 9:
            continue
        chrom, _source, feature, start, end, _score, strand, _frame, raw_attributes = parts[:9]

        if feature == "gene":
            if current is not None:
                yield current.as_row(assembly_id=assembly_id, refseq_by_transcript=refseq_by_transcript)
                current = None
            gene_attributes = _attributes(raw_attributes)
            continue

        if feature == "transcript":
            if current is not None:
                yield current.as_row(assembly_id=assembly_id, refseq_by_transcript=refseq_by_transcript)
            current = _Transcript(
                gene_attributes=gene_attributes,
                attributes=_attributes(raw_attributes),
                chrom=chrom,
                start=int(start),
                end=int(end),
                strand=1 if strand == "+" else -1,
            )
            continue

        if feature == "exon" and current is not None:
            current.exons.append({"start": int(start), "end": int(end)})

    if current is not None:
        yield current.as_row(assembly_id=assembly_id, refseq_by_transcript=refseq_by_transcript)


def parse_gencode_refseq_metadata(lines: Iterable[str]) -> dict[str, list[str]]:
    """Parse GENCODE's `metadata.RefSeq` file: transcript id → RefSeq accessions.

    Searching by a RefSeq accession has to keep working after the swap — the refGene
    table was keyed on them, so any saved query, panel or bookmark that names an
    ``NM_`` accession would otherwise stop resolving.
    """
    refseq_by_transcript: dict[str, list[str]] = {}
    for line in lines:
        columns = line.rstrip("\n").split("\t")
        if len(columns) < 2 or not columns[0]:
            continue
        accessions = refseq_by_transcript.setdefault(_strip_version(columns[0]), [])
        for accession in columns[1:]:
            accession = accession.strip()
            if accession and accession not in accessions:
                accessions.append(accession)
    return refseq_by_transcript
