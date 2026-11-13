import { RANK_BY_VARIANT_SCORE } from './variantFilterFormUtils';

/**
 * The order of a prioritised search, in the Phenotype section of the small-variant and SV
 * filters: by the combined score (the variant and the phenotype match), or by the variant score
 * alone, which leaves the phenotype match out of the order so a variant in a gene without
 * Monarch phenotype data (a novel-gene candidate) ranks by its own evidence. Either order ranks
 * every candidate of the search, not only the page shown. Set only while prioritization is on.
 */
export default function RankByField({
  prioritize,
  rankBy,
  onChange,
}: {
  prioritize: boolean;
  rankBy: string;
  onChange: (value: string) => void;
}) {
  return (
    <>
      <label className="analysis-field-label">
        Rank by
        <select
          aria-label="Rank prioritised variants by"
          value={rankBy === RANK_BY_VARIANT_SCORE ? RANK_BY_VARIANT_SCORE : ''}
          disabled={!prioritize}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">Combined score (variant and phenotype)</option>
          <option value={RANK_BY_VARIANT_SCORE}>Variant score (novel-gene candidates)</option>
        </select>
      </label>
      <p className="table-subtle">
        By the variant score the phenotype match is left out of the order: a variant in a gene
        without Monarch phenotype data, which scores 0 on phenotype, ranks with equally
        deleterious variants in phenotype-matched genes. The scores stay the same.
      </p>
    </>
  );
}

/** The note above a list ranked by the variant score; nothing for one ranked otherwise. */
export function RankedByNote({ rankedBy }: { rankedBy?: string | null }) {
  if (rankedBy !== RANK_BY_VARIANT_SCORE) return null;
  return (
    <p className="ranking-cache-note">
      Ranked by the variant score: the gene–phenotype match is left out of the order and the
      rank, so a candidate in a gene without phenotype data ranks by its own evidence.
    </p>
  );
}
