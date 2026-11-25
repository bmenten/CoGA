import React from 'react';
import type { NiptDiagnosticCandidateOut, NiptDiagnosticCandidatesOut } from '../../lib/apiSchema.generated';
import { RECESSIVE_STATUS_LABELS, pct, recessiveProbabilitiesText } from './niptClassification';

/** How the report names a candidate's flags. */
export const DIAGNOSTIC_FLAG_LABELS: Record<string, string> = {
  repeat_context: 'indel in a repeat',
  clinvar_pathogenic: 'ClinVar pathogenic',
};

/** One line of the criteria the report and the signed record name. */
export const describeDiagnosticCriteria = (criteria: NiptDiagnosticCandidatesOut['criteria']): string =>
  `${criteria.reference}: rare at ${pct(criteria.rare_max_af_dominant)} or less (recessive alleles ${pct(
    criteria.rare_max_af_recessive,
  )}; an allele without a population frequency in ${criteria.cohort_common_min_samples} cfDNA samples or more is not rare), ` +
  `a ${criteria.effect_impacts.join(' or ')} impact or a SpliceAI score of ${criteria.min_spliceai} or more; ` +
  `ClinVar pathogenic (no benign or conflicting assertion, frequency ${pct(criteria.clinvar_max_af)} or less) passes the ` +
  'effect filter and counts as rare for the recessive pairing alone.';

interface ViewProps {
  title: string;
  description: string;
  candidates: NiptDiagnosticCandidateOut[];
  renderVariant: (candidate: NiptDiagnosticCandidateOut) => React.ReactNode;
}

const View: React.FC<ViewProps> = ({ title, description, candidates, renderVariant }) => (
  <div className="report-section report-nipt-group">
    <h3 className="report-subheading">
      {title} ({candidates.length})
    </h3>
    <p className="report-paragraph">{description}</p>
    {candidates.length ? (
      candidates.map((candidate) => (
        <div key={candidate.variant._id}>
          {candidate.confidence === 'low' || candidate.flags.length ? (
            <p className="report-paragraph">
              {candidate.confidence === 'low' ? <strong>Low confidence. </strong> : null}
              {candidate.flags.map((flag) => DIAGNOSTIC_FLAG_LABELS[flag] ?? flag).join(', ')}
            </p>
          ) : null}
          {renderVariant(candidate)}
        </div>
      ))
    ) : (
      <p className="report-paragraph report-empty">None.</p>
    )}
  </div>
);

interface NiptDiagnosticCandidatesSectionProps {
  data: NiptDiagnosticCandidatesOut;
  renderVariant: (candidate: NiptDiagnosticCandidateOut) => React.ReactNode;
}

/**
 * The locked diagnostic candidates of the report's scope (REQ-NIPT-020): per view, the variants
 * the R validation's criteria leave for further investigation. The sign-out freezes the criteria
 * and the candidates of the analysis's diagnostic panel.
 */
const NiptDiagnosticCandidatesSection: React.FC<NiptDiagnosticCandidatesSectionProps> = ({ data, renderVariant }) => (
  <section className="surface-card report-section" aria-label="Diagnostic candidates">
    <h2 className="section-title">Diagnostic candidates</h2>
    <p className="report-paragraph">
      The variants left for further investigation by the locked criteria of the {describeDiagnosticCriteria(data.criteria)}{' '}
      Each needs orthogonal confirmation.
    </p>
    {data.capped ? (
      <p className="report-paragraph report-incomplete-list">
        <strong>These lists may be incomplete:</strong> the scope holds more variants than the analysis classifies at
        once. Narrow it with a gene panel or a gene.
      </p>
    ) : null}
    <View
      title="De novo in the fetus"
      description="In the fetal window, no supported paternal call, in no other cfDNA sample of the assay (unless ClinVar calls it pathogenic)."
      candidates={data.de_novo}
      renderVariant={renderVariant}
    />
    <View
      title="Paternal, inherited by the fetus"
      description="The father's allele, which the fetus inherited (probability 50% or more)."
      candidates={data.paternal}
      renderVariant={renderVariant}
    />
    <View
      title="Maternal, inherited by the fetus"
      description="The mother's allele, which the fetus inherited (probability 50% or more). The maternal inference rests on a small allele-fraction shift: read its probability."
      candidates={data.maternal}
      renderVariant={renderVariant}
    />
    <div className="report-section report-nipt-group">
      <h3 className="report-subheading">Recessive: both parents carriers ({data.recessive_genes.length})</h3>
      <p className="report-paragraph">
        Genes in which the mother and the father each carry a qualifying allele, and X-linked genes in which a son&rsquo;s
        mother carries one, with the fetal status and its probabilities (undetermined: an inheritance between 20% and
        80%).
      </p>
      {data.recessive_genes.length ? (
        <ul className="report-list">
          {data.recessive_genes.map((gene) => (
            <li key={gene.gene}>
              {gene.gene}
              {gene.mode !== 'autosomal' ? ' (X-linked)' : ''}: {RECESSIVE_STATUS_LABELS[gene.status] ?? gene.status} (
              {recessiveProbabilitiesText(gene.risk, gene.carrier_probability, gene.unaffected_probability, gene.mode === 'x_linked_son')})
              {gene.risk_uses_prior ? ', an inheritance read at its Mendelian prior' : ''}
            </li>
          ))}
        </ul>
      ) : (
        <p className="report-paragraph report-empty">None.</p>
      )}
    </div>
  </section>
);

export default NiptDiagnosticCandidatesSection;
