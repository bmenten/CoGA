"""Per-family SV→gene index (SNV + SV compound het, Phase 0).

Stores which genes a family's structural variants hit, so the small-variant workspace can
flag genes that also carry an SV (the cross-type "second hit"). This module owns only the
Postgres rows + the badge summary; the ClickHouse scan that populates it lives in
``clickhouse_family_variants`` (which holds the SV helpers). See docs/snv-sv-compound-het.md.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

# Genotype groupings (mirrors the structural genotype groups used elsewhere).
_HET_GTS = {"0/1", "1/0", "0|1", "1|0"}
_HOM_GTS = {"1/1", "1|1"}


def _gt_has_alt(gt: str | None) -> bool:
    text_gt = str(gt or "").strip()
    if not text_gt:
        return False
    return any(allele not in {"0", ".", ""} for allele in text_gt.replace("|", "/").split("/"))


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


def _phase_verdict(
    svs: list[dict[str, Any]],
    affected: set[str],
    unaffected: set[str],
    snv_gt_by_sample: dict[str, str] | None,
) -> str:
    """trans / cis / unknown by segregation — the same rule as the SNV+SNV compound-het call.

    A clean candidate needs the SNV heterozygous in every affected sample and the SV present
    in every affected sample. It's ``trans`` (compound-het) when no unaffected individual
    carries *both* hits, ``cis`` when one does, and ``unknown`` without that evidence.
    """
    if snv_gt_by_sample is None or not affected:
        return "unknown"

    snv_het_in_affected = all(
        str(snv_gt_by_sample.get(sample, "")).strip() in _HET_GTS for sample in affected
    )
    sv_in_affected = all(
        any(_gt_has_alt((sv.get("gt") or {}).get(sample)) for sv in svs) for sample in affected
    )
    if not (snv_het_in_affected and sv_in_affected):
        return "unknown"

    for sample in unaffected:
        snv_alt = _gt_has_alt(snv_gt_by_sample.get(sample))
        sv_alt = any(_gt_has_alt((sv.get("gt") or {}).get(sample)) for sv in svs)
        if snv_alt and sv_alt:
            return "cis"
    if not unaffected:
        return "unknown"  # no segregation evidence (e.g. singleton)
    return "trans"


async def is_index_built(session: AsyncSession, family_uuid: str) -> bool:
    found = (
        await session.execute(
            text(
                "SELECT 1 FROM family_sv_gene_index_status WHERE family_id = CAST(:fid AS uuid)"
            ),
            {"fid": family_uuid},
        )
    ).scalar()
    return found is not None


async def store_sv_gene_index(
    session: AsyncSession,
    *,
    family_uuid: str,
    gene_map: dict[str, list[dict[str, Any]]],
    sv_total: int,
) -> None:
    """Replace the family's index with ``gene -> [svs]`` and mark it built."""
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
            INSERT INTO family_sv_gene_index_status (family_id, sv_total, gene_count, computed_at)
            VALUES (CAST(:fid AS uuid), :sv_total, :gene_count, now())
            ON CONFLICT (family_id) DO UPDATE SET
                sv_total = EXCLUDED.sv_total,
                gene_count = EXCLUDED.gene_count,
                computed_at = now()
            """
        ),
        {"fid": family_uuid, "sv_total": sv_total, "gene_count": len(gene_map)},
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
    rows = (
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
) -> dict[str, Any]:
    """Compact badge summary: which SV types hit the gene, the zygosity in affected
    individuals, and — when the SNV genotype is supplied — the trans/cis phase verdict.

    The headline is a deletion in trans with a heterozygous SNV: the deletion removes the
    other allele, so the pair is effectively biallelic (``deletion_unmasked``)."""
    sv_types = sorted({str(sv.get("sv_type") or "SV").upper() for sv in svs})
    affected = set(affected_samples or [])
    zygosities: set[str] = set()
    for sv in svs:
        gt_map = sv.get("gt") or {}
        for sample in affected:
            gt = str(gt_map.get(sample, "")).strip()
            if gt in _HOM_GTS:
                zygosities.add("hom")
            elif gt in _HET_GTS:
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
        phase_evidence: str | None = "read"
    else:
        phase = _phase_verdict(svs, affected, set(unaffected_samples or []), snv_gt_by_sample)
        phase_evidence = "segregation" if phase in {"trans", "cis"} else None

    has_deletion = any(t in {"DEL", "CNV"} for t in sv_types)
    return {
        "sv_count": len(svs),
        "sv_types": sv_types,
        "affected_zygosity": affected_zygosity,
        "has_deletion": has_deletion,
        "phase": phase,
        "phase_evidence": phase_evidence,
        "deletion_unmasked": has_deletion and phase == "trans",
    }


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
