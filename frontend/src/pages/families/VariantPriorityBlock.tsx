import { type RankedBy, type SmallVariantPriority } from './smallVariantSearch';
import { SEGREGATION_MODE_LABELS } from './smallVariantResultUtils';

/**
 * Visible priority breakdown for a prioritized variant — used on the cards so the
 * score and the reasons behind it are readable without hovering or opening the dialog.
 * It leads with the score the page is ranked by (`rankedBy`) beside the rank, and lists the
 * other among the metrics.
 */
export default function VariantPriorityBlock({
  priority,
  rankedBy = null,
}: {
  priority: SmallVariantPriority;
  rankedBy?: RankedBy;
}) {
  const byVariantScore = rankedBy === 'variant_score';
  const modes = (priority.segregation_modes || [])
    .map((mode) => SEGREGATION_MODE_LABELS[mode] || mode)
    .join(', ');
  const matched = (priority.phenotype_matches || [])
    .map((match) => match.label || match.hpo_id)
    .slice(0, 5);

  const metrics: Array<[string, string]> = [
    byVariantScore
      ? ['Priority', priority.combined_score.toFixed(2)]
      : ['Variant', priority.variant_score.toFixed(2)],
    ['Pathogenicity', priority.pathogenicity_score.toFixed(2)],
    ['Rarity', priority.frequency_score.toFixed(2)],
    [
      'Phenotype',
      typeof priority.phenotype_score === 'number'
        ? priority.phenotype_score.toFixed(2)
        : 'n/a',
    ],
  ];

  return (
    <div className="variant-card-priority">
      <div className="variant-card-priority-head">
        <span className="variant-card-priority-score">
          {byVariantScore
            ? `Variant score ${priority.variant_score.toFixed(2)}`
            : `Priority ${priority.combined_score.toFixed(2)}`}
        </span>
        {priority.rank ? (
          <span className="variant-card-priority-rank">#{priority.rank}</span>
        ) : null}
        {matched.length ? (
          <span className="variant-card-priority-pheno" title="Matches the patient's phenotypes">
            ✦ phenotype match
          </span>
        ) : null}
      </div>
      <div className="variant-card-priority-metrics">
        {metrics.map(([label, value]) => (
          <span key={label} className="variant-card-priority-metric">
            <span className="variant-card-priority-metric-label">{label}</span>
            <span className="variant-card-priority-metric-value">{value}</span>
          </span>
        ))}
      </div>
      {modes ? (
        <p className="variant-card-priority-note">Inheritance: {modes}</p>
      ) : priority.segregation_weight >= 1 ? (
        <p className="variant-card-priority-note">
          Segregation not evaluated (no affected individuals flagged).
        </p>
      ) : (
        <p className="variant-card-priority-note">No compatible inheritance mode.</p>
      )}
      {matched.length ? (
        <p className="variant-card-priority-note">
          Phenotype match{priority.phenotype_gene ? ` (${priority.phenotype_gene})` : ''}:{' '}
          {matched.join(', ')}
        </p>
      ) : null}
    </div>
  );
}
