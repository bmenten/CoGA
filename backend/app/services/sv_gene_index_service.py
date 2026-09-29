"""Per-family SV→gene index (SNV + SV compound het, Phase 0).

Stores which genes a family's structural variants hit, so the small-variant workspace can
flag genes that also carry an SV (the cross-type "second hit"). This module owns only the
Postgres rows + the badge summary; the ClickHouse scan that populates it lives in
``clickhouse_family_variants`` (which holds the SV helpers). See docs/snv-sv-compound-het.md.

The index records the storage-level SV data version it was built from; a family whose SVs
have changed since (any insert, delete or restore) gets it rebuilt on the next read. The
badge's cis/trans verdict comes from the reads where the calls share a phase set, else
from the family by the same rule as the SNV + SNV compound het (``compound_het_phase``).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, text
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
    segregation_phase,
    small_variant_carriage,
)
from .genotypes import HET, HOM_ALT, HOM_REF, classify_genotype, genotype_has_alt


def _gt_has_alt(gt: str | None) -> bool:
    return genotype_has_alt(str(gt or ""))


def _phased_alt_index(gt: str | None) -> int | None:
    """Haplotype index (0/1) carrying the alt allele of a phased heterozygous GT (``a|b``).

    Returns None unless the GT is phased and a clean het (exactly one alt allele)."""
    text_gt = str(gt or "").strip()
    if "|" not in text_gt:
        return None
    alleles = text_gt.split("|")
    if len(alleles) != 2:
        return None
    alt_positions = [index for index, allele in enumerate(alleles) if allele not in {"0", ".", ""}]
    return alt_positions[0] if len(alt_positions) == 1 else None


def _read_phase_verdict(
    svs: list[dict[str, Any]],
    affected: set[str],
    snv_gt_by_sample: dict[str, str] | None,
    snv_ps_by_sample: dict[str, int] | None,
) -> str | None:
    """Read-based cis/trans: when the SNV and an SV share a phase set in an affected sample,
    compare the haplotype each alt sits on. None when no phased pair is comparable."""
    if not snv_gt_by_sample or not snv_ps_by_sample:
        return None
    verdicts: set[str] = set()
    for sample in affected:
        snv_index = _phased_alt_index(snv_gt_by_sample.get(sample))
        snv_ps = snv_ps_by_sample.get(sample)
        if snv_index is None or snv_ps is None:
            continue
        for sv in svs:
            sv_index = _phased_alt_index((sv.get("gt") or {}).get(sample))
            sv_ps = (sv.get("ps") or {}).get(sample)
            if sv_index is None or sv_ps is None or int(sv_ps) != int(snv_ps):
                continue
            verdicts.add("cis" if sv_index == snv_index else "trans")
    if not verdicts:
        return None
    # A demonstrated cis in any affected sample rules out the biallelic mechanism.
    return "cis" if "cis" in verdicts else "trans"


def _sv_carriage(svs: list[dict[str, Any]], sample: str, affected: set[str]) -> Carriage:
    """A sample's carriage of the SV hit on this gene.

    A carrier has an ALT call in any of the gene's SVs. A non-carrier has none, and a
    genotyped reference call in every SV an affected sample carries: the SV was looked
    for in that sample and not found. Anything less, notably a callset with no call for
    the sample at all (per-sample SV files), is not evidence of absence.
    """
    if any(_gt_has_alt((sv.get("gt") or {}).get(sample)) for sv in svs):
        return CARRIER
    carried = [
        sv
        for sv in svs
        if any(_gt_has_alt((sv.get("gt") or {}).get(member)) for member in affected)
    ]
    if carried and all(
        classify_genotype(str((sv.get("gt") or {}).get(sample) or "")) == HOM_REF
        for sv in carried
    ):
        return NON_CARRIER
    return UNKNOWN_CARRIAGE


def _phase_verdict(
    svs: list[dict[str, Any]],
    affected: set[str],
    unaffected: set[str],
    snv_gt_by_sample: dict[str, str] | None,
    *,
    snv_dp_by_sample: dict[str, int] | None = None,
    pedigree: FamilyPedigree | None = None,
    snv_locus: Locus | None = None,
) -> str:
    """trans / cis / unknown from the family — the rule the SNV+SNV compound het uses too.

    A candidate needs the SNV heterozygous in every affected sample and the SV present in
    every affected sample. It is then ``cis`` when an unaffected individual carries both
    hits, and otherwise traced through the affected samples' parents
    (``compound_het_phase.segregation_phase``). Unaffected relatives who carry neither hit
    are not evidence of trans.
    """
    if snv_gt_by_sample is None or not affected:
        return PHASE_UNKNOWN

    snv_het_in_affected = all(
        classify_genotype(str(snv_gt_by_sample.get(sample, ""))) == HET for sample in affected
    )
    sv_in_affected = all(
        any(_gt_has_alt((sv.get("gt") or {}).get(sample)) for sv in svs) for sample in affected
    )
    if not (snv_het_in_affected and sv_in_affected):
        return PHASE_UNKNOWN

    snv_dp = snv_dp_by_sample or {}
    loci: list[Locus] = [snv_locus] if snv_locus is not None else []
    for sv in svs:
        loci.extend([(sv.get("chr"), sv.get("start")), (sv.get("chr"), sv.get("end"))])
    return segregation_phase(
        affected=affected,
        unaffected=unaffected,
        first=lambda sample: small_variant_carriage(
            snv_gt_by_sample.get(sample), snv_dp.get(sample)
        ),
        second=lambda sample: _sv_carriage(svs, sample, affected),
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
    rows = (
        await session.execute(
            text(
                """
                SELECT gene_symbol, sv_count, svs
                FROM family_sv_gene_index
                WHERE family_id = CAST(:fid AS uuid) AND gene_symbol IN :genes
                """
            ).bindparams(bindparam("genes", expanding=True)),
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
    individuals, and — when the SNV genotype is supplied — the trans/cis phase verdict.

    The phase is read from the reads when the SNV and an SV share a phase set, else from
    the family (``pedigree``: whose parent is whom; ``snv_dp_by_sample``: the depth behind
    a parent's reference call; ``snv_locus``: where a male carries one copy).

    The headline is a deletion in trans with a heterozygous SNV: the deletion removes the
    other allele, so the pair is effectively biallelic (``deletion_unmasked``)."""
    sv_types = sorted({str(sv.get("sv_type") or "SV").upper() for sv in svs})
    affected = set(affected_samples or [])
    zygosities: set[str] = set()
    for sv in svs:
        gt_map = sv.get("gt") or {}
        for sample in affected:
            genotype_class = classify_genotype(str(gt_map.get(sample, "")))
            if genotype_class == HOM_ALT:
                zygosities.add("hom")
            elif genotype_class == HET:
                zygosities.add("het")
    if "hom" in zygosities and "het" in zygosities:
        affected_zygosity: str | None = "mixed"
    elif "hom" in zygosities:
        affected_zygosity = "hom"
    elif "het" in zygosities:
        affected_zygosity = "het"
    else:
        affected_zygosity = None

    # Prefer read-based phasing (phased SVs) when available; otherwise infer by segregation.
    read_phase = _read_phase_verdict(svs, affected, snv_gt_by_sample, snv_ps_by_sample)
    if read_phase is not None:
        phase = read_phase
        phase_evidence: str | None = PHASE_EVIDENCE_READ
    else:
        phase = _phase_verdict(
            svs,
            affected,
            set(unaffected_samples or []),
            snv_gt_by_sample,
            snv_dp_by_sample=snv_dp_by_sample,
            pedigree=pedigree,
            snv_locus=snv_locus,
        )
        phase_evidence = PHASE_EVIDENCE_SEGREGATION if phase in {PHASE_TRANS, PHASE_CIS} else None

    has_deletion = any(t in {"DEL", "CNV"} for t in sv_types)
    locus_chr, locus_start, locus_end = _bounding_locus(svs)
    return {
        "sv_count": len(svs),
        "sv_types": sv_types,
        "affected_zygosity": affected_zygosity,
        "has_deletion": has_deletion,
        "phase": phase,
        "phase_evidence": phase_evidence,
        "deletion_unmasked": has_deletion and phase == PHASE_TRANS,
        "chr": locus_chr,
        "start": locus_start,
        "end": locus_end,
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
