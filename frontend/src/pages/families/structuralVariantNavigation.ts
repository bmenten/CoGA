import type { StructuralVariant } from './structuralVariantSearch';

/**
 * Flanking context added on each side of a structural variant when it is opened in a
 * genome workspace.
 *
 * Both windows span the whole call — first breakpoint to last — plus this much either
 * side, rather than the bare `start-end` span, which for a BND or an insertion (where
 * `end` is the same base as `start`) collapses to a single position and leaves the view
 * parked on the start breakpoint instead of showing the event.
 *
 * IGV takes the tighter flank: it renders reads, so the useful view is the junctions
 * close up. The chromosome view is a whole-chromosome picture where a wider margin costs
 * nothing and keeps the neighbourhood on screen.
 */
export const SV_IGV_FLANK_BP = 1_000;
export const SV_CHROMOSOME_FLANK_BP = 50_000;

/**
 * Primary-locus IGV + Chromosome-view links for a structural variant. Shared by
 * StructuralVariantCards and StructuralVariantTable so the locus/window/query-param
 * format stays in one place. The Table additionally renders a remote-locus (BND
 * partner) IGV link, which stays local to that component but pads with the same flank.
 */
export const buildStructuralVariantNavigation = ({
  familyId,
  linkSearch,
  projectId,
  variant,
}: {
  familyId?: string;
  linkSearch: string;
  projectId?: string;
  variant: StructuralVariant;
}) => {
  const chr = variant.chr.startsWith('chr') ? variant.chr : `chr${variant.chr}`;
  // `end` is not guaranteed to sit after `start` for every caller/type, so anchor the
  // far edge on whichever is greater before padding.
  const callEnd = Math.max(variant.start, variant.end);
  // The call's own span, for showing where the SV is. The padded windows below are where
  // a viewer opens, which is not the same thing.
  const locus = `${chr}:${variant.start}-${callEnd}`;
  const igvLocus = `${chr}:${Math.max(1, variant.start - SV_IGV_FLANK_BP)}-${
    callEnd + SV_IGV_FLANK_BP
  }`;
  const backPath = `/families/${familyId}/structural-variants${linkSearch}`;
  const igvHref = `/families/${familyId}/igv?locus=${encodeURIComponent(igvLocus)}${
    projectId ? `&project_id=${projectId}` : ''
  }&back_path=${encodeURIComponent(backPath)}`;
  const viewHref = `/families/${familyId}/chromosome/${variant.chr.replace(/^chr/, '')}?start=${Math.max(
    0,
    variant.start - SV_CHROMOSOME_FLANK_BP,
  )}&end=${callEnd + SV_CHROMOSOME_FLANK_BP}${linkSearch ? `&${linkSearch.slice(1)}` : ''}${
    projectId ? `&project_id=${projectId}` : ''
  }`;
  return { locus, igvLocus, igvHref, viewHref };
};
