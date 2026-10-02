import type {
  ApiFamilyMemberDetail,
  ApiFamilyRecord,
  ApiFamilyRegionOfInterest,
  ApiHpoAnnotation,
  ApiHpoTerm,
} from '../../lib/apiTypes';
import { sortTagDefinitions, type SmallVariantTagDefinition } from './smallVariantSearch';
import type {
  CarrierStatus,
  ClinicalStatus,
  CoupleDraft,
  MemberDetailDraft,
  ParentChildDraft,
  RelativeDraft,
  StructureMemberDraft,
} from './familyDetailTypes';
import { ROLE_OPTIONS } from './familyDetailConstants';

export const sampleKey = (sampleId: string): string => sampleId.trim().toLowerCase();

export const formatHpoTermOption = (term: ApiHpoTerm): string => `${term.hpo_id} ${term.label}`;

export const clinicalStatusForMember = (member: {
  clinical_status?: string | null;
  affected?: boolean;
}): ClinicalStatus => {
  if (member.clinical_status === 'affected' || member.clinical_status === 'unaffected') {
    return member.clinical_status;
  }
  if (member.affected) return 'affected';
  return 'unknown';
};

export const carrierStatusForMember = (member: {
  carrier_status?: string | boolean | null;
}): CarrierStatus => {
  if (member.carrier_status === 'carrier') return 'carrier';
  if (member.carrier_status === 'not_carrier') return 'not_carrier';
  if (member.carrier_status === true) return 'carrier';
  return 'unknown';
};

export const hpoTooltip = (annotation: ApiHpoAnnotation): string => {
  return [
    `${annotation.hpo_id}: ${annotation.definition || annotation.label}`,
    annotation.note ? `Note: ${annotation.note}` : '',
    annotation.evidence ? `Evidence: ${annotation.evidence}` : '',
  ]
    .filter(Boolean)
    .join(' | ');
};

export const memberDraftFromFamilyMember = (member: ApiFamilyRecord['members'][number]): StructureMemberDraft => ({
  sample_id: member.sample_id,
  sex: member.sex === 'male' || member.sex === 'female' ? member.sex : 'und',
  role: ROLE_OPTIONS.includes(member.role as StructureMemberDraft['role'])
    ? (member.role as StructureMemberDraft['role'])
    : 'relative',
  clinical_status: clinicalStatusForMember(member),
  carrier_status: carrierStatusForMember(member),
  carrier_type: member.carrier_type ?? '',
  isNew: false,
  removed: member.active === false,
});

// The member dialog's draft as stored, before any edit.
export const memberDetailDraftFromDetail = (detail: ApiFamilyMemberDetail): MemberDetailDraft => ({
  sample_id: detail.member.sample_id,
  sex: detail.member.sex === 'male' || detail.member.sex === 'female' ? detail.member.sex : 'und',
  role: ROLE_OPTIONS.includes(detail.member.role as StructureMemberDraft['role'])
    ? (detail.member.role as StructureMemberDraft['role'])
    : 'relative',
  clinical_status: clinicalStatusForMember(detail.member),
  carrier_status: carrierStatusForMember(detail.member),
  carrier_type: detail.member.carrier_type ?? '',
  father_id: detail.father_id ?? '',
  mother_id: detail.mother_id ?? '',
});

// Typed as a record so that a field added to the draft cannot be left out of the comparison.
const MEMBER_DETAIL_FIELDS: Record<keyof MemberDetailDraft, true> = {
  sample_id: true,
  sex: true,
  role: true,
  clinical_status: true,
  carrier_status: true,
  carrier_type: true,
  father_id: true,
  mother_id: true,
};

export const sameMemberDetailDraft = (a: MemberDetailDraft, b: MemberDetailDraft): boolean =>
  (Object.keys(MEMBER_DETAIL_FIELDS) as (keyof MemberDetailDraft)[]).every(
    (field) => a[field] === b[field],
  );

export const parentChildDraftsFromRelationships = (family: ApiFamilyRecord | undefined): ParentChildDraft[] => {
  const relationships = family?.relationships?.filter(
    (relationship) => relationship.relationship_type === 'parent_child',
  );
  return (relationships || []).map((relationship, index) => ({
    id: relationship.id || `parent-child-${index}`,
    parent: relationship.sample_id_a,
    child: relationship.sample_id_b,
    parent_role:
      relationship.role_a === 'father' || relationship.role_a === 'mother'
        ? relationship.role_a
        : 'parent',
  }));
};

export const parentsForSample = (
  family: ApiFamilyRecord | undefined,
  sampleId: string,
): { father_id: string; mother_id: string } => {
  const childKey = sampleKey(sampleId);
  return (family?.relationships || []).reduce(
    (parents, relationship) => {
      if (
        relationship.relationship_type !== 'parent_child' ||
        sampleKey(relationship.sample_id_b) !== childKey
      ) {
        return parents;
      }
      if (relationship.role_a === 'father') {
        parents.father_id = relationship.sample_id_a;
      } else if (relationship.role_a === 'mother') {
        parents.mother_id = relationship.sample_id_a;
      }
      return parents;
    },
    { father_id: '', mother_id: '' },
  );
};

export const coupleDraftsFromRelationships = (family: ApiFamilyRecord | undefined): CoupleDraft[] =>
  (family?.relationships || [])
    .filter((relationship) => relationship.relationship_type === 'couple')
    .map((relationship, index) => ({
      id: relationship.id || `couple-${index}`,
      partnerA: relationship.sample_id_a,
      partnerB: relationship.sample_id_b,
      context: typeof relationship.metadata?.context === 'string' ? relationship.metadata.context : '',
    }));

/** The links of unknown degree: `sample_id_b` is related through `sample_id_a`. */
export const relativeDraftsFromRelationships = (family: ApiFamilyRecord | undefined): RelativeDraft[] =>
  (family?.relationships || [])
    .filter((relationship) => relationship.relationship_type === 'relative')
    .map((relationship, index) => ({
      id: relationship.id || `relative-${index}`,
      member: relationship.sample_id_b,
      relatedTo: relationship.sample_id_a,
    }));

export interface SampleSequencingQc {
  /** Package-relative path of the rendered QC report (NanoPlot, MultiQC or Qualimap). */
  report?: string;
  reads?: {
    mean_read_length?: number;
    median_read_length?: number;
    mean_read_quality?: number;
    median_read_quality?: number;
    read_length_n50?: number;
    read_count?: number;
    total_bases?: number;
  };
  depth?: {
    mean_depth?: number;
    mito_mean_depth?: number;
  };
  /** Qualimap bamqc headline numbers (the PGT pipeline runs it on every sample). */
  alignment?: {
    mapped_reads_percent?: number;
    duplicated_reads_percent?: number;
    mean_mapping_quality?: number;
  };
  /** The sex the pipeline read off the reads (ngs-bits), apart from the recorded sex. */
  sex_check?: {
    method?: string;
    inferred_sex?: string;
    ratio_chry_chrx?: number;
  };
  /** Per-embryo QC from the PGT pipeline, as percentages. */
  pgt?: {
    allele_dropout_rate?: number;
    allele_dropin_rate?: number;
    mendelian_concordance?: number;
    mendelian_concordance_imputed?: number;
  };
}

/**
 * Sequencing QC recorded on a sample at package import, or undefined when the family
 * was imported without QC outputs. `sample_metadata` is typed but untyped-valued, so
 * narrow it here rather than casting at each use site.
 */
export const sequencingQcForMember = (member: {
  sample_metadata?: Record<string, unknown> | null;
}): SampleSequencingQc | undefined => {
  const qc = member.sample_metadata?.sequencing_qc;
  return qc && typeof qc === 'object' ? (qc as SampleSequencingQc) : undefined;
};

/** Read lengths in kb, which is how long-read N50s are normally quoted. */
const formatKilobases = (bases: number): string =>
  `${(bases / 1000).toFixed(bases < 10_000 ? 1 : 0)} kb`;

/**
 * The single headline number for the members-table chip: mean depth.
 *
 * The chip sits in a narrow column beside the Status pill and has to stay that
 * compact, so it carries one measurement and the QC verdict; every other metric is on
 * hover (`formatSequencingQcDetail`). Depth is the number an interpreter reads first,
 * and the verdict beside it covers the case where a *different* metric is the one
 * failing.
 */
export const formatSequencingQcSummary = (qc: SampleSequencingQc | undefined): string | null => {
  if (!qc) return null;
  if (typeof qc.depth?.mean_depth === 'number') {
    return `${qc.depth.mean_depth.toFixed(1)}x`;
  }
  if (typeof qc.reads?.read_length_n50 === 'number') {
    return `N50 ${formatKilobases(qc.reads.read_length_n50)}`;
  }
  return null;
};

/** Every recorded QC metric, spelled out for a tooltip. */
export const formatSequencingQcDetail = (qc: SampleSequencingQc | undefined): string | null => {
  if (!qc) return null;
  const parts: string[] = [];
  if (typeof qc.depth?.mean_depth === 'number') {
    parts.push(`mean depth ${qc.depth.mean_depth.toFixed(1)}x`);
  }
  if (typeof qc.depth?.mito_mean_depth === 'number') {
    parts.push(`chrM depth ${Math.round(qc.depth.mito_mean_depth).toLocaleString()}x`);
  }
  if (typeof qc.reads?.read_length_n50 === 'number') {
    parts.push(`read-length N50 ${Math.round(qc.reads.read_length_n50).toLocaleString()} bp`);
  }
  if (typeof qc.reads?.median_read_length === 'number') {
    parts.push(`median read length ${Math.round(qc.reads.median_read_length).toLocaleString()} bp`);
  }
  if (typeof qc.reads?.median_read_quality === 'number') {
    parts.push(`median read quality Q${qc.reads.median_read_quality.toFixed(0)}`);
  }
  if (typeof qc.reads?.read_count === 'number') {
    parts.push(`${Math.round(qc.reads.read_count).toLocaleString()} reads`);
  }
  if (typeof qc.reads?.total_bases === 'number') {
    parts.push(`${(qc.reads.total_bases / 1e9).toFixed(1)} Gb total`);
  }
  if (typeof qc.alignment?.mapped_reads_percent === 'number') {
    parts.push(`${qc.alignment.mapped_reads_percent.toFixed(1)}% mapped`);
  }
  if (typeof qc.alignment?.duplicated_reads_percent === 'number') {
    parts.push(`${qc.alignment.duplicated_reads_percent.toFixed(1)}% duplicates`);
  }
  if (typeof qc.pgt?.allele_dropout_rate === 'number') {
    parts.push(`ADO ${qc.pgt.allele_dropout_rate.toFixed(1)}%`);
  }
  if (typeof qc.pgt?.allele_dropin_rate === 'number') {
    parts.push(`ADI ${qc.pgt.allele_dropin_rate.toFixed(1)}%`);
  }
  const concordance = qc.pgt?.mendelian_concordance;
  const imputedConcordance = qc.pgt?.mendelian_concordance_imputed;
  if (typeof concordance === 'number' || typeof imputedConcordance === 'number') {
    const before = typeof concordance === 'number' ? `${concordance.toFixed(1)}% before` : null;
    const after = typeof imputedConcordance === 'number' ? `${imputedConcordance.toFixed(1)}% after` : null;
    parts.push(`Mendelian concordance ${[before, after].filter(Boolean).join(', ')} imputation`);
  }
  if (qc.sex_check?.inferred_sex) {
    parts.push(`sex read as ${qc.sex_check.inferred_sex}${qc.sex_check.method ? ` (${qc.sex_check.method})` : ''}`);
  }
  return parts.length ? parts.join(' · ') : null;
};

export const formatRegion = (roi: ApiFamilyRegionOfInterest): string => {
  const chrom = roi.chr.startsWith('chr') ? roi.chr : `chr${roi.chr}`;
  return `${chrom}:${roi.start.toLocaleString()}-${roi.end.toLocaleString()}`;
};

export const getReviewSummaryTags = (
  tagCounts: Record<string, number> | undefined,
  tagDefinitions: SmallVariantTagDefinition[],
) => {
  const counts = tagCounts || {};
  const activeKeys = new Set<string>();
  const knownTags = sortTagDefinitions(tagDefinitions)
    .map((tag) => {
      const count = counts[tag.key] ?? 0;
      if (count <= 0) return null;
      activeKeys.add(tag.key);
      return {
        key: tag.key,
        label: tag.label,
        count,
      };
    })
    .filter((entry): entry is { key: string; label: string; count: number } => entry !== null);
  const unknownTags = Object.entries(counts)
    .filter(([key, count]) => count > 0 && !activeKeys.has(key))
    .sort(([left], [right]) => left.localeCompare(right, undefined, { sensitivity: 'base' }))
    .map(([key, count]) => ({
      key,
      label: key,
      count,
    }));
  return [...knownTags, ...unknownTags];
};
