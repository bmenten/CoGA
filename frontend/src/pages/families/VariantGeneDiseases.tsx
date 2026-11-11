import { summarizeGeneDiseases, type GeneDiseaseLinks, type GeneDiseaseSummaryBasis } from './geneDiseaseLinks';

// Where the names on a card's top line come from, said after them.
const BASIS_NOTES: Record<GeneDiseaseSummaryBasis, string> = {
  causal: 'Monarch, causal',
  omim: 'OMIM',
  associated: 'Monarch, associated only, not causal',
};

// The longest list the expanded detail shows per source; the Gene Explorer has the rest.
const DETAIL_LIST_LENGTH = 8;

interface GeneDiseaseState {
  symbol?: string | null;
  // The gene's links, once the page's lookup answered.
  links?: GeneDiseaseLinks;
  failed: boolean;
  onRetry: () => void;
}

/**
 * The diseases the card's gene is linked to, in one line under the card's heading.
 *
 * A failed lookup says so rather than leaving the line out, which would read as a gene with no
 * known disease.
 */
export const GeneDiseaseSummaryLine = ({
  symbol,
  links,
  failed,
  onRetry,
  onShowAll,
}: GeneDiseaseState & { onShowAll: () => void }) => {
  if (!symbol) return null;
  const summary = links ? summarizeGeneDiseases(links) : null;
  return (
    <div className="variant-card-diseases">
      <span className="variant-card-col-key">Disease</span>
      {summary === null ? (
        failed ? (
          <span className="variant-card-empty-note">
            The gene–disease links could not be loaded.{' '}
            <button type="button" className="button-link" onClick={onRetry}>
              Retry
            </button>
          </span>
        ) : (
          <span className="variant-card-empty-note">Loading…</span>
        )
      ) : summary.basis === null ? (
        <span className="variant-card-empty-note">No gene–disease link cached for {symbol}</span>
      ) : (
        <>
          <span className="variant-card-diseases-names">{summary.names.join(' · ')}</span>
          {summary.more ? (
            <button
              type="button"
              className="button-link"
              title="Every link is under More annotations & detail"
              onClick={onShowAll}
            >
              +{summary.more} more
            </button>
          ) : null}
          <span className="variant-card-disease-source">{BASIS_NOTES[summary.basis]}</span>
        </>
      )}
    </div>
  );
};

/**
 * Every gene–disease link of the card's gene, for its expanded detail: Monarch's and OMIM's.
 */
export const VariantGeneDiseases = ({ symbol, links, failed, onRetry }: GeneDiseaseState) => {
  if (!symbol) {
    return <p className="variant-card-empty-note">No gene assigned to this variant.</p>;
  }
  if (!links) {
    if (failed) {
      return (
        <p className="variant-card-empty-note" role="alert">
          The gene–disease associations of {symbol} could not be loaded.{' '}
          <button type="button" className="button-link" onClick={onRetry}>
            Retry
          </button>
        </p>
      );
    }
    return <p className="variant-card-empty-note">Loading gene–disease associations…</p>;
  }
  const { monarch, omim } = links;
  if (!monarch.length && !omim.length) {
    return <p className="variant-card-empty-note">No gene–disease associations cached for {symbol}.</p>;
  }

  return (
    <div className="variant-card-disease-groups">
      {monarch.length ? (
        <div>
          <p className="variant-card-disease-heading">Monarch ({monarch.length})</p>
          <ul className="variant-card-disease-list">
            {monarch.slice(0, DETAIL_LIST_LENGTH).map((entry) => (
              <li key={entry.mondo_id}>
                <a href={entry.monarch_url} target="_blank" rel="noreferrer">
                  {entry.disease_label}
                </a>
                {/* Whether the gene causes the disease or is merely associated changes
                    how the association should be read, so it is stated. */}
                {entry.causal ? <span className="variant-card-chip variant-card-chip--strong">causal</span> : null}
                {entry.sources.length ? (
                  <span className="variant-card-disease-source">{entry.sources.join(', ')}</span>
                ) : null}
              </li>
            ))}
          </ul>
          {monarch.length > DETAIL_LIST_LENGTH ? (
            <p className="variant-card-empty-note">
              +{monarch.length - DETAIL_LIST_LENGTH} more in the Gene Explorer.
            </p>
          ) : null}
        </div>
      ) : null}
      {omim.length ? (
        <div>
          <p className="variant-card-disease-heading">OMIM ({omim.length})</p>
          <ul className="variant-card-disease-list">
            {omim.slice(0, DETAIL_LIST_LENGTH).map((entry) => (
              <li key={`${entry.omim_id ?? ''}|${entry.label}`}>
                {entry.href ? (
                  <a href={entry.href} target="_blank" rel="noreferrer">
                    {entry.label}
                  </a>
                ) : (
                  <span>{entry.label}</span>
                )}
                {entry.omim_id ? <span className="variant-card-disease-source">MIM {entry.omim_id}</span> : null}
              </li>
            ))}
          </ul>
          {omim.length > DETAIL_LIST_LENGTH ? (
            <p className="variant-card-empty-note">+{omim.length - DETAIL_LIST_LENGTH} more in the Gene Explorer.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};
