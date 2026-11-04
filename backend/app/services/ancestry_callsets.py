"""What the family's stored callsets hold of each individual at the ancestry PCA's markers.

Exploratory and outside CoGA's intended purpose (TF-01 §4). The PCA reads the family's small
variants from ClickHouse at the reference markers only: a few thousand rows, read in well under
a second, never the VCF file again. Each individual is read from each callset that holds it
(``clair3`` direct calls, ``nipt`` per-sample calls, ``glimpse2`` imputed genotypes), and what
the callset covers is measured, not declared:

- At a marker a fully sequenced genome carries the ALT allele with the share the reference
  frequency predicts (about two in five markers). A callset's *coverage ratio* is the markers
  where the individual has a non-reference call over that expectation.
- **complete**: nearly every marker has an explicit genotype call; an absent marker is missing.
- **genome**: the ratio is as expected inside and outside the exome; an absent record is
  homozygous reference everywhere.
- **exome**: as expected inside the exome mask, far below it outside; an absent record is
  reference only inside the mask.
- **partial** (a gene panel, a subset of sites, a filtered imputation): below the expectation
  inside the exome too. Absence cannot be read there, so the callset is not projected.

An individual is projected from its best projectable callset: direct calls before NIPT
per-sample calls before imputed genotypes.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

import numpy as np

from ..ancestry_schemas import AncestryCallsetKind, AncestryCallsetScanOut, AncestrySampleOut
from .ancestry_projection import (
    MIN_CALL_RATE,
    MIN_MARKERS,
    AncestryReference,
    carries_alt,
    genotype_dosage,
    project_least_squares,
)
from .clickhouse_variant_storage import _execute as storage_execute, _small_table_name
from .nipt import is_cfdna_sample_metadata

CALLSET_LABELS = {"clair3": "Direct calls", "nipt": "NIPT per-sample calls", "glimpse2": "Imputed genotypes"}
# Which callset projects an individual when more than one can.
CALLSET_PREFERENCE = ("clair3", "nipt", "glimpse2")
# Imputation has no FILTER of its own: its calls are read whatever the column holds.
IMPUTED_CALLSETS = frozenset({"glimpse2"})
# A callset counts as covering a set of markers when it carries at least this share of the
# non-reference calls a sequenced genome has there.
MIN_COVERAGE_RATIO = 0.75
# A callset with an explicit genotype at this share of the markers needs no absence rule.
COMPLETE_SHARE = 0.95
# Markers asked for in one query; the query text stays well below ClickHouse's size limit.
_QUERY_CHUNK = 5000

EXCLUDED_CFDNA = "A maternal-plasma cfDNA sample is a mixture of mother and fetus, not one individual."
EXCLUDED_EMBRYO = "An embryo is left out: its genotypes come from a few amplified cells and are its parents' alleles."
NO_CALLSET = "No stored callset holds this individual's genotypes at the reference markers."
REASON_PARTIAL = ("The callset holds fewer non-reference calls than a sequenced genome or exome has at these "
                  "markers (a gene panel, a subset of sites or a filtered imputation): an absent marker "
                  "cannot be read as reference.")


@dataclass(frozen=True, slots=True)
class MarkerRow:
    """One stored small-variant row at a reference marker."""

    callset: str
    chrom: str
    pos: int
    ref: str
    alt: str
    filters: tuple[str, ...]
    samples: tuple[str, ...]
    gts: tuple[str, ...]
    call_filters: tuple[tuple[str, ...], ...]


@dataclass
class CallsetGenotypes:
    """One callset's reading of one individual: the scan shown, and the dosages it projects."""

    scan: AncestryCallsetScanOut
    dosages: Any = field(repr=False)
    usable: Any = field(repr=False)


def _xpos(marker_chrom: str, pos: int) -> int:
    # The reference's markers are autosomes, ranked by number in ClickHouse's xpos.
    return int(marker_chrom) * 1_000_000_000 + pos


async def read_marker_rows(family_uuid: str, project: str, reference: AncestryReference) -> list[MarkerRow]:
    """The family's stored small-variant rows at the reference markers, in a fixed order."""
    table = _small_table_name("GRCh38", "entries")
    positions = sorted(_xpos(m.chrom, m.pos) for m in reference.markers)
    rows: list[MarkerRow] = []
    for start in range(0, len(positions), _QUERY_CHUNK):
        # Fixed assembly and allowlisted table helper; every value is bound.
        result = await storage_execute(f"""
            SELECT source, chrom, pos, ref, alt, filters, `calls.sampleId`, `calls.gt`, `calls.filters`
            FROM {table}
            WHERE family_guid = %(family)s AND project_guid = %(project)s AND sign = 1
              AND source IN %(callsets)s AND xpos IN %(xpos)s
        """, {"family": family_uuid, "project": project, "callsets": tuple(CALLSET_PREFERENCE),
              "xpos": tuple(positions[start:start + _QUERY_CHUNK])})
        for source, chrom, pos, ref, alt, filters, samples, gts, call_filters in result or []:
            rows.append(MarkerRow(
                callset=str(source), chrom=str(chrom), pos=int(pos), ref=str(ref), alt=str(alt),
                filters=tuple(str(v) for v in filters or ()), samples=tuple(str(v) for v in samples or ()),
                gts=tuple(str(v) for v in gts or ()),
                call_filters=tuple(tuple(str(v) for v in values or ()) for values in call_filters or ())))
    rows.sort(key=lambda r: (r.callset, r.chrom, r.pos, r.ref, r.alt, r.samples, r.gts))
    return rows


def rows_digest(rows: Iterable[MarkerRow]) -> str:
    """A digest of the rows a calculation read: the same rows read again give the same digest."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps([row.callset, row.chrom, row.pos, row.ref, row.alt, row.filters,
                                  row.samples, row.gts, row.call_filters], separators=(",", ":")).encode())
        digest.update(b"\n")
    return "ancestry-rows-v1:" + digest.hexdigest()


def _filter_state(row: MarkerRow, index: int) -> str:
    """``pass``, ``unfiltered`` (FILTER ``.``) or ``filtered`` for one call of a row.

    A row merged from one-sample VCFs carries each sample's own FILTER with its call; a row of a
    multi-sample VCF carries the record's FILTER, which is every sample's.
    """
    per_sample = any(row.call_filters)
    values = row.call_filters[index] if per_sample and index < len(row.call_filters) else row.filters
    if not values:
        return "unfiltered"
    return "pass" if all(value == "PASS" for value in values) else "filtered"


def excluded_reason(sample_row: dict[str, Any]) -> str | None:
    """Why an individual of the family is not projected at all, or None."""
    if is_cfdna_sample_metadata(sample_row.get("sample_metadata")):
        return EXCLUDED_CFDNA
    if str(sample_row.get("role") or "").lower() == "embryo":
        return EXCLUDED_EMBRYO
    return None


def _classify(reference: AncestryReference, carrier: Any, explicit: Any) -> tuple[AncestryCallsetKind, Any, dict[str, float | None]]:
    """The callset's kind, the markers an absent record counts as reference at, and its ratios."""
    expected = reference.expected_carrier
    n = len(reference.markers)
    ratios: dict[str, float | None] = {
        "coverage_ratio": float(carrier.sum() / expected.sum()), "exome_ratio": None, "outside_exome_ratio": None}
    mask = reference.exome.covered if reference.exome is not None else None
    if mask is not None:
        ratios["exome_ratio"] = float(carrier[mask].sum() / expected[mask].sum())
        ratios["outside_exome_ratio"] = float(carrier[~mask].sum() / expected[~mask].sum())
    if explicit.sum() >= COMPLETE_SHARE * n:
        return "complete", np.zeros(n, dtype=bool), ratios
    if mask is None:
        if ratios["coverage_ratio"] >= MIN_COVERAGE_RATIO:
            return "genome", np.ones(n, dtype=bool), ratios
        return "partial", np.zeros(n, dtype=bool), ratios
    exome_ratio = ratios["exome_ratio"] or 0.0
    outside_ratio = ratios["outside_exome_ratio"] or 0.0
    if exome_ratio >= MIN_COVERAGE_RATIO and outside_ratio >= MIN_COVERAGE_RATIO:
        return "genome", np.ones(n, dtype=bool), ratios
    if exome_ratio >= MIN_COVERAGE_RATIO:
        return "exome", mask.copy(), ratios
    return "partial", np.zeros(n, dtype=bool), ratios


def read_callsets(rows: list[MarkerRow], reference: AncestryReference,
                  samples: list[str]) -> dict[str, dict[str, CallsetGenotypes]]:
    """Each individual's reading from each callset that holds it, by sample and callset."""
    n = len(reference.markers)
    index = {(m.chrom, m.pos): i for i, m in enumerate(reference.markers)}
    wanted = set(samples)
    # (callset, sample) -> per-marker status, dosage, carrier and explicit-call arrays.
    status: dict[tuple[str, str], np.ndarray] = {}
    dosage: dict[tuple[str, str], np.ndarray] = {}
    carrier: dict[tuple[str, str], np.ndarray] = {}
    explicit: dict[tuple[str, str], np.ndarray] = {}
    for row in rows:
        at = index.get((row.chrom, row.pos))
        if at is None:
            continue
        marker = reference.markers[at]
        for i, (sample, gt) in enumerate(zip(row.samples, row.gts)):
            if sample not in wanted:
                continue
            key = (row.callset, sample)
            if key not in status:
                status[key] = np.full(n, "absent", dtype=object)
                dosage[key] = np.zeros(n)
                carrier[key] = np.zeros(n, dtype=bool)
                explicit[key] = np.zeros(n, dtype=bool)
            carrier[key][at] |= carries_alt(gt)
            explicit[key][at] |= "." not in gt and bool(gt)
            if status[key][at] != "absent":
                # Two rows of one callset at one position: which one is the individual's call
                # cannot be told, so the marker is left out.
                status[key][at] = "ambiguous_record"
                continue
            state, alt_count = genotype_dosage(gt, row.ref, row.alt, marker)
            if state == "called":
                filter_state = _filter_state(row, i)
                state = {"pass": "called", "unfiltered": "called_unfiltered", "filtered": "filtered"}[filter_state]
            status[key][at] = state
            dosage[key][at] = alt_count
    readings: dict[str, dict[str, CallsetGenotypes]] = {}
    for (callset, sample), marks in status.items():
        counts = Counter(marks.tolist())
        # Unfiltered calls are taken from imputed genotypes, and from a callset that filtered
        # none of its calls; a callset with PASS calls keeps its unfiltered ones out.
        accepts_unfiltered = callset in IMPUTED_CALLSETS or (counts["called"] == 0 and counts["called_unfiltered"] > 0)
        kind, covered, ratios = _classify(reference, carrier[(callset, sample)], explicit[(callset, sample)])
        called = (marks == "called") | ((marks == "called_unfiltered") & accepts_unfiltered)
        assumed = (marks == "absent") & covered
        usable = called | assumed
        covered_count = n if kind in {"complete", "genome"} else int(covered.sum())
        qc = {"called": int((marks == "called").sum()),
              "called_unfiltered": int(((marks == "called_unfiltered") & accepts_unfiltered).sum()),
              "assumed_ref": int(assumed.sum()),
              "unfiltered": 0 if accepts_unfiltered else int((marks == "called_unfiltered").sum()),
              "outside_covered": int(((marks == "absent") & ~covered).sum())}
        for state in ("filtered", "no_call", "allele_mismatch", "invalid_gt", "ambiguous_record"):
            qc[state] = counts[state]
        usable_count = int(usable.sum())
        call_rate = usable_count / covered_count if covered_count else 0.0
        reason = None
        if kind == "partial":
            reason = REASON_PARTIAL
        elif usable_count < MIN_MARKERS:
            reason = f"Too few usable markers: {usable_count:,} of at least {MIN_MARKERS:,}."
        elif call_rate < MIN_CALL_RATE:
            reason = (f"Too few of the markers the callset covers have a usable genotype: "
                      f"{call_rate:.0%}, at least {MIN_CALL_RATE:.0%}.")
        scan = AncestryCallsetScanOut(
            callset=callset, label=CALLSET_LABELS.get(callset, callset), kind=kind,
            carriers=int(carrier[(callset, sample)].sum()),
            expected_carriers=round(float(reference.expected_carrier.sum()), 1),
            coverage_ratio=round(float(ratios["coverage_ratio"] or 0.0), 3),
            exome_ratio=None if ratios["exome_ratio"] is None else round(ratios["exome_ratio"], 3),
            outside_exome_ratio=None if ratios["outside_exome_ratio"] is None else round(ratios["outside_exome_ratio"], 3),
            explicit_calls=int(explicit[(callset, sample)].sum()), accepts_unfiltered=accepts_unfiltered,
            covered_markers=covered_count, usable_markers=usable_count, call_rate=round(call_rate, 4), qc=qc,
            projectable=reason is None, reason=reason)
        readings.setdefault(sample, {})[callset] = CallsetGenotypes(scan=scan, dosages=dosage[(callset, sample)],
                                                                     usable=usable)
    return readings


def _preference(callset: str) -> int:
    return CALLSET_PREFERENCE.index(callset) if callset in CALLSET_PREFERENCE else len(CALLSET_PREFERENCE)


def project_family(rows: list[MarkerRow], reference: AncestryReference,
                   sample_rows: list[dict[str, Any]]) -> list[AncestrySampleOut]:
    """Each individual of the family projected from its best callset, or why it is not."""
    samples = [str(row["sample_id"]) for row in sample_rows]
    excluded = {str(row["sample_id"]): excluded_reason(row) for row in sample_rows}
    readings = read_callsets(rows, reference, [s for s in samples if excluded[s] is None])
    results = []
    for sample in samples:
        if excluded[sample] is not None:
            results.append(AncestrySampleOut(sample_id=sample, status="excluded", reason=excluded[sample]))
            continue
        options = sorted(readings.get(sample, {}).values(),
                         key=lambda r: (not r.scan.projectable, _preference(r.scan.callset), -r.scan.usable_markers))
        candidates = sorted((r.scan for r in options), key=lambda s: _preference(s.callset))
        if not options:
            results.append(AncestrySampleOut(sample_id=sample, status="insufficient_data", reason=NO_CALLSET))
            continue
        best = options[0]
        pcs = project_least_squares(reference, best.dosages, best.usable) if best.scan.projectable else None
        reason = best.scan.reason if best.scan.projectable is False else (
            None if pcs is not None else "The usable markers cannot determine the four components.")
        results.append(AncestrySampleOut(
            sample_id=sample, status="projected" if pcs is not None else "insufficient_data", pcs=pcs,
            call_rate=best.scan.call_rate, qc=best.scan.qc, callset=best.scan.callset, kind=best.scan.kind,
            usable_markers=best.scan.usable_markers, reason=reason, candidates=candidates))
    return results
