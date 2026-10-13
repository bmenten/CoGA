"""Per-family SV→gene index (SNV + SV compound het, Phase 0).

Stores which genes a family's structural variants hit, so the small-variant workspace can
flag genes that also carry an SV (the cross-type "second hit"). This module owns only the
Postgres rows + the badge summary; the ClickHouse scan that populates it lives in
``clickhouse_family_variants`` (which holds the SV helpers). See docs/snv-sv-compound-het.md.

The index records the storage-level SV data version it was built from; a family whose SVs
have changed since (any insert, delete or restore) gets it rebuilt on the next read.

Each SV that hits the gene gets its own cis/trans verdict against the small variant: from
the reads where the two calls share a phase set, else from the family, each by the same
rule as the SNV + SNV compound het (``compound_het_phase``). The badge's verdict is drawn
from theirs, and a deletion in trans wins (``summarize_second_hit``).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .compound_het_phase import (
    CARRIER,
    NON_CARRIER,
    PHASE_CIS,
    PHASE_EVIDENCE_READ,
    PHASE_EVIDENCE_SEGREGATION,
    PHASE_TRANS,
    PHASE_UNKNOWN,
    UNKNOWN_CARRIAGE,
    Carriage,
    FamilyPedigree,
    Locus,
    Phase,
    phased_alt_haplotype,
    segregation_phase,
    small_variant_carriage,
)
from .genotypes import HET, HOM_ALT, HOM_REF, classify_genotype, genotype_has_alt
from .sv_copy_change import COPY_LOSS, copy_change, sv_kind

# How far apart two SV calls of one type may lie and still be read as one SV, when a
# relative's own call stands in for an SV whose record holds no call for them
# (``_sv_carriage``): a caller run on each sample apart places the same SV's breakpoints a
# little differently in each. 1 kb is a distance commonly used to merge SV calls across
# samples.
SAME_SV_MAX_DISTANCE = 1_000


def _gt_has_alt(gt: str | None) -> bool:
    return genotype_has_alt(str(gt or ""))


def _sv_kind(sv: dict[str, Any]) -> str:
    """The SV's type without its subtypes, in capitals: ``DEL:ME`` is ``DEL``."""
    return sv_kind(sv.get("sv_type"))


def _sv_span(sv: dict[str, Any]) -> tuple[int, int] | None:
    """The SV's start and end, the lower first; None when either is missing."""
    start, end = sv.get("start"), sv.get("end")
    if start is None or end is None:
        return None
    try:
        first, last = int(start), int(end)
    except (TypeError, ValueError):
        return None
    return (first, last) if first <= last else (last, first)


def _same_sv(sv: dict[str, Any], other: dict[str, Any]) -> bool:
    """Whether ``other`` may be ``sv`` as called in another sample's own file: the same type
    (``_sv_kind``) on the same chromosome, overlapping it or at most
    ``SAME_SV_MAX_DISTANCE`` away."""
    if _sv_kind(sv) != _sv_kind(other):
        return False
    if str(sv.get("chr") or "") != str(other.get("chr") or ""):
        return False
    span, other_span = _sv_span(sv), _sv_span(other)
    if span is None or other_span is None:
        return False
    return max(span[0], other_span[0]) - min(span[1], other_span[1]) <= SAME_SV_MAX_DISTANCE


def _copy_loss(sv: dict[str, Any], pedigree: FamilyPedigree | None) -> bool:
    """Whether the SV removes a copy of what it spans, by the rule the SV ranking reads too
    (``sv_copy_change.copy_change``, CLIN-47): a deletion of any subtype (``DEL:ME``; a
    ``LOSS`` or ``CNV_LOSS`` likewise), or a CNV unless the copy numbers stored for its
    carriers (``calls.cn``) show none lost (two copies, or one for a male on chrX or chrY
    outside the PARs). A CNV that stores no copy number for its carriers may be a loss, and
    counts as one. A duplication, insertion, inversion or breakend removes no copy."""
    calls = sv.get("gt") or {}
    copy_numbers = sv.get("cn") or {}
    change = copy_change(
        sv.get("sv_type"),
        [(sample, genotype, copy_numbers.get(sample)) for sample, genotype in calls.items()],
        chrom=sv.get("chr"),
        start=sv.get("start"),
        end=sv.get("end"),
        pedigree=pedigree,
    )
    return change == COPY_LOSS


def _affected_zygosity(svs: Sequence[dict[str, Any]], affected: set[str]) -> str | None:
    """The zygosity of these SVs in the affected samples: ``het``, ``hom``, ``mixed``, or
    None when no affected sample carries one of them."""
    zygosities: set[str] = set()
    for sv in svs:
        calls = sv.get("gt") or {}
        for sample in affected:
            genotype_class = classify_genotype(str(calls.get(sample, "")))
            if genotype_class == HOM_ALT:
                zygosities.add("hom")
            elif genotype_class == HET:
                zygosities.add("het")
    if "hom" in zygosities and "het" in zygosities:
        return "mixed"
    if "hom" in zygosities:
        return "hom"
    if "het" in zygosities:
        return "het"
    return None


def _read_phase_verdict(
    sv: dict[str, Any],
    affected: set[str],
    snv_gt_by_sample: dict[str, str] | None,
    snv_ps_by_sample: dict[str, int] | None,
) -> Phase | None:
    """Read-based cis/trans of one SV: where the SNV and the SV share a phase set in an
    affected sample, compare the haplotype each alt sits on. None when no phased pair is
    comparable.

    Both calls are read by ``phased_alt_haplotype``, as the SNV + SNV pairs read theirs: a
    half call such as ``.|1`` places nothing, so the family decides."""
    if not snv_gt_by_sample or not snv_ps_by_sample:
        return None
    calls = sv.get("gt") or {}
    phase_sets = sv.get("ps") or {}
    verdicts: set[Phase] = set()
    for sample in affected:
        snv_index = phased_alt_haplotype(snv_gt_by_sample.get(sample))
        snv_ps = snv_ps_by_sample.get(sample)
        if snv_index is None or snv_ps is None:
            continue
        sv_index = phased_alt_haplotype(calls.get(sample))
        sv_ps = phase_sets.get(sample)
        if sv_index is None or sv_ps is None or int(sv_ps) != int(snv_ps):
            continue
        verdicts.add(PHASE_CIS if sv_index == snv_index else PHASE_TRANS)
    if not verdicts:
        return None
    # This SV in cis in any affected sample leaves that sample an intact copy: it is not
    # the second hit they share.
    return PHASE_CIS if PHASE_CIS in verdicts else PHASE_TRANS


def _sv_carriage(
    sv: dict[str, Any], gene_svs: Sequence[dict[str, Any]], sample: str
) -> Carriage:
    """A sample's carriage of one SV.

    A carrier has an ALT call in it, a non-carrier a genotyped reference call: the SV was
    looked for in that sample and not found. A no-call says neither. Nor does a record
    that holds no call for the sample at all: an SV file of one sample (a per-sample
    upload, a per-sample caller) holds none for the others, so it never shows that a
    relative lacks the SV. Such a sample carries it when their own file holds it: an ALT
    call of theirs in an SV of the gene that may be this one (``_same_sv``). Another SV of
    theirs, such as a duplication beside the deletion or a deletion more than
    ``SAME_SV_MAX_DISTANCE`` away, is not this one.
    """
    calls = sv.get("gt") or {}
    if sample in calls:
        genotype = calls.get(sample)
        if _gt_has_alt(genotype):
            return CARRIER
        if classify_genotype(str(genotype or "")) == HOM_REF:
            return NON_CARRIER
        return UNKNOWN_CARRIAGE
    if any(
        other is not sv
        and _gt_has_alt((other.get("gt") or {}).get(sample))
        and _same_sv(sv, other)
        for other in gene_svs
    ):
        return CARRIER
    return UNKNOWN_CARRIAGE


def _phase_verdict(
    sv: dict[str, Any],
    gene_svs: Sequence[dict[str, Any]],
    affected: set[str],
    unaffected: set[str],
    snv_gt_by_sample: dict[str, str] | None,
    *,
    snv_dp_by_sample: dict[str, int] | None = None,
    pedigree: FamilyPedigree | None = None,
    snv_locus: Locus | None = None,
) -> Phase:
    """trans / cis / unknown of one SV, from the family — the rule the SNV+SNV compound het
    uses too.

    A candidate needs the SNV heterozygous in every affected sample and this SV in every
    affected sample (``_sv_carriage``). It is then ``cis`` when an unaffected individual
    carries both, and otherwise traced through the affected samples' parents
    (``compound_het_phase.segregation_phase``). Unaffected relatives who carry neither hit
    are not evidence of trans, and the gene's other SVs are not evidence about this one.
    """
    if snv_gt_by_sample is None or not affected:
        return PHASE_UNKNOWN

    snv_het_in_affected = all(
        classify_genotype(str(snv_gt_by_sample.get(sample, ""))) == HET for sample in affected
    )
    sv_in_affected = all(_sv_carriage(sv, gene_svs, sample) == CARRIER for sample in affected)
    if not (snv_het_in_affected and sv_in_affected):
        return PHASE_UNKNOWN

    snv_dp = snv_dp_by_sample or {}
    loci: list[Locus] = [snv_locus] if snv_locus is not None else []
    loci.extend([(sv.get("chr"), sv.get("start")), (sv.get("chr"), sv.get("end"))])
    return segregation_phase(
        affected=affected,
        unaffected=unaffected,
        first=lambda sample: small_variant_carriage(
            snv_gt_by_sample.get(sample), snv_dp.get(sample)
        ),
        second=lambda sample: _sv_carriage(sv, gene_svs, sample),
        pedigree=pedigree,
        loci=loci,
    )


async def is_index_current(
    session: AsyncSession, family_uuid: str, sv_data_version: str | None
) -> bool:
    """True when the family's index exists and was built from ``sv_data_version``."""
    row = (
        await session.execute(
            text(
                "SELECT sv_data_version FROM family_sv_gene_index_status "
                "WHERE family_id = CAST(:fid AS uuid)"
            ),
            {"fid": family_uuid},
        )
    ).first()
    return row is not None and row[0] == sv_data_version


async def store_sv_gene_index(
    session: AsyncSession,
    *,
    family_uuid: str,
    gene_map: dict[str, list[dict[str, Any]]],
    sv_total: int,
    sv_data_version: str | None = None,
) -> None:
    """Replace the family's index with ``gene -> [svs]``, stamped with the SV data version
    it was built from.

    Two page loads can find the index out of date at once; the transaction lock makes the
    second rebuild wait for the first and replace it, instead of colliding on its rows.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"sv_gene_index:{family_uuid}"},
    )
    await session.execute(
        text("DELETE FROM family_sv_gene_index WHERE family_id = CAST(:fid AS uuid)"),
        {"fid": family_uuid},
    )
    if gene_map:
        await session.execute(
            text(
                """
                INSERT INTO family_sv_gene_index (family_id, gene_symbol, sv_count, svs)
                VALUES (CAST(:fid AS uuid), :gene_symbol, :sv_count, CAST(:svs AS jsonb))
                """
            ),
            [
                {
                    "fid": family_uuid,
                    "gene_symbol": gene.upper(),
                    "sv_count": len(svs),
                    "svs": json.dumps(svs),
                }
                for gene, svs in gene_map.items()
            ],
        )
    await session.execute(
        text(
            """
            INSERT INTO family_sv_gene_index_status
                (family_id, sv_total, gene_count, sv_data_version, computed_at)
            VALUES (CAST(:fid AS uuid), :sv_total, :gene_count, :sv_data_version, now())
            ON CONFLICT (family_id) DO UPDATE SET
                sv_total = EXCLUDED.sv_total,
                gene_count = EXCLUDED.gene_count,
                sv_data_version = EXCLUDED.sv_data_version,
                computed_at = now()
            """
        ),
        {
            "fid": family_uuid,
            "sv_total": sv_total,
            "gene_count": len(gene_map),
            "sv_data_version": sv_data_version,
        },
    )
    await session.commit()


async def get_sv_second_hits(
    session: AsyncSession, *, family_uuid: str, gene_symbols: set[str]
) -> dict[str, dict[str, Any]]:
    """Return ``{GENE -> {sv_count, svs}}`` for the requested genes that carry an SV."""
    if not gene_symbols:
        return {}
    upper_genes = sorted({gene.upper() for gene in gene_symbols if gene})
    if not upper_genes:
        return {}
    # The genes go as one text[] parameter, not one parameter per gene (an expanding IN):
    # a CSV export's 50,001 rows can hit more genes than the 32,767 parameters asyncpg
    # takes in one statement (DATA-3).
    rows = (
        await session.execute(
            text(
                """
                SELECT gene_symbol, sv_count, svs
                FROM family_sv_gene_index
                WHERE family_id = CAST(:fid AS uuid)
                  AND gene_symbol = ANY(CAST(:genes AS text[]))
                """
            ),
            {"fid": family_uuid, "genes": upper_genes},
        )
    ).mappings().all()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        svs = row["svs"] if isinstance(row["svs"], list) else json.loads(row["svs"] or "[]")
        result[row["gene_symbol"]] = {"sv_count": row["sv_count"], "svs": svs}
    return result


async def get_sv_hit_genes(session: AsyncSession, *, family_uuid: str) -> list[str]:
    """All genes the family's SVs hit (drives the ``require_sv_second_hit`` filter)."""
    rows: Sequence[Any] = (
        await session.execute(
            text(
                "SELECT gene_symbol FROM family_sv_gene_index WHERE family_id = CAST(:fid AS uuid)"
            ),
            {"fid": family_uuid},
        )
    ).scalars().all()
    return [str(row) for row in rows]


def _listing_key(sv: dict[str, Any]) -> tuple[str, int, int, str, str]:
    """Where an SV sorts in the badge's list: by position, then type and id."""
    start, end = _sv_span(sv) or (0, 0)
    return (str(sv.get("chr") or ""), start, end, _sv_kind(sv), str(sv.get("sv_id") or ""))


def _sv_second_hit(
    sv: dict[str, Any],
    gene_svs: Sequence[dict[str, Any]],
    affected: set[str],
    unaffected: set[str],
    *,
    snv_gt_by_sample: dict[str, str] | None,
    snv_ps_by_sample: dict[str, int] | None,
    snv_dp_by_sample: dict[str, int] | None,
    pedigree: FamilyPedigree | None,
    snv_locus: Locus | None,
) -> dict[str, Any]:
    """One SV of the gene with its own phase against the SNV.

    Only an SV an affected sample carries can be their second hit, so only such an SV gets
    a phase: from the reads where it shares a phase set with the SNV, else from the
    family."""
    zygosity = _affected_zygosity([sv], affected)
    phase: Phase = PHASE_UNKNOWN
    evidence: str | None = None
    if zygosity is not None:
        read_phase = _read_phase_verdict(sv, affected, snv_gt_by_sample, snv_ps_by_sample)
        if read_phase is not None:
            phase, evidence = read_phase, PHASE_EVIDENCE_READ
        else:
            phase = _phase_verdict(
                sv,
                gene_svs,
                affected,
                unaffected,
                snv_gt_by_sample,
                snv_dp_by_sample=snv_dp_by_sample,
                pedigree=pedigree,
                snv_locus=snv_locus,
            )
            if phase in (PHASE_TRANS, PHASE_CIS):
                evidence = PHASE_EVIDENCE_SEGREGATION
    span = _sv_span(sv)
    return {
        "sv_id": str(sv.get("sv_id") or ""),
        "sv_type": str(sv.get("sv_type") or "SV").upper(),
        "chr": str(sv.get("chr") or "") or None,
        "start": span[0] if span else None,
        "end": span[1] if span else None,
        "affected_zygosity": zygosity,
        "copy_loss": _copy_loss(sv, pedigree),
        "phase": phase,
        "phase_evidence": evidence,
    }


def _strongest_evidence(entries: Sequence[dict[str, Any]]) -> str:
    """Read phasing when any of these verdicts rests on it, else segregation."""
    if any(entry["phase_evidence"] == PHASE_EVIDENCE_READ for entry in entries):
        return PHASE_EVIDENCE_READ
    return PHASE_EVIDENCE_SEGREGATION


def summarize_second_hit(
    svs: list[dict[str, Any]],
    affected_samples: list[str],
    *,
    unaffected_samples: list[str] | None = None,
    snv_gt_by_sample: dict[str, str] | None = None,
    snv_ps_by_sample: dict[str, int] | None = None,
    snv_dp_by_sample: dict[str, int] | None = None,
    pedigree: FamilyPedigree | None = None,
    snv_locus: Locus | None = None,
) -> dict[str, Any]:
    """Compact badge summary: which SV types hit the gene, the zygosity in affected
    individuals, each SV with its own trans/cis verdict (``svs``), and the gene's verdict
    drawn from theirs.

    Each SV is phased on its own (``_sv_second_hit``): from the reads when it shares a
    phase set with the SNV, else from the family (``pedigree``: whose parent is whom;
    ``snv_dp_by_sample``: the depth behind a parent's reference call; ``snv_locus``: where
    a male carries one copy). Of the SVs the affected samples carry, the gene's verdict is

    * a copy loss (``_copy_loss``) in trans with the heterozygous SNV, whatever the other
      SVs say: it removes the other copy, so the pair is effectively biallelic
      (``deletion_unmasked``), and the phase is trans;
    * otherwise trans when any of them is in trans: a compound-heterozygous candidate;
    * cis only when every one of them is in cis;
    * unknown otherwise.

    A cis verdict of one SV used to stand for all of them, so a duplication in cis hid a
    deletion in trans with the SNV (CLIN-28). The evidence is read phasing when a verdict
    behind the gene's rests on it; a cis is read-backed only when every one is."""
    sv_types = sorted({str(sv.get("sv_type") or "SV").upper() for sv in svs})
    affected = set(affected_samples or [])
    unaffected = set(unaffected_samples or [])
    entries = [
        _sv_second_hit(
            sv,
            svs,
            affected,
            unaffected,
            snv_gt_by_sample=snv_gt_by_sample,
            snv_ps_by_sample=snv_ps_by_sample,
            snv_dp_by_sample=snv_dp_by_sample,
            pedigree=pedigree,
            snv_locus=snv_locus,
        )
        for sv in sorted(svs, key=_listing_key)
    ]
    carried = [entry for entry in entries if entry["affected_zygosity"] is not None]
    unmasking = [
        entry for entry in carried if entry["copy_loss"] and entry["phase"] == PHASE_TRANS
    ]
    in_trans = [entry for entry in carried if entry["phase"] == PHASE_TRANS]
    phase: Phase = PHASE_UNKNOWN
    phase_evidence: str | None = None
    if in_trans:
        phase, phase_evidence = PHASE_TRANS, _strongest_evidence(unmasking or in_trans)
    elif carried and all(entry["phase"] == PHASE_CIS for entry in carried):
        phase = PHASE_CIS
        phase_evidence = (
            PHASE_EVIDENCE_READ
            if all(entry["phase_evidence"] == PHASE_EVIDENCE_READ for entry in carried)
            else PHASE_EVIDENCE_SEGREGATION
        )

    locus_chr, locus_start, locus_end = _bounding_locus(svs)
    return {
        "sv_count": len(svs),
        "sv_types": sv_types,
        "affected_zygosity": _affected_zygosity(svs, affected),
        "has_deletion": any(entry["copy_loss"] for entry in entries),
        "phase": phase,
        "phase_evidence": phase_evidence,
        "deletion_unmasked": bool(unmasking),
        "chr": locus_chr,
        "start": locus_start,
        "end": locus_end,
        "svs": entries,
    }


def _bounding_locus(svs: list[dict[str, Any]]) -> tuple[str | None, int | None, int | None]:
    """Span covering these SVs, so a link can go to them rather than to their gene.

    Gene symbol and gene coordinates disagree often enough that filtering by gene sends
    the analyst to an empty page: SV annotation includes flanking genes, while the SV
    search requires a real overlap with a stored transcript. A locus is not open to that
    disagreement.

    SVs on one gene share a chromosome; if a symbol somehow spans several, bound only the
    most-represented one rather than inventing a span across chromosomes.
    """
    by_chrom: dict[str, list[tuple[int, int]]] = {}
    for sv in svs:
        chrom = str(sv.get("chr") or "").strip()
        start = sv.get("start")
        end = sv.get("end")
        if not chrom or start is None or end is None:
            continue
        try:
            start_pos, end_pos = int(start), int(end)
        except (TypeError, ValueError):
            continue
        if end_pos < start_pos:
            start_pos, end_pos = end_pos, start_pos
        by_chrom.setdefault(chrom, []).append((start_pos, end_pos))
    if not by_chrom:
        return None, None, None
    chrom = max(by_chrom, key=lambda key: len(by_chrom[key]))
    spans = by_chrom[chrom]
    return chrom, min(span[0] for span in spans), max(span[1] for span in spans)


async def clear_family_sv_gene_index(session: AsyncSession, family_uuid: str) -> None:
    await session.execute(
        text("DELETE FROM family_sv_gene_index WHERE family_id = CAST(:fid AS uuid)"),
        {"fid": family_uuid},
    )
    await session.execute(
        text("DELETE FROM family_sv_gene_index_status WHERE family_id = CAST(:fid AS uuid)"),
        {"fid": family_uuid},
    )
    await session.commit()
