"""Annotation tables uploaded beside a small-variant VCF: VEP TSV and mutserve.

Each is parsed into a :class:`VepAnnotationLookup`, a temporary SQLite index keyed by
variant id (and, for VEP, by its normalised Location + Allele), which the VCF loader
queries record by record. Split out of ``variant_upload_service``, which keeps the
upload plumbing that feeds these parsers their lines.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException

from .clickhouse_variant_ids import build_small_variant_id
from .data_scope import normalize_chromosome
from .family_package_common import _normalize_header_key
from .variant_annotation_parser import (
    ALT_ALLELE_KEY,
    CLINVAR_FILE_ALIASES,
    declared_gnomad_datasets,
    entry_alt_allele,
    file_in_description,
    normalize_small_variant_annotation_entry,
    vep_allele_forms,
)
from .vcf_fields import field_int
from .vcf_header_provenance import extract_vep_tab_provenance

# A multi-allelic record's table rows are stored by the name VEP gives the record (its
# alleles after VEP's trimming, ``_vep_record_name``) with each row's own allele, which the
# lookup ties to the VCF's ALT: neither its variant id (``G,T``, VEP writes ``G/T``) nor a
# single allele's position matches a row.
_ROW_ALLELE = "_vep_allele"
_ROW_ALLELE_NUM = "_vep_allele_num"


def _vep_record_name(chrom: str, pos: int, ref: str, alts: list[str]) -> str:
    """A multi-allelic VCF record as VEP names it in a table's Uploaded_variation, chromosome
    normalised: ``{chrom}:{start}:{REF}/{ALT}/{ALT}``, after VEP trims an indel's shared first
    base (``vep_allele_forms``), which moves the start one base on."""
    forms = vep_allele_forms(ref, alts)
    if forms != list(alts):
        pos, ref = pos + 1, ref[1:] or "-"
    return f"{chrom}:{pos}:{ref}/{'/'.join(forms)}"


@dataclass(slots=True)
class VepAnnotationLookup:
    row_count: int
    conn: sqlite3.Connection | None = None
    temp_path: str | None = None
    # Annotation/DB versions parsed from the VEP TSV's ``## … version …`` header
    # (where VEP/gnomAD/ClinVar/… releases live for TSV-annotated families).
    provenance_modules: dict[str, Any] | None = None

    def get(
        self, variant_id: str, chrom: str, start: int, ref: str, alt: str
    ) -> list[dict[str, Any]] | None:
        if self.conn is None:
            return None
        if "," in alt:
            return self._multi_allelic(chrom, start, ref, alt.split(","))
        rows = self.conn.execute(
            "SELECT annotation_json FROM annotations WHERE key_type = ? AND key_value = ?",
            ("variant_id", variant_id),
        ).fetchall()
        if rows:
            return [json.loads(row[0]) for row in rows]
        # VEP left-aligns/trims indels, so its reported coordinate (and the
        # Uploaded_variation behind ``variant_id``) is shifted relative to the VCF
        # POS. Fall back to VEP's normalized Location + Allele representation, which
        # we can reconstruct from the VCF allele without a reference genome.
        locus_key = _vep_location_allele_key(chrom, start, ref, alt)
        if locus_key is None:
            return None
        rows = self.conn.execute(
            "SELECT annotation_json FROM annotations WHERE key_type = ? AND key_value = ?",
            ("locus_allele", locus_key),
        ).fetchall()
        return [json.loads(row[0]) for row in rows] or None

    def _multi_allelic(
        self, chrom: str, start: int, ref: str, alts: list[str]
    ) -> list[dict[str, Any]] | None:
        """A multi-allelic record's rows, each naming the ALT it is of (``alt_allele``)."""
        assert self.conn is not None
        rows = self.conn.execute(
            "SELECT annotation_json FROM annotations WHERE key_type = ? AND key_value = ?",
            ("record_name", _vep_record_name(chrom, start, ref, alts)),
        ).fetchall()
        annotations: list[dict[str, Any]] = []
        for row in rows:
            annotation = json.loads(row[0])
            entry = {"Allele": annotation.pop(_ROW_ALLELE, None), "ALLELE_NUM": annotation.pop(_ROW_ALLELE_NUM, None)}
            allele = entry_alt_allele(entry, ref, alts)
            if allele is not None:
                annotation[ALT_ALLELE_KEY] = allele
            annotations.append(annotation)
        return annotations or None

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self.temp_path:
            try:
                os.unlink(self.temp_path)
            except FileNotFoundError:
                pass
            self.temp_path = None


def _parse_vep_uploaded_variation(value: str) -> tuple[str, int, str | None, str | None] | None:
    parts = value.strip().split("_", 2)
    if len(parts) < 2:
        return None
    chrom = normalize_chromosome(parts[0])
    try:
        start = int(parts[1])
    except ValueError:
        return None
    ref: str | None = None
    alt: str | None = None
    if len(parts) == 3 and "/" in parts[2]:
        ref_value, alt_value = parts[2].split("/", 1)
        ref = ref_value or None
        alt = alt_value or None
    return chrom, start, ref, alt


def _parse_vep_location(value: str) -> tuple[str, int] | None:
    if not value or ":" not in value:
        return None
    chrom_value, position_value = value.split(":", 1)
    start_text = position_value.split("-", 1)[0].replace(",", "")
    try:
        return normalize_chromosome(chrom_value), int(start_text)
    except ValueError:
        return None


def _vep_location_allele_key(chrom: str, pos: int, ref: str, alt: str) -> str | None:
    """Reproduce VEP's ``{chrom}:{location}:{allele}`` for a VCF allele.

    VEP normalizes (left-aligns and trims the shared anchor base) before
    reporting a variant, so for indels its Location/Allele — and the
    Uploaded_variation it derives — sit at a different coordinate than the VCF
    POS. A VCF insertion ``A>AT`` is reported with Allele ``T`` and a deletion
    ``AC>A`` with Allele ``-`` at the shifted position. Reconstructing the same
    key here (no reference genome required) lets the annotation join find indels
    that the exact ``variant_id`` match misses.
    """
    if not ref or not alt:
        return None
    # Trim any shared suffix, keeping at least one base on each side.
    while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    prefix = 0
    while prefix < len(ref) and prefix < len(alt) and ref[prefix] == alt[prefix]:
        prefix += 1
    ref_rem, alt_rem = ref[prefix:], alt[prefix:]
    if not ref_rem and alt_rem:
        # Insertion: bases inserted just after the last shared base.
        return f"{chrom}:{pos + prefix - 1}:{alt_rem}"
    if ref_rem and not alt_rem:
        # Deletion: VEP marks the deleted span with a dash allele.
        return f"{chrom}:{pos + prefix}:-"
    if alt_rem:
        # SNV / MNV / substitution.
        return f"{chrom}:{pos + prefix}:{alt_rem}"
    return None


def _sqlite_annotation_lookup() -> VepAnnotationLookup:
    temp_file = tempfile.NamedTemporaryFile(prefix="coga-vep-", suffix=".sqlite3", delete=False)
    temp_file.close()
    conn = sqlite3.connect(temp_file.name, check_same_thread=False)
    conn.execute(
        "CREATE TABLE annotations (key_type TEXT NOT NULL, key_value TEXT NOT NULL, annotation_json TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX idx_annotations_key ON annotations (key_type, key_value)")
    return VepAnnotationLookup(
        row_count=0,
        conn=conn,
        temp_path=temp_file.name,
    )


def _indexed_lookup(
    index_lines: Callable[[VepAnnotationLookup, Any], None], lines: Any
) -> VepAnnotationLookup:
    """A new lookup filled from ``lines`` by ``index_lines``. A parse that fails closes it:
    its SQLite file is in the temporary directory, which on Cloud Run is memory."""
    lookup = _sqlite_annotation_lookup()
    try:
        index_lines(lookup, lines)
    except BaseException:
        lookup.close()
        raise
    return lookup


def _store_vep_annotation(
    lookup: VepAnnotationLookup,
    *,
    key_type: str,
    key_value: str,
    annotation: dict[str, Any],
) -> None:
    if lookup.conn is None:
        return
    lookup.conn.execute(
        "INSERT INTO annotations (key_type, key_value, annotation_json) VALUES (?, ?, ?)",
        (key_type, key_value, json.dumps(annotation)),
    )


def _vep_extra_fields(value: str) -> dict[str, str]:
    """The fields VEP's default output writes in its ``Extra`` column, as ``KEY=VALUE``
    pairs joined by ``;`` (``IMPACT=MODERATE;SYMBOL=GENE1;SIFT=deleterious(0.01)``), which
    ``--tab`` output writes as columns of their own. A key without a value is a flag."""
    fields: dict[str, str] = {}
    for item in value.split(";"):
        key, sep, field_value = item.partition("=")
        key = key.strip()
        if key and key != "-":
            fields[key] = field_value.strip() if sep else "1"
    return fields


def _parse_vep_tsv_annotation_lines(lines: Any) -> VepAnnotationLookup:
    return _indexed_lookup(_index_vep_tsv_lines, lines)


# A VEP table's description of a column or an Extra key, in its ``## Column descriptions`` or
# ``## Extra column keys`` block: ``## ClinVar_CLNSIG : … CLNSIG field from [PATH]/clinvar_….vcf.gz``.
_VEP_TAB_FIELD_DESCRIPTION = re.compile(r"^##\s*(\S+)\s*:\s*(.*)$")


def _index_vep_tsv_lines(lookup: VepAnnotationLookup, lines: Any) -> None:
    header: list[str] | None = None
    row_count = 0
    # VEP states its tool/database versions in the leading ``## … version …`` block
    # (before "## Column descriptions:"); buffer it for provenance capture.
    provenance_header: list[str] = []
    # Whether the table holds a ClinVar file's classification (a column, or a described Extra
    # key, which a row without a value leaves out), and the file VEP describes it from: then a
    # row's ClinVar classification is that file's, never the cache's CLIN_SIG.
    clinvar_file_annotated = False
    clinvar_file_name: str | None = None
    # The columns and Extra keys the table describes, each with the file its description names
    # (VEP --custom), and the gnomAD datasets whose allele frequency the table declares
    # (``declared_gnomad_datasets``), as a column or a described Extra key.
    described_files: dict[str, str] = {}
    described_fields: set[str] = set()
    gnomad_annotated: tuple[str, ...] = ()

    for raw_line in lines:
        line = raw_line.rstrip("\n\r")
        if not line:
            continue
        if line.startswith("##"):
            if len(provenance_header) < 80:
                provenance_header.append(line)
            described = _VEP_TAB_FIELD_DESCRIPTION.match(line)
            if described:
                described_fields.add(described.group(1))
                described_file = file_in_description(described.group(2))
                if described_file:
                    described_files.setdefault(described.group(1), described_file)
            if described and described.group(1) in CLINVAR_FILE_ALIASES:
                clinvar_file_annotated = True
                clinvar_file_name = clinvar_file_name or described_files.get(described.group(1))
            continue
        if line.startswith("#"):
            header = line.lstrip("#").split("\t")
            clinvar_file_annotated = clinvar_file_annotated or not set(header).isdisjoint(
                CLINVAR_FILE_ALIASES
            )
            gnomad_annotated = declared_gnomad_datasets(described_fields.union(header), described_files)
            continue
        if header is None:
            continue
        values = line.split("\t")
        row = {key: value for key, value in zip(header, values)}
        # VEP's default output (not ``--tab``) writes SYMBOL, IMPACT, CANONICAL, MANE, HGVS,
        # SIFT, PolyPhen, CLIN_SIG and the frequencies in its Extra column: read as the
        # columns ``--tab`` writes, a column of the row's own winning.
        for key, value in _vep_extra_fields(row.pop("Extra", "")).items():
            row.setdefault(key, value)
        annotation = normalize_small_variant_annotation_entry(
            row, clinvar_file_annotated=clinvar_file_annotated, gnomad_annotated=gnomad_annotated
        )
        if not annotation:
            continue
        row_count += 1

        uploaded = _parse_vep_uploaded_variation(row.get("Uploaded_variation", ""))
        allele = row.get("Allele") or None
        location = _parse_vep_location(row.get("Location", ""))

        if uploaded is not None:
            chrom, start, ref, alt = uploaded
            if ref and alt and "/" in alt:
                # A multi-allelic record (``A/G/T``): stored by VEP's name for it, with the
                # row's allele (``VepAnnotationLookup._multi_allelic``).
                _store_vep_annotation(
                    lookup,
                    key_type="record_name",
                    key_value=f"{chrom}:{start}:{ref}/{alt}",
                    annotation={
                        **annotation,
                        _ROW_ALLELE: row.get("Allele"),
                        _ROW_ALLELE_NUM: row.get("ALLELE_NUM"),
                    },
                )
            elif ref and alt:
                _store_vep_annotation(
                    lookup,
                    key_type="variant_id",
                    key_value=build_small_variant_id(chrom, start, ref, alt),
                    annotation=annotation,
                )

        # Index by VEP's normalized Location + Allele. VEP shifts/trims indels, so
        # the Uploaded_variation position differs from the VCF POS; keying on the
        # Location column (not the Uploaded_variation position) is what lets the
        # join recover indels — see _vep_location_allele_key for the lookup side.
        if location is not None and allele:
            loc_chrom, loc_start = location
            _store_vep_annotation(
                lookup,
                key_type="locus_allele",
                key_value=f"{loc_chrom}:{loc_start}:{allele}",
                annotation=annotation,
            )
        elif uploaded is not None:
            # No usable Location column: fall back to the Uploaded_variation
            # position (correct for SNVs, the best available for indels).
            chrom, start, ref, alt = uploaded
            fallback_allele = allele or alt
            if fallback_allele:
                _store_vep_annotation(
                    lookup,
                    key_type="locus_allele",
                    key_value=f"{chrom}:{start}:{fallback_allele}",
                    annotation=annotation,
                )

    if header is None:
        raise HTTPException(status_code=400, detail="VEP TSV annotation file is missing a header row")
    lookup.row_count = row_count
    lookup.provenance_modules = (
        extract_vep_tab_provenance(
            provenance_header,
            clinvar_file_annotated=clinvar_file_annotated,
            clinvar_file_name=clinvar_file_name,
        )
        or None
    )
    if lookup.conn is not None:
        lookup.conn.commit()


# mutserve's mtDNA annotation columns, mapped onto the annotation keys the mtDNA
# workspace reads. Everything not listed here is still carried through verbatim (the
# annotation is stored as JSON), so haplogroup/selection/NuMT context is not lost.
# Keys are matched after _normalize_header_key, which drops separators and case.
_MUTSERVE_ANNOTATION_FIELDS = {
    _normalize_header_key(column): key
    for column, key in {
        "VariantLevel": "variant_level",
        "Coverage": "coverage",
        "MeanBaseQuality": "mean_base_quality",
        "Mutation": "mutation",
        "Substitution": "substitution",
        "Maplocus": "gene",
        "Category": "category",
        "Phylotree17_haplogroups": "haplogroup",
        "Phylotree17_clades": "haplogroup_clades",
        "HaploGrep2_weight": "haplogroup_weight",
        "AminoAcid": "amino_acid",
        "NewAminoAcid": "new_amino_acid",
        "AminoAcid_pos_protein": "amino_acid_position",
        "MutPred_Score": "mutpred_score",
        "mtDNA_Selection_Score": "mtdna_selection_score",
        "OXPHOS_complex": "oxphos_complex",
        "Helix_vaf_hom": "helix_af",
        "Helix_vaf_het": "helix_af_het",
        "Helix_count_hom": "helix_count_hom",
        "Helix_count_het": "helix_count_het",
        "NuMTs_dayama": "numts",
        "LowComplexityRegion": "low_complexity_region",
    }.items()
}


# The mutserve TSV's ID column holds the caller's internal sample label (literally
# "sample"), never a variant identifier. It must never be read as an rsid.
_MUTSERVE_IGNORED_COLUMNS = frozenset({"id", "filter"})


# mtDNA-Server's annotation (the PGT pipeline's mito/variants.annotated.txt) writes in its
# Phylotree17_haplogroups and Phylotree17_clades columns how many PhyloTree 17 haplogroups and
# clades carry the variant (4247 at 73G), not their names. A number there is kept as that count,
# never read as a haplogroup: it would become the sample's "haplogroup".
_MUTSERVE_COUNT_FIELDS = {"haplogroup": "haplogroup_count", "haplogroup_clades": "haplogroup_clade_count"}


def _mutserve_annotation_row(row: dict[str, str]) -> dict[str, Any]:
    annotation: dict[str, Any] = {}
    for column, raw_value in row.items():
        key = _normalize_header_key(column)
        if key in _MUTSERVE_IGNORED_COLUMNS:
            continue
        value = (raw_value or "").strip()
        if value in {"", "."}:
            continue
        field = _MUTSERVE_ANNOTATION_FIELDS.get(key, key)
        if field in _MUTSERVE_COUNT_FIELDS and value.isdigit():
            field = _MUTSERVE_COUNT_FIELDS[field]
        annotation[field] = value
    return annotation


def parse_mutserve_annotation_lines(lines: Any) -> VepAnnotationLookup:
    """Index a mutserve mtDNA annotation TSV by ``chrM`` variant id.

    This is a sibling of the VEP-TSV parser rather than a reuse of it: mutserve writes
    a bare (un-``#``-prefixed) header, has no ``Uploaded_variation``/``Location``
    columns to key on, and its ``ID`` column holds the caller's sample label, which the
    VEP parser's ``rsid`` alias list would otherwise store as every variant's rsid. The
    join key here is built from ``Pos``/``Ref``/``Variant`` against the chrM contig.
    """
    return _indexed_lookup(_index_mutserve_lines, lines)


def _index_mutserve_lines(lookup: VepAnnotationLookup, lines: Any) -> None:
    header: list[str] | None = None
    row_count = 0
    for raw_line in lines:
        line = raw_line.rstrip("\n\r")
        if not line or line.startswith("##"):
            continue
        values = line.split("\t")
        if header is None:
            header = [value.lstrip("#").strip() for value in values]
            continue
        row = {key: value for key, value in zip(header, values)}
        normalized = {_normalize_header_key(key): value for key, value in row.items()}
        position = field_int(normalized.get("pos"))
        ref = (normalized.get("ref") or "").strip()
        alt = (normalized.get("variant") or normalized.get("alt") or "").strip()
        if position is None or not ref or not alt:
            continue
        annotation = _mutserve_annotation_row(row)
        if not annotation:
            continue
        row_count += 1
        _store_vep_annotation(
            lookup,
            key_type="variant_id",
            key_value=build_small_variant_id("M", position, ref, alt),
            annotation=annotation,
        )
    lookup.row_count = row_count
    if lookup.conn is not None:
        lookup.conn.commit()


def parse_mutserve_annotation_path(path) -> VepAnnotationLookup | None:
    """Parse a mutserve annotation TSV from disk, or None when it holds no rows.

    A header-only file is the normal outcome for a run with nothing to annotate (the
    mitochondrial SV annotation in this pipeline is routinely empty), so it returns
    None rather than an empty lookup that would suppress the VCF's own annotations.
    """
    if path is None or not path.is_file():
        return None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lookup = parse_mutserve_annotation_lines(handle)
    if lookup.row_count == 0:
        lookup.close()
        return None
    return lookup
