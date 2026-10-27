import React, { useMemo, useState } from 'react';
import { useParams, useSearchParams, Link } from 'react-router';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import { cssVar } from '../../lib/colors';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import FamilyPageBanners from '../../components/FamilyPageBanners';
import PageState from '../../components/PageState';
import QueryFailure from '../../components/QueryFailure';
import HaplotypeLegend from '../../components/visualizations/HaplotypeLegend';
import VizTooltip from '../../components/visualizations/VizTooltip';
import type {
  HaplotypeResponse,
  HaplotypeSegment,
  PhasedMarkerResponse,
  PhasedSite,
} from '../../lib/apiSchema.generated';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import { apiPath } from '../../lib/apiPath';
import { normalizeSex } from '../../lib/haplotypeRisk';
import {
  NO_ALLELE,
  alleleBase,
  inRanges,
  nucleotideColor,
  oneCopyAlleles,
  rowAlleles,
  twoDifferentAlleles,
} from '../../lib/phasedMarkers';
import { useFamilyReference } from '../../lib/reference';
import { raw, routePath } from '../../lib/routePath';

// Floor on the zoomed-in window so the view can't collapse to nothing.
const MIN_SPAN = 2_000;
// Cap rendered columns so a dense window stays responsive; the rest is summarised.
const MAX_COLUMNS = 800;

// The two homolog lanes drawn per member, mirroring the chromosome view.
const LANES = ['hap1', 'hap2'] as const;
type Lane = (typeof LANES)[number];

const laneColor = (seg: HaplotypeSegment | null, lane: Lane): string => {
  const grey = cssVar('--color-haplotype-unknown');
  if (!seg) return grey;
  const lineage = (lane === 'hap1' ? seg.hap1_lineage : seg.hap2_lineage) || '';
  const value = lane === 'hap1' ? seg.hap1 : seg.hap2;
  const shade = parseInt(value, 10);
  if (lineage === 'paternal') {
    return cssVar(shade === 1 ? '--color-haplotype-father-light' : '--color-haplotype-father-dark');
  }
  if (lineage === 'maternal') {
    return cssVar(shade === 1 ? '--color-haplotype-mother-light' : '--color-haplotype-mother-dark');
  }
  return grey; // untransmitted / unknown / donor
};

// Does a raw marker agree with its cleaned haplotype block on this lane? They agree
// when the founder colour the marker would draw equals the block's. A disagreement
// (returns false) flags a phasing switch / artifact / unannotated recombination;
// null = nothing to compare (no block, or the lane is uninformative here).
const laneMatchesBlock = (
  seg: HaplotypeSegment | null,
  laneValue: number | null,
  lane: Lane,
): boolean | null => {
  if (!seg || laneValue === null) return null;
  const rawSeg: HaplotypeSegment =
    lane === 'hap1' ? { ...seg, hap1: String(laneValue) } : { ...seg, hap2: String(laneValue) };
  return laneColor(rawSeg, lane) === laneColor(seg, lane);
};

// Uninformative alleles (lane not resolved for this member) are a light grey so the
// eye lands on the nucleotides that actually carry segregation signal.
const UNINFORMATIVE_COLOR = '#cbd5e1';

const coveringSegment = (segments: HaplotypeSegment[], pos: number): HaplotypeSegment | null => {
  let lo = 0;
  let hi = segments.length - 1;
  let found: HaplotypeSegment | null = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segments[mid].start <= pos) {
      found = segments[mid];
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found && pos < found.end ? found : null;
};

const parseAlleleIndices = (gt: string): number[] | null => {
  const sep = gt.includes('|') ? '|' : gt.includes('/') ? '/' : null;
  if (!sep) return null;
  const alleles = gt.split(sep).map((part) => parseInt(part, 10));
  return alleles.some((n) => Number.isNaN(n)) ? null : alleles;
};

// A man's call on his one X (chrX outside the PARs) is read, and what a member's two homolog rows
// draw at a site (`rowAlleles`: a call of one allele, once), by lib/phasedMarkers.ts, which the
// haplotype track's tooltip shares (ENG-19).

// Mirrors the backend Mendelian check: the child genotype must form from one
// allele of each parent (two parents), or share an allele with the lone parent.
const mendelianConsistent = (parents: number[][], child: number[]): boolean => {
  if (parents.length >= 2) {
    const childKey = [...child].sort((a, b) => a - b).join(',');
    for (const pa of parents[0]) {
      for (const ma of parents[1]) {
        if ([pa, ma].sort((a, b) => a - b).join(',') === childKey) return true;
      }
    }
    return false;
  }
  const parentAlleles = new Set(parents[0]);
  return child.some((allele) => parentAlleles.has(allele));
};

// Why a child's genotype is a Mendelian error, as its cell's note says it: it cannot come from
// the parents; or, for a son on his one X, which is his mother's (ENG-19), his mother does not
// carry his allele, or he has two different alleles.
type MendelError = 'parents' | 'not-his-mothers' | 'two-alleles';
const MENDEL_ERROR_NOTES: Record<MendelError, string> = {
  parents:
    "⚠ Mendelian error — this genotype can't be inherited from the parents (sample swap, wrong pedigree, or genotyping noise).",
  'not-his-mothers':
    '⚠ Mendelian error — his mother does not carry this allele, and on chrX outside the PARs a son’s one X is hers (sample swap, wrong pedigree, or genotyping noise).',
  'two-alleles':
    '⚠ Mendelian error — two different alleles, which a son cannot have on his one X outside the PARs (a sex mix-up or contamination).',
};

// One empty list, so the memos below see a stable value before data arrive.
const NO_SITES: PhasedSite[] = [];
const NO_RANGES: number[][] = [];

const FamilyRoiMarkersPage: React.FC = () => {
  const { familyId = '' } = useParams();
  const [searchParams] = useSearchParams();
  const projectId = searchParams.get('project_id');
  // Floating hover tooltip (same component/style as the chromosome view's track).
  const [tooltip, setTooltip] = useState<{ x: number; y: number; node: React.ReactNode } | null>(null);

  const {
    data: family,
    isLoading: familyLoading,
    isError: familyFailed,
    error: familyError,
    refetch: refetchFamily,
  } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    queryFn: async () => (await api.get(apiPath`/families/${familyId}`)).data as ApiFamilyRecord,
  });

  // The family's assembly, for the validated-scope warning every family page carries.
  const {
    assemblyName,
    assemblyValidated,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(family?.projects, projectId ?? undefined);

  const roi = family?.roi ?? null;
  // The user-adjustable view window. `null` means "follow the ROI default" (ROI ± flank);
  // zoom/pan write an explicit span here, and Reset clears it back to the default.
  const [viewport, setViewport] = useState<{ start: number; end: number } | null>(null);
  const window = useMemo(() => {
    if (!roi) return null;
    // The standard view is the ROI itself; zoom/pan widen it for context on demand.
    const base = { start: Math.max(0, roi.start), end: roi.end };
    const view = viewport ?? base;
    return { chr: roi.chr, start: view.start, end: view.end };
  }, [roi, viewport]);

  const {
    data: phased,
    isLoading: phasedLoading,
    isFetching: phasedFetching,
    isError: phasedFailed,
    error: phasedError,
    refetch: refetchPhased,
  } = useQuery<PhasedMarkerResponse>({
    queryKey: ['phased-markers', familyId, window?.chr, window?.start, window?.end],
    enabled: !!window,
    placeholderData: keepPreviousData,
    queryFn: async () =>
      (
        await api.get(apiPath`/families/${familyId}/phased-markers`, {
          params: { chr: window!.chr, start: window!.start, end: window!.end },
        })
      ).data as PhasedMarkerResponse,
  });

  const {
    data: haplo,
    isError: haploFailed,
    error: haploError,
    refetch: refetchHaplo,
  } = useQuery<HaplotypeResponse>({
    queryKey: ['haplotypes', familyId, window?.chr, window?.start, window?.end],
    enabled: !!window,
    placeholderData: keepPreviousData,
    queryFn: async () =>
      (
        await api.get(apiPath`/families/${familyId}/haplotypes`, {
          params: { chr: window!.chr, start: window!.start, end: window!.end },
        })
      ).data as HaplotypeResponse,
  });

  const roleBySample = useMemo(() => {
    const map = new Map<string, string>();
    (family?.members ?? []).forEach((m) => map.set(m.sample_id, String(m.role || '').toLowerCase()));
    return map;
  }, [family?.members]);

  const segmentsBySample = useMemo(() => {
    const map = new Map<string, HaplotypeSegment[]>();
    (haplo?.samples ?? []).forEach((s) =>
      map.set(s.sample, [...s.segments].sort((a, b) => a.start - b.start)),
    );
    return map;
  }, [haplo?.samples]);

  // Positions where at least one EMBRYO has a determinable inherited homolog: the
  // markers that actually drive the segregation call.
  const embryoInformativePos = useMemo(() => {
    const set = new Set<number>();
    (phased?.samples ?? []).forEach((s) => {
      if (roleBySample.get(s.sample) !== 'embryo') return;
      s.markers.forEach((m) => {
        if (m.hap1 !== null || m.hap2 !== null) set.add(m.pos);
      });
    });
    return set;
  }, [phased?.samples, roleBySample]);

  const allSites = phased?.sites ?? NO_SITES;
  const informativeSites = useMemo(
    () => allSites.filter((s) => embryoInformativePos.has(s.pos)),
    [allSites, embryoInformativePos],
  );

  // Only the informative markers drive the segregation call; the rest are noise here.
  const shownSites = useMemo(() => informativeSites.slice(0, MAX_COLUMNS), [informativeSites]);
  const siteByPos = useMemo(() => new Map(shownSites.map((s) => [s.pos, s])), [shownSites]);
  const sampleOrder = useMemo(() => (phased?.samples ?? []).map((s) => s.sample), [phased?.samples]);
  const qcBySample = useMemo(
    () => new Map((phased?.samples ?? []).map((s) => [s.sample, s.qc])),
    [phased?.samples],
  );

  // Per member, the resolved homolog index on each lane at each position. A lane is
  // informative exactly where it carries a value: for a child that means the parent-of-
  // origin was resolvable there; a parent carries its own allele; relatives have none.
  const markerLanes = useMemo(() => {
    const map = new Map<string, Map<number, { hap1: number | null; hap2: number | null }>>();
    (phased?.samples ?? []).forEach((s) => {
      const byPos = new Map<number, { hap1: number | null; hap2: number | null }>();
      s.markers.forEach((m) => byPos.set(m.pos, { hap1: m.hap1, hap2: m.hap2 }));
      map.set(s.sample, byPos);
    });
    return map;
  }, [phased?.samples]);

  // child sample -> its parents' samples (sample_id_a = parent, sample_id_b = child), with
  // the parent's role in that relationship (role_a: father or mother).
  const parentsOf = useMemo(() => {
    const map = new Map<string, { sample: string; role: string }[]>();
    (family?.relationships ?? []).forEach((rel) => {
      if (rel.relationship_type !== 'parent_child') return;
      const parents = map.get(rel.sample_id_b) ?? [];
      if (!parents.some((parent) => parent.sample === rel.sample_id_a)) {
        parents.push({ sample: rel.sample_id_a, role: String(rel.role_a || '').toLowerCase() });
      }
      map.set(rel.sample_id_b, parents);
    });
    return map;
  }, [family?.relationships]);

  const sexBySample = useMemo(() => {
    const map = new Map<string, 'male' | 'female' | 'unknown'>();
    (family?.members ?? []).forEach((m) => map.set(m.sample_id, normalizeSex(m.sex)));
    return map;
  }, [family?.members]);
  const hemizygousRanges = phased?.hemizygous_x_ranges ?? NO_RANGES;

  // Per child, the shown positions whose genotype can't be inherited from its
  // (genotyped) parents — a Mendelian error, highlighted for emphasis — and why. Where a man
  // has one X (chrX outside the PARs), a man's call is one copy, and a son is checked against
  // his mother alone, as the QC counts; a member whose sex is not recorded has two copies.
  const mendelErrorPosBySample = useMemo(() => {
    const map = new Map<string, Map<number, MendelError>>();
    const idxBySample = new Map(sampleOrder.map((sample, index) => [sample, index]));
    parentsOf.forEach((parents, child) => {
      const childIdx = idxBySample.get(child);
      const genotyped = parents.filter((parent) => idxBySample.has(parent.sample));
      if (childIdx === undefined || genotyped.length === 0) return;
      // A son's one X is his mother's: the parent who is his mother, or else recorded female.
      const mother =
        genotyped.find((parent) => parent.role === 'mother') ??
        genotyped.find((parent) => parent.role !== 'father' && sexBySample.get(parent.sample) === 'female');
      const male = (sample: string) => sexBySample.get(sample) === 'male';
      const errors = new Map<number, MendelError>();
      shownSites.forEach((site) => {
        const gtOf = (sample: string) => site.gts[idxBySample.get(sample) ?? -1] ?? '';
        const oneCopy = inRanges(hemizygousRanges, site.pos);
        if (oneCopy && male(child)) {
          // His father gives him a Y: a father-son pair is neither checked nor an error.
          const motherAlleles = mother ? parseAlleleIndices(gtOf(mother.sample)) : null;
          if (!motherAlleles) return;
          if (twoDifferentAlleles(gtOf(child))) {
            errors.set(site.pos, 'two-alleles'); // a genotype a son cannot have
            return;
          }
          const son = oneCopyAlleles(gtOf(child));
          if (son && !motherAlleles.includes(son[0])) errors.set(site.pos, 'not-his-mothers');
          return;
        }
        const childAlleles = parseAlleleIndices(gtOf(child));
        if (!childAlleles) return;
        const parentAlleles = genotyped
          .map((parent) => (oneCopy && male(parent.sample) ? oneCopyAlleles : parseAlleleIndices)(gtOf(parent.sample)))
          .filter((a): a is number[] => a !== null);
        if (parentAlleles.length < genotyped.length) return; // need every parent genotyped
        if (!mendelianConsistent(parentAlleles, childAlleles)) errors.set(site.pos, 'parents');
      });
      if (errors.size) map.set(child, errors);
    });
    return map;
  }, [parentsOf, shownSites, sampleOrder, sexBySample, hemizygousRanges]);

  // The project's reference too: the assembly-scope warning shows nothing while it loads
  // (#515), so the markers showed without "Not validated for clinical use" (ENG-12).
  if (familyLoading || phasedLoading || referenceLoading) {
    return <PageState loading title="Loading ROI markers…" />;
  }
  // Without the family its ROI is unknown, not absent (#607). A family that does not exist,
  // or is not in the user's projects, is not found: the page said "could not be loaded" for
  // a 404 too (ENG-10).
  if (familyFailed) {
    return (
      <FamilyLoadFailure
        kicker="ROI markers"
        what="Family"
        error={familyError}
        notFoundMessage="This family could not be found."
        onRetry={() => void refetchFamily()}
      />
    );
  }
  if (!roi || !window) {
    return (
      <PageState
        title="No region of interest"
        message="Set a region of interest for this family to review its markers."
        action={
          <Link to={routePath`/families/${familyId}${raw(projectId ? `?project_id=${encodeURIComponent(projectId)}` : '')}`}>← Back to family</Link>
        }
      />
    );
  }

  const backLink = routePath`/families/${familyId}${raw(projectId ? `?project_id=${encodeURIComponent(projectId)}` : '')}`;
  const inheritanceModel = (family?.metadata?.pgt as { inheritance_model?: string } | undefined)
    ?.inheritance_model;
  const inRoi = (pos: number) => pos >= roi.start && pos <= roi.end;

  // Zoom keeps the window centred; pan shifts it by a fraction of its span. Both clamp
  // the left edge at 0 and enforce a minimum span so the view stays sensible.
  const setView = (start: number, end: number) => {
    const s = Math.max(0, Math.round(start));
    setViewport({ start: s, end: Math.round(Math.max(end, s + MIN_SPAN)) });
  };
  const zoom = (factor: number) => {
    const span = window.end - window.start;
    const center = (window.start + window.end) / 2;
    const newSpan = Math.max(MIN_SPAN, span * factor);
    setView(center - newSpan / 2, center + newSpan / 2);
  };
  const pan = (fraction: number) => {
    const span = window.end - window.start;
    let start = window.start + span * fraction;
    let end = window.end + span * fraction;
    if (start < 0) {
      end -= start; // keep the span when bumping against the chromosome start
      start = 0;
    }
    setView(start, end);
  };
  const isDefaultView = viewport === null;

  // Hover tooltip, delegated at the table so we don't attach a handler per cell. Renders
  // the chromosome-view tooltip style for both the Mendel-error badge and the band cells.
  const handleTableMove = (event: React.MouseEvent<HTMLTableElement>) => {
    const target = event.target as HTMLElement;
    const badge = target.closest('.roi-markers-mendel') as HTMLElement | null;
    if (badge) {
      const child = badge.dataset.sample ?? '';
      const qc = qcBySample.get(child);
      // Where the window holds chrX outside the PARs, the QC counted a son on his one X
      // against his mother alone (ENG-19).
      const sonOnOneX = sexBySample.get(child) === 'male' && hemizygousRanges.length > 0;
      if (qc) {
        setTooltip({
          x: event.clientX,
          y: event.clientY,
          node: (
            <div className="roi-markers-tip">
              <div className="roi-markers-tip-head">
                <strong>Mendelian error rate: {(qc.mendel_rate * 100).toFixed(1)}%</strong>
              </div>
              <div className="roi-markers-tip-row">
                {qc.mendel_errors.toLocaleString()} of {qc.informative_sites.toLocaleString()}{' '}
                {sonOnOneX
                  ? `sites where ${child} and the parents he is checked against are genotyped have a genotype he cannot have from them`
                  : `sites where both parents and ${child} are genotyped have a genotype that cannot be inherited from these parents`}{' '}
                (an impossible transmission).
              </div>
              {sonOnOneX ? (
                <div className="roi-markers-tip-row">
                  On chrX outside the PARs a son’s one X is his mother’s: there he is checked against her alone.
                </div>
              ) : null}
              <div className="roi-markers-tip-row roi-markers-tip-region">
                A high rate points to a sample swap, an incorrect pedigree, or genotyping noise rather than a
                real variant.
              </div>
            </div>
          ),
        });
        return;
      }
    }
    const cell = target.closest('.roi-markers-band') as HTMLElement | null;
    if (!cell) {
      setTooltip(null);
      return;
    }
    const pos = Number(cell.dataset.pos);
    const sample = cell.dataset.sample ?? '';
    const lane = cell.dataset.lane ?? '';
    const base = cell.dataset.base ?? '';
    const informative = cell.dataset.informative === '1';
    const noAllele = cell.dataset.noAllele === '1';
    const mendelError = mendelErrorPosBySample.get(sample)?.get(pos) ?? null;
    const blockMismatch = cell.dataset.blockMismatch === '1';
    const site = siteByPos.get(pos);
    const role = roleBySample.get(sample);
    const flank = !inRoi(pos);
    setTooltip({
      x: event.clientX,
      y: event.clientY,
      node: (
        <div className="roi-markers-tip">
          <div className="roi-markers-tip-head">
            <strong>
              {roi.chr}:{pos.toLocaleString()}
            </strong>
            {site && (
              <span className="roi-markers-tip-change">
                {' '}
                <span style={{ color: nucleotideColor(site.ref) }}>{site.ref}</span>
                <span style={{ color: '#94a3b8' }}>›</span>
                <span style={{ color: nucleotideColor(site.alt) }}>{site.alt}</span>
              </span>
            )}
            <span className="roi-markers-tip-region">{flank ? ' · flank' : ' · ROI'}</span>
          </div>
          <div className="roi-markers-tip-row">
            <strong>{sample}</strong>
            {role && <span className="roi-markers-tip-role"> ({role})</span>} · homolog {lane}
          </div>
          <div className="roi-markers-tip-row">
            {noAllele ? (
              'no allele: one copy here, its allele on the other row'
            ) : (
              <>
                allele{' '}
                <span style={{ color: informative ? nucleotideColor(base) : UNINFORMATIVE_COLOR, fontWeight: 700 }}>
                  {base}
                </span>
                {informative ? '' : ' — uninformative (parent-of-origin unresolved here)'}
              </>
            )}
          </div>
          {mendelError && (
            <div className="roi-markers-tip-row roi-markers-tip-mendel">{MENDEL_ERROR_NOTES[mendelError]}</div>
          )}
          {blockMismatch && !mendelError && (
            <div className="roi-markers-tip-row roi-markers-tip-mendel">
              ⚠ Disagrees with the haplotype block — a phasing switch, artifact, or unannotated
              recombination here.
            </div>
          )}
        </div>
      ),
    });
  };

  return (
    <div className="page-shell roi-markers-page space-y-4">
      <div className="space-y-1">
        <Link to={backLink} className="family-roi-link">
          ← Back to {family?.family_id ?? 'family'}
        </Link>
        <h1 className="section-title">ROI marker review — {roi.label}</h1>
        <FamilyPageBanners
          metadata={family?.metadata}
          assemblyScope={{
            name: assemblyName,
            validated: assemblyValidated,
            unavailable: referenceFailed,
            onRetry: retryReference,
          }}
        />
        <p className="segregation-note">
          Informative phased markers for every family member across the ROI {roi.chr}:
          {roi.start.toLocaleString()}–{roi.end.toLocaleString()}. The view opens on the ROI; use the zoom and
          pan controls to widen the window for flanking context. Genotypes are derived from the phased imputed
          data; each marker shows its nucleotide allele on white above a continuous lineage-coloured band
          (blue = paternal, green = maternal, grey = untransmitted/donor), matching the chromosome view. The
          ROI is bracketed by an orange line between members and any flanking markers are dimmed; each member
          shows its two homolog rows — the allele is nucleotide-coloured where the lane is informative and
          light grey where it is not. A call of one allele, such as a man&apos;s one X outside the
          pseudo-autosomal regions, shows its allele once (on the father&apos;s first row, on anyone else&apos;s
          second: a son&apos;s maternal row) and {NO_ALLELE} on the other row. A cell is shaded{' '}
          <strong>darker orange</strong> for an impossible
          transmission (a Mendelian error) and <strong>lighter orange</strong> when the marker merely
          disagrees with the haplotype block, and a very light grey when the genotype is homozygous
          (uninformative for phasing). Use it to re-check the ROI for errors, artefacts and recombination.
        </p>
        <HaplotypeLegend inheritanceModel={inheritanceModel} />
      </div>

      <div className="roi-markers-controls" role="group" aria-label="Adjust the marker view">
        <div className="roi-markers-zoom">
          <button type="button" className="button-ghost" onClick={() => pan(-0.5)} aria-label="Pan left" title="Pan left">
            ←
          </button>
          <button type="button" className="button-ghost" onClick={() => zoom(2)} aria-label="Zoom out" title="Zoom out">
            −
          </button>
          <button type="button" className="button-ghost" onClick={() => zoom(0.5)} aria-label="Zoom in" title="Zoom in">
            +
          </button>
          <button type="button" className="button-ghost" onClick={() => pan(0.5)} aria-label="Pan right" title="Pan right">
            →
          </button>
          <button
            type="button"
            className="button-ghost"
            onClick={() => setViewport(null)}
            disabled={isDefaultView}
            title="Reset to the ROI"
          >
            Reset
          </button>
        </div>
        <span className="roi-markers-range">
          {window.chr}:{window.start.toLocaleString()}–{window.end.toLocaleString()}{' '}
          <span className="roi-markers-range-span">
            ({Math.round((window.end - window.start) / 1000).toLocaleString()} kb)
          </span>
          {phasedFetching && <span className="roi-markers-updating"> · updating…</span>}
        </span>
      </div>

      {haploFailed ? (
        <QueryFailure
          what="the haplotype blocks at the ROI"
          error={haploError}
          onRetry={() => void refetchHaplo()}
          consequence="The markers are not coloured by the inherited haplotype."
        />
      ) : null}

      {/* The markers' failure is said in their place: it read "0 markers in view · 0
          informative for embryos", an uninformative ROI (#607). */}
      {phasedFailed ? (
        <QueryFailure
          what="the phased markers at the ROI"
          error={phasedError}
          onRetry={() => void refetchPhased()}
          consequence="Which markers are informative for the embryos is unknown."
        />
      ) : (
      <>
      <div className="roi-markers-summary">
        <span className="table-chip">{allSites.length.toLocaleString()} markers in view</span>
        <span className="table-chip">{informativeSites.length.toLocaleString()} informative for embryos</span>
        {phased?.truncated && (
          <span className="table-chip table-chip--critical">truncated — zoom in for the full set</span>
        )}
      </div>

      {shownSites.length < informativeSites.length && (
        <div className="segregation-note segregation-note--error">
          Showing the first {shownSites.length.toLocaleString()} of {informativeSites.length.toLocaleString()} informative
          markers.
        </div>
      )}

      <div className="roi-markers-table-wrap">
        <table
          className="roi-markers-table"
          onMouseMove={handleTableMove}
          onMouseLeave={() => setTooltip(null)}
        >
          <thead>
            <tr>
              <th className="roi-markers-corner">Member</th>
              {shownSites.map((site) => (
                <th
                  key={site.pos}
                  className={`roi-markers-poscol${inRoi(site.pos) ? ' roi-markers-poscol--roi' : ''}`}
                  title={`${roi.chr}:${site.pos.toLocaleString()} ${site.ref}>${site.alt}${
                    inRoi(site.pos) ? '' : ' (flank)'
                  }`}
                >
                  {site.pos.toLocaleString()}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sampleOrder.map((sample, memberIdx) => {
              const segs = segmentsBySample.get(sample) ?? [];
              const role = roleBySample.get(sample);
              const qc = qcBySample.get(sample);
              const embryo = role === 'embryo';
              // Two stacked bands per member: homolog 1 (hap1) above homolog 2 (hap2),
              // each coloured by lineage with its inherited allele on the band.
              const bandRows = LANES.map((lane, laneIdx) => (
                <tr
                  key={`${sample}-${lane}`}
                  className={`roi-markers-bandrow${embryo ? ' roi-markers-row--embryo' : ''}`}
                >
                  {laneIdx === 0 && (
                    <th className="roi-markers-namecol" scope="rowgroup" rowSpan={LANES.length}>
                      <span className="roi-markers-name">{sample}</span>
                      {role && <span className="roi-markers-role"> ({role})</span>}
                      {qc && qc.mendel_errors > 0 && (
                        <span className="roi-markers-mendel" data-sample={sample}>
                          {' '}
                          ⚠{(qc.mendel_rate * 100).toFixed(1)}%
                        </span>
                      )}
                    </th>
                  )}
                  {shownSites.map((site) => {
                    const seg = coveringSegment(segs, site.pos);
                    const gt = site.gts[memberIdx] ?? '';
                    const oneCopyMan = sexBySample.get(sample) === 'male' && inRanges(hemizygousRanges, site.pos);
                    const rows = rowAlleles(gt, oneCopyMan, role);
                    const idx = rows[lane];
                    // The other row of a call of one allele has none: it is not a missing one.
                    const noAllele = idx === null;
                    const base = noAllele ? NO_ALLELE : idx ? alleleBase(idx, site.ref, site.alt) : '·';
                    const bg = laneColor(seg, lane);
                    // Colour the allele by nucleotide only where this member's lane is
                    // informative (a resolved marker); otherwise mute it to grey.
                    const laneValue = markerLanes.get(sample)?.get(site.pos)?.[lane] ?? null;
                    const informative = laneValue !== null && !noAllele;
                    const mendelError = mendelErrorPosBySample.get(sample)?.has(site.pos) ?? false;
                    // Raw marker disagrees with the cleaned haplotype block here.
                    const blockMismatch = !noAllele && laneMatchesBlock(seg, laneValue, lane) === false;
                    // Both homologs carry the same allele → uninformative for phasing. One
                    // allele is one copy, not two.
                    const homozygous = /^\d+$/.test(rows.hap1 ?? '') && rows.hap1 === rows.hap2;
                    // Orange emphasises a suspect marker (impossible transmission or a
                    // block disagreement); homozygous (uninformative) sites are a faint grey.
                    const flagClass = mendelError
                      ? ' roi-markers-band--mendel'
                      : blockMismatch
                        ? ' roi-markers-band--block-mismatch'
                        : homozygous
                          ? ' roi-markers-band--homozygous'
                          : '';
                    return (
                      <td
                        key={site.pos}
                        className={`roi-markers-band${inRoi(site.pos) ? '' : ' roi-markers-band--flank'}${flagClass}`}
                        data-pos={site.pos}
                        data-sample={sample}
                        data-lane={laneIdx + 1}
                        data-base={base}
                        data-informative={informative ? '1' : '0'}
                        data-no-allele={noAllele ? '1' : '0'}
                        data-mendel={mendelError ? '1' : '0'}
                        data-block-mismatch={blockMismatch ? '1' : '0'}
                      >
                        <span
                          className={`roi-markers-allele${informative ? '' : ' roi-markers-allele--muted'}`}
                          style={{ color: informative ? nucleotideColor(base) : UNINFORMATIVE_COLOR }}
                        >
                          {base}
                        </span>
                        {/* The lineage colour is a thin band line under the nucleotide. */}
                        <span className="roi-markers-band-line" style={{ background: bg || undefined }} />
                      </td>
                    );
                  })}
                </tr>
              ));
              // A thin spacer row separates members: whitespace in the flanks, an orange
              // line through the ROI columns so the region reads as a continuous bracket.
              if (memberIdx > 0) {
                bandRows.unshift(
                  <tr key={`${sample}-spacer`} className="roi-markers-spacer" aria-hidden="true">
                    <td className="roi-markers-spacer-name" />
                    {shownSites.map((site) => (
                      <td
                        key={site.pos}
                        className={`roi-markers-spacer-cell${
                          inRoi(site.pos) ? ' roi-markers-spacer-cell--roi' : ''
                        }`}
                      />
                    ))}
                  </tr>,
                );
              }
              return bandRows;
            })}
          </tbody>
        </table>
      </div>
      </>
      )}

      {tooltip && (
        <VizTooltip x={tooltip.x} y={tooltip.y}>
          {tooltip.node}
        </VizTooltip>
      )}
    </div>
  );
};

export default FamilyRoiMarkersPage;
