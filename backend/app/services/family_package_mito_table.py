"""A family's mitochondrial calls from one mtDNA-Server (mutserve) table for all its samples.

The PGT pipeline (nf-cmgg/copgtm) writes ``mito/variants.annotated.txt``: mutserve's
annotated variant table, one row per variant per sample, its ``ID`` column naming the
sample's chrM BAM (``<sample>.depth_10x_chrM.bam``), its ``VariantLevel`` the heteroplasmy
level, its ``Coverage`` the depth and its ``GT`` the call (``1`` homoplasmic, ``0/1``
heteroplasmic). It writes no chrM VCF. The owner decided on 2026-10-30 that CoGA reads the
table itself: each sample's rows become the chrM VCF and the mutserve annotation the ``mito``
dataset's loader reads from a VCF pipeline, so they are stored and analysed alike.

A ``mito`` entry names the table (``table``) and the ID its sample's rows carry
(``table_id``); Discover proposes both, matching each ID to a member. Pure: the importer
writes what this returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .upload_safety import read_path_text_bounded


MITO_TABLE_PATTERNS = ("mito/variants.annotated.txt",)
_ID_COLUMN = "ID"
_BASES = frozenset("ACGT")
# mutserve writes a homoplasmic call as one allele and a heteroplasmic one as two.
_GENOTYPES = {"1": "1", "0/1": "0/1", "1/0": "0/1", "0|1": "0/1", "1|0": "0/1"}


@dataclass(frozen=True)
class MitoTable:
    """The table's header and rows, each row a list of cells in header order."""

    header: list[str]
    rows: list[list[str]]

    def column(self, name: str) -> int | None:
        lowered = [cell.lower() for cell in self.header]
        return lowered.index(name.lower()) if name.lower() in lowered else None

    def ids(self) -> list[str]:
        """The sample IDs the table holds rows for, in their first row's order."""
        index = self.column(_ID_COLUMN)
        if index is None:
            return []
        return list(dict.fromkeys(row[index] for row in self.rows if len(row) > index and row[index]))

    def rows_of(self, table_id: str) -> list[list[str]]:
        index = self.column(_ID_COLUMN)
        if index is None:
            return []
        return [row for row in self.rows if len(row) > index and row[index] == table_id]


def read_mito_table(path: Path) -> MitoTable:
    """The table at ``path`` (tab-separated, a header line first). A table is small: one row
    per variant per sample."""
    lines = [line for line in read_path_text_bounded(path, kind="mtDNA-Server table").splitlines() if line.strip()]
    if not lines:
        return MitoTable(header=[], rows=[])
    return MitoTable(header=lines[0].split("\t"), rows=[line.split("\t") for line in lines[1:]])


def table_id_member(table_id: str, sample_ids: list[str]) -> str | None:
    """The member a table ID names: the ID itself, or its file name up to a ``.``, ``_`` or
    ``-`` after a member's ID (``EMB1.depth_10x_chrM.bam`` is EMB1's, never EMB10's); the
    longest such member when several match. None when it names none."""
    name = table_id.replace("\\", "/").rsplit("/", 1)[-1]
    matches = [
        sample_id
        for sample_id in sample_ids
        if name == sample_id or any(name.startswith(f"{sample_id}{separator}") for separator in "._-")
    ]
    return max(matches, key=len) if matches else None


@dataclass(frozen=True)
class MitoSampleCalls:
    """One sample's rows of the table, as the ``mito`` loader reads a VCF pipeline's files."""

    vcf_text: str
    annotation_lines: list[str]
    records: int
    # Rows not written as a record: an allele that is not one base (mutserve writes a
    # deletion as ``D``), or a call it does not state as one of its genotypes.
    skipped: int


def mito_sample_calls(table: MitoTable, *, table_id: str, sample_id: str, source: str) -> MitoSampleCalls:
    """The chrM VCF of the table's rows for ``table_id``, its one sample column named
    ``sample_id``, with each row's genotype, depth (``Coverage``) and heteroplasmy level
    (``VariantLevel``, as ``AF``), and the mutserve annotation of those rows alone: a
    variant's annotation carries the sample's own level, which differs between samples."""
    columns = {name: table.column(name) for name in ("Pos", "Ref", "Variant", "VariantLevel", "Coverage", "GT", "Filter")}
    lines = [
        "##fileformat=VCFv4.2",
        f"##source=CoGA, from the mtDNA-Server table {source} (ID {table_id})",
        "##contig=<ID=chrM,length=16569>",
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth (mutserve Coverage)">',
        '##FORMAT=<ID=AF,Number=A,Type=Float,Description="Heteroplasmy level (mutserve VariantLevel)">',
        "\t".join(["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", sample_id]),
    ]
    rows = table.rows_of(table_id)
    records = skipped = 0

    def cell(row: list[str], name: str) -> str:
        index = columns[name]
        return row[index].strip() if index is not None and index < len(row) else ""

    for row in rows:
        position, ref, alt = cell(row, "Pos"), cell(row, "Ref").upper(), cell(row, "Variant").upper()
        genotype = _GENOTYPES.get(cell(row, "GT"))
        if not position.isdigit() or ref not in _BASES or alt not in _BASES or ref == alt or genotype is None:
            skipped += 1
            continue
        depth = cell(row, "Coverage") or "."
        level = cell(row, "VariantLevel") or "."
        lines.append(
            "\t".join(
                ["chrM", position, ".", ref, alt, ".", cell(row, "Filter") or ".", ".", "GT:DP:AF", f"{genotype}:{depth}:{level}"]
            )
        )
        records += 1
    annotation_lines = ["\t".join(table.header), *("\t".join(row) for row in rows)]
    return MitoSampleCalls(
        vcf_text="\n".join(lines) + "\n", annotation_lines=annotation_lines, records=records, skipped=skipped
    )
