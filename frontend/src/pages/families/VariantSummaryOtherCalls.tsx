import React from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type {
  FamilyMitoDNAAnalysisOut,
  FamilyParaphaseTableOut,
  FamilyRepeatExpansionTableOut,
} from '../../lib/apiSchema.generated';
import { repeatStatusLabel } from '../../lib/repeatStatus';
import type { ApiRepeatStatus } from '../../lib/apiTypes';
import { VariantSummarySectionState } from './VariantSummarySectionState';

/**
 * The variant summary's sections of the family's other calls: repeat expansions, Paraphase and
 * mtDNA. Each reads its analysis page's own response (under the same query key, so a visit to
 * either page reads it once) and counts what that page shows, by the statuses it gives.
 */

/** A table of counts: one row per label, one column per sample. */
const CountTable: React.FC<{
  label: string;
  caption: string;
  samples: string[];
  rows: { key: string; label: string; counts: (number | string)[] }[];
}> = ({ label, caption, samples, rows }) => (
  <div className="analysis-results-card overflow-x-auto">
    <table className="analysis-table table-sticky" aria-label={caption}>
      <thead>
        <tr>
          <th>{label}</th>
          {samples.map((sample) => (
            <th key={sample} className="text-center">
              {sample}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key}>
            <td>{row.label}</td>
            {row.counts.map((count, index) => (
              <td key={samples[index]} className="text-center">
                {typeof count === 'number'
                  ? count.toLocaleString('en-US')
                  : count}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const projectParams = (projectId?: string | null) =>
  projectId ? { project_id: projectId } : undefined;

// The repeat page's statuses, most severe first.
const REPEAT_STATUSES: ApiRepeatStatus[] = [
  'pathogenic',
  'premutation',
  'intermediate',
  'review',
  'not_assessed',
  'normal',
  'unknown',
];

export const RepeatExpansionSummary: React.FC<{
  familyId: string;
  projectId?: string | null;
}> = ({ familyId, projectId }) => {
  const query = useQuery<FamilyRepeatExpansionTableOut>({
    queryKey: ['family', familyId, 'repeat-expansions', projectId || null],
    queryFn: async () =>
      (
        await api.get(apiPath`/families/${familyId}/repeat-expansions`, {
          params: projectParams(projectId),
        })
      ).data as FamilyRepeatExpansionTableOut,
  });
  const table = query.data;
  const samples = table ? table.samples.map((member) => member.sample_id) : [];
  const rows = table
    ? REPEAT_STATUSES.map((status) => ({
        key: status,
        label: status === 'review' ? 'Review' : repeatStatusLabel(status),
        counts: samples.map(
          (sample) =>
            table.loci.filter((locus) => locus.calls[sample]?.status === status)
              .length
        ),
      })).filter((row) => row.counts.some((count) => count > 0))
    : [];
  return (
    <section id="repeat-expansions" className="analysis-panel space-y-4">
      <h2 className="section-title">Repeat expansions</h2>
      {!table ? (
        <VariantSummarySectionState
          what="repeat expansions"
          loading={query.isLoading}
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : (
        <>
          <p className="analysis-count">
            {table.loci.length.toLocaleString('en-US')} repeat loci with calls.
            Each member’s calls by the status the repeat page gives them,
            against the repeat catalogue’s thresholds.
          </p>
          <CountTable
            label="Call status"
            caption="Repeat calls per member and status"
            samples={samples}
            rows={[
              {
                key: 'called',
                label: 'Loci called',
                counts: samples.map(
                  (sample) =>
                    table.loci.filter((locus) => locus.calls[sample]).length
                ),
              },
              ...rows,
            ]}
          />
        </>
      )}
    </section>
  );
};

// The Paraphase page's clinical statuses, most severe first, with its labels.
const PARAPHASE_STATUSES: { key: string; label: string }[] = [
  { key: 'pathogenic', label: 'Pathogenic' },
  { key: 'carrier', label: 'Carrier' },
  { key: 'review', label: 'Review' },
  { key: 'normal', label: 'Normal' },
  { key: 'no_call', label: 'No-call' },
  { key: 'none', label: 'No status' },
];

export const ParaphaseSummary: React.FC<{
  familyId: string;
  projectId?: string | null;
}> = ({ familyId, projectId }) => {
  const query = useQuery<FamilyParaphaseTableOut>({
    queryKey: ['family', familyId, 'paraphase', projectId || null],
    queryFn: async () =>
      (
        await api.get(apiPath`/families/${familyId}/paraphase`, {
          params: projectParams(projectId),
        })
      ).data as FamilyParaphaseTableOut,
  });
  const table = query.data;
  const samples = table ? table.samples.map((member) => member.sample_id) : [];
  const statusOf = (
    gene: FamilyParaphaseTableOut['genes'][number],
    sample: string
  ) =>
    gene.samples[sample]
      ? gene.samples[sample].clinical_status || 'none'
      : null;
  return (
    <section id="paraphase" className="analysis-panel space-y-4">
      <h2 className="section-title">Paraphase</h2>
      {!table ? (
        <VariantSummarySectionState
          what="Paraphase results"
          loading={query.isLoading}
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : (
        <>
          <p className="analysis-count">
            {table.genes.length.toLocaleString('en-US')} genes or regions with
            results; of them{' '}
            {table.genes
              .filter((gene) => gene.has_copy_number_signal)
              .length.toLocaleString('en-US')}{' '}
            with a copy-number change. Each member’s results by the status the
            Paraphase page gives them.
          </p>
          <CountTable
            label="Status"
            caption="Paraphase results per member and status"
            samples={samples}
            rows={[
              {
                key: 'resolved',
                label: 'Genes with a result',
                counts: samples.map(
                  (sample) =>
                    table.genes.filter((gene) => gene.samples[sample]).length
                ),
              },
              ...PARAPHASE_STATUSES.map((status) => ({
                key: status.key,
                label: status.label,
                counts: samples.map(
                  (sample) =>
                    table.genes.filter(
                      (gene) => statusOf(gene, sample) === status.key
                    ).length
                ),
              })).filter((row) => row.counts.some((count) => count > 0)),
            ]}
          />
        </>
      )}
    </section>
  );
};

const formatDepth = (depth: number | null | undefined) =>
  depth == null ? '–' : `${Math.round(depth)}×`;

export const MitoDnaSummary: React.FC<{
  familyId: string;
  projectId?: string | null;
}> = ({ familyId, projectId }) => {
  const query = useQuery<FamilyMitoDNAAnalysisOut>({
    queryKey: ['family', familyId, 'mitochondrial-dna', projectId || null],
    queryFn: async () =>
      (
        await api.get(apiPath`/families/${familyId}/mitochondrial-dna`, {
          params: projectParams(projectId),
        })
      ).data as FamilyMitoDNAAnalysisOut,
  });
  const analysis = query.data;
  const samples = analysis
    ? analysis.samples.map((sample) => sample.sample_id)
    : [];
  const zygosityCount = (sample: string, zygosity: string) =>
    (analysis?.variants ?? []).filter(
      (variant) => variant.calls[sample]?.zygosity === zygosity
    ).length;
  return (
    <section id="mitochondrial-dna" className="analysis-panel space-y-4">
      <h2 className="section-title">Mitochondrial DNA</h2>
      {!analysis ? (
        <VariantSummarySectionState
          what="mtDNA analysis"
          loading={query.isLoading}
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : (
        <>
          <p className="analysis-count">
            {analysis.variants.length.toLocaleString('en-US')} chrM variants and{' '}
            {analysis.structural_variants.length.toLocaleString('en-US')} chrM
            deletions or duplications. Homoplasmic from{' '}
            {Math.round(analysis.homoplasmy_threshold * 100)} % heteroplasmy,
            low-level below {Math.round(analysis.heteroplasmy_threshold * 100)}{' '}
            %, as the mtDNA analysis reads them.
          </p>
          <CountTable
            label="Per member"
            caption="mtDNA calls per member"
            samples={samples}
            rows={[
              {
                key: 'haplogroup',
                label: 'Haplogroup',
                counts: analysis.samples.map(
                  (sample) => sample.haplogroup || '–'
                ),
              },
              {
                key: 'depth',
                label: 'Mean depth',
                counts: analysis.samples.map((sample) =>
                  formatDepth(sample.coverage.mean_depth)
                ),
              },
              {
                key: 'homoplasmic',
                label: 'Homoplasmic',
                counts: samples.map((sample) =>
                  zygosityCount(sample, 'homoplasmic')
                ),
              },
              {
                key: 'heteroplasmic',
                label: 'Heteroplasmic',
                counts: samples.map((sample) =>
                  zygosityCount(sample, 'heteroplasmic')
                ),
              },
              {
                key: 'low_level',
                label: 'Low-level',
                counts: samples.map((sample) =>
                  zygosityCount(sample, 'low_level')
                ),
              },
              {
                key: 'svs',
                label: 'Deletions or duplications',
                counts: samples.map(
                  (sample) =>
                    analysis.structural_variants.filter(
                      (variant) => variant.calls[sample]
                    ).length
                ),
              },
            ]}
          />
        </>
      )}
    </section>
  );
};
