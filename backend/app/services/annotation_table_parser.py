"""Annotation tables uploaded beside a small-variant VCF: VEP TSV and mutserve (#528).

Each is parsed into a :class:`VepAnnotationLookup`, a temporary SQLite index keyed by
variant id (and, for VEP, by its normalised Location + Allele), which the VCF loader
queries record by record. Split out of ``variant_upload_service``, which keeps the
upload plumbing that feeds these parsers their lines.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException

from .clickhouse_variant_ids import build_small_variant_id
from .data_scope import normalize_chromosome
from .family_package_common import _normalize_header_key
from .variant_annotation_parser import normalize_small_variant_annotation_entry
from .vcf_header_provenance import extract_vep_tab_provenance


def _coerce_int(value: Any) -> int | None:
    """Parse an integer, tolerating float-like text; None when unparseable/missing."""
    if value is None or str(value).strip() in {"", "."}:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None


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


def _parse_vep_tsv_annotation_lines(lines: Any) -> VepAnnotationLookup:
    return _indexed_lookup(_index_vep_tsv_lines, lines)


def _index_vep_tsv_lines(lookup: VepAnnotationLookup, lines: Any) -> None:
    header: list[str] | None = None
    row_count = 0
    # VEP states its tool/database versions in the leading ``## … version …`` block
    # (before "## Column descriptions:"); buffer it for provenance capture.
    provenance_header: list[str] = []

    for raw_line in lines:
        line = raw_line.rstrip("\n\r")
        if not line:
            continue
        if line.startswith("##"):
            if len(provenance_header) < 80:
                provenance_header.append(line)
            continue
        if line.startswith("#"):
            header = line.lstrip("#").split("\t")
            continue
        if header is None:
            continue
        values = line.split("\t")
        row = {key: value for key, value in zip(header, values)}
        annotation = normalize_small_variant_annotation_entry(row)
        if not annotation:
            continue
        row_count += 1

        uploaded = _parse_vep_uploaded_variation(row.get("Uploaded_variation", ""))
        allele = row.get("Allele") or None
        location = _parse_vep_location(row.get("Location", ""))

        if uploaded is not None:
            chrom, start, ref, alt = uploaded
            if ref and alt:
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
    lookup.provenance_modules = extract_vep_tab_provenance(provenance_header) or None
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


def _mutserve_annotation_row(row: dict[str, str]) -> dict[str, Any]:
    annotation: dict[str, Any] = {}
    for column, raw_value in row.items():
        key = _normalize_header_key(column)
        if key in _MUTSERVE_IGNORED_COLUMNS:
            continue
        value = (raw_value or "").strip()
        if value in {"", "."}:
            continue
        annotation[_MUTSERVE_ANNOTATION_FIELDS.get(key, key)] = value
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
        position = _coerce_int(normalized.get("pos"))
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
