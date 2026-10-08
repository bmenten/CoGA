"""Per-marker phasing for GLIMPSE2-imputed variants.

For every family member, returns the value drawn on each haplotype lane at each
informative imputed site — one raw call per site, no binning/smoothing/voting:

- For a child: which parental homolog (0/1) was inherited on each side
  (paternal -> hap1, maternal -> hap2), mapped to the displayed shade of the
  parent's stored haplotype block so the marker always agrees with the block.
- For a parent: the alleles on their own two phased homologs (the raw per-site
  version of their haplotype blocks).

This is deliberate: the phased-marker overlay is the *diagnostic* layer. Its
purpose is to expose where the imputed phasing is noisy or uncertain about the
haplotypes (isolated switches, jitter at recombination boundaries). The "cleaned"
view is the Haplotype block track; collapsing the raw calls here would hide
exactly the signal the overlay exists to show.

The Mendelian classifier is reused verbatim from the haplotype block builder so
the child homolog indices line up with the parents' raw homologs. Child markers
are then mapped to displayed shades via each parent's STORED-block shade map
(``founder_shade_map``), exactly as the lineage service colours relatives. This
makes the markers agree with the blocks genome-wide — with no region-local
affected-child recount, and no dependence on current affected status.
"""
from __future__ import annotations

from typing import Any

from ..schemas import (
    PhasedMarker,
    PhasedMarkerResponse,
    PhasedMarkerSample,
    PhasedSampleQc,
    PhasedSite,
)
from .clickhouse_family_variants import fetch_imputed_phased_genotypes
from .clickhouse_interval_tracks import fetch_interval_track_rows
from .family_metadata_context import FamilyMetadataContext
from .haplotype_lineage_service import _allele_int, build_pedigree, founder_shade_map
from .haplotype_phase_correction import corrected_genotype_rows
from .haplotype_block_builder import (
    _parent_sample_names,
    _phased_haplotype_alleles,
    _transmitted_parent_haplotype,
)

# Fetch ceiling — covers a whole (largest) chromosome of family-scoped imputed
# sites with headroom (~275k on chr2 here); truncation only affects the tail of
# an unusually large family's full-chromosome view. This bounds the ClickHouse
# query only; the markers it returns are emitted raw, one point per site.
PHASED_FETCH_LIMIT = 500_000


def _is_mendelian_consistent(
    father_alleles: tuple[str, str],
    mother_alleles: tuple[str, str],
    child_alleles: tuple[str, str],
) -> bool:
    """True if the child's genotype can be formed from one paternal + one maternal
    allele. A False here is a genuine Mendelian inconsistency (impossible transmission
    -> likely sample swap / wrong pedigree), distinct from parent-of-origin ambiguity
    (e.g. both parents het + child het), which is perfectly consistent yet leaves both
    `_transmitted_parent_haplotype` calls None. We must NOT flag that benign case."""
    child_state = tuple(sorted(child_alleles))
    for paternal_allele in father_alleles:
        for maternal_allele in mother_alleles:
            if tuple(sorted((paternal_allele, maternal_allele))) == child_state:
                return True
    return False


def _shares_allele(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """Do two genotypes share at least one allele? A true parent and child always do;
    sharing none is a single-parent Mendelian inconsistency."""
    return bool({a[0], a[1]} & {b[0], b[1]})


def _transmitted_single_parent_haplotype(
    parent_alleles: tuple[str, str] | None,
    child_alleles: tuple[str, str] | None,
) -> str | None:
    """With only ONE known parent (donor family), which parent homolog (0/1) the
    child inherited is resolvable only where the parent is heterozygous AND the child
    is homozygous: the child's duplicated allele is the transmitted one and exactly
    one parent homolog carries it. Parent-homozygous or child-heterozygous sites are
    ambiguous — the absent/donor parent could supply either allele — so they yield
    ``None``."""
    if parent_alleles is None or child_alleles is None:
        return None
    p0, p1 = parent_alleles
    c0, c1 = child_alleles
    if p0 == p1 or c0 != c1:
        return None
    allele = c0
    if p0 == allele and p1 != allele:
        return "0"
    if p1 == allele and p0 != allele:
        return "1"
    return None


def compute_phased_markers(
    rows: list[tuple[int, list[str], list[str]]],
    *,
    father: str,
    mother: str,
    children: list[str],
    father_shade: dict[int, int],
    mother_shade: dict[int, int],
) -> tuple[dict[str, list[PhasedMarker]], dict[str, PhasedSampleQc]]:
    """Per member, return the raw per-site haplotype-lane values.

    Children get the parental homolog inherited on each side (hap1 = paternal,
    hap2 = maternal), each mapped to the displayed shade of the corresponding
    parent's stored haplotype block via ``father_shade`` / ``mother_shade``
    ({raw_homolog_idx: displayed_shade}). Parents get the alleles on their own two
    phased homologs (raw, unmapped). One PhasedMarker per imputed site — no binning
    or majority-voting, so isolated single-marker switches are preserved (the
    overlay surfaces phasing noise/uncertainty rather than hiding it).

    The shade maps come from the parents' STORED blocks, which were oriented once
    genome-wide at upload; mapping through them makes a child's markers agree with
    the parent's block shade everywhere — no region-local recount, no dependence on
    current affected status. If a shade map is empty (parent has no stored block in
    the region) it acts as identity (raw homolog index passes through).

    Also returns a per-child QC summary keyed by child name: at every site where
    BOTH parents and the child carry a valid phased genotype (jointly informative),
    ``informative_sites`` is incremented, and ``mendel_errors`` is incremented when
    the child's genotype is impossible given the parents' alleles (a true Mendelian
    inconsistency). QC is computed before the marker is emitted, so a Mendel error —
    which leaves both transmissions undetermined and thus draws no marker — is still
    counted."""
    # A donor (single-parent) family has only one known parent; the other side is a
    # donor whose lane stays blank. Both `father` and `mother` may be passed; a falsy
    # one means that side is absent.
    present_parents = [
        (name, shade, side)
        for name, shade, side in (
            (father, father_shade, "paternal"),
            (mother, mother_shade, "maternal"),
        )
        if name
    ]
    single_parent = len(present_parents) == 1
    members = [name for name, _, _ in present_parents] + list(children)

    def shade_of(value: int | None, shade_map: dict[int, int]) -> int | None:
        if value is None:
            return None
        return shade_map.get(value, value)

    result: dict[str, list[PhasedMarker]] = {member: [] for member in members}
    qc_counts: dict[str, dict[str, int]] = {
        child: {"informative_sites": 0, "mendel_errors": 0} for child in children
    }
    for row in rows:
        pos, sample_ids, gts = row[0], row[-2], row[-1]
        gt = dict(zip(sample_ids, gts))
        parent_alleles: dict[str, tuple[str, str]] = {}
        for name, _shade, _side in present_parents:
            alleles = _phased_haplotype_alleles(gt.get(name))
            if alleles is None:
                parent_alleles = {}
                break
            parent_alleles[name] = alleles
        if not parent_alleles:
            continue  # need every present parent genotyped at this site

        # Present parents: the alleles on their own homologs (raw, not shade-mapped).
        for name, _shade, _side in present_parents:
            a = parent_alleles[name]
            result[name].append(PhasedMarker(pos=pos, hap1=_allele_int(a[0]), hap2=_allele_int(a[1])))

        for child in children:
            child_alleles = _phased_haplotype_alleles(gt.get(child))
            if child_alleles is None:
                continue
            child_qc = qc_counts[child]

            if single_parent:
                # One known parent (donor family): the inherited homolog is resolvable
                # only at parent-het + child-hom sites; the donor lane is left blank.
                # Mendel error = child shares no allele with the known parent.
                name, shade, side = present_parents[0]
                parent = parent_alleles[name]
                child_qc["informative_sites"] += 1
                if not _shares_allele(parent, child_alleles):
                    child_qc["mendel_errors"] += 1
                transmitted = _transmitted_single_parent_haplotype(parent, child_alleles)
                if transmitted is None:
                    continue
                lane = shade_of(int(transmitted), shade)
                result[child].append(
                    PhasedMarker(pos=pos, hap1=lane, hap2=None)
                    if side == "paternal"
                    else PhasedMarker(pos=pos, hap1=None, hap2=lane)
                )
                continue

            # Two known parents (trio): classic Mendelian parent-of-origin. Count the
            # jointly-informative site and flag a Mendelian inconsistency before any
            # marker decision — a Mendel error draws no marker but is the signal we want.
            father_alleles = parent_alleles[father]
            mother_alleles = parent_alleles[mother]
            child_qc["informative_sites"] += 1
            if not _is_mendelian_consistent(father_alleles, mother_alleles, child_alleles):
                child_qc["mendel_errors"] += 1
            paternal_raw = _transmitted_parent_haplotype(father_alleles, mother_alleles, child_alleles)
            maternal_raw = _transmitted_parent_haplotype(mother_alleles, father_alleles, child_alleles)
            if paternal_raw is None and maternal_raw is None:
                continue
            # `_transmitted_parent_haplotype` returns the homolog index as a string.
            paternal = int(paternal_raw) if paternal_raw is not None else None
            maternal = int(maternal_raw) if maternal_raw is not None else None
            result[child].append(
                PhasedMarker(
                    pos=pos,
                    hap1=shade_of(paternal, father_shade),
                    hap2=shade_of(maternal, mother_shade),
                )
            )
    qc_by_child = {
        child: PhasedSampleQc(
            informative_sites=counts["informative_sites"],
            mendel_errors=counts["mendel_errors"],
            mendel_rate=(
                counts["mendel_errors"] / counts["informative_sites"]
                if counts["informative_sites"]
                else 0.0
            ),
        )
        for child, counts in qc_counts.items()
    }
    return result, qc_by_child


async def _parent_block_shade_maps(
    context: FamilyMetadataContext,
    *,
    chr: str,
    father: str,
    mother: str,
    start: int,
    end: int,
) -> tuple[dict[int, int], dict[int, int]]:
    """Build each parent's {raw_homolog_idx: displayed_shade} map from their STORED
    haplotype blocks overlapping the region, mirroring the lineage service. A parent
    with no block here (or no resolvable uuid) yields an empty map -> identity
    orientation downstream."""
    name_to_uuid = context.sample_name_to_uuid
    father_uuid = name_to_uuid.get(father)
    mother_uuid = name_to_uuid.get(mother)
    sample_uuids = [uuid for uuid in (father_uuid, mother_uuid) if uuid]
    if not sample_uuids or not context.assembly_name:
        return {}, {}
    rows = await fetch_interval_track_rows(
        context.assembly_name,
        family_uuid=context.family_uuid,
        sample_uuids=sample_uuids,
        track_type="haplotype",
        chromosomes=[chr],
        start=start,
        end=end,
    )
    segments_by_uuid: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        segments_by_uuid.setdefault(str(row["sample_uuid"]), []).append(
            {"hap1": str(row.get("hap1") or ""), "hap2": str(row.get("hap2") or "")}
        )
    father_shade = founder_shade_map(segments_by_uuid.get(str(father_uuid), [])) if father_uuid else {}
    mother_shade = founder_shade_map(segments_by_uuid.get(str(mother_uuid), [])) if mother_uuid else {}
    return father_shade, mother_shade


async def get_family_phased_markers_response(
    context: FamilyMetadataContext,
    *,
    chr: str,
    start: int | None,
    end: int | None,
) -> PhasedMarkerResponse:
    father, mother = _parent_sample_names(context)
    if (
        (father is None and mother is None)
        or not context.assembly_name
        or start is None
        or end is None
        or end <= start
    ):
        return PhasedMarkerResponse(chr=chr, start=start, end=end, samples=[])

    # Inheritance markers are only meaningful for the index parents' own children:
    # `_transmitted_parent_haplotype` resolves which parental homolog a *child*
    # inherited. Running it on a relative (e.g. the father's mother) is biologically
    # backwards and yields coincidental, wildly-switching noise. So compute markers
    # only for the index couple's children (single-parent/donor families: the one
    # known parent's children); relatives still appear (for the tooltip) but carry no
    # marker dots — their lineage block is the meaningful view.
    present_parents = [p for p in (father, mother) if p]
    pedigree = build_pedigree(context.sample_rows, context.relationship_rows)
    if father and mother:
        children = sorted(pedigree.children_of.get(father, set()) & pedigree.children_of.get(mother, set()))
    else:
        children = sorted(pedigree.children_of.get(father or mother, set()))
    relatives = [
        name
        for name in context.sample_name_to_uuid
        if name not in set(present_parents) and name not in set(children)
    ]
    member_order = [*present_parents, *children, *relatives]

    # Orient each child's markers from the parents' STORED haplotype blocks
    # (oriented once genome-wide at upload), exactly as the lineage service derives
    # a parent's founder shade. This keeps the markers in step with the blocks in
    # every sub-region — even distal to a crossover, where a region-local affected
    # recount would have flipped them — and removes any dependence on current
    # affected status. An empty shade map (no stored block here) falls back to
    # identity orientation inside compute_phased_markers.
    father_shade, mother_shade = await _parent_block_shade_maps(
        context, chr=chr, father=father, mother=mother, start=int(start), end=int(end)
    )
    rows = await fetch_imputed_phased_genotypes(
        context, chrom=chr, start=int(start), end=int(end), limit=PHASED_FETCH_LIMIT
    )

    # The fetch is `ORDER BY pos LIMIT PHASED_FETCH_LIMIT`, so once the region holds
    # at least that many sites it deterministically drops the highest-coordinate tail
    # with no signal. Rendering the surviving markers would draw a partial overlay
    # that silently stops part-way across a full-length block — misleading in a
    # clinical view. So when truncated we emit no markers/sites and flag it; the
    # client shows a 'too many sites — zoom in' state while the blocks still render.
    # `covered` reports the [min_pos, max_pos] span we actually fetched.
    if len(rows) >= PHASED_FETCH_LIMIT:
        positions = [row[0] for row in rows]
        covered = [min(positions), max(positions)] if positions else None
        return PhasedMarkerResponse(
            chr=chr,
            start=start,
            end=end,
            samples=[
                PhasedMarkerSample(sample=member, markers=[], reference=member in set(present_parents))
                for member in member_order
            ],
            sites=[],
            truncated=True,
            covered=covered,
        )

    markers_by_member, qc_by_child = compute_phased_markers(
        # The parents' phase as the blocks were built from: swapped where it switched,
        # so a child's markers stay with its blocks past a corrected phase switch. The
        # tooltip's genotypes below stay as called.
        corrected_genotype_rows(rows, context.phase_corrections, chrom=chr),
        father=father,
        mother=mother,
        children=children,
        father_shade=father_shade,
        mother_shade=mother_shade,
    )
    # Only sites that actually produced a marker for >=1 member carry diagnostic
    # signal (both parents non-missing at that pos -> at least the parents' own raw
    # markers are emitted). Positions where every member was dropped (e.g. a parent
    # missing) are pure wasted payload: no dot is drawn and no tooltip row matters.
    # The hover tooltip anchors on these positions, so excluding them also keeps the
    # client's hover targets aligned with the rendered markers.
    marker_positions = {marker.pos for markers in markers_by_member.values() for marker in markers}
    # Per-site raw phased genotypes for every member, for the hover tooltip. These
    # are the members' actual alleles (decoded to nucleotides client-side via
    # ref/alt) — unlike the per-member marker lanes, which are homolog indices.
    sites = [
        PhasedSite(
            pos=pos,
            ref=ref,
            alt=alt,
            gts=[dict(zip(sample_ids, gts)).get(member, "") for member in member_order],
        )
        for pos, ref, alt, sample_ids, gts in rows
        if pos in marker_positions
    ]
    return PhasedMarkerResponse(
        chr=chr,
        start=start,
        end=end,
        samples=[
            PhasedMarkerSample(
                sample=member,
                markers=markers_by_member.get(member, []),
                reference=member in set(present_parents),
                qc=qc_by_child.get(member),
            )
            for member in member_order
        ],
        sites=sites,
    )
