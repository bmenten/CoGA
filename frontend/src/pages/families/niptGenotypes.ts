import type { NiptTrioOut } from '../../lib/apiSchema.generated';
import type { GenotypeGridEntry, GenotypeRow, GenotypeTone } from './FamilyGenotypeGrid';
import { FATHER_STATE_LABELS, pct } from './niptClassification';
import type { FamilyMember, NiptClassification, SmallVariant } from './smallVariantSearch';

/**
 * The samples a monogenic NIPT analysis reads (the NIPT list's `trio`): the maternal plasma, the
 * father, and the fetus's placeholder member, which holds no data.
 */
export type NiptTrio = NiptTrioOut;

/** The plasma's role on the NIPT list, in place of the member's own (the mother's, mostly). */
export const NIPT_PLASMA_ROLE = 'plasma (cfDNA)';

/**
 * The plasma's call column. Its reads are a mixture of the mother's and the fetus's DNA, so it has
 * no genotype: the caller's GT (Mutect2 writes 0/1 for every call) is not shown.
 */
export const NIPT_PLASMA_CALL = 'mixture';

// The father's genotype class, read from his allele depths, as a genotype's tone.
const FATHER_STATE_TONE: Record<string, GenotypeTone> = {
  hom_alt: 'alert',
  het: 'warn',
  hom_ref: 'ref',
  absent: 'ref',
  low_vaf: 'review',
  low_support: 'review',
  missing: 'na',
};

const alleleDepths = (depth?: number | null, altReads?: number | null): string =>
  depth != null && altReads != null ? `${depth - altReads},${altReads}` : '—';

const fraction = (value?: number | null): string => (value == null ? '—' : value.toFixed(2));

/** The plasma's row: its reads as the analysis read them, never a genotype. */
const plasmaRow = (member: FamilyMember, nipt: NiptClassification): GenotypeRow => {
  const role = { ...member, role: NIPT_PLASMA_ROLE };
  let detail: string | undefined;
  if (nipt.cf_call_variant_id) {
    detail = `Read from the plasma's call on ${nipt.cf_call_variant_id}: its VCF writes this allele another way.`;
  } else if (nipt.flags.includes('plasma_call_overlaps')) {
    detail = "The plasma's calls carry only part of this allele's bases: read as no call.";
  }
  if (nipt.cf_depth_estimated) {
    return {
      member: role,
      tone: 'na',
      call: 'no call',
      values: { dp: nipt.cf_depth != null ? `~${nipt.cf_depth}` : '—', ad: '—', af: '—' },
      detail:
        detail ??
        (nipt.cf_depth != null
          ? 'No plasma call here: the depth is its target coverage.'
          : 'No plasma call here, outside the targets.'),
    };
  }
  return {
    member: role,
    tone: 'info',
    call: NIPT_PLASMA_CALL,
    values: {
      dp: nipt.cf_depth ?? '—',
      ad: alleleDepths(nipt.cf_depth, nipt.cf_alt_reads),
      af: fraction(nipt.observed_vaf),
    },
    detail,
  };
};

/** The father's row: his genotype class from his allele depths, not his caller's GT. */
const fatherRow = (member: FamilyMember, nipt: NiptClassification): GenotypeRow => {
  const state = nipt.father_state ?? 'missing';
  let detail: string | undefined;
  if (nipt.father_call_variant_id) {
    detail = `Read from his call on ${nipt.father_call_variant_id}: his VCF writes this allele another way.`;
  } else if (nipt.flags.includes('father_call_overlaps')) {
    detail = "His calls carry only part of this allele's bases: read as no call.";
  }
  return {
    member,
    tone: FATHER_STATE_TONE[state] ?? 'na',
    call: FATHER_STATE_LABELS[state] ?? state,
    values: {
      dp: nipt.father_depth ?? '—',
      ad: alleleDepths(nipt.father_depth, nipt.father_alt_reads),
      af: fraction(nipt.father_vaf),
    },
    detail,
  };
};

/**
 * A NIPT variant's rows of the genotype grid. The plasma and the father show the reads the
 * analysis classified with (also where it read a call off another record, which the variant's
 * own record does not hold); the fetus, which has no sample, has no row: what the analysis
 * infers of it is in the NIPT block. Any other member keeps its own call (`ownRow`).
 */
export const niptGenotypeEntries = (
  nipt: NiptClassification,
  members: FamilyMember[],
  trio: NiptTrio,
  ownRow: (member: FamilyMember) => GenotypeRow,
): GenotypeGridEntry[] =>
  members
    .filter((member) => member.sample_id !== trio.fetus_sample_id)
    .map((member) => {
      if (member.sample_id === trio.cfdna_sample_id) return plasmaRow(member, nipt);
      if (member.sample_id === trio.father_sample_id) return fatherRow(member, nipt);
      return ownRow(member);
    });

export interface CompactGenotype {
  sampleId: string;
  affected?: boolean | null;
  role?: string | null;
  gt: string;
}

/**
 * The table's short form of the same rows: the plasma's allele fraction and the father's genotype
 * class, always; any other member's call where it has one. No fetus.
 */
export const niptCompactGenotypes = (
  variant: SmallVariant & { nipt: NiptClassification },
  members: FamilyMember[],
  trio: NiptTrio,
): CompactGenotype[] =>
  members
    .filter((member) => member.sample_id !== trio.fetus_sample_id)
    .map((member) => {
      const nipt = variant.nipt;
      let gt: string;
      if (member.sample_id === trio.cfdna_sample_id) {
        gt = nipt.cf_depth_estimated ? 'plasma: no call' : `plasma ${pct(nipt.observed_vaf)}`;
      } else if (member.sample_id === trio.father_sample_id) {
        const state = nipt.father_state ?? 'missing';
        gt = `father: ${FATHER_STATE_LABELS[state] ?? state}`;
      } else {
        gt = variant.genotypes.find((entry) => entry.sample === member.sample_id)?.gt || '—';
      }
      return { sampleId: member.sample_id, affected: member.affected, role: member.role, gt };
    })
    .filter((entry) => entry.gt !== '—');
