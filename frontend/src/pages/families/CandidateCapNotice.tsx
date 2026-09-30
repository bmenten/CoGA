/**
 * Says when a family variant search read only part of the callset (#725 follow-up).
 *
 * Two backend caps can cut a search short, and the table alone cannot show either: its
 * total just reads as an estimate. `candidates_capped` means the search read a capped
 * candidate window before filtering (compound-het / recessive pairing, expanded carrier
 * screening, a Python-filtered SV search), so a match beyond it is missing however few
 * rows are shown. `ranking_truncated` means a prioritised ranking covered only its
 * window, so the best candidate may be missing. `candidate_limit` is where it stopped.
 */
export type CandidateCapFlags = {
  candidates_capped?: boolean;
  ranking_truncated?: boolean;
  candidate_limit?: number | null;
};

type CandidateCapNoticeProps = {
  page: CandidateCapFlags | null | undefined;
  /** What a candidate is, in the plural: "variants" or "SVs". */
  noun: string;
};

const limitPhrase = (limit: number | null | undefined, noun: string): string =>
  typeof limit === 'number' && limit > 0
    ? `the first ${limit.toLocaleString()} candidate ${noun}`
    : `a limited number of candidate ${noun}`;

const NARROWING = 'a region, a gene panel or a gene';

/** The sentence a notice shows for these flags, or null when the search was complete. */
export function candidateCapMessage(page: CandidateCapFlags | null | undefined, noun: string): string | null {
  if (page?.candidates_capped) {
    return (
      `Results may be incomplete: the search stopped after ${limitPhrase(page.candidate_limit, noun)}, ` +
      `so matches beyond that point are not shown and the total is a lower bound. ` +
      `Narrow the filters (${NARROWING}) so the search reads the whole callset.`
    );
  }
  if (page?.ranking_truncated) {
    return (
      `Ranking may be incomplete: the prioritizer ranked only ${limitPhrase(page.candidate_limit, noun)}, ` +
      `so the top candidate may not be shown. ` +
      `Narrow the filters (${NARROWING}, or tighter frequency or impact) to rank the full set.`
    );
  }
  return null;
}

export default function CandidateCapNotice({ page, noun }: CandidateCapNoticeProps) {
  const message = candidateCapMessage(page, noun);
  if (!message) return null;
  return (
    <div
      className="variant-workspace-feedback variant-workspace-feedback--warning"
      role="status"
      data-testid="candidate-cap-notice"
    >
      {message}
    </div>
  );
}
